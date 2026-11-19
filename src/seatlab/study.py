"""Seal and verify portable, offline SeatLab study bundles."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from . import tape as wire
from .alignment import align
from .calibration import calibrate
from .context import ContextSpec
from .study_contract import (
    Check,
    FileReceipt,
    StudyManifest,
    parse_manifest,
    parse_recipe,
    referenced_paths,
    valid_sha256,
)
from .study_inputs import read_document, snapshot
from .team_alignment import team_align
from .trajectory import role_trajectory

__all__ = ["StudyMismatch", "seal_study", "verify_study"]

_MANIFEST_SCHEMA = "seatlab.study/v1"
_VERIFICATION_SCHEMA = "seatlab.study-verification/v1"


class StudyMismatch(ValueError):
    """A study bundle's recorded bytes, reports, or expected pin do not match."""


def _path_in_snapshot(root: Path, relative: str) -> Path:
    return root.joinpath(*relative.split("/"))


def _require_siblings(recipe: Path, out: Path) -> Path:
    """Require recipe and output to share a directory and refuse existing output paths."""
    if out.name in ("", ".", ".."):
        raise wire.TapeError("output path must name a file")
    if os.path.lexists(out):
        raise FileExistsError(f"refusing to overwrite existing output path: {out}")
    recipe_parent = recipe.parent
    out_parent = out.parent
    if not recipe_parent.is_dir():
        raise wire.TapeError(f"recipe parent directory does not exist: {recipe_parent}")
    if not out_parent.is_dir():
        raise wire.TapeError(f"output parent directory does not exist: {out_parent}")
    recipe_dir = recipe_parent.resolve(strict=True)
    out_dir = out_parent.resolve(strict=True)
    if recipe_dir != out_dir:
        raise wire.TapeError("recipe and output manifest must live in the same directory")
    if recipe.name == out.name:
        raise FileExistsError(f"output path must differ from recipe path: {out}")
    return recipe_dir


def _run_check(check: Check, root: Path) -> dict[str, object]:
    if check["kind"] == "calibration":
        calibration = check
        context_path = calibration.get("context")
        context = (
            ContextSpec.load(_path_in_snapshot(root, context_path))
            if context_path is not None
            else None
        )
        return calibrate(
            _path_in_snapshot(root, calibration["tape"]),
            _path_in_snapshot(root, calibration["predictions"]),
            _path_in_snapshot(root, calibration["pairs"]),
            context=context,
        )
    if check["kind"] == "alignment":
        alignment = check
        return align(
            _path_in_snapshot(root, alignment["source"]),
            _path_in_snapshot(root, alignment["target"]),
            _path_in_snapshot(root, alignment["pairs"]),
            input_paths=alignment.get("input_paths"),
        )
    if check["kind"] == "role-trajectory":
        trajectory = check
        return role_trajectory(
            _path_in_snapshot(root, trajectory["source"]),
            _path_in_snapshot(root, trajectory["target"]),
            source_order=_path_in_snapshot(root, trajectory["source_order"]),
            target_order=_path_in_snapshot(root, trajectory["target_order"]),
            role=trajectory["role"],
            fields=trajectory.get("fields"),
        )
    team = check
    return team_align(
        _path_in_snapshot(root, team["source"]),
        _path_in_snapshot(root, team["target"]),
        source_order=_path_in_snapshot(root, team["source_order"]),
        target_order=_path_in_snapshot(root, team["target_order"]),
        input_paths=team.get("input_paths"),
    )


def _check_summary(
    check: Check, report: Mapping[str, object], report_sha256: str
) -> dict[str, object]:
    if check["kind"] == "team-alignment":
        return {
            "id": check["id"],
            "kind": check["kind"],
            "report_sha256": report_sha256,
            "status": report["status"],
            "matched_steps": report["matched_steps"],
            "boundary_step": report["boundary_step"],
        }
    if check["kind"] == "role-trajectory":
        return {
            "id": check["id"],
            "kind": check["kind"],
            "report_sha256": report_sha256,
            "role": report["role"],
            "fields": report["fields"],
            "status": report["status"],
            "schedule": report["schedule"],
            "role_decisions": report["role_decisions"],
            "raw_view": report["raw_view"],
            "selected_view": report["selected_view"],
            "accepted_action": report["accepted_action"],
        }
    summary: dict[str, object] = {
        "id": check["id"],
        "kind": check["kind"],
        "report_sha256": report_sha256,
        "counts": report["counts"],
    }
    if "external_observations" in report:
        summary["external_observations"] = report["external_observations"]
    return summary


def _compare_file_receipts(
    recorded: Mapping[str, FileReceipt], actual: Mapping[str, FileReceipt]
) -> None:
    if set(recorded) != set(actual):
        raise StudyMismatch("recorded file set does not match snapshot file set")
    for rel_path in sorted(recorded):
        expected = recorded[rel_path]
        observed = actual[rel_path]
        if expected["bytes"] != observed["bytes"] or expected["sha256"] != observed["sha256"]:
            raise StudyMismatch(
                f"file {rel_path!r} does not match manifest "
                f"(expected {expected['bytes']} bytes / {expected['sha256']}, "
                f"got {observed['bytes']} bytes / {observed['sha256']})"
            )


def seal_study(recipe: Path, out: Path) -> dict[str, object]:
    """Snapshot referenced files, run checks, and atomically publish a study manifest."""
    root = _require_siblings(recipe, out)
    checks = parse_recipe(read_document(recipe))
    paths = referenced_paths(checks)
    reports: dict[str, str] = {}
    with snapshot(root, paths) as (private_root, file_receipts):
        for check in checks:
            report = _run_check(check, private_root)
            reports[check["id"]] = wire.digest(report)
        manifest: StudyManifest = {
            "schema": _MANIFEST_SCHEMA,
            "checks": checks,
            "files": {path: file_receipts[path] for path in paths},
            "reports": reports,
        }
        parse_manifest(manifest)
    wire.write_lines(out, [manifest])
    return cast(dict[str, object], manifest)


def _verify_reports(
    manifest: Path,
    *,
    expect_sha256: str | None,
    on_check: Callable[[Check, dict[str, object], str], None] | None = None,
) -> dict[str, object]:
    """Verify once, optionally consuming each report after its digest matches.

    The pin is checked before any referenced file is opened. Every check runs inside one
    private snapshot, so callers that need report contents never read the bundle twice.
    """
    if expect_sha256 is not None and not valid_sha256(expect_sha256):
        raise wire.TapeError(
            "expect_sha256 must be a lowercase 64-character hex SHA-256 digest"
        )

    raw_manifest = read_document(manifest)
    parsed = parse_manifest(raw_manifest)
    study_sha256 = wire.digest(raw_manifest)
    if expect_sha256 is not None and study_sha256 != expect_sha256:
        raise StudyMismatch(
            f"manifest digest {study_sha256} does not match expected pin {expect_sha256}"
        )

    root = manifest.parent.resolve(strict=True)
    checks = parsed["checks"]
    paths = referenced_paths(checks)
    check_summaries: list[dict[str, object]] = []
    with snapshot(root, paths) as (private_root, file_receipts):
        _compare_file_receipts(parsed["files"], file_receipts)
        for check in checks:
            report = _run_check(check, private_root)
            report_sha256 = wire.digest(report)
            expected_report = parsed["reports"][check["id"]]
            if report_sha256 != expected_report:
                raise StudyMismatch(
                    f"check {check['id']!r} report digest {report_sha256} "
                    f"does not match manifest {expected_report}"
                )
            check_summaries.append(_check_summary(check, report, report_sha256))
            if on_check is not None:
                on_check(check, report, report_sha256)
        total_bytes = sum(file_receipts[path]["bytes"] for path in paths)

    summary: dict[str, object] = {
        "schema": _VERIFICATION_SCHEMA,
        "status": "verified",
        "study_sha256": study_sha256,
        "files": len(paths),
        "bytes": total_bytes,
        "checks": check_summaries,
    }
    return summary


def verify_study(manifest: Path, *, expect_sha256: str | None = None) -> dict[str, object]:
    """Verify a study manifest's optional pin, file digests, and derived reports."""
    return _verify_reports(manifest, expect_sha256=expect_sha256)
