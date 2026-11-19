"""Pure compiler from a seatlab.panel-matrix/v1 document to a recipe, a design and a file map."""

from __future__ import annotations

import re
from typing import cast

from . import tape as wire
from .panel import DESIGN_SCHEMA, MAX_CASES, MAX_CRITERIA, _parse_design
from .projection import Projection
from .study_contract import MAX_CHECKS, RECIPE_SCHEMA, parse_recipe, validate_path

MATRIX_SCHEMA = "seatlab.panel-matrix/v1"
_LABEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,27}\Z")
_TOP = frozenset({"schema", "contrasts", "criteria", "cases"})
_CONTRAST = frozenset({"source", "target", "role"})
_CRITERION = frozenset({"id", "contrast", "dimension", "when"})

__all__ = ["MATRIX_SCHEMA", "compile_matrix"]


def _label(value: object, what: str) -> str:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise wire.TapeError(f"{what} must match [a-z0-9][a-z0-9_-]{{0,27}}")
    return value


def _mapping(value: object, what: str, low: int, high: int) -> dict[str, object]:
    if type(value) is not dict or not low <= len(value) <= high:
        raise wire.TapeError(f"matrix {what} must be an object with {low} to {high} entries")
    return cast(dict[str, object], value)


def _exact(
    item: object, required: frozenset[str], optional: frozenset[str], what: str
) -> dict[str, object]:
    if type(item) is not dict or not required <= item.keys() <= required | optional:
        raise wire.TapeError(f"matrix {what} must have exactly the documented fields")
    return cast(dict[str, object], item)


def compile_matrix(
    document: object,
) -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
    """Return (recipe, design, {output file: input file}) or raise wire.TapeError."""
    top = _exact(document, _TOP, frozenset(), "document")
    if top["schema"] != MATRIX_SCHEMA:
        raise wire.TapeError(f"invalid {MATRIX_SCHEMA} schema")

    raw_contrasts = _mapping(top["contrasts"], "contrasts", 1, MAX_CRITERIA)
    contrasts: dict[str, dict[str, object]] = {}
    arms: set[str] = set()
    measurements: dict[str, tuple[tuple[str, str], str, tuple[str, ...]]] = {}
    seen_measure: set[tuple[tuple[str, str], str, tuple[str, ...]]] = set()
    for name, raw in raw_contrasts.items():
        _label(name, "contrast label")
        item = _exact(raw, _CONTRAST, frozenset({"fields"}), f"contrast {name!r}")
        source = _label(item["source"], f"contrast {name!r} source arm")
        target = _label(item["target"], f"contrast {name!r} target arm")
        if source == target:
            raise wire.TapeError(f"contrast {name!r} must not compare an arm with itself")
        if type(item["role"]) is not str:
            raise wire.TapeError(f"contrast {name!r} role must be a string")
        fields = item.get("fields")
        if "fields" in item and (type(fields) is not list or not fields):
            raise wire.TapeError(f"contrast {name!r} fields must be a nonempty array")
        # Comparing equality or difference is symmetric in the two native arms.
        pair = (source, target) if source < target else (target, source)
        pointers = Projection(cast(list[str] | None, fields)).fields or ()
        measure = (pair, cast(str, item["role"]), pointers)
        measurements[name] = measure
        if measure in seen_measure:
            raise wire.TapeError(
                f"contrast {name!r} duplicates another contrast's measurement"
            )
        seen_measure.add(measure)
        arms.update((source, target))
        contrasts[name] = item

    raw_criteria = top["criteria"]
    if type(raw_criteria) is not list or not 1 <= len(raw_criteria) <= MAX_CRITERIA:
        raise wire.TapeError(f"matrix criteria must be an array of 1 to {MAX_CRITERIA} items")
    criteria: list[dict[str, object]] = []
    bound: list[tuple[str, str]] = []  # (criterion id, contrast) in declared order
    used: set[str] = set()
    alias: set[tuple[tuple[str, str], str, tuple[str, ...], str, str]] = set()
    for raw in raw_criteria:
        item = _exact(raw, _CRITERION, frozenset(), "criterion")
        cid, contrast = item["id"], item["contrast"]
        if type(cid) is not str:
            raise wire.TapeError("criterion id must be a string")
        if type(contrast) is not str or contrast not in contrasts:
            raise wire.TapeError(f"criterion {cid!r} names an undeclared contrast")
        if type(item["dimension"]) is not str or type(item["when"]) is not str:
            raise wire.TapeError(f"criterion {cid!r} needs a dimension and direction")
        pair, role, pointers = measurements[contrast]
        dim = cast(str, item["dimension"])
        key = (
            pair,
            role,
            pointers if dim == "selected_view" else (),
            dim,
            cast(str, item["when"]),
        )
        if key in alias:
            raise wire.TapeError(
                f"criterion {cid!r} duplicates another criterion's measurement"
            )
        alias.add(key)
        used.add(contrast)
        entry: dict[str, object] = {
            "id": cid,
            "dimension": item["dimension"],
            "when": item["when"],
            "role": contrasts[contrast]["role"],
        }
        if item["dimension"] == "selected_view":
            fields = contrasts[contrast].get("fields")
            entry["fields"] = None if fields is None else list(cast(list[str], fields))
        criteria.append(entry)
        bound.append((cid, contrast))
    if used != contrasts.keys():
        raise wire.TapeError("every declared contrast must be used by a criterion")

    cases = _mapping(top["cases"], "cases", 1, MAX_CASES)
    for case in cases:
        _label(case, "case label")
    inputs: dict[str, str] = {}  # casefolded input dir -> "case/arm"
    checks: list[dict[str, object]] = []
    design_cases: dict[str, dict[str, object]] = {}
    files: dict[str, str] = {}
    for case in sorted(cases):
        cell = _mapping(cases[case], f"case {case!r}", len(arms), len(arms))
        if cell.keys() != arms:
            raise wire.TapeError(f"case {case!r} must name exactly the arms {sorted(arms)}")
        for arm in sorted(arms):
            if cell[arm] is None:
                continue
            folder = validate_path(cell[arm])
            folded = folder.casefold()
            if folded in inputs:
                raise wire.TapeError(
                    f"input folder of {case}/{arm} also belongs to {inputs[folded]}"
                )
            inputs[folded] = f"{case}/{arm}"
        made: dict[str, str | None] = {}
        for name in sorted(contrasts):
            src, dst = (cast(str, contrasts[name][k]) for k in ("source", "target"))
            if cell[src] is None or cell[dst] is None:
                made[name] = None
                continue
            if len(checks) >= MAX_CHECKS:
                raise wire.TapeError(f"matrix compiles to more than {MAX_CHECKS} checks")
            check: dict[str, object] = {"id": f"{case}.{name}", "kind": "role-trajectory"}
            for end, arm in (("source", src), ("target", dst)):
                for stem, leaf in ((end, "episodes.jsonl"), (f"{end}_order", "order.json")):
                    out = f"cells/{case}/{arm}/{leaf}"
                    check[stem] = out
                    files[out] = f"{cast(str, cell[arm])}/{leaf}"
            check["role"] = contrasts[name]["role"]
            if "fields" in contrasts[name]:
                check["fields"] = list(cast(list[str], contrasts[name]["fields"]))
            checks.append(check)
            made[name] = cast(str, check["id"])
        design_cases[case] = {cid: made[contrast] for cid, contrast in bound}
    if not checks:
        raise wire.TapeError("matrix compiles to no measured checks: no case has both arms")

    recipe: dict[str, object] = {"schema": RECIPE_SCHEMA, "checks": checks}
    design: dict[str, object] = {
        "schema": DESIGN_SCHEMA,
        "criteria": sorted(criteria, key=lambda item: cast(str, item["id"])),
        "cases": design_cases,
    }
    parse_recipe(recipe)
    _parse_design(design)
    for out, source in files.items():
        validate_path(out)
        validate_path(source)
    return recipe, design, dict(sorted(files.items()))
