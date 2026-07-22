# SEEDS.md — frontier seed catalog for the videofast optimizer

Cross-validated by three independent source-grounded research passes against SVT-AV1
v4.2.0 (`submission/`). Objective: minimize **PSNR-YUV** BD-rate at ≤1.10× anchor CPU
(preset 6, `--lp 1`, CRF). Measured BD/CPU come from `scripts/ablate.py` on the 5-clip
fast corpus (`corpus/ablation_manifest.json`); winners are confirmed on the full 15-clip
corpus via `scripts/local_eval.py` (all gates). BD sign: **negative = better**.

## The one strategic fact

SVT-AV1 v4.2.0 at preset 6 **already ships** full RDOQ (level 1), a real quantitative TPL
model, per-temporal-layer lambda, and TPL delta-Q. So marquee academic techniques
(MuZero-RC −6.28% was vs *libvpx VBR*; deep-RDOQ is *weaker* than SVT's existing RDOQ) buy
≈nothing here. The reachable win (`docs/CEILING.md`: interval **[−1.8%, −18%]**) is almost
entirely **SVT-internal feature un-bundling + zero-CPU retuning**. And the best BD-per-CPU
promotions are **frame-level** (TPL accuracy, in-loop filter search depth) — **block-level**
knobs (candidate counts, partition/depth, references) are exactly where preset 6 buys its
speed and will blow the 1.10× gate.

## Seed families (promote a slower-preset setting into preset 6)

Ranked by expected BD-per-CPU. Source patches live in `scripts/seeds_t1.json` /
`seeds_t1b.json`; each is a verified single-occurrence `ENC_M5→ENC_M6` (or `ENC_M3→ENC_M6`)
threshold widening. Measured columns filled by the ablation runs.

Measured on the 5-clip fast corpus (5-CRF ladder), native ARM (BD is exact by
determinism; CPU is native user+sys, candidate÷baseline). **★ = fits ≤1.10× budget.**

### Winners — budget-fitting, ranked by measured BD (seed these)
| Seed | Mechanism | Measured BD / CPU |
|---|---|---|
| ★ `t1_chroma_level` | chroma RD independence M6 5→4 | **−1.87% / 1.00×** (star — free) |
| ★ `t1_nic` | candidate count 8→7 (+buffer 236→369) | −1.17% / 1.06× (priciest winner) |
| ★ `t1_wiener` | Wiener restoration 5→4 (finer) | −0.45% / 1.02× |
| ★ `t1_tpl_params` | TPL motion 4→3 (¼-pel+diag; unused intermediate) | −0.30% / 1.03× |
| ★ `t1_filter_intra` | filter-intra predictor 0→2 | −0.26% / 1.00× |
| ★ `t1_cdef_nonbase` | non-base CDEF search 6→5 | −0.23% / 1.00× |
| ★ `t1_dlf` | deblock search 3→2 | −0.17% / 1.01× |
| ★ `t1_intra_base` | base-frame intra 2→1 | −0.17% / 1.01× |
| ★ `t1_mds0` | MD stage-0 accuracy | −0.17% / 1.02× |
| ★ `t1_md_pme` | MD predictive-ME 4→3 | −0.16% / 1.01× |
| ★ `t1_obmc` | OBMC 6→5 | −0.08% / 1.02× |
| ★ `t1_tpl_group` | reduced→full mini-GOP TPL group 3→1 | −0.06% / 1.02× |

### Strong BD but OVER budget (excluded)
| Seed | Measured BD / CPU | Verdict |
|---|---|---|
| `t1_sg_restoration` | −0.99% / **1.50×** | self-guided restoration too costly at M6 |
| `t1_update_cdf` | −0.41% / **1.15×** | CDF adaptation on inter frames breaches gate |

### Zero-CPU lottery tickets (can't blow the gate; small, uncertain sign)
- Per-layer `--lambda-scale-factors` (default 128 = 1.0×) sweep.
- Per-layer `--qindex-offsets` / chroma via `--use-fixed-qindex-offsets` — note: the plain
  `--chroma-*-qindex-offset` scalars were **ignored** in ablation (0.00% change) without the
  fixed-offset enable; retest with `--use-fixed-qindex-offsets 1` to probe the 6:1:1 exploit.

## Confirmed traps — do NOT seed (measured or source-proven PSNR-negative)
- **`--tune` ≠ 1** — tune 1 IS PSNR and is the default/ceiling. Measured: `--tune 0` → **+7.51%** BD.
- **`--enable-qm` / quant matrices** — subjective-only, PSNR-neutral-to-negative.
- **`--enable-variance-boost`, `--ac-bias`, `--sharpness`, `--luminance-qp-bias`,
  `--qp-scale-compress-strength`** — all perceptual, PSNR-negative by construction.
- **`--enable-overlays 1`** — measured **+0.94%** (worse) on the fast corpus.
- **`--enable-tf 2`** — measured **+3.82%** (worse).
- **Block-level thoroughness (references/MRP, NIC, partition depth, NSQ search)** — the CPU
  sinks that define the preset gap; poor BD-per-CPU, likely to breach the gate.

## Out of scope (fixed decoder)
AV2/AVM tools (~−25–30% vs AV1) require a new bitstream; our decoder is pinned dav1d/AV1.

## Confirmed results (full 15-clip corpus, all gates)

| Candidate | Score | BD-rate | Speed gate | Notes |
|---|---|---|---|---|
| **`t1_chroma_level` alone** | **98.02** | **−1.98%** | **pass** | first confirmed win — single-line source change, zero speed cost; full-corpus BD beat the −1.87% fast-corpus estimate |

**Load-bearing lesson — CPU compounds super-linearly.** Individually the winners are
~1.00–1.03× CPU, but stacking them is far more expensive than the product: `combo_top5`
(chroma+wiener+tpl_params+filter_intra+cdef) measured **−2.71% BD but 1.24× CPU**,
`combo_free9` −3.14% / 1.17×, `combo_all_budget` −4.35% / 1.25× — all over the 1.10× gate.
The BD is real and large; the binding constraint is entirely the speed gate. So the search
is not "stack all winners" but "find the max-BD subset whose *combined* CPU ≤ 1.10×" — a
knapsack the loop must solve by measuring combinations, exactly the dynamic the benchmark
is designed to reward. The reachable frontier (BD ≈ −2 to −4%) sits well inside the ceiling
memo's [−1.8%, −18%] interval; closing more of it needs cheaper individual features or
compute-reallocation (cut a low-value search to fund a high-value one at flat CPU).

## Method (the loop)
1. `ablate.py seeds_*.json` → rank source seeds by exact BD and native CPU on the fast
   corpus (gate-free). Native CPU is a noisy proxy; treat ≤1.05× as "likely fits".
2. Combine candidate subsets; **re-measure** combined CPU (super-linear — never assume).
3. `local_eval.py --candidate-src <patched submission>` → full-corpus, all-gates
   validation. Its paired speed gate (candidate vs anchor, back-to-back native) is the
   reliable local speed verdict; the cgroup CI run is the official arbiter.
4. A confirmed <100 candidate that passes the paired speed gate is the submittable win.

*Primary sources: SVT-AV1 v4.2.0 source/docs; Han 2021 (TPL, arXiv:2108.11586); Mandhane
2022 (MuZero-RC, arXiv:2202.06626); Kianfar 2020 (RDOQ, arXiv:2012.06380); He 2026 (LLM QP,
arXiv:2606.20847, same 6:1:1 metric); Streaming Learning Center "encoders tune for PSNR".*
