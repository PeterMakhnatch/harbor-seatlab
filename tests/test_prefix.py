import os
import sys
import tempfile
import unittest
from pathlib import Path

from seatlab import tape
from seatlab.prefix import prefix

_TRIAL = "dd19e118-5159-4831-bc8e-365d571e6b6b"

_POLICY = """
import json
from pathlib import Path
import sys
import time

payload = sys.stdin.buffer.read()
with Path(sys.argv[1]).open("ab") as log:
    log.write(payload)
mode = sys.argv[2]
if mode == "history":
    turn = json.loads(payload)["view"]["turn"]
    print(json.dumps({"action": {1: "query:1", 2: None, 3: "choose:99"}[turn]}))
elif mode == "timeout":
    time.sleep(30)
elif mode == "overwrite":
    Path(sys.argv[3]).write_bytes(b"not JSON")
    print('{"action":null}')
elif mode == "bad-output":
    print("private-stdout")
    print("private-stderr", file=sys.stderr)
elif mode == "output-limit":
    sys.stdout.buffer.write(b"X" * (128 * 1024))
else:
    print('{"action":' + mode + '}')
"""


def _frame(
    turn: int,
    view: dict[str, object],
    observed: tape.Observation | None,
    *,
    basis: str = "bound",
) -> dict:
    return {
        "turn": turn,
        "basis": basis,
        "view": view,
        "view_sha256": tape.digest(view),
        "observed": observed,
    }


def _episode(id: str, role: str, frames: list[dict], *, closed: bool = True) -> dict:
    payload = {
        "schema": "seatlab.episode/v1",
        "id": id,
        "case_sha256": tape.digest({"synthetic": True}),
        "role": role,
        "native_trial_id": _TRIAL,
        "closed": closed,
        "frames": frames,
    }
    return {**payload, "episode_sha256": tape.digest(payload)}


def _null_episode(id: str, role: str, *, closed: bool = True) -> dict:
    return _episode(
        id, role, [_frame(1, {"private-view": role}, {"action": None})], closed=closed
    )


class PrefixTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.source = self.root / "episodes.jsonl"
        self.log = self.root / "private-invocations.jsonl"
        self.script = self.root / "private-policy.py"
        self.script.write_text(_POLICY, encoding="utf-8")

    def _write(self, *rows: dict) -> None:
        self.source.write_bytes(b"".join(tape.canonical(row) + b"\n" for row in rows))

    def _command(self, mode: str, *args: str) -> list[str]:
        return [os.path.abspath(sys.executable), str(self.script), str(self.log), mode, *args]

    def _assert_payloads(self, *payloads: dict) -> None:
        self.assertEqual(
            self.log.read_bytes(),
            b"".join(tape.canonical(payload) + b"\n" for payload in payloads),
        )

    @unittest.skipUnless(os.name == "posix", "external prefix requires POSIX")
    def test_divergence_withholds_suffix_and_history_is_canonical_and_role_local(
        self,
    ) -> None:
        views = [
            {"turn": 1, "arbitrary": {"z": "café", "a": [1, True]}},
            {"turn": 2, "context": "private-second-view"},
            {"turn": 3, "context": "private-boundary-view"},
            {"turn": 4, "context": "private-suffix-sentinel"},
        ]
        other_view = {"turn": 1, "context": "private-other-role"}
        first = _episode(
            "first",
            "Planner",
            [
                _frame(1, views[0], {"action": "query:1"}),
                _frame(2, views[1], {"action": None}),
                _frame(3, views[2], {"action": "choose:5"}),
                _frame(4, views[3], {"action": "private-suffix-action"}),
            ],
        )
        second = _episode("second", "Partner", [_frame(1, other_view, {"action": "query:1"})])
        self._write(first, second)

        report = prefix(self.source, self._command("history"), history=True)

        one = {"view": views[0], "action": "query:1"}
        two = {"view": views[1], "action": None}
        self._assert_payloads(
            {"view": views[0], "history": []},
            {"view": views[1], "history": [one]},
            {"view": views[2], "history": [one, two]},
            {"view": other_view, "history": []},
        )
        self.assertNotIn(b"private-suffix", self.log.read_bytes())
        self.assertEqual(
            set(report),
            {"schema", "policy", "interpretation", "history", "counts", "episodes"},
        )
        self.assertEqual(report["schema"], "seatlab.prefix-result/v1")
        self.assertEqual(report["policy"], "external")
        self.assertEqual(report["interpretation"], "recorded_prefix_agreement")
        self.assertIs(report["history"], True)
        self.assertEqual(
            report["counts"],
            {
                "episodes": 2,
                "frames": 5,
                "matched_prefix": 3,
                "calls": 4,
                "withheld_suffix": 1,
                "matched": 1,
                "observed_prefix": 0,
                "diverged": 1,
                "missing_observation": 0,
                "unbound": 0,
                "policy_failed": 0,
                "matched_open": 0,
                "matched_closed": 1,
            },
        )
        self.assertEqual(
            report["episodes"][0],
            {
                "id": first["id"],
                "case_sha256": first["case_sha256"],
                "role": first["role"],
                "native_trial_id": first["native_trial_id"],
                "episode_sha256": first["episode_sha256"],
                "closed": True,
                "frames": 4,
                "matched_prefix": 2,
                "boundary_turn": 3,
                "status": "diverged",
                "source_action": {"action": "choose:5"},
                "candidate_action": {"action": "choose:99"},
                "error": None,
                "calls": 3,
                "withheld_suffix": 1,
            },
        )
        self.assertEqual([row["id"] for row in report["episodes"]], ["first", "second"])
        self.assertNotIn(b"private-", tape.canonical(report))
        self.assertNotIn(b'"view"', tape.canonical(report))

    @unittest.skipUnless(os.name == "posix", "external prefix requires POSIX")
    def test_default_transport_is_the_raw_view(self) -> None:
        first_view = {"history": ["inside the original view"], "z": "é", "a": {"action": 3}}
        first = _episode(
            "open", "Planner", [_frame(1, first_view, {"action": None})], closed=False
        )
        second = _null_episode("closed", "Partner")
        self._write(first, second)

        report = prefix(self.source, self._command("null"))

        self._assert_payloads(first_view, second["frames"][0]["view"])
        self.assertIs(report["history"], False)
        self.assertEqual(report["counts"]["matched_open"], 1)
        self.assertEqual(report["counts"]["matched_closed"], 1)
        for row in report["episodes"]:
            self.assertEqual(row["status"], "matched")
            self.assertEqual(row["calls"], 1)
            self.assertEqual(row["withheld_suffix"], 0)
            for key in ("boundary_turn", "source_action", "candidate_action", "error"):
                self.assertIsNone(row[key])

    @unittest.skipUnless(os.name == "posix", "external prefix requires POSIX")
    def test_boundaries_precede_policy_and_distinguish_missing_from_null(self) -> None:
        null = {"action": None}
        accepted = {"signal": "accepted explicit null"}
        suffix = {"signal": "private-suffix-sentinel"}
        unbound = {"signal": "private-reconstructed"}
        missing = {"signal": "private-missing-observation"}
        self._write(
            _episode(
                "a",
                "A",
                [_frame(1, unbound, None, basis="reconstructed"), _frame(2, suffix, null)],
            ),
            _episode(
                "b",
                "B",
                [_frame(1, unbound, null, basis="reconstructed"), _frame(2, suffix, null)],
            ),
            _episode(
                "c", "C", [_frame(1, missing, None), _frame(2, suffix, null)], closed=False
            ),
            _episode(
                "d",
                "D",
                [
                    _frame(1, accepted, null),
                    _frame(2, missing, None),
                    _frame(3, suffix, null),
                ],
            ),
        )

        for policy in ("observed", self._command("null")):
            with self.subTest(policy=policy):
                report = prefix(self.source, policy, history=True)
                rows = report["episodes"]
                self.assertEqual(
                    [row["status"] for row in rows],
                    ["unbound", "unbound", "missing_observation", "missing_observation"],
                )
                self.assertEqual([row["matched_prefix"] for row in rows], [0, 0, 0, 1])
                self.assertEqual([row["boundary_turn"] for row in rows], [1, 1, 1, 2])
                self.assertEqual(
                    [row["source_action"] for row in rows], [None, null, None, None]
                )
                self.assertEqual([row["candidate_action"] for row in rows], [None] * 4)
                self.assertEqual([row["withheld_suffix"] for row in rows], [1] * 4)
                counts = report["counts"]
                self.assertEqual(counts["unbound"], 2)
                self.assertEqual(counts["missing_observation"], 2)
                self.assertEqual(counts["withheld_suffix"], 4)
                self.assertEqual(counts["matched_open"], 0)
                self.assertEqual(counts["matched_closed"], 0)
                self.assertEqual(counts["calls"], 0 if policy == "observed" else 1)
                self.assertNotIn(b"private-", tape.canonical(report))
                if policy == "observed":
                    self.assertFalse(self.log.exists())
        self._assert_payloads({"view": accepted, "history": []})

    @unittest.skipUnless(os.name == "posix", "external prefix requires POSIX")
    def test_invalid_later_input_never_launches_first_policy(self) -> None:
        first = _null_episode("first", "Planner")
        second = _null_episode("second", "Partner")
        first_line = tape.canonical(first) + b"\n"
        invalid = (
            b"{",
            tape.canonical({**second, "episode_sha256": "a" * 64}),
            tape.canonical(_null_episode("first", "Partner")),
            tape.canonical(_null_episode("second", "Planner")),
        )
        for line in invalid:
            with self.subTest(line=line):
                self.source.write_bytes(first_line + line + b"\n")
                with self.assertRaisesRegex(tape.TapeError, r":2:"):
                    prefix(self.source, self._command("null"))
                self.assertFalse(self.log.exists())

    @unittest.skipUnless(os.name == "posix", "external prefix requires POSIX")
    def test_execution_uses_retained_verified_rows(self) -> None:
        first = _null_episode("first", "Planner")
        second = _null_episode("second", "Partner")
        self._write(first, second)

        report = prefix(self.source, self._command("overwrite", str(self.source)))

        self.assertEqual(self.source.read_bytes(), b"not JSON")
        self.assertEqual(report["counts"]["matched"], 2)
        self._assert_payloads(first["frames"][0]["view"], second["frames"][0]["view"])

    @unittest.skipUnless(os.name == "posix", "external prefix requires POSIX")
    def test_external_action_type_and_null_envelopes(self) -> None:
        cases = (
            (1, "true", "policy_failed", "invalid_action", None),
            (0, "false", "policy_failed", "invalid_action", None),
            (1, '"1"', "diverged", None, {"action": "1"}),
            ("1", "1", "diverged", None, {"action": 1}),
            (None, "1", "diverged", None, {"action": 1}),
            (1, "null", "diverged", None, {"action": None}),
        )
        for source, mode, status, error, candidate in cases:
            with self.subTest(source=source, mode=mode):
                self._write(_episode("one", "Planner", [_frame(1, {}, {"action": source})]))
                report = prefix(self.source, self._command(mode))
                row = report["episodes"][0]
                self.assertEqual(row["status"], status)
                self.assertEqual(row["error"], error)
                self.assertEqual(row["source_action"], {"action": source})
                self.assertEqual(row["candidate_action"], candidate)
                self.assertEqual(row["calls"], 1)

    @unittest.skipUnless(os.name == "posix", "external prefix requires POSIX")
    def test_failures_count_only_successful_spawns_and_withhold_suffix(self) -> None:
        view = {"signal": "private-first-view"}
        self._write(
            _episode(
                "one",
                "Planner",
                [
                    _frame(1, view, {"action": None}),
                    _frame(2, {"private-suffix": True}, {"action": None}),
                ],
            )
        )
        cases = (
            ([str(self.root / "private-missing-executable")], "spawn_failed", 0, 10.0),
            (self._command("timeout"), "timeout", 1, 0.5),
            (self._command("bad-output"), "invalid_output", 1, 10.0),
            (self._command("output-limit"), "output_limit", 1, 10.0),
        )
        for command, reason, calls, timeout in cases:
            with self.subTest(reason=reason):
                report = prefix(self.source, command, timeout=timeout)
                row = report["episodes"][0]
                self.assertEqual(row["status"], "policy_failed")
                self.assertEqual(row["error"], reason)
                self.assertEqual(row["calls"], calls)
                self.assertEqual(row["matched_prefix"], 0)
                self.assertEqual(row["boundary_turn"], 1)
                self.assertEqual(row["source_action"], {"action": None})
                self.assertIsNone(row["candidate_action"])
                self.assertEqual(row["withheld_suffix"], 1)
                self.assertEqual(report["counts"]["calls"], calls)
                self.assertNotIn(b"private-", tape.canonical(report))
        self.assertNotIn(b"private-suffix", self.log.read_bytes())

    def test_observed_is_self_consistency_with_no_calls_and_counts_open_and_closed(
        self,
    ) -> None:
        self._write(
            _null_episode("first", "Planner", closed=False),
            _null_episode("second", "Partner"),
        )

        report = prefix(self.source, "observed", history=True)

        self.assertEqual(report["policy"], "observed")
        self.assertEqual(report["interpretation"], "native_self_consistency")
        self.assertEqual(report["counts"]["observed_prefix"], 2)
        self.assertEqual(report["counts"]["matched"], 0)
        self.assertEqual(report["counts"]["calls"], 0)
        self.assertEqual(report["counts"]["matched_open"], 1)
        self.assertEqual(report["counts"]["matched_closed"], 1)
        for row in report["episodes"]:
            self.assertEqual(row["status"], "observed_prefix")
            self.assertEqual(row["matched_prefix"], 1)
            self.assertEqual(row["calls"], 0)
            for key in ("boundary_turn", "source_action", "candidate_action", "error"):
                self.assertIsNone(row[key])
        self.assertFalse(self.log.exists())
        self.assertNotIn(b"private-view", tape.canonical(report))

    def test_argument_validation_precedes_file_access(self) -> None:
        for policy in (
            "arrival",
            [],
            ["relative"],
            ["/absolute", "x\0y"],
            ["/absolute", 1],
            None,
        ):
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    prefix(self.source, policy)
        for timeout in (True, False, None, "1", 0, -1, 300.01, float("inf"), float("nan")):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "timeout"):
                    prefix(self.source, "observed", timeout=timeout)

    def test_empty_source_has_zero_totals(self) -> None:
        self._write()
        report = prefix(self.source, "observed")
        self.assertEqual(report["episodes"], [])
        self.assertTrue(all(count == 0 for count in report["counts"].values()))
