"""Select a small observed-coverage panel of cases from a verified study bundle.

A design names criteria (a dimension of one role's trajectory comparison and the
direction it should show) and, for every case, which verified role-trajectory check
measures each criterion. Only the already-verified, value-free tallies of those checks
are read; raw role values are never inspected or emitted. The result reports observed
coverage of the supplied cases. It is not prospective efficacy, a minimum trial count,
or a statistically representative sample.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple, cast

from . import tape as wire
from .panel_cover import minimum_panel
from .projection import Projection
from .study import _verify_reports
from .study_contract import Check, valid_sha256
from .study_inputs import read_document

__all__ = ["PANEL_SCHEMA", "DESIGN_SCHEMA", "panel_report"]

DESIGN_SCHEMA = "seatlab.panel-design/v1"
PANEL_SCHEMA = "seatlab.observed-panel/v1"

MAX_CRITERIA = 16
MAX_CASES = 256

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_DIMENSIONS = ("raw_view", "selected_view", "accepted_action")
_WHENS = ("different", "equal")
_DESIGN_FIELDS = frozenset({"schema", "criteria", "cases"})
_CRITERION_FIELDS = frozenset({"id", "dimension", "when", "role"})


def _label(value: object, what: str) -> str:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise wire.TapeError(f"{what} must be a portable ASCII identifier")
    return value


def _criterion(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise wire.TapeError("panel criterion must be an object")
    raw = cast(dict[object, object], value)
    if any(type(key) is not str for key in raw):
        raise wire.TapeError("panel criterion keys must be strings")
    item = cast(dict[str, object], raw)
    dimension = item.get("dimension")
    if dimension not in _DIMENSIONS:
        raise wire.TapeError(
            "panel criterion dimension must be 'raw_view', 'selected_view', "
            "or 'accepted_action'"
        )
    expected = _CRITERION_FIELDS | ({"fields"} if dimension == "selected_view" else set())
    if item.keys() != expected:
        raise wire.TapeError(
            "panel criterion must have exactly the documented fields "
            "(fields only for selected_view)"
        )
    cid = _label(item["id"], "panel criterion id")
    when = item["when"]
    if when not in _WHENS:
        raise wire.TapeError("panel criterion when must be 'different' or 'equal'")
    role = item["role"]
    if type(role) is not str or wire._ROLE.fullmatch(role) is None:
        raise wire.TapeError(f"panel criterion {cid!r} role must be a short ASCII identifier")
    fields: list[str] | None = None
    if dimension == "selected_view":
        raw_fields = item["fields"]
        if raw_fields is not None:
            if type(raw_fields) is not list or not raw_fields:
                raise wire.TapeError(
                    f"panel criterion {cid!r} fields must be null or a nonempty array"
                )
            try:
                Projection(cast(list[str], raw_fields))
            except ValueError as exc:
                raise wire.TapeError(
                    f"panel criterion {cid!r} fields are invalid: {exc}"
                ) from exc
            fields = cast(list[str], raw_fields)
    return {
        "id": cid,
        "dimension": cast(str, dimension),
        "when": cast(str, when),
        "role": role,
        "fields": fields,
    }


def _parse_design(
    value: object,
) -> tuple[list[dict[str, object]], dict[str, dict[str, str | None]]]:
    if type(value) is not dict or value.keys() != _DESIGN_FIELDS:
        raise wire.TapeError(
            "panel design must be an object with exactly the documented fields"
        )
    document = cast(dict[str, object], value)
    if document["schema"] != DESIGN_SCHEMA:
        raise wire.TapeError(f"invalid {DESIGN_SCHEMA} schema")

    raw_criteria = document["criteria"]
    if type(raw_criteria) is not list or not 1 <= len(raw_criteria) <= MAX_CRITERIA:
        raise wire.TapeError(f"panel design must contain 1 to {MAX_CRITERIA} criteria")
    criteria: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in raw_criteria:
        criterion = _criterion(raw)
        cid = cast(str, criterion["id"])
        if cid in seen:
            raise wire.TapeError(f"duplicate panel criterion id {cid!r}")
        seen.add(cid)
        criteria.append(criterion)
    criteria.sort(key=lambda item: cast(str, item["id"]))

    raw_cases = document["cases"]
    if type(raw_cases) is not dict or not 1 <= len(raw_cases) <= MAX_CASES:
        raise wire.TapeError(f"panel design must contain 1 to {MAX_CASES} cases")
    cases: dict[str, dict[str, str | None]] = {}
    for raw_name, raw_row in cast(dict[object, object], raw_cases).items():
        name = _label(raw_name, "panel case label")
        if type(raw_row) is not dict or raw_row.keys() != seen:
            raise wire.TapeError(
                f"panel case {name!r} must map exactly the declared criteria"
            )
        row: dict[str, str | None] = {}
        used: set[tuple[str, str, str]] = set()
        for criterion in criteria:
            cid = cast(str, criterion["id"])
            ref = cast(dict[str, object], raw_row)[cid]
            if ref is None:
                row[cid] = None
                continue
            check_id = _label(ref, f"panel case {name!r} check id")
            key = (check_id, cast(str, criterion["dimension"]), cast(str, criterion["when"]))
            if key in used:
                raise wire.TapeError(
                    f"panel case {name!r} reuses check {check_id!r} as a duplicate "
                    "alias of the same dimension and direction"
                )
            used.add(key)
            row[cid] = check_id
        cases[name] = row
    return criteria, dict(sorted(cases.items()))


def _count(value: object, what: str) -> int:
    if type(value) is not int or value < 0:
        raise wire.TapeError(f"{what} must be a nonnegative integer")
    return value


def _classify(report: dict[str, object], dimension: str) -> str:
    tally = report[dimension]
    if type(tally) is not dict:
        raise wire.TapeError(f"trajectory {dimension} tally must be an object")
    equal = _count(tally.get("equal"), "equal count")
    different = _count(tally.get("different"), "different count")
    unknown = _count(tally.get("unknown"), "unknown count")
    if different > 0:
        return "different"
    decisions = _count(report["role_decisions"], "role_decisions")
    if (
        report["status"] == "complete"
        and decisions > 0
        and unknown == 0
        and equal == decisions
    ):
        return "equal"
    return "unknown"


def _identity(report: dict[str, object]) -> tuple[str, list[str]]:
    digest = report.get("case_sha256")
    if not valid_sha256(digest):
        raise wire.TapeError("verified trajectory has no valid native case digest")
    ids: list[str] = []
    for key in ("source_native_trial_id", "target_native_trial_id"):
        trial = report.get(key)
        if type(trial) is not str or not trial:
            raise wire.TapeError("verified trajectory has no valid native trial id")
        ids.append(trial)
    return cast(str, digest), ids


class _Assessment(NamedTuple):
    criteria: list[dict[str, object]]
    rows: dict[str, dict[str, str | None]]
    endpoints: dict[str, tuple[str, str, str, str]]
    native_ids: set[str]
    report: dict[str, object]


def _assess_design(
    manifest: Path,
    design: Path,
    *,
    expect_sha256: str,
    select_cases: bool,
    expect_design_sha256: str | None = None,
) -> _Assessment:
    """Read one design snapshot, verify the native evidence, and optionally choose cases."""
    if not valid_sha256(expect_sha256):
        raise wire.TapeError("panel requires a lowercase SHA-256 study pin")
    raw_design = read_document(design)
    criteria, cases = _parse_design(raw_design)
    design_sha256 = wire.digest(raw_design)

    if expect_design_sha256 is not None and design_sha256 != expect_design_sha256:
        raise wire.TapeError("panel design does not match its expected digest")
    endpoints: dict[str, tuple[str, str, str, str]] = {}
    native_ids: set[str] = set()
    seen: dict[str, dict[str, object]] = {}

    def collect(check: Check, report: dict[str, object], _digest: str) -> None:
        if check["kind"] != "role-trajectory":
            return
        digest, trial_ids = _identity(report)
        endpoints[check["id"]] = (
            check["source"],
            check["source_order"],
            check["target"],
            check["target_order"],
        )
        native_ids.update(trial_ids)
        fields = report["fields"]
        seen[check["id"]] = {
            "role": check["role"],
            "fields": None if fields is None else list(cast(list[str], fields)),
            "case_sha256": digest,
            "trial_ids": trial_ids,
            "observed": {dim: _classify(report, dim) for dim in _DIMENSIONS},
        }

    summary = _verify_reports(manifest, expect_sha256=expect_sha256, on_check=collect)
    kinds = {
        cast(str, item["id"]): cast(str, item["kind"])
        for item in cast(list[dict[str, object]], summary["checks"])
    }

    case_digest: dict[str, str | None] = {}
    case_trials: dict[str, list[str]] = {}
    digest_owner: dict[str, str] = {}
    trial_owner: dict[str, str] = {}
    results: dict[str, dict[str, str]] = {}

    for name, row in cases.items():
        digests: set[str] = set()
        trials: set[str] = set()
        results[name] = {}
        for criterion in criteria:
            cid = cast(str, criterion["id"])
            check_id = row[cid]
            if check_id is None:
                results[name][cid] = "unknown"
                continue
            if check_id not in kinds:
                raise wire.TapeError(
                    f"panel case {name!r} references unknown check {check_id!r}"
                )
            info = seen.get(check_id)
            if info is None:
                raise wire.TapeError(
                    f"panel case {name!r} check {check_id!r} must be kind role-trajectory"
                )
            if info["role"] != criterion["role"]:
                raise wire.TapeError(
                    f"panel criterion {cid!r} role does not match check {check_id!r}"
                )
            if criterion["dimension"] == "selected_view":
                wanted = cast("list[str] | None", criterion["fields"])
                actual = cast("list[str] | None", info["fields"])
                if (wanted is None) != (actual is None) or (
                    wanted is not None
                    and actual is not None
                    and sorted(wanted) != sorted(actual)
                ):
                    raise wire.TapeError(
                        f"panel criterion {cid!r} fields do not match check {check_id!r}"
                    )
            digests.add(cast(str, info["case_sha256"]))
            trials.update(cast(list[str], info["trial_ids"]))
            observed = cast(dict[str, str], info["observed"])[
                cast(str, criterion["dimension"])
            ]
            if observed == "unknown":
                results[name][cid] = "unknown"
            elif observed == criterion["when"]:
                results[name][cid] = "witness"
            else:
                results[name][cid] = "opposite"
        if len(digests) > 1:
            raise wire.TapeError(f"panel case {name!r} mixes native case digests")
        digest = next(iter(digests), None)
        if digest is not None:
            other = digest_owner.setdefault(digest, name)
            if other != name:
                raise wire.TapeError(
                    f"panel cases {other!r} and {name!r} share one native case digest"
                )
        for trial in sorted(trials):
            other = trial_owner.setdefault(trial, name)
            if other != name:
                raise wire.TapeError(
                    f"native trial id is used under panel cases {other!r} and {name!r}"
                )
        case_digest[name] = digest
        case_trials[name] = sorted(trials)

    ids = [cast(str, criterion["id"]) for criterion in criteria]
    witnessed = {
        name: frozenset(cid for cid in ids if results[name][cid] == "witness")
        for name in cases
    }
    targets = sorted(cid for cid in ids if any(cid in found for found in witnessed.values()))
    selected = list(minimum_panel(witnessed)) if select_cases else []

    criterion_rows: list[dict[str, object]] = []
    for cid in ids:
        values = [results[name][cid] for name in cases]
        wit, opp, unk = (values.count(label) for label in ("witness", "opposite", "unknown"))
        state = "witnessed" if wit else "indeterminate" if unk else "opposite_only"
        criterion_rows.append(
            {
                "id": cid,
                "state": state,
                "witnesses": wit,
                "opposites": opp,
                "unknown": unk,
                "selected_witnesses": [name for name in selected if cid in witnessed[name]],
            }
        )

    report: dict[str, object] = {
        "schema": PANEL_SCHEMA,
        "study_sha256": summary["study_sha256"],
        "design_sha256": design_sha256,
        "case_count": len(cases),
        "criterion_count": len(criteria),
        "known_native_trials": len(trial_owner),
        "selection": {
            "cases": selected,
            "case_count": len(selected),
            "covered_criteria": targets,
            "not_witnessed": [cid for cid in ids if cid not in targets],
        },
        "criteria": criterion_rows,
        "cases": [
            {
                "id": name,
                "case_sha256": case_digest[name],
                "native_trial_ids": case_trials[name],
                "results": results[name],
            }
            for name in cases
        ],
    }
    return _Assessment(criteria, cases, endpoints, native_ids, report)


def panel_report(manifest: Path, design: Path, *, expect_sha256: str) -> dict[str, object]:
    """Verify a pinned study and choose the fewest cases witnessing every criterion."""
    return _assess_design(
        manifest, design, expect_sha256=expect_sha256, select_cases=True
    ).report
