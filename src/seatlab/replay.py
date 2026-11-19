"""Replay independent role-local views through offline policies."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Iterator
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from time import monotonic
from typing import cast

from . import tape as wire
from .context import ContextSpec

_BUILTINS = {"observed", "arrival", "revision", "intersection"}
_BAYS = {"A", "B", "C", "D"}
_OUTPUT_LIMIT = 64 * 1024
_Report = tuple[int, list[str]]


class _PolicyFailure(Exception):
    """An unmeasured result with a fixed, public reason."""

    def __init__(self, reason: wire.ReplayError) -> None:
        super().__init__(reason)
        self.reason = reason


def _available(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(bay, str) or bay not in _BAYS for bay in value)
        or len(value) != len(set(value))
    ):
        raise ValueError("invalid handoff availability")
    return cast(list[str], value)


def _handoff(
    view: dict[str, object],
) -> tuple[dict[str, int], list[_Report], list[str] | None]:
    if view.keys() != {"utilities", "messages", "pooled_available"}:
        raise ValueError("expected a handoff view")
    utilities, messages, pooled = (
        view["utilities"],
        view["messages"],
        view["pooled_available"],
    )
    if (
        not isinstance(utilities, dict)
        or utilities.keys() != _BAYS
        or any(type(rank) is not int for rank in utilities.values())
        or set(utilities.values()) != {1, 2, 3, 4}
    ):
        raise ValueError("invalid handoff utilities")
    if not isinstance(messages, list):
        raise ValueError("invalid handoff messages")
    reports: list[_Report] = []
    for item in cast(list[object], messages):
        if not isinstance(item, dict) or item.keys() != {"revision", "available"}:
            raise ValueError("invalid handoff message")
        report = cast(dict[str, object], item)
        revision = report["revision"]
        if type(revision) is not int or revision < 1:
            raise ValueError("invalid handoff revision")
        reports.append((revision, _available(report["available"])))
    allowed = None if pooled is None else _available(pooled)
    return cast(dict[str, int], utilities), reports, allowed


def _latest(reports: list[_Report]) -> _Report | None:
    if len({revision for revision, _ in reports}) != len(reports):
        raise ValueError("ambiguous handoff revision")
    return max(reports, key=lambda report: report[0], default=None)


def _transform(view: dict[str, object], name: str) -> dict[str, object]:
    if name == "identity":
        return view
    _, reports, _ = _handoff(view)
    report = _latest(reports)
    if report is None:
        return view
    revision, available = report
    return {**view, "messages": [{"revision": revision, "available": available}]}


def _handoff_action(view: dict[str, object], policy: str) -> str | None:
    utilities, reports, pooled = _handoff(view)
    report = _latest(reports) if policy == "revision" else (reports[-1] if reports else None)
    eligible: Iterable[str]
    if pooled is not None:
        eligible = pooled
    elif report is not None:
        eligible = report[1]
    else:
        eligible = utilities
    return max(eligible, key=utilities.__getitem__, default=None)


def _intersection(view: dict[str, object]) -> int | None:
    reports = view.get("reports")
    if not isinstance(reports, dict):
        raise ValueError("expected an intersection view")
    proposal = view.keys() != {"reports"}
    if proposal and (
        view.keys() != {"role", "mode", "reports"}
        or view["role"] != "m1"
        or view["mode"] not in ("full", "first8")
        or reports.keys() != {"m1", "m2", "m3", "m4", "m5"}
    ):
        raise ValueError("invalid calendar proposal view")
    common: set[int] | None = None
    for seat, values in cast(dict[object, object], reports).items():
        if (
            not isinstance(seat, str)
            or not seat
            or not isinstance(values, list)
            or any(type(slot) is not int for slot in values)
        ):
            raise ValueError("invalid intersection report")
        slots = cast(list[int], values)
        if (proposal and slots and slots[0] < 0) or any(
            left > right or (proposal and left == right) for left, right in pairwise(slots)
        ):
            raise ValueError("invalid intersection report")
        if common is None:
            common = set(slots)
        else:
            common.intersection_update(slots)
    return min(common, default=None) if common is not None else None


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _PolicyFailure("timeout")
    return remaining


def _exchange(process: subprocess.Popen[bytes], data: bytes, deadline: float) -> bytearray:
    assert process.stdin is not None and process.stdout is not None
    os.set_blocking(process.stdin.fileno(), False)
    os.set_blocking(process.stdout.fileno(), False)
    pending = memoryview(data)
    output = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdin, selectors.EVENT_WRITE)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            events = selector.select(_remaining(deadline))
            for key, event in events:
                try:
                    if event == selectors.EVENT_WRITE:
                        pending = pending[os.write(key.fd, pending) :]
                        if not pending:
                            selector.unregister(key.fd)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, _OUTPUT_LIMIT + 1 - len(output))
                        if not chunk:
                            selector.unregister(key.fd)
                        output.extend(chunk)
                        if len(output) > _OUTPUT_LIMIT:
                            raise _PolicyFailure("output_limit")
                except BlockingIOError:
                    continue
    try:
        status = process.wait(timeout=_remaining(deadline))
    except subprocess.TimeoutExpired:
        raise _PolicyFailure("timeout") from None
    if status != 0:
        raise _PolicyFailure("policy_failed")
    return output


def _close_process(process: subprocess.Popen[bytes]) -> None:
    # Do not signal a group after reaping the leader: the OS can reuse its PID.
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    for pipe in (process.stdin, process.stdout):
        if pipe is not None:
            pipe.close()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _external(
    argv: tuple[str, ...],
    data: bytes,
    timeout: float,
    *,
    on_spawn: Callable[[], None] | None = None,
) -> wire.Action:
    try:
        with tempfile.TemporaryDirectory(prefix="seatlab-") as cwd:
            deadline = monotonic() + timeout
            try:
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=cwd,
                    env={"PATH": "/usr/bin:/bin"},
                    start_new_session=True,
                    bufsize=0,
                )
            except (OSError, ValueError, subprocess.SubprocessError):
                raise _PolicyFailure("spawn_failed") from None
            try:
                if on_spawn is not None:
                    on_spawn()
                output = _exchange(process, data + b"\n", deadline)
            except OSError:
                raise _PolicyFailure("policy_failed") from None
            finally:
                _close_process(process)
    except OSError:
        raise _PolicyFailure("policy_failed") from None
    try:
        value = wire.decode(bytes(output))
    except (ValueError, RecursionError):
        raise _PolicyFailure("invalid_output") from None
    if not isinstance(value, dict) or value.keys() != {"action"}:
        raise _PolicyFailure("invalid_output")
    action = cast(dict[str, object], value)["action"]
    if action is None or type(action) is str or type(action) is int:
        return action
    raise _PolicyFailure("invalid_action")


def configure_policy(
    policy: str | list[str], timeout: float, *, observed: bool = True
) -> tuple[wire.Policy, tuple[str, ...]]:
    """Validate timeout and policy selection, returning the policy name and exec argv."""
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 300
    ):
        raise ValueError("timeout must be positive and at most 300 seconds")
    if isinstance(policy, str):
        if policy == "observed" and not observed:
            raise ValueError("observed policy requires a source decision")
        if policy not in _BUILTINS:
            raise ValueError("unknown replay policy")
        return cast(wire.Policy, policy), ()
    if (
        not isinstance(policy, list)
        or not policy
        or any(not isinstance(arg, str) or "\0" in arg for arg in policy)
        or not os.path.isabs(policy[0])
    ):
        raise ValueError("external policy requires an absolute executable path")
    if os.name != "posix":
        raise ValueError("external replay requires a POSIX host")
    return "external", tuple(policy)


def choose_view(
    view: dict[str, object],
    name: wire.Policy,
    argv: tuple[str, ...],
    data: bytes,
    timeout: float,
) -> wire.Action:
    """Choose an action from a role-local view alone; observed is not allowed here."""
    if name == "observed":
        raise ValueError("observed policy requires a source decision")
    if name == "intersection":
        return _intersection(view)
    if name == "external":
        return _external(argv, data, timeout)
    if name in ("arrival", "revision"):
        return _handoff_action(view, name)
    raise ValueError("unknown replay policy")


def _choose(
    row: wire.Decision,
    view: dict[str, object],
    policy: wire.Policy,
    argv: tuple[str, ...],
    data: bytes,
    timeout: float,
) -> wire.Action:
    if policy == "observed":
        if row["observed"] is None:
            raise _PolicyFailure("missing_observation")
        return row["observed"]["action"]
    return choose_view(view, policy, argv, data, timeout)


def scored_action(
    source: wire.Decision,
    name: wire.Policy,
    transform_id: str,
    data: bytes,
    action: wire.Action,
    error: wire.ReplayError | None,
) -> wire.Result:
    """Score one candidate action or failure against a validated source decision."""
    result_action: wire.Action = None
    result_score: wire.Score | None = None
    result_error: wire.ReplayError | None = error
    if result_error is None:
        try:
            result_score = wire.score(source, action)
        except wire.UnknownActionError:
            result_error = "invalid_action"
        else:
            result_action = action
    elif action is not None:
        raise ValueError("failed action cannot carry a non-null action")
    status = "measured" if result_error is None else "unmeasured"
    basis = source["basis"]
    if basis == "bound" and transform_id != "identity":
        basis = "reconstructed"
    return wire.validate_result(
        {
            "schema": wire.RESULT_SCHEMA,
            "key": source["key"],
            "role": source["role"],
            "native_trial_id": source["native_trial_id"],
            "basis": basis,
            "source_view_sha256": source["view_sha256"],
            "source_decision_sha256": wire.decision_sha256(source),
            "input_sha256": sha256(data).hexdigest(),
            "input_bytes": len(data),
            "policy": name,
            "transform": transform_id,
            "status": status,
            "action": result_action,
            "score": result_score,
            "error": result_error,
        }
    )


def replay(
    tape: Path,
    out: Path,
    policy: str | list[str],
    transform: str | ContextSpec = "identity",
    timeout: float = 10.0,
) -> dict[str, int]:
    """Publish a scored ledger, with one result for each valid tape decision.

    External programs require POSIX and an absolute executable path. They receive
    only the actor view on stdin and run in an empty private working directory
    with a minimal PATH. This is not a sandbox. Run only programs you trust.
    """
    if isinstance(transform, ContextSpec):
        context = transform
        transform_id = transform.identifier
    elif transform in ("identity", "latest"):
        context = None
        transform_id = transform
    else:
        raise ValueError("unknown replay transform")
    name, argv = configure_policy(policy, timeout)
    if name == "observed" and transform_id != "identity":
        raise ValueError("observed requires the identity transform")

    summary = {"decisions": 0, "measured": 0, "unmeasured": 0}

    def results() -> Iterator[wire.Result]:
        for row in wire.read_tape(tape):
            if context is None:
                view = _transform(row["view"], transform_id)
            else:
                try:
                    view = context.apply(row["view"])
                except ValueError as exc:
                    raise ValueError(f"context transform for {row['key']}: {exc}") from exc
            data = wire.canonical(view)
            action: wire.Action = None
            error: wire.ReplayError | None = None
            try:
                action = _choose(row, view, name, argv, data, timeout)
            except _PolicyFailure as failure:
                error = failure.reason
            result = scored_action(row, name, transform_id, data, action, error)
            summary["decisions"] += 1
            summary[result["status"]] += 1
            yield result

    wire.write_lines(out, results())
    return summary
