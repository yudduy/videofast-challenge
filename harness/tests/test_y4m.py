from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from harness.y4m import Y4MInfo, parse_header, read_frames


def _write_y4m(path: Path, colorspace: str = "C420") -> list[tuple[np.ndarray, ...]]:
    frames: list[tuple[np.ndarray, ...]] = []
    for offset in (0, 20):
        y = np.arange(8, dtype=np.uint8).reshape(2, 4) + offset
        cb = np.array([[10 + offset, 11 + offset]], dtype=np.uint8)
        cr = np.array([[12 + offset, 13 + offset]], dtype=np.uint8)
        frames.append((y, cb, cr))

    payload = bytearray(f"YUV4MPEG2 W4 H2 F30:1 Ip A1:1 {colorspace}\n".encode())
    for index, planes in enumerate(frames):
        marker = b"FRAME\n" if index == 0 else b"FRAME Xfoo=bar\n"
        payload.extend(marker)
        for plane in planes:
            payload.extend(plane.tobytes())
    path.write_bytes(payload)
    return frames


@pytest.mark.parametrize("colorspace", ["C420", "C420jpeg", "C420mpeg2"])
def test_parse_header_and_read_frames_roundtrip(
    tmp_path: Path, colorspace: str
) -> None:
    path = tmp_path / "tiny.y4m"
    expected = _write_y4m(path, colorspace)

    assert parse_header(path) == Y4MInfo(4, 2, 30, 1, colorspace)
    actual = list(read_frames(path))

    assert len(actual) == 2
    for expected_planes, actual_planes in zip(expected, actual, strict=True):
        for expected_plane, actual_plane in zip(
            expected_planes, actual_planes, strict=True
        ):
            assert actual_plane.dtype == np.uint8
            np.testing.assert_array_equal(actual_plane, expected_plane)


def test_read_frames_honors_max_frames(tmp_path: Path) -> None:
    path = tmp_path / "tiny.y4m"
    expected = _write_y4m(path)

    actual = list(read_frames(path, max_frames=1))

    assert len(actual) == 1
    np.testing.assert_array_equal(actual[0][0], expected[0][0])


def test_rejects_ten_bit_colorspace(tmp_path: Path) -> None:
    path = tmp_path / "ten-bit.y4m"
    path.write_bytes(b"YUV4MPEG2 W4 H2 F30:1 C420p10\n")

    with pytest.raises(ValueError, match="only 8-bit"):
        parse_header(path)


def test_rejects_truncated_frame(tmp_path: Path) -> None:
    path = tmp_path / "truncated.y4m"
    path.write_bytes(b"YUV4MPEG2 W4 H2 F30:1 C420\nFRAME\n\x00")

    with pytest.raises(ValueError, match="truncated Y4M frame"):
        list(read_frames(path))

