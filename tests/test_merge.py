import tempfile
import unittest
from pathlib import Path

from seatlab import tape as wire
from seatlab.context import ContextSpec
from seatlab.merge import merge
from seatlab.partition import partition
from seatlab.replay import replay

_TRIAL = "dd19e118-5159-4831-bc8e-365d571e6b6b"


def _dispatch(key: str, available: str) -> wire.Decision:
    view = {
        "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
        "messages": [
            {"revision": 1, "available": [available]},
            {"revision": 2, "available": ["D"]},
        ],
        "pooled_available": None,
    }
    return {
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


def _observation(key: str, *, missing: bool = False) -> wire.Decision:
    view = {"signal": "private-actor-input"}
    return {
        "schema": wire.DECISION_SCHEMA,
        "key": key,
        "role": "scheduler",
        "native_trial_id": _TRIAL,
        "basis": "reconstructed",
        "view": view,
        "view_sha256": wire.digest(view),
        "observed": None if missing else {"action": None},
        "answers": [
            {"action": None, "optimal": True, "feasible": True},
            {"action": "private-answer", "optimal": False, "feasible": False},
        ],
    }


class MergeTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source.jsonl"
        self.serial = self.root / "serial.jsonl"
        self.out = self.root / "merged.jsonl"

    def _observed_run(self, *rows: wire.Decision) -> Path:
        wire.write_lines(self.source, rows)
        directory = self.root / "registration"
        partition(self.source, directory, 1, "observed")
        replay(self.source, self.serial, "observed")
        return directory / "plan.json"

    def test_matches_serial_across_worker_orders_and_partitions(self) -> None:
        cases = [("zulu", "B"), ("alpha", "A"), ("delta", "C"), ("echo", "A"), ("beta", "B")]
        wire.write_lines(self.source, [_dispatch(key, action) for key, action in cases])
        recipe = self.root / "recipe.json"
        spec = {
            "schema": "seatlab.context/v1",
            "ops": [{"op": "take", "path": ["messages"], "count": 1}],
        }
        recipe.write_bytes(wire.canonical(spec))
        worker_parts = self.root / "worker-parts"
        partition(self.source, worker_parts, 2, "revision", transform_file=recipe)
        context = ContextSpec.load(worker_parts / "context.json")
        replay(self.source, self.serial, "revision", context)
        workers = []
        for index in (1, 2):
            worker = self.root / f"worker-{index}.jsonl"
            replay(worker_parts / f"part-{index:04d}.jsonl", worker, "revision", context)
            reordered = self.root / f"reordered-{index}.jsonl"
            wire.write_lines(reordered, reversed(list(wire.read_results(worker))))
            workers.append(reordered)

        registration = self.root / "registration"
        manifest = partition(self.source, registration, 3, "revision", transform_file=recipe)
        report = merge(registration / "plan.json", self.source, workers[::-1], self.out)

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

    def test_incomplete_reports_distinct_coverage_errors(self) -> None:
        keys = (
            "z-missing",
            "valid",
            "a-missing",
            "unmeasured",
            "repeat",
            "conflict",
            "invalid",
        )
        decisions = [_observation(key, missing=key == "unmeasured") for key in keys]
        plan = self._observed_run(*decisions)
        rows = {row["key"]: row for row in wire.read_results(self.serial)}
        first = self.root / "first.jsonl"
        second = self.root / "second.jsonl"
        wire.write_lines(
            first, [rows[key] for key in ("valid", "unmeasured", "repeat", "conflict")]
        )
        wire.write_lines(
            second,
            [
                {**rows["valid"], "key": "foreign"},
                {**rows["invalid"], "source_decision_sha256": "0" * 64},
                {**rows["conflict"], "score": {"optimal": False, "feasible": True}},
                rows["repeat"],
            ],
        )

        report = merge(plan, self.source, [second, first], self.out)
        self.assertEqual(report, merge(plan, self.source, [first, second], self.out))
        self.assertFalse(self.out.exists())
        self.assertEqual(report["registered"], 7)
        self.assertEqual(report["supplied"], 8)
        self.assertEqual(report["measured"], 1)
        self.assertEqual(report["unmeasured"], 1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["missing_keys"], ["a-missing", "z-missing"])
        self.assertEqual(report["foreign_keys"], ["foreign"])
        self.assertEqual(report["duplicate_keys"], ["repeat"])
        self.assertEqual(report["conflicting_keys"], ["conflict"])
        self.assertEqual(report["invalid_keys"], ["conflict", "invalid"])
        self.assertNotIn(b"private-", wire.canonical(report))

    def test_explicit_unmeasured_result_counts_as_complete_coverage(self) -> None:
        plan = self._observed_run(_observation("missing", missing=True), _observation("null"))
        report = merge(plan, self.source, [self.serial], self.out)
        self.assertTrue(report["complete"])
        self.assertEqual(report["measured"], 1)
        self.assertEqual(report["unmeasured"], 1)
        self.assertEqual(self.out.read_bytes(), self.serial.read_bytes())

    def test_rejects_conflicting_external_rows_even_when_both_are_compatible(self) -> None:
        self._observed_run(_observation("decision"))
        external_plan = self.root / "external"
        partition(self.source, external_plan, 1, "external")
        plan = external_plan / "plan.json"
        row = next(wire.read_results(self.serial))
        first = self.root / "external-a.jsonl"
        second = self.root / "external-b.jsonl"
        wire.write_lines(first, [{**row, "policy": "external"}])
        wire.write_lines(
            second,
            [
                {
                    **row,
                    "policy": "external",
                    "action": "private-answer",
                    "score": {"optimal": False, "feasible": False},
                },
            ],
        )

        report = merge(plan, self.source, [first, second], self.out)
        self.assertFalse(report["complete"])
        self.assertEqual(report["conflicting_keys"], ["decision"])
        self.assertEqual(report["invalid_keys"], [])
        self.assertFalse(self.out.exists())

    def test_checks_authoritative_tape_even_without_worker_results(self) -> None:
        original = _observation("decision")
        plan = self._observed_run(original)
        report = merge(plan, self.source, [], self.out)
        self.assertFalse(report["complete"])
        self.assertEqual(report["supplied"], 0)
        self.assertEqual(report["missing_keys"], ["decision"])
        self.assertFalse(self.out.exists())

        changed = self.root / "changed.jsonl"
        wire.write_lines(changed, [{**original, "observed": {"action": "private-answer"}}])

        with self.assertRaisesRegex(wire.TapeError, "source_sha256 does not match"):
            merge(plan, changed, [], self.out)
        self.assertFalse(self.out.exists())

    def test_checks_every_registered_shard_digest_before_publication(self) -> None:
        plan = self._observed_run(_observation("decision"))
        manifest = wire.decode(plan.read_bytes())
        assert isinstance(manifest, dict)
        manifest.pop("plan_sha256")
        manifest["shard_sha256"] = ["0" * 64]
        manifest["plan_sha256"] = wire.digest(manifest)
        changed_plan = self.root / "changed-plan.json"
        wire.write_lines(changed_plan, [manifest])

        with self.assertRaisesRegex(wire.TapeError, "shard_sha256 does not match"):
            merge(changed_plan, self.source, [self.serial], self.out)
        self.assertFalse(self.out.exists())

    def test_rejects_duplicate_keys_within_one_worker_ledger(self) -> None:
        plan = self._observed_run(_observation("decision"))
        repeated = self.root / "repeated.jsonl"
        row = next(wire.read_results(self.serial))
        wire.write_lines(repeated, [row, row])

        with self.assertRaisesRegex(wire.TapeError, "duplicate record key"):
            merge(plan, self.source, [repeated], self.out)
        self.assertFalse(self.out.exists())

    def test_does_not_replace_existing_output_even_on_complete_merge(self) -> None:
        plan = self._observed_run(_observation("decision"))
        self.out.write_bytes(b"existing ledger")

        with self.assertRaises(FileExistsError):
            merge(plan, self.source, [self.serial], self.out)
        self.assertEqual(self.out.read_bytes(), b"existing ledger")
