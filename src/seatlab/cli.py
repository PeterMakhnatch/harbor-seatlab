"""Replay one role's decisions without running Harbor or changing its reward."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .calibration import calibrate
from .context import ContextSpec
from .handoff import from_handoff
from .inspector import write_inspector
from .replay import replay
from .report import compare, describe


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seatlab", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser(
        "from-handoff", help="import a public native Dispatch Handoff study"
    )
    ingest.add_argument("bundle", type=Path)
    ingest.add_argument("--panel", required=True)
    ingest.add_argument("--out", required=True, type=Path, help="new decision tape (JSONL)")
    describe_cmd = commands.add_parser("describe", help="check and summarize a decision tape")
    describe_cmd.add_argument("tape", type=Path)
    run = commands.add_parser("replay", help="apply a policy to every decision in a tape")
    run.add_argument("tape", type=Path)
    run.add_argument("--out", required=True, type=Path, help="new result file (JSONL)")
    transforms = run.add_mutually_exclusive_group()
    transforms.add_argument("--transform", choices=("identity", "latest"), default="identity")
    transforms.add_argument(
        "--transform-file", type=Path, help="declarative context intervention (JSON)"
    )
    run.add_argument("--timeout-seconds", type=float, default=10.0)
    selection = run.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--policy", choices=("observed", "revision", "arrival", "intersection")
    )
    selection.add_argument(
        "--exec",
        dest="executable",
        nargs=argparse.REMAINDER,
        help="trusted executable and arguments; specify last, use an absolute path",
    )
    pair = commands.add_parser(
        "compare", help="compare complete results against the same recorded views"
    )
    pair.add_argument("baseline", type=Path)
    pair.add_argument("candidate", type=Path)
    calibration = commands.add_parser(
        "calibrate", help="validate predictions against independently recorded native trials"
    )
    calibration.add_argument("tape", type=Path, help="recorded decisions from both arms")
    calibration.add_argument(
        "predictions", type=Path, help="replay results for source arms only"
    )
    calibration.add_argument(
        "--pairs", required=True, type=Path, help="registered native pairs"
    )
    calibration.add_argument(
        "--transform-file",
        type=Path,
        help="recipe to independently check predicted actor inputs",
    )
    calibration.add_argument("--html", type=Path, help="also write a new, self-contained HTML report")
    args = parser.parse_args(argv)
    try:
        if args.command == "from-handoff":
            result = from_handoff(args.bundle, args.panel, args.out)
        elif args.command == "describe":
            result = describe(args.tape)
        elif args.command == "compare":
            result = compare(args.baseline, args.candidate)
        elif args.command == "calibrate":
            context = ContextSpec.load(args.transform_file) if args.transform_file else None
            result = calibrate(args.tape, args.predictions, args.pairs, context=context)
            if args.html is not None:
                write_inspector(result, args.html)
        else:
            if args.executable is not None and not args.executable:
                parser.error("--exec requires an absolute executable and optional arguments")
            if not 1 <= args.timeout_seconds <= 300:
                parser.error("--timeout-seconds must be between 1 and 300")
            policy = args.executable if args.executable is not None else args.policy
            result = replay(
                args.tape,
                args.out,
                policy,
                transform=(
                    ContextSpec.load(args.transform_file)
                    if args.transform_file
                    else args.transform
                ),
                timeout=args.timeout_seconds,
            )
    except (OSError, ValueError) as error:
        print(f"seatlab: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0
