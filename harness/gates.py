"""Gate errors and shared scoring-gate helpers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Iterable, Mapping


class GateError(Exception):
    """A benchmark validity or performance gate failure."""

    def __init__(self, gate: str, detail: str) -> None:
        self.gate = gate
        self.detail = detail
        super().__init__(f"{gate}: {detail}")


def geomean(values: Iterable[float]) -> float:
    """Return the geometric mean of a non-empty sequence of positive values."""

    items = [float(value) for value in values]
    if not items:
        raise ValueError("geomean requires at least one value")
    if any(not math.isfinite(value) or value <= 0.0 for value in items):
        raise ValueError("geomean values must be finite and positive")
    return math.exp(math.fsum(math.log(value) for value in items) / len(items))


def _samples(value: Iterable[float] | float, label: str) -> list[float]:
    if isinstance(value, (int, float)):
        samples = [float(value)]
    else:
        samples = [float(sample) for sample in value]
    if not samples:
        raise ValueError(f"{label} has no timing samples")
    if any(not math.isfinite(sample) or sample <= 0.0 for sample in samples):
        raise ValueError(f"{label} timing samples must be finite and positive")
    return samples


def speed_ratios(
    anchor: Mapping[str, Iterable[float] | float],
    candidate: Mapping[str, Iterable[float] | float],
) -> dict[str, float]:
    """Compute candidate/anchor median CPU-time ratios for matching clips."""

    if set(anchor) != set(candidate):
        missing_candidate = sorted(set(anchor) - set(candidate))
        missing_anchor = sorted(set(candidate) - set(anchor))
        raise ValueError(
            "timing clip sets differ "
            f"(missing candidate={missing_candidate}, missing anchor={missing_anchor})"
        )
    if not anchor:
        raise ValueError("speed ratios require at least one clip")

    ratios: dict[str, float] = {}
    for name in sorted(anchor):
        anchor_median = float(median(_samples(anchor[name], f"anchor {name}")))
        candidate_median = float(
            median(_samples(candidate[name], f"candidate {name}"))
        )
        ratios[name] = candidate_median / anchor_median
    return ratios


@dataclass(frozen=True, slots=True)
class SpeedGateResult:
    status: str
    geomean_ratio: float
    per_clip: dict[str, float]


def check_speed(
    anchor: Mapping[str, Iterable[float] | float],
    candidate: Mapping[str, Iterable[float] | float],
    *,
    geomean_max: float,
    per_clip_max: float,
) -> SpeedGateResult:
    """Evaluate speed thresholds, raising ``GateError`` when either is exceeded."""

    ratios = speed_ratios(anchor, candidate)
    overall = geomean(ratios.values())
    result = SpeedGateResult("pass", overall, ratios)
    too_slow = {
        name: ratio for name, ratio in ratios.items() if ratio > float(per_clip_max)
    }
    if overall > float(geomean_max) or too_slow:
        detail = (
            f"geomean ratio {overall:.6g} (limit {geomean_max:.6g}); "
            f"per-clip ratios {ratios} (limit {per_clip_max:.6g})"
        )
        raise GateError("speed", detail)
    return result

