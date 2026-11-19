"""Compare one original and one fresh native cooperative MPE2 export, cell by cell.

`compare_cell(source, target)` takes two validated export directories, each with
`episodes.jsonl`, `order.json` and `outcomes.json`. It never raises on bad data.
It returns a board-safe summary of fixed tokens, counts, numbers and UUIDs.

status:
  measured     both sides are complete and bound; any difference is reported, not refused.
  unavailable  data is missing or incomplete; nothing is counted as equal.
  incompatible the pair or a receipt is invalid (same UUID, other case, tampered files).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from seatlab import tape as wire
from seatlab.world_order import WorldOrder, read_world_order

__all__ = ["compare_cell"]

ROLES = ("p0", "p1")
WORLD_STEPS = 20
EXPECTED_STEPS = tuple(ROLES for _ in range(WORLD_STEPS))
RETURN_TOLERANCE = 1e-6
OUTCOMES_SCHEMA = "seatlab.pz-outcomes/v1"
_OUTCOME_KEYS = frozenset(
    {"completion", "native_trial_id", "case_sha256", "terminated", "truncated", "returns"}
)


class _Stop(Exception):
    """Internal: stop with a stable status and reason token."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _read_world(root: Path, side: str) -> WorldOrder:
    episodes, order = root / "episodes.jsonl", root / "order.json"
    if not (episodes.is_file() and order.is_file()):
        raise _Stop("unavailable", f"{side}_missing")
    try:
        return read_world_order(episodes, order)
    except (OSError, UnicodeDecodeError):
        raise _Stop("unavailable", f"{side}_unreadable") from None
    except (wire.TapeError, ValueError, KeyError, TypeError):
        raise _Stop("incompatible", f"{side}_invalid_export") from None


def _real(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)  # type: ignore[arg-type]


def _read_outcomes(root: Path, side: str, world: WorldOrder) -> dict[str, float]:
    """Return validated per-role returns, bound to the same UUID and case as the world."""
    path = root / "outcomes.json"
    if not path.is_file():
        raise _Stop("unavailable", f"{side}_outcomes_missing")
    try:
        doc = wire.decode(path.read_bytes())
    except (OSError, UnicodeDecodeError):
        raise _Stop("unavailable", f"{side}_outcomes_unreadable") from None
    except (wire.TapeError, ValueError):
        raise _Stop("incompatible", f"{side}_outcomes_invalid") from None
    bad = _Stop("incompatible", f"{side}_outcomes_invalid")
    if not isinstance(doc, dict):
        raise bad
    keys = set(doc) - {"schema"}
    if keys != _OUTCOME_KEYS or doc.get("schema") != OUTCOMES_SCHEMA:
        raise bad
    if (
        doc["native_trial_id"] != world.native_trial_id
        or doc["case_sha256"] != world.case_sha256
    ):
        raise _Stop("incompatible", f"{side}_outcomes_mismatch")
    for field in ("returns", "terminated", "truncated"):
        value = doc[field]
        if not isinstance(value, dict) or set(value) != set(ROLES):
            raise bad
    if not all(_real(v) for v in doc["returns"].values()):
        raise bad
    if not all(type(v) is bool for f in ("terminated", "truncated") for v in doc[f].values()):
        raise bad
    if type(doc["completion"]) is not int or doc["completion"] not in (0, 1):
        raise bad
    if doc["completion"] != 1:
        raise _Stop("unavailable", f"{side}_outcomes_incomplete")
    return {role: float(doc["returns"][role]) for role in ROLES}


def _role_counts(source: WorldOrder, target: WorldOrder) -> dict[str, dict[str, int]]:
    """Align each role's frames on the common scheduled prefix and count agreement."""
    roles: dict[str, dict[str, int]] = {}
    for role in ROLES:
        left, right = source.episodes[role]["frames"], target.episodes[role]["frames"]
        actions = views = 0
        for a, b in zip(left, right):
            actions += a["observed"]["action"] == b["observed"]["action"]  # type: ignore[index]
            views += a["view_sha256"] == b["view_sha256"]
        roles[role] = {
            "decisions": min(len(left), len(right)),
            "actions_equal": actions,
            "views_equal": views,
        }
    return roles


def _measured_frames(world: WorldOrder) -> bool:
    return all(
        frame["basis"] == "bound"
        and isinstance(frame["observed"], dict)
        and type(frame["observed"].get("action")) is int
        for episode in world.episodes.values()
        for frame in episode["frames"]
    )


def compare_cell(source: Path, target: Path) -> dict[str, Any]:
    """Compare an original (source) and a fresh (target) cell export."""
    result: dict[str, Any] = {
        "status": "unavailable",
        "reason": None,
        "source_trial_id": None,
        "target_trial_id": None,
        "case_sha256": None,
        "world_steps": None,
        "order_equal": None,
        "returned": None,
        "max_return_gap": None,
        "returns": None,
        "roles": None,
    }
    try:
        _compare(Path(source), Path(target), result)
    except _Stop as stop:
        result["status"], result["reason"] = stop.status, stop.reason
    return result


def _compare(source: Path, target: Path, result: dict[str, Any]) -> None:
    src = _read_world(source, "source")
    tgt = _read_world(target, "target")
    result["source_trial_id"] = src.native_trial_id
    result["target_trial_id"] = tgt.native_trial_id
    if src.case_sha256 != tgt.case_sha256:
        raise _Stop("incompatible", "case_mismatch")
    result["case_sha256"] = src.case_sha256
    if src.native_trial_id == tgt.native_trial_id:
        raise _Stop("incompatible", "same_trial")
    if set(src.episodes) != set(ROLES) or set(tgt.episodes) != set(ROLES):
        raise _Stop("incompatible", "roles_unexpected")

    result["order_equal"] = src.steps == tgt.steps
    if len(src.steps) == len(tgt.steps):
        result["world_steps"] = len(src.steps)

    src_ret = _read_outcomes(source, "source", src)
    tgt_ret = _read_outcomes(target, "target", tgt)
    gaps = {role: abs(src_ret[role] - tgt_ret[role]) for role in ROLES}
    result["returns"] = {r: {"source": src_ret[r], "target": tgt_ret[r]} for r in ROLES}
    result["max_return_gap"] = max(gaps.values())
    result["returned"] = all(gap <= RETURN_TOLERANCE for gap in gaps.values())

    if not (src.closed and tgt.closed):
        raise _Stop("unavailable", "episode_open")
    if src.steps != EXPECTED_STEPS or tgt.steps != EXPECTED_STEPS:
        raise _Stop("unavailable", "schedule_unexpected")
    if not (_measured_frames(src) and _measured_frames(tgt)):
        raise _Stop("unavailable", "frames_unmeasured")

    result["roles"] = _role_counts(src, tgt)
    result["status"] = "measured"
