"""Offline, deterministic recheck of an independent run against a pinned source panel.

Each design is read once. Both native studies are verified under separate pins; the
case selector runs only on the source.
Only the source's sorted baseline selection is compared; the candidate never reselects.
The report holds labels, digests and native trial ids only: no values, copies or paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from . import tape as wire
from .panel import _assess_design

__all__ = ["RECHECK_SCHEMA", "recheck_exit_code", "recheck_panel"]

RECHECK_SCHEMA = "seatlab.panel-recheck/v1"
_EXIT = {"matched": 0, "changed": 1, "incomplete": 3}


def _descriptor(criteria: list[dict[str, object]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for item in criteria:  # already sorted by id
        fields = cast("list[str] | None", item["fields"])
        out.append({**item, "fields": None if fields is None else sorted(fields)})
    return out


def _cases(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        cast(str, row["id"]): row for row in cast(list[dict[str, object]], report["cases"])
    }


def _state(source: str, candidate: str) -> str:
    if "unknown" in (source, candidate):
        return "unknown"
    return "stable" if source == candidate else "changed"


def recheck_panel(
    source: Path,
    candidate: Path,
    *,
    design: Path,
    candidate_design: Path,
    expect_sha256: str,
    expect_candidate_sha256: str,
    expect_design_sha256: str | None = None,
    expect_candidate_design_sha256: str | None = None,
) -> dict[str, object]:
    """Compare the source's baseline selection across two independently verified studies."""
    src = _assess_design(
        source,
        design,
        expect_sha256=expect_sha256,
        select_cases=True,
        expect_design_sha256=expect_design_sha256,
    )
    cand = _assess_design(
        candidate,
        candidate_design,
        expect_sha256=expect_candidate_sha256,
        select_cases=False,
        expect_design_sha256=expect_candidate_design_sha256,
    )
    if expect_sha256 == expect_candidate_sha256 or src.native_ids & cand.native_ids:
        raise wire.TapeError("candidate reuses the source study or a native role trial id")
    if _descriptor(src.criteria) != _descriptor(cand.criteria):
        raise wire.TapeError("candidate criteria do not match the source criteria")
    s_rep, c_rep = src.report, cand.report
    unknown = sorted(set(cand.rows) - set(src.rows))
    if unknown:
        raise wire.TapeError(f"candidate design has {len(unknown)} unknown case label(s)")
    ids = [cast(str, item["id"]) for item in src.criteria]

    s_cases, c_cases = _cases(s_rep), _cases(c_rep)
    for name, c_row in cand.rows.items():
        before, after = s_cases[name]["case_sha256"], c_cases[name]["case_sha256"]
        if before is not None and after is not None and before != after:
            raise wire.TapeError(f"candidate case {name!r} has a different native case")
        for cid in ids:
            old, new = src.rows[name][cid], c_row[cid]
            if new is None:
                continue
            if old != new or src.endpoints[old] != cand.endpoints[new]:
                raise wire.TapeError(
                    f"candidate case {name!r} changes the measured check or endpoints for {cid!r}"
                )

    selection = sorted(cast(list[str], cast(dict[str, object], s_rep["selection"])["cases"]))
    counts = {"entries": 0, "stable": 0, "changed": 0, "unknown": 0}
    out_cases: list[dict[str, object]] = []
    for name in selection:
        s_case = s_cases[name]
        c_case = c_cases.get(name)
        s_res = cast(dict[str, str], s_case["results"])
        c_res = cast(dict[str, str], c_case["results"]) if c_case is not None else {}
        criteria: dict[str, dict[str, str]] = {}
        for cid in ids:
            src = s_res.get(cid, "unknown")
            cand = c_res.get(cid, "unknown")
            status = _state(src, cand)
            criteria[cid] = {"source": src, "candidate": cand, "status": status}
            counts["entries"] += 1
            counts[status] += 1
        out_cases.append(
            {
                "id": name,
                "source_native_trial_ids": sorted(
                    cast(list[str], s_case["native_trial_ids"])
                ),
                "candidate_native_trial_ids": sorted(
                    cast(list[str], c_case["native_trial_ids"]) if c_case is not None else []
                ),
                "criteria": criteria,
            }
        )

    if counts["changed"]:
        status = "changed"
    elif counts["unknown"] or not selection:
        status = "incomplete"
    else:
        status = "matched"
    return {
        "schema": RECHECK_SCHEMA,
        "status": status,
        "source_study_sha256": s_rep["study_sha256"],
        "candidate_study_sha256": c_rep["study_sha256"],
        "source_design_sha256": s_rep["design_sha256"],
        "candidate_design_sha256": c_rep["design_sha256"],
        "selection": selection,
        "counts": counts,
        "cases": out_cases,
    }


def recheck_exit_code(report: dict[str, object]) -> int:
    """CLI exit status for a recheck report: 0 matched, 1 changed, 3 incomplete."""
    return _EXIT[cast(str, report["status"])]
