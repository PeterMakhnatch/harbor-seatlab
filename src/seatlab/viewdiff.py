"""Locate changes between decoded input views without retaining values."""

from math import copysign
from typing import Literal, TypedDict, cast

_Change = Literal["added", "removed", "type_changed", "value_changed"]
_MISSING = object()


class _PathChange(TypedDict):
    path: str
    change: _Change


class InputDiff(TypedDict):
    changes: int
    shown: int
    truncated: bool
    paths: list[_PathChange]


def diff_input(left: dict[str, object], right: dict[str, object], *, limit: int) -> InputDiff:
    """Count all changes but retain only the first `limit` JSON Pointer records."""
    if type(limit) is not int or not 1 <= limit <= 128:
        raise ValueError("input diff limit must be an integer from 1 to 128")

    changes = 0
    paths: list[_PathChange] = []
    location: list[str | int] = []

    def record(change: _Change) -> None:
        nonlocal changes
        changes += 1
        if len(paths) < limit:
            pointer = "".join(
                "/" + str(part).replace("~", "~0").replace("/", "~1") for part in location
            )
            paths.append({"path": pointer, "change": change})

    def visit(before: object, after: object) -> None:
        if before is _MISSING:
            record("added")
        elif after is _MISSING:
            record("removed")
        elif type(before) is not type(after):
            record("type_changed")
        elif isinstance(before, dict):
            right_object = cast(dict[str, object], after)
            for key in sorted(before.keys() | right_object.keys()):
                location.append(key)
                visit(before.get(key, _MISSING), right_object.get(key, _MISSING))
                location.pop()
        elif isinstance(before, list):
            right_array = cast(list[object], after)
            left_size, right_size = len(before), len(right_array)
            for index in range(max(left_size, right_size)):
                location.append(index)
                visit(
                    before[index] if index < left_size else _MISSING,
                    right_array[index] if index < right_size else _MISSING,
                )
                location.pop()
        elif before != after or (
            # Equal finite floats only have different JSON encodings for signed zero.
            isinstance(before, float)
            and before == 0.0
            and copysign(1.0, before) != copysign(1.0, cast(float, after))
        ):
            record("value_changed")

    visit(left, right)
    return {
        "changes": changes,
        "shown": len(paths),
        "truncated": changes > len(paths),
        "paths": paths,
    }
