import json
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

from seatlab import author, tape
from seatlab.episodes import read_episodes
from seatlab.windows import (
    ACTOR_WINDOW_SCHEMA,
    RECEIPT_SCHEMA,
    VERIFICATION_SCHEMA,
    actor_window,
    verify_windows,
)

_TRIAL_A = "12345678-1234-4abc-9def-0123456789ab"
_TRIAL_B = "22345678-1234-4abc-8def-0123456789ab"
_CASE = tape.digest({"case": "window"})
_SECRET = "SECRET_VIEW_PAYLOAD"
_SECRET_ACTION = "SECRET_ACTION_PAYLOAD"


class _RaisingHistory(list):
    def __len__(self) -> int:
        raise AssertionError("history length was read")

    def __getitem__(self, index: object) -> object:
        raise AssertionError("history item was read")


def _bound_episode(
    episode_id: str,
    role: str,
    trial: str,
    steps: list[tuple[dict[str, object], tape.Action]],
    *,
    closed: bool = True,
    case: str = _CASE,
) -> dict:
    frames = [
        author.accepted_frame(
            turn=turn,
            view=view,
            acknowledged_sha256=tape.digest(view),
            action=action,
        )
        for turn, (view, action) in enumerate(steps, 1)
    ]
    return author.episode_from_frames(
        id=episode_id,
        case_sha256=case,
        role=role,
        native_trial_id=trial,
        closed=closed,
        frames=frames,
    )


def _receipt(episode: dict, depth: int) -> dict:
    frames = episode["frames"]
    receipt_frames = []
    for index, frame in enumerate(frames):
        if depth == 0:
            history: Sequence[object] = ()
        else:
            start = index - depth if index > depth else 0
            history = [
                {
                    "view": frames[offset]["view"],
                    "action": frames[offset]["observed"]["action"],
                }
                for offset in range(start, index)
            ]
        envelope = actor_window(frame["view"], history, depth)
        receipt_frames.append(
            {
                "turn": frame["turn"],
                "view_sha256": frame["view_sha256"],
                "action_sha256": tape.digest({"action": frame["observed"]["action"]}),
                "context_sha256": tape.digest(envelope),
                "context_bytes": len(tape.canonical(envelope)),
            }
        )
    return {
        "schema": RECEIPT_SCHEMA,
        "native_trial_id": episode["native_trial_id"],
        "role": episode["role"],
        "depth": depth,
        "frames": receipt_frames,
    }


def _write(path: Path, rows: list[object]) -> None:
    path.write_bytes(b"".join(tape.canonical(row) + b"\n" for row in rows))


def _manual_window(current: dict[str, object], history: list[dict[str, object]]) -> dict:
    return {"schema": ACTOR_WINDOW_SCHEMA, "current": current, "history": history}


class WindowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(directory)
        self.episodes = self.directory / "episodes.jsonl"
        self.receipts = self.directory / "receipts.jsonl"
        self.out = self.directory / "policy.jsonl"

    def _verify(
        self, episodes: list[dict], receipts: list[dict], *, out: Path | None = None
    ) -> dict:
        _write(self.episodes, episodes)
        _write(self.receipts, receipts)
        return verify_windows(self.episodes, self.receipts, out)

    def _assert_no_payload(self, text: str) -> None:
        self.assertNotIn(_SECRET, text)
        self.assertNotIn(_SECRET_ACTION, text)

    def test_same_current_view_with_different_private_histories(self) -> None:
        current = {"observation": 0, "card": _SECRET}
        prior = {"observation": "start"}
        first = actor_window(current, [{"view": prior, "action": 1}], 1)
        second = actor_window(current, [{"view": prior, "action": 2}], 1)
        ignored = actor_window(current, [{"view": prior, "action": 1}], 0)
        also_ignored = actor_window(current, [{"view": prior, "action": 2}], 0)

        self.assertEqual(first["current"], second["current"])
        self.assertEqual(set(first), {"schema", "current", "history"})
        self.assertEqual(
            tape.canonical(first),
            tape.canonical(_manual_window(current, [{"view": prior, "action": 1}])),
        )
        self.assertNotEqual(tape.digest(first), tape.digest(second))
        self.assertEqual(tape.digest(ignored), tape.digest(also_ignored))
        self.assertEqual(ignored["history"], [])

        shared = {"observation": 0, "card": _SECRET}
        left = _bound_episode(
            "left",
            "player_0",
            _TRIAL_A,
            [({"observation": "start"}, 1), (shared, 0)],
        )
        right = _bound_episode(
            "right",
            "player_0",
            _TRIAL_B,
            [({"observation": "start"}, 2), (dict(shared), 1)],
            case=tape.digest({"case": "other"}),
        )
        report = self._verify([left, right], [_receipt(left, 1), _receipt(right, 1)])
        self.assertNotEqual(
            _receipt(left, 1)["frames"][1]["context_sha256"],
            _receipt(right, 1)["frames"][1]["context_sha256"],
        )
        self.assertEqual(report["status"], "verified")
        self._assert_no_payload(json.dumps(report))

    def test_depth_zero_does_not_copy_history_and_result_does_not_alias(self) -> None:
        current = {"nested": [1], "card": _SECRET}
        item = {"view": {"nested": [2]}, "action": 0}
        history = [item]
        window = actor_window(current, history, 1)
        untouched = actor_window({"only": 1}, _RaisingHistory(), 0)

        current["nested"].append(9)
        item["view"]["nested"].append(8)
        item["action"] = 7
        history.append({"view": {"extra": True}, "action": 3})
        window["history"][0]["view"]["nested"].append(6)

        self.assertEqual(window["current"], {"nested": [1], "card": _SECRET})
        self.assertEqual(window["history"], [{"view": {"nested": [2, 6]}, "action": 0}])
        self.assertEqual(item["view"]["nested"], [2, 8])
        self.assertIsNot(window["current"], current)
        self.assertIsNot(window["history"][0]["view"], item["view"])
        self.assertEqual(untouched["history"], [])
        self.assertEqual(actor_window({"i": 4}, history, 1)["history"][0]["action"], 3)
        self.assertEqual(
            [row["action"] for row in actor_window({"i": 4}, history, 16)["history"]],
            [7, 3],
        )

    def test_window_rejects_bad_depth_extra_keys_and_nested_envelopes(self) -> None:
        current = {"observation": 0}
        valid = [{"view": {"observation": 1}, "action": 0}]
        nested = actor_window(current, [], 0)
        for depth in (True, False, -1, 17, 1.0):
            with self.subTest(depth=depth):
                with self.assertRaises(tape.TapeError):
                    actor_window(current, valid, depth)
        with self.assertRaises(tape.TapeError):
            actor_window(current, [{"view": current, "action": 0, "turn": 1}], 1)
        with self.assertRaisesRegex(tape.TapeError, "nest"):
            actor_window(nested, [], 0)
        with self.assertRaisesRegex(tape.TapeError, "nest"):
            actor_window(current, [{"view": nested, "action": 1}], 1)
        with self.assertRaises(tape.TapeError):
            actor_window([], [], 0)
        with self.assertRaises(tape.TapeError):
            actor_window(current, "not-a-sequence", 0)
        with self.assertRaises(tape.TapeError):
            actor_window(current, [{"view": {"x": 1}, "action": True}], 1)
        with self.assertRaises(tape.TapeError):
            actor_window(current, [{"view": [], "action": 0}], 1)
        with self.assertRaises(tape.TapeError):
            actor_window({"x": {1}}, [], 0)
        self.assertEqual(actor_window(current, [None, valid[0]], 1)["history"], valid)

    def test_missing_excess_altered_and_wrong_role_receipts(self) -> None:
        view = {"card": _SECRET}
        episode = _bound_episode("seat", "player_0", _TRIAL_A, [(view, _SECRET_ACTION)])
        other = _bound_episode("other", "player_1", _TRIAL_A, [(view, 1)])
        receipt = _receipt(episode, 0)
        extra = _receipt(other, 0)
        extra["native_trial_id"] = _TRIAL_B

        with self.assertRaisesRegex(tape.TapeError, "missing window receipt") as missing:
            self._verify([episode, other], [receipt])
        self._assert_no_payload(str(missing.exception))
        self.assertFalse(self.out.exists())

        with self.assertRaisesRegex(tape.TapeError, "unexpected window receipt") as excess:
            self._verify([episode], [receipt, extra])
        self._assert_no_payload(str(excess.exception))

        wrong_role = _receipt(episode, 0)
        wrong_role["role"] = "player_1"
        with self.assertRaisesRegex(tape.TapeError, "role does not match") as role_error:
            self._verify([episode], [wrong_role])
        self._assert_no_payload(str(role_error.exception))

        altered = _receipt(episode, 0)
        digest = altered["frames"][0]["context_sha256"]
        last = "a" if digest[-1] != "a" else "b"
        altered["frames"][0]["context_sha256"] = digest[:-1] + last
        with self.assertRaisesRegex(tape.TapeError, "context digest") as digest_error:
            self._verify([episode], [altered], out=self.out)
        self._assert_no_payload(str(digest_error.exception))
        self.assertFalse(self.out.exists())

        bad_bytes = _receipt(episode, 1)
        bad_bytes["frames"][0]["context_bytes"] += 1
        with self.assertRaisesRegex(tape.TapeError, "context bytes do not match"):
            self._verify([episode], [bad_bytes])

        bad_action = _receipt(episode, 0)
        action_digest = bad_action["frames"][0]["action_sha256"]
        bad_action["frames"][0]["action_sha256"] = "0" * 64
        self.assertNotEqual(action_digest, bad_action["frames"][0]["action_sha256"])
        with self.assertRaisesRegex(tape.TapeError, "action digest") as action_error:
            self._verify([episode], [bad_action])
        self._assert_no_payload(str(action_error.exception))

        bad_view = _receipt(episode, 0)
        bad_view["frames"][0]["view_sha256"] = "1" * 64
        with self.assertRaisesRegex(tape.TapeError, "view digest does not match"):
            self._verify([episode], [bad_view])

        extra_key = _receipt(episode, 0)
        extra_key["native_case"] = _CASE
        with self.assertRaisesRegex(tape.TapeError, "exactly the documented fields"):
            self._verify([episode], [extra_key])

    def test_null_action_is_not_a_missing_observation(self) -> None:
        view = {"card": _SECRET}
        missing = {
            "turn": 1,
            "basis": "bound",
            "view": view,
            "view_sha256": tape.digest(view),
            "observed": None,
        }
        unbound = {
            "turn": 1,
            "basis": "reconstructed",
            "view": view,
            "view_sha256": tape.digest(view),
            "observed": {"action": None},
        }
        missing_episode = author.episode_from_frames(
            id="missing",
            case_sha256=_CASE,
            role="player_0",
            native_trial_id=_TRIAL_A,
            closed=True,
            frames=[missing],
        )
        unbound_episode = author.episode_from_frames(
            id="unbound",
            case_sha256=_CASE,
            role="player_0",
            native_trial_id=_TRIAL_A,
            closed=True,
            frames=[unbound],
        )
        null_episode = _bound_episode(
            "null", "player_0", _TRIAL_A, [(view, None)], closed=False
        )
        placeholder = _receipt(null_episode, 0)

        with self.assertRaisesRegex(tape.TapeError, "observed action") as missing_error:
            self._verify([missing_episode], [placeholder])
        self._assert_no_payload(str(missing_error.exception))
        with self.assertRaisesRegex(tape.TapeError, "frame is not bound") as unbound_error:
            self._verify([unbound_episode], [placeholder])
        self._assert_no_payload(str(unbound_error.exception))

        report = self._verify([null_episode], [_receipt(null_episode, 0)], out=self.out)
        published = list(read_episodes(self.out))
        self.assertEqual(len(published), 1)
        frame = published[0]["frames"][0]
        self.assertEqual(frame["observed"], {"action": None})
        self.assertIsNotNone(frame["observed"])
        self.assertEqual(frame["view"]["history"], [])
        self.assertEqual(frame["view"]["current"], view)
        self.assertNotIn("native_trial_id", frame["view"])
        self.assertEqual(published[0]["native_trial_id"], _TRIAL_A)
        self.assertEqual(published[0]["id"], "null")
        self.assertEqual(published[0]["case_sha256"], _CASE)
        self.assertFalse(published[0]["closed"])
        self.assertEqual(report["counts"]["verified_decisions"], 1)
        self.assertNotEqual(frame["view_sha256"], tape.digest(view))
        self.assertEqual(frame["view_sha256"], tape.digest(_manual_window(view, [])))

    def test_atomic_publish_does_not_replace_and_failed_verify_writes_nothing(self) -> None:
        episode = _bound_episode("seat", "player_0", _TRIAL_A, [({"card": _SECRET}, 1)])
        receipt = _receipt(episode, 0)
        report = self._verify([episode], [receipt], out=self.out)
        first = self.out.read_bytes()
        self.assertEqual(report["status"], "verified")
        with self.assertRaises(FileExistsError):
            self._verify([episode], [receipt], out=self.out)
        self.assertEqual(self.out.read_bytes(), first)

        kept = self.directory / "kept.jsonl"
        kept.write_bytes(b"keep\n")
        link = self.directory / "link.jsonl"
        link.symlink_to(kept)
        with self.assertRaises(FileExistsError):
            self._verify([episode], [receipt], out=link)
        self.assertEqual(kept.read_bytes(), b"keep\n")
        self.assertTrue(link.is_symlink())

        rejected = self.directory / "rejected.jsonl"
        rejected.write_bytes(b"keep\n")
        bad = _receipt(episode, 0)
        bad["frames"][0]["context_bytes"] += 1
        with self.assertRaises(tape.TapeError):
            self._verify([episode], [bad], out=rejected)
        self.assertEqual(rejected.read_bytes(), b"keep\n")
        absent = self.directory / "absent.jsonl"
        with self.assertRaises(tape.TapeError):
            self._verify([episode], [bad], out=absent)
        self.assertFalse(absent.exists())

    def test_verification_counts_seats_and_rejects_cross_joins(self) -> None:
        first = _bound_episode(
            "a",
            "player_0",
            _TRIAL_A,
            [({"card": _SECRET}, 0), ({"card": _SECRET}, _SECRET_ACTION)],
            closed=True,
        )
        second = _bound_episode(
            "b",
            "player_1",
            _TRIAL_A,
            [({"card": "other"}, 2)],
            closed=False,
        )
        third = _bound_episode(
            "c",
            "player_0",
            _TRIAL_B,
            [({"card": _SECRET}, 1)],
            case=_CASE,
        )
        rows = [_receipt(first, 4), _receipt(second, 2), _receipt(third, 0)]
        receipts = [rows[2], rows[0], rows[1]]
        report = self._verify([first, second, third], receipts, out=self.out)
        self.assertEqual(
            report,
            {
                "schema": VERIFICATION_SCHEMA,
                "status": "verified",
                "counts": {
                    "native_trials": 2,
                    "role_episodes": 3,
                    "verified_decisions": 4,
                    "context_bytes": sum(
                        frame["context_bytes"] for row in rows for frame in row["frames"]
                    ),
                },
                "seats": [
                    {
                        "native_trial_id": _TRIAL_A,
                        "role": "player_0",
                        "depth": 4,
                        "decisions": 2,
                        "bytes": sum(frame["context_bytes"] for frame in rows[0]["frames"]),
                    },
                    {
                        "native_trial_id": _TRIAL_A,
                        "role": "player_1",
                        "depth": 2,
                        "decisions": 1,
                        "bytes": rows[1]["frames"][0]["context_bytes"],
                    },
                    {
                        "native_trial_id": _TRIAL_B,
                        "role": "player_0",
                        "depth": 0,
                        "decisions": 1,
                        "bytes": rows[2]["frames"][0]["context_bytes"],
                    },
                ],
            },
        )
        self._assert_no_payload(json.dumps(report))
        published = list(read_episodes(self.out))
        self.assertEqual([row["id"] for row in published], ["a", "b", "c"])
        self.assertEqual(published[0]["native_trial_id"], _TRIAL_A)
        turn_two = published[0]["frames"][1]["view"]
        self.assertEqual(set(turn_two), {"schema", "current", "history"})
        self.assertEqual(turn_two["history"], [{"view": {"card": _SECRET}, "action": 0}])
        self.assertNotIn("native_trial_id", turn_two)
        self.assertNotIn("id", turn_two)

        crossed = _receipt(first, 4)
        crossed["native_trial_id"] = _TRIAL_B
        with self.assertRaisesRegex(tape.TapeError, "crosses native trials") as crossed_error:
            self._verify([first], [crossed])
        self._assert_no_payload(str(crossed_error.exception))

        uppercase = _receipt(first, 4)
        uppercase["native_trial_id"] = _TRIAL_A.upper()
        with self.assertRaises(tape.TapeError) as case_error:
            self._verify([first], [uppercase])
        self._assert_no_payload(str(case_error.exception))

    def test_malformed_receipts_and_future_context_are_rejected(self) -> None:
        episode = _bound_episode(
            "seat",
            "player_0",
            _TRIAL_A,
            [({"card": _SECRET}, 0), ({"card": _SECRET}, 1)],
        )
        receipt = _receipt(episode, 1)
        _write(self.episodes, [episode])

        future = actor_window(
            episode["frames"][0]["view"],
            [{"view": episode["frames"][1]["view"], "action": 1}],
            1,
        )
        forged = _receipt(episode, 1)
        forged["frames"][0]["context_sha256"] = tape.digest(future)
        forged["frames"][0]["context_bytes"] = len(tape.canonical(future))
        with self.assertRaisesRegex(tape.TapeError, "context digest") as future_error:
            self._verify([episode], [forged])
        self._assert_no_payload(str(future_error.exception))

        identified = actor_window(episode["frames"][0]["view"], [], 0)
        identified["native_trial_id"] = _TRIAL_A
        identified_receipt = _receipt(episode, 0)
        identified_receipt["frames"][0]["context_sha256"] = tape.digest(identified)
        identified_receipt["frames"][0]["context_bytes"] = len(tape.canonical(identified))
        with self.assertRaisesRegex(tape.TapeError, "context digest does not match"):
            self._verify([episode], [identified_receipt])

        short = _receipt(episode, 1)
        del short["frames"][1]
        with self.assertRaisesRegex(tape.TapeError, "receipt length does not match"):
            self._verify([episode], [short])

        duplicate = tape.canonical(receipt) + b"\n"
        self.receipts.write_bytes(duplicate + duplicate)
        with self.assertRaisesRegex(tape.TapeError, "duplicate window receipt"):
            verify_windows(self.episodes, self.receipts)

        self.receipts.write_bytes(b'{"schema":"seatlab.window-receipt/v1","schema":"x"}\n')
        with self.assertRaisesRegex(tape.TapeError, "duplicate key"):
            verify_windows(self.episodes, self.receipts)

        self.receipts.write_bytes(b'{"depth":NaN}\n')
        with self.assertRaisesRegex(tape.TapeError, "finite"):
            verify_windows(self.episodes, self.receipts)

        self.receipts.write_bytes(b"{" + b"x" * (tape.MAX_LINE_BYTES + 1) + b"\n")
        with self.assertRaisesRegex(tape.TapeError, "byte limit"):
            verify_windows(self.episodes, self.receipts)

        raw = tape.canonical(receipt)
        self.receipts.write_bytes(raw.replace(b'"depth":1', b'"depth":true', 1) + b"\n")
        with self.assertRaisesRegex(tape.TapeError, "depth must be an integer"):
            verify_windows(self.episodes, self.receipts)

        uppercase_line = raw.replace(b'"view_sha256":"', b'"view_sha256":"A', 1)
        self.receipts.write_bytes(uppercase_line + b"\n")
        with self.assertRaises(tape.TapeError) as uppercase:
            verify_windows(self.episodes, self.receipts)
        self._assert_no_payload(str(uppercase.exception))

        frame_extra = _receipt(episode, 1)
        frame_extra["frames"][0]["observed_action"] = 0
        self.receipts.write_bytes(tape.canonical(frame_extra) + b"\n")
        with self.assertRaisesRegex(tape.TapeError, "exactly the documented fields"):
            verify_windows(self.episodes, self.receipts)

        float_bytes = _receipt(episode, 0)
        float_bytes["frames"][0]["context_bytes"] = 1.5
        self.receipts.write_bytes(tape.canonical(float_bytes) + b"\n")
        with self.assertRaisesRegex(tape.TapeError, "non-negative integer"):
            verify_windows(self.episodes, self.receipts)

        empty = self.directory / "empty.jsonl"
        empty.write_bytes(b"")
        report = verify_windows(empty, empty)
        self.assertEqual(report["counts"]["native_trials"], 0)
        self.assertEqual(report["seats"], [])
        self.assertEqual(report["schema"], VERIFICATION_SCHEMA)
