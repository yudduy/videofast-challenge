# CEILING.md — physics memo for videofast-challenge (v3)

> Bound first, optimize second. v1 authorized the T1 ablation; v2 folded in the measured
> catalog + three literature passes; v3 (2026-07-22 pm) removes T5 after the speed-gate
> audit — the "envelope headroom" was a benchmark bug, now demonstrated and fixed
> (`docs/GATE_AUDIT.md`), so the reachable band recenters on the measured catalog.
> Re-run on scorer/regime change or when a ⬜ ceiling is reached. Regime tags:
> *(fast)* = 5-clip proxy corpus, *(full)* = official 15-clip, *(arm64)* = native provisional.

## 1. The exact scorer (pinned, verified)

Encoded, not prose: `harness/bdrate.py::bd_rate` + `harness/pipeline.py::cmd_run`.

```
score = 100 + mean_over_15_clips( BD_rate_PSNR-YUV( candidate_curve, anchor_curve ) )
```

- PSNR-YUV = (6·Y+Cb+Cr)/8, exact integer SSE, 64 frames; PCHIP over log10(rate) on the
  overlapping quality range; 60% min-overlap gate; anchor ≡ 100.000; lower is better.
- **Scorer verified ground-truth** (`docs/VERIFICATION.md`): harness PSNR == ffmpeg to
  4e-7 dB; dav1d == aomdec bit-identical; SSIM/VMAF-neg move with every confirmed win.
- Hard constraints (gates, not objective terms): conformant dav1d-decodable bitstreams;
  bit-exact cross-x86 determinism; paired same-VM CPU geomean ≤ **1.10×** on the 4 timing
  clips @ CRF 39 (3 reps, median), per-timing-clip ≤ 1.30×, **ladder total ≤ 1.35×**,
  wall 5×. Objective noise σ = 0 (bit-exact); effective score quantum = 5 bips ≈ 0.05 pts.
- **The metric is mispriced chroma — load-bearing.** 6:1:1 is the JVET convention; AOM CTC
  scores PSNR-YUV at 14:1:1 and encoder RD minimizes plain SSE (≈4:1:1 per-sample in
  4:2:0). This benchmark therefore prices a chroma dB ~2.3× above what AV1 encoders are
  institutionally tuned for — the designed-in exploitable axis (confirmed: `t1_chroma_level`
  −1.87% free; upstream precedent MR !2620 fixed the same neglect at P≤3 only).
- **The CPU envelope was non-uniform — audited and fixed.** As shipped, the 1.10× gate
  observed only 19.6% of ladder CPU (no timing clip >720p; 1080p = 47.5% of ladder CPU,
  bounded only by the 1.35× cap). A 6-line legal demo banked −2.14% with the gate reading
  0.9969× while truly spending ~1.15× (`docs/GATE_AUDIT.md`). Fixed 2026-07-22: per-tier
  timing set incl. Vlog_1080P, per-clip 1.20×, ladder cap 1.15×. Post-fix, all spend is
  observed and budget-competing; this term is **not headroom**.

## 2. Ceilings table

| Bound | BD vs anchor | Resource / argument | ⬛/⬜ | Provenance |
|---|---|---|---|---|
| **M4** true R(D) of source | unknown | information content of the clip ensemble; no video R(D) estimate exists; image-domain sandwich bounds show ≥1 dB (≳20% rate) beyond best codecs | ⬛ unknowable | Yang & Mandt ICLR'22 (arXiv 2111.12166); sandwich line ≠ a bound |
| **M3** next-syntax (AV2/AVM v13) | ≈ **−25.6%** (UGC class E) / −29.8% RA overall, at 33× encode | new bitstream — **unreachable**, decoder pinned dav1d/AV1 | ⬜ | AV2 eval, arXiv 2605.15800, CTC v8 |
| **M2s** AV1-syntax optimum | ≈ −30…−35% at 10²–10³× CPU | libaom cpu-0 2-pass ≈ −20…−25% + λ/TD-RDO stack −2…−4% + est. residual; unreachable under gate | ⬜ | SIWG-D001o; LCEVC SPIE'22; arXiv 2304.08634 |
| **M2** libaom at matched CPU | ≈ **0** | no post-2022 source shows libaom ahead of SVT at p6-class time; its edge is expensive tools ⇒ **T4 porting family closed** | ⬜ med-high | SIWG-D001o; SLC 2022-23; Meta Reels |
| **M1c** SVT unlimited-compute | measured P2 **−18.0%** @7.0× *(arm64, 3-clip, CRF 27–51)*; published ladder extends to M0/MR ≈ −21% @24–29× | oracle ablation of the speed constraint | ⬜ | `scripts/ceiling_probe.py`; SVT MR !2343/!2443 charts |
| **M1k** measured-catalog knapsack @1.10× | additive LP **−4.2%**; interaction-corrected **−2.9…−3.5%** | 14 measured seeds; CPU superadditivity k≈1.5–2.0, BD-eff 0.85 | ⬜ | `scripts/ceiling_knapsack.py` (runnable) |
| **M1f** preset-chord @1.10× | −1.8% | linear blend along preset frontier | ⬜ **dominated** | achieved −2.36 already beats it: unbundling > preset-walking, proven |
| **Reachable band (this benchmark)** | **central ≈ −3.2% (M1k mid), stretch −4…−5%** | center on the *measured* catalog optimum; stretch requires unmeasured families (T2 remainder + unprobed T1) to pay, net of interaction tax | ⬜ | ledger §4 |

Calibration cross-checks: our frontier matches SVT's own charts (P3/P2 near-exact; P5 −6.2%
inside the −3.7…−5.5 methodology envelope; our CPU multipliers smaller — favorable for
un-bundling). UGC compresses encoder-delta BD ~11% relative (AV2 class E vs A+B1) — shrink
published pristine-content numbers accordingly. Strongest calibration: **the literature has
no example of buying >2–3% BD at ~1× compute** via encoder-decision changes (best
gate-compatible class: per-clip/static λ retune, −2.0% on SVT over YouTube-UGC). Our −2.36%
at ~1.00× is already at that scale; each further point is publication-grade, not routine.

## 3. Baseline and headroom

Anchor = 100.000 by construction. **Best confirmed = 97.64** (`combo_confirmed_v1`,
chroma+filter_intra+cdef, all gates, full corpus). Central reachable ceiling ≈ **96.8**
(measured-catalog band 96.5–97.1); stretch ≈ **95–96** only if the unmeasured families
(T2 chroma/λ remainder, unprobed T1) pay. Baseline sits at ~70% of the central-estimate
reachable BD ⇒ **axis open but tighter than v2 claimed** — ~0.5–1 point of
measured-catalog headroom, ~1–2.5 more contingent on unmeasured families. M1c (≈82) and
M3 are informational only; no submission can reach them under the gate.

## 4. Headroom ledger — decompose 97.64 → ceiling

| Term | Mechanism | Δscore est. | Evidence / status | Next measurement |
|---|---|---|---|---|
| **T1-rem** | knapsack remainder of measured catalog: `wiener` (+`intra_base`/`md_pme`/`dlf` subset); `nic` iff affordable | −0.4…−1.1 | 7-seed near-miss at 1.114×; knapsack picks | 4–6-seed subsets, **paired** timing, multiple reps |
| **T1-unprobed** | unmeasured level-knobs (subpel, txt/txs, depth refinement, ME area; M4/M3-level promotions) | −0.5…−1.5 ⬜ | no public data exists for these knobs at P5/P6 v4.x — our ablation data is novel | `ablate.py` loop-until-dry per family |
| **T2** | chroma qindex offsets (machinery zero-defaulted in `rc_crf_cqp.c`; steal TUNE_IQ ramp) + per-layer qindex/λ retune, all zero-CPU | −0.3…−1.0 ⬜ | 6:1:1 mispricing (§1) — but `chroma_level` already banked part of the same chroma pool, so the offset sweep competes for a shrunken remainder; VTM MR1636 ≈1% scale; He'26 (arXiv 2606.20847, same metric) −1.26% *with* 5× compute; λ-retune −2.0% class | offset sweep at fixed CPU; per-CRF ramp |
| **T5** | ~~envelope arbitrage~~ | **0 — CLOSED (was a benchmark bug)** | demonstrated at −2.14 "free", then gate fixed: per-tier timing + 1.20 per-clip + 1.15 ladder cap (`docs/GATE_AUDIT.md`); residual boundary-riding ≲ −1 is observed, budget-competing, noise-fragile | — |
| **T0** upstream backports | **0 — CLOSED** | v4.2.0 (2026-07-14) is latest; post-tag master = RTC-only + bugfixes (watch !2752 loop-filter fixes) | — |
| **T4** libaom ports | **≈0 — CLOSED** | M2 marker: no matched-CPU gap to import | — |
| **Interaction tax** | CPU superadditivity (k≈1.5–2.0) + BD sub-additivity (0.85) on any stack | +25…30% of stacked sum | measured: combos ledger; only paired runs trusted | — |
| **Residual to M1c (−18…−21)** | block-level search: NIC depth, partition, references, subpel — the preset gap's bulk | ~13–15 pts | priced at 5–25× CPU, unreachable under 1.10× — **named, not unexplained** | none (out of reach) |

Sum check: M1k (−2.9…−3.5) already contains T1-rem on top of the achieved −2.36; adding
T2 (−0.3…−1.0) and unprobed T1 (−0.5…−1.5) at ~0.75 interaction efficiency gives
−3.5…−5.4 ⇒ brackets the §2 band (central −3.2 measured, stretch −4…−5). Gate satisfied:
no unexplained residual within the reachable region; T5 is excluded by construction
(closed gate), not unexplained.

**Measurement protocol (the constraint is noisy; the objective is not).** BD is bit-exact
(fast corpus tracks full to ~±10%). Single-run native combo CPU is unreliable — proof:
`combo_top5` measured 1.241× while its strict superset `combo_free9` measured 1.165×. Trust
only paired `local_eval.py` timing (and the official cgroup run); near the 1.10 edge, add
reps and a ≥2% safety margin. arm64→x86 transfer risk: per-feature SIMD coverage skew can
reorder CPU costs — re-measure short-listed subsets in the official container. The preset
frontier (`ceiling_probe.py`) used CRFs 27–51 on 3 clips — refresh only if it becomes
decision-relevant. Mechanistic caution: `nic`×`chroma` CPU is plausibly multiplicative
(candidate count × per-candidate chroma RD) — measure the pair before pricing `nic`.

## 5. Verdict

- **Where the slack is:** (1) T1 knapsack remainder around `combo_confirmed_v1 + wiener`;
  (2) T2 chroma-side retune — the metric's designed-in mispricing, zero-CPU, still
  unswept (shrunken by what `chroma_level` already banked); (3) unprobed T1 families.
  Envelope arbitrage is gone: it was a gate bug, demonstrated and closed same-day.
- **First targets:** (a) `combo3+wiener` (+`intra_base`/`md_pme`) with paired timing —
  expected ≈ 97.1–97.2; (b) chroma qindex-offset sweep (source-defaulted, zero CPU).
  Both must be validated under the *fixed* gate (per-tier timing, 1.20 per-clip, 1.15
  ladder cap); uniform-spend candidates targeting ≤1.10× are unaffected by the tightening.
- **Benchmark integrity:** the audit (−2.14 "free" with all gates green) is the single
  most important v2→v3 finding — the equal-speed constraint was falsifiable. Fixed in
  config; **official anchor re-baseline is pending** (runbook, `docs/TASK.md`) and gates
  every future official run.
- **Stop condition:** a family loop is dry when its best confirmed marginal gain < 0.05
  (the score quantum); the measured-catalog axis is exhausted near ~96.5; the stretch
  floor (~95–96) requires unmeasured families to pay — beyond it only new mechanism
  classes remain, which the literature prices above the 2–3%-at-1× frontier.
- **Gate status: memo GREEN** — every reachable-region term is measured, bounded, or
  closed with provenance and the sum covers the band. Optimization is authorized along
  the ranked targets; expensive runs still require a written predicted gain > the 0.05
  quantum first.
