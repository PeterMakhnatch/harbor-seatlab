#!/bin/bash
set -euo pipefail

python3 - <<'PY'
import hashlib
import json
from pathlib import Path

ROLES = ("p1", "p2")
MODES = ("full", "first")
WORLD_DIR = Path("/world")
RECEIPTS_PATH = Path("/logs/world/receipts.jsonl")
SEATS_DIR = Path("/logs/seats")
REWARD_PATH = Path("/logs/verifier/reward.txt")
RECEIPT_FIELDS = frozenset({"role", "turn", "view", "acknowledged_sha256", "action"})


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def strict_loads(text: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        obj: dict[str, object] = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate key: {key!r}")
            obj[key] = value
        return obj

    def reject_constant(token: str) -> object:
        raise ValueError(f"non-finite constant: {token!r}")

    return json.loads(
        text,
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_constant,
    )


def expected_views() -> dict[str, dict[str, object]]:
    case = strict_loads((WORLD_DIR / "case.json").read_text(encoding="utf-8"))
    mode = strict_loads((WORLD_DIR / "mode.json").read_text(encoding="utf-8"))
    if not isinstance(case, dict) or case.keys() != {"options"}:
        raise ValueError("invalid case.json")
    options = case["options"]
    if not isinstance(options, dict) or set(options.keys()) != set(ROLES):
        raise ValueError("invalid case.json options")
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError("invalid mode.json")
    views: dict[str, dict[str, object]] = {}
    for role in ROLES:
        items = options[role]
        if (
            not isinstance(items, list)
            or not items
            or any(not isinstance(item, str) or not item for item in items)
        ):
            raise ValueError(f"invalid options for {role}")
        visible = list(items) if mode == "full" else [items[0]]
        views[role] = {"role": role, "options": visible}
    return views


def left_a_record(seat: str) -> bool:
    agent_dir = SEATS_DIR / seat / "agent"
    if not agent_dir.is_dir():
        return False
    for name in ("trajectory.json", "oracle.txt"):
        candidate = agent_dir / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return True
    return False


def receipts_valid(views: dict[str, dict[str, object]]) -> bool:
    try:
        raw_lines = RECEIPTS_PATH.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    lines = [line for line in raw_lines if line]
    if len(lines) != len(ROLES):
        return False
    seen: set[str] = set()
    for line in lines:
        try:
            row = strict_loads(line)
        except ValueError:
            return False
        if not isinstance(row, dict) or row.keys() != RECEIPT_FIELDS:
            return False
        role = row["role"]
        if not isinstance(role, str) or role not in views or role in seen:
            return False
        if type(row["turn"]) is not int or row["turn"] != 1:
            return False
        expected_view = views[role]
        if row["view"] != expected_view:
            return False
        if row["acknowledged_sha256"] != canonical_digest(expected_view):
            return False
        action = row["action"]
        allowed = expected_view["options"]
        if not isinstance(action, str) or not isinstance(allowed, list) or action not in allowed:
            return False
        seen.add(role)
    return seen == set(ROLES)


def grade() -> int:
    try:
        views = expected_views()
    except (OSError, UnicodeDecodeError, ValueError):
        return 0
    if not all(left_a_record(role) for role in ROLES):
        return 0
    return 1 if receipts_valid(views) else 0


REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
REWARD_PATH.write_text(f"{grade()}\n", encoding="utf-8")
PY
