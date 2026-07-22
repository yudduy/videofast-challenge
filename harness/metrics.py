"""Clip-level objective quality metrics."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from harness.y4m import parse_header, read_frames


def psnr_from_sse(sse: int, count: int) -> float:
    """Compute PSNR from an integer sum of squared errors and pixel count."""

    if sse < 0 or count <= 0:
        raise ValueError("PSNR requires non-negative SSE and a positive pixel count")
    if sse == 0:
        return 100.0
    mse = sse / count
    return 10.0 * math.log10((255.0 * 255.0) / mse)


def _plane_sse(source: np.ndarray, decoded: np.ndarray) -> int:
    if source.shape != decoded.shape:
        raise ValueError(
            f"Y4M plane shape mismatch: source {source.shape}, decoded {decoded.shape}"
        )
    difference = source.astype(np.int64) - decoded.astype(np.int64)
    return int(np.sum(difference * difference, dtype=np.int64))


def clip_psnr(
    src_path: str | Path, dec_path: str | Path, frames: int
) -> dict[str, float]:
    """Compute global per-plane and weighted PSNR over exactly ``frames`` frames."""

    if frames <= 0:
        raise ValueError("frames must be positive")
    source_info = parse_header(src_path)
    decoded_info = parse_header(dec_path)
    if (source_info.width, source_info.height) != (
        decoded_info.width,
        decoded_info.height,
    ):
        raise ValueError(
            "Y4M frame size mismatch: "
            f"source {source_info.width}x{source_info.height}, "
            f"decoded {decoded_info.width}x{decoded_info.height}"
        )

    source_frames = iter(read_frames(src_path))
    decoded_frames = iter(read_frames(dec_path))
    sse = [0, 0, 0]
    counts = [0, 0, 0]

    for index in range(frames):
        try:
            source_planes = next(source_frames)
        except StopIteration as exc:
            raise ValueError(
                f"source Y4M has only {index} frames; expected {frames}"
            ) from exc
        try:
            decoded_planes = next(decoded_frames)
        except StopIteration as exc:
            raise ValueError(
                f"decoded Y4M has only {index} frames; expected {frames}"
            ) from exc

        for plane_index, (source, decoded) in enumerate(
            zip(source_planes, decoded_planes, strict=True)
        ):
            sse[plane_index] += _plane_sse(source, decoded)
            counts[plane_index] += int(source.size)

    try:
        next(source_frames)
    except StopIteration:
        pass
    else:
        raise ValueError(f"source Y4M has more than the expected {frames} frames")
    try:
        next(decoded_frames)
    except StopIteration:
        pass
    else:
        raise ValueError(f"decoded Y4M has more than the expected {frames} frames")

    psnr_y, psnr_cb, psnr_cr = (
        psnr_from_sse(plane_sse, count)
        for plane_sse, count in zip(sse, counts, strict=True)
    )
    psnr_yuv = (6.0 * psnr_y + psnr_cb + psnr_cr) / 8.0
    return {
        "psnr_y": psnr_y,
        "psnr_cb": psnr_cb,
        "psnr_cr": psnr_cr,
        "psnr_yuv": psnr_yuv,
    }

