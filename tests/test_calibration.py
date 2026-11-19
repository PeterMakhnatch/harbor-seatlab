import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from seatlab import tape as wire
from seatlab.calibration import calibrate
from seatlab.context import ContextSpec
from seatlab.replay import replay

_SOURCE = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_TARGET = "be656986-a740-4e46-999d-21410909878c"
_OTHER = "fe8487a3-882b-4697-baa0-248e8fed964c"
_ANSWERS: wire.IntRangeAnswers = {
    "kind": "int-range",
    "min": 0,
    "max": 10,
    "include_null": True,
    "optimal": [1, 2],
    "feasible": [1, 2, 3],
}
_Pair = tuple[str, wire.Decision | None, wire.Decision | None]


def _view(left: list[int], right: list[int]) -> dict[str, object]:
    return {"reports": {"left": left, "right": right}}


def _decision(
    case: str,
    side: str,
    action: wire.Action = 1,
    *,
    view: dict[str, object] | None = None,
    basis: wire.Basis = "bound",
    observed: bool = True,
    answers: list[wire.Answer] | wire.IntRangeAnswers | None = None,
) -> wire.Decision:
    if view is None:
        view = _view([1, 2], [1, 2])
    return wire.decision(
        {
            "schema": wire.DECISION_SCHEMA,
            "key": f"{case}:{side}",
            "role": "receiver",
            "native_trial_id": _SOURCE if side == "source" else _TARGET,
            "basis": basis,
            "view": view,
            "view_sha256": wire.digest(view),
            "observed": {"action": action} if observed else None,
            "answers": _ANSWERS if answers is None else answers,
        }
    )


def _receipt(case: str, side: str, row: wire.Decision | None) -> dict[str, object]:
    return {
        "key": row["key"] if row is not None else f"{case}:{side}",
        "case_sha256": wire.digest(case),
        "native_trial_id": row["native_trial_id"] if row is not None else None,
        "decision_sha256": wire.decision_sha256(row) if row is not None else None,
    }


def _manifest(pairs: list[_Pair]) -> dict[str, object]:
    return {
        "schema": "seatlab.calibration/v1",
        "pairs": [
            {
                "id": case,
                "source": _receipt(case, "source", source),
                "target": _receipt(case, "target", target),
            }
            for case, source, target in pairs
        ],
    }


class CalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _files(
        self,
        pairs: list[_Pair],
        *,
        policy: str = "intersection",
        transform: str | ContextSpec = "identity",
    ) -> tuple[Path, Path, Path]:
        root = Path(tempfile.mkdtemp(dir=self.root))
        tape_path = root / "decisions.jsonl"
        source_path = root / "sources.jsonl"
        prediction_path = root / "predictions.jsonl"
        pairs_path = root / "pairs.json"
        wire.write_lines(
            tape_path,
            (
                row
                for _, source, target in pairs
                for row in (source, target)
                if row is not None
            ),
        )
        wire.write_lines(source_path, (row for _, row, _ in pairs if row is not None))
        replay(source_path, prediction_path, policy, transform=transform)
        pairs_path.write_bytes(wire.canonical(_manifest(pairs)))
        return tape_path, prediction_path, pairs_path

    def test_denominators_keep_all_cases_and_distinguish_measured_null(self) -> None:
        null_view = _view([], [1])
        pairs = [
            ("01-match", _decision("01-match", "source", 2), _decision("01-match", "target")),
            (
                "02-null",
                _decision("02-null", "source", None, view=null_view),
                _decision("02-null", "target", None, view=null_view),
            ),
            (
                "03-input",
                _decision("03-input", "source"),
                _decision("03-input", "target", view=_view([1], [1])),
            ),
            (
                "04-action",
                _decision("04-action", "source"),
                _decision("04-action", "target", 2),
            ),
            ("05-score", _decision("05-score", "source"), _decision("05-score", "target", 3)),
            ("06-source-missing", None, _decision("06-source-missing", "target")),
            ("07-target-missing", _decision("07-target-missing", "source"), None),
            ("08-both-missing", None, None),
            (
                "09-reconstructed",
                _decision("09-reconstructed", "source"),
                _decision("09-reconstructed", "target", basis="reconstructed"),
            ),
            (
                "10-unobserved",
                _decision("10-unobserved", "source"),
                _decision("10-unobserved", "target", observed=False),
            ),
            (
                "11-unscorable",
                _decision("11-unscorable", "source"),
                _decision("11-unscorable", "target", 99),
            ),
            (
                "12-unmeasured",
                _decision("12-unmeasured", "source", view=_view([99], [99])),
                _decision("12-unmeasured", "target"),
            ),
        ]
        report = calibrate(*self._files(list(reversed(pairs))))
        self.assertEqual(report["schema"], "seatlab.calibration-result/v1")
        self.assertEqual(
            report["counts"],
            {
                "pairs": 12,
                "source_available": 10,
                "target_available": 10,
                "source_missing": 2,
                "target_missing": 2,
                "target_acknowledged": 8,
                "native_action_compared": 8,
                "native_action_changed": 4,
                "prediction_measured": 9,
                "prediction_unmeasured": 1,
                "prediction_opaque": 0,
                "prediction_skipped": 0,
                "input_compared": 5,
                "input_matches": 4,
                "action_compared": 4,
                "action_matches": 2,
                "score_compared": 4,
                "score_matches": 3,
                "coincident_action_matches": 1,
                "reproduced": 2,
            },
        )
        by_id = {row["id"]: row for row in report["pairs"]}
        self.assertEqual(list(by_id), [case for case, _, _ in pairs])
        self.assertEqual(by_id["01-match"]["source"]["native_trial_id"], _SOURCE)
        self.assertEqual(by_id["01-match"]["target"]["native_trial_id"], _TARGET)
        self.assertTrue(by_id["01-match"]["native_action_changed"])
        self.assertTrue(by_id["02-null"]["reproduced"])
        self.assertEqual(by_id["02-null"]["prediction"]["status"], "measured")
        self.assertIsNone(by_id["02-null"]["prediction"]["action"])
        self.assertEqual(by_id["02-null"]["target"]["observed"], {"action": None})
        self.assertEqual(
            by_id["02-null"]["target"]["score"], {"optimal": False, "feasible": False}
        )
        self.assertEqual(by_id["03-input"]["reasons"], ["input_mismatch"])
        self.assertTrue(by_id["03-input"]["action_match"])
        self.assertEqual(by_id["04-action"]["reasons"], ["action_mismatch"])
        self.assertTrue(by_id["04-action"]["score_match"])
        self.assertEqual(by_id["05-score"]["reasons"], ["action_mismatch", "score_mismatch"])
        self.assertIsNone(by_id["06-source-missing"]["prediction"])
        self.assertIsNone(by_id["07-target-missing"]["target"]["observed"])
        self.assertEqual(
            by_id["08-both-missing"]["reasons"], ["source_missing", "target_missing"]
        )
        for case, reason in (
            ("09-reconstructed", "target_reconstructed"),
            ("10-unobserved", "target_missing_observation"),
            ("11-unscorable", "target_unscorable_action"),
            ("12-unmeasured", "prediction_unmeasured"),
        ):
            with self.subTest(case=case):
                self.assertEqual(by_id[case]["status"], "skipped")
                self.assertEqual(by_id[case]["reasons"], [reason])
                self.assertIsNone(by_id[case]["input_match"])
                self.assertIsNone(by_id[case]["action_match"])
                self.assertFalse(by_id[case]["reproduced"])

    def test_type_exact_actions_and_independent_score_match(self) -> None:
        answers = [
            {"action": 1, "optimal": True, "feasible": True},
            {"action": "1", "optimal": True, "feasible": True},
        ]
        files = self._files(
            [
                (
                    "types",
                    _decision("types", "source", answers=answers),
                    _decision("types", "target", "1", answers=answers),
                )
            ]
        )
        pair = calibrate(*files)["pairs"][0]
        self.assertTrue(pair["input_match"])
        self.assertTrue(pair["score_match"])
        self.assertTrue(pair["native_action_changed"])
        self.assertFalse(pair["action_match"])
        self.assertFalse(pair["reproduced"])

    def test_context_identifier_alone_cannot_verify_a_reproduction(self) -> None:
        spec = ContextSpec(
            {
                "schema": "seatlab.context/v1",
                "ops": [{"op": "take", "path": ["reports", "*"], "count": 1}],
            }
        )
        source = _decision("context", "source", 2, view=_view([1, 2], [2]))
        target = _decision("context", "target", None, view=_view([1], [2]))
        files = self._files([("context", source, target)], transform=spec)
        opaque = calibrate(*files)
        row = opaque["pairs"][0]
        self.assertEqual(row["status"], "opaque")
        self.assertEqual(row["prediction"]["input_verification"], "known_view")
        self.assertEqual(row["reasons"], ["prediction_context_unverified"])
        self.assertTrue(row["input_match"])
        self.assertTrue(row["action_match"])
        self.assertTrue(row["score_match"])
        self.assertFalse(row["reproduced"])
        self.assertEqual(opaque["counts"]["input_compared"], 0)
        self.assertEqual(opaque["counts"]["prediction_opaque"], 1)

        verified = calibrate(*files, context=spec)
        self.assertEqual(verified["counts"]["reproduced"], 1)
        self.assertEqual(verified["pairs"][0]["prediction"]["input_verification"], "derived")
        wrong = ContextSpec({"schema": "seatlab.context/v1", "ops": []})
        with self.assertRaisesRegex(wire.TapeError, "context identifier"):
            calibrate(*files, context=wrong)

        predicted = next(wire.read_results(files[1]))
        predicted["input_sha256"] = source["view_sha256"]
        predicted["input_bytes"] = len(wire.canonical(source["view"]))
        files[1].write_bytes(wire.canonical(predicted) + b"\n")
        with self.assertRaisesRegex(wire.TapeError, "input commitment"):
            calibrate(*files, context=spec)

    def test_latest_input_is_recomputed(self) -> None:
        view = {
            "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
            "messages": [
                {"revision": 2, "available": ["A"]},
                {"revision": 1, "available": ["B"]},
            ],
            "pooled_available": None,
        }
        answers = [
            {"action": "A", "optimal": True, "feasible": True},
            {"action": "B", "optimal": False, "feasible": True},
        ]
        target_view = {**view, "messages": [{"revision": 2, "available": ["A"]}]}
        source = _decision("latest", "source", "B", view=view, answers=answers)
        target = _decision("latest", "target", "A", view=target_view, answers=answers)
        files = self._files(
            [("latest", source, target)], policy="arrival", transform="latest"
        )
        self.assertEqual(calibrate(*files)["counts"]["reproduced"], 1)
        predicted = next(wire.read_results(files[1]))
        predicted["input_sha256"] = "0" * 64
        files[1].write_bytes(wire.canonical(predicted) + b"\n")
        with self.assertRaisesRegex(wire.TapeError, "input commitment"):
            calibrate(*files)

    def test_external_and_observed_policies_never_execute_in_calibration(self) -> None:
        pairs = [("policy", _decision("policy", "source"), _decision("policy", "target"))]
        files = self._files(pairs, policy="observed")
        prediction = next(wire.read_results(files[1]))
        with (
            patch("seatlab.replay._choose", side_effect=AssertionError("policy execution")),
            patch(
                "seatlab.replay._external", side_effect=AssertionError("external execution")
            ),
        ):
            observed = calibrate(*files)
            self.assertEqual(observed["counts"]["prediction_skipped"], 1)
            self.assertEqual(observed["pairs"][0]["status"], "skipped")
            self.assertEqual(observed["pairs"][0]["reasons"], ["prediction_observed_policy"])
            prediction["policy"] = "external"
            files[1].write_bytes(wire.canonical(prediction) + b"\n")
            external = calibrate(*files)
            self.assertEqual(external["counts"]["prediction_opaque"], 1)
            self.assertEqual(external["counts"]["input_compared"], 0)
            self.assertEqual(external["counts"]["reproduced"], 0)
            row = external["pairs"][0]
            self.assertEqual(row["status"], "opaque")
            self.assertTrue(row["input_match"])
            self.assertTrue(row["action_match"])
            self.assertTrue(row["score_match"])

    def test_rejects_changed_replay_source_score_action_or_input_size(self) -> None:
        files = self._files(
            [("case", _decision("case", "source"), _decision("case", "target"))]
        )
        prediction = next(wire.read_results(files[1]))
        for fields, message in (
            ({"role": "other"}, "replay source"),
            ({"native_trial_id": _OTHER}, "replay source"),
            ({"basis": "reconstructed"}, "replay source"),
            ({"source_decision_sha256": "0" * 64}, "replay source"),
            ({"source_view_sha256": "0" * 64, "input_sha256": "0" * 64}, "replay source"),
            ({"input_bytes": prediction["input_bytes"] + 1}, "input byte count"),
            ({"score": {"optimal": False, "feasible": True}}, "score does not match"),
            ({"action": 2}, "action does not match deterministic policy"),
            ({"action": 99}, "unscorable action"),
            (
                {
                    "status": "unmeasured",
                    "action": None,
                    "score": None,
                    "error": "invalid_action",
                },
                "omits a deterministic measurement",
            ),
        ):
            with self.subTest(fields=fields):
                files[1].write_bytes(wire.canonical({**prediction, **fields}) + b"\n")
                with self.assertRaisesRegex(wire.TapeError, message):
                    calibrate(*files)

    def test_receipts_and_complete_join_are_required(self) -> None:
        source = _decision("case", "source")
        target = _decision("case", "target")
        files = self._files([("case", source, target)])
        original_manifest = _manifest([("case", source, target)])
        for arm, field, value in (
            ("source", "native_trial_id", _OTHER),
            ("target", "decision_sha256", "0" * 64),
        ):
            with self.subTest(arm=arm, field=field):
                manifest = deepcopy(original_manifest)
                manifest["pairs"][0][arm][field] = value
                files[2].write_bytes(wire.canonical(manifest))
                with self.assertRaisesRegex(wire.TapeError, "receipt does not match"):
                    calibrate(*files)

        files[2].write_bytes(wire.canonical(original_manifest))
        prediction = next(wire.read_results(files[1]))
        files[1].write_bytes(b"")
        with self.assertRaisesRegex(wire.TapeError, "replay coverage.*1 missing"):
            calibrate(*files)
        target_prediction = {**prediction, "key": target["key"]}
        files[1].write_bytes(
            wire.canonical(prediction) + b"\n" + wire.canonical(target_prediction) + b"\n"
        )
        with self.assertRaisesRegex(wire.TapeError, "replay coverage.*1 unexpected"):
            calibrate(*files)
        files[1].write_bytes(wire.canonical(prediction) + b"\n")
        files[0].write_bytes(wire.canonical(source) + b"\n")
        with self.assertRaisesRegex(wire.TapeError, "tape coverage.*1 missing"):
            calibrate(*files)
        files[2].write_bytes(wire.canonical(_manifest([("case", None, None)])))
        design = {**source, "native_trial_id": None, "basis": "design", "observed": None}
        files[0].write_bytes(wire.canonical(design) + b"\n")
        with self.assertRaisesRegex(wire.TapeError, "tape coverage.*1 unexpected"):
            calibrate(*files)

    def test_equal_role_and_offline_answers_are_required_even_for_unbound_target(
        self,
    ) -> None:
        source = _decision("case", "source")
        target = _decision("case", "target", basis="reconstructed")
        wrong_role = {**target, "role": "other"}
        wrong_answers = {**target, "answers": {**_ANSWERS, "optimal": [2]}}
        for wrong, message in (
            (wrong_role, "different roles"),
            (wrong_answers, "different offline answers"),
        ):
            with self.subTest(message=message):
                files = self._files([("case", source, wire.decision(wrong))])
                with self.assertRaisesRegex(wire.TapeError, message):
                    calibrate(*files)

    def test_manifest_has_strict_fields_and_unique_pair_case_side_keys(self) -> None:
        source = _decision("a", "source")
        target = _decision("a", "target")
        files = self._files([("a", source, target), ("b", None, None)])
        manifest = _manifest([("a", source, target), ("b", None, None)])
        for index, arm, field, value, message in (
            (1, None, "id", "a", "duplicate calibration pair id"),
            (1, "source", "key", "a:source", "duplicate calibration side key"),
            (1, "target", "case_sha256", wire.digest("a"), "different case_sha256"),
            (1, "target", "decision_sha256", "0" * 64, "native_trial_id"),
            (1, "target", "native_trial_id", _TARGET, "decision_sha256"),
            (0, "target", "native_trial_id", _SOURCE, "used on both sides"),
            (0, "source", "extra", True, "documented side fields"),
            (0, "source", "case_sha256", "A" * 64, "lowercase SHA-256"),
            (0, None, "id", "é", "portable ASCII label"),
        ):
            with self.subTest(field=field, value=value):
                copy = deepcopy(manifest)
                row = copy["pairs"][index]
                (row if arm is None else row[arm])[field] = value
                files[2].write_bytes(wire.canonical(copy))
                with self.assertRaisesRegex(wire.TapeError, message):
                    calibrate(*files)
        manifest["pairs"][1]["source"]["case_sha256"] = wire.digest("a")
        manifest["pairs"][1]["target"]["case_sha256"] = wire.digest("a")
        files[2].write_bytes(wire.canonical(manifest))
        with self.assertRaisesRegex(wire.TapeError, "duplicate case_sha256"):
            calibrate(*files)
        files[2].write_bytes(b'{"schema":"seatlab.calibration/v1","pairs":[],"pairs":[]}')
        with self.assertRaisesRegex(wire.TapeError, "JSON object contains a duplicate key"):
            calibrate(*files)
