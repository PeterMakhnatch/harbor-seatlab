"""Build one seatlab.world-order/v1 manifest from a native Hanabi world log and trial tape."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast
from uuid import UUID

from seatlab.episodes import Episode, read_episodes
from seatlab.tape import TapeError, _check_sha256, canonical, decode, digest, write_lines

SEATS = ("p0", "p1")
_WORLD = Path("artifacts/logs/world/hanabi.json")
_DOC_KEYS = frozenset({"schema", "deck_id", "deck_sha256", "turns", "scores"})
_TURN_KEYS = frozenset({"turn", "seat", "view", "view_sha256", "action"})


def _obj(path: Path, label: str) -> dict[str, object]:
    val = decode(path.read_bytes())
    if not isinstance(val, dict):
        raise TapeError(f"{label}: expected a JSON object")
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


def build_order(
    episodes_path: Path,
    *,
    trial_dir: Path | None,
    hanabi_log: Path | None,
    native_trial_id: str | None,
) -> dict[str, object]:
    if trial_dir is not None:
        tid = _uuid(
            _obj(trial_dir / "result.json", "result.json").get("id"), "result.json id"
        )
        if native_trial_id is not None and _uuid(native_trial_id, "--native-trial-id") != tid:
            raise TapeError("--native-trial-id disagrees with trial result.json id")
        log_path = hanabi_log or (trial_dir / _WORLD)
    else:
        if hanabi_log is None or native_trial_id is None:
            raise TapeError("pass --trial, or --hanabi-log and --native-trial-id")
        tid, log_path = _uuid(native_trial_id, "--native-trial-id"), hanabi_log

    doc = _obj(log_path, "hanabi.json")
    if doc.keys() != _DOC_KEYS or doc.get("schema") != "seatlab.hanabi-native/v1":
        raise TapeError("hanabi.json: expected seatlab.hanabi-native/v1 object")
    deck_sha, turns, scores = doc.get("deck_sha256"), doc.get("turns"), doc.get("scores")
    _check_sha256(deck_sha, "deck_sha256")
    deck_id = doc.get("deck_id")
    if (
        type(deck_id) is not int
        or not isinstance(scores, list)
        or len(scores) != 2
        or any(type(item) is not int for item in cast(list[object], scores))
        or not isinstance(turns, list)
        or not turns
    ):
        raise TapeError("hanabi.json: invalid deck_id, scores, or turns")
    deck_sha_str = cast(str, deck_sha)
    by_role: dict[str, Episode] = {}
    for ep in read_episodes(episodes_path):
        if ep["native_trial_id"] != tid:
            raise TapeError(f"{ep['id']}: native_trial_id does not match {tid}")
        if ep["case_sha256"] != deck_sha_str:
            raise TapeError(f"{ep['id']}: case_sha256 disagrees with hanabi.json deck_sha256")
        if ep["role"] not in SEATS:
            raise TapeError(f"{ep['id']}: unexpected Hanabi role {ep['role']!r}")
        by_role[ep["role"]] = ep
    if not by_role:
        raise TapeError("episodes file contains no episodes")

    cursors: dict[str, int] = dict.fromkeys(by_role, 0)
    steps: list[list[str]] = []
    for idx, raw in enumerate(cast(list[object], turns), 1):
        if not isinstance(raw, dict) or raw.keys() != _TURN_KEYS:
            raise TapeError(f"hanabi turn {idx}: invalid turn object")
        turn_row = cast(dict[str, object], raw)
        seat, view, vsha, action = (
            turn_row.get("seat"),
            turn_row.get("view"),
            turn_row.get("view_sha256"),
            turn_row.get("action"),
        )
        turn_no = turn_row.get("turn")
        if (
            type(turn_no) is not int
            or turn_no != idx
            or not isinstance(seat, str)
            or seat not in SEATS
        ):
            raise TapeError(f"hanabi turn {idx}: invalid turn number or seat")
        if not isinstance(view, dict) or type(action) is not int:
            raise TapeError(f"hanabi turn {idx}: view/view_sha256/action mismatch")
        view_obj = cast(dict[str, object], view)
        if vsha != digest(view_obj):
            raise TapeError(f"hanabi turn {idx}: view/view_sha256/action mismatch")
        if seat not in by_role:
            raise TapeError(f"hanabi turn {idx}: episode missing for acting role {seat!r}")
        pos, frames = cursors[seat], by_role[seat]["frames"]
        if pos >= len(frames):
            raise TapeError(f"hanabi turn {idx}: more {seat} turns than episode frames")
        frame = frames[pos]
        if frame["basis"] != "bound" or frame["view_sha256"] != vsha:
            raise TapeError(f"hanabi turn {idx}: {seat} view_sha256 disagrees with episode")
        if frame["observed"] is None or frame["observed"]["action"] != action:
            raise TapeError(f"hanabi turn {idx}: {seat} action disagrees with episode")
        cursors[seat] = pos + 1
        steps.append([seat])

    for role, ep in by_role.items():
        if cursors[role] != len(ep["frames"]):
            raise TapeError(
                f"{role}: episode has {len(ep['frames'])} frames, log had {cursors[role]}"
            )
    return {
        "schema": "seatlab.world-order/v1",
        "case_sha256": deck_sha_str,
        "native_trial_id": tid,
        "episodes": {r: by_role[r]["episode_sha256"] for r in sorted(by_role)},
        "steps": steps,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episodes", type=Path, help="one-trial Hanabi episodes JSONL")
    p.add_argument("--trial", type=Path, help="native Harbor trial dir (result.json + log)")
    p.add_argument("--hanabi-log", type=Path, help="raw artifacts/logs/world/hanabi.json")
    p.add_argument("--native-trial-id", help="expected trial UUID (required without --trial)")
    p.add_argument("--out", type=Path, required=True, help="new world-order JSON path")
    a = p.parse_args(argv)
    try:
        order = build_order(
            a.episodes,
            trial_dir=a.trial,
            hanabi_log=a.hanabi_log,
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
