"""Minimal YUV4MPEG2 parsing for 8-bit 4:2:0 benchmark clips."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


_SUPPORTED_COLORSPACES = {"C420", "C420jpeg", "C420mpeg2"}


@dataclass(frozen=True, slots=True)
class Y4MInfo:
    width: int
    height: int
    fps_num: int
    fps_den: int
    colorspace: str


def _parse_header_line(line: bytes, path: Path) -> Y4MInfo:
    if not line.endswith(b"\n"):
        raise ValueError(f"invalid Y4M header in {path}: missing newline")
    try:
        tokens = line.rstrip(b"\r\n").decode("ascii").split()
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid Y4M header in {path}: header is not ASCII") from exc
    if not tokens or tokens[0] != "YUV4MPEG2":
        raise ValueError(f"invalid Y4M header in {path}: missing YUV4MPEG2 signature")

    tagged: dict[str, str] = {}
    for token in tokens[1:]:
        if token and token[0] in {"W", "H", "F", "C"}:
            tagged[token[0]] = token
    try:
        width = int(tagged["W"][1:])
        height = int(tagged["H"][1:])
        fps_num_text, fps_den_text = tagged["F"][1:].split(":", 1)
        fps_num = int(fps_num_text)
        fps_den = int(fps_den_text)
        colorspace = tagged["C"]
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"invalid Y4M header in {path}: W, H, F, and C fields are required"
        ) from exc

    if width <= 0 or height <= 0 or fps_num <= 0 or fps_den <= 0:
        raise ValueError(f"invalid Y4M header in {path}: dimensions and fps must be positive")
    if colorspace not in _SUPPORTED_COLORSPACES:
        raise ValueError(
            f"unsupported Y4M colorspace {colorspace!r} in {path}; "
            "only 8-bit C420, C420jpeg, and C420mpeg2 are supported"
        )
    return Y4MInfo(width, height, fps_num, fps_den, colorspace)


def parse_header(path: str | Path) -> Y4MInfo:
    """Parse and validate the stream header without importing numpy."""

    source = Path(path)
    try:
        with source.open("rb") as handle:
            line = handle.readline()
    except OSError as exc:
        raise ValueError(f"cannot read Y4M file {source}: {exc}") from exc
    return _parse_header_line(line, source)


def read_frames(
    path: str | Path, max_frames: int | None = None
) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Yield Y, Cb, and Cr uint8 arrays from a Y4M stream."""

    import numpy as np

    source = Path(path)
    if max_frames is not None and max_frames < 0:
        raise ValueError("max_frames must be non-negative or None")

    with source.open("rb") as handle:
        info = _parse_header_line(handle.readline(), source)
        chroma_width = (info.width + 1) // 2
        chroma_height = (info.height + 1) // 2
        y_size = info.width * info.height
        chroma_size = chroma_width * chroma_height
        frame_size = y_size + 2 * chroma_size
        index = 0

        while max_frames is None or index < max_frames:
            marker = handle.readline()
            if marker == b"":
                return
            marker = marker.rstrip(b"\r\n")
            if marker != b"FRAME" and not (
                marker.startswith(b"FRAME")
                and len(marker) > 5
                and marker[5:6].isspace()
            ):
                raise ValueError(
                    f"invalid Y4M frame marker at frame {index} in {source}"
                )

            payload = handle.read(frame_size)
            if len(payload) != frame_size:
                raise ValueError(
                    f"truncated Y4M frame {index} in {source}: "
                    f"expected {frame_size} bytes, got {len(payload)}"
                )
            pixels = np.frombuffer(payload, dtype=np.uint8)
            y = pixels[:y_size].reshape(info.height, info.width).copy()
            cb = pixels[y_size : y_size + chroma_size].reshape(
                chroma_height, chroma_width
            ).copy()
            cr = pixels[y_size + chroma_size :].reshape(
                chroma_height, chroma_width
            ).copy()
            yield y, cb, cr
            index += 1

