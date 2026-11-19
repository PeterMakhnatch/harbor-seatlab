"""Join a seatlab.study-relation/v1 derivative file to native trials in stock Eval Lab.

Read-only. This runs in the Eval Lab Python environment and imports
``evallab.results.load_trial``; it writes no checkout, catalog, or Parquet store,
and it reads native rewards only through that loader.

The relation rows are a derivative file, not a sealed study. This bridge records
the file digest and the recorded study digest, and re-verifies neither the study
pin nor the native reward computation. Run ``seatlab study verify`` for the pin.

Usage::

    python join.py RELATIONS.jsonl --trials-dir NATIVE_ROOT [--trials-dir ROOT ...]
        [--trial NATIVE_TRIAL ...] [--expect-study-sha256 HEX]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

RELATION_SCHEMA = "seatlab.study-relation/v1"
REPORT_SCHEMA = "seatlab.eval-lab-join/v1"
_OUTCOME_SQL = """
SELECT kind, status, outcome, count(*) AS relations
FROM relations GROUP BY kind, status, outcome ORDER BY kind, status, outcome
"""
_NATIVE_SQL = """
SELECT count(*) AS found_ids,
       count(*) FILTER (WHERE reward IS NOT NULL) AS scored_ids,
       avg(reward) FILTER (WHERE reward IS NOT NULL) AS mean_score,
       count(*) FILTER (WHERE job IS NOT NULL) AS job_trials,
       count(*) FILTER (WHERE job IS NULL) AS standalone_trials,
       (SELECT count(DISTINCT job) FROM native WHERE referenced AND job IS NOT NULL)
           AS distinct_jobs,
       coalesce(sum(executions), 0) AS executions
FROM native WHERE referenced
"""
_UNITS: dict[str, str | None] = {
    "team-alignment": "world_step",
    "role-trajectory": "world_step",
    "alignment": "role_turn",
    "calibration": None,
}
_FIELDS = frozenset(
    {
        "schema",
        "study_sha256",
        "report_sha256",
        "check_id",
        "kind",
        "pair_index",
        "case_sha256",
        "source_native_trial_id",
        "target_native_trial_id",
        "role",
        "status",
        "boundary_unit",
        "boundary",
        "matched_prefix",
        "native_action_changed",
        "reproduced",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CHECK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_TOKEN_FIELDS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")


@dataclass(frozen=True)
class _Native:
    """One loaded native trial, reduced to identifiers, executions, reward and usage."""

    id: str
    job: str | None
    executions: tuple[str | None, ...]
    reward: float | None
    usage: dict[str, int | float | None]
    declared: dict[str, int]


def _canonical(value: object, where: str) -> str:
    if isinstance(value, str) and len(value) == 36:
        try:
            if str(UUID(value)) == value:
                return value
        except ValueError:
            pass
    raise ValueError(f"{where} requires a canonical lowercase UUID")


def _optional_uuid(value: object, where: str) -> str | None:
    return None if value is None else _canonical(value, where)


def _digest(value: object, where: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{where} requires a lowercase 64-character SHA-256 digest")
    return value


def _optional_int(value: object, where: str, minimum: int) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < minimum:
        raise ValueError(f"{where} requires an integer of at least {minimum}")
    return value


def _optional_bool(value: object, where: str) -> bool | None:
    if value is not None and type(value) is not bool:
        raise ValueError(f"{where} requires a boolean or null")
    return value


def _relation(
    raw: Any, number: int, pair_counts: dict[str, int], studies: set[str]
) -> dict[str, Any]:
    where = f"line {number}"
    if not isinstance(raw, dict) or raw.keys() != _FIELDS:
        raise ValueError(f"{where}: fields do not match {RELATION_SCHEMA}")
    if raw["schema"] != RELATION_SCHEMA:
        raise ValueError(f"{where}: unsupported relation schema {raw['schema']!r}")
    studies.add(_digest(raw["study_sha256"], f"{where} study_sha256"))
    _digest(raw["report_sha256"], f"{where} report_sha256")
    check = raw["check_id"]
    if not isinstance(check, str) or _CHECK_ID.fullmatch(check) is None:
        raise ValueError(f"{where}: check_id must be a portable ASCII label")
    kind = raw["kind"]
    if kind not in _UNITS:
        raise ValueError(f"{where}: unsupported relation kind {kind!r}")
    if raw["boundary_unit"] != _UNITS[kind]:
        raise ValueError(f"{where}: boundary_unit does not match kind {kind!r}")
    if type(raw["pair_index"]) is not int or raw["pair_index"] < 1:
        raise ValueError(f"{where}: pair_index requires an integer of at least 1")
    index = raw["pair_index"]
    expected = pair_counts.get(check, 0) + 1
    if index != expected:
        raise ValueError(
            f"{where}: pair_index for check {check!r} is {index}, expected {expected} "
            "in file order"
        )
    pair_counts[check] = index
    role = raw["role"]
    if kind in ("alignment", "role-trajectory"):
        if not isinstance(role, str) or not role:
            raise ValueError(f"{where}: {kind} requires a role label")
    elif role is not None:
        raise ValueError(
            f"{where}: only role alignment and role trajectory relations carry a role"
        )
    if not isinstance(raw["status"], str) or not raw["status"]:
        raise ValueError(f"{where}: status must be a non-empty string")
    _digest(raw["case_sha256"], f"{where} case_sha256")
    source = _optional_uuid(raw["source_native_trial_id"], f"{where} source side")
    target = _optional_uuid(raw["target_native_trial_id"], f"{where} target side")
    if source is not None and source == target:
        raise ValueError(f"{where}: both sides use the same native trial UUID")
    boundary = _optional_int(raw["boundary"], f"{where} boundary", 1)
    prefix = _optional_int(raw["matched_prefix"], f"{where} matched_prefix", 0)
    changed = _optional_bool(raw["native_action_changed"], f"{where} native_action_changed")
    reproduced = _optional_bool(raw["reproduced"], f"{where} reproduced")
    if kind == "calibration":
        if boundary is not None or prefix is not None:
            raise ValueError(f"{where}: calibration relations carry no boundary or prefix")
    elif reproduced is not None or (changed is not None and kind != "role-trajectory"):
        raise ValueError(f"{where}: only calibration relations carry replay outcomes")
    elif kind == "role-trajectory" and prefix is not None:
        raise ValueError(f"{where}: role-trajectory relations carry no matched prefix")
    return {
        "check_id": check,
        "report_sha256": raw["report_sha256"],
        "pair_index": index,
        "kind": kind,
        "status": raw["status"],
        "role": role,
        "case_sha256": raw["case_sha256"],
        "boundary_unit": raw["boundary_unit"],
        "boundary": boundary,
        "matched_prefix": prefix,
        "native_action_changed": changed,
        "reproduced": reproduced,
        "source_native_trial_id": source,
        "target_native_trial_id": target,
    }


def _read_relations(path: Path) -> tuple[list[dict[str, Any]], str, str]:
    data = path.read_bytes()
    pair_counts: dict[str, int] = {}
    studies: set[str] = set()
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line:
            raise ValueError(f"line {number}: blank line is not a relation")
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {number}: invalid JSON: {error}") from error
        rows.append(_relation(raw, number, pair_counts, studies))
    if not rows:
        raise ValueError("relations file has no rows")
    if len(studies) != 1:
        raise ValueError("relation rows name more than one study_sha256")
    return rows, studies.pop(), hashlib.sha256(data).hexdigest()


def _contexts(result: dict[str, Any]) -> list[dict[str, Any]]:
    seats = result.get("seat_results")
    if isinstance(seats, list) and seats:
        return [
            seat["agent_result"]
            for seat in seats
            if isinstance(seat, dict) and isinstance(seat.get("agent_result"), dict)
        ]
    steps = result.get("step_results")
    if isinstance(steps, list) and steps:
        return [
            step["agent_result"]
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("agent_result"), dict)
        ]
    agent = result.get("agent_result")
    return [agent] if isinstance(agent, dict) else []


def _executions(result: dict[str, Any]) -> tuple[str | None, ...]:
    seats = result.get("seat_results")
    if isinstance(seats, list) and seats:
        return tuple(
            seat["seat"] if isinstance(seat.get("seat"), str) else None
            for seat in seats
            if isinstance(seat, dict)
        )
    steps = result.get("step_results")
    if isinstance(steps, list) and steps:
        return tuple(None for _ in steps)
    return (None,)


def _usage(
    contexts: Iterable[dict[str, Any]],
) -> tuple[dict[str, int | float | None], dict[str, int]]:
    """Sum each native metric from declared contexts; absent stays unknown, never zero."""
    rows = list(contexts)
    totals: dict[str, int | float | None] = {}
    declared: dict[str, int] = {}
    for field in _TOKEN_FIELDS:
        values = [
            context[field] for context in rows if type(context.get(field)) in (int, float)
        ]
        totals[field] = sum(values) if values else None
        declared[field] = len(values)
    return totals, declared


def _native(record: Any) -> _Native:
    result = record.result
    if not isinstance(result.get("task_name"), str) or not isinstance(
        result.get("trial_name"), str
    ):
        raise ValueError(
            "directory is not a native Harbor trial (no task_name and trial_name)"
        )
    trial_id = _canonical(result.get("id"), "native result.json id")
    config = result.get("config")
    job = config.get("job_id") if isinstance(config, dict) else None
    usage, declared = _usage(_contexts(result))
    return _Native(
        id=trial_id,
        job=None if job is None else _canonical(job, f"trial {trial_id} config job_id"),
        executions=_executions(result),
        reward=record.primary_reward,
        usage=usage,
        declared=declared,
    )


def _load_native(
    trial_dirs: Sequence[Path], trials: Sequence[Path]
) -> tuple[dict[str, list[_Native]], int]:
    from evallab.results import load_trial

    paths: list[Path] = []
    skipped = 0
    for root in trial_dirs:
        if not root.is_dir():
            raise ValueError(f"--trials-dir is not an existing directory: {root}")
        children = [
            child
            for child in sorted(root.iterdir())
            if child.is_dir() and (child / "result.json").is_file()
        ]
        if children:
            paths.extend(children)
            skipped += len(list(root.iterdir())) - len(children)
        elif (root / "result.json").is_file():
            paths.append(root)
        else:
            raise ValueError(f"--trials-dir holds no native trial directories: {root}")
    for path in trials:
        if not path.is_dir() or not (path / "result.json").is_file():
            raise ValueError(f"--trial is not an existing native trial directory: {path}")
        paths.append(path)

    index: dict[str, list[_Native]] = defaultdict(list)
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        native = _native(load_trial(resolved))
        if native not in index[native.id]:
            index[native.id].append(native)
    return index, skipped


def _side(value: str | None, index: dict[str, list[_Native]]) -> tuple[str, _Native | None]:
    if value is None:
        return "registered-missing", None
    found = index.get(value, ())
    if not found:
        return "native-missing", None
    if len(found) > 1:
        return "ambiguous", None
    return "found", found[0]


def _outcome(
    source_state: str, target_state: str, source: _Native | None, target: _Native | None
) -> tuple[str, str | None]:
    states = (source_state, target_state)
    for state in ("ambiguous", "native-missing"):
        if state in states:
            return "unknown", state
    if "registered-missing" in states:
        return "unknown", "registered-missing-side"
    assert source is not None and target is not None
    if source.reward is None or target.reward is None:
        return "unknown", "score-not-recorded"
    return ("equal" if source.reward == target.reward else "different"), None


def _aggregate(
    rows: list[dict[str, Any]], natives: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE relations(kind VARCHAR, status VARCHAR, outcome VARCHAR)")
        con.executemany(
            "INSERT INTO relations VALUES (?, ?, ?)",
            [(row["kind"], row["status"], row["outcome"]) for row in rows],
        )
        outcomes = [
            {"kind": row[0], "status": row[1], "outcome": row[2], "relations": row[3]}
            for row in con.execute(_OUTCOME_SQL).fetchall()
        ]
        con.execute(
            "CREATE TABLE native(referenced BOOLEAN, reward DOUBLE, job VARCHAR, "
            "executions BIGINT)"
        )
        con.executemany(
            "INSERT INTO native VALUES (?, ?, ?, ?)",
            [
                (
                    row["referenced"],
                    row["reward"],
                    row["job"],
                    len(row["executions"]),
                )
                for row in natives
            ],
        )
        cursor = con.execute(_NATIVE_SQL)
        summary = dict(zip((column[0] for column in cursor.description), cursor.fetchone()))
    finally:
        con.close()
    return outcomes, summary


def _join(relations: list[dict[str, Any]], index: dict[str, list[_Native]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    referenced: set[str] = set()
    registered_missing = 0
    for relation in relations:
        source, source_native = _side(relation["source_native_trial_id"], index)
        target, target_native = _side(relation["target_native_trial_id"], index)
        registered_missing += source == "registered-missing"
        registered_missing += target == "registered-missing"
        referenced.update(
            value
            for value in (
                relation["source_native_trial_id"],
                relation["target_native_trial_id"],
            )
            if value is not None
        )
        outcome, reason = _outcome(source, target, source_native, target_native)
        rows.append(
            {
                **relation,
                "source_state": source,
                "target_state": target,
                "outcome": outcome,
                "unknown_reason": reason,
            }
        )

    found = {value: index[value][0] for value in referenced if len(index.get(value, ())) == 1}
    ambiguous = sorted(value for value in referenced if len(index.get(value, ())) > 1)
    missing = sorted(value for value in referenced if not index.get(value))
    natives = [
        {
            "referenced": group[0].id in referenced,
            "reward": group[0].reward,
            "job": group[0].job,
            "executions": group[0].executions,
        }
        for group in index.values()
        if len(group) == 1
    ]
    outcomes, summary = _aggregate(rows, natives)

    role_counts: Counter[str] = Counter()
    single = 0
    usage: dict[str, dict[str, int | float | None]] = {
        field: {"known_ids": 0, "partial_ids": 0, "unknown_ids": 0, "total": None}
        for field in _TOKEN_FIELDS
    }
    for native in found.values():
        for role in native.executions:
            if role is None:
                single += 1
            else:
                role_counts[role] += 1
        for field, cell in usage.items():
            value = native.usage[field]
            declared = native.declared[field]
            if value is None:
                cell["unknown_ids"] += 1
                continue
            cell["known_ids"] += 1
            if declared < len(native.executions):
                cell["partial_ids"] += 1
            cell["total"] = value if cell["total"] is None else cell["total"] + value
    return {
        "coverage": {
            "relation_rows": len(rows),
            "registered_missing_sides": registered_missing,
            "referenced": len(referenced),
            "found": len(found),
            "missing": len(missing),
            "ambiguous": len(ambiguous),
            "distinct_jobs": summary["distinct_jobs"] or 0,
            "job_trials": summary["job_trials"],
            "standalone_trials": summary["standalone_trials"],
            "executions": summary["executions"],
            "single_agent_executions": single,
            "role_executions": dict(sorted(role_counts.items())),
        },
        "usage": usage,
        "paired": {
            "found_ids": summary["found_ids"],
            "scored_ids": summary["scored_ids"],
            "mean_score": summary["mean_score"],
            "unscored_ids": summary["found_ids"] - summary["scored_ids"],
        },
        "outcomes": outcomes,
        "missing_native_ids": missing,
        "ambiguous_native_ids": ambiguous,
        "rows": rows,
        "referenced": referenced,
    }


def _report(args: argparse.Namespace) -> dict[str, Any]:
    relations, study_sha256, relations_sha256 = _read_relations(args.relations)
    expect = args.expect_study_sha256
    if expect is not None and expect != study_sha256:
        raise ValueError(f"recorded study digest {study_sha256} does not match pin {expect}")
    index, skipped = _load_native(args.trials_dir or (), args.trial or ())
    joined = _join(relations, index)
    referenced = joined.pop("referenced")
    unreferenced = sorted(
        {
            native.id
            for group in index.values()
            for native in group
            if native.id not in referenced
        }
    )
    joined["coverage"]["unreferenced"] = len(unreferenced)
    joined["coverage"]["skipped_entries"] = skipped
    return {
        "schema": REPORT_SCHEMA,
        "relations": {
            "sha256": relations_sha256,
            "study_sha256": study_sha256,
            "expect_study_sha256": expect,
            "checks": len({row["check_id"] for row in relations}),
            "verification": "not performed: derivative rows, not a sealed study",
            "rows": joined.pop("rows"),
        },
        **joined,
        "unreferenced_native_ids": unreferenced,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("relations", type=Path, help="seatlab.study-relation/v1 JSONL")
    parser.add_argument(
        "--trials-dir",
        action="append",
        type=Path,
        help="directory of native trial directories; repeatable",
    )
    parser.add_argument(
        "--trial",
        action="append",
        type=Path,
        help="explicit native trial directory; repeatable",
    )
    parser.add_argument(
        "--expect-study-sha256",
        help="trusted study digest already verified with `seatlab study verify`",
    )
    args = parser.parse_args(argv)
    if not args.trials_dir and not args.trial:
        parser.error("provide --trials-dir, --trial, or both")
    try:
        report = _report(args)
    except ImportError as error:
        parser.exit(2, f"{parser.prog}: use the Eval Lab Python environment: {error}\n")
    except (OSError, ValueError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
