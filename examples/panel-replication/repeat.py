"""Repeat the selected held-out MPE2 panel natively and compare it to the original.

python repeat.py BASE --adapter ADAPTER --harbor ABS --root NEW --public NEW \
    --expect-sha256 64HEX

BASE is only read as data (JSON and projection files); no BASE code runs. ADAPTER
is the trusted pinned pettingzoo-seats adapter. Every selected cell runs once,
serially, with no retry. Outcomes (incomplete, disagreed) exit 0; only setup or
invalid input exits nonzero. Public output holds fixed tokens, numbers, UUIDs and
validated projection files only.

"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path[:] = [str(_HERE)] + [p for p in sys.path[1:] if p not in ("", ".")]

from compare import compare_cell  # noqa: E402
from seatlab.panel import panel_report  # noqa: E402
from seatlab.study import seal_study  # noqa: E402
from seatlab.tape import digest  # noqa: E402

SCHEMA = "seatlab.panel-replication/v1"
TEAMS = ("plain-plain", "rotate-rotate", "plain-rotate", "rotate-plain", "plain-blind")
FILES = ("episodes.jsonl", "order.json", "outcomes.json")
SEED = re.compile(r"s(10[1-9]|11[0-2])")
PATH = re.compile(r"cells/(s[0-9]{3}-[a-z]+-[a-z]+)/(episodes\.jsonl|order\.json)")
RUN_TIMEOUT, EXPORT_TIMEOUT = 360, 60
ROLES = ("p0", "p1")


class Setup(Exception):
    """Setup or invalid input; the message is a fixed token."""


def _need(ok: bool, token: str) -> None:
    if not ok:
        raise Setup(token)


def _json(path: Path, token: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Setup(token) from None


def _sub(cmd: list[str], timeout: int) -> str | None:
    """Run one child quietly. Return None on exit 0, else a fixed token."""
    try:
        done = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except OSError:
        return "failed"
    return None if done.returncode == 0 else "failed"


# ---- panel report reading ---------------------------------------------------


def _results(report: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {case["id"]: case["results"] for case in report["cases"]}


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "source_trial_id": None,
        "target_trial_id": None,
        "case_sha256": None,
        "world_steps": None,
        "order_equal": None,
        "returned": None,
        "max_return_gap": None,
        "returns": None,
        "roles": None,
    }


def _safe_dir(d: Path, names: tuple[str, ...]) -> bool:
    if d.is_symlink() or not d.is_dir():
        return False
    return all((d / n).is_file() and not (d / n).is_symlink() for n in names)


def _complete(rec: dict[str, Any]) -> bool:
    roles = rec["roles"]
    return (
        rec["status"] == "measured"
        and isinstance(roles, dict)
        and all(roles.get(r) and roles[r]["decisions"] > 0 for r in ROLES)
    )


def _equal(rec: dict[str, Any], key: str) -> bool:
    return _complete(rec) and all(
        rec["roles"][r][key] == rec["roles"][r]["decisions"] for r in ROLES
    )


# ---- main -----------------------------------------------------------------------


def _validate_args(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path]:
    base, adapter = args.base.resolve(), args.adapter.resolve()
    _need(base.is_dir() and adapter.is_dir(), "input-missing")
    _need(bool(re.fullmatch(r"[0-9a-f]{64}", args.expect_sha256)), "bad-pin")
    harbor = args.harbor
    _need(
        harbor.is_absolute() and harbor.is_file() and os.access(harbor, os.X_OK), "bad-harbor"
    )
    root, public = args.root.resolve(), args.public.resolve()
    _need(
        not os.path.lexists(args.root) and not os.path.lexists(args.public), "output-exists"
    )
    _need(
        root != public and root not in public.parents and public not in root.parents,
        "outputs-overlap",
    )
    _need(root.parent.is_dir() and public.parent.is_dir(), "output-parent-missing")
    study = adapter / "communication-experiment" / "study.py"
    _need(study.is_file() and (adapter / "export.py").is_file(), "adapter-incomplete")
    return base, adapter, harbor, root, public


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base", type=Path, metavar="BASE")
    ap.add_argument("--adapter", required=True, type=Path)
    ap.add_argument("--harbor", required=True, type=Path)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--public", required=True, type=Path)
    ap.add_argument("--expect-sha256", required=True)
    args = ap.parse_args(argv)
    try:
        return _run(args)
    except Setup as exc:
        print(f"repeat.py: setup failed: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    base, adapter, harbor, root, public = _validate_args(args)
    study_py, export_py = (
        adapter / "communication-experiment" / "study.py",
        adapter / "export.py",
    )
    py = os.path.abspath(sys.executable)

    # Re-verify the immutable original against the pin, then read its panel.
    try:
        report = panel_report(
            base / "study.json", base / "panel-design.json", expect_sha256=args.expect_sha256
        )
    except Setup:
        raise
    except (OSError, ValueError):
        raise Setup("original-not-verified") from None
    original = _results(report)
    _need(bool(original), "original-panel-unreadable")
    selected = report["selection"]["cases"]
    _need(
        1 <= len(selected) <= 3 and all(SEED.fullmatch(s) for s in selected),
        "selection-out-of-range",
    )
    _need(all(s in original for s in selected), "selection-unknown-case")

    design = _json(base / "panel-design.json", "design-unreadable")
    recipe = _json(base / "recipe.json", "recipe-unreadable")
    _need(
        isinstance(design, dict) and isinstance(design.get("cases"), dict), "design-invalid"
    )
    _need(
        isinstance(recipe, dict) and isinstance(recipe.get("checks"), list), "recipe-invalid"
    )
    by_id = {c.get("id"): c for c in recipe["checks"] if isinstance(c, dict)}
    cells = [(f"{s}-{t}", s, t) for s in selected for t in TEAMS]
    allowed = {c for c, _, _ in cells}
    wanted: dict[str, dict[str, Any]] = {}
    for s in selected:
        mapping = design["cases"].get(s)
        _need(
            isinstance(mapping, dict) and set(mapping) == set(original[s]),
            "design-case-invalid",
        )
        for cid in {v for v in mapping.values() if v is not None}:
            chk = by_id.get(cid)
            _need(
                isinstance(chk, dict) and chk.get("kind") == "role-trajectory",
                "check-missing",
            )
            used: set[str] = set()
            for key, name in (
                ("source", "episodes.jsonl"),
                ("target", "episodes.jsonl"),
                ("source_order", "order.json"),
                ("target_order", "order.json"),
            ):
                m = PATH.fullmatch(str(chk.get(key)))
                _need(
                    bool(m) and m.group(2) == name and m.group(1) in allowed,
                    "check-path-invalid",
                )
                used.add(m.group(1))  # type: ignore[union-attr]
            wanted[cid] = {"check": chk, "cells": used}

    # Generate configs only, then run each selected cell once, serially.
    _need(
        _sub(
            [py, str(study_py), "prepare", str(root), "--python", py, "--cohort", "held-out"],
            600,
        )
        is None,
        "prepare-failed",
    )
    plan = _json(root / "plan.json", "plan-unreadable")
    trial_of = {
        c.get("id"): c.get("trial_name") for c in plan.get("cells", []) if isinstance(c, dict)
    }
    out = root / "output" / "cells"
    out.mkdir(parents=True)
    records: dict[str, dict[str, Any]] = {}
    for cell, _, _ in cells:
        trial = root / "trials" / str(trial_of.get(cell))
        bad = _sub(
            [py, str(study_py), "run", str(root), cell, "--harbor", str(harbor)], RUN_TIMEOUT
        )
        if bad:
            records[cell] = _unavailable("run-" + bad)
            continue
        if _sub([py, str(export_py), str(trial), str(out / cell)], EXPORT_TIMEOUT):
            records[cell] = _unavailable("export-failed")
            continue
        orig = base / "cells" / cell
        if not _safe_dir(orig, FILES) or not _safe_dir(out / cell, FILES):
            records[cell] = _unavailable("projection-unsafe")
            continue
        records[cell] = compare_cell(orig, out / cell)

    # A trial UUID may appear only once; duplicates are never counted.
    original_ids = {u for case in report["cases"] for u in case["native_trial_ids"]}
    seen: dict[str, list[str]] = {}
    for cell, rec in records.items():
        if rec["target_trial_id"] in original_ids:
            records[cell] = _unavailable("reused-original-trial-id")
        elif rec["status"] == "measured" and rec["target_trial_id"]:
            seen.setdefault(rec["target_trial_id"], []).append(cell)
    for group in seen.values():
        for cell in group if len(group) > 1 else []:
            records[cell] = _unavailable("duplicate-trial-id")

    # Publish only measured, validated projections.
    public.mkdir()
    published = {c for c, r in records.items() if r["status"] == "measured"}
    for cell in sorted(published):
        (public / "cells" / cell).mkdir(parents=True)
        for name in FILES:
            shutil.copyfile(out / cell / name, public / "cells" / cell / name)

    # Reduced recipe: only checks whose fresh inputs are all published.
    live = sorted(c for c, w in wanted.items() if w["cells"] <= published)
    fresh_design = {
        "schema": design.get("schema"),
        "criteria": design.get("criteria"),
        "cases": {
            s: {k: (v if v in live else None) for k, v in design["cases"][s].items()}
            for s in selected
        },
    }
    (public / "panel-design.json").write_text(
        json.dumps(fresh_design, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    sealed, fresh_sha, fresh = False, None, {}
    if live:
        doc = {"schema": recipe.get("schema"), "checks": [wanted[c]["check"] for c in live]}
        (public / "recipe.json").write_text(
            json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        try:
            fresh_sha = digest(seal_study(public / "recipe.json", public / "study.json"))
            fresh = _results(
                panel_report(
                    public / "study.json",
                    public / "panel-design.json",
                    expect_sha256=fresh_sha,
                )
            )
            sealed = True
        except (OSError, ValueError):
            sealed, fresh_sha, fresh = False, None, {}
            (public / "study.json").unlink(missing_ok=True)

    # Pre (original) vs post (fresh) witness states for every criterion.
    criteria: dict[str, Any] = {}
    tally = {"agree": 0, "disagree": 0, "unknown": 0}
    for s in selected:
        criteria[s] = {}
        for crit, pre in sorted(original[s].items()):
            post = fresh.get(s, {}).get(crit, "unknown") if sealed else "unknown"
            if "unknown" in (pre, post):
                verdict = "unknown"
            else:
                verdict = "agree" if pre == post else "disagree"
            tally[verdict] += 1
            criteria[s][crit] = {"pre": pre, "post": post, "verdict": verdict}

    ids = [r["target_trial_id"] for r in records.values() if r["status"] == "measured"]
    counts = {
        "planned_cells": len(cells),
        "valid_fresh_cells": len(published),
        "distinct_fresh_trial_ids": len(set(ids)),
        "complete_cells": sum(_complete(r) for r in records.values()),
        "actions_exact_cells": sum(_equal(r, "actions_equal") for r in records.values()),
        "views_exact_cells": sum(_equal(r, "views_equal") for r in records.values()),
        "order_equal_cells": sum(r["order_equal"] is True for r in records.values()),
        "returns_within_1e-6_cells": sum(r["returned"] is True for r in records.values()),
        "criteria_total": len(criteria) and sum(tally.values()),
        "criteria_agree": tally["agree"],
        "criteria_disagree": tally["disagree"],
        "criteria_unknown": tally["unknown"],
    }
    receipt = {
        "schema": SCHEMA,
        "original_study_sha256": args.expect_sha256,
        "fresh_study_sha256": fresh_sha,
        "fresh_study_sealed": sealed,
        "selection": selected,
        "counts": counts,
        "cells": {
            c: {"seed": s, "team": t, "published": c in published, **records[c]}
            for c, s, t in cells
        },
        "checks": {c: (c in live) for c in sorted(wanted)},
        "criteria": criteria,
    }
    (public / "receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"schema": SCHEMA, "selection": selected, "sealed": sealed, **counts},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
