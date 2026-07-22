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

### Tier 1 — frame-level, high BD-per-CPU (seed first)
| Seed | Mechanism | Source | Measured BD / CPU |
|---|---|---|---|
| `t1_tpl_params` | TPL motion 4→3 (adds ¼-pel + diagonal refine; unused intermediate) | `initial_rc_process.c` get_tpl_params_level | _pending_ |
| `t1_tpl_group` | reduced→full mini-GOP TPL group (M6 3→1) | `initial_rc_process.c` svt_aom_get_tpl_group_level | _pending_ |
| `t1_cdef_nonbase` | non-base CDEF search 6→5 | `enc_mode_config.c` ~L2094 | _pending_ |
| `t1_dlf` | deblock search 3→2 (finer) | `enc_mode_config.c` get_dlf_level_default | _pending_ |
| `t1_sg_restoration` | self-guided restoration OFF→level 3 | `enc_mode_config.c` sg_filter_level_default | _pending_ |
| `t1_wiener` | Wiener restoration 5→4 (finer) | `enc_mode_config.c` wn_filter_level_default | _pending_ |
| `t1_update_cdf` | entropy CDF adaptation on base inter frames | `enc_mode_config.c` update_cdf_level_default | _pending_ |

### Tier 2 — block-level, cheap-ish (test, gate-risky)
| Seed | Mechanism | Measured BD / CPU |
|---|---|---|
| `t1_filter_intra` | filter-intra predictor 0→2 | _pending_ |
| `t1_chroma_level` | chroma RD independence 5→4 | _pending_ |
| `t1_intra_base` | base-frame intra search 2→1 | _pending_ |
| `t1_mds0` | MD stage-0 accuracy | _pending_ |
| `t1_md_pme` | MD predictive-ME 4→3 | _pending_ |
| `t1_obmc` | OBMC level 6→5 | _pending_ |
| `t1_nic` | candidate count 8→7 (+buffer ceiling) — *agent 1 flags gate-risky* | _pending_ |

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

## Method
1. `ablate.py seeds_t1.json` → rank source seeds by BD/CPU on the fast corpus (gate-free).
2. Combine budget-fitting winners (sub-additive; re-measure combined CPU).
3. `local_eval.py --candidate-src <patched submission>` → full-corpus, all-gates validation.
4. A confirmed <100 candidate that passes the speed gate is the submittable improvement.

*Primary sources: SVT-AV1 v4.2.0 source/docs; Han 2021 (TPL, arXiv:2108.11586); Mandhane
2022 (MuZero-RC, arXiv:2202.06626); Kianfar 2020 (RDOQ, arXiv:2012.06380); He 2026 (LLM QP,
arXiv:2606.20847, same 6:1:1 metric); Streaming Learning Center "encoders tune for PSNR".*
