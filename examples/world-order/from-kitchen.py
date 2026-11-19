"""Build one seatlab.world-order/v1 manifest from a native Cramped Room kitchen trial."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast
from uuid import UUID

from seatlab.episodes import Episode, read_episodes
from seatlab.tape import TapeError, _check_sha256, canonical, decode, digest, write_lines

ROLES = ("chef0", "chef1")
DIRS = {(0, -1): "north", (0, 1): "south", (1, 0): "east", (-1, 0): "west", (0, 0): "stay"}
_WORLD = Path("artifacts/logs/world/kitchen.jsonl")
_ACKS = Path("artifacts/logs/world/seatlab-acks.jsonl")
_ROW_KEYS = frozenset(
    {
        "tick",
        "joint_action",
        "state",
        "reward",
        "sparse_reward_by_agent",
        "shaped_reward_by_agent",
        "soup_delivery_by_agent",
    }
)


def _obj(raw: bytes, where: str) -> dict[str, object]:
    val = decode(raw)
    if not isinstance(val, dict):
        raise TapeError(f"{where}: expected a JSON object")
    return cast(dict[str, object], val)


def _uuid(val: object, where: str) -> str:
    if isinstance(val, str) and len(val) == 36:
        try:
            u = UUID(val)
            if str(u) == val and u.int != 0:
                return val
        except ValueError:
            pass
    raise TapeError(f"{where}: expected a canonical nonzero UUID")


def _word(val: object, tick: int) -> str:
    if val == "interact":
        return "interact"
    if isinstance(val, list) and len(val) == 2:
        dx, dy = cast(list[object], val)
        if type(dx) is int and type(dy) is int:
            word = DIRS.get((dx, dy))
            if word is not None:
                return word
    raise TapeError(f"kitchen tick {tick}: invalid native action")


def _jsonl(path: Path) -> list[dict[str, object]]:
    lines = path.read_bytes().splitlines()
    if not lines:
        raise TapeError(f"{path}: expected nonempty JSONL")
    return [_obj(line, f"{path}:{i}") for i, line in enumerate(lines, 1)]


def build_order(
    episodes_path: Path,
    *,
    trial_dir: Path | None,
    kitchen_log: Path | None,
    ack_log: Path | None,
    native_trial_id: str | None,
) -> dict[str, object]:
    if trial_dir is not None:
        res = _obj((trial_dir / "result.json").read_bytes(), "result.json")
        tid = _uuid(res.get("id"), "result.json id")
        if native_trial_id is not None and _uuid(native_trial_id, "--native-trial-id") != tid:
            raise TapeError("--native-trial-id disagrees with trial result.json id")
        world_path = kitchen_log or (trial_dir / _WORLD)
        ack_path = ack_log or (trial_dir / _ACKS)
    else:
        if kitchen_log is None or ack_log is None or native_trial_id is None:
            raise TapeError(
                "pass --trial, or --kitchen-log, --ack-log, and --native-trial-id"
            )
        tid = _uuid(native_trial_id, "--native-trial-id")
        world_path, ack_path = kitchen_log, ack_log

    by_role: dict[str, Episode] = {}
    cases: set[str] = set()
    for ep in read_episodes(episodes_path):
        if ep["native_trial_id"] != tid:
            raise TapeError(f"{ep['id']}: native_trial_id does not match {tid}")
        cases.add(ep["case_sha256"])
        by_role[ep["role"]] = ep
    if set(by_role) != set(ROLES) or len(cases) != 1:
        raise TapeError("episodes must contain exactly chef0 and chef1 for one case")
    case_sha = next(iter(cases))
    _check_sha256(case_sha, "case_sha256")

    rows, acks = _jsonl(world_path), _jsonl(ack_path)
    if len(rows) != len(acks) or any(len(by_role[r]["frames"]) != len(rows) for r in ROLES):
        raise TapeError("transition, ack, and episode frame counts must match")

    prev_post: str | None = None
    for turn, (row, ack) in enumerate(zip(rows, acks, strict=True), 1):
        tick = row.get("tick")
        if row.keys() != _ROW_KEYS or type(tick) is not int or tick != turn:
            raise TapeError(f"kitchen.jsonl tick {turn}: invalid transition row")
        ack_tick = ack.get("tick")
        if (
            ack.keys() != {"tick", "view", "ack_sha256"}
            or type(ack_tick) is not int
            or ack_tick != turn - 1
        ):
            raise TapeError(f"seatlab-acks.jsonl tick {turn - 1}: invalid ack row")
        view, ack_raw = ack.get("view"), ack.get("ack_sha256")
        if not isinstance(view, dict) or not isinstance(ack_raw, dict):
            raise TapeError(f"seatlab-acks.jsonl tick {turn - 1}: invalid view or ack_sha256")
        view_obj = cast(dict[str, object], view)
        ack_map = cast(dict[str, object], ack_raw)
        view_tick = view_obj.get("tick")
        if (
            view_obj.keys() != {"tick", "state"}
            or type(view_tick) is not int
            or view_tick != turn - 1
            or ack_map.keys() != set(ROLES)
        ):
            raise TapeError(f"seatlab-acks.jsonl tick {turn - 1}: invalid view or ack_sha256")
        view_sha = digest(view_obj)
        if prev_post is not None and view_sha != prev_post:
            raise TapeError(
                f"tick {turn - 1}: pre-action view disagrees with prior post-state"
            )
        joint = row.get("joint_action")
        if not isinstance(joint, list) or len(joint) != len(ROLES):
            raise TapeError(f"kitchen.jsonl tick {turn}: invalid joint_action")
        actions = [_word(item, turn) for item in cast(list[object], joint)]
        for role, action in zip(ROLES, actions, strict=True):
            frame = by_role[role]["frames"][turn - 1]
            if (
                ack_map.get(role) != view_sha
                or frame["basis"] != "bound"
                or frame["view_sha256"] != view_sha
            ):
                raise TapeError(f"{role} turn {turn}: view/ack digest disagrees with episode")
            if frame["observed"] is None or frame["observed"]["action"] != action:
                raise TapeError(f"{role} turn {turn}: native action disagrees with episode")
        state = row.get("state")
        if not isinstance(state, dict):
            raise TapeError(f"kitchen.jsonl tick {turn}: invalid post-state")
        state_obj = cast(dict[str, object], state)
        timestep = state_obj.get("timestep")
        if type(timestep) is not int or timestep != turn:
            raise TapeError(f"kitchen.jsonl tick {turn}: invalid post-state")
        prev_post = digest({"tick": turn, "state": state_obj})

    return {
        "schema": "seatlab.world-order/v1",
        "case_sha256": case_sha,
        "native_trial_id": tid,
        "episodes": {r: by_role[r]["episode_sha256"] for r in ROLES},
        "steps": [list(ROLES) for _ in rows],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episodes", type=Path, help="one-trial kitchen episodes JSONL")
    p.add_argument("--trial", type=Path, help="native Harbor trial dir (result.json + logs)")
    p.add_argument("--kitchen-log", type=Path, help="raw artifacts/logs/world/kitchen.jsonl")
    p.add_argument("--ack-log", type=Path, help="raw artifacts/logs/world/seatlab-acks.jsonl")
    p.add_argument("--native-trial-id", help="expected trial UUID (required without --trial)")
    p.add_argument("--out", type=Path, required=True, help="new world-order JSON path")
    a = p.parse_args(argv)
    try:
        order = build_order(
            a.episodes,
            trial_dir=a.trial,
            kitchen_log=a.kitchen_log,
            ack_log=a.ack_log,
            native_trial_id=a.native_trial_id,
        )
        write_lines(a.out, [order])
    except (OSError, ValueError, TapeError) as exc:
        print(f"{p.prog}: {exc}", file=sys.stderr)
        return 1
    print(canonical(order).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
