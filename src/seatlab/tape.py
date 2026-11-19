"""Validate, score, and publish portable JSON Lines decision ledgers."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import tempfile
from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator
from itertools import count
from pathlib import Path
from typing import Literal, TypeAlias, TypedDict, TypeVar, cast
from uuid import UUID

DECISION_SCHEMA = "seatlab.decision/v1"
RESULT_SCHEMA = "seatlab.decision-result/v1"
MAX_LINE_BYTES = 4 * 1024 * 1024

Action: TypeAlias = str | int | None
Basis: TypeAlias = Literal["reconstructed", "bound", "design"]
Policy: TypeAlias = Literal["observed", "arrival", "revision", "intersection", "external"]
Transform: TypeAlias = Literal["identity", "latest"]
ReplayError: TypeAlias = Literal[
    "missing_observation",
    "invalid_action",
    "invalid_output",
    "spawn_failed",
    "policy_failed",
    "timeout",
    "output_limit",
]


class _Identity(TypedDict):
    key: str
    role: str
    native_trial_id: str | None
    basis: Basis


class Observation(TypedDict):
    action: Action


class Score(TypedDict):
    optimal: bool
    feasible: bool


class Answer(Score, Observation):
    pass


class IntRangeAnswers(TypedDict):
    kind: Literal["int-range"]
    min: int
    max: int
    include_null: bool
    optimal: list[int]
    feasible: list[int]


class Decision(_Identity):
    schema: Literal["seatlab.decision/v1"]
    view: dict[str, object]
    view_sha256: str
    observed: Observation | None
    answers: list[Answer] | IntRangeAnswers


class Result(_Identity):
    schema: Literal["seatlab.decision-result/v1"]
    source_decision_sha256: str
    source_view_sha256: str
    input_sha256: str
    input_bytes: int
    policy: Policy
    transform: Transform
    status: Literal["measured", "unmeasured"]
    action: Action
    score: Score | None
    error: ReplayError | None


class TapeError(ValueError):
    """A record violates the JSON or ledger contract."""


class UnknownActionError(TapeError):
    """A well-typed action is absent from the answer data."""


_DECISION_FIELDS = frozenset(Decision.__annotations__)
_RESULT_FIELDS = frozenset(Result.__annotations__)
_SCORE_FIELDS = frozenset(Score.__annotations__)
_ACTION_FIELDS = frozenset(Observation.__annotations__)
_ANSWER_FIELDS = _SCORE_FIELDS | _ACTION_FIELDS
_RANGE_FIELDS = frozenset(IntRangeAnswers.__annotations__)
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")
_ROLE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_EXTERNAL_ERRORS = (
    "invalid_output",
    "spawn_failed",
    "policy_failed",
    "timeout",
    "output_limit",
)


def _check_text(value: str) -> None:
    if _SURROGATE.search(value):
        raise TapeError("JSON string contains an unpaired surrogate")


def _check_json(value: object) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        _check_text(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise TapeError("JSON numbers must be finite")
    elif isinstance(value, list):
        for item in value:
            _check_json(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TapeError("JSON object keys must be strings")
            _check_text(key)
            _check_json(item)
    else:
        raise TapeError("value is not a JSON type")


def canonical(value: object) -> bytes:
    """Return the canonical ASCII JSON bytes, without a framing newline."""
    try:
        _check_json(value)
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    except TapeError:
        raise
    except (ValueError, RecursionError) as exc:
        raise TapeError("value cannot be encoded as canonical JSON") from exc


def digest(value: object) -> str:
    """Return the lowercase SHA-256 of a canonical JSON value."""
    return hashlib.sha256(canonical(value)).hexdigest()


def decision_sha256(row: Decision) -> str:
    """Hash the full validated source decision, including offline answers."""
    return digest(row)


def _unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise TapeError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise TapeError("JSON numbers must be finite")


def decode(data: str | bytes) -> object:
    """Decode one strict JSON value from text or UTF-8 bytes."""
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        value: object = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        _check_json(value)
        return value
    except TapeError:
        raise
    except (ValueError, RecursionError) as exc:
        raise TapeError("invalid UTF-8 JSON") from exc


def _object(value: object, fields: frozenset[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() != fields:
        raise TapeError(f"{name} must be an object with exactly the documented fields")
    return cast(dict[str, object], value)


def _identity(row: dict[str, object]) -> None:
    key, role = row["key"], row["role"]
    if not isinstance(key, str) or _KEY.fullmatch(key) is None:
        raise TapeError("key must be a portable ASCII label")
    if not isinstance(role, str) or _ROLE.fullmatch(role) is None:
        raise TapeError("role must be a short ASCII identifier")
    basis, trial_id = row["basis"], row["native_trial_id"]
    if basis == "design":
        if trial_id is not None:
            raise TapeError("design records must have null native_trial_id")
        return
    if basis not in ("reconstructed", "bound"):
        raise TapeError("invalid basis")
    if isinstance(trial_id, str) and len(trial_id) == 36:
        try:
            if str(UUID(trial_id)) == trial_id:
                return
        except ValueError:
            pass
    raise TapeError("native records require a canonical native_trial_id UUID")


def _action(value: object) -> Action:
    if value is None or type(value) is int:
        return value
    if isinstance(value, str):
        _check_text(value)
        return value
    raise TapeError("action must be a string, an integer, or null")


def _check_sha256(value: object, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TapeError(f"{field} must be a lowercase SHA-256 hex digest")


def _check_flags(row: dict[str, object]) -> None:
    if type(row["optimal"]) is not bool or type(row["feasible"]) is not bool:
        raise TapeError("optimal and feasible must be booleans")


def _check_range_answers(value: object) -> None:
    answers = _object(value, _RANGE_FIELDS, "int-range answers")
    if answers["kind"] != "int-range":
        raise TapeError("invalid answers kind")
    low, high = answers["min"], answers["max"]
    if type(low) is not int or type(high) is not int or low > high:
        raise TapeError("int-range answers require ordered integer min and max")
    if type(answers["include_null"]) is not bool:
        raise TapeError("include_null must be a boolean")
    for name in ("optimal", "feasible"):
        actions = answers[name]
        if not isinstance(actions, list):
            raise TapeError(f"{name} must be an array")
        previous = None
        for action in actions:
            if type(action) is not int or not low <= action <= high:
                raise TapeError(f"{name} actions must be integers within the range")
            if previous is not None and action <= previous:
                raise TapeError(f"{name} actions must be sorted and distinct")
            previous = action


def _check_answers(answers: object) -> None:
    if isinstance(answers, dict):
        _check_range_answers(answers)
        return
    if not isinstance(answers, list):
        raise TapeError("answers must be an array or an int-range object")
    seen: set[Action] = set()
    for answer in answers:
        row = _object(answer, _ANSWER_FIELDS, "answer")
        action = _action(row["action"])
        _check_flags(row)
        if action in seen:
            raise TapeError("duplicate answer action")
        seen.add(action)


def decision(record: object) -> Decision:
    """Validate one decision record, including its role-local view commitment."""
    row = _object(record, _DECISION_FIELDS, "decision")
    if row["schema"] != DECISION_SCHEMA:
        raise TapeError("invalid decision schema")
    _identity(row)
    view = row["view"]
    if not isinstance(view, dict):
        raise TapeError("view must be a JSON object")
    _check_sha256(row["view_sha256"], "view_sha256")
    if row["view_sha256"] != digest(view):
        raise TapeError("view_sha256 does not match view")
    if row["observed"] is not None:
        if row["basis"] == "design":
            raise TapeError("design records cannot attest an observed action")
        observed = _object(row["observed"], _ACTION_FIELDS, "observed")
        _action(observed["action"])
    _check_answers(row["answers"])
    return cast(Decision, record)


def _contains(values: list[int], action: int) -> bool:
    index = bisect_left(values, action)
    return index < len(values) and values[index] == action


def score(record: Decision, action: object) -> Score:
    """Look up an action in a validated decision; do not score unknown actions."""
    value = _action(action)
    answers = record["answers"]
    if isinstance(answers, list):
        for answer in answers:
            if answer["action"] == value:
                return {"optimal": answer["optimal"], "feasible": answer["feasible"]}
    else:
        if value is None and answers["include_null"]:
            return {"optimal": False, "feasible": False}
        if type(value) is int and answers["min"] <= value <= answers["max"]:
            return {
                "optimal": _contains(answers["optimal"], value),
                "feasible": _contains(answers["feasible"], value),
            }
    raise UnknownActionError("action is absent from the decision's answers")


def validate_result(record: object) -> Result:
    """Validate a result without claiming that its scores or hashes prove provenance."""
    row = _object(record, _RESULT_FIELDS, "result")
    if row["schema"] != RESULT_SCHEMA:
        raise TapeError("invalid result schema")
    _identity(row)
    _check_sha256(row["source_decision_sha256"], "source_decision_sha256")
    _check_sha256(row["source_view_sha256"], "source_view_sha256")
    _check_sha256(row["input_sha256"], "input_sha256")
    size = row["input_bytes"]
    if type(size) is not int or size < 2:
        raise TapeError("input_bytes must be at least 2 for a JSON object")
    policy, transform = row["policy"], row["transform"]
    if policy not in ("observed", "arrival", "revision", "intersection", "external"):
        raise TapeError("invalid result policy")
    if transform not in ("identity", "latest"):
        raise TapeError("invalid result transform")
    if transform == "identity" and row["source_view_sha256"] != row["input_sha256"]:
        raise TapeError("identity transform must preserve the view digest")
    if policy == "observed" and transform != "identity":
        raise TapeError("observed policy does not accept transformed input")
    if row["basis"] == "bound" and transform != "identity":
        raise TapeError("transformed results cannot retain a bound basis")

    error = row["error"]
    if row["basis"] == "design" and policy == "observed" and error != "missing_observation":
        raise TapeError("design records have no observed measurement")
    if row["status"] == "measured":
        if error is not None:
            raise TapeError("measured results must have null error")
        _action(row["action"])
        _check_flags(_object(row["score"], _SCORE_FIELDS, "score"))
    elif row["status"] == "unmeasured":
        if row["action"] is not None or row["score"] is not None:
            raise TapeError("unmeasured results must have null action and score")
        if error not in ("missing_observation", "invalid_action", *_EXTERNAL_ERRORS):
            raise TapeError("unmeasured results require a safe error code")
        if error == "missing_observation" and policy != "observed":
            raise TapeError("only observed policy can report missing_observation")
        if error in _EXTERNAL_ERRORS and policy != "external":
            raise TapeError("only external policy can report an execution or output error")
    else:
        raise TapeError("invalid result status")
    return cast(Result, record)


_Row = TypeVar("_Row", Decision, Result)


def _read_lines(path: Path, validate: Callable[[object], _Row]) -> Iterator[_Row]:
    seen: set[str] = set()
    with path.open("rb") as source:
        for number in count(1):
            line = source.readline(MAX_LINE_BYTES + 2)
            if not line:
                return
            framing = 2 if line.endswith(b"\r\n") else int(line.endswith(b"\n"))
            try:
                if len(line) - framing > MAX_LINE_BYTES:
                    raise TapeError("JSON line exceeds the byte limit")
                row = validate(decode(line))
                if row["key"] in seen:
                    raise TapeError("duplicate record key")
            except TapeError as exc:
                raise TapeError(f"{path}:{number}: {exc}") from exc
            seen.add(row["key"])
            yield row


def read_tape(path: Path) -> Iterator[Decision]:
    """Read validated decisions with unique keys from a UTF-8 JSON Lines file."""
    return _read_lines(path, decision)


def read_results(path: Path) -> Iterator[Result]:
    """Read validated results with unique keys from a UTF-8 JSON Lines file."""
    return _read_lines(path, validate_result)


def write_lines(path: Path, records: Iterable[object]) -> None:
    """Publish caller-validated records atomically; never replace an existing path."""
    if os.path.lexists(path):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".seatlab-", suffix=".tmp", delete=False
        ) as output:
            temp_path = Path(output.name)
            for record in records:
                line = canonical(record)
                if len(line) > MAX_LINE_BYTES:
                    raise TapeError("JSON line exceeds the byte limit")
                output.write(line)
                output.write(b"\n")
            output.flush()
            os.fsync(output.fileno())
        # A same-directory hard link publishes the complete file without replacing a target.
        os.link(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
