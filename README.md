# videofast-challenge

An optimization benchmark for AV1 video encoding, hosted as a
[Yukon](https://github.com/apps/yukon-autoresearch) GitHub Actions benchmark:
modify the vendored **SVT-AV1 v4.2.0** encoder in `submission/` to minimize
**PSNR-YUV BD-rate** against the pinned anchor **at equal speed**
(≤1.10× anchor CPU-time, measured paired in the same VM).

**Score = 100 + mean BD-rate% vs anchor** · lower is better · anchor ≡ 100.000.

The score is hardware-independent by construction: BD-rate is a bit-exact
function of (source, settings), enforced by a determinism gate, so any runner
can compute it; only the speed *gate* touches the clock, and it compares
candidate vs anchor back-to-back on the same machine so shared-runner noise
cancels.

## Layout

| Path | Role |
| --- | --- |
| `submission/` | Vendored SVT-AV1 source — **the only path solvers may edit** |
| `harness/` | Trusted scoring pipeline (encode → dav1d decode → PSNR → BD-rate → gates) |
| `harness/anchor/` | Committed anchor curves + bitstream hashes + timing reference |
| `anchor/` | Pristine pinned source tarball the anchor is rebuilt from every run |
| `corpus/manifest.json` | Clip URLs + SHA-256; media is fetched at setup, never committed |
| `toolchain/` | Pinned build/measurement container (digest-pinned in `harness/config.json`) |
| `.yukon/` | `setup.sh` / `run.sh` entrypoints; `score.json` is written here |
| `docs/` | RULES.md · TASK.md · CEILING.md |

## Run it

```sh
bash .yukon/setup.sh   # fetch+verify corpus, build anchor + candidate
bash .yukon/run.sh     # encode, decode, metric, gate → .yukon/score.json
```

CI (`.github/workflows/benchmark.yml`, `workflow_dispatch`) is the official
path: untrusted build/encode run inside the pinned container with
`--network=none`. Local runs are for iteration; macOS/ARM results are estimates
unless the bitstream-hash manifest matches the official run.

See `docs/RULES.md` for the full contract and anti-gaming provisions.
