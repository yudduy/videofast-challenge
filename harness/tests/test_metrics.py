from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from harness.metrics import clip_psnr, psnr_from_sse


def _write_y4m(path: Path, planes: tuple[np.ndarray, np.ndarray, np.ndarray]) -> None:
    payload = bytearray(b"YUV4MPEG2 W4 H2 F30:1 C420\nFRAME\n")
    for plane in planes:
        payload.extend(np.asarray(plane, dtype=np.uint8).tobytes())
    path.write_bytes(payload)


def _planes(value: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.full((2, 4), value, dtype=np.uint8),
        np.full((1, 2), value, dtype=np.uint8),
        np.full((1, 2), value, dtype=np.uint8),
    )


def test_identical_clip_uses_100_db_cap(tmp_path: Path) -> None:
    source = tmp_path / "source.y4m"
    decoded = tmp_path / "decoded.y4m"
    _write_y4m(source, _planes(17))
    _write_y4m(decoded, _planes(17))

    result = clip_psnr(source, decoded, 1)

    assert result == {
        "psnr_y": 100.0,
        "psnr_cb": 100.0,
        "psnr_cr": 100.0,
        "psnr_yuv": 100.0,
    }


def test_constant_one_error_has_analytic_psnr(tmp_path: Path) -> None:
    source = tmp_path / "source.y4m"
    decoded = tmp_path / "decoded.y4m"
    _write_y4m(source, _planes(20))
    _write_y4m(decoded, _planes(19))
    expected = 10.0 * math.log10(255.0**2)

    result = clip_psnr(source, decoded, 1)

    assert result["psnr_y"] == pytest.approx(expected, abs=1e-9)
    assert result["psnr_cb"] == pytest.approx(expected, abs=1e-9)
    assert result["psnr_cr"] == pytest.approx(expected, abs=1e-9)
    assert result["psnr_yuv"] == pytest.approx(expected, abs=1e-9)


def test_yuv_weighting_formula(tmp_path: Path) -> None:
    source = tmp_path / "source.y4m"
    decoded = tmp_path / "decoded.y4m"
    source_planes = _planes(50)
    decoded_planes = (
        np.full((2, 4), 49, dtype=np.uint8),
        np.full((1, 2), 48, dtype=np.uint8),
        np.full((1, 2), 50, dtype=np.uint8),
    )
    _write_y4m(source, source_planes)
    _write_y4m(decoded, decoded_planes)

    result = clip_psnr(source, decoded, 1)

    expected = (
        6.0 * result["psnr_y"] + result["psnr_cb"] + result["psnr_cr"]
    ) / 8.0
    assert result["psnr_yuv"] == expected


def test_psnr_from_sse_rejects_empty_plane() -> None:
    with pytest.raises(ValueError, match="positive pixel count"):
        psnr_from_sse(0, 0)

