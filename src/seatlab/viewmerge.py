"""Verify view-worker action ledgers against a registered tape and score centrally."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path

from . import replay as engine
from . import tape as wire
from . import viewwork
from .partition import ShardPlan, iter_registered
from .verification import verify_worker_result


def _score_and_verify(
    source: wire.Decision,
    action_row: viewwork.WorkerAction,
    plan: ShardPlan,
    expected_input_sha256: str,
    input_data: bytes,
) -> wire.Result:
    if action_row["policy"] != plan.policy:
        raise wire.TapeError(
            f"worker action policy does not match registered policy for {source['key']!r}"
        )
    if action_row["input_sha256"] != expected_input_sha256:
        raise wire.TapeError(
            f"worker action input commitment does not match {source['key']!r}"
        )
    result = engine.scored_action(
        source,
        plan.policy,
        plan.transform_id,
        input_data,
        action_row["action"],
        action_row["error"],
    )
    verify_worker_result(source, result, plan.policy, plan.transform_id, plan.context)
    return result


def merge_actions(
    plan: ShardPlan, tape: Path, ledgers: Sequence[Path], out: Path
) -> dict[str, object]:
    """Score and verify view-worker actions centrally and restore source order.

    External actions attest neither execution nor choice. Only their request binding,
    transformed input digest, policy family, and answer-key scoring are checked.
    """
    if plan.namespace is None:
        raise wire.TapeError("merge_actions requires a view-only partition plan")
    if plan.policy == "observed":
        raise wire.TapeError("view-only plans do not support observed policy")
    first_by_id: dict[str, viewwork.WorkerAction] = {}
    # A repeated request_id has an entry even when all its rows equal the first row.
    alternatives: dict[str, list[viewwork.WorkerAction]] = {}
    supplied = 0
    for ledger in ledgers:
        for row in viewwork.read_actions(ledger):
            supplied += 1
            request_id = row["request_id"]
            first = first_by_id.get(request_id)
            if first is None:
                first_by_id[request_id] = row
            else:
                repeats = alternatives.setdefault(request_id, [])
                if row != first:
                    repeats.append(row)

    ordered: list[wire.Result] = []
    missing_keys: list[str] = []
    invalid_keys: list[str] = []
    duplicate_keys: list[str] = []
    conflicting_keys: list[str] = []
    measured = 0
    # Exhaustion checks the canonical source and shard commitments even on incomplete merges.
    for source, request in iter_registered(plan, tape):
        if request is None:
            raise wire.TapeError("view-only registration did not yield a worker view")
        key = source["key"]
        request_id = request["request_id"]
        first = first_by_id.pop(request_id, None)
        repeats = alternatives.pop(request_id, None)
        if repeats is not None:
            if repeats:
                conflicting_keys.append(key)
            else:
                duplicate_keys.append(key)
        if first is None:
            missing_keys.append(key)
            continue
        input_data = wire.canonical(request["view"])
        expected_input_sha256 = sha256(input_data).hexdigest()
        try:
            scored = _score_and_verify(
                source, first, plan, expected_input_sha256, input_data
            )
            if repeats is not None:
                for row in repeats:
                    _score_and_verify(source, row, plan, expected_input_sha256, input_data)
        except wire.TapeError:
            invalid_keys.append(key)
            continue
        if repeats is None:
            ordered.append(scored)
            measured += scored["status"] == "measured"

    for request_id, rows in alternatives.items():
        if rows:
            conflicting_keys.append(request_id)
        else:
            duplicate_keys.append(request_id)

    complete = (
        len(ordered) == plan.decisions
        and not first_by_id
        and not duplicate_keys
        and not conflicting_keys
    )
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
        "foreign_keys": sorted(first_by_id),
        "duplicate_keys": sorted(duplicate_keys),
        "conflicting_keys": sorted(conflicting_keys),
        "invalid_keys": sorted(invalid_keys),
    }
