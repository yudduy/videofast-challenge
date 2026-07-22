#!/usr/bin/env bash
# Trusted setup: fetch+verify corpus, pull pinned toolchain image (CI),
# extract anchor source, build anchor + candidate encoders (sandboxed in CI).
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 harness/cli.py setup "$@"
