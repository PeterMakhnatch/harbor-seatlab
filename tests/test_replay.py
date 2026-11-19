import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seatlab import tape
from seatlab.replay import replay

_TRIAL = "dd19e118-5159-4831-bc8e-365d571e6b6b"


def _handoff_view() -> dict[str, object]:
    return {
        "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
        "messages": [
            {"revision": 2, "available": ["C", "D"]},
            {"revision": 1, "available": ["A", "B"]},
        ],
        "pooled_available": None,
    }


def _row(
    view: dict[str, object],
    *,
    key: str = "handoff:arrival:0:reordered:0",
    role: str = "dispatch",
    basis: tape.Basis = "reconstructed",
    observed: tape.Observation | None = None,
    answers: list[tape.Answer] | None = None,
) -> tape.Decision:
    return {
        "schema": tape.DECISION_SCHEMA,
        "key": key,
        "role": role,
        "native_trial_id": None if basis == "design" else _TRIAL,
        "basis": basis,
        "view": view,
        "view_sha256": tape.digest(view),
        "observed": observed,
        "answers": answers
        if answers is not None
        else [
            {"action": "A", "optimal": False, "feasible": False},
            {"action": "B", "optimal": False, "feasible": False},
            {"action": "C", "optimal": True, "feasible": True},
            {"action": "D", "optimal": False, "feasible": True},
        ],
    }


_POLICY = r"""
import json
import os
from pathlib import Path
import sys
import time

mode = sys.argv[1]
if mode == "duplex":
    sys.stdout.buffer.write(b" " * (48 * 1024))
    sys.stdout.flush()
payload = sys.stdin.buffer.read()
if mode == "contract":
    assert payload == b'{"signal":"caf\\u00e9","some":[1,true]}\n'
    assert os.environ["PATH"] == "/usr/bin:/bin"
    assert "SEATLAB_REPLAY_TEST_TOKEN" not in os.environ
    assert not list(Path.cwd().iterdir())
    assert Path.cwd() != Path(__file__).parent
    assert sys.argv[2] == "private-policy-argument"
    print("private-policy-stderr", file=sys.stderr)
    print('{"action":"B"}')
elif mode == "latest":
    view = json.loads(payload)
    assert view == {
        "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
        "messages": [{"revision": 2, "available": ["C", "D"]}],
        "pooled_available": None,
    }
    print('{"action":"C"}')
elif mode == "duplex":
    assert json.loads(payload) == {"signal": "z" * (256 * 1024)}
    print('{"action":"C"}')
elif mode == "timeout":
    print('{"action":"C"}', flush=True)
    time.sleep(30)
elif mode == "invalid-action":
    print('{"action":"private-unrecognized-action"}')
elif mode == "bool-action":
    print('{"action":true}')
elif mode == "invalid-output":
    print('{"action":"C"} private-policy-stdout')
elif mode == "output-limit":
    sys.stdout.buffer.write(b"x" * (128 * 1024))
elif mode == "nonzero":
    sys.exit(2)
"""


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source.jsonl"

    def test_arrival_and_revision_use_the_same_reversed_source(self) -> None:
        view = _handoff_view()
        tape.write_lines(self.source, [_row(view, basis="bound")])
        raw_path = self.root / "raw.jsonl"
        latest_path = self.root / "latest.jsonl"
        revision_path = self.root / "revision.jsonl"

        summary = replay(self.source, raw_path, "arrival")
        self.assertEqual(summary, {"decisions": 1, "measured": 1, "unmeasured": 0})
        replay(self.source, latest_path, "arrival", transform="latest")
        replay(self.source, revision_path, "revision")
        raw = next(tape.read_results(raw_path))
        latest = next(tape.read_results(latest_path))
        revision = next(tape.read_results(revision_path))

        self.assertEqual(raw["action"], "A")
        self.assertEqual(raw["score"], {"optimal": False, "feasible": False})
        self.assertEqual(latest["action"], "C")
        self.assertEqual(latest["score"], {"optimal": True, "feasible": True})
        self.assertEqual(revision["action"], "C")
        self.assertEqual(raw["input_sha256"], tape.digest(view))
        self.assertEqual(raw["input_bytes"], len(tape.canonical(view)))
        newest_only = {
            **view,
            "messages": [{"revision": 2, "available": ["C", "D"]}],
        }
        self.assertEqual(latest["input_sha256"], tape.digest(newest_only))
        self.assertEqual(latest["input_bytes"], len(tape.canonical(newest_only)))
        self.assertLess(latest["input_bytes"], raw["input_bytes"])
        self.assertEqual(latest["source_view_sha256"], raw["source_view_sha256"])
        self.assertEqual(raw["basis"], "bound")
        self.assertEqual(latest["basis"], "reconstructed")

    def test_silent_and_pooled_views_use_only_allowed_bays(self) -> None:
        silent = {**_handoff_view(), "messages": []}
        pooled = {**_handoff_view(), "pooled_available": ["D"]}
        tape.write_lines(
            self.source, [_row(silent, key="silent"), _row(pooled, key="pooled")]
        )
        for policy in ("arrival", "revision"):
            with self.subTest(policy=policy):
                out = self.root / f"{policy}.jsonl"
                replay(self.source, out, policy)
                self.assertEqual(
                    [row["action"] for row in tape.read_results(out)], ["A", "D"]
                )

    def test_missing_observation_is_not_a_measured_null(self) -> None:
        reports = {"reports": {"m1": [2], "m2": [3]}}
        answers: list[tape.Answer] = [{"action": None, "optimal": True, "feasible": True}]
        tape.write_lines(
            self.source,
            [
                _row(reports, key="missing", role="scheduler", answers=answers),
                _row(
                    reports,
                    key="null",
                    role="scheduler",
                    answers=answers,
                    observed={"action": None},
                ),
            ],
        )
        out = self.root / "observed.jsonl"
        summary = replay(self.source, out, "observed")
        self.assertEqual(summary, {"decisions": 2, "measured": 1, "unmeasured": 1})
        missing, known_null = tape.read_results(out)
        self.assertEqual(missing["key"], "missing")
        self.assertEqual(missing["status"], "unmeasured")
        self.assertEqual(missing["error"], "missing_observation")
        self.assertIsNone(missing["score"])
        self.assertIsNone(missing["action"])
        self.assertEqual(known_null["status"], "measured")
        self.assertEqual(known_null["score"], {"optimal": True, "feasible": True})
        self.assertIsNone(known_null["action"])
        self.assertIsNone(known_null["error"])

    def test_intersection_selects_first_common_slot_or_null(self) -> None:
        answers: list[tape.Answer] = [
            {"action": 2, "optimal": True, "feasible": True},
            {"action": None, "optimal": False, "feasible": True},
        ]
        tape.write_lines(
            self.source,
            [
                _row(
                    {"reports": {"m1": [2, 4, 7], "m2": [1, 2, 4]}},
                    key="common",
                    role="scheduler",
                    basis="design",
                    answers=answers,
                ),
                _row(
                    {"reports": {"m1": [2], "m2": [4]}},
                    key="none",
                    role="scheduler",
                    basis="design",
                    answers=answers,
                ),
            ],
        )
        out = self.root / "intersection.jsonl"
        replay(self.source, out, "intersection")
        self.assertEqual(
            [(row["status"], row["action"]) for row in tape.read_results(out)],
            [("measured", 2), ("measured", None)],
        )

    def test_intersection_replays_a_native_calendar_proposal_envelope(self) -> None:
        reports = {
            "m1": [0, 10, 12],
            "m2": [1, 10, 12],
            "m3": [2, 10, 12],
            "m4": [3, 10, 12],
            "m5": [0, 1, 2, 3, 4, 5, 6, 7, 10, 12],
        }
        answers: list[tape.Answer] = [
            {"action": 10, "optimal": True, "feasible": True},
            {"action": 12, "optimal": False, "feasible": True},
            {"action": None, "optimal": False, "feasible": False},
        ]
        tape.write_lines(
            self.source,
            [
                _row(
                    {"role": "m1", "mode": "full", "reports": reports},
                    key="calendar:full",
                    role="m1",
                    observed={"action": 10},
                    answers=answers,
                ),
                _row(
                    {
                        "role": "m1",
                        "mode": "first8",
                        "reports": {seat: ticks[:8] for seat, ticks in reports.items()},
                    },
                    key="calendar:first8",
                    role="m1",
                    observed={"action": None},
                    answers=answers,
                ),
            ],
        )
        out = self.root / "calendar.jsonl"
        summary = replay(self.source, out, "intersection")
        self.assertEqual(summary, {"decisions": 2, "measured": 2, "unmeasured": 0})
        self.assertEqual(
            [(row["status"], row["action"]) for row in tape.read_results(out)],
            [("measured", 10), ("measured", None)],
        )

    def test_intersection_rejects_invalid_calendar_proposal_envelopes(self) -> None:
        reports = {f"m{index}": [10] for index in range(1, 6)}
        proposal = {"role": "m1", "mode": "full", "reports": reports}
        missing_report = {seat: slots for seat, slots in reports.items() if seat != "m5"}
        invalid_views = [
            {**proposal, "role": "m2"},
            {**proposal, "mode": "other"},
            {**proposal, "extra": None},
            {**proposal, "reports": missing_report},
            {**proposal, "reports": {**reports, "m6": [10]}},
            {**proposal, "reports": {**reports, "m1": [10, 10]}},
            {**proposal, "reports": {**reports, "m1": [-1, 10]}},
        ]
        for index, view in enumerate(invalid_views):
            with self.subTest(view=view):
                source = self.root / f"view-{index}.jsonl"
                out = self.root / f"result-{index}.jsonl"
                tape.write_lines(source, [_row(view, role="m1")])
                with self.assertRaises(ValueError):
                    replay(source, out, "intersection")
                self.assertFalse(out.exists())

    def test_invalid_tape_does_not_publish_partial_results(self) -> None:
        valid = _row(_handoff_view())
        invalid = {**valid, "key": "invalid", "view_sha256": "0" * 64}
        tape.write_lines(self.source, [valid, invalid])
        out = self.root / "unpublished.jsonl"
        with self.assertRaises(ValueError):
            replay(self.source, out, "arrival")
        self.assertFalse(out.exists())

    def test_latest_rejects_ambiguous_or_non_handoff_view(self) -> None:
        tied = {
            **_handoff_view(),
            "messages": [
                {"revision": 2, "available": ["C", "D"]},
                {"revision": 2, "available": ["A", "B"]},
            ],
        }
        bad_revision = {
            **_handoff_view(),
            "messages": [{"revision": True, "available": ["A", "B"]}],
        }
        for index, invalid in enumerate([tied, bad_revision, {"reports": {}}]):
            with self.subTest(view=invalid):
                source = self.root / f"invalid-{index}.jsonl"
                out = self.root / f"unpublished-{index}.jsonl"
                tape.write_lines(
                    source, [_row(_handoff_view()), _row(invalid, key="invalid")]
                )
                with self.assertRaises(ValueError):
                    replay(source, out, "arrival", transform="latest")
                self.assertFalse(out.exists())

    def test_observed_refuses_transformed_context(self) -> None:
        tape.write_lines(self.source, [_row(_handoff_view(), observed={"action": "C"})])
        out = self.root / "unpublished.jsonl"
        with self.assertRaisesRegex(ValueError, "observed requires the identity transform"):
            replay(self.source, out, "observed", transform="latest")
        self.assertFalse(out.exists())

    def _command(self, *args: str) -> list[str]:
        script = self.root / "private user policy.py"
        script.write_text(_POLICY, encoding="utf-8")
        return [str(Path(sys.executable).resolve()), "-I", str(script), *args]

    @unittest.skipUnless(os.name == "posix", "external replay requires POSIX")
    def test_external_policy_receives_only_canonical_view(self) -> None:
        view = {"some": [1, True], "signal": "café"}
        tape.write_lines(self.source, [_row(view, observed={"action": "A"})])
        out = self.root / "external.jsonl"
        command = self._command("contract", "private-policy-argument")
        with patch.dict(os.environ, {"SEATLAB_REPLAY_TEST_TOKEN": "private-policy-env"}):
            replay(self.source, out, command)
        result = next(tape.read_results(out))
        self.assertEqual(result["policy"], "external")
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["action"], "B")
        self.assertEqual(result["input_sha256"], tape.digest(view))
        self.assertEqual(result["input_bytes"], len(tape.canonical(view)))
        ledger = out.read_text(encoding="utf-8")
        for secret in (command[0], command[2], "private-policy-"):
            self.assertNotIn(secret, ledger)

    @unittest.skipUnless(os.name == "posix", "external replay requires POSIX")
    def test_external_policy_receives_transformed_view(self) -> None:
        tape.write_lines(self.source, [_row(_handoff_view())])
        out = self.root / "latest.jsonl"
        replay(self.source, out, self._command("latest"), transform="latest")
        result = next(tape.read_results(out))
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["action"], "C")
        self.assertNotEqual(result["input_sha256"], result["source_view_sha256"])

    @unittest.skipUnless(os.name == "posix", "external replay requires POSIX")
    def test_external_input_commitment_excludes_reference_data(self) -> None:
        view = _handoff_view()
        command = self._command("latest")
        results: list[tape.Result] = []
        for optimal in (False, True):
            decision = _row(
                view,
                answers=[{"action": "C", "optimal": optimal, "feasible": True}],
            )
            source = self.root / f"decision-{optimal}.jsonl"
            out = self.root / f"result-{optimal}.jsonl"
            tape.write_lines(source, [decision])
            replay(source, out, command, transform="latest")
            result = next(tape.read_results(out))
            self.assertEqual(result["status"], "measured")
            self.assertEqual(result["action"], "C")
            self.assertEqual(result["score"], {"optimal": optimal, "feasible": True})
            self.assertEqual(result["source_decision_sha256"], tape.digest(decision))
            results.append(result)
        original, corrected = results
        self.assertEqual(original["input_sha256"], corrected["input_sha256"])
        self.assertNotEqual(
            original["source_decision_sha256"], corrected["source_decision_sha256"]
        )

    @unittest.skipUnless(os.name == "posix", "external replay requires POSIX")
    def test_external_io_drains_stdout_while_sending_the_view(self) -> None:
        view = {"signal": "z" * (256 * 1024)}
        tape.write_lines(self.source, [_row(view)])
        out = self.root / "duplex.jsonl"
        replay(self.source, out, self._command("duplex"))
        result = next(tape.read_results(out))
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["action"], "C")
        self.assertEqual(result["input_bytes"], len(tape.canonical(view)))

    @unittest.skipUnless(os.name == "posix", "external replay requires POSIX")
    def test_external_failures_are_unmeasured_without_leaking_data(self) -> None:
        tape.write_lines(self.source, [_row(_handoff_view())])
        cases = {
            "timeout": "timeout",
            "invalid-action": "invalid_action",
            "bool-action": "invalid_action",
            "invalid-output": "invalid_output",
            "output-limit": "output_limit",
            "nonzero": "policy_failed",
            "missing-program": "spawn_failed",
        }
        for mode, reason in cases.items():
            with self.subTest(mode=mode):
                out = self.root / f"{mode}.jsonl"
                command = (
                    [str(self.root / "private-policy-missing")]
                    if mode == "missing-program"
                    else self._command(mode)
                )
                timeout = 0.5 if mode == "timeout" else 10.0
                summary = replay(self.source, out, command, timeout=timeout)
                result = next(tape.read_results(out))
                self.assertEqual(summary, {"decisions": 1, "measured": 0, "unmeasured": 1})
                self.assertEqual(result["status"], "unmeasured")
                self.assertEqual(result["error"], reason)
                self.assertIsNone(result["score"])
                self.assertIsNone(result["action"])
                self.assertNotIn("private-", out.read_text(encoding="utf-8"))
