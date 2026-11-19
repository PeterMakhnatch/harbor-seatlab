"""Build validated bound frames, decisions, and role-local episodes from trusted adapters.

A trusted task adapter supplies the role, native trial UUID, case digest, and world-accepted
actor acknowledgment digest. Matching SHA-256 digests check internal consistency against
canonical JSON bytes; they do not authenticate the adapter, actor, or native trial origin.

Use ``seatlab.tape.canonical``, ``seatlab.tape.digest``, and ``seatlab.tape.write_lines`` for
canonical encoding, hashing, and atomic JSON Lines publication.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import cast

from . import episodes as episode_wire
from . import tape as wire
from .episodes import EPISODE_SCHEMA, Episode, EpisodeFrame

__all__ = ["accepted_frame", "decision_from_frame", "episode_from_frames"]


def _clone_json_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise wire.TapeError(f"{label} must be a JSON object")
    return cast(dict[str, object], wire.decode(wire.canonical(value)))


def _clone_answers(
    answers: Sequence[wire.Answer] | wire.IntRangeAnswers,
) -> list[wire.Answer] | wire.IntRangeAnswers:
    if isinstance(answers, dict):
        return cast(wire.IntRangeAnswers, wire.decode(wire.canonical(answers)))
    if isinstance(answers, (str, bytes)) or not isinstance(answers, Sequence):
        raise wire.TapeError("answers must be an array or an int-range object")
    return cast(list[wire.Answer], wire.decode(wire.canonical(list(answers))))


def accepted_frame(
    *,
    turn: int,
    view: dict[str, object],
    acknowledged_sha256: str,
    action: wire.Action,
) -> EpisodeFrame:
    """Build one validated ``bound`` frame from a world-accepted actor receipt.

    ``acknowledged_sha256`` must be the actual SHA-256 digest returned by the actor and
    accepted by the world for ``view``, not a digest recomputed from a missing or
    reconstructed view. Passing ``action=None`` records an explicit accepted null action
    (``observed={"action": None}``). Do not use this helper when no action was accepted or
    when the view was reconstructed; emit ``observed=None`` or ``basis="reconstructed"``
    directly instead.

    A matching digest checks canonical byte equality for the complete ``view`` object; it
    does not prove who produced the view or receipt.
    """
    if type(turn) is not int or turn < 1:
        raise wire.TapeError("frame: turn must be a positive 1-based integer")
    view_copy = _clone_json_object(view, "view")
    wire._check_sha256(acknowledged_sha256, "acknowledged_sha256")
    action_value = wire._action(action)
    actual_sha256 = wire.digest(view_copy)
    if acknowledged_sha256 != actual_sha256:
        raise wire.TapeError("acknowledged_sha256 does not match canonical view")
    frame: EpisodeFrame = {
        "turn": turn,
        "basis": "bound",
        "view": view_copy,
        "view_sha256": actual_sha256,
        "observed": {"action": action_value},
    }
    return episode_wire._validate_frame(frame)


def decision_from_frame(
    *,
    key: str,
    role: str,
    native_trial_id: str,
    frame: EpisodeFrame,
    answers: Sequence[wire.Answer] | wire.IntRangeAnswers,
) -> wire.Decision:
    """Build one validated ``seatlab.decision/v1`` record from an episode frame.

    The trusted adapter supplies ``key``, ``role``, ``native_trial_id``, and offline
    ``answers``. The input ``frame`` is re-validated before constructing the decision, and
    the returned record owns independent copies of ``view``, ``observed``, and ``answers`` so
    later caller mutations cannot desynchronize ``view_sha256`` or stored answers.
    """
    validated = episode_wire._validate_frame(frame)
    observed = validated["observed"]
    record: dict[str, object] = {
        "schema": wire.DECISION_SCHEMA,
        "key": key,
        "role": role,
        "native_trial_id": native_trial_id,
        "basis": validated["basis"],
        "view": _clone_json_object(validated["view"], "view"),
        "view_sha256": validated["view_sha256"],
        "observed": None if observed is None else {"action": observed["action"]},
        "answers": _clone_answers(answers),
    }
    return wire.decision(record)


def episode_from_frames(
    *,
    id: str,
    case_sha256: str,
    role: str,
    native_trial_id: str,
    closed: bool,
    frames: Iterable[EpisodeFrame],
) -> Episode:
    """Seal an ordered sequence of validated frames into a ``seatlab.episode/v1`` record.

    The trusted adapter supplies ``id``, ``case_sha256``, ``role``, ``native_trial_id``, and
    ``closed``. Every frame is re-validated in native order (consecutive 1-based turns) and
    copied so subsequent mutations to caller inputs cannot invalidate ``view_sha256`` or
    ``episode_sha256``.
    """
    if isinstance(frames, (str, bytes, dict)) or not isinstance(frames, Iterable):
        raise wire.TapeError("frames must be a nonempty iterable of episode frames")
    copied_frames: list[EpisodeFrame] = []
    for turn, raw_frame in enumerate(frames, 1):
        validated = episode_wire._validate_frame(raw_frame, turn)
        observed = validated["observed"]
        copied_frames.append(
            {
                "turn": validated["turn"],
                "basis": validated["basis"],
                "view": _clone_json_object(validated["view"], "view"),
                "view_sha256": validated["view_sha256"],
                "observed": None if observed is None else {"action": observed["action"]},
            }
        )
    if not copied_frames:
        raise wire.TapeError("frames must be a nonempty array")
    payload: dict[str, object] = {
        "schema": EPISODE_SCHEMA,
        "id": id,
        "case_sha256": case_sha256,
        "role": role,
        "native_trial_id": native_trial_id,
        "closed": closed,
        "frames": copied_frames,
    }
    episode_wire._validate_episode_header(payload)
    record = {**payload, "episode_sha256": wire.digest(payload)}
    return episode_wire._episode(record)
