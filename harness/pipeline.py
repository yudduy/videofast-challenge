"""Trusted setup and scoring orchestration for the videofast challenge."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tarfile
import time
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar

from harness import corpus
from harness.config import Config
from harness.gates import GateError, SpeedGateResult, check_speed, geomean
from harness.ivf import payload_bytes
from harness.sandbox import Runner
from harness.y4m import parse_header


_T = TypeVar("_T")
_HARNESS_VERSION = "1"


@dataclass(frozen=True, slots=True)
class _Encoded:
    name: str
    crf: int
    path: Path
    sha256: str
    wall_seconds: float
    cpu_seconds: float | None


def _arg(args: Any, name: str, default: Any = None) -> Any:
    return getattr(args, name, default)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _stage(name: str, count: int, started: float) -> None:
    print(f"STAGE {name} {count} {time.monotonic() - started:.3f}s", flush=True)


def _docker_reachable() -> bool:
    try:
        completed = subprocess.run(
            ["docker", "info"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def resolve_mode(requested: str, cfg: Config) -> str:
    if requested not in {"auto", "docker", "native"}:
        raise ValueError(f"unsupported execution mode {requested!r}")
    pending = "PENDING" in cfg.toolchain_image
    if requested == "docker":
        if pending:
            raise RuntimeError("toolchain image not yet published")
        return "docker"
    if requested == "native":
        return "native"
    wants_docker = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    if not wants_docker:
        wants_docker = _docker_reachable()
    return "docker" if wants_docker and not pending else "native"


def _fingerprint(mode: str, cfg: Config) -> dict[str, Any]:
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "mode": mode,
        "image": cfg.toolchain_image if mode == "docker" else None,
    }


def _pull_image(cfg: Config) -> None:
    if "PENDING" in cfg.toolchain_image:
        raise RuntimeError("toolchain image not yet published")
    try:
        result = subprocess.run(
            ["docker", "pull", cfg.toolchain_image],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"cannot run docker pull: {exc}") from exc
    if result.returncode:
        tail = "\n".join(result.stderr.splitlines()[-30:])
        raise RuntimeError(f"docker pull failed: {tail}")


def _extract_anchor(cfg: Config, work_dir: Path) -> Path:
    actual = _sha256(cfg.anchor_tarball)
    if actual != cfg.anchor_tarball_sha256.lower():
        raise RuntimeError(
            "anchor tarball sha256 mismatch: "
            f"expected {cfg.anchor_tarball_sha256}, got {actual}"
        )
    destination = work_dir / "anchor_extract"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(cfg.anchor_tarball, "r:*") as archive:
        root = destination.resolve()
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"unsafe path in anchor tarball: {member.name}")
            if member.issym() or member.islnk():
                link = (target.parent / member.linkname).resolve()
                if link != root and root not in link.parents:
                    raise RuntimeError(
                        f"unsafe link in anchor tarball: {member.name}"
                    )
        archive.extractall(destination, filter="data")
    children = [path for path in destination.iterdir() if path.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return destination


def _runner(
    cfg: Config,
    mode: str,
    work_dir: Path,
    *,
    protected_paths: Sequence[Path] = (),
) -> Runner:
    return Runner(
        mode,
        cfg,
        work_dir=work_dir,
        cache_dir=cfg.cache_dir,
        protected_paths=protected_paths,
    )


def _work_entry(path: Path, work_dir: Path) -> Path:
    try:
        relative = path.resolve().relative_to(work_dir.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"Docker encoder binary must be inside the work directory: {path}"
        ) from exc
    if not relative.parts:
        raise RuntimeError(f"invalid encoder binary path: {path}")
    return work_dir / relative.parts[0]


def cmd_setup(cfg: Config, args: Any) -> None:
    started = time.monotonic()
    mode = resolve_mode(_arg(args, "mode", "auto"), cfg)
    work_dir = Path(_arg(args, "work_dir", None) or cfg.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest = Path(_arg(args, "corpus", None) or cfg.corpus_manifest)
    if mode == "docker":
        _pull_image(cfg)
    clips = corpus.ensure(manifest, cfg.cache_dir)
    runner = _runner(cfg, mode, work_dir)

    anchor_override = _arg(args, "anchor_bin", None)
    candidate_override = _arg(args, "candidate_bin", None)
    anchor_binary = (
        Path(anchor_override).resolve()
        if anchor_override
        else runner.build_encoder(_extract_anchor(cfg, work_dir), "anchor")
    )
    candidate_binary = (
        Path(candidate_override).resolve()
        if candidate_override
        else runner.build_encoder(cfg.repo_root / "submission", "candidate")
    )
    for label, binary in (("anchor", anchor_binary), ("candidate", candidate_binary)):
        if not binary.is_file():
            raise RuntimeError(f"{label} encoder binary not found: {binary}")
    _write_json_atomic(
        work_dir / "setup_state.json",
        {
            "mode": mode,
            "anchor_binary": str(anchor_binary),
            "candidate_binary": str(candidate_binary),
        },
    )
    _stage("setup", len(clips), started)


def _load_binaries(args: Any, work_dir: Path) -> tuple[Path, Path]:
    anchor_override = _arg(args, "anchor_bin", None)
    candidate_override = _arg(args, "candidate_bin", None)
    if anchor_override and candidate_override:
        anchor, candidate = Path(anchor_override).resolve(), Path(candidate_override).resolve()
    else:
        state_path = work_dir / "setup_state.json"
        if not state_path.is_file():
            raise RuntimeError(
                f"setup state missing at {state_path}; run setup first or provide both "
                "--anchor-bin and --candidate-bin"
            )
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            anchor = (
                Path(anchor_override).resolve()
                if anchor_override
                else Path(state["anchor_binary"])
            )
            candidate = (
                Path(candidate_override).resolve()
                if candidate_override
                else Path(state["candidate_binary"])
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError(f"invalid setup state {state_path}: {exc}") from exc
    if not anchor.is_file():
        raise RuntimeError(f"anchor encoder binary not found: {anchor}")
    if not candidate.is_file():
        raise RuntimeError(f"candidate encoder binary not found: {candidate}")
    return anchor, candidate


def _configured_or_fallback(
    configured: Sequence[str], active: Sequence[str], count: int
) -> list[str]:
    active_set = set(active)
    if configured and all(name in active_set for name in configured):
        return sorted(configured)
    return list(active[:count])


def _parallel(
    jobs: int, calls: Iterable[tuple[tuple[Any, ...], Callable[..., _T]]]
) -> list[_T]:
    calls_list = list(calls)
    results: list[_T] = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {executor.submit(function, *arguments): arguments for arguments, function in calls_list}
        for future in as_completed(futures):
            results.append(future.result())
    return results


def _anchor_clip(anchor_data: Mapping[str, Any], name: str) -> tuple[list[dict[str, Any]], Mapping[str, Any]]:
    try:
        raw = anchor_data["clips"][name]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"anchor data has no curve for clip {name!r}") from exc
    if isinstance(raw, list):
        return raw, {}
    if not isinstance(raw, dict):
        raise RuntimeError(f"invalid anchor curve for clip {name!r}")
    points = raw.get("curve", raw.get("points"))
    if not isinstance(points, list):
        raise RuntimeError(f"invalid anchor curve points for clip {name!r}")
    return points, raw


def _point_for_crf(points: Sequence[Mapping[str, Any]], crf: int) -> Mapping[str, Any]:
    for point in points:
        if int(point.get("crf", -1)) == crf:
            return point
    raise RuntimeError(f"anchor data has no point at CRF {crf}")


def _wall_limit(
    anchor_data: Mapping[str, Any] | None, name: str, crf: int, factor: float
) -> float:
    if anchor_data is None:
        return 600.0
    points, clip_data = _anchor_clip(anchor_data, name)
    wall_times = clip_data.get("wall_times", {}) if isinstance(clip_data, dict) else {}
    value = wall_times.get(str(crf), wall_times.get(crf)) if isinstance(wall_times, dict) else None
    if value is None:
        value = _point_for_crf(points, crf).get("wall_seconds", 600.0)
    timeout = factor * float(value)
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise RuntimeError(
            f"invalid wall timeout for {name} CRF {crf}: {timeout}"
        )
    return timeout


def _encode_one(
    runner: Runner,
    binary: Path,
    name: str,
    source: Path,
    output: Path,
    crf: int,
    timeout: float,
) -> _Encoded:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    begun = time.monotonic()
    cpu_seconds = runner.encode(binary, source, output, crf, timeout)
    elapsed = time.monotonic() - begun
    if not output.is_file():
        raise RuntimeError(f"encoder did not create bitstream {output}")
    return _Encoded(name, crf, output, _sha256(output), elapsed, cpu_seconds)


def _quality_one(
    runner: Runner,
    encoded: _Encoded,
    source: Path,
    decoded_dir: Path,
    frames: int,
    guard: bool,
    model: str,
) -> tuple[str, int, dict[str, Any]]:
    decoded = decoded_dir / f"{encoded.name}.crf{encoded.crf}.y4m"
    try:
        runner.decode(encoded.path, decoded)
        metrics = dict(runner.compute_psnr(source, decoded, frames))
        guard_result = runner.vmaf(source, decoded, model) if guard else None
        if guard_result is None:
            metrics.update({"vmaf": None, "ssim": None})
        else:
            metrics.update(
                {
                    "vmaf": float(guard_result["vmaf_mean"]),
                    "ssim": float(guard_result["ssim_mean"]),
                }
            )
        try:
            byte_count = payload_bytes(encoded.path)
        except ValueError as exc:
            raise GateError("conformance", f"invalid IVF for {encoded.name} CRF {encoded.crf}: {exc}") from exc
        metrics["bytes"] = byte_count
        return encoded.name, encoded.crf, metrics
    except GateError:
        raise
    except Exception as exc:
        raise GateError(
            "conformance", f"{encoded.name} CRF {encoded.crf}: {exc}"
        ) from exc
    finally:
        decoded.unlink(missing_ok=True)


def _curve_points(points: Sequence[Mapping[str, Any]], quality: str) -> list[tuple[float, float]] | None:
    result: list[tuple[float, float]] = []
    for point in points:
        value = point.get(quality)
        if value is None:
            return None
        result.append((float(point["kbps"]), float(value)))
    return result


def _mean_optional(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return statistics.fmean(present) if present else None


def _guard_bd(
    runner: Runner,
    anchor: list[tuple[float, float]] | None,
    candidate: list[tuple[float, float]] | None,
    label: str,
) -> float | None:
    if anchor is None or candidate is None:
        return None
    try:
        return float(runner.compute_bd(anchor, candidate))
    except Exception as exc:
        print(f"WARNING {label} guard BD-rate unavailable: {exc}", flush=True)
        return None


def _timing_core() -> int:
    get_affinity = getattr(os, "sched_getaffinity", None)
    if get_affinity is not None:
        affinity = get_affinity(0)
        if affinity:
            return min(affinity)
    return 0


def _load_anchor(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load anchor data {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"anchor data {path} must contain a JSON object")
    return value


def _manifest_digest(encoded: Sequence[_Encoded]) -> str:
    lines = [
        f"{item.name}:{item.crf}:{item.sha256}"
        for item in sorted(encoded, key=lambda item: (item.name, item.crf))
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _ladder_cpu_gate(
    anchor_data: Mapping[str, Any],
    candidate_cpu_seconds: Sequence[float | None],
    timing_names: Sequence[str],
    anchor_cpu_medians_now: Mapping[str, float],
    total_cpu_max: float,
) -> dict[str, float | str | None]:
    """Evaluate the paired-VM total ladder CPU gate and return score metrics."""

    try:
        cpu_reference = anchor_data["timing"]["cpu_reference"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("anchor data has no timing.cpu_reference") from exc
    if not isinstance(cpu_reference, Mapping):
        raise RuntimeError("anchor timing.cpu_reference must be an object")

    ratios: list[float] = []
    for name in timing_names:
        try:
            current = float(anchor_cpu_medians_now[name])
            reference = float(cpu_reference[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"anchor CPU reference unavailable for timing clip {name!r}"
            ) from exc
        if (
            not math.isfinite(current)
            or current <= 0.0
            or not math.isfinite(reference)
            or reference <= 0.0
        ):
            raise RuntimeError(
                f"invalid anchor CPU timing for {name!r}: "
                f"current={current}, reference={reference}"
            )
        ratios.append(current / reference)
    vm_scale = geomean(ratios)

    candidate_total: float | None = None
    if all(value is not None for value in candidate_cpu_seconds):
        values = [float(value) for value in candidate_cpu_seconds if value is not None]
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise RuntimeError("candidate ladder CPU values must be finite and nonnegative")
        candidate_total = math.fsum(values)

    raw_anchor_total = anchor_data.get("ladder_cpu_total")
    anchor_total: float | None = None
    cap: float | None = None
    if raw_anchor_total is not None:
        try:
            anchor_total = float(raw_anchor_total)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("anchor ladder_cpu_total must be numeric") from exc
        if not math.isfinite(anchor_total) or anchor_total < 0.0:
            raise RuntimeError(
                "anchor ladder_cpu_total must be finite and nonnegative"
            )
        cap = float(total_cpu_max) * vm_scale * anchor_total

    metrics: dict[str, float | str | None] = {
        "candidate_total": candidate_total,
        "anchor_ref_total": anchor_total,
        "vm_scale": vm_scale,
        "cap": cap,
        "status": (
            "skipped"
            if anchor_total is None or candidate_total is None
            else "pass"
        ),
    }
    if candidate_total is not None and cap is not None and candidate_total > cap:
        raise GateError(
            "speed",
            f"candidate ladder CPU total {candidate_total:.6g}s exceeds scaled cap "
            f"{cap:.6g}s (anchor ladder CPU total {anchor_total:.6g}s, "
            f"vm_scale {vm_scale:.6g})",
        )
    return metrics


def cmd_run(cfg: Config, args: Any) -> float:
    score_path = cfg.score_path
    score_path.unlink(missing_ok=True)
    total_started = time.monotonic()
    mode = resolve_mode(_arg(args, "mode", "auto"), cfg)
    work_dir = Path(_arg(args, "work_dir", None) or cfg.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest = Path(_arg(args, "corpus", None) or cfg.corpus_manifest)
    clips = corpus.ensure(manifest, cfg.cache_dir)
    clips = sorted(clips, key=lambda clip: clip.name)
    if not clips:
        raise RuntimeError("active corpus is empty")
    clip_by_name = {clip.name: clip for clip in clips}
    names = [clip.name for clip in clips]
    jobs_arg = _arg(args, "jobs", None)
    jobs = min(4, os.cpu_count() or 1) if jobs_arg is None else int(jobs_arg)
    if jobs <= 0:
        raise ValueError("--jobs must be positive")
    anchor_binary, candidate_binary = _load_binaries(args, work_dir)
    runner = _runner(cfg, mode, work_dir)
    candidate_runner = runner
    if mode == "docker":
        candidate_runner = _runner(
            cfg,
            mode,
            work_dir,
            protected_paths=(_work_entry(anchor_binary, work_dir),),
        )
    fingerprint = _fingerprint(mode, cfg)
    regen = bool(_arg(args, "regen_anchor", False))
    anchor_path = Path(
        _arg(args, "anchor_data", None) or (cfg.anchor_data_dir / "anchor.json")
    ).resolve()
    anchor_data: dict[str, Any] | None = None
    estimate = False
    if not regen:
        anchor_data = _load_anchor(anchor_path)
        if anchor_data.get("fingerprint") != fingerprint:
            if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
                raise RuntimeError("anchor data not generated on official platform")
            estimate = True
    else:
        estimate = not (
            mode == "docker"
            and fingerprint["system"] == cfg.official_platform.system
            and fingerprint["machine"] == cfg.official_platform.machine
        )
    _stage("initialize", len(clips), total_started)

    # Regeneration measures the trusted anchor and deliberately scores it against itself.
    ladder_binary = anchor_binary if regen else candidate_binary
    ladder_runner = runner if regen else candidate_runner
    bitstream_dir = work_dir / "bitstreams"
    bitstream_dir.mkdir(parents=True, exist_ok=True)
    ladder_started = time.monotonic()
    encode_calls: list[tuple[tuple[Any, ...], Callable[..., _Encoded]]] = []
    for clip in clips:
        for crf in cfg.crf_ladder:
            timeout = _wall_limit(anchor_data, clip.name, crf, cfg.timing.wall_timeout_factor)
            output = bitstream_dir / f"{clip.name}.crf{crf}.ivf"
            encode_calls.append(
                ((ladder_runner, ladder_binary, clip.name, clip.path, output, crf, timeout), _encode_one)
            )
    encoded = _parallel(jobs, encode_calls)
    encoded_by_key = {(item.name, item.crf): item for item in encoded}
    ladder_cpu_known = all(item.cpu_seconds is not None for item in encoded)
    _stage("encode", len(encoded), ladder_started)

    determinism_names = _configured_or_fallback(cfg.determinism_clips, names, 2)
    determinism_started = time.monotonic()
    det_calls: list[tuple[tuple[Any, ...], Callable[..., _Encoded]]] = []
    for name in determinism_names:
        original = encoded_by_key.get((name, cfg.timing.crf))
        if original is None:
            raise RuntimeError(
                f"timing CRF {cfg.timing.crf} required for determinism is not in crf_ladder"
            )
        timeout = _wall_limit(anchor_data, name, cfg.timing.crf, cfg.timing.wall_timeout_factor)
        det_calls.append(
            (
                (
                    ladder_runner,
                    ladder_binary,
                    name,
                    clip_by_name[name].path,
                    original.path,
                    cfg.timing.crf,
                    timeout,
                ),
                _encode_one,
            )
        )
    det_results = _parallel(jobs, det_calls)
    for result in det_results:
        original = encoded_by_key.get((result.name, result.crf))
        assert original is not None
        if result.sha256 != original.sha256:
            raise GateError(
                "determinism",
                f"{result.name} CRF {result.crf} produced {result.sha256}, expected {original.sha256}",
            )
    _stage("determinism", len(det_results), determinism_started)

    quality_started = time.monotonic()
    decoded_dir = work_dir / "decoded"
    decoded_dir.mkdir(parents=True, exist_ok=True)
    use_guard = cfg.guard_metrics.enabled and not bool(_arg(args, "skip_guard", False))
    quality_calls = [
        (
            (
                runner,
                item,
                clip_by_name[item.name].path,
                decoded_dir,
                cfg.frames,
                use_guard,
                cfg.guard_metrics.vmaf_model,
            ),
            _quality_one,
        )
        for item in encoded
    ]
    quality_rows = _parallel(jobs, quality_calls)
    quality_by_key = {(name, crf): values for name, crf, values in quality_rows}
    _stage("conformance-quality", len(quality_rows), quality_started)

    candidate_curves: dict[str, list[dict[str, Any]]] = {}
    for clip in clips:
        info = parse_header(clip.path)
        fps = info.fps_num / info.fps_den
        curve: list[dict[str, Any]] = []
        for crf in cfg.crf_ladder:
            encoded_item = encoded_by_key[(clip.name, crf)]
            values = quality_by_key[(clip.name, crf)]
            byte_count = int(values["bytes"])
            point = {
                "crf": crf,
                "bytes": byte_count,
                "kbps": byte_count * 8.0 * fps / cfg.frames / 1000.0,
                "psnr_y": float(values["psnr_y"]),
                "psnr_cb": float(values["psnr_cb"]),
                "psnr_cr": float(values["psnr_cr"]),
                "psnr_yuv": float(values["psnr_yuv"]),
                "vmaf": values["vmaf"],
                "ssim": values["ssim"],
                "sha256": encoded_item.sha256,
                "wall_seconds": encoded_item.wall_seconds,
            }
            if regen and ladder_cpu_known:
                point["cpu_seconds"] = encoded_item.cpu_seconds
            curve.append(point)
        candidate_curves[clip.name] = curve

    rd_started = time.monotonic()
    comparison_anchor = (
        {name: curve for name, curve in candidate_curves.items()}
        if regen
        else {name: _anchor_clip(anchor_data or {}, name)[0] for name in names}
    )
    per_clip_metrics: dict[str, Any] = {}
    bd_values: list[float] = []
    vmaf_values: list[float | None] = []
    ssim_values: list[float | None] = []
    for name in names:
        anchor_curve = comparison_anchor[name]
        candidate_curve = candidate_curves[name]
        anchor_psnr = _curve_points(anchor_curve, "psnr_yuv")
        candidate_psnr = _curve_points(candidate_curve, "psnr_yuv")
        assert anchor_psnr is not None and candidate_psnr is not None
        bd = float(runner.compute_bd(anchor_psnr, candidate_psnr))
        bd_values.append(bd)
        anchor_vmaf = _curve_points(anchor_curve, "vmaf")
        candidate_vmaf = _curve_points(candidate_curve, "vmaf")
        anchor_ssim = _curve_points(anchor_curve, "ssim")
        candidate_ssim = _curve_points(candidate_curve, "ssim")
        guard_results: dict[str, float | None] = {
            "vmaf_bd_rate": _guard_bd(
                runner, anchor_vmaf, candidate_vmaf, f"{name} VMAF"
            ),
            "ssim_bd_rate": _guard_bd(
                runner, anchor_ssim, candidate_ssim, f"{name} SSIM"
            ),
        }
        vmaf_values.append(guard_results["vmaf_bd_rate"])
        ssim_values.append(guard_results["ssim_bd_rate"])
        per_clip_metrics[name] = {
            "bd_rate": bd,
            "points": candidate_curve,
            **guard_results,
        }
    bd_mean = statistics.fmean(bd_values)
    _stage("rd-validity", len(names), rd_started)

    timing_names = _configured_or_fallback(cfg.timing.clips, names, 4)
    timing_started = time.monotonic()
    anchor_samples: dict[str, list[float]] = {name: [] for name in timing_names}
    candidate_samples: dict[str, list[float]] = {name: [] for name in timing_names}
    core = _timing_core()
    for name in timing_names:
        source = clip_by_name[name].path
        timeout = _wall_limit(anchor_data, name, cfg.timing.crf, cfg.timing.wall_timeout_factor)
        scored = encoded_by_key[(name, cfg.timing.crf)]
        for _ in range(cfg.timing.reps):
            scored.path.unlink(missing_ok=True)
            anchor_samples[name].append(
                float(runner.timed_encode(anchor_binary, source, scored.path, cfg.timing.crf, timeout, cpuset_core=core))
            )
            timed_candidate = anchor_binary if regen else candidate_binary
            scored.path.unlink(missing_ok=True)
            candidate_samples[name].append(
                float(
                    (runner if regen else candidate_runner).timed_encode(
                        timed_candidate,
                        source,
                        scored.path,
                        cfg.timing.crf,
                        timeout,
                        cpuset_core=core,
                    )
                )
            )
            if not scored.path.is_file():
                raise GateError(
                    "determinism",
                    f"timed {name} CRF {cfg.timing.crf} did not create a bitstream",
                )
            actual_hash = _sha256(scored.path)
            if actual_hash != scored.sha256:
                raise GateError(
                    "determinism",
                    f"timed {name} CRF {cfg.timing.crf} produced {actual_hash}, "
                    f"expected {scored.sha256}",
                )
    speed_result: SpeedGateResult = check_speed(
        anchor_samples,
        candidate_samples,
        geomean_max=cfg.timing.geomean_max,
        per_clip_max=cfg.timing.per_clip_max,
    )
    timing_metrics = {
        name: {
            "anchor_cpu_median": float(statistics.median(anchor_samples[name])),
            "candidate_cpu_median": float(statistics.median(candidate_samples[name])),
        }
        for name in timing_names
    }
    anchor_cpu_medians_now = {
        name: timing_metrics[name]["anchor_cpu_median"] for name in timing_names
    }
    ladder_cpu_anchor = anchor_data
    if regen:
        ladder_cpu_anchor = {
            "timing": {"cpu_reference": anchor_cpu_medians_now},
        }
        if ladder_cpu_known:
            ladder_cpu_anchor["ladder_cpu_total"] = math.fsum(
                float(item.cpu_seconds)
                for item in encoded
                if item.cpu_seconds is not None
            )
    assert ladder_cpu_anchor is not None
    ladder_cpu_metrics = _ladder_cpu_gate(
        ladder_cpu_anchor,
        [item.cpu_seconds for item in encoded],
        timing_names,
        anchor_cpu_medians_now,
        cfg.timing.total_cpu_max,
    )
    if ladder_cpu_metrics["status"] == "skipped":
        if regen:
            print(
                "WARNING per-encode CPU accounting unavailable; "
                "omitting anchor ladder CPU fields",
                flush=True,
            )
        else:
            if ladder_cpu_metrics["anchor_ref_total"] is None:
                print(
                    "WARNING anchor data has no ladder_cpu_total; "
                    "skipping total ladder CPU gate",
                    flush=True,
                )
            if ladder_cpu_metrics["candidate_total"] is None:
                if mode == "docker":
                    raise RuntimeError(
                        "candidate per-encode CPU accounting unavailable in docker mode"
                    )
                print(
                    "WARNING candidate per-encode CPU accounting unavailable; "
                    "skipping total ladder CPU gate",
                    flush=True,
                )
    _stage("speed", len(timing_names), timing_started)

    if not regen:
        spot_started = time.monotonic()
        for name in determinism_names:
            points, _ = _anchor_clip(anchor_data or {}, name)
            expected = str(_point_for_crf(points, cfg.timing.crf)["sha256"])
            output = bitstream_dir / "anchor-spot" / f"{name}.crf{cfg.timing.crf}.ivf"
            result = _encode_one(
                runner,
                anchor_binary,
                name,
                clip_by_name[name].path,
                output,
                cfg.timing.crf,
                _wall_limit(anchor_data, name, cfg.timing.crf, cfg.timing.wall_timeout_factor),
            )
            if result.sha256 != expected:
                raise GateError(
                    "anchor-drift",
                    f"{name} CRF {cfg.timing.crf} produced {result.sha256}, expected {expected}",
                )
        _stage("anchor-spot-check", len(determinism_names), spot_started)

    score = 100.0 + bd_mean
    if regen:
        anchor_clips: dict[str, dict[str, Any]] = {}
        for name in names:
            clip_document: dict[str, Any] = {
                "curve": candidate_curves[name],
                "wall_times": {
                    str(point["crf"]): point["wall_seconds"]
                    for point in candidate_curves[name]
                },
            }
            if ladder_cpu_known:
                clip_document["ladder_cpu_total"] = math.fsum(
                    float(point["cpu_seconds"])
                    for point in candidate_curves[name]
                )
            anchor_clips[name] = clip_document
        anchor_document = {
            "fingerprint": fingerprint,
            "clips": anchor_clips,
            "timing": {
                "crf": cfg.timing.crf,
                "cpu_reference": {
                    name: timing_metrics[name]["anchor_cpu_median"]
                    for name in timing_names
                },
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if ladder_cpu_known:
            anchor_document["ladder_cpu_total"] = math.fsum(
                float(clip["ladder_cpu_total"])
                for clip in anchor_clips.values()
            )
        _write_json_atomic(anchor_path, anchor_document)
        written_anchor = _load_anchor(anchor_path)
        serialized_bd: list[float] = []
        for name in names:
            written_curve, _ = _anchor_clip(written_anchor, name)
            written_points = _curve_points(written_curve, "psnr_yuv")
            candidate_points = _curve_points(candidate_curves[name], "psnr_yuv")
            assert written_points is not None and candidate_points is not None
            value = float(runner.compute_bd(written_points, candidate_points))
            serialized_bd.append(value)
            per_clip_metrics[name]["bd_rate"] = value
        bd_mean = statistics.fmean(serialized_bd)
        score = 100.0 + bd_mean
        if score != 100.0:
            raise AssertionError(
                f"anchor self-test score was {score}, expected exactly 100.0"
            )

    metrics = {
        "bd_rate_mean": bd_mean,
        "per_clip": per_clip_metrics,
        "guard": {
            "vmaf_neg_bd_mean": _mean_optional(vmaf_values),
            "ssim_bd_mean": _mean_optional(ssim_values),
        },
        "gates": {
            "conformance": "pass",
            "determinism": "pass",
            "rd_validity": "pass",
            "speed": {
                "status": speed_result.status,
                "geomean_ratio": speed_result.geomean_ratio,
                "per_clip": speed_result.per_clip,
            },
        },
        "bitstream_manifest_sha256": _manifest_digest(encoded),
        "fingerprint": fingerprint,
        "estimate": estimate,
        "timing": timing_metrics,
        "ladder_cpu": ladder_cpu_metrics,
        "harness_version": _HARNESS_VERSION,
    }
    _write_json_atomic(score_path, {"score": score, "metrics": metrics})
    _stage("score", len(names), total_started)
    print(f"SCORE {score} (bd_rate_mean {bd_mean}%)", flush=True)
    return score
