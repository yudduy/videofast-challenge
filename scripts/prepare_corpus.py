#!/usr/bin/env python3
"""Build the epoch-1 corpus from the YouTube UGC dataset (CC-BY user uploads).

One-time maintainer tool, committed for provenance. For each pick it range-fetches
a prefix of the raw-video MKV from the ugc-dataset GCS bucket, verifies the stream
is 8-bit yuv420p rawvideo, extracts the first 64 frames to Y4M, and emits
corpus/manifest.json entries pointing at this repo's `corpus-epoch1` GitHub release
(where the produced .y4m files are uploaded as assets). The published Y4M SHA-256s,
not this script, are the corpus ground truth.

Usage: python3 scripts/prepare_corpus.py <output_dir> [--manifest-only]
Requires: curl, ffmpeg, ffprobe on PATH.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

BUCKET = "https://storage.googleapis.com/ugc-dataset/original_videos"
RELEASE_BASE = (
    "https://github.com/yudduy/videofast-challenge/releases/download/corpus-epoch1"
)
FRAMES = 64

# (category, resolution dir, filename, prefix bytes to fetch)
PICKS = [
    ("Sports", "1080P", "Sports_1080P-08e1.mkv", 235_000_000),
    ("Gaming", "1080P", "Gaming_1080P-3a9d.mkv", 235_000_000),
    ("Vlog", "1080P", "Vlog_1080P-45c9.mkv", 235_000_000),
    ("NewsClip", "1080P", "NewsClip_1080P-22b3.mkv", 235_000_000),
    ("NewsClip", "720P", "NewsClip_720P-0c81.mkv", 110_000_000),
    ("HowTo", "720P", "HowTo_720P-0b01.mkv", 110_000_000),
    ("HowTo", "720P", "HowTo_720P-269e.mkv", 110_000_000),
    ("Lecture", "720P", "Lecture_720P-094d.mkv", 110_000_000),
    ("Vlog", "480P", "Vlog_480P-1b39.mkv", 55_000_000),
    ("NewsClip", "480P", "NewsClip_480P-2ba7.mkv", 55_000_000),
    ("HowTo", "480P", "HowTo_480P-15c1.mkv", 55_000_000),
    ("Lecture", "480P", "Lecture_480P-5aee.mkv", 55_000_000),
    ("Gaming", "360P", "Gaming_360P-3794.mkv", 35_000_000),
    ("Sports", "360P", "Sports_360P-11b7.mkv", 35_000_000),
    ("Vlog", "360P", "Vlog_360P-2e9d.mkv", 35_000_000),
]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_y4m_frames(path: Path) -> int:
    count = 0
    with path.open("rb") as f:
        data = f.read()
    pos = 0
    while True:
        pos = data.find(b"FRAME", pos)
        if pos == -1:
            return count
        count += 1
        pos += 5


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "corpus_build")
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for category, res, fname, prefix_bytes in PICKS:
        stem = fname.rsplit(".", 1)[0]
        y4m_name = f"{stem}_64f.y4m"
        y4m_path = out_dir / y4m_name
        src_url = f"{BUCKET}/{category}/{res}/{fname}"
        if not y4m_path.exists():
            tmp = out_dir / f"{stem}.prefix.mkv"
            if not tmp.exists() or tmp.stat().st_size < prefix_bytes:
                print(f"[fetch] {fname} first {prefix_bytes} bytes", flush=True)
                r = run(["curl", "-fsSL", "--retry", "3", "--max-time", "1200",
                         "-r", f"0-{prefix_bytes - 1}", "-o", str(tmp), src_url])
                if r.returncode != 0:
                    print(f"FAIL fetch {fname}: {r.stderr.strip()}")
                    return 1
            probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries",
                         "stream=codec_name,pix_fmt,width,height,r_frame_rate",
                         "-of", "json", str(tmp)])
            info = json.loads(probe.stdout)["streams"][0]
            if info["pix_fmt"] != "yuv420p":
                print(f"REJECT {fname}: pix_fmt={info['pix_fmt']} (need yuv420p) — swap this pick")
                return 1
            print(f"[extract] {y4m_name} {info['width']}x{info['height']} "
                  f"@{info['r_frame_rate']} {info['codec_name']}", flush=True)
            r = run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp),
                     "-frames:v", str(FRAMES), "-f", "yuv4mpegpipe", str(y4m_path)])
            if r.returncode != 0:
                print(f"FAIL extract {fname}: {r.stderr.strip()}")
                return 1
            got = count_y4m_frames(y4m_path)
            if got != FRAMES:
                print(f"FAIL {fname}: extracted {got} frames, wanted {FRAMES}")
                return 1
            tmp.unlink()
        digest = sha256_file(y4m_path)
        print(f"[ok] {y4m_name} {y4m_path.stat().st_size} bytes sha256={digest}")
        entries.append({
            "name": y4m_name,
            "url": f"{RELEASE_BASE}/{y4m_name}",
            "sha256": digest,
            "source": src_url,
            "attribution": "YouTube UGC Dataset (Creative Commons Attribution), "
                           "Wang, Inguva & Adsumilli, ICIP 2019",
        })
    manifest = {"epoch": 1, "frames": FRAMES, "clips": entries}
    manifest_path = Path("corpus/manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[done] wrote {manifest_path} with {len(entries)} clips")
    return 0


if __name__ == "__main__":
    sys.exit(main())
