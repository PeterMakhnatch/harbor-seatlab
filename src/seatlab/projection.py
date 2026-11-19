"""Project role-local JSON views using bounded RFC 6901 JSON Pointers."""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TypeAlias, cast

from . import tape as wire

MAX_FIELDS = 32
MAX_POINTER_LENGTH = 512
MAX_POINTER_COMPONENTS = 32

_Step: TypeAlias = tuple[str, int | None]
_CompiledPointer: TypeAlias = tuple[_Step, ...]

_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*", re.ASCII)
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_MAX_INDEX_TEXT = str(sys.maxsize - 1)
_MISSING = object()


def _unescape_token(token: str) -> str:
    if "~" not in token:
        return token
    parts: list[str] = []
    index = 0
    size = len(token)
    while index < size:
        tilde = token.find("~", index)
        if tilde < 0:
            parts.append(token[index:])
            break
        if tilde > index:
            parts.append(token[index:tilde])
        if tilde + 1 >= size:
            raise ValueError(f"invalid escape in JSON Pointer token {token!r}")
        code = token[tilde + 1]
        if code == "0":
            parts.append("~")
        elif code == "1":
            parts.append("/")
        else:
            raise ValueError(f"invalid escape '~{code}' in JSON Pointer token {token!r}")
        index = tilde + 2
    return "".join(parts)


def _parse_array_index(token: str) -> int | None:
    if _ARRAY_INDEX.fullmatch(token) is None:
        return None
    if len(token) > len(_MAX_INDEX_TEXT) or (
        len(token) == len(_MAX_INDEX_TEXT) and token > _MAX_INDEX_TEXT
    ):
        return None
    return int(token)


def _compile_pointer(pointer: object) -> _CompiledPointer:
    if not isinstance(pointer, str):
        raise ValueError("projection fields must contain only JSON Pointer strings")
    if pointer == "":
        raise ValueError(
            "root JSON Pointer '' is not allowed; omit fields to project the whole view"
        )
    if not pointer.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {pointer!r}")
    if len(pointer) > MAX_POINTER_LENGTH:
        raise ValueError(f"JSON Pointer exceeds {MAX_POINTER_LENGTH} characters")
    if _SURROGATE.search(pointer):
        raise ValueError("JSON Pointer contains an unpaired surrogate")
    raw_tokens = pointer.split("/")[1:]
    if len(raw_tokens) > MAX_POINTER_COMPONENTS:
        raise ValueError(f"JSON Pointer exceeds {MAX_POINTER_COMPONENTS} components")
    steps: list[_Step] = []
    for raw in raw_tokens:
        key = _unescape_token(raw)
        steps.append((key, _parse_array_index(key)))
    return tuple(steps)


def _resolve(view: dict[str, object], steps: _CompiledPointer) -> object:
    current: object = view
    for key, array_index in steps:
        if isinstance(current, dict):
            mapping = cast(dict[str, object], current)
            if key not in mapping:
                return _MISSING
            current = mapping[key]
        elif isinstance(current, list):
            if array_index is None or array_index >= len(current):
                return _MISSING
            current = current[array_index]
        else:
            return _MISSING
    return current


@dataclass(frozen=True, slots=True)
class Projection:
    """An immutable RFC 6901 projection over the current bound actor view."""

    fields: tuple[str, ...] | None
    _steps: tuple[_CompiledPointer, ...] = field(repr=False, compare=False)

    def __init__(self, fields: Sequence[str] | None = None) -> None:
        if fields is None:
            object.__setattr__(self, "fields", None)
            object.__setattr__(self, "_steps", ())
            return
        if not isinstance(fields, Sequence) or isinstance(
            fields, (str, bytes, bytearray, memoryview)
        ):
            raise ValueError(
                "projection fields must be None or a sequence of JSON Pointer strings"
            )
        if len(fields) > MAX_FIELDS:
            raise ValueError(f"projection supports at most {MAX_FIELDS} fields")
        compiled: dict[str, _CompiledPointer] = {}
        for pointer in fields:
            steps = _compile_pointer(pointer)
            if pointer in compiled:
                raise ValueError(f"duplicate JSON Pointer {pointer!r}")
            compiled[pointer] = steps
        ordered = tuple(sorted(compiled))
        object.__setattr__(self, "fields", ordered)
        object.__setattr__(self, "_steps", tuple(compiled[pointer] for pointer in ordered))

    def _encode(self, view: dict[str, object], *, require_all: bool) -> bytes | None:
        if not isinstance(view, dict):
            raise ValueError("projection view must be a JSON object")
        if self.fields is None:
            return wire.canonical(view)
        if not self.fields:
            return wire.canonical([])
        projected: list[list[object]] = []
        for steps in self._steps:
            value = _resolve(view, steps)
            if value is _MISSING:
                if require_all:
                    return None
                projected.append([False])
            else:
                projected.append([True, value])
        return wire.canonical(projected)

    def encode(self, view: dict[str, object]) -> bytes:
        """Encode the projection, preserving absent fields as distinct from null."""
        return cast(bytes, self._encode(view, require_all=False))

    def encode_present(self, view: dict[str, object]) -> bytes | None:
        """Encode the projection, or return None if any selected field is absent."""
        return self._encode(view, require_all=True)
