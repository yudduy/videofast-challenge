"""Configuration loading for the videofast scoring harness."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class PsnrWeights:
    y: int
    cb: int
    cr: int


@dataclass(frozen=True, slots=True)
class TimingConfig:
    clips: tuple[str, ...]
    crf: int
    reps: int
    geomean_max: float
    per_clip_max: float
    total_cpu_max: float
    wall_timeout_factor: float


@dataclass(frozen=True, slots=True)
class GuardMetricsConfig:
    vmaf_model: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class OfficialPlatform:
    system: str
    machine: str


@dataclass(frozen=True, slots=True)
class Config:
    """Validated harness configuration with root-relative paths resolved."""

    repo_root: Path
    svt_tag: str
    anchor_tarball: Path
    anchor_tarball_sha256: str
    toolchain_image: str
    encoder_binary_relpath: str
    encoder_args: tuple[str, ...]
    crf_ladder: tuple[int, ...]
    frames: int
    psnr_weights: PsnrWeights
    timing: TimingConfig
    determinism_clips: tuple[str, ...]
    guard_metrics: GuardMetricsConfig
    corpus_manifest: Path
    cache_dir: Path
    work_dir: Path
    anchor_data_dir: Path
    score_path: Path
    build_timeout_s: int
    official_platform: OfficialPlatform

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> Config:
        return load_config(path)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"config field {field!r} must be an object")
    return value


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"config field {field!r} must be an array")
    return value


def _root_path(repo_root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"config field {field!r} must be a non-empty string")
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def load_config(path: str | Path | None = None) -> Config:
    """Load a JSON config and resolve its path fields against the repository root."""

    config_path = (
        Path(path).expanduser().resolve()
        if path is not None
        else Path(__file__).resolve().with_name("config.json")
    )
    repo_root = config_path.parent.parent
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load harness config {config_path}: {exc}") from exc
    data = _mapping(raw, "root")

    try:
        weights = _mapping(data["psnr_weights"], "psnr_weights")
        timing = _mapping(data["timing"], "timing")
        guard = _mapping(data["guard_metrics"], "guard_metrics")
        platform = _mapping(data["official_platform"], "official_platform")
        encoder_args = _sequence(data["encoder_args"], "encoder_args")
        crf_ladder = _sequence(data["crf_ladder"], "crf_ladder")
        determinism_clips = _sequence(
            data["determinism_clips"], "determinism_clips"
        )
        timing_clips = _sequence(timing["clips"], "timing.clips")

        config = Config(
            repo_root=repo_root,
            svt_tag=str(data["svt_tag"]),
            anchor_tarball=_root_path(
                repo_root, data["anchor_tarball"], "anchor_tarball"
            ),
            anchor_tarball_sha256=str(data["anchor_tarball_sha256"]),
            toolchain_image=str(data["toolchain_image"]),
            encoder_binary_relpath=str(data["encoder_binary_relpath"]),
            encoder_args=tuple(str(arg) for arg in encoder_args),
            crf_ladder=tuple(int(crf) for crf in crf_ladder),
            frames=int(data["frames"]),
            psnr_weights=PsnrWeights(
                y=int(weights["y"]),
                cb=int(weights["cb"]),
                cr=int(weights["cr"]),
            ),
            timing=TimingConfig(
                clips=tuple(str(name) for name in timing_clips),
                crf=int(timing["crf"]),
                reps=int(timing["reps"]),
                geomean_max=float(timing["geomean_max"]),
                per_clip_max=float(timing["per_clip_max"]),
                total_cpu_max=float(timing["total_cpu_max"]),
                wall_timeout_factor=float(timing["wall_timeout_factor"]),
            ),
            determinism_clips=tuple(str(name) for name in determinism_clips),
            guard_metrics=GuardMetricsConfig(
                vmaf_model=str(guard["vmaf_model"]),
                enabled=bool(guard["enabled"]),
            ),
            corpus_manifest=_root_path(
                repo_root, data["corpus_manifest"], "corpus_manifest"
            ),
            cache_dir=_root_path(repo_root, data["cache_dir"], "cache_dir"),
            work_dir=_root_path(repo_root, data["work_dir"], "work_dir"),
            anchor_data_dir=_root_path(
                repo_root, data["anchor_data_dir"], "anchor_data_dir"
            ),
            score_path=_root_path(repo_root, data["score_path"], "score_path"),
            build_timeout_s=int(data["build_timeout_s"]),
            official_platform=OfficialPlatform(
                system=str(platform["system"]), machine=str(platform["machine"])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid harness config {config_path}: {exc}") from exc

    if config.frames <= 0:
        raise ValueError("config field 'frames' must be positive")
    if len(config.crf_ladder) < 4:
        raise ValueError("config field 'crf_ladder' must contain at least four values")
    if config.timing.reps <= 0:
        raise ValueError("config field 'timing.reps' must be positive")
    if (
        not math.isfinite(config.timing.total_cpu_max)
        or config.timing.total_cpu_max <= 0.0
    ):
        raise ValueError(
            "config field 'timing.total_cpu_max' must be finite and positive"
        )
    return config


# Short alias for callers that prefer ``config.load()``.
load = load_config
