"""Find the first different or unknown world decision step across two native trials."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from . import tape as wire
from .episodes import EpisodeFrame
from .verification import action_equal
from .viewdiff import InputDiff, diff_input
from .world_order import WorldOrder, read_world_order, validate_world_pair

__all__ = ["TEAM_ALIGNMENT_SCHEMA", "team_align"]

TEAM_ALIGNMENT_SCHEMA = "seatlab.team-alignment/v1"

TeamStatus = Literal[
    "matched",
    "matched_open",
    "order_changed",
    "step_changed",
    "missing_evidence",
    "length_changed",
]
RoleStatus = Literal["equal", "input_changed", "action_changed", "unbound", "missing_action"]


class RoleEvidence(TypedDict):
    role: str
    turn: int
    status: RoleStatus
    input_diff: NotRequired[InputDiff]


class TeamAlignmentReport(TypedDict):
    schema: Literal["seatlab.team-alignment/v1"]
    case_sha256: str
    source_native_trial_id: str
    target_native_trial_id: str
    status: TeamStatus
    matched_steps: int
    boundary_step: int | None
    source_steps: int
    target_steps: int
    source_withheld_steps: int
    target_withheld_steps: int
    source_roles: list[str]
    target_roles: list[str]
    roles: list[RoleEvidence]




def _compare_role(
    role: str,
    left: EpisodeFrame,
    right: EpisodeFrame,
    *,
    input_paths: int | None,
) -> RoleEvidence:
    turn = left["turn"]
    if left["basis"] != "bound" or right["basis"] != "bound":
        return {"role": role, "turn": turn, "status": "unbound"}
    if left["view_sha256"] != right["view_sha256"]:
        row: RoleEvidence = {"role": role, "turn": turn, "status": "input_changed"}
        if input_paths is not None:
            row["input_diff"] = diff_input(left["view"], right["view"], limit=input_paths)
        return row
    left_obs, right_obs = left["observed"], right["observed"]
    if left_obs is None or right_obs is None:
        return {"role": role, "turn": turn, "status": "missing_action"}
    if not action_equal(left_obs["action"], right_obs["action"]):
        return {"role": role, "turn": turn, "status": "action_changed"}
    return {"role": role, "turn": turn, "status": "equal"}


def _report(
    source: WorldOrder,
    target: WorldOrder,
    status: TeamStatus,
    matched_steps: int,
    boundary_step: int | None,
    *,
    source_roles: Sequence[str] = (),
    target_roles: Sequence[str] = (),
    roles: Sequence[RoleEvidence] = (),
) -> dict[str, object]:
    after = matched_steps if boundary_step is None else boundary_step
    result: TeamAlignmentReport = {
        "schema": TEAM_ALIGNMENT_SCHEMA,
        "case_sha256": source.case_sha256,
        "source_native_trial_id": source.native_trial_id,
        "target_native_trial_id": target.native_trial_id,
        "status": status,
        "matched_steps": matched_steps,
        "boundary_step": boundary_step,
        "source_steps": len(source.steps),
        "target_steps": len(target.steps),
        "source_withheld_steps": max(0, len(source.steps) - after),
        "target_withheld_steps": max(0, len(target.steps) - after),
        "source_roles": list(source_roles),
        "target_roles": list(target_roles),
        "roles": list(roles),
    }
    return cast(dict[str, object], result)


def team_align(
    source: Path,
    target: Path,
    *,
    source_order: Path,
    target_order: Path,
    input_paths: int | None = None,
) -> dict[str, object]:
    """Find the first world step where two native multi-role trials differ or lack evidence.

    Roles inside a simultaneous step are compared by role name regardless of listing order.
    Each role consumes its next 1-based local frame and is checked in order: bound basis,
    canonical view digest, known observation, and strict JSON action equality. Any concrete
    difference in the group yields ``step_changed`` even if another role is unbound or
    missing an action; absent evidence alone yields ``missing_evidence``.

    Steps after a boundary are withheld without inspection. On a matched prefix with uneven
    step counts, ``length_changed`` applies only when both trials are closed; if either
    trial is open, status is ``matched_open`` and extra uncompared steps are withheld.
    """
    if input_paths is not None and not (type(input_paths) is int and 1 <= input_paths <= 128):
        raise wire.TapeError("input_paths must be an integer from 1 to 128")

    left_world = read_world_order(source, source_order)
    right_world = read_world_order(target, target_order)
    validate_world_pair(left_world, right_world)

    left_total = len(left_world.steps)
    right_total = len(right_world.steps)
    cursor = {role: 0 for role in left_world.episodes}
    shared = min(left_total, right_total)

    for step_index in range(shared):
        step = step_index + 1
        left_group = left_world.steps[step_index]
        right_group = right_world.steps[step_index]
        if left_group != right_group:
            return _report(
                left_world,
                right_world,
                "order_changed",
                step_index,
                step,
                source_roles=left_group,
                target_roles=right_group,
            )

        rows: list[RoleEvidence] = []
        has_change = has_missing = False
        for role in left_group:
            turn_index = cursor[role]
            row = _compare_role(
                role,
                left_world.episodes[role]["frames"][turn_index],
                right_world.episodes[role]["frames"][turn_index],
                input_paths=input_paths,
            )
            role_status = row["status"]
            if role_status in ("input_changed", "action_changed"):
                has_change = True
            elif role_status in ("unbound", "missing_action"):
                has_missing = True
            rows.append(row)

        if has_change or has_missing:
            status: TeamStatus = "step_changed" if has_change else "missing_evidence"
            return _report(
                left_world,
                right_world,
                status,
                step_index,
                step,
                source_roles=left_group,
                target_roles=right_group,
                roles=rows,
            )

        for role in left_group:
            cursor[role] += 1

    if left_total == right_total:
        end: TeamStatus = (
            "matched" if (left_world.closed and right_world.closed) else "matched_open"
        )
        return _report(left_world, right_world, end, shared, None)

    if left_world.closed and right_world.closed:
        return _report(
            left_world,
            right_world,
            "length_changed",
            shared,
            shared + 1,
            source_roles=left_world.steps[shared] if left_total > shared else (),
            target_roles=right_world.steps[shared] if right_total > shared else (),
        )

    return _report(left_world, right_world, "matched_open", shared, None)
