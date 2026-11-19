import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from seatlab import tape as wire
from seatlab.viewwork import (
    WORKER_ACTION_SCHEMA,
    WORKER_VIEW_SCHEMA,
    WorkerAction,
    WorkerView,
    act,
    make_request,
    read_actions,
    read_requests,
    validate_action,
    validate_request,
)

_NAMESPACE = "0123456789abcdef0123456789abcdef"
_OTHER_NAMESPACE = "fedcba9876543210fedcba9876543210"
_CONTEXT_TRANSFORM = f"context-v1:{'a' * 64}"


def _handoff_view() -> dict[str, object]:
    return {
        "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
        "messages": [
            {"revision": 2, "available": ["C", "D"]},
            {"revision": 1, "available": ["A", "B"]},
        ],
        "pooled_available": None,
    }


def _action_row(**changes: object) -> dict[str, object]:
    view: dict[str, object] = {"signal": "café"}
    request = make_request(view, _NAMESPACE, "external", "identity", 1)
    return {
        "schema": WORKER_ACTION_SCHEMA,
        "request_id": request["request_id"],
        "input_sha256": wire.digest(view),
        "policy": "external",
        "action": "B",
        "error": None,
        **changes,
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
    assert "SEATLAB_VIEWWORK_TEST_TOKEN" not in os.environ
    assert not list(Path.cwd().iterdir())
    assert Path.cwd() != Path(__file__).parent
    assert sys.argv[2] == "private-policy-argument"
    print("private-policy-stderr", file=sys.stderr)
    print('{"action":"B"}')
elif mode == "null":
    assert payload == b'{"signal":"null-choice"}\n'
    print('{"action":null}')
elif mode == "int":
    print('{"action":7}')
elif mode == "duplex":
    assert json.loads(payload) == {"signal": "z" * (256 * 1024)}
    print('{"action":"C"}')
elif mode == "timeout":
    print('{"action":"C"}', flush=True)
    time.sleep(30)
elif mode == "bool-action":
    print('{"action":true}')
elif mode == "invalid-output":
    print('{"action":"C"} private-policy-stdout')
elif mode == "output-limit":
    sys.stdout.buffer.write(b"x" * (128 * 1024))
elif mode == "nonzero":
    sys.exit(2)
"""


class RequestSchemaTests(unittest.TestCase):
    def test_make_request_is_deterministic_and_uses_exact_domain_and_fields(self) -> None:
        view: dict[str, object] = {"z": [2, 1], "a": "café"}
        request = make_request(view, _NAMESPACE, "revision", _CONTEXT_TRANSFORM, 1)
        expected_id = wire.digest(
            [
                "seatlab.worker-request-id/v1",
                _NAMESPACE,
                "revision",
                _CONTEXT_TRANSFORM,
                1,
                wire.digest(view),
            ]
        )
        self.assertEqual(
            request,
            {
                "schema": WORKER_VIEW_SCHEMA,
                "request_id": expected_id,
                "view": view,
            },
        )
        self.assertEqual(set(request.keys()), {"schema", "request_id", "view"})
        self.assertEqual(
            make_request(
                {"a": "café", "z": [2, 1]}, _NAMESPACE, "revision", _CONTEXT_TRANSFORM, 1
            ),
            request,
        )
        # Distinct namespaces, policies, transforms, ordinals, or views alter request_id.
        self.assertNotEqual(
            make_request(view, _OTHER_NAMESPACE, "revision", _CONTEXT_TRANSFORM, 1)[
                "request_id"
            ],
            expected_id,
        )
        self.assertNotEqual(
            make_request(view, _NAMESPACE, "arrival", _CONTEXT_TRANSFORM, 1)["request_id"],
            expected_id,
        )
        self.assertNotEqual(
            make_request(view, _NAMESPACE, "revision", "identity", 1)["request_id"],
            expected_id,
        )
        self.assertNotEqual(
            make_request(view, _NAMESPACE, "revision", _CONTEXT_TRANSFORM, 2)["request_id"],
            expected_id,
        )
        self.assertNotEqual(
            make_request({"a": "other"}, _NAMESPACE, "revision", _CONTEXT_TRANSFORM, 1)[
                "request_id"
            ],
            expected_id,
        )

    def test_make_request_rejects_invalid_inputs_and_observed_policy(self) -> None:
        view: dict[str, object] = {"a": 1}
        bad_views: tuple[Any, ...] = ([], "not-object", None, 123, {"bad": float("nan")})
        for bad_view in bad_views:
            with self.subTest(view=bad_view), self.assertRaises(wire.TapeError):
                make_request(bad_view, _NAMESPACE, "arrival", "identity", 1)
        bad_namespaces: tuple[Any, ...] = (
            "",
            "0" * 31,
            "0" * 33,
            "A" * 32,
            "g" * 32,
            None,
            123,
        )
        for bad_namespace in bad_namespaces:
            with self.subTest(namespace=bad_namespace), self.assertRaises(wire.TapeError):
                make_request(view, bad_namespace, "arrival", "identity", 1)
        bad_policies: tuple[Any, ...] = ("observed", "unknown", "", None)
        for bad_policy in bad_policies:
            with self.subTest(policy=bad_policy), self.assertRaises(wire.TapeError):
                make_request(view, _NAMESPACE, bad_policy, "identity", 1)
        bad_transforms: tuple[Any, ...] = (
            "",
            "other",
            "context-v1:" + "A" * 64,
            "context-v1:123",
            None,
        )
        for bad_transform in bad_transforms:
            with self.subTest(transform=bad_transform), self.assertRaises(wire.TapeError):
                make_request(view, _NAMESPACE, "arrival", bad_transform, 1)
        bad_ordinals: tuple[Any, ...] = (0, -1, True, False, 1.0, "1", None)
        for bad_ordinal in bad_ordinals:
            with self.subTest(ordinal=bad_ordinal), self.assertRaises(wire.TapeError):
                make_request(view, _NAMESPACE, "arrival", "identity", bad_ordinal)

    def test_validate_request_enforces_exact_field_allowlist_and_types(self) -> None:
        base = dict(make_request({"x": 1}, _NAMESPACE, "external", "identity", 1))
        self.assertEqual(validate_request(base), base)
        for field in ("schema", "request_id", "view"):
            missing = {k: v for k, v in base.items() if k != field}
            with self.subTest(missing=field), self.assertRaisesRegex(wire.TapeError, "exactly"):
                validate_request(missing)
        for extra in (
            {"key": "decision:0"},
            {"role": "dispatch"},
            {"answers": []},
            {"observed": None},
            {"exec": ["/bin/sh", "-c", "true"]},
            {"policy": "arrival"},
            {"view_sha256": wire.digest({"x": 1})},
            {"native_trial_id": None},
        ):
            with self.subTest(extra=extra), self.assertRaisesRegex(wire.TapeError, "exactly"):
                validate_request({**base, **extra})
        for changes in (
            {"schema": wire.DECISION_SCHEMA},
            {"schema": WORKER_ACTION_SCHEMA},
            {"request_id": "A" * 64},
            {"request_id": "0" * 63},
            {"request_id": "g" * 64},
            {"request_id": None},
            {"view": []},
            {"view": "not-a-dict"},
            {"view": {"bad": float("inf")}},
            {"view": {"bad": "\ud800"}},
        ):
            with self.subTest(changes=changes), self.assertRaises(wire.TapeError):
                validate_request({**base, **changes})
        with self.assertRaises(wire.TapeError):
            validate_request(["not", "an", "object"])


class ActionSchemaTests(unittest.TestCase):
    def test_validate_action_accepts_str_int_null_and_valid_errors(self) -> None:
        for action in ("A", "", 0, -5, None):
            with self.subTest(action=action):
                row = _action_row(action=action, error=None)
                validated = validate_action(row)
                self.assertEqual(validated, row)
                self.assertEqual(
                    set(validated.keys()),
                    {"schema", "request_id", "input_sha256", "policy", "action", "error"},
                )
        for policy in ("arrival", "revision", "intersection", "external"):
            with self.subTest(policy=policy):
                row = _action_row(policy=policy, action=None, error="invalid_action")
                self.assertEqual(validate_action(row), row)
        for error in (
            "invalid_action",
            "invalid_output",
            "spawn_failed",
            "policy_failed",
            "timeout",
            "output_limit",
        ):
            with self.subTest(error=error):
                row = _action_row(policy="external", action=None, error=error)
                self.assertEqual(validate_action(row), row)

    def test_validate_action_rejects_unknown_fields_invalid_digests_values_and_errors(
        self,
    ) -> None:
        base = _action_row()
        for field in ("schema", "request_id", "input_sha256", "policy", "action", "error"):
            missing = {k: v for k, v in base.items() if k != field}
            with self.subTest(missing=field), self.assertRaisesRegex(wire.TapeError, "exactly"):
                validate_action(missing)
        for extra in (
            {"score": {"optimal": True, "feasible": True}},
            {"status": "measured"},
            {"key": "decision:0"},
            {"input_bytes": 12},
            {"transform": "identity"},
        ):
            with self.subTest(extra=extra), self.assertRaisesRegex(wire.TapeError, "exactly"):
                validate_action({**base, **extra})
        for changes in (
            {"schema": wire.RESULT_SCHEMA},
            {"schema": WORKER_VIEW_SCHEMA},
            {"request_id": "A" * 64},
            {"request_id": "0" * 63},
            {"request_id": None},
            {"input_sha256": "A" * 64},
            {"input_sha256": "z" * 64},
            {"input_sha256": None},
            {"policy": "observed"},
            {"policy": "unknown"},
            {"policy": None},
            {"action": True},
            {"action": False},
            {"action": 1.0},
            {"action": []},
            {"action": {}},
            {"action": "\ud800"},
            {"action": "A", "error": "timeout"},
            {"action": 0, "error": "invalid_action"},
            {"action": "", "error": "policy_failed"},
            {"action": None, "error": "missing_observation"},
            {"action": None, "error": "unknown_error"},
            {"action": None, "error": 123},
            {"action": None, "error": True},
            {"policy": "arrival", "action": None, "error": "timeout"},
            {"policy": "revision", "action": None, "error": "policy_failed"},
            {"policy": "intersection", "action": None, "error": "spawn_failed"},
            {"policy": "arrival", "action": None, "error": "invalid_output"},
            {"policy": "revision", "action": None, "error": "output_limit"},
        ):
            with self.subTest(changes=changes), self.assertRaises(wire.TapeError):
                validate_action({**base, **changes})


class ReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "records.jsonl"

    def test_readers_accept_utf8_crlf_and_unterminated_final_line(self) -> None:
        first_req = make_request({"label": "salle é"}, _NAMESPACE, "arrival", "identity", 1)
        second_req = make_request({"label": "salle è"}, _NAMESPACE, "arrival", "identity", 2)
        raw_req = (
            json.dumps(first_req, ensure_ascii=False).encode("utf-8")
            + b"\r\n"
            + wire.canonical(second_req)
        )
        self.path.write_bytes(raw_req)
        self.assertEqual(list(read_requests(self.path)), [first_req, second_req])

        first_act = validate_action(
            _action_row(request_id=first_req["request_id"], action="café")
        )
        second_act = validate_action(
            _action_row(request_id=second_req["request_id"], action=None)
        )
        raw_act = (
            json.dumps(first_act, ensure_ascii=False).encode("utf-8")
            + b"\r\n"
            + wire.canonical(second_act)
        )
        self.path.write_bytes(raw_act)
        self.assertEqual(list(read_actions(self.path)), [first_act, second_act])

    def test_readers_reject_duplicate_json_keys_and_duplicate_request_ids(self) -> None:
        req = make_request({"x": 1}, _NAMESPACE, "arrival", "identity", 1)
        self.path.write_bytes(wire.canonical(req) + b"\n" + wire.canonical(req) + b"\n")
        with self.assertRaisesRegex(wire.TapeError, r":2: duplicate request_id"):
            list(read_requests(self.path))

        dup_key_req = (
            f'{{"schema":"{WORKER_VIEW_SCHEMA}","request_id":"{req["request_id"]}",'
            f'"view":{{"a":1,"\\u0061":2}}}}\n'
        ).encode("utf-8")
        self.path.write_bytes(dup_key_req)
        with self.assertRaisesRegex(wire.TapeError, r":1: JSON object contains a duplicate key"):
            list(read_requests(self.path))

        action = validate_action(_action_row(request_id=req["request_id"]))
        other = validate_action(_action_row(request_id=req["request_id"], action=None))
        self.path.write_bytes(wire.canonical(action) + b"\n" + wire.canonical(other) + b"\n")
        with self.assertRaisesRegex(wire.TapeError, r":2: duplicate request_id"):
            list(read_actions(self.path))

        dup_key_act = (
            f'{{"schema":"{WORKER_ACTION_SCHEMA}","request_id":"{req["request_id"]}",'
            f'"input_sha256":"{action["input_sha256"]}","policy":"external",'
            f'"action":"A","action":"B","error":null}}\n'
        ).encode("utf-8")
        self.path.write_bytes(dup_key_act)
        with self.assertRaisesRegex(wire.TapeError, r":1: JSON object contains a duplicate key"):
            list(read_actions(self.path))

    def test_readers_enforce_byte_limit_and_reject_malformed_lines(self) -> None:
        req = make_request({"signal": "abc"}, _NAMESPACE, "arrival", "identity", 1)
        line = wire.canonical(req)
        self.path.write_bytes(line + b"\r\n")
        with patch("seatlab.tape.MAX_LINE_BYTES", len(line)):
            self.assertEqual(list(read_requests(self.path)), [req])
            self.path.write_bytes(line + b" \n")
            with self.assertRaisesRegex(wire.TapeError, r":1: JSON line exceeds"):
                list(read_requests(self.path))

        action = validate_action(_action_row(request_id=req["request_id"]))
        act_line = wire.canonical(action)
        self.path.write_bytes(act_line + b"\r\n")
        with patch("seatlab.tape.MAX_LINE_BYTES", len(act_line)):
            self.assertEqual(list(read_actions(self.path)), [action])
            self.path.write_bytes(act_line + b" \n")
            with self.assertRaisesRegex(wire.TapeError, r":1: JSON line exceeds"):
                list(read_actions(self.path))

        for bad_bytes in (b"\n", b"{} {}\n", b"\xff\n", b'{"x":NaN}\n'):
            with self.subTest(bad_bytes=bad_bytes):
                self.path.write_bytes(bad_bytes)
                with self.assertRaises(wire.TapeError):
                    list(read_requests(self.path))
                with self.assertRaises(wire.TapeError):
                    list(read_actions(self.path))


class ActTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.requests = self.root / "part-0001.jsonl"
        self.out = self.root / "actions.jsonl"

    def _command(self, *args: str) -> list[str]:
        script = self.root / "private user policy.py"
        script.write_text(_POLICY, encoding="utf-8")
        return [str(Path(sys.executable).resolve()), "-I", str(script), *args]

    def test_builtin_policies_act_standalone_without_scoring_file(self) -> None:
        view = _handoff_view()
        req = make_request(view, _NAMESPACE, "arrival", "identity", 1)
        wire.write_lines(self.requests, [req])

        arrival_out = self.root / "arrival.jsonl"
        revision_out = self.root / "revision.jsonl"
        self.assertEqual(
            act(self.requests, arrival_out, "arrival"),
            {"requests": 1, "acted": 1, "failed": 0},
        )
        self.assertEqual(
            act(self.requests, revision_out, "revision"),
            {"requests": 1, "acted": 1, "failed": 0},
        )
        arrival_row = next(read_actions(arrival_out))
        revision_row = next(read_actions(revision_out))
        expected_sha = hashlib.sha256(wire.canonical(view)).hexdigest()
        self.assertEqual(
            arrival_row,
            {
                "schema": WORKER_ACTION_SCHEMA,
                "request_id": req["request_id"],
                "input_sha256": expected_sha,
                "policy": "arrival",
                "action": "A",
                "error": None,
            },
        )
        self.assertEqual(revision_row["policy"], "revision")
        self.assertEqual(revision_row["action"], "C")
        self.assertIsNone(revision_row["error"])
        self.assertEqual(revision_row["input_sha256"], expected_sha)

        # Intersection supports both integer choice and deliberate null choice.
        common_req = make_request(
            {"reports": {"m1": [2, 4], "m2": [1, 2]}},
            _NAMESPACE,
            "intersection",
            "identity",
            1,
        )
        empty_req = make_request(
            {"reports": {"m1": [2], "m2": [4]}}, _NAMESPACE, "intersection", "identity", 2
        )
        inter_requests = self.root / "inter-requests.jsonl"
        inter_out = self.root / "inter-actions.jsonl"
        wire.write_lines(inter_requests, [common_req, empty_req])
        self.assertEqual(
            act(inter_requests, inter_out, "intersection"),
            {"requests": 2, "acted": 2, "failed": 0},
        )
        rows = list(read_actions(inter_out))
        self.assertEqual(
            [(row["action"], row["error"]) for row in rows],
            [(2, None), (None, None)],
        )

    def test_act_rejects_observed_unknown_and_relative_exec_without_spawning(self) -> None:
        req = make_request(_handoff_view(), _NAMESPACE, "arrival", "identity", 1)
        wire.write_lines(self.requests, [req])
        bad_policies: tuple[Any, ...] = (
            "observed",
            "unknown",
            [],
            ["relative/policy.py"],
            ["/bin/echo\0bad"],
            [123],
        )
        bad_timeouts: tuple[Any, ...] = (0, -1, 301, True, False, "10")
        with patch("seatlab.replay.subprocess.Popen", side_effect=AssertionError("spawn")):
            for bad_policy in bad_policies:
                with self.subTest(policy=bad_policy), self.assertRaises(ValueError):
                    act(self.requests, self.out, bad_policy)
                self.assertFalse(self.out.exists())
            for bad_timeout in bad_timeouts:
                with self.subTest(timeout=bad_timeout), self.assertRaises(ValueError):
                    act(self.requests, self.out, "arrival", timeout=bad_timeout)
                self.assertFalse(self.out.exists())

    def test_packet_cannot_trigger_implicit_execution_for_builtin_policy(self) -> None:
        # Even if the view itself contains command-like data, builtin policy never spawns.
        view: dict[str, object] = {
            "reports": {"m1": [3], "m2": [3]},
        }
        req = make_request(view, _NAMESPACE, "intersection", "identity", 1)
        wire.write_lines(self.requests, [req])
        with patch("seatlab.replay.subprocess.Popen", side_effect=AssertionError("spawn")):
            summary = act(self.requests, self.out, "intersection")
        self.assertEqual(summary, {"requests": 1, "acted": 1, "failed": 0})
        self.assertEqual(next(read_actions(self.out))["action"], 3)

        # Unknown packet keys like exec/command are rejected at read time before any execution.
        injected = {
            **req,
            "exec": [str(Path(sys.executable).resolve()), "-c", "print('{\"action\":1}')"],
        }
        injected_path = self.root / "injected.jsonl"
        wire.write_lines(injected_path, [injected])
        injected_out = self.root / "injected-out.jsonl"
        with patch("seatlab.replay.subprocess.Popen", side_effect=AssertionError("spawn")):
            with self.assertRaises(wire.TapeError):
                act(injected_path, injected_out, "intersection")
        self.assertFalse(injected_out.exists())

    def test_host_and_view_validation_errors_are_not_captured_as_policy_failures(self) -> None:
        bad_req = make_request({"not": "a-handoff-view"}, _NAMESPACE, "arrival", "identity", 1)
        wire.write_lines(self.requests, [bad_req])
        with self.assertRaises(ValueError):
            act(self.requests, self.out, "arrival")
        self.assertFalse(self.out.exists())

        good_req = make_request(_handoff_view(), _NAMESPACE, "arrival", "identity", 1)
        good_path = self.root / "good.jsonl"
        wire.write_lines(good_path, [good_req])
        with patch("seatlab.replay.choose_view", side_effect=RuntimeError("host boom")):
            with self.assertRaisesRegex(RuntimeError, "host boom"):
                act(good_path, self.out, "arrival")
        self.assertFalse(self.out.exists())

    def test_existing_output_is_not_replaced(self) -> None:
        req = make_request(_handoff_view(), _NAMESPACE, "arrival", "identity", 1)
        wire.write_lines(self.requests, [req])
        self.out.write_bytes(b"existing-actions")
        with self.assertRaises(FileExistsError):
            act(self.requests, self.out, "arrival")
        self.assertEqual(self.out.read_bytes(), b"existing-actions")

    @unittest.skipUnless(os.name == "posix", "external replay requires POSIX")
    def test_external_policy_receives_only_canonical_view_never_envelope_id(self) -> None:
        view: dict[str, object] = {"some": [1, True], "signal": "café"}
        req = make_request(view, _NAMESPACE, "external", "identity", 1)
        wire.write_lines(self.requests, [req])
        command = self._command("contract", "private-policy-argument")
        with patch.dict(os.environ, {"SEATLAB_VIEWWORK_TEST_TOKEN": "private-policy-env"}):
            summary = act(self.requests, self.out, command)
        self.assertEqual(summary, {"requests": 1, "acted": 1, "failed": 0})
        action_row: WorkerAction = next(read_actions(self.out))
        self.assertEqual(
            action_row,
            {
                "schema": WORKER_ACTION_SCHEMA,
                "request_id": req["request_id"],
                "input_sha256": wire.digest(view),
                "policy": "external",
                "action": "B",
                "error": None,
            },
        )
        ledger = self.out.read_text(encoding="utf-8")
        for secret in (command[0], command[2], "private-policy-"):
            self.assertNotIn(secret, ledger)

    @unittest.skipUnless(os.name == "posix", "external replay requires POSIX")
    def test_external_null_action_is_distinct_from_policy_failure(self) -> None:
        null_view: dict[str, object] = {"signal": "null-choice"}
        req: WorkerView = make_request(null_view, _NAMESPACE, "external", "identity", 1)
        wire.write_lines(self.requests, [req])

        null_out = self.root / "null.jsonl"
        self.assertEqual(
            act(self.requests, null_out, self._command("null")),
            {"requests": 1, "acted": 1, "failed": 0},
        )
        null_row = next(read_actions(null_out))
        self.assertIsNone(null_row["action"])
        self.assertIsNone(null_row["error"])
        self.assertEqual(null_row["input_sha256"], wire.digest(null_view))

        int_out = self.root / "int.jsonl"
        self.assertEqual(
            act(self.requests, int_out, self._command("int")),
            {"requests": 1, "acted": 1, "failed": 0},
        )
        int_row = next(read_actions(int_out))
        self.assertEqual(int_row["action"], 7)
        self.assertIsNone(int_row["error"])

        cases = {
            "timeout": "timeout",
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
                summary = act(self.requests, out, command, timeout=timeout)
                row = next(read_actions(out))
                self.assertEqual(summary, {"requests": 1, "acted": 0, "failed": 1})
                self.assertEqual(row["policy"], "external")
                self.assertIsNone(row["action"])
                self.assertEqual(row["error"], reason)
                self.assertEqual(row["input_sha256"], wire.digest(null_view))
                self.assertNotIn("private-", out.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "external replay requires POSIX")
    def test_external_duplex_large_view_and_fresh_output(self) -> None:
        view: dict[str, object] = {"signal": "z" * (256 * 1024)}
        req = make_request(view, _NAMESPACE, "external", "identity", 1)
        wire.write_lines(self.requests, [req])
        summary = act(self.requests, self.out, self._command("duplex"))
        self.assertEqual(summary, {"requests": 1, "acted": 1, "failed": 0})
        row = next(read_actions(self.out))
        self.assertEqual(row["action"], "C")
        self.assertIsNone(row["error"])
        self.assertEqual(row["input_sha256"], wire.digest(view))
