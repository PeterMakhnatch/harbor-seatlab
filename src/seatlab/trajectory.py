"""Compare one role's observable decisions across two native worlds on a shared schedule.

Moves are observations, not causal attributions. Unlike ``team_align``, this walk never
stops at the first input or action divergence: it measures every turn of the named role
on the common schedule and stops only where the world-step order itself changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, TypedDict, cast

from . import tape as wire
from .episodes import EpisodeFrame
from .projection import Projection
from .verification import action_equal
from .world_order import read_world_order, validate_world_pair

__all__ = ["TRAJECTORY_SCHEMA", "role_trajectory"]

TRAJECTORY_SCHEMA = "seatlab.role-trajectory/v1"

Verdict = Literal["equal", "different", "unknown"]


class _Tally:
    """Counts for one dimension; step numbers are 1-based world steps."""

    __slots__ = ("equal", "different", "unknown", "first_different", "first_unknown")

    def __init__(self) -> None:
        self.equal = self.different = self.unknown = 0
        self.first_different: int | None = None
        self.first_unknown: int | None = None

    def add(self, verdict: Verdict, step: int) -> None:
        if verdict == "equal":
            self.equal += 1
        elif verdict == "different":
            self.different += 1
            if self.first_different is None:
                self.first_different = step
        else:
            self.unknown += 1
            if self.first_unknown is None:
                self.first_unknown = step

    def report(self) -> dict[str, object]:
        return {
            "equal": self.equal,
            "different": self.different,
            "unknown": self.unknown,
            "first_different_step": self.first_different,
            "first_unknown_step": self.first_unknown,
        }


class _Measure(TypedDict):
    raw_view: Verdict
    selected_view: Verdict
    accepted_action: Verdict


def _measure(left: EpisodeFrame, right: EpisodeFrame, projection: Projection) -> _Measure:
    if left["basis"] != "bound" or right["basis"] != "bound":
        return {
            "raw_view": "unknown",
            "selected_view": "unknown",
            "accepted_action": "unknown",
        }
    raw: Verdict = "equal" if left["view_sha256"] == right["view_sha256"] else "different"

    selected = raw
    if projection.fields is not None:
        left_bytes = projection.encode_present(left["view"])
        right_bytes = projection.encode_present(right["view"])
        if left_bytes is None or right_bytes is None:
            selected = "unknown"
        else:
            selected = "equal" if left_bytes == right_bytes else "different"

    left_obs, right_obs = left["observed"], right["observed"]
    action: Verdict
    if left_obs is None or right_obs is None:
        action = "unknown"
    else:
        action = (
            "equal" if action_equal(left_obs["action"], right_obs["action"]) else "different"
        )
    return {"raw_view": raw, "selected_view": selected, "accepted_action": action}


def role_trajectory(
    source: Path,
    target: Path,
    *,
    source_order: Path,
    target_order: Path,
    role: str,
    fields: Sequence[str] | None = None,
) -> dict[str, object]:
    """Measure one role's view and action differences across a shared world schedule.

    Only schedule-aligned world steps are compared; the walk stops before the first
    simultaneous role group that differs and never re-aligns by sorting or local turn.
    Every turn of ``role`` inside the common schedule is measured, even after another
    role's action changes. Three dimensions are counted independently, with their own
    denominators: ``raw_view`` (digest, both bound), ``selected_view`` (canonical JSON of
    the projected ``fields``, or the whole view; unknown if a projected field is absent on
    either side) and ``accepted_action`` (both bound and observed; strict equality).
    Unbound frames make all three unknown. No raw values are emitted.
    """
    if not isinstance(role, str):
        raise wire.TapeError("role must be a string")
    if fields is not None and (
        not isinstance(fields, Sequence)
        or isinstance(fields, (str, bytes, bytearray, memoryview))
        or len(fields) == 0
    ):
        raise wire.TapeError("fields must be None or a nonempty sequence of JSON Pointers")
    projection = Projection(fields)

    left_world = read_world_order(source, source_order)
    right_world = read_world_order(target, target_order)
    validate_world_pair(left_world, right_world)
    if role not in left_world.episodes:
        raise wire.TapeError(f"role {role!r} is not in the registered role set")

    left_frames = left_world.episodes[role]["frames"]
    right_frames = right_world.episodes[role]["frames"]
    left_total, right_total = len(left_world.steps), len(right_world.steps)
    shared = min(left_total, right_total)

    tallies = {name: _Tally() for name in ("raw_view", "selected_view", "accepted_action")}
    cursor = 0
    matched = 0
    order_changed = False
    for index in range(shared):
        group = left_world.steps[index]
        if group != right_world.steps[index]:
            order_changed = True
            break
        matched = index + 1
        if role in group:
            row = _measure(left_frames[cursor], right_frames[cursor], projection)
            cursor += 1
            for name, verdict in row.items():
                tallies[name].add(cast(Verdict, verdict), matched)

    boundary: int | None
    status: str
    if order_changed:
        boundary, status = matched + 1, "order_changed"
    else:
        boundary = None if left_total == right_total else shared + 1
        if not (left_world.closed and right_world.closed):
            status = "open"
        elif boundary is None:
            status = "complete"
        else:
            status = "length_changed"

    return {
        "schema": TRAJECTORY_SCHEMA,
        "source_native_trial_id": left_world.native_trial_id,
        "target_native_trial_id": right_world.native_trial_id,
        "case_sha256": left_world.case_sha256,
        "role": role,
        "fields": None if projection.fields is None else list(projection.fields),
        "status": status,
        "schedule": {
            "matched_steps": matched,
            "boundary_step": boundary,
            "source_steps": left_total,
            "target_steps": right_total,
        },
        "role_decisions": cursor,
        "raw_view": tallies["raw_view"].report(),
        "selected_view": tallies["selected_view"].report(),
        "accepted_action": tallies["accepted_action"].report(),
    }
