"""NumPy/SciPy-backed compute entry point used inside the toolchain image."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


# ``docker run`` executes this file as ``/harness/xcompute.py``.  Add the
# package parent (``/`` in the container) so absolute ``harness.*`` imports
# work just as they do from the repository checkout.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _point_list(value: str, option: str) -> list[tuple[float, float]]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{option} is not valid JSON: {exc}") from exc
    if not isinstance(decoded, list):
        raise ValueError(f"{option} must encode a list of [rate, quality] points")

    points: list[tuple[float, float]] = []
    for index, point in enumerate(decoded):
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{option} point {index} must be [rate, quality]")
        try:
            points.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{option} point {index} is not numeric") from exc
    return points


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    psnr = commands.add_parser("psnr", help="compute clip-level PSNR")
    psnr.add_argument("--src", required=True)
    psnr.add_argument("--dec", required=True)
    psnr.add_argument("--frames", required=True, type=int)

    bd = commands.add_parser("bd", help="compute PCHIP BD-rate")
    bd.add_argument("--anchor-json", required=True)
    bd.add_argument("--candidate-json", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "psnr":
            from harness.metrics import clip_psnr

            result: object = clip_psnr(args.src, args.dec, args.frames)
        else:
            from harness.bdrate import bd_rate

            anchor = _point_list(args.anchor_json, "--anchor-json")
            candidate = _point_list(args.candidate_json, "--candidate-json")
            result = {"bd_rate": bd_rate(anchor, candidate)}
    except Exception as exc:
        # Return a distinct status for validity gates so the host-side runner
        # can preserve GateError semantics across the container boundary.
        from harness.gates import GateError

        if isinstance(exc, GateError):
            print(exc.detail, file=sys.stderr)
            return 2
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
