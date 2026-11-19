"""Validate declarative offline study recipes and sealed manifests."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal, NotRequired, TypeAlias, TypedDict, cast

from . import tape as wire

RECIPE_SCHEMA = "seatlab.study-recipe/v1"
MANIFEST_SCHEMA = "seatlab.study/v1"

MAX_CHECKS = 128
MAX_PATH_COMPONENTS = 16
MAX_PATH_LENGTH = 256
MIN_INPUT_PATHS = 1
MAX_INPUT_PATHS = 128

_CHECK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_PATH_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class CalibrationCheck(TypedDict):
    id: str
    kind: Literal["calibration"]
    tape: str
    predictions: str
    pairs: str
    context: NotRequired[str]


class AlignmentCheck(TypedDict):
    id: str
    kind: Literal["alignment"]
    source: str
    target: str
    pairs: str
    input_paths: NotRequired[int]


Check: TypeAlias = CalibrationCheck | AlignmentCheck


class FileReceipt(TypedDict):
    bytes: int
    sha256: str


class StudyManifest(TypedDict):
    schema: Literal["seatlab.study/v1"]
    checks: list[Check]
    files: dict[str, FileReceipt]
    reports: dict[str, str]


_RECIPE_FIELDS = frozenset({"schema", "checks"})
_MANIFEST_FIELDS = frozenset(StudyManifest.__annotations__)
_FILE_RECEIPT_FIELDS = frozenset(FileReceipt.__annotations__)
_CALIBRATION_REQUIRED = frozenset({"id", "kind", "tape", "predictions", "pairs"})
_CALIBRATION_ALLOWED = _CALIBRATION_REQUIRED | frozenset({"context"})
_ALIGNMENT_REQUIRED = frozenset({"id", "kind", "source", "target", "pairs"})
_ALIGNMENT_ALLOWED = _ALIGNMENT_REQUIRED | frozenset({"input_paths"})

__all__ = [
    "AlignmentCheck",
    "CalibrationCheck",
    "Check",
    "FileReceipt",
    "MANIFEST_SCHEMA",
    "MAX_CHECKS",
    "MAX_INPUT_PATHS",
    "MAX_PATH_COMPONENTS",
    "MAX_PATH_LENGTH",
    "MIN_INPUT_PATHS",
    "RECIPE_SCHEMA",
    "StudyManifest",
    "parse_manifest",
    "parse_recipe",
    "referenced_paths",
    "valid_sha256",
    "validate_path",
]


def valid_sha256(value: object) -> bool:
    """Return whether value is a canonical 64-character lowercase SHA-256 hex digest."""
    return type(value) is str and _SHA256.fullmatch(value) is not None


def validate_path(value: object) -> str:
    """Validate a canonical relative POSIX path under the study bundle root."""
    if type(value) is not str:
        raise wire.TapeError("study path must be a string")
    if not value or len(value) > MAX_PATH_LENGTH:
        raise wire.TapeError(
            f"study path must be 1 to {MAX_PATH_LENGTH} ASCII characters"
        )
    if not value.isascii() or "\\" in value:
        raise wire.TapeError("study path must be a relative POSIX ASCII path")
    if value.startswith("/") or value.endswith("/"):
        raise wire.TapeError("study path must not start or end with '/'")
    parts = value.split("/")
    if len(parts) > MAX_PATH_COMPONENTS:
        raise wire.TapeError(
            f"study path must have at most {MAX_PATH_COMPONENTS} components"
        )
    for part in parts:
        if not part or part in (".", "..") or part.startswith("."):
            raise wire.TapeError(
                "study path must not contain empty, dot, or hidden components"
            )
        if _PATH_COMPONENT.fullmatch(part) is None:
            raise wire.TapeError(f"invalid study path component {part!r}")
    return value


def _check_id(value: object) -> str:
    if type(value) is not str or _CHECK_ID.fullmatch(value) is None:
        raise wire.TapeError("check id must be a portable ASCII identifier")
    return value


def _parse_check(value: object) -> Check:
    if type(value) is not dict:
        raise wire.TapeError("study check must be an object")
    raw = cast(dict[object, object], value)
    if any(type(key) is not str for key in raw):
        raise wire.TapeError("study check keys must be strings")
    fields = cast(dict[str, object], raw)
    keys = fields.keys()
    kind = fields.get("kind")
    if kind == "calibration":
        if not _CALIBRATION_REQUIRED <= keys <= _CALIBRATION_ALLOWED:
            raise wire.TapeError(
                "calibration check must have exactly the documented fields"
            )
        check_id = _check_id(fields["id"])
        check: CalibrationCheck = {
            "id": check_id,
            "kind": "calibration",
            "tape": validate_path(fields["tape"]),
            "predictions": validate_path(fields["predictions"]),
            "pairs": validate_path(fields["pairs"]),
        }
        if "context" in fields:
            check["context"] = validate_path(fields["context"])
        return check
    if kind == "alignment":
        if not _ALIGNMENT_REQUIRED <= keys <= _ALIGNMENT_ALLOWED:
            raise wire.TapeError(
                "alignment check must have exactly the documented fields"
            )
        check_id = _check_id(fields["id"])
        alignment: AlignmentCheck = {
            "id": check_id,
            "kind": "alignment",
            "source": validate_path(fields["source"]),
            "target": validate_path(fields["target"]),
            "pairs": validate_path(fields["pairs"]),
        }
        if "input_paths" in fields:
            limit = fields["input_paths"]
            if type(limit) is not int or not MIN_INPUT_PATHS <= limit <= MAX_INPUT_PATHS:
                raise wire.TapeError(
                    f"alignment check {check_id!r} input_paths must be an integer "
                    f"from {MIN_INPUT_PATHS} to {MAX_INPUT_PATHS}"
                )
            alignment["input_paths"] = limit
        return alignment
    raise wire.TapeError("study check kind must be 'calibration' or 'alignment'")


def _parse_checks(value: object) -> list[Check]:
    if type(value) is not list:
        raise wire.TapeError("study checks must be an array")
    items = cast(list[object], value)
    if not 1 <= len(items) <= MAX_CHECKS:
        raise wire.TapeError(f"study must contain between 1 and {MAX_CHECKS} checks")
    checks: list[Check] = []
    seen: set[str] = set()
    for item in items:
        check = _parse_check(item)
        check_id = check["id"]
        if check_id in seen:
            raise wire.TapeError(f"duplicate study check id {check_id!r}")
        seen.add(check_id)
        checks.append(check)
    return checks


def referenced_paths(checks: Sequence[Check]) -> list[str]:
    """Return sorted unique relative paths referenced by checks."""
    paths: set[str] = set()
    for check in checks:
        if check["kind"] == "calibration":
            paths.add(validate_path(check["tape"]))
            paths.add(validate_path(check["predictions"]))
            paths.add(validate_path(check["pairs"]))
            if "context" in check:
                paths.add(validate_path(check["context"]))
        elif check["kind"] == "alignment":
            paths.add(validate_path(check["source"]))
            paths.add(validate_path(check["target"]))
            paths.add(validate_path(check["pairs"]))
        else:
            raise wire.TapeError("study check kind must be 'calibration' or 'alignment'")
    return sorted(paths)


def _strict_object(value: object, fields: frozenset[str], name: str) -> dict[str, object]:
    if type(value) is not dict or value.keys() != fields:
        raise wire.TapeError(f"{name} must be an object with exactly the documented fields")
    return cast(dict[str, object], value)


def parse_recipe(value: object) -> list[Check]:
    """Validate a seatlab.study-recipe/v1 object and return its ordered checks."""
    document = _strict_object(value, _RECIPE_FIELDS, "study recipe")
    if document["schema"] != RECIPE_SCHEMA:
        raise wire.TapeError(f"invalid {RECIPE_SCHEMA} schema")
    return _parse_checks(document["checks"])


def _parse_file_receipt(value: object, path: str) -> FileReceipt:
    receipt = _strict_object(value, _FILE_RECEIPT_FIELDS, f"file receipt for {path!r}")
    size = receipt["bytes"]
    digest = receipt["sha256"]
    if type(size) is not int or size < 0:
        raise wire.TapeError(f"file receipt for {path!r} bytes must be a nonnegative integer")
    if not valid_sha256(digest):
        raise wire.TapeError(
            f"file receipt for {path!r} sha256 must be a lowercase SHA-256 hex digest"
        )
    return {"bytes": size, "sha256": cast(str, digest)}


def parse_manifest(value: object) -> StudyManifest:
    """Validate a seatlab.study/v1 manifest object and its exact file and check coverage."""
    document = _strict_object(value, _MANIFEST_FIELDS, "study manifest")
    if document["schema"] != MANIFEST_SCHEMA:
        raise wire.TapeError(f"invalid {MANIFEST_SCHEMA} schema")
    checks = _parse_checks(document["checks"])

    raw_files = document["files"]
    if type(raw_files) is not dict:
        raise wire.TapeError("study manifest files must be an object")
    files_obj = cast(dict[object, object], raw_files)
    files: dict[str, FileReceipt] = {}
    for raw_path, raw_receipt in files_obj.items():
        path = validate_path(raw_path)
        files[path] = _parse_file_receipt(raw_receipt, path)

    expected_paths = set(referenced_paths(checks))
    if files.keys() != expected_paths:
        missing = len(expected_paths - files.keys())
        extra = len(files.keys() - expected_paths)
        raise wire.TapeError(
            f"study manifest files coverage mismatch: {missing} missing, {extra} unexpected"
        )

    raw_reports = document["reports"]
    if type(raw_reports) is not dict:
        raise wire.TapeError("study manifest reports must be an object")
    reports_obj = cast(dict[object, object], raw_reports)
    reports: dict[str, str] = {}
    for raw_id, raw_digest in reports_obj.items():
        check_id = _check_id(raw_id)
        if not valid_sha256(raw_digest):
            raise wire.TapeError(
                f"study manifest report digest for {check_id!r} must be a lowercase "
                "SHA-256 hex digest"
            )
        reports[check_id] = cast(str, raw_digest)

    expected_ids = {check["id"] for check in checks}
    if reports.keys() != expected_ids:
        missing = len(expected_ids - reports.keys())
        extra = len(reports.keys() - expected_ids)
        raise wire.TapeError(
            f"study manifest reports coverage mismatch: {missing} missing, {extra} unexpected"
        )

    return {
        "schema": MANIFEST_SCHEMA,
        "checks": checks,
        "files": files,
        "reports": reports,
    }
