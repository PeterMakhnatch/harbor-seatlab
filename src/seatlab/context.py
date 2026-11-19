"""Compile declarative, content-addressed edits to a role-local JSON view."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard, cast

from .tape import TapeError, decode, digest

_SCHEMA = "seatlab.context/v1"
_MAX_SPEC_BYTES = 64 * 1024
_MAX_OPS = 32
_MAX_PATH_DEPTH = 8
_MAX_STRING_LENGTH = 256


@dataclass(frozen=True, slots=True)
class _Take:
    path: tuple[str, ...]
    count: int


@dataclass(frozen=True, slots=True)
class _Relabel:
    path: tuple[str, ...]
    source: str
    target: str


def _bounded_text(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and len(value) <= _MAX_STRING_LENGTH


def _compile_op(value: object, index: int) -> _Take | _Relabel:
    prefix = f"context op {index}"
    if not isinstance(value, dict):
        raise ValueError(f"{prefix} must be an object")
    operation = cast(dict[str, object], value)
    name = operation.get("op")
    if name == "take":
        fields = {"op", "path", "count"}
    elif name == "relabel":
        fields = {"op", "path", "from", "to"}
    else:
        raise ValueError(f"{prefix} must be take or relabel")
    if operation.keys() != fields:
        raise ValueError(f"{prefix} ({name}) must have exactly the documented fields")

    raw_path = operation["path"]
    if (
        not isinstance(raw_path, list)
        or not 1 <= len(raw_path) <= _MAX_PATH_DEPTH
        or any(not part or not _bounded_text(part) for part in cast(list[object], raw_path))
    ):
        raise ValueError(
            f"{prefix} path must have 1 to {_MAX_PATH_DEPTH} nonempty string components "
            f"of at most {_MAX_STRING_LENGTH} characters each"
        )
    path = cast(list[str], raw_path)
    if name == "take":
        count = operation["count"]
        if type(count) is not int or count < 0:
            raise ValueError(f"{prefix} count must be a nonnegative integer")
        return _Take(tuple(path), count)
    source, target = operation["from"], operation["to"]
    if not _bounded_text(source) or not _bounded_text(target):
        raise ValueError(
            f"{prefix} from and to must be strings of at most {_MAX_STRING_LENGTH} characters"
        )
    return _Relabel(tuple(path), source, target)


def _update(value: object, op: _Take | _Relabel, depth: int = 0) -> object:
    if depth == len(op.path):
        if isinstance(op, _Take):
            if not isinstance(value, list):
                raise ValueError("take requires an array")
            return value[: op.count] if op.count < len(value) else value
        if not isinstance(value, str):
            raise ValueError("relabel requires a string")
        if value == op.target:
            return value
        if value != op.source:
            raise ValueError("relabel requires the source or target label")
        return op.target

    if not isinstance(value, dict):
        raise ValueError(f"path component {depth + 1} requires an object")
    fields = cast(dict[str, object], value)
    part = op.path[depth]
    if part == "*":
        if not fields:
            raise ValueError("wildcard must match at least one field")
        updated = fields
        for key, child in fields.items():
            replacement = _update(child, op, depth + 1)
            if replacement is not child:
                if updated is fields:
                    updated = fields.copy()
                updated[key] = replacement
        return updated
    if part not in fields:
        raise ValueError(f"missing field {part!r}")
    child = fields[part]
    replacement = _update(child, op, depth + 1)
    if replacement is child:
        return fields
    return {**fields, part: replacement}


@dataclass(frozen=True, slots=True)
class ContextSpec:
    """An immutable program with only the two supported JSON view operations."""

    identifier: str
    _ops: tuple[_Take | _Relabel, ...]

    def __init__(self, value: object) -> None:
        if not isinstance(value, dict) or value.keys() != {"schema", "ops"}:
            raise ValueError("context spec must have exactly the schema and ops fields")
        spec = cast(dict[str, object], value)
        if spec["schema"] != _SCHEMA:
            raise ValueError("invalid context spec schema")
        ops = spec["ops"]
        if not isinstance(ops, list) or len(ops) > _MAX_OPS:
            raise ValueError(
                f"context spec ops must be an array of at most {_MAX_OPS} operations"
            )
        compiled = tuple(
            _compile_op(op, index) for index, op in enumerate(cast(list[object], ops), start=1)
        )
        object.__setattr__(self, "identifier", f"context-v1:{digest(value)}")
        object.__setattr__(self, "_ops", compiled)

    @classmethod
    def load(cls, path: Path) -> ContextSpec:
        """Load and compile a strict UTF-8 JSON file, without executing any code."""
        with path.open("rb") as stream:
            data = stream.read(_MAX_SPEC_BYTES + 1)
        if len(data) > _MAX_SPEC_BYTES:
            raise ValueError(f"context spec exceeds {_MAX_SPEC_BYTES} bytes")
        try:
            value = decode(data)
        except TapeError as exc:
            raise ValueError(f"invalid context spec: {exc}") from exc
        return cls(value)

    def apply(self, view: dict[str, object]) -> dict[str, object]:
        """Return the transformed view without mutating it. Share unchanged branches."""
        if not isinstance(view, dict):
            raise ValueError("context view must be an object")
        result: object = view
        for index, op in enumerate(self._ops, start=1):
            try:
                result = _update(result, op)
            except ValueError as exc:
                raise ValueError(f"context op {index} at {list(op.path)!r}: {exc}") from exc
        assert isinstance(result, dict)  # Nonempty paths cannot replace the root object.
        return cast(dict[str, object], result)
