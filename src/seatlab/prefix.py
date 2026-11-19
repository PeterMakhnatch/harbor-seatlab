"""Compare trusted policies on the observed causal prefix of each role-local episode."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

from . import tape as wire
from .episodes import Episode, read_episodes
from .replay import _external, _PolicyFailure

PREFIX_RESULT_SCHEMA = "seatlab.prefix-result/v1"


class _EpisodeResult(TypedDict):
    id: str
    case_sha256: str
    role: str
    native_trial_id: str
    episode_sha256: str
    closed: bool
    frames: int
    matched_prefix: int
    boundary_turn: int | None
    status: str
    source_action: wire.Observation | None
    candidate_action: wire.Observation | None
    error: wire.ReplayError | None
    calls: int
    withheld_suffix: int


def _run_episode(
    episode: Episode, argv: tuple[str, ...], *, history: bool, timeout: float
) -> _EpisodeResult:
    frames = episode["frames"]
    status = "matched" if argv else "observed_prefix"
    prior_steps: list[dict[str, object]] = []
    candidate_action: wire.Observation | None = None
    error: wire.ReplayError | None = None
    matched = calls = 0

    def on_spawn() -> None:
        nonlocal calls
        calls += 1

    for frame in frames:
        if frame["basis"] != "bound":
            status = "unbound"
            break
        observation = frame["observed"]
        if observation is None:
            status = "missing_observation"
            break
        if argv:
            view = frame["view"]
            data = wire.canonical({"view": view, "history": prior_steps} if history else view)
            try:
                candidate = _external(argv, data, timeout, on_spawn=on_spawn)
            except _PolicyFailure as failure:
                status = "policy_failed"
                error = failure.reason
                break
            source = observation["action"]
            if type(candidate) is not type(source) or candidate != source:
                status = "diverged"
                candidate_action = {"action": candidate}
                break
            if history:
                prior_steps.append({"view": view, "action": candidate})
        matched += 1

    boundary = frames[matched] if matched < len(frames) else None
    return {
        "id": episode["id"],
        "case_sha256": episode["case_sha256"],
        "role": episode["role"],
        "native_trial_id": episode["native_trial_id"],
        "episode_sha256": episode["episode_sha256"],
        "closed": episode["closed"],
        "frames": len(frames),
        "matched_prefix": matched,
        "boundary_turn": boundary["turn"] if boundary is not None else None,
        "status": status,
        "source_action": boundary["observed"] if boundary is not None else None,
        "candidate_action": candidate_action,
        "error": error,
        "calls": calls,
        "withheld_suffix": len(frames) - boundary["turn"] if boundary is not None else 0,
    }


def prefix(
    episodes: Path, policy: str | list[str], *, history: bool = False, timeout: float = 10.0
) -> dict[str, object]:
    """Report prefix agreement without a simulator or scores.

    External programs receive only the current view and optional accepted history.
    The transport is not a sandbox. Run only programs you trust.
    """
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 300
    ):
        raise ValueError("timeout must be positive and at most 300 seconds")
    argv: tuple[str, ...] = ()
    if isinstance(policy, str):
        if policy != "observed":
            raise ValueError("unknown prefix policy")
    else:
        if (
            not isinstance(policy, list)
            or not policy
            or any(not isinstance(arg, str) or "\0" in arg for arg in policy)
            or not os.path.isabs(policy[0])
        ):
            raise ValueError("external policy requires an absolute executable path")
        if os.name != "posix":
            raise ValueError("external prefix requires a POSIX host")
        argv = tuple(policy)

    # Complete the preflight and close the file before the first policy can run.
    verified = list(read_episodes(episodes))
    counts = dict.fromkeys(
        (
            "episodes",
            "frames",
            "matched_prefix",
            "calls",
            "withheld_suffix",
            "matched",
            "observed_prefix",
            "diverged",
            "missing_observation",
            "unbound",
            "policy_failed",
            "matched_open",
            "matched_closed",
        ),
        0,
    )
    counts["episodes"] = len(verified)
    results = []
    for episode in verified:
        result = _run_episode(episode, argv, history=history, timeout=timeout)
        results.append(result)
        counts[result["status"]] += 1
        counts["frames"] += result["frames"]
        counts["matched_prefix"] += result["matched_prefix"]
        counts["calls"] += result["calls"]
        counts["withheld_suffix"] += result["withheld_suffix"]
        if result["boundary_turn"] is None:
            counts["matched_closed" if result["closed"] else "matched_open"] += 1
    return {
        "schema": PREFIX_RESULT_SCHEMA,
        "policy": "external" if argv else "observed",
        "interpretation": "recorded_prefix_agreement" if argv else "native_self_consistency",
        "history": history,
        "counts": counts,
        "episodes": results,
    }
