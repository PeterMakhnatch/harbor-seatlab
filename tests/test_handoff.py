import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from seatlab.handoff import from_handoff
from seatlab.tape import digest, read_tape, score

_PINS = dict.fromkeys(("result", "artifact_manifest", "world_manifest"), "a" * 64)
_BASE_SOURCES = (
    "seatlab/run_panel.py",
    "seatlab/evidence.py",
    "tasks/dispatch-handoff/task.toml",
    "tasks/dispatch-handoff/environment/Dockerfile",
    "tasks/dispatch-handoff/environment/docker-compose.yaml",
    "tasks/dispatch-handoff/environment/player/Dockerfile",
    "tasks/dispatch-handoff/environment/scenario.json",
    "tasks/dispatch-handoff/environment/seatworld.py",
    "tasks/dispatch-handoff/environment/serve.py",
    "tasks/dispatch-handoff/instructions/dispatch.md",
    "tasks/dispatch-handoff/instructions/inventory.md",
    "tasks/dispatch-handoff/solution/solve.sh",
    "tasks/dispatch-handoff/tests/score.py",
    "tasks/dispatch-handoff/tests/test.sh",
)
_RANKS = {"A": 2, "B": 4, "C": 1, "D": 3}
_CHANGED_CHOICE = {"silent": "B", "stale": "B", "fresh": "D", "reordered": "B", "pooled": "D"}
_REVISIONS = {"silent": (), "stale": (1,), "fresh": (2,), "reordered": (2, 1), "pooled": ()}


def _commitments(measurement: dict[str, Any]) -> None:
    world = measurement["manifest"]
    measurement["manifest_sha256"] = digest(world)
    measurement["fingerprint"] = digest(
        [
            {field: case[field] for field in ("id", "utilities", "before", "after")}
            for case in world["cases"]
        ]
    )


def _measured(cell: dict[str, Any], policy: str) -> dict[str, Any]:
    condition = cell["condition"]
    changed_choice = (
        "D" if policy == "oracle" and condition == "reordered" else _CHANGED_CHOICE[condition]
    )
    choices = ("B" if condition == "silent" else "A", changed_choice)
    cases = []
    masks = ((["A", "C"], ["A", "C"]), (["A", "B"], ["A", "D"]))
    for index, (before, after) in enumerate(masks):
        accepted = [{"revision": 1, "available": before}, {"revision": 2, "available": after}]
        cases.append(
            {
                "id": index,
                "utilities": _RANKS.copy(),
                "before": before,
                "after": after,
                "reports": None if condition == "pooled" else accepted,
                "visible": [accepted[revision - 1] for revision in _REVISIONS[condition]],
                "chosen": choices[index],
            }
        )
    correct = [chosen == best for chosen, best in zip(choices, ("A", "D"), strict=True)]
    rows = [
        {"id": index, "optimal": flag, "feasible": flag, "changed": bool(index)}
        for index, flag in enumerate(correct)
    ]
    seat = {
        "agent_name": "oracle" if policy == "oracle" else "seatlab-dispatch-handoff",
        "agent_version": "1.0.0",
        "under_test": True,
        "model_info": None,
        "served_route": None,
        "n_input_tokens": 0,
        "n_cache_tokens": 0,
        "n_output_tokens": 0,
        "cost_usd": None,
    }
    optimal = sum(row["optimal"] for row in rows)
    measurement = {
        "trial_id": str(uuid4()),
        "native_sha256": _PINS.copy(),
        "reward": optimal / 2,
        "seats": {
            "dispatch": seat,
            "inventory": {**seat, "agent_name": "nop"} if condition == "pooled" else seat,
        },
        "score": {
            "episodes": 2,
            "rows": rows,
            "changed": 1,
            "optimal": optimal,
            "feasible": sum(row["feasible"] for row in rows),
        },
        "manifest": {
            "schema": "seatlab.world/v1",
            "complete": True,
            "settings": {
                "schema": "seatlab.scenario/v1",
                "seed": cell["seed"],
                "condition": condition,
                "episodes": 2,
            },
            "cases": cases,
        },
    }
    _commitments(measurement)
    return {**cell, "status": "measured", "measurement": measurement}


def _unmeasured(cell: dict[str, Any]) -> dict[str, Any]:
    return {
        **cell,
        "status": "unmeasured",
        "absence": "native_failed",
        "process": {"state": "exited", "returncode": 0},
        "native_attempt": {
            "trial_id": str(uuid4()),
            "native_sha256": {**_PINS, "world_manifest": None},
            "seats": {
                "inventory": {"model_turns": 1, "http_status": 404},
                "dispatch": {"model_turns": 0, "http_status": None},
            },
        },
    }


def _panel(policy: str, conditions: list[str]) -> dict[str, Any]:
    grid = [
        {"cell_id": f"seed-17--{condition}", "seed": 17, "condition": condition}
        for condition in conditions
    ]
    sources = dict.fromkeys(_BASE_SOURCES, "f" * 64)
    if policy != "oracle":
        sources["seatlab/seat_agent.py"] = "e" * 64
    hosted = policy == "hosted"
    return {
        "name": policy,
        "plan": {
            "schema": "seatlab.panel-plan/v1",
            "native": {"expected_version": "0.26.0", "olam_base": "b" * 40},
            "policy": policy,
            "hosted_route": (
                {"model": "example/model:free", "provider": "example"} if hosted else None
            ),
            "source_sha256": sources,
            "seeds": [17],
            "conditions": conditions,
            "episodes": 2,
            "cells": grid,
        },
        "cells": [_unmeasured(cell) if hosted else _measured(cell, policy) for cell in grid],
        "planned": len(grid),
        "measured": 0 if hosted else len(grid),
        "unmeasured": len(grid) if hosted else 0,
    }


class HandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.bundle = Path(directory.name) / "public.json"
        self.tape = Path(directory.name) / "decisions.jsonl"
        self.arrival = _panel("arrival", ["stale", "fresh", "reordered"])
        self.hosted = _panel("hosted", ["fresh", "reordered"])
        self.oracle = _panel("oracle", ["silent", "pooled"])
        self.study = {
            "schema": "seatlab.dispatch-handoff-study/v1",
            "panels": [self.arrival, self.hosted, self.oracle],
        }

    def _import(self, panel: str = "arrival") -> dict[str, int]:
        self.bundle.write_text(json.dumps(self.study), encoding="utf-8")
        return from_handoff(self.bundle, panel, self.tape)

    def test_role_local_projection_scoring_and_arrival_order(self) -> None:
        self.assertEqual(
            self._import(),
            {"planned_cells": 3, "measured_cells": 3, "unmeasured_cells": 0, "decisions": 6},
        )
        rows = list(read_tape(self.tape))
        self.assertEqual(len(rows), 6)
        row = rows[1]
        self.assertEqual(row["key"], "handoff:arrival:17:stale:1")
        trial_id = self.arrival["cells"][0]["measurement"]["trial_id"]
        self.assertEqual(row["native_trial_id"], trial_id)
        self.assertEqual(row["role"], "dispatch")
        self.assertEqual(row["basis"], "reconstructed")
        self.assertEqual(
            row["view"],
            {
                "utilities": _RANKS,
                "messages": [{"revision": 1, "available": ["A", "B"]}],
                "pooled_available": None,
            },
        )
        self.assertEqual(row["observed"], {"action": "B"})
        self.assertEqual(score(row, "B"), {"optimal": False, "feasible": False})
        self.assertEqual(score(row, "D"), {"optimal": True, "feasible": True})
        self.assertEqual(
            rows[-1]["view"]["messages"],
            [
                {"revision": 2, "available": ["A", "D"]},
                {"revision": 1, "available": ["A", "B"]},
            ],
        )

    def test_unseen_availability_changes_answers_not_the_actor_view(self) -> None:
        panel = _panel("arrival", ["stale"])
        self.study["panels"] = [panel]
        self._import()
        original = list(read_tape(self.tape))[1]
        self.tape = self.tape.with_name("other-decisions.jsonl")
        measurement = panel["cells"][0]["measurement"]
        case = measurement["manifest"]["cases"][1]
        case["after"] = ["A", "C"]
        case["reports"][1]["available"] = ["A", "C"]
        _commitments(measurement)
        self._import()
        other = list(read_tape(self.tape))[1]
        self.assertEqual(other["view"], original["view"])
        self.assertEqual(other["view_sha256"], original["view_sha256"])
        self.assertEqual(score(original, "D"), {"optimal": True, "feasible": True})
        self.assertEqual(score(other, "D"), {"optimal": False, "feasible": False})

    def test_pool_is_only_direct_availability_view(self) -> None:
        self._import("oracle")
        silent, pooled = list(read_tape(self.tape))[1::2]
        self.assertEqual(silent["view"]["messages"], [])
        self.assertIsNone(silent["view"]["pooled_available"])
        self.assertEqual(pooled["view"]["messages"], [])
        self.assertEqual(pooled["view"]["pooled_available"], ["A", "D"])

    def test_entirely_unmeasured_has_denominator_but_no_decisions(self) -> None:
        self.assertEqual(
            self._import("hosted"),
            {"planned_cells": 2, "measured_cells": 0, "unmeasured_cells": 2, "decisions": 0},
        )
        self.assertEqual(self.tape.read_bytes(), b"")

    def test_altered_choice_with_same_score_is_rejected(self) -> None:
        measurement = self.arrival["cells"][-1]["measurement"]
        # Both B and C score zero for this shipment. The native rule chooses B.
        measurement["manifest"]["cases"][1]["chosen"] = "C"
        _commitments(measurement)
        with self.assertRaisesRegex(ValueError, "native choice"):
            self._import()
        self.assertFalse(self.tape.exists())

    def test_altered_public_answer_is_rejected(self) -> None:
        self.arrival["cells"][-1]["measurement"]["score"]["rows"][1]["optimal"] = True
        with self.assertRaisesRegex(ValueError, "official optimum"):
            self._import()
        self.assertFalse(self.tape.exists())

    def test_broken_grid_is_rejected_even_in_unselected_panel(self) -> None:
        self.hosted["cells"].reverse()
        with self.assertRaisesRegex(ValueError, "planned grid"):
            self._import()
        self.assertFalse(self.tape.exists())

    def test_latent_mismatch_is_rejected_across_paired_panels(self) -> None:
        # The official score stays the same. Only withheld truth changes.
        measurement = self.oracle["cells"][0]["measurement"]
        measurement["manifest"]["cases"][1]["after"] = ["A", "C"]
        _commitments(measurement)
        with self.assertRaisesRegex(ValueError, "latent scenarios differ"):
            self._import()
        self.assertFalse(self.tape.exists())

    def test_existing_tape_is_not_overwritten(self) -> None:
        self.tape.write_bytes(b"existing tape")
        with self.assertRaises(FileExistsError):
            self._import()
        self.assertEqual(self.tape.read_bytes(), b"existing tape")
