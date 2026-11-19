"""Validate authoritative world-step ordering for one native multi-role trial."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from . import tape as wire
from .episodes import Episode, read_episodes

__all__ = ["WORLD_ORDER_SCHEMA", "WorldOrder", "read_world_order", "validate_world_pair"]

WORLD_ORDER_SCHEMA = "seatlab.world-order/v1"
_ORDER_FIELDS = frozenset({"schema", "case_sha256", "native_trial_id", "episodes", "steps"})


@dataclass(frozen=True, slots=True)
class WorldOrder:
    """Validated episode set and authoritative world-step groups for one native trial."""

    case_sha256: str
    native_trial_id: str
    episodes: dict[str, Episode]
    steps: tuple[tuple[str, ...], ...]

    @property
    def closed(self) -> bool:
        """Return whether every participating role episode in this trial is closed."""
        return all(episode["closed"] for episode in self.episodes.values())


def validate_world_pair(source: WorldOrder, target: WorldOrder) -> None:
    """Require independent trials with the same registered case and roles."""
    if source.case_sha256 != target.case_sha256:
        raise wire.TapeError("source and target world orders have different case_sha256")
    if source.native_trial_id == target.native_trial_id:
        raise wire.TapeError("source and target cannot share the same native_trial_id")
    if source.episodes.keys() != target.episodes.keys():
        raise wire.TapeError("source and target world orders must cover the same role set")


def _canonical_uuid(value: object, field: str) -> str:
    if isinstance(value, str) and len(value) == 36:
        try:
            if str(UUID(value)) == value:
                return value
        except ValueError:
            pass
    raise wire.TapeError(f"{field} requires a canonical native_trial_id UUID")


def _role(value: object, where: str) -> str:
    if not isinstance(value, str) or wire._ROLE.fullmatch(value) is None:
        raise wire.TapeError(f"{where} role must be a short ASCII identifier")
    return value


def _parse_order(
    order: Path,
) -> tuple[str, str, dict[str, str], tuple[tuple[str, ...], ...]]:
    document = wire._object(
        wire.decode(order.read_bytes()),
        _ORDER_FIELDS,
        "world order document",
    )
    if document["schema"] != WORLD_ORDER_SCHEMA:
        raise wire.TapeError(f"invalid {WORLD_ORDER_SCHEMA} schema")
    wire._check_sha256(document["case_sha256"], "case_sha256")
    case_sha256 = cast(str, document["case_sha256"])
    native_trial_id = _canonical_uuid(document["native_trial_id"], "world order")

    raw_episodes = document["episodes"]
    if not isinstance(raw_episodes, dict) or not raw_episodes:
        raise wire.TapeError("world order episodes must be a nonempty object")
    expected_digests: dict[str, str] = {}
    for raw_role, raw_digest in cast(dict[object, object], raw_episodes).items():
        role = _role(raw_role, "world order episodes")
        wire._check_sha256(raw_digest, f"episodes[{role!r}] episode_sha256")
        expected_digests[role] = cast(str, raw_digest)

    raw_steps = document["steps"]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise wire.TapeError("world order steps must be a nonempty array")
    steps: list[tuple[str, ...]] = []
    for index, raw_group in enumerate(cast(list[object], raw_steps), 1):
        if not isinstance(raw_group, list) or not raw_group:
            raise wire.TapeError(f"world order step {index} must be a nonempty array")
        seen: set[str] = set()
        group: list[str] = []
        for raw_role in cast(list[object], raw_group):
            role = _role(raw_role, f"world order step {index}")
            if role not in expected_digests:
                raise wire.TapeError(
                    f"world order step {index} references undeclared role {role!r}"
                )
            if role in seen:
                raise wire.TapeError(f"world order step {index} repeats role {role!r}")
            seen.add(role)
            group.append(role)
        group.sort()
        steps.append(tuple(group))

    return case_sha256, native_trial_id, expected_digests, tuple(steps)


def read_world_order(episodes: Path, order: Path) -> WorldOrder:
    """Read and cross-check one native trial episode tape against its world-order file."""
    case_sha256, native_trial_id, expected_digests, steps = _parse_order(order)

    by_role: dict[str, Episode] = {}
    for episode in read_episodes(episodes):
        if episode["native_trial_id"] != native_trial_id:
            raise wire.TapeError(
                f"episode {episode['id']!r} native_trial_id does not match world order"
            )
        if episode["case_sha256"] != case_sha256:
            raise wire.TapeError(
                f"episode {episode['id']!r} case_sha256 does not match world order"
            )
        role = episode["role"]
        if role not in expected_digests:
            raise wire.TapeError(f"unexpected role {role!r} in episode tape")
        by_role[role] = episode

    if by_role.keys() != expected_digests.keys():
        missing = sorted(expected_digests.keys() - by_role.keys())
        raise wire.TapeError(f"world order missing episode for role(s): {', '.join(missing)}")

    ordered_episodes: dict[str, Episode] = {}
    for role in sorted(expected_digests):
        episode = by_role[role]
        if episode["episode_sha256"] != expected_digests[role]:
            raise wire.TapeError(f"role {role!r} episode_sha256 does not match world order")
        ordered_episodes[role] = episode

    consumed = {role: 0 for role in ordered_episodes}
    for group in steps:
        for role in group:
            consumed[role] += 1

    for role, episode in ordered_episodes.items():
        expected_frames = len(episode["frames"])
        actual_frames = consumed[role]
        if actual_frames != expected_frames:
            raise wire.TapeError(
                f"role {role!r} frame count mismatch: "
                f"world order consumes {actual_frames}, episode has {expected_frames}"
            )

    return WorldOrder(
        case_sha256=case_sha256,
        native_trial_id=native_trial_id,
        episodes=ordered_episodes,
        steps=steps,
    )
