#!/usr/bin/env python3
"""Measure SVT-AV1's own BD-rate-vs-CPU Pareto frontier for the ceiling memo.

For each preset it encodes a subset of the corpus over a CRF sub-ladder using the
already-built anchor binary, times CPU (user+sys), decodes with dav1d, computes
PSNR-YUV with the harness metrics, and reports per-preset mean BD-rate vs the
preset-6 anchor curve and mean CPU ratio vs preset 6. The 1.10x-CPU intercept of
that frontier is the trivially-reachable same-codebase floor; the slowest preset
is the unlimited-compute ceiling (speed constraint relaxed).

Provisional/native by design (arm64) — official numbers come from a Linux x86-64
regime. Usage: python3 scripts/ceiling_probe.py <anchor_bin> [out.json]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.bdrate import bd_rate
from harness.metrics import clip_psnr

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / ".cache/corpus"
FRAMES = 64
PRESETS = [6, 5, 4, 3, 2]
CRFS = [27, 35, 43, 51]
CLIPS = [
    "Vlog_360P-2e9d_64f.y4m",
    "NewsClip_480P-2ba7_64f.y4m",
    "HowTo_480P-15c1_64f.y4m",
]
BASE_ARGS = ["--lp", "1", "--keyint", "-1", "--scd", "0",
             "--film-grain", "0", "--passes", "1", "--progress", "0", "-n", str(FRAMES)]
_TIME = re.compile(r"([0-9.]+)\s+user\s+([0-9.]+)\s+sys")


def encode(anchor: str, clip: Path, preset: int, crf: int, out: Path) -> float:
    argv = ["/usr/bin/time", "-l", anchor, "-i", str(clip), "-b", str(out),
            "--preset", str(preset), "--crf", str(crf), *BASE_ARGS]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"encode failed p{preset} crf{crf} {clip.name}: {proc.stderr[-300:]}")
    m = _TIME.search(proc.stderr)
    if not m:
        raise RuntimeError(f"cannot parse CPU time from: {proc.stderr[-300:]}")
    return float(m.group(1)) + float(m.group(2))


def kbps(out: Path, fps: float) -> float:
    from harness.ivf import payload_bytes
    return payload_bytes(out) * 8.0 * fps / FRAMES / 1000.0


def main() -> int:
    anchor = sys.argv[1]
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "docs/ceiling_frontier.json"
    from harness.y4m import parse_header
    curves: dict[int, dict[str, list[tuple[float, float]]]] = {p: {} for p in PRESETS}
    cpu: dict[int, dict[str, float]] = {p: {} for p in PRESETS}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for clip_name in CLIPS:
            clip = CACHE / clip_name
            info = parse_header(clip)
            fps = info.fps_num / info.fps_den
            for preset in PRESETS:
                pts: list[tuple[float, float]] = []
                total_cpu = 0.0
                for crf in CRFS:
                    ivf = tmp / f"{clip_name}.p{preset}.crf{crf}.ivf"
                    total_cpu += encode(anchor, clip, preset, crf, ivf)
                    dec = tmp / f"{clip_name}.p{preset}.crf{crf}.y4m"
                    subprocess.run(["dav1d", "-i", str(ivf), "-o", str(dec)],
                                   capture_output=True, check=True)
                    q = clip_psnr(clip, dec, FRAMES)["psnr_yuv"]
                    pts.append((kbps(ivf, fps), q))
                    dec.unlink()
                    ivf.unlink()
                curves[preset][clip_name] = pts
                cpu[preset][clip_name] = total_cpu
                print(f"  p{preset} {clip_name}: cpu={total_cpu:.2f}s", flush=True)
    rows = []
    for preset in PRESETS:
        bds, ratios = [], []
        for clip_name in CLIPS:
            bds.append(bd_rate(curves[6][clip_name], curves[preset][clip_name]))
            ratios.append(cpu[preset][clip_name] / cpu[6][clip_name])
        mean_bd = sum(bds) / len(bds)
        mean_ratio = sum(ratios) / len(ratios)
        rows.append({"preset": preset, "mean_bd_rate_pct": mean_bd, "mean_cpu_ratio": mean_ratio})
        print(f"preset {preset}: BD-rate {mean_bd:+.2f}%  CPU {mean_ratio:.2f}x")
    out_path.write_text(json.dumps({"clips": CLIPS, "crfs": CRFS, "frontier": rows}, indent=2) + "\n")
    print(f"[done] {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
