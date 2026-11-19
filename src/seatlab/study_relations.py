"""Publish raw-content-free study relation rows joinable to native trial UUIDs.

Each row is one registered comparison, never one trial. A row carries the case and check
identity, the two native trial UUIDs, and the verified boundary outcome. Actions, views,
rewards, labels, prompts, file paths and other values stay in the study bundle.

Rows are published only after the whole study reproduces, including its last check.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal, TypedDict, cast

from . import tape as wire
from .study import _verify_reports
from .study_contract import Check

__all__ = ["export_relations"]

RELATION_SCHEMA: Final = "seatlab.study-relation/v1"
SUMMARY_SCHEMA: Final = "seatlab.study-relations-summary/v1"

RelationKind = Literal["calibration", "alignment", "team-alignment"]
BoundaryUnit = Literal["world_step", "role_turn"]


class StudyRelation(TypedDict):
    schema: Literal["seatlab.study-relation/v1"]
    study_sha256: str
    report_sha256: str
    check_id: str
    kind: RelationKind
    pair_index: int
    case_sha256: str
    source_native_trial_id: str | None
    target_native_trial_id: str | None
    role: str | None
    status: str
    boundary_unit: BoundaryUnit | None
    boundary: int | None
    matched_prefix: int | None
    native_action_changed: bool | None
    reproduced: bool | None


def _row(
    *,
    study_sha256: str,
    report_sha256: str,
    check_id: str,
    kind: RelationKind,
    pair_index: int,
    case_sha256: str,
    source_native_trial_id: str | None,
    target_native_trial_id: str | None,
    role: str | None,
    status: str,
    boundary_unit: BoundaryUnit | None,
    boundary: int | None,
    matched_prefix: int | None,
    native_action_changed: bool | None,
    reproduced: bool | None,
) -> StudyRelation:
    return {
        "schema": RELATION_SCHEMA,
        "study_sha256": study_sha256,
        "report_sha256": report_sha256,
        "check_id": check_id,
        "kind": kind,
        "pair_index": pair_index,
        "case_sha256": case_sha256,
        "source_native_trial_id": source_native_trial_id,
        "target_native_trial_id": target_native_trial_id,
        "role": role,
        "status": status,
        "boundary_unit": boundary_unit,
        "boundary": boundary,
        "matched_prefix": matched_prefix,
        "native_action_changed": native_action_changed,
        "reproduced": reproduced,
    }


def _arm_trial(arm: object) -> str | None:
    """Return one arm's native trial UUID, or null when the arm is not registered."""
    if arm is None:
        return None
    return cast(str, cast(Mapping[str, object], arm)["native_trial_id"])


def _calibration_rows(
    check: Check, report: Mapping[str, object], report_sha256: str, study_sha256: str
) -> list[StudyRelation]:
    rows: list[StudyRelation] = []
    pairs = cast(list[Mapping[str, object]], report["pairs"])
    for index, pair in enumerate(pairs, 1):
        rows.append(
            _row(
                study_sha256=study_sha256,
                report_sha256=report_sha256,
                check_id=check["id"],
                kind="calibration",
                pair_index=index,
                case_sha256=cast(str, pair["case_sha256"]),
                source_native_trial_id=_arm_trial(pair["source"]),
                target_native_trial_id=_arm_trial(pair["target"]),
                role=None,
                status=cast(str, pair["status"]),
                boundary_unit=None,
                boundary=None,
                matched_prefix=None,
                native_action_changed=cast(bool | None, pair["native_action_changed"]),
                reproduced=cast(bool, pair["reproduced"]),
            )
        )
    return rows


def _alignment_rows(
    check: Check, report: Mapping[str, object], report_sha256: str, study_sha256: str
) -> list[StudyRelation]:
    rows: list[StudyRelation] = []
    pairs = cast(list[Mapping[str, object]], report["pairs"])
    for index, pair in enumerate(pairs, 1):
        source, target = pair["source"], pair["target"]
        missing = source is None or target is None
        rows.append(
            _row(
                study_sha256=study_sha256,
                report_sha256=report_sha256,
                check_id=check["id"],
                kind="alignment",
                pair_index=index,
                case_sha256=cast(str, pair["case_sha256"]),
                source_native_trial_id=_arm_trial(source),
                target_native_trial_id=_arm_trial(target),
                role=cast(str, pair["role"]),
                status=cast(str, pair["status"]),
                boundary_unit="role_turn",
                boundary=cast(int | None, pair["boundary_turn"]),
                # A missing arm has no compared prefix; do not invent a zero.
                matched_prefix=None if missing else cast(int, pair["matched_prefix"]),
                native_action_changed=None,
                reproduced=None,
            )
        )
    return rows


def _team_rows(
    check: Check, report: Mapping[str, object], report_sha256: str, study_sha256: str
) -> list[StudyRelation]:
    return [
        _row(
            study_sha256=study_sha256,
            report_sha256=report_sha256,
            check_id=check["id"],
            kind="team-alignment",
            pair_index=1,
            case_sha256=cast(str, report["case_sha256"]),
            source_native_trial_id=cast(str, report["source_native_trial_id"]),
            target_native_trial_id=cast(str, report["target_native_trial_id"]),
            role=None,
            status=cast(str, report["status"]),
            boundary_unit="world_step",
            boundary=cast(int | None, report["boundary_step"]),
            matched_prefix=cast(int, report["matched_steps"]),
            native_action_changed=None,
            reproduced=None,
        )
    ]


def _rows(
    check: Check, report: Mapping[str, object], report_sha256: str, study_sha256: str
) -> list[StudyRelation]:
    kind = check["kind"]
    if kind == "calibration":
        return _calibration_rows(check, report, report_sha256, study_sha256)
    if kind == "alignment":
        return _alignment_rows(check, report, report_sha256, study_sha256)
    return _team_rows(check, report, report_sha256, study_sha256)


def export_relations(manifest: Path, out: Path, *, expect_sha256: str) -> dict[str, object]:
    """Write one relation row per verified comparison and return a summary.

    Each verified report contributes only its compact rows. No full report is retained
    across checks. ``write_lines`` publishes all rows after the last check verifies.
    """
    rows: list[StudyRelation] = []

    def collect(check: Check, report: dict[str, object], report_sha256: str) -> None:
        rows.extend(_rows(check, report, report_sha256, expect_sha256))

    summary = _verify_reports(manifest, expect_sha256=expect_sha256, on_check=collect)
    wire.write_lines(out, rows)
    return {
        "schema": SUMMARY_SCHEMA,
        "study_sha256": summary["study_sha256"],
        "rows": len(rows),
        "checks": len(cast(list[dict[str, object]], summary["checks"])),
    }
