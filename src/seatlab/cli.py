"""Replay one role's decisions without running Harbor or changing its reward."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .alignment import align
from .calibration import calibrate
from .context import ContextSpec
from .handoff import from_handoff
from .inspector import write_inspector
from .merge import merge
from .partition import partition
from .prefix import prefix
from .replay import replay
from .report import compare, describe
from .study import StudyMismatch, seal_study, verify_study
from .study_html import write_study_html
from .study_relations import export_relations
from .sufficiency import audit_context
from .tape import digest
from .team_alignment import team_align
from .trajectory import role_trajectory
from .viewwork import act
from .windows import verify_windows


def _policy_arguments(parser: argparse.ArgumentParser, policies: tuple[str, ...]) -> None:
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--policy", choices=policies)
    selection.add_argument(
        "--exec",
        dest="executable",
        nargs=argparse.REMAINDER,
        help="trusted executable and arguments; specify last, use an absolute path",
    )


def _transform_arguments(parser: argparse.ArgumentParser) -> None:
    transforms = parser.add_mutually_exclusive_group()
    transforms.add_argument("--transform", choices=("identity", "latest"), default="identity")
    transforms.add_argument(
        "--transform-file", type=Path, help="declarative context intervention (JSON)"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seatlab", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    study = commands.add_parser(
        "study", help="seal, verify or export relations of a portable native study"
    )
    study_commands = study.add_subparsers(dest="study_action", required=True)
    seal = study_commands.add_parser("seal", help="record file and analysis digests")
    seal.add_argument("recipe", type=Path)
    seal.add_argument(
        "--out", type=Path, required=True, help="new manifest beside the recipe"
    )
    verify = study_commands.add_parser("verify", help="recompute the registered analyses")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--expect-sha256", help="trusted canonical manifest digest")
    verify.add_argument(
        "--html", type=Path, help="also write a new, self-contained HTML report"
    )
    relations = study_commands.add_parser(
        "relations", help="export one verified JSONL row per registered comparison"
    )
    relations.add_argument("manifest", type=Path)
    relations.add_argument(
        "--expect-sha256", required=True, help="trusted canonical manifest digest"
    )
    relations.add_argument(
        "--out", type=Path, required=True, help="new relation file (JSONL)"
    )
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
    _transform_arguments(run)
    _policy_arguments(run, ("observed", "revision", "arrival", "intersection"))
    split = commands.add_parser(
        "partition", help="register a source tape and produce disjoint worker inputs"
    )
    split.add_argument("tape", type=Path)
    split.add_argument("--shards", type=int, required=True)
    split.add_argument("--out", type=Path, required=True, help="new partition directory")
    split.add_argument(
        "--policy",
        choices=("observed", "revision", "arrival", "intersection", "external"),
        required=True,
    )
    _transform_arguments(split)
    split.add_argument(
        "--views-only",
        metavar="NAMESPACE",
        help="send only transformed actor views; use a distinct 32-character lowercase hex study ID",
    )
    worker = commands.add_parser("act", help="return unscored actions for a view-only part")
    worker.add_argument("requests", type=Path)
    worker.add_argument("--out", type=Path, required=True, help="new unscored action file")
    _policy_arguments(worker, ("revision", "arrival", "intersection"))
    gather = commands.add_parser(
        "merge",
        help="verify worker ledgers against the registered tape and publish in source order",
    )
    gather.add_argument("--plan", type=Path, required=True)
    gather.add_argument("--tape", type=Path, required=True)
    gather.add_argument("--out", type=Path, required=True, help="new merged ledger")
    gather.add_argument("ledgers", type=Path, nargs="*", help="worker result JSONL files")
    sequence = commands.add_parser(
        "prefix", help="compare a policy only while a recorded role history still applies"
    )
    sequence.add_argument("episodes", type=Path)
    sequence.add_argument(
        "--history", action="store_true", help="include this role's matched past"
    )
    _policy_arguments(sequence, ("observed",))
    alignment = commands.add_parser(
        "align",
        help="find the first changed input or action in independently recorded episodes",
    )
    alignment.add_argument("source", type=Path)
    alignment.add_argument("target", type=Path)
    alignment.add_argument(
        "--pairs", type=Path, required=True, help="registered episode receipts"
    )
    alignment.add_argument(
        "--input-paths",
        metavar="N",
        type=int,
        help="list at most N changed JSON Pointer paths at the first input boundary (1–128)",
    )
    joint = commands.add_parser(
        "team-align", help="find the first different decision step across two native teams"
    )
    joint.add_argument("source", type=Path, help="role episodes from one native trial")
    joint.add_argument(
        "target", type=Path, help="role episodes from a different native trial"
    )
    joint.add_argument("--source-order", type=Path, required=True)
    joint.add_argument("--target-order", type=Path, required=True)
    joint.add_argument(
        "--input-paths", metavar="N", type=int, help="show up to N changed paths"
    )
    trajectory = commands.add_parser(
        "role-trajectory",
        help="compare one participant's views and actions on two observed schedules",
    )
    trajectory.add_argument("source", type=Path)
    trajectory.add_argument("target", type=Path)
    trajectory.add_argument("--source-order", type=Path, required=True)
    trajectory.add_argument("--target-order", type=Path, required=True)
    trajectory.add_argument("--role", required=True)
    trajectory.add_argument("--field", action="append", metavar="JSON_POINTER", help="select an observed field; repeatable")
    audit = commands.add_parser(
        "context-audit",
        help="find recorded actions that selected context fields cannot distinguish",
    )
    audit.add_argument("train", type=Path, help="native role episodes (JSONL)")
    audit.add_argument(
        "--heldout", type=Path, help="optional episodes from disjoint cases and trials"
    )
    fields = audit.add_mutually_exclusive_group()
    fields.add_argument(
        "--field",
        action="append",
        metavar="POINTER",
        help="keep a JSON Pointer from the current role view; repeatable",
    )
    fields.add_argument("--no-fields", action="store_true", help="use only role identity")
    audit.add_argument(
        "--witnesses",
        type=int,
        default=3,
        metavar="N",
        help="show receipts for at most N conflicting inputs (0–100)",
    )
    windows = commands.add_parser(
        "verify-windows",
        help="join native episodes to the context a trusted wrapper sent",
    )
    windows.add_argument("episodes", type=Path, help="native role episodes (JSONL)")
    windows.add_argument(
        "receipts", type=Path, help="context receipts for those native trials"
    )
    windows.add_argument(
        "--out",
        type=Path,
        help="new episode file of the verified actor envelopes (JSONL)",
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
    calibration.add_argument(
        "--html", type=Path, help="also write a new, self-contained HTML report"
    )
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        if args.command == "study":
            if args.study_action == "seal":
                manifest = seal_study(args.recipe, args.out)
                result = {
                    "schema": "seatlab.study-seal/v1",
                    "study_sha256": digest(manifest),
                }
            elif args.study_action == "relations":
                result = export_relations(
                    args.manifest, args.out, expect_sha256=args.expect_sha256
                )
            else:
                result = verify_study(args.manifest, expect_sha256=args.expect_sha256)
                if args.html is not None:
                    write_study_html(result, args.html)
        elif args.command == "from-handoff":
            result = from_handoff(args.bundle, args.panel, args.out)
        elif args.command == "describe":
            result = describe(args.tape)
        elif args.command == "partition":
            result = partition(
                args.tape,
                args.out,
                args.shards,
                args.policy,
                transform=args.transform,
                transform_file=args.transform_file,
                namespace=args.views_only,
            )
        elif args.command == "merge":
            result = merge(args.plan, args.tape, args.ledgers, args.out)
            if not result["complete"]:
                exit_code = 1
        elif args.command == "compare":
            result = compare(args.baseline, args.candidate)
        elif args.command == "align":
            result = align(args.source, args.target, args.pairs, input_paths=args.input_paths)
        elif args.command == "team-align":
            result = team_align(
                args.source,
                args.target,
                source_order=args.source_order,
                target_order=args.target_order,
                input_paths=args.input_paths,
            )
        elif args.command == "role-trajectory":
            result = role_trajectory(
                args.source, args.target, source_order=args.source_order,
                target_order=args.target_order, role=args.role, fields=args.field,
            )
        elif args.command == "context-audit":
            result = audit_context(
                args.train,
                fields=() if args.no_fields else args.field,
                heldout=args.heldout,
                witnesses=args.witnesses,
            )
        elif args.command == "verify-windows":
            result = verify_windows(args.episodes, args.receipts, out=args.out)
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
            if args.command == "prefix":
                result = prefix(
                    args.episodes, policy, history=args.history, timeout=args.timeout_seconds
                )
            elif args.command == "act":
                result = act(args.requests, args.out, policy, timeout=args.timeout_seconds)
            else:
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
    except StudyMismatch as error:
        print(f"seatlab: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"seatlab: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return exit_code
