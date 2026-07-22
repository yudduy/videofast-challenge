from __future__ import annotations

import pytest

from harness.bdrate import bd_rate
from harness.gates import GateError


ANCHOR = [
    (100.0, 30.0),
    (180.0, 32.0),
    (310.0, 34.0),
    (520.0, 36.0),
    (820.0, 38.0),
]


def test_identical_curves_are_zero() -> None:
    assert bd_rate(ANCHOR, ANCHOR) == pytest.approx(0.0, abs=1e-12)


def test_uniform_ten_percent_rate_increase_is_ten_percent() -> None:
    candidate = [(rate * 1.10, quality) for rate, quality in ANCHOR]

    assert bd_rate(ANCHOR, candidate) == pytest.approx(10.0, abs=1e-9)


def test_non_monotonic_quality_is_rd_validity_failure() -> None:
    duplicate_quality = [
        (100.0, 30.0),
        (180.0, 32.0),
        (310.0, 32.0),
        (520.0, 36.0),
    ]

    with pytest.raises(GateError, match="strictly increasing") as error:
        bd_rate(ANCHOR, duplicate_quality)
    assert error.value.gate == "rd-validity"


def test_non_monotonic_rate_is_rd_validity_failure() -> None:
    non_monotonic = [
        (100.0, 30.0),
        (300.0, 32.0),
        (250.0, 34.0),
        (520.0, 36.0),
    ]

    with pytest.raises(GateError) as error:
        bd_rate(ANCHOR, non_monotonic)
    assert error.value.gate == "rd-validity"


def test_disjoint_quality_ranges_are_rejected() -> None:
    candidate = [
        (100.0, 40.0),
        (180.0, 42.0),
        (310.0, 44.0),
        (520.0, 46.0),
    ]

    with pytest.raises(GateError, match="no overlap") as error:
        bd_rate(ANCHOR, candidate)
    assert error.value.gate == "rd-validity"


def test_unsorted_inputs_are_sorted_internally() -> None:
    shuffled_anchor = [ANCHOR[index] for index in (3, 0, 4, 1, 2)]
    shuffled_candidate = [ANCHOR[index] for index in (1, 4, 0, 2, 3)]

    assert bd_rate(shuffled_anchor, shuffled_candidate) == pytest.approx(0.0, abs=1e-12)

