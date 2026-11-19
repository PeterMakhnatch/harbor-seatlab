import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seatlab import tape as wire
from seatlab.context import ContextSpec
from seatlab.replay import replay
from seatlab.verification import verify_worker_result


def _decision(
    observed: wire.Observation | None, view: dict[str, object] | None = None
) -> wire.Decision:
    if view is None:
        view = {"reports": {"left": [1, 2], "right": [1, 2]}}
    return wire.decision(
        {
            "schema": wire.DECISION_SCHEMA,
            "key": "source",
            "role": "receiver",
            "native_trial_id": "dd19e118-5159-4831-bc8e-365d571e6b6b",
            "basis": "bound",
            "view": view,
            "view_sha256": wire.digest(view),
            "observed": observed,
            "answers": [
                {"action": None, "optimal": False, "feasible": False},
                {"action": 1, "optimal": True, "feasible": True},
                {"action": 2, "optimal": True, "feasible": True},
            ],
        }
    )


def _result(
    source: wire.Decision, policy: wire.Policy, transform: str | ContextSpec = "identity"
) -> wire.Result:
    with tempfile.TemporaryDirectory() as temp:
        tape = Path(temp) / "source.jsonl"
        ledger = Path(temp) / "replay.jsonl"
        wire.write_lines(tape, [source])
        replay(tape, ledger, policy, transform)
        (result,) = wire.read_results(ledger)
        return result


def _failure(result: wire.Result, error: wire.ReplayError) -> wire.Result:
    return wire.validate_result(
        {**result, "status": "unmeasured", "action": None, "score": None, "error": error}
    )


class WorkerVerificationTests(unittest.TestCase):
    def test_observed_null_is_distinct_from_missing_observation(self) -> None:
        accepted = _decision({"action": None})
        result = _result(accepted, "observed")
        self.assertEqual(result["status"], "measured")
        self.assertIsNone(result["action"])
        verify_worker_result(accepted, result, "observed", "identity", None)
        with self.assertRaisesRegex(wire.TapeError, "omits a deterministic measurement"):
            verify_worker_result(
                accepted,
                _failure(result, "missing_observation"),
                "observed",
                "identity",
                None,
            )

        missing = _decision(None)
        result = _result(missing, "observed")
        self.assertEqual(result["error"], "missing_observation")
        verify_worker_result(missing, result, "observed", "identity", None)
        forged = wire.validate_result(
            {
                **result,
                "status": "measured",
                "score": wire.score(missing, None),
                "error": None,
            }
        )
        with self.assertRaisesRegex(wire.TapeError, "status does not match"):
            verify_worker_result(missing, forged, "observed", "identity", None)

    def test_rejects_forged_action_score_and_omitted_measurement(self) -> None:
        source = _decision({"action": 1})
        for policy in ("observed", "intersection"):
            with self.subTest(policy=policy):
                result = _result(source, policy)
                verify_worker_result(source, result, policy, "identity", None)
                for forged, reason in (
                    (
                        wire.validate_result({**result, "action": 2}),
                        "action does not match deterministic policy",
                    ),
                    (
                        wire.validate_result({**result, "score": wire.score(source, None)}),
                        "score does not match",
                    ),
                    (_failure(result, "invalid_action"), "omits a deterministic measurement"),
                ):
                    with self.assertRaisesRegex(wire.TapeError, reason):
                        verify_worker_result(source, forged, policy, "identity", None)

    def test_accepts_real_unscorable_deterministic_action(self) -> None:
        source = _decision({"action": 99}, {"reports": {"left": [99], "right": [99]}})
        for policy in ("observed", "intersection"):
            with self.subTest(policy=policy):
                result = _result(source, policy)
                self.assertEqual(result["status"], "unmeasured")
                self.assertEqual(result["error"], "invalid_action")
                verify_worker_result(source, result, policy, "identity", None)
        observed = _result(source, "observed")
        with self.assertRaisesRegex(wire.TapeError, "status does not match"):
            verify_worker_result(
                source,
                _failure(observed, "missing_observation"),
                "observed",
                "identity",
                None,
            )

    def test_rejects_mismatched_source_input_and_registration(self) -> None:
        source = _decision({"action": 1})
        result = _result(source, "intersection")
        for fields in (
            {"key": "other"},
            {"role": "other"},
            {"basis": "reconstructed"},
            {"native_trial_id": "ee19e118-5159-4831-bc8e-365d571e6b6b"},
            {"source_decision_sha256": "0" * 64},
            {"source_view_sha256": "0" * 64, "input_sha256": "0" * 64},
        ):
            with (
                self.subTest(fields=fields),
                self.assertRaisesRegex(wire.TapeError, "replay source does not match"),
            ):
                verify_worker_result(
                    source,
                    wire.validate_result({**result, **fields}),
                    "intersection",
                    "identity",
                    None,
                )
        with self.assertRaisesRegex(wire.TapeError, "input byte count"):
            larger = wire.validate_result(
                {**result, "input_bytes": result["input_bytes"] + 1}
            )
            verify_worker_result(source, larger, "intersection", "identity", None)
        with self.assertRaisesRegex(wire.TapeError, "registered policy"):
            verify_worker_result(source, result, "observed", "identity", None)
        with self.assertRaisesRegex(wire.TapeError, "registered transform"):
            verify_worker_result(source, result, "intersection", "latest", None)

    def test_external_results_require_the_transformed_input_but_never_run(self) -> None:
        source = _decision(None)
        context = ContextSpec(
            {
                "schema": "seatlab.context/v1",
                "ops": [{"op": "take", "path": ["reports", "*"], "count": 1}],
            }
        )
        transform = context.identifier
        result = _result(source, "intersection", context)
        external = wire.validate_result({**result, "policy": "external", "action": 2})
        self.assertNotEqual(external["input_sha256"], source["view_sha256"])
        with patch("seatlab.replay.subprocess.Popen", side_effect=AssertionError("process")):
            verify_worker_result(source, external, "external", transform, context)
            for error in ("spawn_failed", "timeout", "invalid_output", "invalid_action"):
                with self.subTest(error=error):
                    verify_worker_result(
                        source, _failure(external, error), "external", transform, context
                    )
            with self.assertRaisesRegex(wire.TapeError, "offline answers"):
                wrong_score = wire.validate_result(
                    {**external, "score": wire.score(source, None)}
                )
                verify_worker_result(source, wrong_score, "external", transform, context)
            with self.assertRaisesRegex(wire.TapeError, "context spec is required"):
                verify_worker_result(source, external, "external", transform, None)
            with self.assertRaisesRegex(wire.TapeError, "context identifier"):
                wrong = ContextSpec({"schema": "seatlab.context/v1", "ops": []})
                verify_worker_result(source, external, "external", transform, wrong)
            with self.assertRaisesRegex(wire.TapeError, "input commitment"):
                wrong_input = wire.validate_result(
                    {**external, "input_sha256": source["view_sha256"]}
                )
                verify_worker_result(source, wrong_input, "external", transform, context)
