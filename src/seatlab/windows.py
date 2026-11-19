"""Build and verify the private window submitted to an external actor.

``actor_window`` is the canonical policy input: the current raw view and at most
``depth`` earlier ``{view, action}`` records, oldest first. Depth 0 does not read
or copy history. ``verify_windows`` joins wrapper receipts to source episodes.
The episode proves which view and action the world accepted. The receipt proves
which window bytes the wrapper submitted. An optional output records that window
under the source native identity; it does not mint a trial.

A receipt row is exactly ``schema``, ``native_trial_id``, ``role``, ``depth`` and
``frames``. A receipt frame is exactly ``turn``, ``view_sha256``, ``action_sha256``,
``context_sha256`` and ``context_bytes``. ``action_sha256`` hashes the canonical
``{"action": value}`` object. ``context_sha256`` hashes the canonical
``seatlab.actor-window/v1`` envelope, and ``context_bytes`` counts those bytes
without the framing newline.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import count
from pathlib import Path
from typing import TypedDict, cast

from . import tape as wire
from .author import accepted_frame, episode_from_frames
from .episodes import Episode, EpisodeFrame, read_episodes

ACTOR_WINDOW_SCHEMA = "seatlab.actor-window/v1"
RECEIPT_SCHEMA = "seatlab.window-receipt/v1"
VERIFICATION_SCHEMA = "seatlab.window-verification/v1"
MAX_WINDOW_DEPTH = 16

_HISTORY_FIELDS = frozenset({"view", "action"})
_WINDOW_FIELDS = frozenset({"schema", "current", "history"})
_RECEIPT_FIELDS = frozenset({"schema", "native_trial_id", "role", "depth", "frames"})
_RECEIPT_FRAME_FIELDS = frozenset(
    {"turn", "view_sha256", "action_sha256", "context_sha256", "context_bytes"}
)

__all__ = [
    "ACTOR_WINDOW_SCHEMA",
    "MAX_WINDOW_DEPTH",
    "RECEIPT_SCHEMA",
    "VERIFICATION_SCHEMA",
    "actor_window",
    "verify_windows",
]


class _ReceiptFrame(TypedDict):
    turn: int
    view_sha256: str
    action_sha256: str
    context_sha256: str
    context_bytes: int


class _Receipt(TypedDict):
    schema: str
    native_trial_id: str
    role: str
    depth: int
    frames: list[_ReceiptFrame]


class _Counts(TypedDict):
    native_trials: int
    role_episodes: int
    verified_decisions: int
    context_bytes: int


class _Seat(TypedDict):
    native_trial_id: str
    role: str
    depth: int
    decisions: int
    bytes: int


def _depth(value: object) -> int:
    if type(value) is not int or value < 0 or value > MAX_WINDOW_DEPTH:
        raise wire.TapeError("depth must be an integer from 0 to 16")
    return value


def _raw_view(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise wire.TapeError(f"{label} must be a JSON object")
    if value.get("schema") == ACTOR_WINDOW_SCHEMA:
        raise wire.TapeError("policy envelope must not nest a previous window")
    return cast(dict[str, object], wire.decode(wire.canonical(value)))


def _history_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() != _HISTORY_FIELDS:
        raise wire.TapeError("history item must contain exactly view and action")
    view = _raw_view(value["view"], "history view")
    return {"view": view, "action": wire._action(value["action"])}


def actor_window(
    current: dict[str, object],
    history: Sequence[object],
    depth: int,
) -> dict[str, object]:
    """Return the policy input for one decision.

    ``history`` is chronological, oldest first. Only the last ``depth`` records are
    copied. A boolean depth is rejected. The result does not alias ``current`` or
    the selected history objects.
    """
    window_depth = _depth(depth)
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise wire.TapeError("history must be a sequence of view-action records")
    current_view = _raw_view(current, "current")
    if window_depth == 0:
        return {"schema": ACTOR_WINDOW_SCHEMA, "current": current_view, "history": []}
    total = len(history)
    start = total - window_depth if total > window_depth else 0
    selected = [_history_record(history[index]) for index in range(start, total)]
    return {"schema": ACTOR_WINDOW_SCHEMA, "current": current_view, "history": selected}


def _receipt_frame(value: object, expected_turn: int) -> _ReceiptFrame:
    label = f"receipt frame {expected_turn}"
    frame = wire._object(value, _RECEIPT_FRAME_FIELDS, label)
    turn = frame["turn"]
    if type(turn) is not int or turn != expected_turn:
        raise wire.TapeError(f"{label}: turn must be a consecutive 1-based integer")
    wire._check_sha256(frame["view_sha256"], "view_sha256")
    wire._check_sha256(frame["action_sha256"], "action_sha256")
    wire._check_sha256(frame["context_sha256"], "context_sha256")
    nbytes = frame["context_bytes"]
    if type(nbytes) is not int or nbytes < 0:
        raise wire.TapeError("context_bytes must be a non-negative integer")
    if nbytes > wire.MAX_LINE_BYTES:
        raise wire.TapeError("context_bytes exceeds the byte limit")
    return cast(_ReceiptFrame, frame)


def _receipt(value: object) -> _Receipt:
    row = wire._object(value, _RECEIPT_FIELDS, "window receipt")
    if row["schema"] != RECEIPT_SCHEMA:
        raise wire.TapeError("invalid window receipt schema")
    wire._identity(
        {
            "key": "window",
            "role": row["role"],
            "native_trial_id": row["native_trial_id"],
            "basis": "bound",
        }
    )
    row["depth"] = _depth(row["depth"])
    frames = row["frames"]
    if not isinstance(frames, list) or not frames:
        raise wire.TapeError("frames must be a nonempty array")
    row["frames"] = [_receipt_frame(frame, turn) for turn, frame in enumerate(frames, 1)]
    return cast(_Receipt, row)


def _read_receipts(path: Path) -> list[_Receipt]:
    rows: list[_Receipt] = []
    seen: set[tuple[str, str]] = set()
    with path.open("rb") as source:
        for number in count(1):
            line = source.readline(wire.MAX_LINE_BYTES + 2)
            if not line:
                break
            framing = 2 if line.endswith(b"\r\n") else int(line.endswith(b"\n"))
            try:
                if len(line) - framing > wire.MAX_LINE_BYTES:
                    raise wire.TapeError("JSON line exceeds the byte limit")
                receipt = _receipt(wire.decode(line))
                seat = (receipt["native_trial_id"], receipt["role"])
                if seat in seen:
                    raise wire.TapeError("duplicate window receipt")
            except wire.TapeError as exc:
                raise wire.TapeError(f"{path}:{number}: {exc}") from exc
            seen.add(seat)
            rows.append(receipt)
    return rows


def _require_seats(source: list[tuple[str, str]], receipts: list[tuple[str, str]]) -> None:
    source_set = set(source)
    receipt_set = set(receipts)
    if source_set == receipt_set:
        return
    missing = [seat for seat in source if seat not in receipt_set]
    extra = [seat for seat in receipts if seat not in source_set]
    for trial, role in missing:
        for other_trial, other_role in extra:
            if trial == other_trial and role != other_role:
                raise wire.TapeError("window receipt role does not match")
            if role == other_role and trial != other_trial:
                raise wire.TapeError("window receipt crosses native trials")
    if missing:
        raise wire.TapeError("missing window receipt")
    raise wire.TapeError("unexpected window receipt")


def _accepted_action(frame: EpisodeFrame) -> wire.Action:
    if frame["basis"] != "bound":
        raise wire.TapeError("frame is not bound")
    observed = frame["observed"]
    if observed is None:
        raise wire.TapeError("observed action is missing")
    return observed["action"]


def _submitted_window(
    frames: Sequence[EpisodeFrame],
    index: int,
    depth: int,
) -> dict[str, object]:
    """Rebuild the wrapper input from earlier accepted frames only."""
    if depth == 0:
        return actor_window(frames[index]["view"], (), depth)
    start = index - depth if index > depth else 0
    history = [
        {"view": frames[offset]["view"], "action": _accepted_action(frames[offset])}
        for offset in range(start, index)
    ]
    return actor_window(frames[index]["view"], history, depth)


def _assert_policy_window(envelope: dict[str, object], index: int, depth: int) -> None:
    history = envelope.get("history")
    if envelope.get("schema") != ACTOR_WINDOW_SCHEMA or set(envelope) != _WINDOW_FIELDS:
        raise wire.TapeError("policy input contains an identity field")
    if not isinstance(history, list) or len(history) > depth or len(history) > index:
        raise wire.TapeError("policy window includes a future frame")
    for item in history:
        if not isinstance(item, dict) or set(item) != _HISTORY_FIELDS:
            raise wire.TapeError("policy history nests a context frame")
        view = item["view"]
        if isinstance(view, dict) and view.get("schema") == ACTOR_WINDOW_SCHEMA:
            raise wire.TapeError("policy history nests a context frame")


def _context_bytes(
    receipt_frame: _ReceiptFrame,
    source: EpisodeFrame,
    envelope: dict[str, object],
) -> int:
    if receipt_frame["view_sha256"] != source["view_sha256"]:
        raise wire.TapeError("view digest does not match")
    action = _accepted_action(source)
    if receipt_frame["action_sha256"] != wire.digest({"action": action}):
        raise wire.TapeError("action digest does not match")
    if receipt_frame["context_sha256"] != wire.digest(envelope):
        raise wire.TapeError("context digest does not match")
    nbytes = len(wire.canonical(envelope))
    if receipt_frame["context_bytes"] != nbytes:
        raise wire.TapeError("context bytes do not match")
    return nbytes


def _policy_episode(episode: Episode, frames: list[EpisodeFrame]) -> Episode:
    return episode_from_frames(
        id=episode["id"],
        case_sha256=episode["case_sha256"],
        role=episode["role"],
        native_trial_id=episode["native_trial_id"],
        closed=episode["closed"],
        frames=frames,
    )


def verify_windows(
    episodes: Path,
    receipts: Path,
    out: Path | None = None,
) -> dict[str, object]:
    """Join one receipt to each native role episode and optionally publish policy inputs.

    Publication uses ``seatlab.tape.write_lines`` and does not replace an existing path.
    Mismatch errors do not include view or action values.
    """
    source = list(read_episodes(episodes))
    rows = _read_receipts(receipts)
    by_seat = {(row["native_trial_id"], row["role"]): row for row in rows}
    _require_seats(
        [(episode["native_trial_id"], episode["role"]) for episode in source],
        [(row["native_trial_id"], row["role"]) for row in rows],
    )

    seats: list[_Seat] = []
    published: list[Episode] = []
    total_decisions = 0
    total_bytes = 0
    for episode in source:
        receipt = by_seat[(episode["native_trial_id"], episode["role"])]
        source_frames = episode["frames"]
        receipt_frames = receipt["frames"]
        if len(receipt_frames) != len(source_frames):
            raise wire.TapeError("receipt length does not match episode")
        depth = receipt["depth"]
        policy_frames: list[EpisodeFrame] = []
        seat_bytes = 0
        for index, source_frame in enumerate(source_frames):
            receipt_frame = receipt_frames[index]
            if receipt_frame["turn"] != source_frame["turn"]:
                raise wire.TapeError("receipt turn does not match source")
            _accepted_action(source_frame)
            envelope = _submitted_window(source_frames, index, depth)
            _assert_policy_window(envelope, index, depth)
            seat_bytes += _context_bytes(receipt_frame, source_frame, envelope)
            if out is not None:
                policy_frames.append(
                    accepted_frame(
                        turn=source_frame["turn"],
                        view=envelope,
                        acknowledged_sha256=receipt_frame["context_sha256"],
                        action=_accepted_action(source_frame),
                    )
                )
        decisions = len(source_frames)
        total_decisions += decisions
        total_bytes += seat_bytes
        seats.append(
            {
                "native_trial_id": episode["native_trial_id"],
                "role": episode["role"],
                "depth": depth,
                "decisions": decisions,
                "bytes": seat_bytes,
            }
        )
        if out is not None:
            published.append(_policy_episode(episode, policy_frames))

    if out is not None:
        wire.write_lines(out, published)
    counts: _Counts = {
        "native_trials": len({episode["native_trial_id"] for episode in source}),
        "role_episodes": len(source),
        "verified_decisions": total_decisions,
        "context_bytes": total_bytes,
    }
    return {
        "schema": VERIFICATION_SCHEMA,
        "status": "verified",
        "counts": counts,
        "seats": seats,
    }
