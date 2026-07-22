#!/usr/bin/env python3
"""Gate-free ablation: measure each candidate setting's exact BD-rate and CPU ratio.

The ranking engine for the autoresearch loop. Appending a CLI flag overrides SVT-AV1's
preset-derived default, so a shim `anchor + extra_flags` reproduces exactly what a
source-default change to preset 6 would encode — letting us ablate candidate settings
with NO rebuilds. BD-rate is exact (cross-ISA determinism proven); CPU is native
user+sys, candidate-vs-baseline on the same machine (consistent basis, a good proxy for
the official cgroup ratio for single-threaded encodes). No speed gate is enforced here —
we want the BD/CPU tradeoff for every candidate, including ones that overspend CPU, so
the loop can pick the ones that fit the ≤1.10x budget.

Usage: scripts/ablate.py [seeds.json] [--corpus corpus/ablation_manifest.json]
       [--out docs/ablation_ledger.json]
seeds.json = [{"label": str, "flags": ["--enable-tf","2", ...], "family": str, "note": str}]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.bdrate import bd_rate
from harness.ivf import payload_bytes
from harness.metrics import clip_psnr
from harness.y4m import parse_header

REPO = Path(__file__).resolve().parent.parent
ANCHOR_BIN = REPO / ".cache/work/anchor_src/Bin/Release/SvtAv1EncApp"
CACHE = REPO / ".cache/corpus"
FRAMES = 64
CRFS = [23, 31, 39, 47, 55]
BASE_ARGS = ["--preset", "6", "--lp", "1", "--keyint", "-1", "--scd", "0",
             "--film-grain", "0", "--passes", "1", "--progress", "0", "-n", str(FRAMES)]
_TIME = re.compile(r"([0-9.]+)\s+user\s+([0-9.]+)\s+sys")


def encode(clip: Path, crf: int, out: Path, extra: list[str]) -> float:
    argv = ["/usr/bin/time", "-l", str(ANCHOR_BIN), "-i", str(clip), "-b", str(out),
            *BASE_ARGS, "--crf", str(crf), *extra]
    p = subprocess.run(argv, capture_output=True, text=True)
    if p.returncode != 0 or not out.is_file():
        raise RuntimeError(f"encode failed crf{crf} {clip.name} {extra}: {p.stderr[-300:]}")
    m = _TIME.search(p.stderr)
    return float(m.group(1)) + float(m.group(2)) if m else float("nan")


def curve(clips: list[tuple[str, Path, float]], extra: list[str], tmp: Path):
    """Return {clip: [(kbps, psnr_yuv)]} and total CPU seconds for a setting."""
    curves: dict[str, list[tuple[float, float]]] = {}
    total_cpu = 0.0
    for name, path, fps in clips:
        pts = []
        for crf in CRFS:
            ivf = tmp / f"{name}.{crf}.ivf"
            total_cpu += encode(path, crf, ivf, extra)
            dec = tmp / f"{name}.{crf}.y4m"
            subprocess.run(["dav1d", "-i", str(ivf), "-o", str(dec)],
                           capture_output=True, check=True)
            q = clip_psnr(path, dec, FRAMES)["psnr_yuv"]
            pts.append((payload_bytes(ivf) * 8.0 * fps / FRAMES / 1000.0, q))
            dec.unlink(); ivf.unlink()
        curves[name] = pts
    return curves, total_cpu


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds", nargs="?", default=str(REPO / "scripts/seeds.json"))
    ap.add_argument("--corpus", default=str(REPO / "corpus/ablation_manifest.json"))
    ap.add_argument("--out", default=str(REPO / "docs/ablation_ledger.json"))
    args = ap.parse_args()

    manifest = json.load(open(args.corpus))
    clips = []
    for c in manifest["clips"]:
        p = CACHE / c["name"]
        info = parse_header(p)
        clips.append((c["name"], p, info.fps_num / info.fps_den))
    seeds = json.load(open(args.seeds))
    print(f"[ablate] {len(clips)} clips x {len(CRFS)} CRF, {len(seeds)} candidates", flush=True)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        print("[baseline] plain preset-6 anchor ...", flush=True)
        base_curves, base_cpu = curve(clips, [], tmp)
        rows = []
        for s in seeds:
            try:
                cand_curves, cand_cpu = curve(clips, s["flags"], tmp)
                bds = [bd_rate(base_curves[n], cand_curves[n]) for n, _, _ in clips]
                bd_mean = sum(bds) / len(bds)
                cpu_ratio = cand_cpu / base_cpu
                rows.append({"label": s["label"], "family": s.get("family", ""),
                             "flags": s["flags"], "bd_rate_mean": round(bd_mean, 3),
                             "cpu_ratio": round(cpu_ratio, 3),
                             "fits_budget": cpu_ratio <= 1.10,
                             "per_clip_bd": {n: round(b, 2) for (n, _, _), b in zip(clips, bds)},
                             "note": s.get("note", "")})
                flag = "OK " if cpu_ratio <= 1.10 else "SLOW"
                print(f"  [{flag}] {s['label']:<28} BD {bd_mean:+6.2f}%  CPU {cpu_ratio:.2f}x", flush=True)
            except Exception as e:
                rows.append({"label": s["label"], "error": str(e)[:200]})
                print(f"  [ERR ] {s['label']:<28} {str(e)[:80]}", flush=True)

    rows.sort(key=lambda r: r.get("bd_rate_mean", 999))
    json.dump({"corpus": [c[0] for c in clips], "crfs": CRFS, "candidates": rows},
              open(args.out, "w"), indent=2)
    print(f"\n[done] {args.out}")
    print("\nRanked (best BD first; ★ = fits ≤1.10x budget):")
    for r in rows:
        if "bd_rate_mean" in r:
            star = "★" if r["fits_budget"] else " "
            print(f"  {star} {r['bd_rate_mean']:+6.2f}%  {r['cpu_ratio']:.2f}x  {r['label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
