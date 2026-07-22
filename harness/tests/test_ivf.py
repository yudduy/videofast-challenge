from __future__ import annotations

from pathlib import Path
import struct

import pytest

from harness.ivf import frame_count, payload_bytes


def _ivf_bytes(frames: list[bytes]) -> bytes:
    header = struct.pack(
        "<4sHH4sHHIIII",
        b"DKIF",
        0,
        32,
        b"AV01",
        16,
        16,
        30,
        1,
        len(frames),
        0,
    )
    body = bytearray()
    for pts, frame in enumerate(frames):
        body.extend(struct.pack("<IQ", len(frame), pts))
        body.extend(frame)
    return header + body


def test_payload_bytes_and_frame_count(tmp_path: Path) -> None:
    path = tmp_path / "tiny.ivf"
    path.write_bytes(_ivf_bytes([b"abc", b"12345"]))

    assert payload_bytes(path) == 8
    assert frame_count(path) == 2


@pytest.mark.parametrize("trim", [1, 6, 13])
def test_truncated_file_is_rejected(tmp_path: Path, trim: int) -> None:
    path = tmp_path / "truncated.ivf"
    path.write_bytes(_ivf_bytes([b"abc", b"12345"])[:-trim])

    with pytest.raises(ValueError, match="truncated IVF"):
        payload_bytes(path)


def test_rejects_wrong_fourcc(tmp_path: Path) -> None:
    path = tmp_path / "vp9.ivf"
    data = bytearray(_ivf_bytes([]))
    data[8:12] = b"VP90"
    path.write_bytes(data)

    with pytest.raises(ValueError, match="expected AV01"):
        payload_bytes(path)

