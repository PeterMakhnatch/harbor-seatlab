"""View-only worker request and action envelopes for offline replay."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from hashlib import sha256
from itertools import count
from pathlib import Path
from typing import Literal, TypeAlias, TypedDict, TypeVar, cast

from . import replay as engine
from . import tape as wire

WORKER_VIEW_SCHEMA = "seatlab.worker-view/v1"
WORKER_ACTION_SCHEMA = "seatlab.worker-action/v1"
_REQUEST_ID_DOMAIN = "seatlab.worker-request-id/v1"

WorkerPolicy: TypeAlias = Literal["arrival", "revision", "intersection", "external"]
WorkerError: TypeAlias = Literal[
    "invalid_action",
    "invalid_output",
    "spawn_failed",
    "policy_failed",
    "timeout",
    "output_limit",
]


class WorkerView(TypedDict):
    schema: Literal["seatlab.worker-view/v1"]
    request_id: str
    view: dict[str, object]


class WorkerAction(TypedDict):
    schema: Literal["seatlab.worker-action/v1"]
    request_id: str
    input_sha256: str
    policy: WorkerPolicy
    action: wire.Action
    error: WorkerError | None


_VIEW_FIELDS = frozenset(WorkerView.__annotations__)
_ACTION_FIELDS = frozenset(WorkerAction.__annotations__)
_NAMESPACE = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CONTEXT_TRANSFORM = re.compile(r"context-v1:[0-9a-f]{64}")
_WORKER_POLICIES = ("arrival", "revision", "intersection", "external")
_EXTERNAL_ERRORS = (
    "invalid_output",
    "spawn_failed",
    "policy_failed",
    "timeout",
    "output_limit",
)


def _object(value: object, fields: frozenset[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() != fields:
        raise wire.TapeError(f"{name} must be an object with exactly the documented fields")
    return cast(dict[str, object], value)


def _check_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise wire.TapeError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _check_action(value: object) -> wire.Action:
    if value is None or type(value) is int:
        return value
    if type(value) is str:
        wire.canonical(value)
        return value
    raise wire.TapeError("action must be a string, an integer, or null")


def _check_namespace(value: object) -> str:
    if not isinstance(value, str) or _NAMESPACE.fullmatch(value) is None:
        raise wire.TapeError("namespace must be a 32-character lowercase hex string")
    return value


def _check_transform_id(value: object) -> str:
    if not isinstance(value, str) or (
        value not in ("identity", "latest") and _CONTEXT_TRANSFORM.fullmatch(value) is None
    ):
        raise wire.TapeError("invalid replay transform")
    return value


def make_request(
    view: dict[str, object],
    namespace: str,
    policy: str,
    transform_id: str,
    ordinal: int,
) -> WorkerView:
    """Build a minimal view-only worker request with a deterministic request_id."""
    if not isinstance(view, dict):
        raise wire.TapeError("view must be a JSON object")
    namespace = _check_namespace(namespace)
    if not isinstance(policy, str) or policy not in _WORKER_POLICIES:
        raise wire.TapeError(
            "view workers accept only arrival, revision, intersection, or external"
        )
    transform_id = _check_transform_id(transform_id)
    if type(ordinal) is not int or ordinal < 1:
        raise wire.TapeError("ordinal must be a positive integer")
    view_sha256 = wire.digest(view)
    request_id = wire.digest(
        [_REQUEST_ID_DOMAIN, namespace, policy, transform_id, ordinal, view_sha256]
    )
    return validate_request(
        {
            "schema": WORKER_VIEW_SCHEMA,
            "request_id": request_id,
            "view": view,
        }
    )


def validate_request(record: object) -> WorkerView:
    """Validate one view-only worker request envelope."""
    row = _object(record, _VIEW_FIELDS, "worker view")
    if row["schema"] != WORKER_VIEW_SCHEMA:
        raise wire.TapeError("invalid worker view schema")
    _check_sha256(row["request_id"], "request_id")
    view = row["view"]
    if not isinstance(view, dict):
        raise wire.TapeError("view must be a JSON object")
    wire.canonical(view)
    return cast(WorkerView, row)


def validate_action(record: object) -> WorkerAction:
    """Validate one view-worker action envelope."""
    row = _object(record, _ACTION_FIELDS, "worker action")
    if row["schema"] != WORKER_ACTION_SCHEMA:
        raise wire.TapeError("invalid worker action schema")
    _check_sha256(row["request_id"], "request_id")
    _check_sha256(row["input_sha256"], "input_sha256")
    policy = row["policy"]
    if not isinstance(policy, str) or policy not in _WORKER_POLICIES:
        raise wire.TapeError("invalid worker action policy")
    error = row["error"]
    if error is None:
        _check_action(row["action"])
    else:
        if row["action"] is not None:
            raise wire.TapeError("failed worker action must have null action")
        if not isinstance(error, str) or error not in ("invalid_action", *_EXTERNAL_ERRORS):
            raise wire.TapeError("worker action failure requires a safe error code")
        if error in _EXTERNAL_ERRORS and policy != "external":
            raise wire.TapeError(
                "only external policy can report an execution or output error"
            )
    return cast(WorkerAction, row)


_Row = TypeVar("_Row", WorkerView, WorkerAction)


def _read_lines(path: Path, validate: Callable[[object], _Row]) -> Iterator[_Row]:
    seen: set[str] = set()
    with path.open("rb") as source:
        for number in count(1):
            line = source.readline(wire.MAX_LINE_BYTES + 2)
            if not line:
                return
            framing = 2 if line.endswith(b"\r\n") else int(line.endswith(b"\n"))
            try:
                if len(line) - framing > wire.MAX_LINE_BYTES:
                    raise wire.TapeError("JSON line exceeds the byte limit")
                row = validate(wire.decode(line))
                request_id = row["request_id"]
                if request_id in seen:
                    raise wire.TapeError("duplicate request_id")
            except wire.TapeError as exc:
                raise wire.TapeError(f"{path}:{number}: {exc}") from exc
            seen.add(request_id)
            yield row


def read_requests(path: Path) -> Iterator[WorkerView]:
    """Read validated view-only requests with unique request_ids from UTF-8 JSONL."""
    return _read_lines(path, validate_request)


def read_actions(path: Path) -> Iterator[WorkerAction]:
    """Read validated worker actions with unique request_ids from UTF-8 JSONL."""
    return _read_lines(path, validate_action)


def act(
    requests: Path,
    out: Path,
    policy: str | list[str],
    timeout: float = 10.0,
) -> dict[str, int]:
    """Produce unscored actions for a view-only request shard without any scoring file.

    External programs require POSIX and an absolute executable path. They receive
    only the canonical actor view on stdin and run in an empty private working
    directory with a minimal PATH. This is not a sandbox. Run only programs you trust.
    """
    policy_name, argv = engine.configure_policy(policy, timeout, observed=False)
    worker_policy = cast(WorkerPolicy, policy_name)
    summary = {"requests": 0, "acted": 0, "failed": 0}

    def actions() -> Iterator[WorkerAction]:
        for request in read_requests(requests):
            data = wire.canonical(request["view"])
            action: wire.Action = None
            error: WorkerError | None = None
            try:
                action = engine.choose_view(
                    request["view"], worker_policy, argv, data, timeout
                )
            except engine._PolicyFailure as failure:
                error = cast(WorkerError, failure.reason)
            row = validate_action(
                {
                    "schema": WORKER_ACTION_SCHEMA,
                    "request_id": request["request_id"],
                    "input_sha256": sha256(data).hexdigest(),
                    "policy": worker_policy,
                    "action": action,
                    "error": error,
                }
            )
            summary["requests"] += 1
            if error is None:
                summary["acted"] += 1
            else:
                summary["failed"] += 1
            yield row

    wire.write_lines(out, actions())
    return summary
