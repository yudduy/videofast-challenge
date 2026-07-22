# CEILING.md — physics memo for videofast-challenge

> Bound first, optimize second. This memo authorizes optimization work: it says
> whether slack exists, where it lives, and when to stop. Versioned; revise when
> the scorer, regime, or a soft ceiling is reached. Numbers marked *(provisional,
> native arm64)* are cheap proxies for ranking; only Linux x86-64 official-regime
> runs update beliefs about the score.

## 1. The exact scorer (pinned)

Encoded, not prose: `harness/bdrate.py::bd_rate` + `harness/pipeline.py::cmd_run`.

```
score = 100 + mean_over_clips( BD_rate_PSNR-YUV( candidate_curve, anchor_curve ) )
```

- Quality: `PSNR-YUV = (6·PSNR_Y + PSNR_Cb + PSNR_Cr)/8`, exact integer SSE over
  64 frames (`harness/metrics.py`).
- BD-rate: PCHIP over log10(rate), integrated on the **overlapping** PSNR-YUV
  range (`harness/bdrate.py`). Anchor ≡ score 100.000. Lower is better.
- **Hard constraints** (not objective terms — they gate, and change the feasible
  set): every bitstream decodes with pinned dav1d; bit-exact determinism across
  x86-64; paired same-VM CPU-time **geomean ≤ 1.10×**, per-clip ≤ 1.30×, total
  ladder CPU ≤ 1.35× anchor. So the real objective is:

  **minimize PSNR-YUV BD-rate of a legal AV1 bitstream, decodable by dav1d,
  subject to ≤1.10× anchor (SVT-AV1 v4.2.0, preset 6, `--lp 1`) CPU-time.**

The speed floor is the whole game: without it the objective is unbounded-below
by spending compute (walk to preset 0). *With* it, the achievable set is narrow
and structured — see the ledger.

## 2. Ceilings table

| Bound | Value | Resource / argument | ⬛/⬜ | Provenance |
|---|---|---|---|---|
| **M4** info-theoretic R(D) of the source | unknown for video | true entropy of the clip ensemble; unmeasurable without the source distribution | ⬛ | Neural sandwich bounds exist for *images* only (~30% rate over VVC on Kodak); no credible video R(D) estimate published |
| **M3** next-syntax ceiling (AV2/AVM) | ≈ −25…−30% BD vs AV1 | what a *newer bitstream* buys; **unreachable here** — our decoder is dav1d/AV1 | ⬜ | AV2 Common Test Conditions, AV2 v13 vs AV1 random-access |
| **M2** cross-implementation marker (libaom) | *pending* | whether a *different* AV1 encoder extracts more at matched CPU — headroom that needs porting, not retuning | ⬜ | libaom `--cpu-used` sweep at matched CPU (to measure) |
| **M1c** unlimited-compute SVT ceiling (speed relaxed) | preset 2 = **−18.0%** BD *(provisional, arm64)*; preset 0 pending | oracle ablation of the speed constraint: best SVT-AV1 can do with its own tools given unbounded search | ⬜ | `scripts/ceiling_probe.py` preset sweep, `docs/ceiling_frontier.json` |
| **M1f** same-codebase floor (speed **binding**) | ≈ **−1.8%** BD at 1.10× CPU *(provisional, arm64)* | SVT's own preset frontier intersected with the 1.10× CPU budget — trivially reachable by preset-walking alone | ⬜ | `scripts/ceiling_probe.py`, linear interp to CPU ratio 1.10 |

### Measured SVT-AV1 BD-rate-vs-CPU frontier *(provisional, native arm64; 3 clips × 4 CRF)*

| preset | mean BD-rate vs anchor | mean CPU ratio vs anchor |
|---|---|---|
| 6 (anchor) | 0.00% | 1.00× |
| 5 | −6.18% | **1.35×** |
| 4 | −8.42% | 1.75× |
| 3 | −14.04% | 3.60× |
| 2 | −18.00% | 7.04× |

**The load-bearing fact:** preset 5 already costs **1.35×** CPU — *past* the 1.10×
geomean gate (and at the 1.35× total-CPU cap). You cannot wholesale adopt even one
slower preset within budget. Naive preset-blending buys only ≈ −1.8%. The entire
[−1.8%, −18%] interval below that is visible-but-not-trivially-reachable, unlocked
only by promoting *individual* slow-preset features whose BD-per-CPU is high enough
to fit the 10% budget.

**Binding actionable interval:** reachable headroom is **[M1f ≈ −1.8%, M1c ≈ −18%]**
BD-rate. That ~16-point gap is the reshuffle surface. M3/M4 bound the *whole field*
and are informational here (what AV1 syntax itself leaves on the table); they are
NOT reachable by any submission to this benchmark.

## 3. Baseline and headroom

Baseline = anchor = SVT-AV1 v4.2.0 preset 6 `--lp 1`, by construction at BD-rate
0.0 / score 100.000 (verified: `harness/anchor/anchor.json`, self-test asserts
exactly 100.0). Headroom is measured *downward* from 100: the easy floor is
score ≈ 98.2 (M1f), the same-codebase ceiling score ≈ 82 (M1c, preset-2). A
submission at score 98 is barely past preset-blending; a submission approaching
~85 would be capturing most of what SVT's toolbox holds under the speed gate —
an outcome with no public precedent.

## 4. Headroom ledger — decompose [M1f → M1c]

The gap between "preset-walk within budget" (M1f) and "unlimited-compute SVT"
(M1c) is the reshuffle surface. Named terms, each independently measurable, must
sum to the gap within noise; unexplained residual ⇒ measure, don't optimize.

| Term | Mechanism | How to measure its score-Δ | Candidate intervention |
|---|---|---|---|
| **T1 feature-Pareto un-bundling** | SVT bundles cheap-but-effective and expensive features into monolithic presets; some slower-preset features cost ≪ their BD gain | ablate each preset-5/4 feature into preset-6 individually, record ΔBD and ΔCPU | promote only high-BD-per-CPU features into the budget (seed family 2) |
| **T2 RD-lambda / QP mapping** | preset-invariant lambda + chroma-QP tables are not optimal for this corpus/metric | grid-perturb lambda & chroma offsets, hold speed | seed family 3 |
| **T3 quantization detail** | dead-zone / rounding offsets, cheap trellis toggles | toggle at fixed preset, ΔBD vs ΔCPU | seed family 4 |
| **T4 cross-impl technique** | libaom extracts value SVT doesn't at this budget (only if M2 shows a gap) | M2 marker minus M1c | port specific tools (seed family 5) |
| **Residual** | *declared after T1–T4 measured* | must be < BD-rate noise (~0.1%) or the system is not understood | — |

Noise band: anchor-vs-anchor identity run gives BD-rate σ ≈ 0 (bit-exact);
cross-preset σ dominated by the corpus, ~0.1–0.3 BD points on 15 clips.

## 5. Verdict

- **Where the slack is:** term **T1** (feature-Pareto un-bundling) — the benchmark
  is *designed* around it. The speed gate makes preset-walking (M1f) cheap and
  nearly exhausted on day one; everything past M1f requires breaking SVT's
  preset bundling to buy slower-preset quality that individually fits the 10%
  compute budget.
- **First target:** measure T1 — per-feature ΔBD/ΔCPU ablation at preset 6 — before
  writing any optimization. That ablation IS the map.
- **Stop condition:** a submission reaching the M1f→M1c frontier at 1.10× CPU has
  exhausted same-codebase headroom; further gain then requires M2/M3 techniques
  (porting or new syntax) and the benchmark should rotate corpus/epoch or open a
  new track rather than chase sub-noise deltas.
- **Gate status:** ledger terms not yet summed to the gap ⇒ **memo is provisional**;
  the only authorized next action on the optimization axis is the T1 ablation
  measurement, not a tuning attempt.

*Re-run this procedure when the official Linux x86-64 anchor lands (regime change
from the provisional arm64 numbers), when M2/preset-0 measurements complete, or
when any ⬜ soft ceiling is reached.*
