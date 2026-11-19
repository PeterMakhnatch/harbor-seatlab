"""Replay independent role-local views through offline policies."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import tempfile
from collections.abc import Iterable, Iterator
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from time import monotonic
from typing import cast

from . import tape as wire

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


def _external(argv: tuple[str, ...], data: bytes, timeout: float) -> wire.Action:
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


def _choose(
    row: wire.Decision,
    view: dict[str, object],
    policy: str,
    argv: tuple[str, ...],
    data: bytes,
    timeout: float,
) -> wire.Action:
    if policy == "observed":
        if row["observed"] is None:
            raise _PolicyFailure("missing_observation")
        return row["observed"]["action"]
    if policy == "intersection":
        return _intersection(view)
    if policy == "external":
        return _external(argv, data, timeout)
    return _handoff_action(view, policy)


def replay(
    tape: Path,
    out: Path,
    policy: str | list[str],
    transform: str = "identity",
    timeout: float = 10.0,
) -> dict[str, int]:
    """Publish a scored ledger, with one result for each valid tape decision.

    External programs require POSIX and an absolute executable path. They receive
    only the actor view on stdin and run in an empty private working directory
    with a minimal PATH. This is not a sandbox. Run only programs you trust.
    """
    if transform not in ("identity", "latest"):
        raise ValueError("unknown replay transform")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 300
    ):
        raise ValueError("timeout must be positive and at most 300 seconds")
    argv: tuple[str, ...] = ()
    if isinstance(policy, str):
        if policy not in _BUILTINS:
            raise ValueError("unknown replay policy")
        if policy == "observed" and transform != "identity":
            raise ValueError("observed requires the identity transform")
        name = policy
    else:
        if (
            not isinstance(policy, list)
            or not policy
            or any(not isinstance(arg, str) or "\0" in arg for arg in policy)
            or not os.path.isabs(policy[0])
        ):
            raise ValueError("external policy requires an absolute executable path")
        if os.name != "posix":
            raise ValueError("external replay requires a POSIX host")
        name = "external"
        argv = tuple(policy)

    summary = {"decisions": 0, "measured": 0, "unmeasured": 0}

    def results() -> Iterator[wire.Result]:
        for row in wire.read_tape(tape):
            source_decision_sha256 = wire.decision_sha256(row)
            view = _transform(row["view"], transform)
            data = wire.canonical(view)
            action: wire.Action = None
            result_score = None
            error = None
            try:
                chosen = _choose(row, view, name, argv, data, timeout)
                try:
                    result_score = wire.score(row, chosen)
                except wire.UnknownActionError:
                    raise _PolicyFailure("invalid_action") from None
                action = chosen
            except _PolicyFailure as failure:
                error = failure.reason
            status = "measured" if error is None else "unmeasured"
            basis = row["basis"]
            if basis == "bound" and transform != "identity":
                basis = "reconstructed"
            result = wire.validate_result(
                {
                    "schema": wire.RESULT_SCHEMA,
                    "key": row["key"],
                    "role": row["role"],
                    "native_trial_id": row["native_trial_id"],
                    "basis": basis,
                    "source_view_sha256": row["view_sha256"],
                    "source_decision_sha256": source_decision_sha256,
                    "input_sha256": sha256(data).hexdigest(),
                    "input_bytes": len(data),
                    "policy": name,
                    "transform": transform,
                    "status": status,
                    "action": action,
                    "score": result_score,
                    "error": error,
                },
            )
            summary["decisions"] += 1
            summary[status] += 1
            yield result

    wire.write_lines(out, results())
    return summary
