"""Import Dispatch Handoff's public study, not native trial directories.

This adapter checks portable decision evidence for consistency. It does not
verify the original execution or establish what a participant used to decide.
"""

import re
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import UUID

from .tape import Decision, canonical, decision, decode, digest, write_lines

_BAYS = ("A", "B", "C", "D")
_DELIVERY = {
    "silent": (),
    "stale": (1,),
    "fresh": (2,),
    "reordered": (2, 1),
    "pooled": (),
}
_ROLES = ("dispatch", "inventory")
_NATIVE_PINS = ("result", "artifact_manifest", "world_manifest")
_ABSENCES = (
    "not_attempted",
    "launch_failed",
    "native_failed",
    "missing_native_output",
    "incomplete_dossier",
)
_TASK_SOURCES = (
    "task.toml",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/player/Dockerfile",
    "environment/scenario.json",
    "environment/seatworld.py",
    "environment/serve.py",
    "instructions/dispatch.md",
    "instructions/inventory.md",
    "solution/solve.sh",
    "tests/score.py",
    "tests/test.sh",
)


class _Cell(TypedDict):
    cell_id: str
    seed: int
    condition: str


class _Report(TypedDict):
    revision: int
    available: list[str]


def _object(value: object, label: str, *keys: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or any(not isinstance(key, str) for key in value)
        or (keys and value.keys() != set(keys))
    ):
        raise ValueError(f"invalid {label} fields")
    return cast("dict[str, Any]", value)


def _array(value: object, label: str, size: int | None = None) -> list[Any]:
    if not isinstance(value, list) or (size is not None and len(value) != size):
        raise ValueError(f"invalid {label} array or count")
    return value


def _matches(value: object, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _pins(pins: dict[str, Any], *, attempted: bool = False) -> None:
    for name, value in pins.items():
        if attempted and name != "result" and value is None:
            continue
        if not _matches(value, r"[0-9a-f]{64}"):
            raise ValueError("invalid SHA-256 pin")


def _plan(value: object) -> tuple[int, str, list[_Cell]]:
    plan = _object(
        value,
        "panel plan",
        "schema",
        "native",
        "policy",
        "hosted_route",
        "seeds",
        "conditions",
        "episodes",
        "source_sha256",
        "cells",
    )
    policy = plan["policy"]
    if plan["schema"] != "seatlab.panel-plan/v1" or policy not in (
        "oracle",
        "arrival",
        "hosted",
    ):
        raise ValueError("unsupported panel plan schema or policy")
    native = _object(plan["native"], "native source", "expected_version", "olam_base")
    if not _matches(native["expected_version"], r"\d+\.\d+\.\d+") or not _matches(
        native["olam_base"], r"[0-9a-f]{40}"
    ):
        raise ValueError("invalid native source labels")
    sources = ["seatlab/run_panel.py", "seatlab/evidence.py"]
    if policy != "oracle":
        sources.append("seatlab/seat_agent.py")
    sources.extend(f"tasks/dispatch-handoff/{name}" for name in _TASK_SOURCES)
    _pins(_object(plan["source_sha256"], "source pins", *sources))
    if policy == "hosted":
        route = _object(plan["hosted_route"], "hosted route", "model", "provider")
        slug = r"[a-z0-9][a-z0-9._-]*"
        if not _matches(route["model"], rf"{slug}/{slug}:free") or not _matches(
            route["provider"], slug
        ):
            raise ValueError("invalid hosted route labels")
    elif plan["hosted_route"] is not None:
        raise ValueError("deterministic plan cannot declare a hosted route")

    seeds = _array(plan["seeds"], "plan seeds")
    if (
        not seeds
        or any(type(seed) is not int or not -(2**63) <= seed < 2**63 for seed in seeds)
        or sorted(set(seeds)) != seeds
    ):
        raise ValueError("expected distinct ordered signed 64-bit plan seeds")
    conditions = _array(plan["conditions"], "plan conditions")
    if not conditions or conditions != [name for name in _DELIVERY if name in conditions]:
        raise ValueError("expected distinct known conditions in plan order")
    episodes = plan["episodes"]
    if type(episodes) is not int or not 1 <= episodes <= 256:
        raise ValueError("expected 1 to 256 planned episodes")
    grid: list[_Cell] = [
        {"cell_id": f"seed-{seed}--{condition}", "seed": seed, "condition": condition}
        for seed in seeds
        for condition in conditions
    ]
    if canonical(plan["cells"]) != canonical(grid):
        raise ValueError("plan cells do not match the full ordered grid")
    return episodes, policy, grid


def _trial_id(value: object, trial_ids: set[str]) -> str:
    if not isinstance(value, str):
        raise ValueError("missing native trial UUID")
    try:
        trial_id = UUID(value)
    except ValueError:
        raise ValueError("invalid native trial UUID") from None
    if str(trial_id) != value or trial_id.int == 0 or value in trial_ids:
        raise ValueError("noncanonical or duplicate native trial UUID")
    trial_ids.add(value)
    return value


def _check_unknown(row: dict[str, Any], policy: str, trial_ids: set[str]) -> None:
    absence = row["absence"]
    if absence not in _ABSENCES:
        raise ValueError("unknown unmeasured cell absence")
    process = _object(row["process"], "process receipt", "state", "returncode")
    state, code = process["state"], process["returncode"]
    if state == "exited":
        if type(code) is not int or not -255 <= code <= 255:
            raise ValueError("invalid process exit code")
    elif state not in ("no_receipt", "invalid_receipt", "launch_failed") or code is not None:
        raise ValueError("invalid process receipt")
    attempt = row["native_attempt"]
    if attempt is None:
        if (
            (absence == "not_attempted" and state != "no_receipt")
            or (absence == "launch_failed") != (state == "launch_failed")
            or (absence == "native_failed") != (state == "exited" and code != 0)
        ):
            raise ValueError("absence contradicts the process receipt")
        return
    if absence not in ("native_failed", "incomplete_dossier"):
        raise ValueError("absence contradicts the native attempt")
    attempt = _object(attempt, "native attempt", "trial_id", "native_sha256", "seats")
    _trial_id(attempt["trial_id"], trial_ids)
    _pins(_object(attempt["native_sha256"], "attempt pins", *_NATIVE_PINS), attempted=True)
    for role, status in _object(attempt["seats"], "native attempt seats", *_ROLES).items():
        seat = _object(status, "native attempt seat", "model_turns", "http_status")
        turns, http = seat["model_turns"], seat["http_status"]
        hosted = policy == "hosted" and not (
            role == "inventory" and row["condition"] == "pooled"
        )
        if (turns is not None or http is not None) and not hosted:
            raise ValueError("deterministic seat has hosted attempt data")
        if turns is not None and (type(turns) is not int or turns not in (0, 1)):
            raise ValueError("invalid attempted model turn count")
        if http is not None and (
            type(http) is not int or not 100 <= http <= 599 or http == 200
        ):
            raise ValueError("invalid attempted model status")


def _mask(value: object, label: str) -> list[str]:
    bays = _array(value, label, 2)
    if not all(bay in _BAYS for bay in bays) or not bays[0] < bays[1]:
        raise ValueError(f"expected two distinct ordered bays in {label}")
    return bays


def _report(value: object, revision: int) -> _Report:
    report = _object(value, "report", "revision", "available")
    if type(report["revision"]) is not int or report["revision"] != revision:
        raise ValueError("report revision does not match delivery order")
    return {"revision": revision, "available": _mask(report["available"], "report mask")}


def _cases(manifest: dict[str, Any], cell: _Cell, episodes: int) -> list[Any]:
    settings = {
        "schema": "seatlab.scenario/v1",
        "seed": cell["seed"],
        "condition": cell["condition"],
        "episodes": episodes,
    }
    if manifest["schema"] != "seatlab.world/v1" or manifest["complete"] is not True:
        raise ValueError("expected a complete public world manifest")
    if canonical(manifest["settings"]) != canonical(settings):
        raise ValueError("world settings do not match the planned cell")
    return _array(manifest["cases"], "world cases", episodes)


def _episode(
    value: object, index: int, condition: str, policy: str
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], str, dict[str, int | bool]]:
    case = _object(
        value,
        "world case",
        "id",
        "utilities",
        "before",
        "after",
        "reports",
        "visible",
        "chosen",
    )
    if type(case["id"]) is not int or case["id"] != index:
        raise ValueError("world case IDs are missing or out of order")
    utilities = _object(case["utilities"], "dispatch utilities", *_BAYS)
    if any(type(rank) is not int for rank in utilities.values()) or set(
        utilities.values()
    ) != {1, 2, 3, 4}:
        raise ValueError("dispatch utilities must rank the 4 bays from 1 to 4")
    before = _mask(case["before"], "before mask")
    after = _mask(case["after"], "after mask")
    if len(set(before).intersection(after)) != 2 - index % 2:
        raise ValueError("latent availability does not match the episode change split")

    if condition == "pooled":
        if case["reports"] is not None:
            raise ValueError("pooled cases require null accepted reports")
        accepted = []
    else:
        accepted = [
            _report(report, revision)
            for revision, report in enumerate(
                _array(case["reports"], "accepted reports", 2), 1
            )
        ]
    delivery = _DELIVERY[condition]
    messages = [
        _report(value, revision)
        for value, revision in zip(
            _array(case["visible"], "visible reports", len(delivery)), delivery, strict=True
        )
    ]
    if any(message != accepted[message["revision"] - 1] for message in messages):
        raise ValueError("visible report does not match the corresponding accepted report")
    pooled = after if condition == "pooled" else None
    view = {"utilities": utilities, "messages": messages, "pooled_available": pooled}
    chosen = case["chosen"]
    if chosen not in _BAYS:
        raise ValueError("missing or invalid native bay choice")
    if policy != "hosted":
        eligible = pooled
        if messages:
            message = (
                messages[-1]
                if policy == "arrival"
                else max(messages, key=lambda item: item["revision"])
            )
            eligible = message["available"]
        if chosen != max(_BAYS if eligible is None else eligible, key=utilities.__getitem__):
            raise ValueError("native choice disagrees with the planned dispatch policy")

    best = max(after, key=utilities.__getitem__)
    answers = [
        {"action": bay, "optimal": bay == best, "feasible": bay in after} for bay in _BAYS
    ]
    row = {
        "id": index,
        "optimal": chosen == best,
        "feasible": chosen in after,
        "changed": before != after,
    }
    latent = {"id": index, "utilities": utilities, "before": before, "after": after}
    return view, answers, latent, chosen, row


def _check_seats(value: object, condition: str, policy: str) -> None:
    seats = _object(value, "measurement seats", *_ROLES)
    for role, seat in seats.items():
        seat = _object(
            seat,
            "measurement seat",
            "agent_name",
            "agent_version",
            "under_test",
            "model_info",
            "served_route",
            "n_input_tokens",
            "n_cache_tokens",
            "n_output_tokens",
            "cost_usd",
        )
        if role == "inventory" and condition == "pooled":
            name = "nop"
        else:
            name = "oracle" if policy == "oracle" else "seatlab-dispatch-handoff"
        if (
            seat["agent_name"] != name
            or seat["agent_version"] != "1.0.0"
            or seat["under_test"] is not True
        ):
            raise ValueError("native seat identity does not match the planned policy")
        if (policy != "hosted" or name == "nop") and (
            seat["model_info"] is not None or seat["served_route"] is not None
        ):
            raise ValueError("deterministic seat cannot declare a model route")


def _measured(
    value: object,
    cell: _Cell,
    episodes: int,
    policy: str,
    trial_ids: set[str],
    paired: dict[tuple[int, int], str],
    selected: str | None,
) -> list[Decision]:
    measurement = _object(
        value,
        "measurement",
        "trial_id",
        "native_sha256",
        "score",
        "reward",
        "fingerprint",
        "seats",
        "manifest",
        "manifest_sha256",
    )
    trial_id = _trial_id(measurement["trial_id"], trial_ids)
    _pins(_object(measurement["native_sha256"], "native pins", *_NATIVE_PINS))
    manifest = _object(
        measurement["manifest"], "world manifest", "schema", "complete", "settings", "cases"
    )
    if digest(manifest) != measurement["manifest_sha256"]:
        raise ValueError("public manifest SHA-256 does not match")
    condition, seed = cell["condition"], cell["seed"]
    _check_seats(measurement["seats"], condition, policy)
    records = []
    latent_cases = []
    score_rows = []
    for index, value in enumerate(_cases(manifest, cell, episodes)):
        view, answers, latent, chosen, row = _episode(value, index, condition, policy)
        latent_cases.append(latent)
        score_rows.append(row)
        if selected is not None:
            records.append(
                decision(
                    {
                        "schema": "seatlab.decision/v1",
                        "key": f"handoff:{selected}:{seed}:{condition}:{index}",
                        "role": "dispatch",
                        "native_trial_id": trial_id,
                        "basis": "reconstructed",
                        "view": view,
                        "view_sha256": digest(view),
                        "observed": {"action": chosen},
                        "answers": answers,
                    }
                )
            )
    fingerprint = digest(latent_cases)
    if measurement["fingerprint"] != fingerprint:
        raise ValueError("latent scenario fingerprint does not match")
    if paired.setdefault((seed, episodes), fingerprint) != fingerprint:
        raise ValueError("latent scenarios differ for a paired seed")
    totals = {
        flag: sum(row[flag] for row in score_rows)
        for flag in ("optimal", "feasible", "changed")
    }
    expected_score = {"episodes": episodes, "rows": score_rows, **totals}
    if canonical(measurement["score"]) != canonical(expected_score):
        raise ValueError("public score does not match the native choice and official optimum")
    reward = measurement["reward"]
    if type(reward) not in (int, float) or reward != totals["optimal"] / episodes:
        raise ValueError("public reward does not match the recomputed score")
    return records


def _panel(
    panel: dict[str, Any],
    selected: str | None,
    trial_ids: set[str],
    paired: dict[tuple[int, int], str],
) -> tuple[dict[str, int], list[Decision]]:
    episodes, policy, grid = _plan(panel["plan"])
    cells = _array(panel["cells"], "panel grid", len(grid))
    measured = 0
    records: list[Decision] = []
    for cell, cell_value in zip(grid, cells, strict=True):
        try:
            row = _object(cell_value, "public cell")
            if canonical({key: row.get(key) for key in cell}) != canonical(cell):
                raise ValueError("cell coordinates do not match the planned grid")
            status = row.get("status")
            if status == "measured":
                _object(row, "measured cell", *cell, "status", "measurement")
                records.extend(
                    _measured(
                        row["measurement"],
                        cell,
                        episodes,
                        policy,
                        trial_ids,
                        paired,
                        selected,
                    )
                )
                measured += 1
            elif status == "unmeasured":
                _object(
                    row,
                    "unmeasured cell",
                    *cell,
                    "status",
                    "absence",
                    "process",
                    "native_attempt",
                )
                _check_unknown(row, policy, trial_ids)
            else:
                raise ValueError("unknown cell status")
        except ValueError as exc:
            raise ValueError(f"{cell['cell_id']}: {exc}") from None
    planned = len(grid)
    unmeasured = planned - measured
    counts = {"planned": planned, "measured": measured, "unmeasured": unmeasured}
    if canonical({key: panel[key] for key in counts}) != canonical(counts):
        raise ValueError("public panel counts do not match the planned grid")
    summary = {
        "planned_cells": planned,
        "measured_cells": measured,
        "unmeasured_cells": unmeasured,
        "decisions": len(records),
    }
    return summary, records


def from_handoff(bundle: Path, panel: str, output: Path) -> dict[str, int]:
    """Atomically write reconstructed dispatch decisions from one public panel.

    The denominator includes unmeasured cells. An entirely unmeasured panel
    produces an empty tape, not a measured score of zero.
    """
    study = _object(decode(bundle.read_bytes()), "public study", "schema", "panels")
    if study["schema"] != "seatlab.dispatch-handoff-study/v1":
        raise ValueError("unsupported public Dispatch Handoff study schema")
    names: set[str] = set()
    trial_ids: set[str] = set()
    paired: dict[tuple[int, int], str] = {}
    selected: tuple[dict[str, int], list[Decision]] | None = None
    for value in _array(study["panels"], "public panels"):
        entry = _object(
            value,
            "public panel",
            "name",
            "plan",
            "cells",
            "planned",
            "measured",
            "unmeasured",
        )
        name = entry["name"]
        if not _matches(name, r"[a-z][a-z0-9_-]{0,63}") or name in names:
            raise ValueError("invalid or duplicate public panel name")
        names.add(name)
        try:
            result = _panel(entry, name if name == panel else None, trial_ids, paired)
        except ValueError as exc:
            raise ValueError(f"panel {name}: {exc}") from None
        if name == panel:
            selected = result
    if selected is None:
        raise ValueError("requested panel is not in the public study")
    summary, records = selected
    write_lines(output, records)
    return summary
