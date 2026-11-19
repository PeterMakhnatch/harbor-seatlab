import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seatlab import tape as wire
from seatlab.calibration import calibrate
from seatlab.context import ContextSpec
from seatlab.replay import replay

_SOURCE = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_TARGET = "be656986-a740-4e46-999d-21410909878c"
_ANSWERS: wire.IntRangeAnswers = {
    "kind": "int-range",
    "min": 0,
    "max": 10,
    "include_null": True,
    "optimal": [1, 2],
    "feasible": [1, 2, 3],
}
_SPEC = ContextSpec(
    {
        "schema": "seatlab.context/v1",
        "ops": [{"op": "take", "path": ["reports", "*"], "count": 1}],
    }
)
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


def _external_result(
    source: wire.Decision,
    *,
    action: wire.Action = 1,
    transform: str | ContextSpec = "identity",
    status: str = "measured",
    error: wire.ReplayError | None = None,
) -> wire.Result:
    if isinstance(transform, ContextSpec):
        transformed = transform.apply(source["view"])
        transform_id = transform.identifier
    else:
        transformed = source["view"]
        transform_id = transform
    data = wire.canonical(transformed)
    basis = source["basis"]
    if basis == "bound" and transform_id != "identity":
        basis = "reconstructed"
    score = wire.score(source, action) if status == "measured" else None
    return wire.validate_result(
        {
            "schema": wire.RESULT_SCHEMA,
            "key": source["key"],
            "role": source["role"],
            "native_trial_id": source["native_trial_id"],
            "basis": basis,
            "source_decision_sha256": wire.decision_sha256(source),
            "source_view_sha256": source["view_sha256"],
            "input_sha256": wire.digest(transformed),
            "input_bytes": len(data),
            "policy": "external",
            "transform": transform_id,
            "status": status,
            "action": action if status == "measured" else None,
            "score": score,
            "error": error,
        }
    )


class ExternalCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _write_cohort(
        self, pairs: list[_Pair], predictions: list[wire.Result]
    ) -> tuple[Path, Path, Path]:
        root = Path(tempfile.mkdtemp(dir=self.root))
        tape_path = root / "decisions.jsonl"
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
        wire.write_lines(prediction_path, predictions)
        pairs_path.write_bytes(wire.canonical(_manifest(pairs)))
        return tape_path, prediction_path, pairs_path

    def test_builtin_only_cohort_omits_external_fields(self) -> None:
        root = Path(tempfile.mkdtemp(dir=self.root))
        pairs: list[_Pair] = [
            (
                "01-reproduced",
                _decision("01-reproduced", "source", 2),
                _decision("01-reproduced", "target", 1),
            ),
            ("02-missing", None, _decision("02-missing", "target", 1)),
            (
                "03-unverified-context",
                _decision("03-unverified-context", "source", 2, view=_view([1, 2], [2])),
                _decision("03-unverified-context", "target", None, view=_view([1], [2])),
            ),
        ]
        tape_path = root / "decisions.jsonl"
        source_path = root / "sources.jsonl"
        prediction_path = root / "predictions.jsonl"
        pairs_path = root / "pairs.json"
        wire.write_lines(
            tape_path,
            (row for _, s, t in pairs for row in (s, t) if row is not None),
        )
        wire.write_lines(source_path, (s for _, s, _ in pairs if s is not None))
        pairs_path.write_bytes(wire.canonical(_manifest(pairs)))

        replay(source_path, prediction_path, "intersection", transform="identity")
        report = calibrate(tape_path, prediction_path, pairs_path)
        self.assertEqual(set(report), {"schema", "counts", "pairs"})
        self.assertNotIn("external_observations", report)
        for row in report["pairs"]:
            self.assertNotIn("external_observation", row)
        self.assertEqual(report["counts"]["reproduced"], 1)

        # Even with an opaque built-in context (recipe withheld), no external fields appear.
        opaque_pred = root / "opaque.jsonl"
        replay(source_path, opaque_pred, "intersection", transform=_SPEC)
        opaque_report = calibrate(tape_path, opaque_pred, pairs_path)
        self.assertEqual(set(opaque_report), {"schema", "counts", "pairs"})
        self.assertEqual(opaque_report["counts"]["prediction_opaque"], 2)
        for row in opaque_report["pairs"]:
            self.assertNotIn("external_observation", row)

        # Observed policy also omits external fields.
        observed_pred = root / "observed.jsonl"
        replay(source_path, observed_pred, "observed", transform="identity")
        observed_report = calibrate(tape_path, observed_pred, pairs_path)
        self.assertEqual(set(observed_report), {"schema", "counts", "pairs"})
        for row in observed_report["pairs"]:
            self.assertNotIn("external_observation", row)

    def test_external_observation_boundaries_and_denominators(self) -> None:
        null_view = _view([], [1])
        diff_view = _view([9], [9])
        pairs_and_preds: list[
            tuple[str, wire.Decision | None, wire.Decision | None, wire.Result | None]
        ] = []

        # 01: Derived input, bound target, measured action, scorable, all match.
        s01 = _decision("01-full-match", "source", 2)
        t01 = _decision("01-full-match", "target", 1)
        pairs_and_preds.append(("01-full-match", s01, t01, _external_result(s01, action=1)))

        # 02: Distinct measured explicit null matching target null.
        s02 = _decision("02-null-match", "source", 1, view=null_view)
        t02 = _decision("02-null-match", "target", None, view=null_view)
        pairs_and_preds.append(("02-null-match", s02, t02, _external_result(s02, action=None)))

        # 03: Measured explicit null vs non-null target (action & score mismatch).
        s03 = _decision("03-null-mismatch", "source", 1, view=null_view)
        t03 = _decision("03-null-mismatch", "target", 1, view=null_view)
        pairs_and_preds.append(
            ("03-null-mismatch", s03, t03, _external_result(s03, action=None))
        )

        # 04: Input match, action mismatch, score match (1 vs 2 both optimal & feasible).
        s04 = _decision("04-action-mismatch-score-match", "source", 1)
        t04 = _decision("04-action-mismatch-score-match", "target", 2)
        pairs_and_preds.append(
            ("04-action-mismatch-score-match", s04, t04, _external_result(s04, action=1))
        )

        # 05: Input mismatch with coincident equal action -> no action/score credit.
        s05 = _decision("05-input-mismatch-coincident", "source", 1)
        t05 = _decision("05-input-mismatch-coincident", "target", 1, view=diff_view)
        pairs_and_preds.append(
            ("05-input-mismatch-coincident", s05, t05, _external_result(s05, action=1))
        )

        # 06: External execution failed with bound matching target -> input only.
        s06 = _decision("06-exec-failed", "source", 1)
        t06 = _decision("06-exec-failed", "target", 1)
        pairs_and_preds.append(
            (
                "06-exec-failed",
                s06,
                t06,
                _external_result(s06, status="unmeasured", error="policy_failed"),
            )
        )

        # 07: Target action unknown with bound matching target -> input only.
        s07 = _decision("07-target-unobserved", "source", 1)
        t07 = _decision("07-target-unobserved", "target", observed=False)
        pairs_and_preds.append(
            ("07-target-unobserved", s07, t07, _external_result(s07, action=1))
        )

        # 08: Target action unscorable (99) -> input & action compared, score None.
        s08 = _decision("08-target-unscorable", "source", 1)
        t08 = _decision("08-target-unscorable", "target", 99)
        pairs_and_preds.append(
            ("08-target-unscorable", s08, t08, _external_result(s08, action=1))
        )

        # 09: Unbound target (basis="reconstructed") -> nothing compared.
        s09 = _decision("09-unbound-target", "source", 1)
        t09 = _decision("09-unbound-target", "target", 1, basis="reconstructed")
        pairs_and_preds.append(
            ("09-unbound-target", s09, t09, _external_result(s09, action=1))
        )

        # 10: Missing target (target=None) -> nothing compared, pair status "missing".
        s10 = _decision("10-missing-target", "source", 1)
        pairs_and_preds.append(
            ("10-missing-target", s10, None, _external_result(s10, action=1))
        )

        # 11: Missing source (source=None) -> no prediction, no external_observation.
        t11 = _decision("11-missing-source", "target", 1)
        pairs_and_preds.append(("11-missing-source", None, t11, None))

        # 12: External execution failed & target view differs -> input_match False.
        s12 = _decision("12-exec-failed-input-mismatch", "source", 1)
        t12 = _decision("12-exec-failed-input-mismatch", "target", 1, view=diff_view)
        pairs_and_preds.append(
            (
                "12-exec-failed-input-mismatch",
                s12,
                t12,
                _external_result(s12, status="unmeasured", error="timeout"),
            )
        )

        pairs = [(cid, s, t) for cid, s, t, _ in pairs_and_preds]
        preds = [p for _, _, _, p in pairs_and_preds if p is not None]
        files = self._write_cohort(pairs, preds)

        with (
            patch("seatlab.replay._choose", side_effect=AssertionError("policy execution")),
            patch(
                "seatlab.replay._external", side_effect=AssertionError("external execution")
            ),
            patch("subprocess.Popen", side_effect=AssertionError("subprocess spawned")),
        ):
            report = calibrate(*files)

        # Strict v1 top-level counts remain unchanged and give zero reproduced credit.
        self.assertEqual(report["counts"]["pairs"], 12)
        self.assertEqual(report["counts"]["prediction_opaque"], 11)
        self.assertEqual(report["counts"]["input_compared"], 0)
        self.assertEqual(report["counts"]["input_matches"], 0)
        self.assertEqual(report["counts"]["action_compared"], 0)
        self.assertEqual(report["counts"]["action_matches"], 0)
        self.assertEqual(report["counts"]["score_compared"], 0)
        self.assertEqual(report["counts"]["score_matches"], 0)
        self.assertEqual(report["counts"]["coincident_action_matches"], 0)
        self.assertEqual(report["counts"]["reproduced"], 0)

        # External observation counts across 11 external predictions:
        # inputs_compared: 01,02,03,04,05,06,07,08,12 = 9 (09 unbound, 10 missing)
        # input_matches: 01,02,03,04,06,07,08 = 7 (05,12 mismatched)
        # actions_compared: 01,02,03,04,08 = 5 (06 unmeasured, 07 unobserved)
        # action_matches: 01,02 = 2 (03,04,08 mismatched)
        # scores_compared: 01,02,03,04 = 4 (08 unscorable)
        # score_matches: 01,02,04 = 3 (03 mismatched)
        self.assertEqual(
            report["external_observations"],
            {
                "predictions": 11,
                "inputs_compared": 9,
                "input_matches": 7,
                "actions_compared": 5,
                "action_matches": 2,
                "scores_compared": 4,
                "score_matches": 3,
            },
        )

        by_id = {row["id"]: row for row in report["pairs"]}
        expected_obs: dict[str, dict[str, object]] = {
            "01-full-match": {
                "input_match": True,
                "action_match": True,
                "score_match": True,
                "execution_attested": False,
            },
            "02-null-match": {
                "input_match": True,
                "action_match": True,
                "score_match": True,
                "execution_attested": False,
            },
            "03-null-mismatch": {
                "input_match": True,
                "action_match": False,
                "score_match": False,
                "execution_attested": False,
            },
            "04-action-mismatch-score-match": {
                "input_match": True,
                "action_match": False,
                "score_match": True,
                "execution_attested": False,
            },
            "05-input-mismatch-coincident": {
                "input_match": False,
                "action_match": None,
                "score_match": None,
                "execution_attested": False,
            },
            "06-exec-failed": {
                "input_match": True,
                "action_match": None,
                "score_match": None,
                "execution_attested": False,
            },
            "07-target-unobserved": {
                "input_match": True,
                "action_match": None,
                "score_match": None,
                "execution_attested": False,
            },
            "08-target-unscorable": {
                "input_match": True,
                "action_match": False,
                "score_match": None,
                "execution_attested": False,
            },
            "09-unbound-target": {
                "input_match": None,
                "action_match": None,
                "score_match": None,
                "execution_attested": False,
            },
            "10-missing-target": {
                "input_match": None,
                "action_match": None,
                "score_match": None,
                "execution_attested": False,
            },
            "12-exec-failed-input-mismatch": {
                "input_match": False,
                "action_match": None,
                "score_match": None,
                "execution_attested": False,
            },
        }
        for case_id, expected in expected_obs.items():
            with self.subTest(case=case_id):
                row = by_id[case_id]
                self.assertEqual(row["external_observation"], expected)
                self.assertIs(row["external_observation"]["execution_attested"], False)
                self.assertFalse(row["reproduced"])
                self.assertEqual(
                    row["status"], "missing" if case_id == "10-missing-target" else "opaque"
                )
        # Missing source has no prediction and therefore no external_observation field.
        self.assertNotIn("external_observation", by_id["11-missing-source"])
        self.assertEqual(by_id["11-missing-source"]["status"], "missing")

        # Existing v1 pair fields stay intact: pair-level action_match shows the
        # logged coincident match, while external_observation.action_match is None.
        self.assertFalse(by_id["05-input-mismatch-coincident"]["input_match"])
        self.assertTrue(by_id["05-input-mismatch-coincident"]["action_match"])
        self.assertIsNone(
            by_id["05-input-mismatch-coincident"]["external_observation"]["action_match"]
        )

    def test_undescribed_context_for_external_policy_yields_no_comparison(self) -> None:
        source = _decision("ctx", "source", 2, view=_view([1, 2], [2]))
        target = _decision("ctx", "target", None, view=_view([1], [2]))
        prediction = _external_result(source, action=None, transform=_SPEC)
        files = self._write_cohort([("ctx", source, target)], [prediction])

        # Without context spec, verification is "known_view": no external comparison.
        without_spec = calibrate(*files)
        self.assertEqual(
            without_spec["external_observations"],
            {
                "predictions": 1,
                "inputs_compared": 0,
                "input_matches": 0,
                "actions_compared": 0,
                "action_matches": 0,
                "scores_compared": 0,
                "score_matches": 0,
            },
        )
        row = without_spec["pairs"][0]
        self.assertEqual(row["status"], "opaque")
        self.assertEqual(row["prediction"]["input_verification"], "known_view")
        self.assertEqual(
            row["external_observation"],
            {
                "input_match": None,
                "action_match": None,
                "score_match": None,
                "execution_attested": False,
            },
        )
        self.assertFalse(row["reproduced"])

        # Supplying the context spec derives the input and unlocks external_observation,
        # while strict status remains opaque and reproduced remains 0.
        with_spec = calibrate(*files, context=_SPEC)
        self.assertEqual(with_spec["counts"]["reproduced"], 0)
        self.assertEqual(with_spec["counts"]["input_compared"], 0)
        self.assertEqual(with_spec["counts"]["prediction_opaque"], 1)
        self.assertEqual(
            with_spec["external_observations"],
            {
                "predictions": 1,
                "inputs_compared": 1,
                "input_matches": 1,
                "actions_compared": 1,
                "action_matches": 1,
                "scores_compared": 1,
                "score_matches": 1,
            },
        )
        row_with = with_spec["pairs"][0]
        self.assertEqual(row_with["status"], "opaque")
        self.assertEqual(row_with["prediction"]["input_verification"], "derived")
        self.assertEqual(
            row_with["external_observation"],
            {
                "input_match": True,
                "action_match": True,
                "score_match": True,
                "execution_attested": False,
            },
        )
        self.assertFalse(row_with["reproduced"])

        # Also check "unavailable" verification (undescribed context whose digest
        # matches neither source nor target view): still no external comparison.
        other_target = _decision("ctx", "target", None, view=_view([9], [9]))
        unavail_files = self._write_cohort([("ctx", source, other_target)], [prediction])
        unavail = calibrate(*unavail_files)
        self.assertEqual(
            unavail["pairs"][0]["prediction"]["input_verification"], "unavailable"
        )
        self.assertEqual(
            unavail["pairs"][0]["external_observation"],
            {
                "input_match": None,
                "action_match": None,
                "score_match": None,
                "execution_attested": False,
            },
        )
        self.assertEqual(unavail["external_observations"]["inputs_compared"], 0)

    def test_latest_transform_derives_input_for_external_policy(self) -> None:
        view = {
            "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
            "messages": [
                {"revision": 2, "available": ["A"]},
                {"revision": 1, "available": ["B"]},
            ],
            "pooled_available": None,
        }
        answers: list[wire.Answer] = [
            {"action": "A", "optimal": True, "feasible": True},
            {"action": "B", "optimal": False, "feasible": True},
        ]
        target_view = {**view, "messages": [{"revision": 2, "available": ["A"]}]}
        source = _decision("latest", "source", "B", view=view, answers=answers)
        target = _decision("latest", "target", "A", view=target_view, answers=answers)
        transformed_bytes = wire.canonical(target_view)
        prediction = wire.validate_result(
            {
                "schema": wire.RESULT_SCHEMA,
                "key": source["key"],
                "role": source["role"],
                "native_trial_id": source["native_trial_id"],
                "basis": "reconstructed",
                "source_decision_sha256": wire.decision_sha256(source),
                "source_view_sha256": source["view_sha256"],
                "input_sha256": wire.digest(target_view),
                "input_bytes": len(transformed_bytes),
                "policy": "external",
                "transform": "latest",
                "status": "measured",
                "action": "A",
                "score": wire.score(source, "A"),
                "error": None,
            }
        )
        files = self._write_cohort([("latest", source, target)], [prediction])
        report = calibrate(*files)
        self.assertEqual(report["counts"]["reproduced"], 0)
        self.assertEqual(
            report["external_observations"],
            {
                "predictions": 1,
                "inputs_compared": 1,
                "input_matches": 1,
                "actions_compared": 1,
                "action_matches": 1,
                "scores_compared": 1,
                "score_matches": 1,
            },
        )
        self.assertEqual(
            report["pairs"][0]["external_observation"],
            {
                "input_match": True,
                "action_match": True,
                "score_match": True,
                "execution_attested": False,
            },
        )

    def test_type_exact_action_equality_in_external_observation(self) -> None:
        answers: list[wire.Answer] = [
            {"action": 1, "optimal": True, "feasible": True},
            {"action": "1", "optimal": True, "feasible": True},
            {"action": None, "optimal": False, "feasible": False},
        ]
        source = _decision("types", "source", 1, answers=answers)
        target = _decision("types", "target", "1", answers=answers)
        prediction = _external_result(source, action=1)
        files = self._write_cohort([("types", source, target)], [prediction])
        report = calibrate(*files)
        self.assertEqual(
            report["external_observations"],
            {
                "predictions": 1,
                "inputs_compared": 1,
                "input_matches": 1,
                "actions_compared": 1,
                "action_matches": 0,
                "scores_compared": 1,
                "score_matches": 1,
            },
        )
        self.assertEqual(
            report["pairs"][0]["external_observation"],
            {
                "input_match": True,
                "action_match": False,
                "score_match": True,
                "execution_attested": False,
            },
        )

    def test_mixed_cohort_keeps_builtin_and_external_accounting_separate(self) -> None:
        root = Path(tempfile.mkdtemp(dir=self.root))
        s_builtin = _decision("01-builtin", "source", 2)
        t_builtin = _decision("01-builtin", "target", 1)
        s_ext = _decision("02-external", "source", 2)
        t_ext = _decision("02-external", "target", 1)

        src_one = root / "src_one.jsonl"
        pred_one = root / "pred_one.jsonl"
        wire.write_lines(src_one, [s_builtin])
        replay(src_one, pred_one, "intersection")
        builtin_pred = next(wire.read_results(pred_one))
        ext_pred = _external_result(s_ext, action=1)

        files = self._write_cohort(
            [("01-builtin", s_builtin, t_builtin), ("02-external", s_ext, t_ext)],
            [builtin_pred, ext_pred],
        )
        report = calibrate(*files)

        self.assertEqual(report["counts"]["pairs"], 2)
        self.assertEqual(report["counts"]["reproduced"], 1)
        self.assertEqual(report["counts"]["input_compared"], 1)
        self.assertEqual(report["counts"]["prediction_opaque"], 1)
        self.assertEqual(
            report["external_observations"],
            {
                "predictions": 1,
                "inputs_compared": 1,
                "input_matches": 1,
                "actions_compared": 1,
                "action_matches": 1,
                "scores_compared": 1,
                "score_matches": 1,
            },
        )
        by_id = {row["id"]: row for row in report["pairs"]}
        self.assertEqual(by_id["01-builtin"]["status"], "reproduced")
        self.assertTrue(by_id["01-builtin"]["reproduced"])
        self.assertNotIn("external_observation", by_id["01-builtin"])
        self.assertEqual(by_id["02-external"]["status"], "opaque")
        self.assertFalse(by_id["02-external"]["reproduced"])
        self.assertEqual(
            by_id["02-external"]["external_observation"],
            {
                "input_match": True,
                "action_match": True,
                "score_match": True,
                "execution_attested": False,
            },
        )

    def test_end_to_end_external_replay_and_calibration_never_reexecutes(self) -> None:
        root = Path(tempfile.mkdtemp(dir=self.root))
        policy_script = root / "policy.py"
        marker = root / "ran.log"
        policy_script.write_text(
            "import json, sys\n"
            f"with open({str(marker)!r}, 'a', encoding='utf-8') as f:\n"
            "    f.write('run\\n')\n"
            "view = json.load(sys.stdin)\n"
            "left = set(view['reports']['left'])\n"
            "right = set(view['reports']['right'])\n"
            "common = left & right\n"
            "print(json.dumps({'action': min(common) if common else None}))\n",
            encoding="utf-8",
        )
        s1 = _decision("case-1", "source", 2, view=_view([1, 2], [2]))
        t1 = _decision("case-1", "target", None, view=_view([1], [2]))
        s2 = _decision("case-2", "source", 2, view=_view([1, 2], [1, 2]))
        t2 = _decision("case-2", "target", 1, view=_view([1], [1]))

        tape_path = root / "decisions.jsonl"
        source_path = root / "sources.jsonl"
        pred_path = root / "predictions.jsonl"
        pairs_path = root / "pairs.json"
        pairs: list[_Pair] = [("case-1", s1, t1), ("case-2", s2, t2)]
        wire.write_lines(tape_path, [s1, t1, s2, t2])
        wire.write_lines(source_path, [s1, s2])
        pairs_path.write_bytes(wire.canonical(_manifest(pairs)))

        replay(
            source_path,
            pred_path,
            [sys.executable, str(policy_script)],
            transform=_SPEC,
        )
        runs_after_replay = len(marker.read_text(encoding="utf-8").splitlines())
        self.assertEqual(runs_after_replay, 2)

        with patch("subprocess.Popen", side_effect=AssertionError("must not spawn")):
            report = calibrate(tape_path, pred_path, pairs_path, context=_SPEC)

        self.assertEqual(
            len(marker.read_text(encoding="utf-8").splitlines()), runs_after_replay
        )
        self.assertEqual(report["counts"]["reproduced"], 0)
        self.assertEqual(report["counts"]["prediction_opaque"], 2)
        self.assertEqual(
            report["external_observations"],
            {
                "predictions": 2,
                "inputs_compared": 2,
                "input_matches": 2,
                "actions_compared": 2,
                "action_matches": 2,
                "scores_compared": 2,
                "score_matches": 2,
            },
        )
        decoded = json.loads(wire.canonical(report))
        self.assertEqual(decoded["external_observations"]["score_matches"], 2)
