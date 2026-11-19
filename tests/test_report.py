import tempfile
import unittest
from pathlib import Path
from typing import cast

from seatlab.replay import replay
from seatlab.report import compare, describe
from seatlab.tape import canonical, digest, read_results

TRIAL_ONE = "00000000-0000-4000-8000-000000000001"
TRIAL_TWO = "00000000-0000-4000-8000-000000000002"


def _write(path: Path, *rows: dict[str, object]) -> None:
    path.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))


def _result(
    key: str,
    *,
    role: str = "dispatch",
    trial: str | None = TRIAL_ONE,
    basis: str = "reconstructed",
    action: int | str | None = 2,
    optimal: bool = True,
    feasible: bool = True,
    error: str | None = None,
    source: dict[str, object] | None = None,
    transformed: dict[str, object] | None = None,
) -> dict[str, object]:
    original = {"messages": []} if source is None else source
    view = original if transformed is None else transformed
    source_decision = _decision(key, role, trial, basis, original, None, best=2)
    result_basis = "reconstructed" if transformed is not None and basis == "bound" else basis
    return {
        "schema": "seatlab.decision-result/v1",
        "key": key,
        "role": role,
        "native_trial_id": trial,
        "basis": result_basis,
        "source_view_sha256": digest(original),
        "source_decision_sha256": digest(source_decision),
        "input_sha256": digest(view),
        "input_bytes": len(canonical(view)),
        "policy": "external",
        "transform": "identity" if transformed is None else "latest",
        "status": "measured" if error is None else "unmeasured",
        "action": action if error is None else None,
        "score": {"optimal": optimal, "feasible": feasible} if error is None else None,
        "error": error,
    }


def _decision(
    key: str,
    role: str,
    trial: str | None,
    basis: str,
    view: dict[str, object],
    observed: dict[str, object] | None,
    *,
    best: str | int = "bay-a",
) -> dict[str, object]:
    return {
        "schema": "seatlab.decision/v1",
        "key": key,
        "role": role,
        "native_trial_id": trial,
        "basis": basis,
        "view": view,
        "view_sha256": digest(view),
        "observed": observed,
        "answers": [
            {"action": best, "optimal": True, "feasible": True},
            {"action": None, "optimal": False, "feasible": True},
        ],
    }


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.baseline = root / "baseline.jsonl"
        self.candidate = root / "candidate.jsonl"
        self.tape = root / "tape.jsonl"

    def test_paired_changes_count_only_jointly_measured_scores(self) -> None:
        calendar = _result(
            "meeting",
            role="scheduler",
            trial=TRIAL_TWO,
            action=None,
            optimal=False,
            source={"reports": {"m1": [3], "m2": [7]}},
        )
        design = _result("design", trial=None, basis="design", feasible=False)
        _write(
            self.baseline,
            _result("ship-1", action=1, optimal=False, feasible=False),
            _result("ship-2"),
            _result("ship-3", error="timeout"),
            _result("ship-4"),
            calendar,
            design,
        )
        _write(
            self.candidate,
            design,
            calendar,
            _result("ship-4", error="invalid_action"),
            _result("ship-3", action=None),
            _result("ship-2", action=3, optimal=False, feasible=False),
            _result("ship-1", action="1"),
        )

        report = compare(self.baseline, self.candidate)
        expected = {
            "schema": "seatlab.paired-replay/v1",
            "paired": 6,
            "measured_pairs": 4,
            "incomplete_pairs": 2,
            "changed_actions": 2,
            "changed_inputs": 0,
            "native_trials": 2,
            "design_pairs": 1,
            "both_optimal": 1,
            "gained_optimal": 1,
            "lost_optimal": 1,
            "neither_optimal": 1,
            "both_feasible": 1,
            "gained_feasible": 1,
            "lost_feasible": 1,
            "neither_feasible": 1,
            "baseline_unmeasured": 1,
            "candidate_unmeasured": 1,
            "baseline_completed_null": 1,
            "candidate_completed_null": 2,
            "baseline_errors": {"timeout": 1},
            "candidate_errors": {"invalid_action": 1},
        }
        self.assertEqual({name: report[name] for name in expected}, expected)
        roles = cast(dict[str, dict[str, object]], report["by_role"])
        self.assertEqual(roles["dispatch"]["paired"], 5)
        self.assertEqual(roles["dispatch"]["measured_pairs"], 3)
        self.assertEqual(roles["dispatch"]["native_trials"], 1)
        self.assertEqual(roles["scheduler"]["measured_pairs"], 1)
        trials = cast(dict[str, dict[str, object]], report["by_native_trial"])
        self.assertEqual(set(trials), {TRIAL_ONE, TRIAL_TWO})
        self.assertEqual(trials[TRIAL_ONE]["paired"], 4)
        self.assertEqual(trials[TRIAL_ONE]["measured_pairs"], 2)
        self.assertEqual(trials[TRIAL_TWO]["paired"], 1)

    def test_changed_input_without_changed_score(self) -> None:
        older = {"revision": 1, "available": ["A", "B"]}
        latest = {"revision": 2, "available": ["A"]}
        original: dict[str, object] = {"messages": [older, latest]}
        reduced: dict[str, object] = {"messages": [latest]}
        _write(self.baseline, _result("ship", source=original, basis="bound"))
        _write(
            self.candidate,
            _result("ship", source=original, basis="bound", transformed=reduced),
        )

        report = compare(self.baseline, self.candidate)
        self.assertEqual(report["changed_inputs"], 1)
        self.assertEqual(report["changed_actions"], 0)
        self.assertEqual(report["both_optimal"], 1)
        self.assertEqual(report["gained_optimal"], 0)
        self.assertEqual(report["lost_optimal"], 0)
        old_size, new_size = len(canonical(original)), len(canonical(reduced))
        self.assertEqual(report["baseline_input_bytes"], old_size)
        self.assertEqual(report["candidate_input_bytes"], new_size)
        self.assertEqual(report["input_bytes_saved"], old_size - new_size)
        reverse = compare(self.candidate, self.baseline)
        self.assertEqual(reverse["input_bytes_saved"], new_size - old_size)

    def test_all_unmeasured_pairs_keep_zero_score_denominators(self) -> None:
        _write(self.baseline, _result("ship", error="timeout"))
        _write(self.candidate, _result("ship", error="invalid_action"))
        report = compare(self.baseline, self.candidate)
        self.assertEqual(report["paired"], 1)
        self.assertEqual(report["incomplete_pairs"], 1)
        self.assertEqual(report["measured_pairs"], 0)
        self.assertEqual(report["changed_actions"], 0)
        self.assertEqual(report["baseline_completed_null"], 0)
        self.assertEqual(report["candidate_completed_null"], 0)
        self.assertEqual(report["neither_optimal"], 0)
        roles = cast(dict[str, dict[str, object]], report["by_role"])
        self.assertEqual(roles["dispatch"]["measured_pairs"], 0)
        trials = cast(dict[str, dict[str, object]], report["by_native_trial"])
        self.assertEqual(trials[TRIAL_ONE]["measured_pairs"], 0)

    def test_key_set_must_match_in_both_directions(self) -> None:
        common = _result("shared")
        _write(self.baseline, common, _result("baseline-only"), _result("baseline-extra"))
        _write(self.candidate, common, _result("candidate-only"))
        with self.assertRaisesRegex(
            ValueError, "1 missing from baseline, 2 missing from candidate"
        ):
            compare(self.baseline, self.candidate)

    def test_duplicate_key_on_either_side_is_invalid(self) -> None:
        row = _result("ship")
        for duplicated in (self.baseline, self.candidate):
            with self.subTest(duplicated=duplicated.name):
                _write(self.baseline, row)
                _write(self.candidate, row)
                _write(duplicated, row, row)
                with self.assertRaisesRegex(ValueError, "duplicate record key"):
                    compare(self.baseline, self.candidate)

    def test_same_key_does_not_override_source_provenance(self) -> None:
        _write(self.baseline, _result("ship"))
        cases = {
            "role": _result("ship", role="scheduler"),
            "native_trial_id": _result("ship", trial=TRIAL_TWO),
            "source_view_sha256": _result("ship", source={"messages": ["edited"]}),
        }
        for field, row in cases.items():
            with self.subTest(field=field):
                _write(self.candidate, row)
                with self.assertRaisesRegex(ValueError, field):
                    compare(self.baseline, self.candidate)

    def test_changed_answer_data_cannot_create_a_gain(self) -> None:
        view: dict[str, object] = {"reports": {"m1": [5], "m2": [11]}}
        row = _decision(
            "meeting", "scheduler", TRIAL_ONE, "reconstructed", view, {"action": None}, best=5
        )
        _write(self.tape, row)
        replay(self.tape, self.baseline, "observed")

        # The tape remains valid and the actor sees the same input. Only gold changes.
        answers = cast(list[dict[str, object]], row["answers"])
        answers[1]["optimal"] = True
        _write(self.tape, row)
        replay(self.tape, self.candidate, "observed")

        (baseline,) = read_results(self.baseline)
        (candidate,) = read_results(self.candidate)
        self.assertEqual(baseline["score"], {"optimal": False, "feasible": True})
        self.assertEqual(candidate["score"], {"optimal": True, "feasible": True})

        with self.assertRaisesRegex(ValueError, "source_decision_sha256"):
            compare(self.baseline, self.candidate)

    def test_valid_shape_required_even_for_unpaired_rows(self) -> None:
        _write(self.baseline, _result("ship"))
        row = _result("extra")
        row["score"] = {"optimal": 1, "feasible": True}
        _write(self.candidate, _result("ship"), row)
        with self.assertRaisesRegex(ValueError, "optimal and feasible must be booleans"):
            compare(self.baseline, self.candidate)

    def test_equal_input_commitments_cannot_report_different_byte_lengths(self) -> None:
        row = _result("ship")
        _write(self.baseline, row)
        row["input_bytes"] = 9000
        _write(self.candidate, row)
        with self.assertRaisesRegex(ValueError, "input_bytes"):
            compare(self.baseline, self.candidate)

    def test_describe_uses_validated_decisions_across_domains(self) -> None:
        dispatch: dict[str, object] = {"utilities": {"bay-a": 3}, "messages": []}
        calendar: dict[str, object] = {"reports": {"m1": [5], "m2": [11]}}
        design: dict[str, object] = {"locally-authored-domain": "role input"}
        _write(
            self.tape,
            _decision("ship-1", "dispatch", TRIAL_ONE, "reconstructed", dispatch, None),
            _decision(
                "ship-2",
                "dispatch",
                TRIAL_ONE,
                "reconstructed",
                dispatch,
                {"action": "bay-a"},
            ),
            _decision(
                "meeting", "scheduler", TRIAL_TWO, "bound", calendar, {"action": None}, best=5
            ),
            _decision("design", "planner", None, "design", design, None, best="north"),
        )
        self.assertEqual(
            describe(self.tape),
            {
                "schema": "seatlab.tape-description/v1",
                "decisions": 4,
                "by_basis": {"bound": 1, "design": 1, "reconstructed": 2},
                "by_role": {"dispatch": 2, "planner": 1, "scheduler": 1},
                "native_trials": 2,
                "observed": 2,
                "observed_completed_null": 1,
                "unobserved": 2,
                "distinct_view_lengths": sorted(
                    {len(canonical(view)) for view in (dispatch, calendar, design)}
                ),
            },
        )

    def test_describe_rejects_tampered_view(self) -> None:
        row = _decision("ship", "dispatch", TRIAL_ONE, "reconstructed", {}, None)
        row["view"] = {"unauthorized": "change"}
        _write(self.tape, row)
        with self.assertRaisesRegex(ValueError, "view_sha256"):
            describe(self.tape)
