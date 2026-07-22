from __future__ import annotations

import hashlib

import pytest

from harness.config import load_config
from harness.gates import GateError
from harness.pipeline import (
    _Encoded,
    _configured_or_fallback,
    _ladder_cpu_gate,
    _quality_one,
    _require_anchor_ladder_cpu_total,
    _wall_limit,
)


def test_configured_clip_names_fall_back_as_a_group() -> None:
    active = ["a.y4m", "b.y4m", "c.y4m"]

    assert _configured_or_fallback(("missing.y4m",), active, 2) == active[:2]
    assert _configured_or_fallback(("b.y4m",), active, 2) == ["b.y4m"]


def test_wall_limit_uses_exact_scaled_anchor_reference() -> None:
    anchor = {
        "clips": {
            "clip.y4m": {
                "curve": [{"crf": 39, "wall_seconds": 0.1}],
                "wall_times": {"39": 0.1},
            }
        }
    }

    assert _wall_limit(anchor, "clip.y4m", 39, 5.0) == pytest.approx(0.5)
    assert _wall_limit(None, "clip.y4m", 39, 5.0) == 600.0


def test_wall_limit_rejects_nonpositive_values() -> None:
    anchor = {
        "clips": {
            "clip.y4m": {
                "curve": [{"crf": 39, "wall_seconds": 0.0}],
            }
        }
    }

    with pytest.raises(RuntimeError, match="invalid wall timeout"):
        _wall_limit(anchor, "clip.y4m", 39, 5.0)


def test_config_loads_total_cpu_max() -> None:
    assert load_config().timing.total_cpu_max == pytest.approx(1.35)


def test_ladder_cpu_gate_scales_cap_by_paired_anchor_timings() -> None:
    anchor = {
        "ladder_cpu_total": 100.0,
        "timing": {"cpu_reference": {"a": 1.0, "b": 2.0}},
    }

    result = _ladder_cpu_gate(
        anchor,
        [10.0, 20.0],
        ["a", "b"],
        {"a": 2.0, "b": 8.0},
        1.35,
    )

    assert result == {
        "candidate_total": pytest.approx(30.0),
        "anchor_ref_total": pytest.approx(100.0),
        "vm_scale": pytest.approx(8.0**0.5),
        "cap": pytest.approx(135.0 * 8.0**0.5),
        "status": "pass",
    }


def test_ladder_cpu_gate_skips_without_anchor_total() -> None:
    anchor = {"timing": {"cpu_reference": {"a": 2.0}}}

    result = _ladder_cpu_gate(anchor, [3.0, 4.0], ["a"], {"a": 3.0}, 1.35)

    assert result == {
        "candidate_total": pytest.approx(7.0),
        "anchor_ref_total": None,
        "vm_scale": pytest.approx(1.5),
        "cap": None,
        "status": "skipped",
    }


def test_ladder_cpu_gate_requires_anchor_total_for_docker_scoring() -> None:
    anchor = {"timing": {"cpu_reference": {"a": 2.0}}}

    _require_anchor_ladder_cpu_total(anchor, mode="native", regen=False)
    _require_anchor_ladder_cpu_total(anchor, mode="docker", regen=True)
    with pytest.raises(
        RuntimeError, match="docker scoring requires an anchor ladder CPU reference"
    ):
        _require_anchor_ladder_cpu_total(anchor, mode="docker", regen=False)


def test_ladder_cpu_gate_skips_when_candidate_cpu_is_unknown() -> None:
    anchor = {
        "ladder_cpu_total": 100.0,
        "timing": {"cpu_reference": {"a": 2.0}},
    }

    result = _ladder_cpu_gate(anchor, [3.0, None], ["a"], {"a": 3.0}, 1.35)

    assert result == {
        "candidate_total": None,
        "anchor_ref_total": pytest.approx(100.0),
        "vm_scale": pytest.approx(1.5),
        "cap": pytest.approx(202.5),
        "status": "skipped",
    }


def test_ladder_cpu_gate_rejects_total_over_scaled_cap() -> None:
    anchor = {
        "ladder_cpu_total": 100.0,
        "timing": {"cpu_reference": {"a": 2.0}},
    }

    with pytest.raises(GateError, match="candidate ladder CPU total") as raised:
        _ladder_cpu_gate(anchor, [136.0], ["a"], {"a": 2.0}, 1.35)

    assert raised.value.gate == "speed"
    assert "anchor ladder CPU total 100" in raised.value.detail
    assert "scaled cap 135" in raised.value.detail


def test_quality_rehash_rejects_bitstream_changed_before_decode(tmp_path) -> None:
    class DecodeMustNotRun:
        called = False

        def decode(self, _input, _output) -> None:
            self.called = True

    bitstream = tmp_path / "clip.ivf"
    original = b"original bitstream"
    bitstream.write_bytes(original)
    encoded = _Encoded(
        name="clip.y4m",
        crf=39,
        path=bitstream,
        sha256=hashlib.sha256(original).hexdigest(),
        wall_seconds=1.0,
        cpu_seconds=0.5,
    )
    bitstream.write_bytes(b"corrupted bitstream")
    runner = DecodeMustNotRun()

    with pytest.raises(GateError, match="bitstream changed before decode") as raised:
        _quality_one(
            runner,  # type: ignore[arg-type]
            encoded,
            tmp_path / "source.y4m",
            tmp_path / "decoded",
            64,
            False,
            "unused-model",
        )

    assert raised.value.gate == "determinism"
    assert runner.called is False
