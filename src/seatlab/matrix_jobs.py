"""Pure planner: a portable native-job worklist and a ready-to-fill candidate matrix.

Inputs are the already validated matrix, compiled panel design and ``panel_report`` result.
Nothing here reads files, runs commands, or reserves jobs.
"""

from __future__ import annotations

import copy
from typing import cast

__all__ = ["WORKLIST_SCHEMA", "plan_native_jobs"]

WORKLIST_SCHEMA = "seatlab.native-worklist/v1"


def plan_native_jobs(
    matrix: dict[str, object],
    design: dict[str, object],
    report: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Return (worklist, candidate matrix template) for the report's selected cases."""
    contrasts = cast(dict[str, dict[str, object]], matrix["contrasts"])
    contrast_of = {
        cast(str, item["id"]): cast(str, item["contrast"])
        for item in cast(list[dict[str, object]], matrix["criteria"])
    }
    design_cases = cast(dict[str, dict[str, str | None]], design["cases"])
    selected = sorted(cast(list[str], cast(dict[str, object], report["selection"])["cases"]))

    needed: dict[tuple[str, str], list[str]] = {}
    unmeasured: list[dict[str, str]] = []
    for case in selected:
        row = design_cases[case]
        for criterion in sorted(row):
            if row[criterion] is None:
                unmeasured.append({"case": case, "criterion": criterion})
                continue
            contrast = contrasts[contrast_of[criterion]]
            for arm in (cast(str, contrast["source"]), cast(str, contrast["target"])):
                needed.setdefault((case, arm), []).append(criterion)

    jobs = [
        {"id": f"{case}.{arm}", "case": case, "arm": arm, "criteria": ids}
        for (case, arm), ids in sorted(needed.items())
    ]
    worklist: dict[str, object] = {
        "schema": WORKLIST_SCHEMA,
        "source_study_sha256": report["study_sha256"],
        "source_design_sha256": report["design_sha256"],
        "selected_cases": selected,
        "jobs": jobs,
        "unmeasured": unmeasured,
    }

    cases = cast(dict[str, dict[str, object]], matrix["cases"])
    template: dict[str, object] = {
        "schema": matrix["schema"],
        "contrasts": copy.deepcopy(contrasts),
        "criteria": sorted(
            copy.deepcopy(cast(list[dict[str, str]], matrix["criteria"])),
            key=lambda item: item["id"],
        ),
        "cases": {
            case: {
                arm: f"jobs/{case}.{arm}" if (case, arm) in needed else None
                for arm in sorted(cells)
            }
            for case, cells in sorted(cases.items())
        },
    }
    return worklist, template
