# VERIFICATION.md — ground-truth: does the benchmark measure *real* encoding?

Skeptical end-to-end check that the score reflects real, quality-preserving video
compression and not a harness artifact. Tool: `scripts/ground_truth.py` (anchor vs the
confirmed candidate `combo_confirmed_v1`, CRF ladder, cross-checked against tools the
harness never uses: ffmpeg's own decode + PSNR/SSIM/VMAF filters, and the independent
`aomdec` libaom decoder).

## Results

| Check | Result |
|---|---|
| **Real video out** | decoded frames are pixel-clean real content (visually identical to source; no artifacts) |
| **Standard-conformant** | `dav1d` **and** `aomdec` (independent AV1 decoder engines) decode the bitstream to **bit-identical pixels** (PSNR = ∞) |
| **Harness PSNR is correct** | harness PSNR-YUV == ffmpeg's independent `psnr` filter to **0.0000 dB** (max abs diff 3.8e-7) |
| **The win is real** | BD(harness PSNR) = BD(ffmpeg PSNR) exactly: −2.72% (Vlog 360p), −5.98% (NewsClip 1080p) |
| **Quality preserved, not gamed** | two *independent* metrics agree the candidate is better: SSIM −4.46% / −7.45%, VMAF-neg −0.50% / −0.58% |

All four quality metrics (harness PSNR, ffmpeg PSNR, SSIM, VMAF-neg) move the same
direction — the −2.36% full-corpus win is genuine rate-distortion efficiency, confirmed by
tools outside the scoring pipeline.

## Conclusion on the evaluator

The harness (pinned dav1d decode + exact integer-SSE PSNR in numpy) is **provably correct**
— identical to ffmpeg's PSNR to 7 decimals — while being self-contained, bit-deterministic,
and minimal-attack-surface. It is the canonical scorer. ffmpeg is retained only as an
out-of-band audit; SSIM/VMAF are already recorded by the harness as guard metrics via the
pinned `vmaf` binary. Do **not** swap the evaluator to ffmpeg: it would add a large
dependency and floating-point/version variance for zero accuracy gain.
