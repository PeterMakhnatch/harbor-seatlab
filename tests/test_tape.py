import hashlib
import json
import os
import unittest
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from seatlab.tape import (
    DECISION_SCHEMA,
    RESULT_SCHEMA,
    TapeError,
    UnknownActionError,
    canonical,
    decision,
    decision_sha256,
    decode,
    digest,
    read_results,
    read_tape,
    score,
    validate_result,
    write_lines,
)

TRIAL = "12345678-1234-4abc-9def-0123456789ab"


def decision_row(**changes: object) -> dict[str, object]:
    view = {"display": "salle é", "messages": []}
    return {
        "schema": DECISION_SCHEMA,
        "key": "handoff:arrival:17:reordered:0",
        "role": "dispatch",
        "native_trial_id": TRIAL,
        "basis": "reconstructed",
        "view": view,
        "view_sha256": digest(view),
        "observed": {"action": None},
        "answers": [
            {"action": None, "optimal": False, "feasible": False},
            {"action": 0, "optimal": True, "feasible": False},
            {"action": "0", "optimal": False, "feasible": True},
        ],
        **changes,
    }


def range_answers(**changes: object) -> dict[str, object]:
    return {
        "kind": "int-range",
        "min": 0,
        "max": 2,
        "include_null": True,
        "optimal": [1],
        "feasible": [0, 2],
        **changes,
    }


def result_row(**changes: object) -> dict[str, object]:
    source = decision(decision_row())
    encoded_view = canonical(source["view"])
    view_sha256 = hashlib.sha256(encoded_view).hexdigest()
    return {
        "schema": RESULT_SCHEMA,
        "key": "handoff:arrival:17:reordered:0",
        "role": "dispatch",
        "native_trial_id": TRIAL,
        "basis": "reconstructed",
        "source_decision_sha256": decision_sha256(source),
        "source_view_sha256": view_sha256,
        "input_sha256": view_sha256,
        "input_bytes": len(encoded_view),
        "policy": "observed",
        "transform": "identity",
        "status": "measured",
        "action": None,
        "score": {"optimal": False, "feasible": False},
        "error": None,
        **changes,
    }


class CanonicalJsonTests(unittest.TestCase):
    def test_canonical_ascii_digest_and_utf8_decode(self):
        value = {"z": [2, 1], "a": "é🙂"}
        expected = b'{"a":"\\u00e9\\ud83d\\ude42","z":[2,1]}'
        self.assertEqual(canonical(value), expected)
        self.assertEqual(digest(value), hashlib.sha256(expected).hexdigest())
        self.assertEqual(decode(expected), value)
        self.assertEqual(decode('{"a":"é🙂","z":[2,1]}'.encode()), value)

    def test_decode_rejects_noncanonical_json_types_and_ambiguous_text(self):
        for data in (
            b'{"outer":{"a":1,"\\u0061":2}}',
            b'{"x":NaN}',
            b'{"x":Infinity}',
            b'{"x":-Infinity}',
            b'{"x":1e999}',
            b'"\\ud800"',
            b'{"\\udfff":1}',
            b'"\xff"',
            "{}".encode("utf-16"),
            b"{} {}",
            b"\n",
        ):
            with self.subTest(data=data), self.assertRaises(TapeError):
                decode(data)

    def test_canonical_rejects_lossy_python_coercions(self):
        for value in ({1: "not a string key"}, {"x": (1,)}, "\ud800", float("inf")):
            with self.subTest(value=value), self.assertRaises(TapeError):
                canonical(value)
        cycle: list[object] = []
        cycle.append(cycle)
        with self.assertRaises(TapeError):
            canonical(cycle)


class DecisionTests(unittest.TestCase):
    def test_explicit_null_action_is_known_without_making_unknown_optimal_or_feasible(self):
        row = decision(decision_row())
        self.assertEqual(row["observed"], {"action": None})
        self.assertEqual(score(row, None), {"optimal": False, "feasible": False})
        self.assertEqual(score(row, 0), {"optimal": True, "feasible": False})
        self.assertEqual(score(row, "0"), {"optimal": False, "feasible": True})
        with self.assertRaises(UnknownActionError):
            score(row, "unknown")
        for invalid in (False, 0.0, [], {}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TapeError) as error:
                    score(row, invalid)
                self.assertNotIsInstance(error.exception, UnknownActionError)

    def test_unknown_observation_is_not_an_observed_null_action(self):
        unobserved = decision(decision_row(observed=None))
        self.assertIsNone(unobserved["observed"])
        design = decision(decision_row(basis="design", native_trial_id=None, observed=None))
        self.assertIsNone(design["native_trial_id"])
        self.assertIsNone(design["observed"])
        for name in ("native_trial_id", "observed"):
            row = decision_row(basis="design", native_trial_id=None, observed=None)
            del row[name]
            with self.subTest(name=name), self.assertRaises(TapeError):
                decision(row)
        with self.assertRaises(TapeError):
            decision(decision_row(basis="design", native_trial_id=None))
        with self.assertRaises(TapeError):
            decision(decision_row(basis="design", observed=None))
        with self.assertRaises(TapeError):
            decision(decision_row(native_trial_id=None, observed=None))

    def test_structurally_valid_unlisted_observation_is_not_coerced(self):
        row = decision(decision_row(observed={"action": 9}))
        self.assertEqual(row["observed"], {"action": 9})
        with self.assertRaises(UnknownActionError):
            score(row, 9)

    def test_full_decision_digest_changes_when_only_offline_answers_change(self):
        raw = decision_row(answers=range_answers())
        original = decision(raw)
        changed = decision({**raw, "answers": range_answers(optimal=[0])})
        self.assertEqual(original["view_sha256"], changed["view_sha256"])
        self.assertEqual(
            decision_sha256(original), hashlib.sha256(canonical(raw)).hexdigest()
        )
        self.assertNotEqual(decision_sha256(original), decision_sha256(changed))

    def test_nonportable_selectors_and_digest_mismatch_fail(self):
        for changes in (
            {"key": ""},
            {"key": "é"},
            {"key": "key\n"},
            {"key": "../native"},
            {"key": r"relative\native"},
            {"role": "dispatch team"},
            {"role": "r" * 33},
            {"basis": "guess"},
            {"native_trial_id": TRIAL.upper()},
            {"native_trial_id": TRIAL.replace("-", "")},
            {"view_sha256": "A" * 64},
            {"view": []},
            {"extra": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(TapeError):
                decision(decision_row(**changes))
        with self.assertRaisesRegex(TapeError, "view_sha256 does not match"):
            decision(decision_row(view={"messages": ["changed"]}))

    def test_answer_action_and_flags_are_exact_types(self):
        for answers in (
            [{"action": True, "optimal": False, "feasible": True}],
            [{"action": 1.0, "optimal": False, "feasible": True}],
            [{"action": 1, "optimal": 0, "feasible": True}],
            [{"action": 1, "optimal": False, "feasible": 1}],
            [{"action": 1, "optimal": False, "feasible": True, "source": "extra"}],
        ):
            with self.subTest(answers=answers), self.assertRaises(TapeError):
                decision(decision_row(answers=answers))
        with self.assertRaisesRegex(TapeError, "duplicate answer action"):
            item = {"action": None, "optimal": False, "feasible": False}
            decision(decision_row(answers=[item, {**item, "optimal": True}]))

    def test_compact_range_matches_explicit_scores_without_expansion(self):
        compact = decision(decision_row(answers=range_answers()))
        explicit = decision(
            decision_row(
                answers=[
                    {"action": 0, "optimal": False, "feasible": True},
                    {"action": 1, "optimal": True, "feasible": False},
                    {"action": 2, "optimal": False, "feasible": True},
                    {"action": None, "optimal": False, "feasible": False},
                ]
            )
        )
        for action in (None, 0, 1, 2):
            with self.subTest(action=action):
                self.assertEqual(score(compact, action), score(explicit, action))
        for action in (-1, 3, "1"):
            with self.subTest(action=action), self.assertRaises(UnknownActionError):
                score(compact, action)
        no_null = decision(decision_row(answers=range_answers(include_null=False)))
        with self.assertRaises(UnknownActionError):
            score(no_null, None)

    def test_compact_range_checks_bounds_order_duplicates_and_type(self):
        for changes in (
            {"kind": "approximate"},
            {"min": True},
            {"max": 2.0},
            {"min": 3},
            {"include_null": 1},
            {"feasible": [2, 0]},
            {"optimal": [1, 1]},
            {"optimal": [True]},
            {"feasible": [None]},
            {"feasible": [3]},
            {"optimal": {"1": True}},
            {"extra": 0},
        ):
            with self.subTest(changes=changes), self.assertRaises(TapeError):
                decision(decision_row(answers=range_answers(**changes)))


class ResultTests(unittest.TestCase):
    def test_null_can_be_a_completed_result_or_an_unmeasured_result(self):
        measured = validate_result(result_row())
        unmeasured = validate_result(
            result_row(
                status="unmeasured",
                score=None,
                error="missing_observation",
                basis="design",
                native_trial_id=None,
            )
        )
        self.assertIsNone(measured["action"])
        self.assertIsNone(unmeasured["action"])
        self.assertIsNotNone(measured["score"])
        self.assertIsNone(unmeasured["score"])
        self.assertIsNone(measured["error"])
        self.assertEqual(unmeasured["error"], "missing_observation")
        with self.assertRaises(TapeError):
            validate_result(result_row(basis="design", native_trial_id=None))

    def test_result_checks_status_and_safe_error_contract(self):
        for changes in (
            {"status": "unknown"},
            {"error": "invalid_action"},
            {"action": True},
            {"score": None},
            {"score": {"optimal": 1, "feasible": True}},
            {"status": "unmeasured", "error": "missing_observation"},
            {"status": "unmeasured", "score": None, "action": 1, "error": "invalid_action"},
            {"status": "unmeasured", "score": None, "error": None},
            {"status": "unmeasured", "score": None, "error": "policy printed diagnostics"},
            {"status": "unmeasured", "score": None, "error": "timeout"},
            {
                "status": "unmeasured",
                "score": None,
                "error": "missing_observation",
                "policy": "external",
            },
            {"policy": "external-with-user-path"},
            {"transform": "reorder"},
        ):
            with self.subTest(changes=changes), self.assertRaises(TapeError):
                validate_result(result_row(**changes))
        failed = result_row(
            status="unmeasured", score=None, error="timeout", policy="external"
        )
        self.assertEqual(validate_result(failed), failed)
        del failed["error"]
        with self.assertRaises(TapeError):
            validate_result(failed)

    def test_result_requires_lowercase_source_decision_digest(self):
        row = result_row()
        del row["source_decision_sha256"]
        with self.assertRaisesRegex(TapeError, "exactly"):
            validate_result(row)
        for value in (None, "a" * 63, "A" * 64, "z" * 64):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TapeError, "source_decision_sha256"):
                    validate_result(result_row(source_decision_sha256=value))

    def test_result_checks_view_commitments_and_transform_basis(self):
        for changes in (
            {"source_view_sha256": "A" * 64},
            {"input_sha256": "0" * 64},
            {"input_bytes": 1},
            {"input_bytes": True},
            {"input_bytes": 2.0},
            {"transform": "latest"},
            {"transform": "latest", "policy": "arrival", "basis": "bound"},
        ):
            with self.subTest(changes=changes), self.assertRaises(TapeError):
                validate_result(result_row(**changes))
        for changed in ({}, {"input_sha256": "0" * 64}):
            row = result_row(transform="latest", policy="arrival", **changed)
            self.assertEqual(validate_result(row), row)


class LedgerFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.directory / "ledger.jsonl"

    def test_native_trial_ids_may_repeat_but_record_keys_may_not(self):
        first = decision(decision_row(key="decision:0"))
        second = decision(decision_row(key="decision:1"))
        write_lines(self.path, [first, second])
        self.assertEqual(list(read_tape(self.path)), [first, second])
        self.assertEqual(
            self.path.read_bytes(), canonical(first) + b"\n" + canonical(second) + b"\n"
        )
        self.path.write_bytes(canonical(first) + b"\n" + canonical(first))
        with self.assertRaisesRegex(TapeError, r":2: duplicate record key"):
            list(read_tape(self.path))

    def test_result_reader_checks_duplicate_keys_and_record_type(self):
        first = validate_result(result_row())
        write_lines(self.path, [first])
        self.assertEqual(list(read_results(self.path)), [first])
        self.path.write_bytes(canonical(first) + b"\n" + canonical(first))
        with self.assertRaisesRegex(TapeError, r":2: duplicate record key"):
            list(read_results(self.path))
        self.path.write_bytes(canonical(decision_row()))
        with self.assertRaisesRegex(TapeError, r":1: result must be an object"):
            list(read_results(self.path))

    def test_reader_accepts_utf8_crlf_and_unterminated_final_line(self):
        first = decision_row(key="decision:0")
        second = decision_row(key="decision:1")
        raw = json.dumps(first, ensure_ascii=False).encode() + b"\r\n" + canonical(second)
        self.path.write_bytes(raw)
        self.assertEqual(list(read_tape(self.path)), [first, second])
        self.path.write_bytes(raw + b"\n\n")
        with self.assertRaisesRegex(TapeError, r":3: invalid UTF-8 JSON"):
            list(read_tape(self.path))

    def test_per_line_byte_limit_excludes_crlf_but_not_trailing_spaces(self):
        row = decision(decision_row())
        line = canonical(row)
        self.path.write_bytes(line + b"\r\n")
        with patch("seatlab.tape.MAX_LINE_BYTES", len(line)):
            self.assertEqual(list(read_tape(self.path)), [row])
            self.path.write_bytes(line + b" \n")
            with self.assertRaisesRegex(TapeError, r":1: JSON line exceeds"):
                list(read_tape(self.path))
        other = self.directory / "large.jsonl"
        with patch("seatlab.tape.MAX_LINE_BYTES", len(line) - 1):
            with self.assertRaisesRegex(TapeError, "JSON line exceeds"):
                write_lines(other, [row])
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_existing_output_is_not_replaced_or_consumed(self):
        self.path.write_bytes(b"unchanged")

        def records() -> Iterator[object]:
            self.fail("writer consumed records for an existing output")
            yield None

        with self.assertRaises(FileExistsError):
            write_lines(self.path, records())
        self.assertEqual(self.path.read_bytes(), b"unchanged")

    def test_dangling_symlink_is_not_replaced(self):
        target = self.directory / "missing.jsonl"
        try:
            self.path.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        with self.assertRaises(FileExistsError):
            write_lines(self.path, [])
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(self.path.readlink(), target)
        self.assertFalse(target.exists())

    def test_destination_created_after_writer_starts_is_not_replaced(self):
        def raced_records() -> Iterator[object]:
            yield decision(decision_row())
            self.assertFalse(os.path.lexists(self.path))
            self.path.write_bytes(b"other writer")

        with self.assertRaises(FileExistsError):
            write_lines(self.path, raced_records())
        self.assertEqual(self.path.read_bytes(), b"other writer")
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_late_validation_failure_leaves_no_output_or_temporary_file(self):
        def invalid_records() -> Iterator[object]:
            yield decision(decision_row())
            yield decision(decision_row(view_sha256="0" * 64))

        with self.assertRaisesRegex(TapeError, "view_sha256 does not match"):
            write_lines(self.path, invalid_records())
        self.assertEqual(list(self.directory.iterdir()), [])
