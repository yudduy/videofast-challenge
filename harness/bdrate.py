"""PCHIP-based Bjontegaard delta-rate calculation."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy.interpolate import PchipInterpolator

from harness.gates import GateError


Point = tuple[float, float]


def _prepare(points: Iterable[tuple[float, float]], curve: str) -> tuple[np.ndarray, np.ndarray]:
    prepared: list[Point] = []
    for rate, quality in points:
        rate_float = float(rate)
        quality_float = float(quality)
        if not math.isfinite(rate_float) or rate_float <= 0.0:
            raise GateError("rd-validity", f"{curve} rates must be finite and positive")
        if not math.isfinite(quality_float):
            raise GateError("rd-validity", f"{curve} qualities must be finite")
        prepared.append((rate_float, quality_float))

    if len(prepared) < 4:
        raise GateError(
            "rd-validity", f"{curve} curve requires at least 4 RD points"
        )
    prepared.sort(key=lambda point: point[1])
    rates = np.asarray([point[0] for point in prepared], dtype=np.float64)
    qualities = np.asarray([point[1] for point in prepared], dtype=np.float64)
    if np.any(np.diff(qualities) <= 0.0):
        raise GateError(
            "rd-validity", f"{curve} quality values are not strictly increasing"
        )
    if np.any(np.diff(rates) <= 0.0):
        raise GateError(
            "rd-validity",
            f"{curve} rate values are not strictly increasing with quality",
        )
    return qualities, np.log10(rates)


def bd_rate(
    anchor_points: Iterable[tuple[float, float]],
    candidate_points: Iterable[tuple[float, float]],
) -> float:
    """Return candidate BD-rate percentage relative to the anchor curve."""

    anchor_quality, anchor_log_rate = _prepare(anchor_points, "anchor")
    candidate_quality, candidate_log_rate = _prepare(candidate_points, "candidate")
    lo = max(float(anchor_quality[0]), float(candidate_quality[0]))
    hi = min(float(anchor_quality[-1]), float(candidate_quality[-1]))
    if hi <= lo:
        raise GateError("rd-validity", "no overlap between RD quality ranges")

    anchor_curve = PchipInterpolator(anchor_quality, anchor_log_rate)
    candidate_curve = PchipInterpolator(candidate_quality, candidate_log_rate)
    anchor_integral = anchor_curve.antiderivative()
    candidate_integral = candidate_curve.antiderivative()
    average_difference = (
        (candidate_integral(hi) - candidate_integral(lo))
        - (anchor_integral(hi) - anchor_integral(lo))
    ) / (hi - lo)
    return (10.0 ** float(average_difference) - 1.0) * 100.0

