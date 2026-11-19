import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from seatlab import tape
from seatlab.context import ContextSpec
from seatlab.replay import replay

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
_TAKE = {"op": "take", "path": ["reports", "*"], "count": 8}
_RELABEL = {"op": "relabel", "path": ["mode"], "from": "full", "to": "first8"}


def _spec(*ops: object) -> dict[str, object]:
    return {"schema": "seatlab.context/v1", "ops": list(ops)}


class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "context.json"

    def _load(self, value: object) -> ContextSpec:
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return ContextSpec.load(self.path)

    def test_identifier_commits_to_ordered_ops_not_json_layout(self) -> None:
        value = _spec(_TAKE, _RELABEL)
        first = self._load(value)
        expected = hashlib.sha256(tape.canonical(value)).hexdigest()
        self.assertEqual(first.identifier, f"context-v1:{expected}")
        self.path.write_text(json.dumps(value, sort_keys=True, indent=2), encoding="utf-8")
        self.assertEqual(ContextSpec.load(self.path).identifier, first.identifier)
        self.assertNotEqual(self._load(_spec(_RELABEL, _TAKE)).identifier, first.identifier)
        self.assertNotEqual(
            self._load(_spec({**_TAKE, "count": 9})).identifier, first.identifier
        )
        view = {"reports": {"m1": [1]}, "mode": "full"}
        self.assertEqual(first.apply(view)["mode"], "first8")

    def test_rejects_non_strict_json_and_oversize_files(self) -> None:
        for content, message in (
            (b'{"schema":"seatlab.context/v1","ops":[],"ops":[]}', "duplicate key"),
            (b'{"schema":"seatlab.context/v1","ops":[NaN]}', "finite"),
            (b'"\xff"', "UTF-8 JSON"),
            (b"[]" + b" " * 65536, "exceeds"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.path.write_bytes(content)
                ContextSpec.load(self.path)

    def test_rejects_unknown_fields_operators_and_unbounded_shape(self) -> None:
        for value, message in (
            ({**_spec(), "include": "another.json"}, "exactly"),
            ({**_spec(), "schema": "seatlab.context/v2"}, "schema"),
            ({**_spec(), "ops": {}}, "ops"),
            (_spec(*([_TAKE] * 33)), "ops"),
            (_spec(None), "op 1 must be an object"),
            (_spec({**_TAKE, "op": "eval"}), "take or relabel"),
            (_spec({**_TAKE, "code": "process()"}), "exactly"),
            (_spec({"op": "take", "path": ["reports"]}), "exactly"),
            (_spec({**_TAKE, "path": "reports.*"}), "path"),
            (_spec({**_TAKE, "path": []}), "path"),
            (_spec({**_TAKE, "path": ["reports", 0]}), "path"),
            (_spec({**_TAKE, "path": ["reports", ""]}), "path"),
            (_spec({**_TAKE, "path": ["field"] * 9}), "path"),
            (_spec({**_TAKE, "path": ["a" * 257]}), "path"),
            (_spec({**_TAKE, "count": -1}), "count"),
            (_spec({**_TAKE, "count": True}), "count"),
            (_spec({**_TAKE, "count": 8.0}), "count"),
            (_spec({**_RELABEL, "from": 1}), "from and to"),
            (_spec({**_RELABEL, "to": "a" * 257}), "from and to"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self._load(value)

    def test_copy_on_write_shares_unchanged_branches(self) -> None:
        program = self._load(_spec({**_TAKE, "count": 1}, _RELABEL))
        first = {"start": 42}
        long_report = [first, {"start": 43}]
        short_report = [45]
        reports = {"m1": long_report, "m2": short_report}
        unseen_by_program = {"notes": ["unchanged"]}
        view = {"reports": reports, "mode": "full", "details": unseen_by_program}
        original = tape.canonical(view)

        result = program.apply(view)
        self.assertEqual(result["mode"], "first8")
        self.assertEqual(result["reports"], {"m1": [first], "m2": short_report})
        self.assertIsNot(result, view)
        updated = result["reports"]
        assert isinstance(updated, dict)
        self.assertIsNot(updated, reports)
        self.assertIsNot(updated["m1"], long_report)
        self.assertIs(updated["m1"][0], first)
        self.assertIs(updated["m2"], short_report)
        self.assertIs(result["details"], unseen_by_program)
        self.assertEqual(tape.canonical(view), original)
        self.assertIs(program.apply(result), result)

    def test_ops_run_in_order_and_take_accepts_zero(self) -> None:
        program = self._load(
            _spec(
                _RELABEL, {**_RELABEL, "from": "first8", "to": "short"}, {**_TAKE, "count": 0}
            )
        )
        self.assertEqual(
            program.apply({"reports": {"m1": [9]}, "mode": "full"}),
            {"reports": {"m1": []}, "mode": "short"},
        )

    def test_apply_rejects_invalid_paths_and_shapes_without_mutation(self) -> None:
        for ops, view, message in (
            ((_TAKE,), {"report": {"m1": [1]}}, "op 1.*missing field 'reports'"),
            ((_TAKE,), {"reports": [[1]]}, "path component 2 requires an object"),
            ((_TAKE,), {"reports": {}}, "wildcard must match"),
            (
                (_TAKE,),
                {"reports": {"m1": list(range(9)), "m2": 2}},
                "take requires an array",
            ),
            ((_RELABEL,), {"mode": None}, "relabel requires a string"),
            ((_RELABEL,), {"mode": "other"}, "relabel requires the source or target label"),
            (
                (_TAKE, _RELABEL),
                {"reports": {"m1": list(range(9))}, "mode": "other"},
                "op 2.*source or target label",
            ),
            (({**_TAKE, "path": ["answers"]},), {"reports": {}}, "missing field 'answers'"),
        ):
            with self.subTest(message=message):
                program = self._load(_spec(*ops))
                original = tape.canonical(view)
                with self.assertRaisesRegex(ValueError, message):
                    program.apply(view)
                self.assertEqual(tape.canonical(view), original)

    def test_replay_commits_to_program_and_native_source(self) -> None:
        source = _EXAMPLES / "meeting.jsonl"
        program = ContextSpec.load(_EXAMPLES / "meeting-first8.json")
        rows = {row["key"]: row for row in tape.read_tape(source)}
        out = self.root / "replay.jsonl"
        summary = replay(source, out, "intersection", transform=program)
        self.assertEqual(
            summary, {"decisions": len(rows), "measured": len(rows), "unmeasured": 0}
        )
        results = list(tape.read_results(out))
        self.assertEqual(len(results), len(rows))
        for result in results:
            row = rows[result["key"]]
            target = rows[row["key"].replace(":full", ":first8")]
            with self.subTest(key=row["key"]):
                self.assertEqual(result["transform"], program.identifier)
                self.assertEqual(result["policy"], "intersection")
                self.assertEqual(result["basis"], "reconstructed")
                self.assertEqual(result["native_trial_id"], row["native_trial_id"])
                self.assertEqual(result["source_decision_sha256"], tape.decision_sha256(row))
                self.assertEqual(result["source_view_sha256"], row["view_sha256"])
                self.assertEqual(result["input_sha256"], target["view_sha256"])
                self.assertEqual(result["input_bytes"], len(tape.canonical(target["view"])))
                self.assertEqual(result["status"], "measured")
                observed = target["observed"]
                assert observed is not None
                self.assertEqual(result["action"], observed["action"])

        sample = results[0]
        for changes, message in (
            ({"basis": "bound"}, "cannot retain a bound basis"),
            ({"policy": "observed"}, "does not accept transformed input"),
            ({"transform": "context-v1:" + "A" * 64}, "invalid result transform"),
            ({"transform": program.identifier[:-1]}, "invalid result transform"),
            (
                {"transform": program.identifier.replace("v1:", "v2:")},
                "invalid result transform",
            ),
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(tape.TapeError, message),
            ):
                tape.validate_result({**sample, **changes})

    def test_invalid_context_aborts_replay_without_publishing_partial_results(self) -> None:
        row = next(tape.read_tape(_EXAMPLES / "meeting.jsonl"))
        invalid_view = {**row["view"], "mode": "other"}
        invalid_row = {
            **row,
            "key": "second",
            "view": invalid_view,
            "view_sha256": tape.digest(invalid_view),
        }
        source = self.root / "source.jsonl"
        out = self.root / "replay.jsonl"
        tape.write_lines(source, [row, invalid_row])
        program = ContextSpec.load(_EXAMPLES / "meeting-first8.json")
        for policy, transform, error in (
            ("intersection", program, "second.*op 2.*source or target label"),
            ("intersection", program.identifier, "unknown replay transform"),
            ("observed", program, "observed requires the identity transform"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                replay(source, out, policy, transform=transform)
            self.assertEqual(set(self.root.iterdir()), {source})

    def test_transformed_policy_failures_remain_unmeasured(self) -> None:
        row = next(tape.read_tape(_EXAMPLES / "meeting.jsonl"))
        source = self.root / "source.jsonl"
        out = self.root / "replay.jsonl"
        observed = row["observed"]
        assert observed is not None and observed["action"] is not None
        answer = tape.score(row, observed["action"]) | observed
        tape.write_lines(source, [{**row, "answers": [answer]}])
        program = ContextSpec.load(_EXAMPLES / "meeting-first8.json")
        summary = replay(source, out, "intersection", transform=program)
        self.assertEqual(summary, {"decisions": 1, "measured": 0, "unmeasured": 1})
        result = next(tape.read_results(out))
        self.assertEqual(result["transform"], program.identifier)
        self.assertEqual(result["status"], "unmeasured")
        self.assertEqual(result["error"], "invalid_action")
        self.assertIsNone(result["action"])
        self.assertIsNone(result["score"])
