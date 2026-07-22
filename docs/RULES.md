# videofast-challenge — Rules

## What you may edit

Only `submission/` (the vendored SVT-AV1 v4.2.0 source tree). Everything else —
harness, workflow, corpus manifest, anchor data — is rebuilt from the trusted
baseline on every submission; edits outside `submission/` have no effect and
Yukon strips them.

## The contract your encoder must keep

- The harness builds your tree with a fixed command inside the pinned toolchain
  container and expects an executable at `submission/Bin/Release/SvtAv1EncApp`.
- It is invoked with a fixed argument list (see `harness/config.json`):
  y4m in, IVF out, `--preset 6 --lp 1` CRF mode, one pass, 64 frames. Your
  binary must accept these flags; you are free to reinterpret internals.
- Output must be a conformant AV1 bitstream: **every bitstream is decoded with
  pinned dav1d; a decode failure fails the whole submission.**
- **Determinism is mandatory**: bit-identical bitstreams across repeated runs
  and across x86-64 machines. Designated clips are encoded twice per run and
  hash-compared; promoted commits are occasionally re-dispatched on a different
  VM and their full bitstream-hash manifest must reproduce. Nondeterminism —
  including ISA- or CPU-dependent output — is a disqualification, not a retry.

## Scoring

- Quality metric: PSNR-YUV = (6·PSNR_Y + PSNR_Cb + PSNR_Cr)/8, computed by the
  harness from dav1d-decoded output vs the source (exact integer SSE).
- Rate: sum of frame payload bytes in the IVF (container headers excluded).
- Per clip: Bjøntegaard BD-rate (PCHIP over log-rate) of your 5-point CRF curve
  vs the committed anchor curve. Score = `100 + mean over clips`, lower is
  better. The anchor scores exactly 100.000.
- Recorded but NOT scored (guard metrics): SSIM, VMAF-NEG. Large regressions on
  guard metrics will trigger maintainer review of the diff.

## Speed gate (pass/fail, not part of the score)

- Timing subset: designated clips at one CRF, 3 reps, candidate and anchor run
  back-to-back **in the same VM**, single-threaded, pinned to one core.
- Gate: geomean(candidate CPU-time / anchor CPU-time) ≤ **1.10** and every
  timing clip ≤ 1.30. CPU-time is user+sys of the whole process tree — spawning
  threads or children does not evade it.
- Hard wall-clock timeouts kill runaway encodes (5× anchor reference per clip).

## Forbidden

- **Network access** during build or encode (enforced: `--network=none`).
- **Reading the corpus at build time** (enforced: the build stage has no corpus
  mount). Anything precomputed must ship in your submission archive, where it
  is subject to the size cap and visible in the public PR diff.
- **Embedding corpus-derived data** (precomputed bitstreams, per-clip lookup
  tables keyed on clip identity). Content adaptivity must be a pure function of
  the input pixels. Enforcement: public diff audit, held-out spot checks, and
  corpus epochs — the corpus is periodically refreshed, and overfit gains
  collapse publicly when it rotates.
- **Tampering with harness state**: the trusted step re-derives every metric
  from the bitstreams alone and writes the score last; a pre-planted score file
  is overwritten or fails the run.

## Local development

`bash .yukon/setup.sh && bash .yukon/run.sh` works anywhere with docker (exact,
if your platform is x86-64 Linux) or natively on macOS/ARM (fast estimate; the
bitstream-hash manifest tells you whether your estimate is bit-exact with the
official run). Official numbers come only from the GitHub Actions run.

## Epochs

A corpus refresh starts a new epoch: new clips, regenerated anchor data, score
history resets. Epoch 1 corpus is public by design; robustness to refresh is
part of what this benchmark measures.
