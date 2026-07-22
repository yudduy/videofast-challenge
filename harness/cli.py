"""Command-line entry point for the videofast scoring harness."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Sequence


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import load_config
from harness.gates import GateError
from harness.pipeline import cmd_run, cmd_setup


def _shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("auto", "docker", "native"), default="auto")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--candidate-bin", type=Path)
    parser.add_argument("--anchor-bin", type=Path)
    parser.add_argument("--work-dir", type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 harness/cli.py")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup")
    _shared(setup)

    run = commands.add_parser("run")
    _shared(run)
    run.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    run.add_argument("--anchor-data", type=Path)
    run.add_argument("--regen-anchor", action="store_true")
    run.add_argument("--skip-guard", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run" and os.environ.get("REGEN_ANCHOR", "").lower() == "true":
        args.regen_anchor = True
    cfg = load_config()
    try:
        if args.command == "setup":
            cmd_setup(cfg, args)
        else:
            cmd_run(cfg, args)
    except GateError as exc:
        cfg.score_path.unlink(missing_ok=True)
        print(f"GATE FAIL {exc.gate}: {exc.detail}", flush=True)
        return 2
    except Exception as exc:
        cfg.score_path.unlink(missing_ok=True)
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
