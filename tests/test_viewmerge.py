import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seatlab import tape as wire
from seatlab import viewwork
from seatlab.context import ContextSpec
from seatlab.partition import load_plan, partition
from seatlab.replay import replay
from seatlab.viewmerge import merge_actions

_TRIAL = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_NAMESPACE = "0123456789abcdef0123456789abcdef"
_OTHER_NAMESPACE = "fedcba9876543210fedcba9876543210"

_EXTERNAL_POLICY = r"""
import json
import sys

mode = sys.argv[1]
view = json.loads(sys.stdin.read())
if mode == "choose":
    action = view.get("pick")
    print(json.dumps({"action": action}))
elif mode == "null":
    print('{"action": null}')
elif mode == "out-of-domain":
    print('{"action": "outside-answer-domain"}')
elif mode == "fail":
    sys.exit(2)
"""


def _dispatch(key: str, available: str) -> wire.Decision:
    view = {
        "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
        "messages": [
            {"revision": 1, "available": [available]},
            {"revision": 2, "available": ["D"]},
        ],
        "pooled_available": None,
    }
    return wire.decision(
        {
            "schema": wire.DECISION_SCHEMA,
            "key": key,
            "role": "dispatch",
            "native_trial_id": _TRIAL,
            "basis": "bound",
            "view": view,
            "view_sha256": wire.digest(view),
            "observed": None,
            "answers": [
                {"action": action, "optimal": action == "A", "feasible": True}
                for action in ("A", "B", "C", "D")
            ],
        }
    )


def _calendar(
    key: str,
    reports: dict[str, list[int]],
    *,
    answers: list[wire.Answer] | None = None,
) -> wire.Decision:
    view: dict[str, object] = {"reports": reports}
    return wire.decision(
        {
            "schema": wire.DECISION_SCHEMA,
            "key": key,
            "role": "scheduler",
            "native_trial_id": _TRIAL,
            "basis": "reconstructed",
            "view": view,
            "view_sha256": wire.digest(view),
            "observed": None,
            "answers": answers
            if answers is not None
            else [
                {"action": None, "optimal": False, "feasible": True},
                {"action": 1, "optimal": True, "feasible": True},
                {"action": 2, "optimal": False, "feasible": True},
            ],
        }
    )


def _external_decision(key: str, pick: wire.Action) -> wire.Decision:
    view: dict[str, object] = {"pick": pick, "signal": "actor-visible"}
    return wire.decision(
        {
            "schema": wire.DECISION_SCHEMA,
            "key": key,
            "role": "scheduler",
            "native_trial_id": _TRIAL,
            "basis": "reconstructed",
            "view": view,
            "view_sha256": wire.digest(view),
            "observed": None,
            "answers": [
                {"action": None, "optimal": True, "feasible": True},
                {"action": "alpha", "optimal": False, "feasible": True},
                {"action": "private-answer", "optimal": False, "feasible": False},
            ],
        }
    )


class ViewMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source.jsonl"
        self.serial = self.root / "serial.jsonl"
        self.out = self.root / "merged.jsonl"
        self.script = self.root / "policy.py"
        self.script.write_text(_EXTERNAL_POLICY, encoding="utf-8")

    def _exec(self, mode: str) -> list[str]:
        return [sys.executable, str(self.script), mode]

    def test_matches_serial_across_arrival_order_and_context_transform(self) -> None:
        cases = [("zulu", "B"), ("alpha", "A"), ("delta", "C"), ("echo", "A"), ("beta", "B")]
        wire.write_lines(self.source, [_dispatch(key, action) for key, action in cases])
        recipe = self.root / "recipe.json"
        spec = {
            "schema": "seatlab.context/v1",
            "ops": [{"op": "take", "path": ["messages"], "count": 1}],
        }
        recipe.write_bytes(wire.canonical(spec))
        parts = self.root / "parts"
        manifest = partition(
            self.source,
            parts,
            2,
            "revision",
            transform_file=recipe,
            namespace=_NAMESPACE,
        )
        plan = load_plan(parts / "plan.json")
        context = ContextSpec.load(recipe)
        replay(self.source, self.serial, "revision", context)

        ledgers: list[Path] = []
        for index in (1, 2):
            acted = self.root / f"worker-{index}.jsonl"
            viewwork.act(parts / f"part-{index:04d}.jsonl", acted, "revision")
            reordered = self.root / f"reordered-{index}.jsonl"
            wire.write_lines(reordered, reversed(list(viewwork.read_actions(acted))))
            ledgers.append(reordered)

        report = merge_actions(plan, self.source, ledgers[::-1], self.out)

        self.assertEqual(self.out.read_bytes(), self.serial.read_bytes())
        self.assertEqual(
            report,
            {
                "schema": "seatlab.merge/v1",
                "plan_sha256": manifest["plan_sha256"],
                "policy": "revision",
                "transform": context.identifier,
                "registered": 5,
                "supplied": 5,
                "measured": 5,
                "unmeasured": 0,
                "complete": True,
                "missing_keys": [],
                "foreign_keys": [],
                "duplicate_keys": [],
                "conflicting_keys": [],
                "invalid_keys": [],
            },
        )

    def test_rejects_non_view_only_plan(self) -> None:
        wire.write_lines(self.source, [_dispatch("alpha", "A")])
        parts = self.root / "full-parts"
        partition(self.source, parts, 1, "revision")
        plan = load_plan(parts / "plan.json")

        with self.assertRaisesRegex(wire.TapeError, "view-only"):
            merge_actions(plan, self.source, [], self.out)
        self.assertFalse(self.out.exists())

    def test_external_valid_null_failure_and_out_of_domain_without_reexecution(self) -> None:
        rows = [
            _external_decision("valid-choice", "alpha"),
            _external_decision("valid-null", None),
            _external_decision("out-of-domain", "outside-answer-domain"),
            _external_decision("exec-failure", "alpha"),
        ]
        wire.write_lines(self.source, rows)
        parts = self.root / "parts"
        partition(self.source, parts, 4, "external", namespace=_NAMESPACE)
        plan = load_plan(parts / "plan.json")

        ledgers = [self.root / f"worker-{index}.jsonl" for index in range(1, 5)]
        viewwork.act(parts / "part-0001.jsonl", ledgers[0], self._exec("choose"))
        viewwork.act(parts / "part-0002.jsonl", ledgers[1], self._exec("null"))
        viewwork.act(parts / "part-0003.jsonl", ledgers[2], self._exec("out-of-domain"))
        viewwork.act(parts / "part-0004.jsonl", ledgers[3], self._exec("fail"))

        with patch("seatlab.replay.subprocess.Popen", side_effect=AssertionError("spawn")):
            report = merge_actions(plan, self.source, ledgers[::-1], self.out)

        self.assertTrue(report["complete"])
        self.assertEqual(report["registered"], 4)
        self.assertEqual(report["supplied"], 4)
        self.assertEqual(report["measured"], 2)
        self.assertEqual(report["unmeasured"], 2)
        self.assertEqual(report["missing_keys"], [])
        self.assertEqual(report["foreign_keys"], [])
        self.assertEqual(report["duplicate_keys"], [])
        self.assertEqual(report["conflicting_keys"], [])
        self.assertEqual(report["invalid_keys"], [])

        merged = list(wire.read_results(self.out))
        self.assertEqual([row["key"] for row in merged], [row["key"] for row in rows])
        self.assertEqual(merged[0]["status"], "measured")
        self.assertEqual(merged[0]["action"], "alpha")
        self.assertEqual(merged[0]["score"], {"optimal": False, "feasible": True})
        self.assertIsNone(merged[0]["error"])

        self.assertEqual(merged[1]["status"], "measured")
        self.assertIsNone(merged[1]["action"])
        self.assertEqual(merged[1]["score"], {"optimal": True, "feasible": True})
        self.assertIsNone(merged[1]["error"])

        self.assertEqual(merged[2]["status"], "unmeasured")
        self.assertIsNone(merged[2]["action"])
        self.assertIsNone(merged[2]["score"])
        self.assertEqual(merged[2]["error"], "invalid_action")

        self.assertEqual(merged[3]["status"], "unmeasured")
        self.assertIsNone(merged[3]["action"])
        self.assertIsNone(merged[3]["score"])
        self.assertEqual(merged[3]["error"], "policy_failed")

        # Serial replay on the first three rows matches byte-for-byte.
        subset = self.root / "subset.jsonl"
        wire.write_lines(subset, rows[:3])
        replay(subset, self.serial, self._exec("choose"))
        self.assertEqual(
            [wire.canonical(row) for row in merged[:3]],
            [wire.canonical(row) for row in wire.read_results(self.serial)],
        )

    def test_builtin_worker_cannot_lie_about_action_or_failure(self) -> None:
        rows = [
            _calendar("measured-choice", {"m1": [1, 2], "m2": [1, 2]}),
            _calendar("measured-null", {"m1": [1], "m2": [2]}),
            _calendar("unscorable", {"m1": [99], "m2": [99]}),
        ]
        wire.write_lines(self.source, rows)
        parts = self.root / "parts"
        partition(self.source, parts, 1, "intersection", namespace=_NAMESPACE)
        plan = load_plan(parts / "plan.json")
        valid_ledger = self.root / "valid.jsonl"
        viewwork.act(parts / "part-0001.jsonl", valid_ledger, "intersection")
        base_rows = list(viewwork.read_actions(valid_ledger))

        # Baseline valid builtin run succeeds, including valid null and real unscorable action.
        baseline_out = self.root / "baseline.jsonl"
        baseline = merge_actions(plan, self.source, [valid_ledger], baseline_out)
        self.assertTrue(baseline["complete"])
        self.assertEqual(baseline["measured"], 2)
        self.assertEqual(baseline["unmeasured"], 1)

        for name, index, changes in (
            ("wrong-action", 0, {"action": 2, "error": None}),
            ("false-failure-on-choice", 0, {"action": None, "error": "invalid_action"}),
            ("wrong-null-choice", 1, {"action": 1, "error": None}),
            ("false-failure-on-null", 1, {"action": None, "error": "invalid_action"}),
            ("forged-scorable-on-unscorable", 2, {"action": 1, "error": None}),
        ):
            with self.subTest(case=name):
                tampered = [dict(row) for row in base_rows]
                tampered[index].update(changes)
                ledger = self.root / f"{name}.jsonl"
                wire.write_lines(ledger, tampered)
                out = self.root / f"{name}-out.jsonl"
                report = merge_actions(plan, self.source, [ledger], out)
                self.assertFalse(report["complete"])
                self.assertEqual(report["invalid_keys"], [rows[index]["key"]])
                self.assertFalse(out.exists())

        external_err = [dict(row) for row in base_rows]
        external_err[0].update({"action": None, "error": "timeout"})
        bad_ledger = self.root / "external-error-on-builtin.jsonl"
        wire.write_lines(bad_ledger, external_err)
        bad_out = self.root / "external-error-on-builtin-out.jsonl"
        with self.assertRaises(wire.TapeError):
            merge_actions(plan, self.source, [bad_ledger], bad_out)
        self.assertFalse(bad_out.exists())

    def test_detects_missing_foreign_repeated_conflicting_and_mismatches(self) -> None:
        keys = (
            "z-missing",
            "valid",
            "a-missing",
            "repeat",
            "conflict",
            "bad-input",
            "bad-policy",
        )
        rows = [_external_decision(key, "alpha") for key in keys]
        wire.write_lines(self.source, rows)
        parts = self.root / "parts"
        partition(self.source, parts, 1, "external", namespace=_NAMESPACE)
        plan = load_plan(parts / "plan.json")
        acted = self.root / "acted.jsonl"
        viewwork.act(parts / "part-0001.jsonl", acted, self._exec("choose"))
        by_key = dict(zip(keys, viewwork.read_actions(acted), strict=True))

        foreign_id = "f" * 64
        dup_foreign_id = "e" * 64
        conflict_foreign_id = "d" * 64
        first = self.root / "first.jsonl"
        second = self.root / "second.jsonl"
        wire.write_lines(
            first,
            [
                by_key["valid"],
                by_key["repeat"],
                by_key["conflict"],
                {**by_key["valid"], "request_id": dup_foreign_id},
                {**by_key["valid"], "request_id": conflict_foreign_id, "action": "alpha"},
            ],
        )
        wire.write_lines(
            second,
            [
                {**by_key["valid"], "request_id": foreign_id},
                {**by_key["valid"], "request_id": dup_foreign_id},
                {**by_key["valid"], "request_id": conflict_foreign_id, "action": None},
                {**by_key["bad-input"], "input_sha256": "0" * 64},
                {**by_key["bad-policy"], "policy": "revision"},
                {**by_key["conflict"], "action": None},
                by_key["repeat"],
            ],
        )

        report = merge_actions(plan, self.source, [second, first], self.out)
        self.assertEqual(report, merge_actions(plan, self.source, [first, second], self.out))
        self.assertFalse(self.out.exists())
        self.assertFalse(report["complete"])
        self.assertEqual(report["registered"], 7)
        self.assertEqual(report["supplied"], 12)
        self.assertEqual(report["measured"], 1)
        self.assertEqual(report["unmeasured"], 0)
        self.assertEqual(report["missing_keys"], ["a-missing", "z-missing"])
        self.assertEqual(
            report["foreign_keys"],
            sorted([foreign_id, dup_foreign_id, conflict_foreign_id]),
        )
        self.assertEqual(report["duplicate_keys"], sorted(["repeat", dup_foreign_id]))
        self.assertEqual(
            report["conflicting_keys"], sorted(["conflict", conflict_foreign_id])
        )
        self.assertEqual(report["invalid_keys"], ["bad-input", "bad-policy"])
        self.assertNotIn(b"private-", wire.canonical(report))

    def test_duplicate_or_conflict_marks_incomplete_even_with_valid_rows(self) -> None:
        rows = [_dispatch("only", "A")]
        wire.write_lines(self.source, rows)
        parts = self.root / "parts"
        partition(self.source, parts, 1, "revision", namespace=_NAMESPACE)
        plan = load_plan(parts / "plan.json")
        acted = self.root / "acted.jsonl"
        viewwork.act(parts / "part-0001.jsonl", acted, "revision")

        report = merge_actions(plan, self.source, [acted, acted], self.out)
        self.assertFalse(report["complete"])
        self.assertEqual(report["registered"], 1)
        self.assertEqual(report["supplied"], 2)
        self.assertEqual(report["measured"], 0)
        self.assertEqual(report["unmeasured"], 0)
        self.assertEqual(report["duplicate_keys"], ["only"])
        self.assertEqual(report["conflicting_keys"], [])
        self.assertEqual(report["invalid_keys"], [])
        self.assertFalse(self.out.exists())

    def test_rejects_cross_registration_mix_across_namespaces_or_policies(self) -> None:
        rows = [_dispatch("first", "A"), _dispatch("second", "B")]
        wire.write_lines(self.source, rows)
        parts_a = self.root / "parts-a"
        parts_b = self.root / "parts-b"
        parts_c = self.root / "parts-c"
        partition(self.source, parts_a, 2, "revision", namespace=_NAMESPACE)
        partition(self.source, parts_b, 2, "revision", namespace=_OTHER_NAMESPACE)
        partition(self.source, parts_c, 2, "arrival", namespace=_NAMESPACE)

        plan_a = load_plan(parts_a / "plan.json")
        acted_a1 = self.root / "a1.jsonl"
        acted_b2 = self.root / "b2.jsonl"
        acted_c2 = self.root / "c2.jsonl"
        viewwork.act(parts_a / "part-0001.jsonl", acted_a1, "revision")
        viewwork.act(parts_b / "part-0002.jsonl", acted_b2, "revision")
        viewwork.act(parts_c / "part-0002.jsonl", acted_c2, "arrival")

        other_ns_id = next(viewwork.read_actions(acted_b2))["request_id"]
        report_ns = merge_actions(plan_a, self.source, [acted_a1, acted_b2], self.out)
        self.assertFalse(report_ns["complete"])
        self.assertEqual(report_ns["missing_keys"], ["second"])
        self.assertEqual(report_ns["foreign_keys"], [other_ns_id])
        self.assertFalse(self.out.exists())

        other_policy_id = next(viewwork.read_actions(acted_c2))["request_id"]
        report_policy = merge_actions(plan_a, self.source, [acted_a1, acted_c2], self.out)
        self.assertFalse(report_policy["complete"])
        self.assertEqual(report_policy["missing_keys"], ["second"])
        self.assertEqual(report_policy["foreign_keys"], [other_policy_id])
        self.assertFalse(self.out.exists())

    def test_exhausts_source_and_shard_commitments_even_when_incomplete(self) -> None:
        original = _dispatch("decision", "A")
        wire.write_lines(self.source, [original])
        parts = self.root / "parts"
        partition(self.source, parts, 1, "revision", namespace=_NAMESPACE)
        plan = load_plan(parts / "plan.json")

        report = merge_actions(plan, self.source, [], self.out)
        self.assertFalse(report["complete"])
        self.assertEqual(report["supplied"], 0)
        self.assertEqual(report["missing_keys"], ["decision"])
        self.assertFalse(self.out.exists())

        changed = self.root / "changed.jsonl"
        wire.write_lines(changed, [{**original, "answers": []}])
        with self.assertRaisesRegex(wire.TapeError, "source_sha256 does not match"):
            merge_actions(plan, changed, [], self.out)
        self.assertFalse(self.out.exists())

        manifest = wire.decode((parts / "plan.json").read_bytes())
        assert isinstance(manifest, dict)
        manifest.pop("plan_sha256")
        manifest["shard_sha256"] = ["0" * 64]
        manifest["plan_sha256"] = wire.digest(manifest)
        changed_plan_path = self.root / "changed-plan.json"
        wire.write_lines(changed_plan_path, [manifest])
        changed_plan = load_plan(changed_plan_path)
        with self.assertRaisesRegex(wire.TapeError, "shard_sha256 does not match"):
            merge_actions(changed_plan, self.source, [], self.out)
        self.assertFalse(self.out.exists())

    def test_rejects_malformed_worker_wire_rows_and_within_ledger_duplicates(self) -> None:
        wire.write_lines(self.source, [_dispatch("decision", "A")])
        parts = self.root / "parts"
        partition(self.source, parts, 1, "revision", namespace=_NAMESPACE)
        plan = load_plan(parts / "plan.json")
        acted = self.root / "acted.jsonl"
        viewwork.act(parts / "part-0001.jsonl", acted, "revision")
        valid_row = next(viewwork.read_actions(acted))

        for name, rows in (
            ("within-ledger-duplicate", [valid_row, valid_row]),
            (
                "non-null-action-with-error",
                [{**valid_row, "action": "A", "error": "timeout"}],
            ),
            ("bool-action", [{**valid_row, "action": True}]),
            ("observed-policy", [{**valid_row, "policy": "observed"}]),
            (
                "extra-score-field",
                [{**valid_row, "score": {"optimal": True, "feasible": True}}],
            ),
        ):
            with self.subTest(case=name):
                bad = self.root / f"{name}.jsonl"
                wire.write_lines(bad, rows)
                with self.assertRaises(wire.TapeError):
                    merge_actions(plan, self.source, [bad], self.out)
                self.assertFalse(self.out.exists())

    def test_does_not_replace_existing_output_on_complete_merge(self) -> None:
        wire.write_lines(self.source, [_dispatch("decision", "A")])
        parts = self.root / "parts"
        partition(self.source, parts, 1, "revision", namespace=_NAMESPACE)
        plan = load_plan(parts / "plan.json")
        acted = self.root / "acted.jsonl"
        viewwork.act(parts / "part-0001.jsonl", acted, "revision")
        self.out.write_bytes(b"existing ledger")

        with self.assertRaises(FileExistsError):
            merge_actions(plan, self.source, [acted], self.out)
        self.assertEqual(self.out.read_bytes(), b"existing ledger")
