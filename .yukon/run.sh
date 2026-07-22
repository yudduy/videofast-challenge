#!/usr/bin/env bash
# Trusted benchmark: encode ladder -> dav1d conformance decode -> PSNR/BD-rate
# -> gates (determinism, paired speed, RD validity) -> .yukon/score.json.
# Exits nonzero and writes no score when any gate fails.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 harness/cli.py run "$@"
