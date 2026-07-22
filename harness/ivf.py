"""Small stdlib-only IVF parser for AV1 bitstream accounting."""

from __future__ import annotations

from pathlib import Path
import struct


_FILE_HEADER = struct.Struct("<4sHH4sHHIIII")
_FRAME_HEADER = struct.Struct("<IQ")


def _scan(path: str | Path) -> tuple[int, int]:
    source = Path(path)
    try:
        with source.open("rb") as handle:
            header = handle.read(_FILE_HEADER.size)
            if len(header) != _FILE_HEADER.size:
                raise ValueError(
                    f"truncated IVF header in {source}: expected 32 bytes, got {len(header)}"
                )
            signature, _version, header_size, fourcc, *_rest = _FILE_HEADER.unpack(
                header
            )
            if signature != b"DKIF":
                raise ValueError(f"invalid IVF signature in {source}: expected DKIF")
            if fourcc != b"AV01":
                raise ValueError(
                    f"unsupported IVF fourcc {fourcc!r} in {source}: expected AV01"
                )
            if header_size != _FILE_HEADER.size:
                raise ValueError(
                    f"invalid IVF header size {header_size} in {source}: expected 32"
                )

            total = 0
            count = 0
            while True:
                frame_header = handle.read(_FRAME_HEADER.size)
                if frame_header == b"":
                    return total, count
                if len(frame_header) != _FRAME_HEADER.size:
                    raise ValueError(
                        f"truncated IVF frame header {count} in {source}: "
                        f"expected 12 bytes, got {len(frame_header)}"
                    )
                size, _pts = _FRAME_HEADER.unpack(frame_header)
                payload = handle.read(size)
                if len(payload) != size:
                    raise ValueError(
                        f"truncated IVF frame {count} in {source}: "
                        f"expected {size} bytes, got {len(payload)}"
                    )
                total += size
                count += 1
    except OSError as exc:
        raise ValueError(f"cannot read IVF file {source}: {exc}") from exc


def payload_bytes(path: str | Path) -> int:
    """Return the sum of encoded frame payload sizes after validating the IVF."""

    return _scan(path)[0]


def frame_count(path: str | Path) -> int:
    """Return the number of complete IVF frames after validating the IVF."""

    return _scan(path)[1]

