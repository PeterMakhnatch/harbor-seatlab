import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from seatlab import author, episodes, tape

_TRIAL = "12345678-1234-4abc-9def-0123456789ab"
_CASE_SHA = tape.digest({"case": "receipt-board", "index": 0})


def _answers() -> list[tape.Answer]:
    return [
        {"action": None, "optimal": False, "feasible": False},
        {"action": 0, "optimal": False, "feasible": True},
        {"action": "approve", "optimal": True, "feasible": True},
    ]


def _range_answers() -> tape.IntRangeAnswers:
    return {
        "kind": "int-range",
        "min": 0,
        "max": 3,
        "include_null": True,
        "optimal": [2],
        "feasible": [1, 2],
    }


def _reconstructed_frame(
    turn: int = 1,
    *,
    view: dict[str, object] | None = None,
    observed: tape.Observation | None = None,
) -> episodes.EpisodeFrame:
    payload = {"board": ["item-a"], "turn": turn} if view is None else view
    return {
        "turn": turn,
        "basis": "reconstructed",
        "view": payload,
        "view_sha256": tape.digest(payload),
        "observed": observed,
    }


class AuthorApiTests(unittest.TestCase):
    def test_public_surface_has_no_extra_aliases_or_helpers(self) -> None:
        self.assertEqual(
            author.__all__,
            ["accepted_frame", "decision_from_frame", "episode_from_frames"],
        )

    def test_accepted_frame_binds_canonical_view_and_preserves_explicit_null_action(
        self,
    ) -> None:
        view: dict[str, object] = {"z": ["café", 2], "a": {"nested": [1, 2]}}
        ack = tape.digest({"a": {"nested": [1, 2]}, "z": ["café", 2]})

        frame = author.accepted_frame(
            turn=1,
            view=view,
            acknowledged_sha256=ack,
            action=None,
        )

        self.assertEqual(frame["turn"], 1)
        self.assertEqual(frame["basis"], "bound")
        self.assertEqual(frame["view"], view)
        self.assertEqual(frame["view_sha256"], ack)
        self.assertEqual(frame["observed"], {"action": None})

        # Mutating caller input after frame creation does not desynchronize the frame.
        cast(list[object], view["z"]).append("mutated")
        cast(dict[str, object], view["a"])["nested"] = []
        self.assertEqual(frame["view"], {"z": ["café", 2], "a": {"nested": [1, 2]}})
        self.assertEqual(tape.digest(frame["view"]), frame["view_sha256"])

        int_frame = author.accepted_frame(
            turn=2,
            view={"slot": 2},
            acknowledged_sha256=tape.digest({"slot": 2}),
            action=0,
        )
        str_frame = author.accepted_frame(
            turn=3,
            view={"slot": 3},
            acknowledged_sha256=tape.digest({"slot": 3}),
            action="approve",
        )
        self.assertEqual(int_frame["observed"], {"action": 0})
        self.assertEqual(str_frame["observed"], {"action": "approve"})

    def test_accepted_frame_rejects_mismatched_digest_and_invalid_inputs(self) -> None:
        view: dict[str, object] = {"items": [1, 2], "extra": "visible"}
        ack = tape.digest(view)
        projected_ack = tape.digest({"items": [1, 2]})

        with self.assertRaisesRegex(tape.TapeError, "acknowledged_sha256 does not match"):
            author.accepted_frame(
                turn=1,
                view=view,
                acknowledged_sha256=projected_ack,
                action="approve",
            )

        bad_digests: tuple[Any, ...] = (
            "",
            None,
            ack.upper(),
            ack[:-1],
            "not-a-digest",
        )
        for bad_digest in bad_digests:
            with self.subTest(acknowledged_sha256=bad_digest):
                with self.assertRaises(tape.TapeError):
                    author.accepted_frame(
                        turn=1,
                        view=view,
                        acknowledged_sha256=bad_digest,
                        action=None,
                    )

        bad_turns: tuple[Any, ...] = (0, -1, True, False, 1.0, "1", None)
        for bad_turn in bad_turns:
            with self.subTest(turn=bad_turn):
                with self.assertRaises(tape.TapeError):
                    author.accepted_frame(
                        turn=bad_turn,
                        view=view,
                        acknowledged_sha256=ack,
                        action="approve",
                    )

        bad_views: tuple[Any, ...] = (
            [],
            "not-an-object",
            None,
            {1: "non-string-key"},
            {"bad": float("nan")},
            {"bad": (1, 2)},
            {"bad": "\ud800"},
        )
        for bad_view in bad_views:
            with self.subTest(view=bad_view):
                with self.assertRaises(tape.TapeError):
                    author.accepted_frame(
                        turn=1,
                        view=bad_view,
                        acknowledged_sha256=ack,
                        action="approve",
                    )

        bad_actions: tuple[Any, ...] = (True, False, 1.0, [], {}, "\ud800")
        for bad_action in bad_actions:
            with self.subTest(action=bad_action):
                with self.assertRaises(tape.TapeError):
                    author.accepted_frame(
                        turn=1,
                        view=view,
                        acknowledged_sha256=ack,
                        action=bad_action,
                    )

    def test_decision_from_frame_distinguishes_explicit_null_and_absent_observation(
        self,
    ) -> None:
        bound_view: dict[str, object] = {"board": ["r1"]}
        bound_frame = author.accepted_frame(
            turn=1,
            view=bound_view,
            acknowledged_sha256=tape.digest(bound_view),
            action=None,
        )
        answers = _answers()
        bound_decision = author.decision_from_frame(
            key="receipt:case-0:turn-1",
            role="reviewer",
            native_trial_id=_TRIAL,
            frame=bound_frame,
            answers=answers,
        )
        self.assertEqual(bound_decision["basis"], "bound")
        self.assertEqual(bound_decision["observed"], {"action": None})
        self.assertEqual(tape.decision(bound_decision), bound_decision)

        # Caller mutations to frame or answers cannot affect the returned decision.
        cast(list[object], bound_frame["view"]["board"]).append("tampered")
        assert bound_frame["observed"] is not None
        bound_frame["observed"]["action"] = "approve"
        answers[0]["optimal"] = True
        answers.append({"action": "extra", "optimal": False, "feasible": False})
        self.assertEqual(bound_decision["view"], {"board": ["r1"]})
        self.assertEqual(bound_decision["observed"], {"action": None})
        self.assertEqual(len(cast(list[tape.Answer], bound_decision["answers"])), 3)
        self.assertEqual(tape.decision(bound_decision), bound_decision)

        # Absent observation remains observed=None and reconstructed basis stays reconstructed.
        reconstructed = _reconstructed_frame(turn=2, observed=None)
        range_ans = _range_answers()
        reconstructed_decision = author.decision_from_frame(
            key="receipt:case-0:turn-2",
            role="reviewer",
            native_trial_id=_TRIAL,
            frame=reconstructed,
            answers=range_ans,
        )
        self.assertEqual(reconstructed_decision["basis"], "reconstructed")
        self.assertIsNone(reconstructed_decision["observed"])
        range_ans["optimal"].append(3)
        self.assertEqual(
            cast(tape.IntRangeAnswers, reconstructed_decision["answers"])["optimal"],
            [2],
        )
        self.assertEqual(tape.decision(reconstructed_decision), reconstructed_decision)

        # Tuple sequence of answers is normalized to a validated JSON list.
        tuple_decision = author.decision_from_frame(
            key="receipt:case-0:turn-3",
            role="reviewer",
            native_trial_id=_TRIAL,
            frame=reconstructed,
            answers=tuple(_answers()),
        )
        self.assertIsInstance(tuple_decision["answers"], list)
        self.assertEqual(tape.decision(tuple_decision), tuple_decision)

    def test_decision_from_frame_revalidates_mutated_frame_ids_and_answers(self) -> None:
        view: dict[str, object] = {"board": ["r1"]}
        frame = author.accepted_frame(
            turn=1,
            view=view,
            acknowledged_sha256=tape.digest(view),
            action="approve",
        )

        # Mutated view after accepted_frame must be caught by decision_from_frame.
        cast(list[object], frame["view"]["board"]).append("mutated")
        with self.assertRaisesRegex(tape.TapeError, "view_sha256 does not match view"):
            author.decision_from_frame(
                key="receipt:case-0:turn-1",
                role="reviewer",
                native_trial_id=_TRIAL,
                frame=frame,
                answers=_answers(),
            )

        fresh = author.accepted_frame(
            turn=1,
            view={"board": ["r1"]},
            acknowledged_sha256=tape.digest({"board": ["r1"]}),
            action="approve",
        )
        invalid_frames: tuple[Any, ...] = (
            {**fresh, "basis": "design"},
            {**fresh, "view_sha256": "0" * 64},
            {**fresh, "observed": {"action": True}},
            {**fresh, "extra": 1},
            {k: v for k, v in fresh.items() if k != "view_sha256"},
        )
        for bad_frame in invalid_frames:
            with self.subTest(bad_frame=bad_frame):
                with self.assertRaises(tape.TapeError):
                    author.decision_from_frame(
                        key="receipt:case-0:turn-1",
                        role="reviewer",
                        native_trial_id=_TRIAL,
                        frame=bad_frame,
                        answers=_answers(),
                    )

        invalid_kwargs: tuple[dict[str, Any], ...] = (
            {"key": "_bad"},
            {"key": ""},
            {"role": "1bad"},
            {"role": "bad.role"},
            {"native_trial_id": None},
            {"native_trial_id": _TRIAL.upper()},
            {"native_trial_id": "not-a-uuid"},
            {"answers": "not-answers"},
            {"answers": [{"action": "a", "optimal": True, "feasible": True}] * 2},
            {"answers": {**_range_answers(), "min": 5, "max": 2}},
        )
        for changes in invalid_kwargs:
            with self.subTest(changes=changes):
                kwargs: dict[str, Any] = {
                    "key": "receipt:case-0:turn-1",
                    "role": "reviewer",
                    "native_trial_id": _TRIAL,
                    "frame": fresh,
                    "answers": _answers(),
                    **changes,
                }
                with self.assertRaises(tape.TapeError):
                    author.decision_from_frame(**kwargs)

    def test_episode_from_frames_seals_ordered_episode_and_round_trips_jsonl(self) -> None:
        first_view: dict[str, object] = {"step": 1, "entries": ["a"]}
        second_view: dict[str, object] = {"step": 2, "entries": ["a", "b"]}
        third_view: dict[str, object] = {"step": 3, "entries": ["a", "b", "c"]}
        first = author.accepted_frame(
            turn=1,
            view=first_view,
            acknowledged_sha256=tape.digest(first_view),
            action=None,
        )
        second = _reconstructed_frame(
            turn=2,
            view=second_view,
            observed={"action": "approve"},
        )
        third = _reconstructed_frame(turn=3, view=third_view, observed=None)

        episode = author.episode_from_frames(
            id="receipt-board:case-0:reviewer",
            case_sha256=_CASE_SHA,
            role="reviewer",
            native_trial_id=_TRIAL,
            closed=True,
            frames=(frame for frame in (first, second, third)),
        )

        self.assertEqual(episode["schema"], episodes.EPISODE_SCHEMA)
        self.assertEqual([f["turn"] for f in episode["frames"]], [1, 2, 3])
        self.assertEqual(episode["frames"][0]["basis"], "bound")
        self.assertEqual(episode["frames"][0]["observed"], {"action": None})
        self.assertEqual(episode["frames"][1]["basis"], "reconstructed")
        self.assertEqual(episode["frames"][1]["observed"], {"action": "approve"})
        self.assertIsNone(episode["frames"][2]["observed"])

        payload = {k: v for k, v in episode.items() if k != "episode_sha256"}
        self.assertEqual(episode["episode_sha256"], tape.digest(payload))

        # Caller mutations to input frames after sealing cannot corrupt the sealed episode.
        cast(list[object], first["view"]["entries"]).append("mutated")
        assert first["observed"] is not None
        first["observed"]["action"] = "mutated"
        cast(list[object], second_view["entries"]).append("mutated")
        self.assertEqual(episode["frames"][0]["view"], {"step": 1, "entries": ["a"]})
        self.assertEqual(episode["frames"][0]["observed"], {"action": None})
        self.assertEqual(episode["frames"][1]["view"], {"step": 2, "entries": ["a", "b"]})

        # Sealed records and decisions round-trip cleanly via existing seatlab.tape.write_lines.
        with tempfile.TemporaryDirectory() as directory:
            episode_path = Path(directory) / "episodes.jsonl"
            tape.write_lines(episode_path, [episode])
            self.assertEqual(list(episodes.read_episodes(episode_path)), [episode])

            decision_path = Path(directory) / "decisions.jsonl"
            decision = author.decision_from_frame(
                key="receipt-board:case-0:turn-1",
                role="reviewer",
                native_trial_id=_TRIAL,
                frame=episode["frames"][0],
                answers=_answers(),
            )
            tape.write_lines(decision_path, [decision])
            self.assertEqual(list(tape.read_tape(decision_path)), [decision])

    def test_episode_from_frames_rejects_bad_order_mutations_and_invalid_headers(
        self,
    ) -> None:
        f1 = author.accepted_frame(
            turn=1,
            view={"turn": 1},
            acknowledged_sha256=tape.digest({"turn": 1}),
            action="approve",
        )
        f2 = author.accepted_frame(
            turn=2,
            view={"turn": 2},
            acknowledged_sha256=tape.digest({"turn": 2}),
            action=0,
        )

        # Out-of-order, skipped turn, or duplicate turn frames must fail.
        for bad_frames in ([], [f2, f1], [f2], [f1, f1], "not-frames", {"turn": 1}):
            with self.subTest(bad_frames=bad_frames):
                with self.assertRaises(tape.TapeError):
                    author.episode_from_frames(
                        id="receipt:case-0",
                        case_sha256=_CASE_SHA,
                        role="reviewer",
                        native_trial_id=_TRIAL,
                        closed=True,
                        frames=cast(Any, bad_frames),
                    )

        # Mutated frame view before sealing must fail digest validation.
        mutated = {**f1, "view": {"turn": 999}}
        with self.assertRaisesRegex(tape.TapeError, "view_sha256 does not match view"):
            author.episode_from_frames(
                id="receipt:case-0",
                case_sha256=_CASE_SHA,
                role="reviewer",
                native_trial_id=_TRIAL,
                closed=False,
                frames=[cast(episodes.EpisodeFrame, mutated)],
            )

        # Invalid root header fields fail via native wire validators.
        bad_headers: tuple[dict[str, Any], ...] = (
            {"id": "_invalid"},
            {"id": ""},
            {"case_sha256": _CASE_SHA.upper()},
            {"case_sha256": "short"},
            {"role": "1invalid"},
            {"native_trial_id": None},
            {"native_trial_id": _TRIAL.upper()},
            {"native_trial_id": "not-a-uuid"},
            {"closed": 1},
            {"closed": "true"},
        )
        for changes in bad_headers:
            with self.subTest(changes=changes):
                kwargs: dict[str, Any] = {
                    "id": "receipt:case-0",
                    "case_sha256": _CASE_SHA,
                    "role": "reviewer",
                    "native_trial_id": _TRIAL,
                    "closed": True,
                    "frames": [f1],
                    **changes,
                }
                with self.assertRaises(tape.TapeError):
                    author.episode_from_frames(**kwargs)


if __name__ == "__main__":
    unittest.main()
