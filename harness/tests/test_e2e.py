from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
ENCODER = shutil.which("SvtAv1EncApp")
DECODER = shutil.which("dav1d")

pytestmark = pytest.mark.skipif(
    ENCODER is None or DECODER is None,
    reason="native SvtAv1EncApp and dav1d are required",
)


@dataclass(slots=True)
class E2EContext:
    root: Path
    cli: Path
    score_path: Path
    manifest: Path
    anchor_data: Path
    encoder: Path
    regen_result: subprocess.CompletedProcess[str] | None = None
    regen_score: float | None = None


def _write_y4m(path: Path, seed: int) -> None:
    rng = np.random.default_rng(seed)
    height = width = 128
    chroma_height = height // 2
    chroma_width = width // 2
    y_x = np.arange(width, dtype=np.int16)[None, :]
    y_y = np.arange(height, dtype=np.int16)[:, None]
    c_x = np.arange(chroma_width, dtype=np.int16)[None, :]
    c_y = np.arange(chroma_height, dtype=np.int16)[:, None]
    y_noise = rng.integers(-10, 11, size=(height, width), dtype=np.int16)
    cb_noise = rng.integers(
        -7, 8, size=(chroma_height, chroma_width), dtype=np.int16
    )
    cr_noise = rng.integers(
        -7, 8, size=(chroma_height, chroma_width), dtype=np.int16
    )

    with path.open("wb") as stream:
        stream.write(b"YUV4MPEG2 W128 H128 F30:1 Ip A0:0 C420\n")
        for frame in range(64):
            y = (
                (2 * y_x + 3 * y_y + 5 * frame + 17 * seed) % 256
                + y_noise
            )
            cb = (
                128
                + 25 * np.sin((c_x + 2 * frame + seed) / 9.0)
                + 18 * np.cos((c_y - frame) / 11.0)
                + cb_noise
            )
            cr = (
                128
                + 22 * np.cos((c_x - frame - seed) / 8.0)
                - 20 * np.sin((c_y + frame) / 10.0)
                + cr_noise
            )
            stream.write(b"FRAME\n")
            stream.write(np.clip(y, 0, 255).astype(np.uint8).tobytes())
            stream.write(np.clip(cb, 0, 255).astype(np.uint8).tobytes())
            stream.write(np.clip(cr, 0, 255).astype(np.uint8).tobytes())


def _run_cli(
    context: E2EContext,
    *,
    work_dir: Path,
    candidate_bin: Path,
    anchor_bin: Path,
    regen: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(context.cli),
        "run",
        "--mode",
        "native",
        "--jobs",
        "2",
        "--candidate-bin",
        str(candidate_bin),
        "--anchor-bin",
        str(anchor_bin),
        "--corpus",
        str(context.manifest),
        "--anchor-data",
        str(context.anchor_data),
        "--skip-guard",
        "--work-dir",
        str(work_dir),
    ]
    if regen:
        command.append("--regen-anchor")
    env = os.environ.copy()
    env.pop("REGEN_ANCHOR", None)
    env["GITHUB_ACTIONS"] = "false"
    return subprocess.run(
        command,
        cwd=context.root,
        env=env,
        text=True,
        capture_output=True,
        timeout=900,
        check=False,
    )


def _result_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _read_score(context: E2EContext) -> dict[str, object]:
    return json.loads(context.score_path.read_text(encoding="utf-8"))


def _write_executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _slow_shim(path: Path, encoder: Path) -> Path:
    return _write_executable(
        path,
        f"""#!/bin/bash
set -eu
{shlex.quote(str(encoder))} "$@"
python3 - <<'PY'
import time

deadline = time.process_time() + 2.0
value = 1
while time.process_time() < deadline:
    value = (value * 1664525 + 1013904223) & 0xffffffff
PY
""",
    )


def _existing_output_cheat_shim(path: Path, encoder: Path) -> Path:
    return _write_executable(
        path,
        f"""#!/bin/bash
set -eu
args=("$@")
output=""
for ((index = 0; index < ${{#args[@]}}; index++)); do
    if [ "${{args[$index]}}" = "-b" ]; then
        output="${{args[$((index + 1))]}}"
        break
    fi
done
if [ -e "$output" ]; then
    exit 0
fi
{shlex.quote(str(encoder))} "${{args[@]}}"
python3 - <<'PY'
import time

deadline = time.process_time() + 0.08
while time.process_time() < deadline:
    pass
PY
""",
    )


def _nondeterministic_shim(path: Path, encoder: Path, state_dir: Path) -> Path:
    state_dir.mkdir(parents=True)
    return _write_executable(
        path,
        f"""#!/bin/bash
set -eu
state_dir={shlex.quote(str(state_dir))}
key="$(python3 - "$@" <<'PY'
import hashlib
import os
import sys

args = list(sys.argv[1:])
payload = b"\\0".join(os.fsencode(arg) for arg in args)
print(hashlib.md5(payload, usedforsecurity=False).hexdigest())
PY
)"
marker="$state_dir/$key"
if (set -o noclobber; : > "$marker") 2>/dev/null; then
    exec {shlex.quote(str(encoder))} "$@"
fi
exec {shlex.quote(str(encoder))} "$@" --aq-mode 0
""",
    )


def _corrupting_shim(path: Path, encoder: Path) -> Path:
    return _write_executable(
        path,
        f"""#!/bin/bash
set -eu
{shlex.quote(str(encoder))} "$@"
output=""
while [ "$#" -gt 0 ]; do
    if [ "$1" = "-b" ]; then
        shift
        output="$1"
        break
    fi
    shift
done
python3 - "$output" <<'PY'
import os
import sys

path = sys.argv[1]
os.truncate(path, max(0, os.path.getsize(path) - 100))
PY
""",
    )


@pytest.fixture(scope="session")
def e2e_context(tmp_path_factory: pytest.TempPathFactory) -> E2EContext:
    assert ENCODER is not None
    root = tmp_path_factory.mktemp("videofast-e2e")
    shutil.copytree(
        REPO_ROOT / "harness",
        root / "harness",
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    token = hashlib.sha256(str(root).encode()).hexdigest()[:10]
    sources = root / "sources"
    sources.mkdir()
    clips: list[dict[str, str]] = []
    for index, suffix in enumerate(("a", "b"), start=1):
        name = f"e2e-synthetic-{token}-{suffix}.y4m"
        source = sources / name
        _write_y4m(source, seed=index)
        clips.append(
            {
                "name": name,
                "url": source.as_uri(),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        )
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"clips": clips}), encoding="utf-8")
    anchor_data = root / "anchor.json"
    encoder = Path(ENCODER).resolve()
    context = E2EContext(
        root=root,
        cli=root / "harness" / "cli.py",
        score_path=root / ".yukon" / "score.json",
        manifest=manifest,
        anchor_data=anchor_data,
        encoder=encoder,
    )
    result = _run_cli(
        context,
        work_dir=root / "regen-work",
        candidate_bin=encoder,
        anchor_bin=encoder,
        regen=True,
    )
    if result.returncode != 0:
        pytest.fail(f"anchor regeneration failed\n{_result_output(result)}")
    if not anchor_data.is_file():
        pytest.fail("anchor regeneration did not write anchor.json")
    if not context.score_path.is_file():
        pytest.fail("anchor regeneration did not write score.json")
    context.regen_result = result
    context.regen_score = float(_read_score(context)["score"])
    yield context


def test_e2e_regen_and_identity(e2e_context: E2EContext) -> None:
    assert e2e_context.regen_result is not None
    assert "SCORE " in e2e_context.regen_result.stdout
    assert e2e_context.regen_score == 100.0

    result = _run_cli(
        e2e_context,
        work_dir=e2e_context.root / "identity-work",
        candidate_bin=e2e_context.encoder,
        anchor_bin=e2e_context.encoder,
    )

    assert result.returncode == 0, _result_output(result)
    score_document = _read_score(e2e_context)
    assert score_document["score"] == 100.0
    assert score_document["metrics"]["ladder_cpu"]["status"] == "pass"

    anchor_document = json.loads(
        e2e_context.anchor_data.read_text(encoding="utf-8")
    )
    clip_cpu_totals: list[float] = []
    for clip_document in anchor_document["clips"].values():
        point_cpu_values = [
            point["cpu_seconds"] for point in clip_document["curve"]
        ]
        assert all(isinstance(value, (int, float)) for value in point_cpu_values)
        assert clip_document["ladder_cpu_total"] == pytest.approx(
            sum(point_cpu_values)
        )
        clip_cpu_totals.append(clip_document["ladder_cpu_total"])
    assert anchor_document["ladder_cpu_total"] == pytest.approx(
        sum(clip_cpu_totals)
    )


def test_e2e_sabotage_slow(e2e_context: E2EContext) -> None:
    shim = _slow_shim(e2e_context.root / "slow-encoder", e2e_context.encoder)

    result = _run_cli(
        e2e_context,
        work_dir=e2e_context.root / "slow-work",
        candidate_bin=shim,
        anchor_bin=e2e_context.encoder,
    )

    assert result.returncode == 2, _result_output(result)
    assert "GATE FAIL speed" in _result_output(result)
    assert not e2e_context.score_path.exists()


def test_e2e_timing_requires_fresh_bitstream(e2e_context: E2EContext) -> None:
    shim = _existing_output_cheat_shim(
        e2e_context.root / "existing-output-cheat", e2e_context.encoder
    )

    result = _run_cli(
        e2e_context,
        work_dir=e2e_context.root / "existing-output-work",
        candidate_bin=shim,
        anchor_bin=e2e_context.encoder,
    )

    assert result.returncode == 2, _result_output(result)
    assert "STAGE rd-validity" in result.stdout, _result_output(result)
    assert "GATE FAIL speed" in _result_output(result)
    assert not e2e_context.score_path.exists()


def test_e2e_sabotage_nondet(e2e_context: E2EContext) -> None:
    shim = _nondeterministic_shim(
        e2e_context.root / "nondeterministic-encoder",
        e2e_context.encoder,
        e2e_context.root / "nondeterministic-state",
    )

    result = _run_cli(
        e2e_context,
        work_dir=e2e_context.root / "nondeterministic-work",
        candidate_bin=shim,
        anchor_bin=e2e_context.encoder,
    )

    assert result.returncode == 2, _result_output(result)
    assert "GATE FAIL determinism" in _result_output(result)
    assert not e2e_context.score_path.exists()


def test_e2e_sabotage_corrupt(e2e_context: E2EContext) -> None:
    shim = _corrupting_shim(
        e2e_context.root / "corrupting-encoder", e2e_context.encoder
    )

    result = _run_cli(
        e2e_context,
        work_dir=e2e_context.root / "corrupt-work",
        candidate_bin=shim,
        anchor_bin=e2e_context.encoder,
    )

    output = _result_output(result)
    assert result.returncode == 2, output
    assert "conformance" in output.lower() or "ivf" in output.lower()
    assert not e2e_context.score_path.exists()


def test_e2e_planted_score(
    e2e_context: E2EContext,
) -> None:
    e2e_context.score_path.parent.mkdir(parents=True, exist_ok=True)
    e2e_context.score_path.write_text('{"score": -12345}', encoding="utf-8")
    passing = _run_cli(
        e2e_context,
        work_dir=e2e_context.root / "planted-pass-work",
        candidate_bin=e2e_context.encoder,
        anchor_bin=e2e_context.encoder,
    )

    assert passing.returncode == 0, _result_output(passing)
    assert _read_score(e2e_context)["score"] == 100.0

    e2e_context.score_path.write_text('{"score": -12345}', encoding="utf-8")
    slow = _slow_shim(
        e2e_context.root / "planted-slow-encoder", e2e_context.encoder
    )
    failing = _run_cli(
        e2e_context,
        work_dir=e2e_context.root / "planted-fail-work",
        candidate_bin=slow,
        anchor_bin=e2e_context.encoder,
    )

    assert failing.returncode == 2, _result_output(failing)
    assert "GATE FAIL speed" in _result_output(failing)
    assert not e2e_context.score_path.exists()
