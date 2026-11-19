"""Bound memoryless sufficiency and held-out lookup accuracy for native episodes."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias, TypedDict

from . import tape as wire
from .episodes import Episode, read_episodes
from .projection import Projection

__all__ = ["AUDIT_SCHEMA", "audit_context"]

AUDIT_SCHEMA = "seatlab.context-audit/v1"
_MAX_WITNESSES = 100

_ActionKey: TypeAlias = tuple[type[wire.Action], wire.Action]
_ClassKey: TypeAlias = tuple[str, bytes]
_ReceiptKey: TypeAlias = tuple[str, int]  # (native_trial_id, turn)


class _Example(TypedDict):
    native_trial_id: str
    turn: int
    episode_sha256: str
    view_sha256: str
    action_sha256: str


class _Witness(TypedDict):
    role: str
    projection_sha256: str
    count: int
    examples: list[_Example]


class _FitSummary(TypedDict):
    classes: int
    repeated_classes: int
    repeated_frames: int
    conflicting_classes: int
    minimum_errors: int
    maximum_matches: int


class _HeldoutSummary(TypedDict):
    unseen: int
    tied: int
    matched: int
    disagreed: int


@dataclass(slots=True)
class _Group:
    total: int = 0
    actions: Counter[_ActionKey] = field(default_factory=Counter)


@dataclass(slots=True)
class _SelectedGroup(_Group):
    # Keep at most 2 distinct action hashes (smallest action_sha256) with each
    # action's earliest receipt by (native_trial_id, turn).
    earliest: dict[str, tuple[_ReceiptKey, _Example]] = field(default_factory=dict)


def _action_key(action: wire.Action) -> _ActionKey:
    return (type(action), action)


def _action_sha256(action: wire.Action) -> str:
    return wire.digest({"action": action})


def _counts_dict() -> dict[str, int]:
    return {
        "episodes": 0,
        "cases": 0,
        "trials": 0,
        "frames": 0,
        "eligible": 0,
        "excluded_unbound": 0,
        "excluded_missing": 0,
    }


def _record_selected(
    groups: dict[_ClassKey, _SelectedGroup],
    key: _ClassKey,
    action_key: _ActionKey,
    episode: Episode,
    turn: int,
    view_sha256: str,
    *,
    track_witness: bool,
) -> None:
    group = groups.get(key)
    if group is None:
        group = _SelectedGroup()
        groups[key] = group
    group.total += 1
    group.actions[action_key] += 1
    if not track_witness:
        return
    action_hash = _action_sha256(action_key[1])
    earliest = group.earliest
    if action_hash not in earliest and len(earliest) >= 2:
        worst_hash = max(earliest)
        if action_hash > worst_hash:
            return
        del earliest[worst_hash]
    receipt_key: _ReceiptKey = (episode["native_trial_id"], turn)
    current = earliest.get(action_hash)
    if current is None or receipt_key < current[0]:
        example: _Example = {
            "native_trial_id": episode["native_trial_id"],
            "turn": turn,
            "episode_sha256": episode["episode_sha256"],
            "view_sha256": view_sha256,
            "action_sha256": action_hash,
        }
        earliest[action_hash] = (receipt_key, example)


def _record_full(
    groups: dict[_ClassKey, _Group],
    key: _ClassKey,
    action_key: _ActionKey,
) -> None:
    group = groups.get(key)
    if group is None:
        group = _Group()
        groups[key] = group
    group.total += 1
    group.actions[action_key] += 1


def _summarize_groups(groups: Mapping[_ClassKey, _Group]) -> _FitSummary:
    classes = len(groups)
    repeated_classes = 0
    repeated_frames = 0
    conflicting_classes = 0
    minimum_errors = 0
    maximum_matches = 0
    for group in groups.values():
        total = group.total
        if total >= 2:
            repeated_classes += 1
            repeated_frames += total
        if len(group.actions) >= 2:
            conflicting_classes += 1
        top = max(group.actions.values())
        maximum_matches += top
        minimum_errors += total - top
    return {
        "classes": classes,
        "repeated_classes": repeated_classes,
        "repeated_frames": repeated_frames,
        "conflicting_classes": conflicting_classes,
        "minimum_errors": minimum_errors,
        "maximum_matches": maximum_matches,
    }


def _build_witnesses(
    groups: dict[_ClassKey, _SelectedGroup],
    limit: int,
) -> list[_Witness]:
    if limit == 0:
        return []
    candidates: list[tuple[tuple[str, str], _Witness]] = []
    for (role, encoded), group in groups.items():
        if len(group.actions) < 2:
            continue
        projection_sha256 = hashlib.sha256(encoded).hexdigest()
        # Two deterministic distinct action hashes (lexicographically smallest)
        # with each action's earliest receipt by (native_trial_id, turn).
        examples = [example for _hash, (_receipt, example) in sorted(group.earliest.items())]
        witness: _Witness = {
            "role": role,
            "projection_sha256": projection_sha256,
            "count": group.total,
            "examples": examples,
        }
        candidates.append(((role, projection_sha256), witness))
    candidates.sort(key=lambda item: item[0])
    return [witness for _sort_key, witness in candidates[:limit]]


def _unique_lookup(
    groups: Mapping[_ClassKey, _Group],
) -> dict[_ClassKey, _ActionKey | None]:
    """Map each class to its unique most frequent train action, or None if tied."""
    lookup: dict[_ClassKey, _ActionKey | None] = {}
    for key, group in groups.items():
        top_count = 0
        winner: _ActionKey | None = None
        tied = False
        for action_key, count in group.actions.items():
            if count > top_count:
                top_count = count
                winner = action_key
                tied = False
            elif count == top_count:
                tied = True
        lookup[key] = None if tied else winner
    return lookup


def _score_lookup(
    lookup: dict[_ClassKey, _ActionKey | None],
    key: _ClassKey,
    actual: _ActionKey,
    summary: _HeldoutSummary,
) -> None:
    if key not in lookup:
        summary["unseen"] += 1
        return
    predicted = lookup[key]
    if predicted is None:
        summary["tied"] += 1
    elif predicted == actual:
        summary["matched"] += 1
    else:
        summary["disagreed"] += 1


def _score_heldout(
    path: Path,
    projection: Projection,
    *,
    is_full: bool,
    train_trials: set[str],
    train_cases: set[str],
    full_lookup: dict[_ClassKey, _ActionKey | None],
    selected_lookup: dict[_ClassKey, _ActionKey | None],
) -> dict[str, object]:
    counts = _counts_dict()
    cases: set[str] = set()
    trials: set[str] = set()
    full_summary: _HeldoutSummary = {
        "unseen": 0,
        "tied": 0,
        "matched": 0,
        "disagreed": 0,
    }
    selected_summary: _HeldoutSummary = {
        "unseen": 0,
        "tied": 0,
        "matched": 0,
        "disagreed": 0,
    }

    for episode in read_episodes(path):
        trial = episode["native_trial_id"]
        case = episode["case_sha256"]
        if trial in train_trials:
            raise ValueError(f"heldout shares native_trial_id {trial!r} with train")
        if case in train_cases:
            raise ValueError(f"heldout shares case_sha256 {case!r} with train")
        counts["episodes"] += 1
        trials.add(trial)
        cases.add(case)
        role = episode["role"]
        for frame in episode["frames"]:
            counts["frames"] += 1
            if frame["basis"] != "bound":
                counts["excluded_unbound"] += 1
                continue
            observed = frame["observed"]
            if observed is None:
                counts["excluded_missing"] += 1
                continue
            counts["eligible"] += 1
            actual = _action_key(observed["action"])
            if is_full:
                full_key: _ClassKey = (role, projection.encode(frame["view"]))
                _score_lookup(full_lookup, full_key, actual, full_summary)
            else:
                full_key = (role, wire.canonical(frame["view"]))
                _score_lookup(full_lookup, full_key, actual, full_summary)
                selected_key: _ClassKey = (role, projection.encode(frame["view"]))
                _score_lookup(selected_lookup, selected_key, actual, selected_summary)

    if counts["episodes"] == 0:
        raise ValueError("heldout episode file contains no episodes")

    counts["cases"] = len(cases)
    counts["trials"] = len(trials)
    return {
        "counts": counts,
        "full": full_summary,
        "selected": dict(full_summary) if is_full else selected_summary,
    }


def audit_context(
    train: Path,
    *,
    fields: Sequence[str] | None = None,
    heldout: Path | None = None,
    witnesses: int = 3,
) -> dict[str, object]:
    """Audit memoryless sufficiency of a role-local view projection on native episodes.

    For each role separately, group eligible frames (bound view and known observed
    action, including an accepted null action) by exact canonical full view bytes and
    by exact canonical projected bytes. Any group with more than one distinct typed
    observed action proves a lower bound on disagreements for every deterministic
    memoryless action lookup on the recorded sample.

    This audit does not execute policies, infer rewards, or attribute causality.
    """
    if type(witnesses) is not int or not 0 <= witnesses <= _MAX_WITNESSES:
        raise ValueError(f"witnesses must be an integer from 0 to {_MAX_WITNESSES}")

    projection = Projection(fields)
    is_full = projection.fields is None
    track_witness = witnesses > 0

    counts = _counts_dict()
    train_cases: set[str] = set()
    train_trials: set[str] = set()
    selected_groups: dict[_ClassKey, _SelectedGroup] = {}
    full_groups: dict[_ClassKey, _Group] = {}

    for episode in read_episodes(train):
        counts["episodes"] += 1
        train_trials.add(episode["native_trial_id"])
        train_cases.add(episode["case_sha256"])
        role = episode["role"]
        for frame in episode["frames"]:
            counts["frames"] += 1
            if frame["basis"] != "bound":
                counts["excluded_unbound"] += 1
                continue
            observed = frame["observed"]
            if observed is None:
                counts["excluded_missing"] += 1
                continue
            counts["eligible"] += 1
            action_key = _action_key(observed["action"])
            if is_full:
                _record_selected(
                    selected_groups,
                    (role, projection.encode(frame["view"])),
                    action_key,
                    episode,
                    frame["turn"],
                    frame["view_sha256"],
                    track_witness=track_witness,
                )
            else:
                _record_full(full_groups, (role, wire.canonical(frame["view"])), action_key)
                _record_selected(
                    selected_groups,
                    (role, projection.encode(frame["view"])),
                    action_key,
                    episode,
                    frame["turn"],
                    frame["view_sha256"],
                    track_witness=track_witness,
                )

    if counts["episodes"] == 0:
        raise ValueError("train episode file contains no episodes")

    counts["cases"] = len(train_cases)
    counts["trials"] = len(train_trials)

    selected_summary = _summarize_groups(selected_groups)
    full_summary = selected_summary.copy() if is_full else _summarize_groups(full_groups)
    additional_minimum_errors = (
        selected_summary["minimum_errors"] - full_summary["minimum_errors"]
    )
    witness_list = _build_witnesses(selected_groups, witnesses)

    heldout_report: dict[str, object] | None = None
    if heldout is not None:
        selected_lookup = _unique_lookup(selected_groups)
        full_lookup = selected_lookup if is_full else _unique_lookup(full_groups)
        heldout_report = _score_heldout(
            heldout,
            projection,
            is_full=is_full,
            train_trials=train_trials,
            train_cases=train_cases,
            full_lookup=full_lookup,
            selected_lookup=selected_lookup,
        )

    return {
        "schema": AUDIT_SCHEMA,
        "fields": None if projection.fields is None else list(projection.fields),
        "fit": {
            "counts": counts,
            "full": full_summary,
            "selected": selected_summary,
            "additional_minimum_errors": additional_minimum_errors,
            "witnesses": witness_list,
        },
        "heldout": heldout_report,
    }
