"""Compare attested prefixes of independently recorded native role episodes."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast
from uuid import UUID

from . import tape as wire
from .episodes import Episode, read_episodes
from .viewdiff import InputDiff, diff_input

__all__ = ["align"]

_PAIRS_SCHEMA = "seatlab.episode-pairs/v1"
_ALIGNMENT_SCHEMA = "seatlab.alignment/v1"
_Arm = Literal["source", "target"]
_Seat = tuple[str, str]  # native_trial_id, role
_ARMS: tuple[_Arm, _Arm] = ("source", "target")
_Status = Literal[
    "missing",
    "unbound",
    "missing_observation",
    "input_changed",
    "action_changed",
    "length_changed",
    "open_prefix",
    "matched",
]
_STATUSES: tuple[_Status, ...] = (
    "missing",
    "unbound",
    "missing_observation",
    "input_changed",
    "action_changed",
    "length_changed",
    "open_prefix",
    "matched",
)


class _Receipt(TypedDict):
    native_trial_id: str
    episode_sha256: str


class _Pair(TypedDict):
    id: str
    case_sha256: str
    role: str
    source: _Receipt | None
    target: _Receipt | None


class _AlignedPair(_Pair):
    status: _Status
    matched_prefix: int
    boundary_turn: int | None
    source_action: wire.Observation | None
    target_action: wire.Observation | None
    source_view_sha256: str | None
    target_view_sha256: str | None
    input_diff: NotRequired[InputDiff]


_Registrations = dict[_Arm, dict[_Seat, _Pair]]
_PAIR_FIELDS = frozenset(_Pair.__annotations__)
_RECEIPT_FIELDS = frozenset(_Receipt.__annotations__)


def _receipt(value: object, where: str) -> _Receipt | None:
    if value is None:
        return None
    receipt = wire._object(value, _RECEIPT_FIELDS, where)
    wire._check_sha256(receipt["episode_sha256"], f"{where} episode_sha256")
    trial = receipt["native_trial_id"]
    if isinstance(trial, str) and len(trial) == 36:
        try:
            if str(UUID(trial)) == trial:
                return cast(_Receipt, receipt)
        except ValueError:
            pass
    raise wire.TapeError(f"{where} requires a canonical native_trial_id UUID")


def _read_pairs(path: Path) -> tuple[list[_Pair], _Registrations]:
    document = wire._object(
        wire.decode(path.read_bytes()),
        frozenset({"schema", "pairs"}),
        "episode pair document",
    )
    items = document["pairs"]
    if document["schema"] != _PAIRS_SCHEMA or not isinstance(items, list):
        raise wire.TapeError(f"invalid {_PAIRS_SCHEMA} document")

    pairs: list[_Pair] = []
    ids: set[str] = set()
    cases: set[tuple[str, str]] = set()
    expected: _Registrations = {"source": {}, "target": {}}
    for value in cast(list[object], items):
        item = wire._object(value, _PAIR_FIELDS, "episode pair")
        pair_id, role, case = item["id"], item["role"], item["case_sha256"]
        if not isinstance(pair_id, str) or wire._KEY.fullmatch(pair_id) is None:
            raise wire.TapeError("episode pair id must be a portable ASCII label")
        if not isinstance(role, str) or wire._ROLE.fullmatch(role) is None:
            raise wire.TapeError(f"pair {pair_id!r} role must be a short ASCII identifier")
        wire._check_sha256(case, f"pair {pair_id!r} case_sha256")
        case = cast(str, case)
        case_role = case, role
        if pair_id in ids:
            raise wire.TapeError(f"duplicate episode pair id {pair_id!r}")
        if case_role in cases:
            raise wire.TapeError(f"duplicate case_sha256 and role in pair {pair_id!r}")
        ids.add(pair_id)
        cases.add(case_role)

        source = _receipt(item["source"], f"pair {pair_id!r} source receipt")
        target = _receipt(item["target"], f"pair {pair_id!r} target receipt")
        if source is not None and target is not None:
            if source["native_trial_id"] == target["native_trial_id"]:
                raise wire.TapeError(
                    f"pair {pair_id!r} uses the same native_trial_id in both arms"
                )
        pair: _Pair = {
            "id": pair_id,
            "case_sha256": case,
            "role": role,
            "source": source,
            "target": target,
        }
        for arm in _ARMS:
            receipt = pair[arm]
            if receipt is not None:
                seat = receipt["native_trial_id"], role
                if seat in expected[arm]:
                    raise wire.TapeError(
                        f"duplicate {arm} native_trial_id and role in pair {pair_id!r}"
                    )
                expected[arm][seat] = pair
        pairs.append(pair)

    pairs.sort(key=lambda pair: pair["id"])
    return pairs, expected


def _read_arm(path: Path, arm: _Arm, expected: dict[_Seat, _Pair]) -> dict[_Seat, Episode]:
    # The reader rejects duplicate trial and role identities before yielding a record.
    actual = {(row["native_trial_id"], row["role"]): row for row in read_episodes(path)}
    if actual.keys() != expected.keys():
        missing = len(expected.keys() - actual.keys())
        extra = len(actual.keys() - expected.keys())
        raise wire.TapeError(
            f"alignment {arm} coverage mismatch: {missing} missing, {extra} unexpected"
        )
    for seat, pair in expected.items():
        receipt = pair[arm]
        assert receipt is not None  # Only nonnull receipts are registered for the arm.
        episode = actual[seat]
        if episode["case_sha256"] != pair["case_sha256"]:
            raise wire.TapeError(
                f"pair {pair['id']!r} {arm} case_sha256 does not match episode"
            )
        if episode["episode_sha256"] != receipt["episode_sha256"]:
            raise wire.TapeError(
                f"pair {pair['id']!r} {arm} episode_sha256 does not match receipt"
            )
    return actual


def _get_episode(
    receipt: _Receipt | None, role: str, episodes: dict[_Seat, Episode]
) -> Episode | None:
    return None if receipt is None else episodes[receipt["native_trial_id"], role]


def _align_pair(
    pair: _Pair, source: Episode | None, target: Episode | None, *, input_paths: int | None
) -> _AlignedPair:
    result: _AlignedPair = {
        **pair,
        "status": "missing",
        "matched_prefix": 0,
        "boundary_turn": None,
        "source_action": None,
        "target_action": None,
        "source_view_sha256": None,
        "target_view_sha256": None,
    }
    if source is None or target is None:
        return result

    left_frames, right_frames = source["frames"], target["frames"]
    for left, right in zip(left_frames, right_frames):
        left_view, right_view = left["view_sha256"], right["view_sha256"]
        left_action, right_action = left["observed"], right["observed"]
        if left["basis"] != "bound" or right["basis"] != "bound":
            result["status"] = "unbound"
        elif left_action is None or right_action is None:
            result["status"] = "missing_observation"
        else:
            if left_view != right_view:
                result["status"] = "input_changed"
                if input_paths is not None:
                    result["input_diff"] = diff_input(
                        left["view"], right["view"], limit=input_paths
                    )
            else:
                a, b = left_action["action"], right_action["action"]
                if type(a) is type(b) and a == b:
                    result["matched_prefix"] += 1
                    continue
                result["status"] = "action_changed"
            result["source_action"] = left_action
            result["target_action"] = right_action
        result["boundary_turn"] = left["turn"]
        result["source_view_sha256"] = left_view
        result["target_view_sha256"] = right_view
        return result

    if len(left_frames) != len(right_frames):
        result["status"] = "length_changed"
        result["boundary_turn"] = result["matched_prefix"] + 1
    elif source["closed"] and target["closed"]:
        result["status"] = "matched"
    else:
        result["status"] = "open_prefix"
    return result


def align(
    source: Path, target: Path, pairs: Path, *, input_paths: int | None = None
) -> dict[str, object]:
    """Align a registered cohort of independent native episode tapes.

    Return the matched turn count and the first boundary for each pair. Only
    bound frames with observations on both arms enter input or action comparison.
    At a shared boundary, report the recorded view digests and, if eligible,
    the actual action envelopes. A length boundary has no paired frame. Fully
    matched and open prefixes have no observed boundary. Never infer rewards.

    With input_paths, report changes in the two views at a changed-input
    boundary, without reporting field values.
    """
    if input_paths is not None and not (type(input_paths) is int and 1 <= input_paths <= 128):
        raise wire.TapeError("input_paths must be an integer from 1 to 128")
    manifest, expected = _read_pairs(pairs)
    source_episodes = _read_arm(source, "source", expected["source"])
    target_episodes = _read_arm(target, "target", expected["target"])

    counts = {status: 0 for status in ("pairs", "matched_prefix", *_STATUSES)}
    counts["pairs"] = len(manifest)
    results = []
    for pair in manifest:
        role = pair["role"]
        left = _get_episode(pair["source"], role, source_episodes)
        right = _get_episode(pair["target"], role, target_episodes)
        row = _align_pair(pair, left, right, input_paths=input_paths)
        counts[row["status"]] += 1
        counts["matched_prefix"] += row["matched_prefix"]
        results.append(row)
    return {"schema": _ALIGNMENT_SCHEMA, "counts": counts, "pairs": results}
