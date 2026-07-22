from __future__ import annotations

import math

import pytest

from harness.gates import GateError, check_speed, geomean, speed_ratios


def test_geomean() -> None:
    assert geomean([1.0, 4.0, 16.0]) == pytest.approx(4.0)


def test_geomean_rejects_empty_or_nonpositive_values() -> None:
    with pytest.raises(ValueError):
        geomean([])
    with pytest.raises(ValueError):
        geomean([1.0, 0.0])


def test_speed_ratios_use_per_clip_medians() -> None:
    anchor = {"b": [2.0, 4.0, 3.0], "a": [1.0, 1.0, 5.0]}
    candidate = {"a": [1.1, 1.0, 1.2], "b": [3.3, 3.0, 3.6]}

    ratios = speed_ratios(anchor, candidate)

    assert list(ratios) == ["a", "b"]
    assert ratios == pytest.approx({"a": 1.1, "b": 1.1})


def test_speed_gate_checks_geomean_and_per_clip_limits() -> None:
    anchor = {"a": [1.0], "b": [1.0]}
    passing = {"a": [1.05], "b": [1.15]}

    result = check_speed(
        anchor, passing, geomean_max=1.10, per_clip_max=1.20
    )

    assert result.status == "pass"
    assert result.geomean_ratio == pytest.approx(math.sqrt(1.05 * 1.15))
    assert result.per_clip == pytest.approx({"a": 1.05, "b": 1.15})

    with pytest.raises(GateError) as error:
        check_speed(
            anchor,
            {"a": [1.0], "b": [1.31]},
            geomean_max=2.0,
            per_clip_max=1.30,
        )
    assert error.value.gate == "speed"


def test_speed_gate_rejects_geomean_over_limit() -> None:
    with pytest.raises(GateError, match="geomean ratio"):
        check_speed(
            {"a": 1.0, "b": 1.0},
            {"a": 1.11, "b": 1.11},
            geomean_max=1.10,
            per_clip_max=1.30,
        )

