# Task & Operations Notes

## The task

Minimize PSNR-YUV BD-rate vs the pinned SVT-AV1 v4.2.0 anchor (preset 6,
single-thread, fixed CRF ladder) under the ≤1.10× paired CPU-time gate, by
modifying the encoder source in `submission/`. See docs/RULES.md for the
contract and docs/CEILING.md for how far the gap plausibly goes.

## Where the headroom is (seed families, expected-value order)

1. Preset feature-matrix retune: promote individual speed features toward
   slower-preset settings wherever measured CPU cost fits the 10% budget.
2. RD lambda tables, chroma QP offsets, TPL strength.
3. Quantization: dead-zone/rounding offsets, cheap trellis-style toggles.
4. libaom techniques absent from SVT-AV1 at this preset.
5. (Deprioritized under a PSNR score) psy/perceptual tuning — usually
   PSNR-negative by construction.

## Open questions for the Yukon team

- Submission archive size cap? The full `submission/` tree is ~15–25 MB
  compressed (vendored SVT-AV1 source).
- Confirm `minScoreImprovementBips` semantics = relative improvement on the
  current (always-positive) score.
- Re-baseline procedure when a corpus epoch rotates (score history resets).
- Are `preSubmitCommand` and `scoring`/`leaderboard` extension fields supported
  in prod as in mlxfast-challenge-dev?

## Maintainer runbook

- Toolchain image: dispatch `.github/workflows/toolchain.yml`, then pin the
  printed digest in `harness/config.json` (`toolchain_image`).
- Anchor regen: dispatch `benchmark.yml` with `regen_anchor=true` on the
  baseline commit, download the `anchor-data` artifact, commit its contents to
  `harness/anchor/`. Anchor data is only valid when generated on the official
  platform (Linux x86-64); locally generated anchor data is provisional and
  marked with an environment fingerprint.
- Cross-VM determinism audit: re-dispatch `benchmark.yml` on a promoted commit;
  the bitstream-hash manifest in the score artifact must be identical.
- Epoch rotation: new `corpus/manifest.json` → regen anchor → coordinate
  re-baseline with Yukon team.
