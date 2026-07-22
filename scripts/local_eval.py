#!/usr/bin/env python3
"""Local exact evaluation of a candidate encoder against the committed anchor.

Because SVT-AV1 v4.2.0 is cross-ISA deterministic (proven: all 75 anchor bitstreams
bit-identical arm64<->x86_64), local BD-rate is EXACT, not an estimate — so this is
the authoritative ranking signal for the autoresearch loop. Only the speed gate is
approximate locally (native RUSAGE vs the official cgroup basis); it is reported but
BD-rate is what ranks candidates. The official arbiter remains a CI run.

Usage:
  scripts/local_eval.py --candidate-bin PATH [--anchor-bin PATH] [--label NAME]
  scripts/local_eval.py --candidate-src DIR  [--anchor-bin PATH] [--label NAME]

Prints one JSON line: {label, exit, score, bd_rate_mean, gates, per_clip_bd, note}.
Exit 0 = candidate scored (see score/gates). Nonzero mirrors the harness (2=gate fail).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ANCHOR_BIN = REPO / ".cache/work/anchor_src/Bin/Release/SvtAv1EncApp"
ANCHOR_DATA = REPO / "harness/anchor/anchor.json"
SCORE_PATH = REPO / ".yukon/score.json"


def build_native(src_dir: Path, work: Path) -> Path:
    """Build a candidate encoder from source, mirroring the harness build."""
    build = work / "build_hn"
    subprocess.run(
        ["cmake", "-S", str(src_dir), "-B", str(build), "-G", "Ninja",
         "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(["ninja", "-C", str(build)], check=True, capture_output=True, text=True)
    binary = src_dir / "Bin/Release/SvtAv1EncApp"
    if not binary.is_file():
        raise SystemExit(f"build produced no binary at {binary}")
    return binary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate-bin", type=Path)
    ap.add_argument("--candidate-src", type=Path)
    ap.add_argument("--anchor-bin", type=Path, default=DEFAULT_ANCHOR_BIN)
    ap.add_argument("--label", default="candidate")
    ap.add_argument("--corpus", type=Path)
    ap.add_argument("--keep-guard", action="store_true")
    args = ap.parse_args()

    if not args.anchor_bin.is_file():
        raise SystemExit(f"anchor binary missing: {args.anchor_bin} (run cli.py setup first)")

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        if args.candidate_src:
            candidate_bin = build_native(args.candidate_src.resolve(), work)
        elif args.candidate_bin:
            candidate_bin = args.candidate_bin.resolve()
        else:
            raise SystemExit("need --candidate-bin or --candidate-src")

        SCORE_PATH.unlink(missing_ok=True)
        cmd = [sys.executable, str(REPO / "harness/cli.py"), "run",
               "--mode", "native",
               "--anchor-bin", str(args.anchor_bin),
               "--candidate-bin", str(candidate_bin),
               "--anchor-data", str(ANCHOR_DATA),
               "--work-dir", str(work / "run")]
        if not args.keep_guard:
            cmd.append("--skip-guard")
        if args.corpus:
            cmd += ["--corpus", str(args.corpus)]
        proc = subprocess.run(cmd, capture_output=True, text=True)

    out = {"label": args.label, "exit": proc.returncode, "score": None,
           "bd_rate_mean": None, "gates": None, "per_clip_bd": None, "note": ""}
    if SCORE_PATH.is_file():
        data = json.loads(SCORE_PATH.read_text())
        m = data["metrics"]
        out["score"] = data["score"]
        out["bd_rate_mean"] = m["bd_rate_mean"]
        out["gates"] = {k: (v if isinstance(v, str) else v.get("status"))
                        for k, v in m["gates"].items()}
        out["per_clip_bd"] = {n: round(v["bd_rate"], 3) for n, v in m["per_clip"].items()}
    else:
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        out["note"] = tail[-1] if tail else "no output"
    print(json.dumps(out))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
