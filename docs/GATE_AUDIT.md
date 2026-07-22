# GATE_AUDIT.md — speed-gate audit: resolution-conditional compute arbitrage

**Status: exploit CONFIRMED by demonstration (2026-07-22), gate FIXED same day.**
Official anchor re-baseline required before the next official run (see §4).

## 1. The hole (epoch-1 config as originally shipped)

The 1.10× "equal speed" gate is paired timing on 4 designated clips — all ≤720p.
Against the official anchor's per-clip ladder CPU (`harness/anchor/anchor.json`):

- The timing clips cover **19.6%** of total ladder CPU.
- The four 1080p clips are **47.5%** of ladder CPU and are **never timed**; they were
  bounded only by the 1.35× ladder-total cap and the 5× wall timeout.
- Worst case invisible to the pairing: 1080p at **1.74×** with everything else at
  1.00× — ladder total exactly 1.35×, paired gate reads ~1.00×.

Resolution-conditional compute is *legal generic conditioning* under RULES.md
(a pure function of input pixels; SVT itself sets levels by `input_resolution`
throughout), so this was not a forbidden technique — it was a benchmark bug:
the core "equal speed" claim was falsifiable.

## 2. The demonstration

Six lines in `enc_handle.c::set_param_based_on_input`, same idiom as SVT's own
resolution clamps (`enc_handle.c:4319`): after `svt_aom_derive_input_resolution`,

```c
if (scs->input_resolution >= INPUT_SIZE_1080p_RANGE && scs->static_config.enc_mode == ENC_M6)
    scs->static_config.enc_mode = ENC_M5;
```

Reproduce: apply `scripts/exploit_1080p_arbitrage.json` to a pristine
`submission/` tree, then `scripts/local_eval.py --candidate-src <tree>`.

Result (full 15-clip corpus, all gates, native local_eval, 2026-07-22):

| Measure | Value |
|---|---|
| **Score** | **97.86** (−2.14% BD) — cf. best *legitimate* candidate 97.64 |
| Clips changed | 4 of 15 (the 1080p tier); the other 11 bit-identical to anchor, BD = 0.000 |
| Per-clip BD (1080p) | Gaming −5.63 · Vlog −7.68 · Sports −8.62 · NewsClip −10.18 |
| Paired speed gate | geomean **0.9969** — reports the candidate *faster than anchor*; per-clip 0.991–1.001 |
| Ladder-CPU gate | 216.4 s vs 413.1 s cap — pass with 48% margin |
| True cost of the exploited tier | Vlog_1080P @CRF39, interleaved paired reps, same machine: anchor 6.84 s vs demo 8.84 s = **1.292×** |
| True ladder CPU | ≈ 0.525·1.00 + 0.475·~1.3 ≈ **1.15×** anchor — while the gate reports 0.997× |

Extrapolation to the cap: preset-4-class spend on 1080p (~1.7×) stays under the old
1.35× ladder cap and buys ≈ **−3% BD from arbitrage alone** — on top of, not instead
of, a legitimate 1.10×-everywhere candidate, since the hidden spend never touched the
paired budget. Score gains of this size were purchasable with compute while the
leaderboard column says "equal speed".

## 3. The fix (this commit series)

1. **Timing set spans every resolution tier** — one clip each:
   `Vlog_360P-2e9d`, `NewsClip_480P-2ba7`, `Lecture_720P-094d`, **`Vlog_1080P-45c9`**
   (the corpus's single heaviest clip, 15.7% of ladder CPU — guards the largest pool).
   CI cost: ≈ +1 min (6 extra 1080p CRF-39 encodes).
2. **Per-timing-clip max 1.30× → 1.20×.** The decisive kill: the demo's exploited
   tier measures 1.29×, failing decisively regardless of timing noise (legit
   candidates target ≤1.10; 3-rep paired medians are stable to ~±3%).
3. **Ladder-total cap 1.35× → 1.15×.** The cap is now "paired budget + noise
   margin", not a 25-point side channel. A full-budget legitimate candidate
   (true 1.10×) passes with ~2σ margin; the demo (~1.15×) sits at/over it.

Post-fix, the demonstration candidate fails the per-clip gate outright (1.29 > 1.20)
and has no ladder headroom; the same conditioning at gate-passing strength
(≤1.20× on 1080p) is now *observed, budget-competing spend* — it enters the
geomean at weight 1/4 and the ladder cap — rather than invisible and additive.

## 4. Re-baseline required (maintainer action)

`harness/anchor/anchor.json` must gain a `timing.cpu_reference` entry for
`Vlog_1080P-45c9_64f.y4m`: dispatch `benchmark.yml` with `regen_anchor=true` on
the official platform and commit the artifact (runbook in `docs/TASK.md`).
Until then, runs fail closed with "anchor CPU reference unavailable".

## 5. Residual risk (accepted, documented)

- **Tier-skew within the observed budget:** spending unevenly across tiers while
  passing all three gates (geomean 1.10 / per-clip 1.20 / ladder 1.15) remains
  possible but is bounded, visible in the public per-encode CPU metrics, and
  consumes the same budget legitimate candidates use.
- **Content-targeted spend dodging the timed clip** (hit the 3 untimed 1080p
  clips, skip Vlog-like content): bounded by the 1.15× cap at ≈ −1 BD worst case,
  and a pixel rule shaped to exclude specifically the timed clip is
  clip-fingerprinting — already forbidden and diff-visible.
- **Epoch-2 options if abused:** per-tier ratio caps, CPU-weighted ratio
  aggregate, or timing every clip at one CRF.
