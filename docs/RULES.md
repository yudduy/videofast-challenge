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

- Timing subset: **one designated clip per resolution tier** (360p, 480p, 720p,
  1080p) at one CRF, 3 reps, candidate and anchor run back-to-back **in the same
  VM**, single-threaded, pinned to one core. The set spans every tier so
  resolution-conditional compute cannot hide from the pairing.
- Gate: geomean(candidate CPU-time / anchor CPU-time) ≤ **1.10** and every
  timing clip ≤ **1.20**. CPU-time is read from the encode container's
  **cgroup**, so it counts every process in the pid namespace — threads, forked
  children, and double-forked / reparented / un-reaped workers alike. There is
  no process you can spawn whose CPU escapes the meter.
- Hard wall-clock timeouts kill runaway encodes (5× anchor reference per clip).
- **Every** ladder encode is CPU-accounted, and total ladder CPU is capped at
  **1.15×** the anchor's recorded ladder total (scaled by the same-VM pairing
  ratio) — the paired budget plus a noise margin, not a side channel. History:
  the original epoch-1 config (≤720p-only timing set, 1.35× cap) allowed a
  resolution-conditional candidate to run the 1080p tier — 47% of ladder CPU —
  at ~1.5–1.7× while the paired gate read ~1.00×; `docs/GATE_AUDIT.md`
  demonstrates the exploit and this configuration closes it. Recorded
  per-encode CPU remains public.

## Forbidden

- **Network access** during build or encode (enforced: `--network=none`).
- **Reading the corpus at build time** (enforced: the build stage has no corpus
  mount). Anything precomputed must ship in your submission archive, where it
  is subject to the size cap and visible in the public PR diff.
- **Embedding corpus-derived data** (precomputed bitstreams, per-clip lookup
  tables keyed on clip identity). Content adaptivity must be a pure function of
  the input pixels. Enforcement is layered: your encoder never sees the corpus
  directory or a clip's real filename — each input is bind-mounted at a neutral
  path (`/input/src.y4m`) with no siblings to enumerate — plus public diff audit,
  held-out spot checks, and corpus epochs that collapse overfit gains on refresh.
- **Curve-placement games**: a candidate RD curve must overlap at least 60% of
  the anchor's quality span, or the run fails `rd-validity` — you cannot earn a
  BD-rate from a sliver of overlap at one end.
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
