"""Read role-local native episode records without attesting their provenance."""

from __future__ import annotations

from collections.abc import Iterator
from itertools import count
from pathlib import Path
from typing import Literal, TypedDict, cast

from . import tape as wire

EPISODE_SCHEMA = "seatlab.episode/v1"


class EpisodeFrame(TypedDict):
    turn: int
    basis: Literal["bound", "reconstructed"]
    view: dict[str, object]
    view_sha256: str
    observed: wire.Observation | None


class Episode(TypedDict):
    schema: Literal["seatlab.episode/v1"]
    id: str
    case_sha256: str
    role: str
    native_trial_id: str
    closed: bool
    frames: list[EpisodeFrame]
    episode_sha256: str


_EPISODE_FIELDS = frozenset(Episode.__annotations__)
_FRAME_FIELDS = frozenset(EpisodeFrame.__annotations__)


def _validate_frame(value: object, expected_turn: int | None = None) -> EpisodeFrame:
    label = f"frame {expected_turn}" if expected_turn is not None else "frame"
    frame = wire._object(value, _FRAME_FIELDS, label)
    turn = frame["turn"]
    if expected_turn is not None:
        if type(turn) is not int or turn != expected_turn:
            raise wire.TapeError(f"{label}: turn must be a consecutive 1-based integer")
    elif type(turn) is not int or turn < 1:
        raise wire.TapeError("frame: turn must be a positive 1-based integer")
    if frame["basis"] not in ("bound", "reconstructed"):
        raise wire.TapeError(f"{label}: invalid basis")
    view = frame["view"]
    if not isinstance(view, dict):
        raise wire.TapeError(f"{label}: view must be a JSON object")
    wire._check_sha256(frame["view_sha256"], "view_sha256")
    if frame["view_sha256"] != wire.digest(view):
        raise wire.TapeError(f"{label}: view_sha256 does not match view")
    if frame["observed"] is not None:
        observation = wire._object(frame["observed"], wire._ACTION_FIELDS, "observed")
        wire._action(observation["action"])
    return cast(EpisodeFrame, frame)


def _validate_episode_header(row: dict[str, object]) -> None:
    if row["schema"] != EPISODE_SCHEMA:
        raise wire.TapeError("invalid episode schema")
    # Apply the native identity rules without assigning provenance to the episode.
    wire._identity(
        {
            "key": row["id"],
            "role": row["role"],
            "native_trial_id": row["native_trial_id"],
            "basis": "bound",
        }
    )
    wire._check_sha256(row["case_sha256"], "case_sha256")
    if type(row["closed"]) is not bool:
        raise wire.TapeError("closed must be a boolean")


def _episode(record: object) -> Episode:
    row = wire._object(record, _EPISODE_FIELDS, "episode")
    _validate_episode_header(row)
    wire._check_sha256(row["episode_sha256"], "episode_sha256")
    frames = row["frames"]
    if not isinstance(frames, list) or not frames:
        raise wire.TapeError("frames must be a nonempty array")
    for turn, value in enumerate(frames, 1):
        _validate_frame(value, turn)
    payload = {field: value for field, value in row.items() if field != "episode_sha256"}
    if row["episode_sha256"] != wire.digest(payload):
        raise wire.TapeError("episode_sha256 does not match episode")
    return cast(Episode, row)


def read_episodes(path: Path) -> Iterator[Episode]:
    """Read validated episodes in order, with one record per native role and trial."""
    seen_ids: set[str] = set()
    seen_seats: set[tuple[str, str]] = set()
    with path.open("rb") as source:
        for number in count(1):
            line = source.readline(wire.MAX_LINE_BYTES + 2)
            if not line:
                return
            framing = 2 if line.endswith(b"\r\n") else int(line.endswith(b"\n"))
            try:
                if len(line) - framing > wire.MAX_LINE_BYTES:
                    raise wire.TapeError("JSON line exceeds the byte limit")
                episode = _episode(wire.decode(line))
                episode_id = episode["id"]
                seat = episode["native_trial_id"], episode["role"]
                if episode_id in seen_ids:
                    raise wire.TapeError("duplicate episode id")
                if seat in seen_seats:
                    raise wire.TapeError("duplicate native_trial_id and role")
            except wire.TapeError as exc:
                raise wire.TapeError(f"{path}:{number}: {exc}") from exc
            seen_ids.add(episode_id)
            seen_seats.add(seat)
            yield episode
