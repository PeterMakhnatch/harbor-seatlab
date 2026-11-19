"""Verify worker ledgers against a registered tape and restore source order."""

from collections.abc import Sequence
from pathlib import Path

from . import tape as wire
from .partition import iter_source, load_plan
from .verification import verify_worker_result
from .viewmerge import merge_actions


def merge(
    plan_path: Path, tape: Path, ledgers: Sequence[Path], out: Path
) -> dict[str, object]:
    """Publish a complete ledger without attesting authorship or external execution."""
    plan = load_plan(plan_path)
    if plan.namespace is not None:
        return merge_actions(plan, tape, ledgers, out)
    first_by_key: dict[str, wire.Result] = {}
    # A repeated key has an entry even when all its rows equal the first row.
    alternatives: dict[str, list[wire.Result]] = {}
    supplied = 0
    for ledger in ledgers:
        for result in wire.read_results(ledger):
            supplied += 1
            key = result["key"]
            first = first_by_key.get(key)
            if first is None:
                first_by_key[key] = result
            else:
                repeats = alternatives.setdefault(key, [])
                if result != first:
                    repeats.append(result)

    ordered: list[wire.Result] = []
    missing_keys: list[str] = []
    invalid_keys: list[str] = []
    measured = 0
    # Exhaustion checks the canonical source and shard commitments even on incomplete merges.
    for source in iter_source(plan, tape):
        key = source["key"]
        first = first_by_key.pop(key, None)
        if first is None:
            missing_keys.append(key)
            continue
        repeats = alternatives.get(key)
        try:
            verify_worker_result(source, first, plan.policy, plan.transform_id, plan.context)
            if repeats is not None:
                for result in repeats:
                    verify_worker_result(
                        source, result, plan.policy, plan.transform_id, plan.context
                    )
        except wire.TapeError:
            invalid_keys.append(key)
            continue
        if repeats is None:
            ordered.append(first)
            measured += first["status"] == "measured"

    complete = len(ordered) == plan.decisions and not first_by_key
    if complete:
        wire.write_lines(out, ordered)
    return {
        "schema": "seatlab.merge/v1",
        "plan_sha256": plan.plan_sha256,
        "policy": plan.policy,
        "transform": plan.transform_id,
        "registered": plan.decisions,
        "supplied": supplied,
        "measured": measured,
        "unmeasured": len(ordered) - measured,
        "complete": complete,
        "missing_keys": sorted(missing_keys),
        "foreign_keys": sorted(first_by_key),
        "duplicate_keys": sorted(key for key, rows in alternatives.items() if not rows),
        "conflicting_keys": sorted(key for key, rows in alternatives.items() if rows),
        "invalid_keys": sorted(invalid_keys),
    }
