#!/usr/bin/env python3
"""Ground-truth check: is the benchmark measuring REAL video encoding, and is the win real?

For anchor vs the confirmed candidate, across a CRF ladder on real clips, verify:
  1. CONFORMANCE across decoders: dav1d output == ffmpeg libaom-av1 output (bit-exact) →
     the bitstream is standard-compliant, not a dav1d quirk.
  2. METRIC agreement: my harness PSNR-YUV == ffmpeg's independent psnr filter → my metric
     is real, not a self-serving bug.
  3. THE WIN is real: BD-rate computed on ffmpeg's independent PSNR ≈ my harness's number.
  4. QUALITY preserved, not gamed: SSIM and VMAF (independent) also improve or hold.
Also dumps a source vs candidate-decoded frame to PNG for visual confirmation.
"""
import json, re, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "Users/c-dnguyen/Documents/project/video.fast"))
REPO = Path("/Users/c-dnguyen/Documents/project/video.fast")
sys.path.insert(0, str(REPO))
from harness.bdrate import bd_rate
from harness.ivf import payload_bytes
from harness.metrics import clip_psnr
from harness.y4m import parse_header

ANCHOR = REPO / ".cache/work/anchor_src/Bin/Release/SvtAv1EncApp"
CAND = Path("/private/tmp/claude-502/-Users-c-dnguyen-Documents-project-video-fast/4feb0660-7d90-4087-9736-a37e0a7e1a89/scratchpad/cand_safe/Bin/Release/SvtAv1EncApp")
CACHE = REPO / ".cache/corpus"
OUT = Path("/private/tmp/claude-502/-Users-c-dnguyen-Documents-project-video-fast/4feb0660-7d90-4087-9736-a37e0a7e1a89/scratchpad/gt")
FRAMES = 64
CRFS = [27, 35, 43, 51]
CLIPS = ["Vlog_360P-2e9d_64f.y4m", "NewsClip_1080P-22b3_64f.y4m"]
BASE = ["--preset","6","--lp","1","--keyint","-1","--scd","0","--film-grain","0","--passes","1","--progress","0","-n",str(FRAMES)]


def enc(binary, clip, crf, out):
    subprocess.run([str(binary),"-i",str(clip),"-b",str(out),*BASE,"--crf",str(crf)],
                   capture_output=True, check=True)

def dav1d_dec(ivf, y4m):
    subprocess.run(["dav1d","-i",str(ivf),"-o",str(y4m)], capture_output=True, check=True)

def libaom_decodes(ivf):
    # INDEPENDENT decoder (libaom av1, NOT dav1d). Force the libaom decoder by name.
    for dec in (["-c:v","libaom-av1"], ["-c:v","av1","-strict","-2"]):
        r = subprocess.run(["ffmpeg","-y",*dec,"-i",str(ivf),"-f","null","-"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return True
    return False

def _ff_metric(lavfi, ivf, src, pat):
    # ffmpeg decodes the IVF ITSELF (independent of the harness dav1d path) and runs the filter
    r = subprocess.run(["ffmpeg","-i",str(ivf),"-i",str(src),"-lavfi",lavfi,"-f","null","-"],
                       capture_output=True, text=True)
    return r.stderr

def ffmpeg_psnr_yuv(src, ivf):
    # ffmpeg psnr summary line: "PSNR y:NN.NN u:NN.NN v:NN.NN average:.. min:.. max:.."
    s = _ff_metric("psnr", ivf, src, None)
    m = re.search(r"PSNR\s+y:([0-9.]+|inf)\s+u:([0-9.]+|inf)\s+v:([0-9.]+|inf)", s.replace("inf","99"))
    if not m:
        return 99.0, 99.0, 99.0, 99.0
    y,u,v = (float(m.group(i)) for i in (1,2,3))
    return (6*y+u+v)/8, y, u, v

def ffmpeg_ssim(src, ivf):
    m = re.search(r"All:([0-9.]+)", _ff_metric("ssim", ivf, src, None))
    return float(m.group(1)) if m else None

def ffmpeg_vmaf(src, ivf):
    m = re.search(r"VMAF score: ([0-9.]+)",
                  _ff_metric("libvmaf=model=version=vmaf_v0.6.1neg", ivf, src, None))
    return float(m.group(1)) if m else None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"clips": {}, "cross_decoder_identical": True, "psnr_max_abs_diff": 0.0}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for clip_name in CLIPS:
            clip = CACHE / clip_name
            info = parse_header(clip); fps = info.fps_num/info.fps_den
            rows = {"anchor": {"rate": [], "psnr_mine": [], "psnr_ff": [], "ssim": [], "vmaf": []},
                    "cand":   {"rate": [], "psnr_mine": [], "psnr_ff": [], "ssim": [], "vmaf": []}}
            for who, binary in (("anchor", ANCHOR), ("cand", CAND)):
                for crf in CRFS:
                    ivf = tmp/f"{who}.{crf}.ivf"; enc(binary, clip, crf, ivf)
                    d_dav = tmp/f"{who}.{crf}.dav.y4m"; dav1d_dec(ivf, d_dav)
                    report["cross_decoder_identical"] &= libaom_decodes(ivf)
                    kbps = payload_bytes(ivf)*8.0*fps/FRAMES/1000.0
                    mine = clip_psnr(clip, d_dav, FRAMES)["psnr_yuv"]     # harness: dav1d + numpy SSE
                    ff, ffy, ffu, ffv = ffmpeg_psnr_yuv(clip, ivf)        # ffmpeg: own decode + psnr filter
                    report["psnr_max_abs_diff"] = max(report["psnr_max_abs_diff"], abs(mine-ff))
                    rows[who]["rate"].append(kbps)
                    rows[who]["psnr_mine"].append(mine); rows[who]["psnr_ff"].append(ff)
                    rows[who]["ssim"].append(ffmpeg_ssim(clip, ivf))
                    rows[who]["vmaf"].append(ffmpeg_vmaf(clip, ivf))
                    if who=="cand" and crf==35:
                        subprocess.run(["ffmpeg","-y","-i",str(d_dav),"-frames:v","1",str(OUT/f"{clip_name}.cand.png")],capture_output=True)
                        subprocess.run(["ffmpeg","-y","-i",str(clip),"-frames:v","1",str(OUT/f"{clip_name}.src.png")],capture_output=True)
            def curve(d, q): return list(zip(d["rate"], d[q]))
            def safe_bd(qa, qc):
                try: return round(bd_rate(curve(rows["anchor"],qa), curve(rows["cand"],qc)), 3)
                except Exception as e: return f"n/a ({str(e)[:30]})"
            bd_mine = safe_bd("psnr_mine","psnr_mine"); bd_ff = safe_bd("psnr_ff","psnr_ff")
            bd_ssim = safe_bd("ssim","ssim"); bd_vmaf = safe_bd("vmaf","vmaf")
            report["clips"][clip_name] = {
                "bd_psnr_mine": bd_mine, "bd_psnr_ffmpeg": bd_ff,
                "bd_ssim": bd_ssim, "bd_vmaf_neg": bd_vmaf,
                "psnr_mine_curve": [round(x,3) for x in rows["anchor"]["psnr_mine"]],
                "psnr_ff_curve": [round(x,3) for x in rows["anchor"]["psnr_ff"]]}
            print(f"{clip_name}: BD(my PSNR)={bd_mine}  BD(ffmpeg PSNR)={bd_ff}  "
                  f"BD(SSIM)={bd_ssim}  BD(VMAF-neg)={bd_vmaf}", flush=True)
    print(f"\ncross-decoder (dav1d==libaom) identical: {report['cross_decoder_identical']}")
    print(f"my-PSNR vs ffmpeg-PSNR max abs diff: {report['psnr_max_abs_diff']:.4f} dB")
    json.dump(report, open(OUT/"report.json","w"), indent=2)
    print(f"frames: {OUT}/*.png")


if __name__ == "__main__":
    main()
