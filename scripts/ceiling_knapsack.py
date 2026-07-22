#!/usr/bin/env python3
"""Relaxation bounds over the measured T1 seed catalog (docs/CEILING.md §2 M1k).

Reads docs/ablation_ledger.json + docs/ablation_ledger_t1b.json and computes the
knapsack value of the catalog under the 1.10x CPU gate:
  - additive fractional/integer knapsack (CPU deltas sum) -> optimistic bound
  - interaction-corrected integer knapsack (combined CPU = k * additive sum,
    stacked BD = 0.85 * additive sum for stacks >= 5) -> realistic band
k is calibrated from measured combos; the only trusted combined-CPU datapoint is
the paired 7-seed local_eval run (1.114x, full corpus). Single-run native combo
CPU is unreliable: combo_top5 measured 1.241x while its strict superset
combo_free9 measured 1.165x.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
BUDGET = 0.10
BD_EFF = 0.85  # measured BD sub-additivity on stacks >= 5 (0.83-0.87)
K_PAIRED = 1.75  # 7-seed paired local_eval: 1.114x measured vs 1.065x additive


def load_seeds() -> list[tuple[str, float, float]]:
    seeds = []
    for name in ("ablation_ledger.json", "ablation_ledger_t1b.json"):
        for c in json.loads((DOCS / name).read_text())["candidates"]:
            seeds.append((c["label"], -c["bd_rate_mean"], c["cpu_ratio"] - 1.0))
    return seeds


def frac_knapsack(seeds, budget: float) -> float:
    total = used = 0.0
    for _, bd, cpu in sorted(seeds, key=lambda s: -s[1] / max(s[2], 1e-9)):
        if used + cpu <= budget:
            total, used = total + bd, used + cpu
        else:
            return total + bd * (budget - used) / cpu
    return total


def int_knapsack(seeds, budget: float, bd_eff: float = 1.0):
    best, best_set = 0.0, ()
    for r in range(1, len(seeds) + 1):
        for combo in itertools.combinations(seeds, r):
            if sum(c for _, _, c in combo) <= budget:
                bd = sum(b for _, b, _ in combo) * (bd_eff if r >= 5 else 1.0)
                if bd > best:
                    best, best_set = bd, tuple(n for n, _, _ in combo)
    return best, best_set


def main() -> None:
    seeds = load_seeds()
    print(f"catalog: {len(seeds)} seeds, additive BD of budget-fitting singles = "
          f"-{sum(b for _, b, c in seeds if c <= 0.10):.2f}%")
    print(f"fractional knapsack, additive CPU @{BUDGET:.2f}: "
          f"-{frac_knapsack(seeds, BUDGET):.2f}%")
    bd, picked = int_knapsack(seeds, BUDGET)
    print(f"integer knapsack,    additive CPU @{BUDGET:.2f}: -{bd:.2f}%  {picked}")
    for k in (1.5, K_PAIRED, 2.0):
        bd, picked = int_knapsack(seeds, BUDGET / k, bd_eff=BD_EFF)
        print(f"integer, CPU x{k:.2f} superadditive, BD x{BD_EFF}: -{bd:.2f}%  {picked}")


if __name__ == "__main__":
    main()
