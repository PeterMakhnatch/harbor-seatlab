import tempfile
import unittest
from pathlib import Path
from typing import Any

from seatlab import author, tape as wire
from seatlab.episodes import Episode
from seatlab.world_order import WORLD_ORDER_SCHEMA, WorldOrder, read_world_order

_TRIAL_A = "12345678-1234-4abc-9def-0123456789ab"
_TRIAL_B = "87654321-4321-4abc-9def-ba9876543210"
_CASE_A = wire.digest({"world": "grid", "case": "alpha"})
_CASE_B = wire.digest({"world": "grid", "case": "beta"})


def _episode(
    role: str,
    turns: int,
    *,
    trial: str = _TRIAL_A,
    case_sha256: str = _CASE_A,
    closed: bool = True,
    episode_id: str | None = None,
) -> Episode:
    frames = []
    for turn in range(1, turns + 1):
        view: dict[str, object] = {"role": role, "turn": turn}
        frames.append(
            author.accepted_frame(
                turn=turn,
                view=view,
                acknowledged_sha256=wire.digest(view),
                action=f"act-{turn}",
            )
        )
    return author.episode_from_frames(
        id=episode_id or f"{trial}:{role}",
        case_sha256=case_sha256,
        role=role,
        native_trial_id=trial,
        closed=closed,
        frames=frames,
    )


def _order_doc(
    episodes: list[Episode],
    steps: list[list[str]],
    *,
    case_sha256: str = _CASE_A,
    native_trial_id: str = _TRIAL_A,
    **overrides: object,
) -> dict[str, object]:
    doc: dict[str, object] = {
        "schema": WORLD_ORDER_SCHEMA,
        "case_sha256": case_sha256,
        "native_trial_id": native_trial_id,
        "episodes": {ep["role"]: ep["episode_sha256"] for ep in episodes},
        "steps": steps,
    }
    doc.update(overrides)
    return doc


class WorldOrderReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)

    def _write_case(
        self,
        episodes: list[Episode],
        order: object,
        *,
        raw_order: bytes | None = None,
    ) -> tuple[Path, Path]:
        folder = Path(tempfile.mkdtemp(dir=self.root))
        tape_path = folder / "episodes.jsonl"
        order_path = folder / "order.json"
        wire.write_lines(tape_path, episodes)
        order_path.write_bytes(raw_order if raw_order is not None else wire.canonical(order))
        return tape_path, order_path

    def test_valid_order_sorts_groups_permits_repeated_role_across_steps_and_closed(
        self,
    ) -> None:
        alice = _episode("alice", 3, closed=True)
        bob = _episode("bob", 2, closed=True)
        charlie = _episode("charlie", 1, closed=True)
        steps_one = [["bob", "alice"], ["alice"], ["charlie", "alice", "bob"]]
        steps_two = [["alice", "bob"], ["alice"], ["bob", "charlie", "alice"]]

        first = read_world_order(
            *self._write_case(
                [charlie, bob, alice], _order_doc([alice, bob, charlie], steps_one)
            )
        )
        second = read_world_order(
            *self._write_case(
                [alice, charlie, bob], _order_doc([charlie, alice, bob], steps_two)
            )
        )

        self.assertIsInstance(first, WorldOrder)
        self.assertEqual(first, second)
        self.assertEqual(first.case_sha256, _CASE_A)
        self.assertEqual(first.native_trial_id, _TRIAL_A)
        self.assertEqual(list(first.episodes), ["alice", "bob", "charlie"])
        self.assertEqual(
            first.steps,
            (("alice", "bob"), ("alice",), ("alice", "bob", "charlie")),
        )
        self.assertTrue(first.closed)

        open_bob = _episode("bob", 2, closed=False)
        open_order = read_world_order(
            *self._write_case(
                [alice, open_bob],
                _order_doc(
                    [alice, open_bob], [["bob", "alice"], ["alice"], ["bob", "alice"]]
                ),
            )
        )
        self.assertFalse(open_order.closed)

    def test_rejects_missing_or_extra_frames(self) -> None:
        alice = _episode("alice", 2)
        bob = _episode("bob", 1)

        with self.subTest("missing_frame_consumption"):
            doc = _order_doc([alice, bob], [["alice", "bob"]])
            with self.assertRaisesRegex(wire.TapeError, "frame count mismatch"):
                read_world_order(*self._write_case([alice, bob], doc))

        with self.subTest("extra_frame_consumption"):
            doc = _order_doc([alice, bob], [["alice", "bob"], ["alice"], ["alice"]])
            with self.assertRaisesRegex(wire.TapeError, "frame count mismatch"):
                read_world_order(*self._write_case([alice, bob], doc))

        with self.subTest("declared_role_never_scheduled"):
            doc = _order_doc([alice, bob], [["alice"], ["alice"]])
            with self.assertRaisesRegex(wire.TapeError, "frame count mismatch"):
                read_world_order(*self._write_case([alice, bob], doc))

    def test_rejects_duplicate_role_within_one_step(self) -> None:
        alice = _episode("alice", 2)
        bob = _episode("bob", 1)
        doc = _order_doc([alice, bob], [["alice", "bob", "alice"]])
        with self.assertRaisesRegex(wire.TapeError, "repeats role"):
            read_world_order(*self._write_case([alice, bob], doc))

    def test_rejects_undeclared_missing_and_extra_roles(self) -> None:
        alice = _episode("alice", 1)
        bob = _episode("bob", 1)

        with self.subTest("undeclared_role_in_steps"):
            doc = _order_doc([alice], [["alice", "bob"]])
            with self.assertRaisesRegex(wire.TapeError, "undeclared role"):
                read_world_order(*self._write_case([alice], doc))

        with self.subTest("extra_episode_in_tape"):
            doc = _order_doc([alice], [["alice"]])
            with self.assertRaisesRegex(wire.TapeError, "unexpected role"):
                read_world_order(*self._write_case([alice, bob], doc))

        with self.subTest("missing_episode_in_tape"):
            doc = _order_doc([alice, bob], [["alice", "bob"]])
            with self.assertRaisesRegex(wire.TapeError, "missing episode"):
                read_world_order(*self._write_case([alice], doc))

        with self.subTest("empty_tape"):
            doc = _order_doc([alice], [["alice"]])
            with self.assertRaisesRegex(wire.TapeError, "missing episode"):
                read_world_order(*self._write_case([], doc))

    def test_rejects_cross_run_and_digest_mismatches(self) -> None:
        alice = _episode("alice", 1)
        bob = _episode("bob", 1)
        other_trial_bob = _episode("bob", 1, trial=_TRIAL_B)
        other_case_bob = _episode("bob", 1, case_sha256=_CASE_B)
        changed_alice = _episode("alice", 1, episode_id="alt:alice")

        with self.subTest("cross_run_trial_in_tape"):
            doc = _order_doc([alice, other_trial_bob], [["alice", "bob"]])
            with self.assertRaisesRegex(wire.TapeError, "native_trial_id"):
                read_world_order(*self._write_case([alice, other_trial_bob], doc))

        with self.subTest("order_trial_differs_from_tape"):
            doc = _order_doc([alice], [["alice"]], native_trial_id=_TRIAL_B)
            with self.assertRaisesRegex(wire.TapeError, "native_trial_id"):
                read_world_order(*self._write_case([alice], doc))

        with self.subTest("case_sha256_mismatch"):
            doc = _order_doc([alice, other_case_bob], [["alice", "bob"]])
            with self.assertRaisesRegex(wire.TapeError, "case_sha256"):
                read_world_order(*self._write_case([alice, other_case_bob], doc))

        with self.subTest("episode_sha256_mismatch"):
            doc = _order_doc([changed_alice, bob], [["alice", "bob"]])
            with self.assertRaisesRegex(wire.TapeError, "episode_sha256"):
                read_world_order(*self._write_case([alice, bob], doc))

    def test_rejects_noncanonical_schema_fields_ids_digests_and_duplicate_keys(self) -> None:
        alice = _episode("alice", 1)
        base = _order_doc([alice], [["alice"]])

        for missing_field in list(base):
            broken = dict(base)
            del broken[missing_field]
            with self.subTest(missing_field=missing_field):
                with self.assertRaises(wire.TapeError):
                    read_world_order(*self._write_case([alice], broken))

        bad_docs: list[tuple[str, Any]] = [
            ("not_object", []),
            ("extra_field", {**base, "extra": 1}),
            ("bad_schema", {**base, "schema": "seatlab.world-order/v2"}),
            ("uppercase_case_sha", {**base, "case_sha256": _CASE_A.upper()}),
            ("short_case_sha", {**base, "case_sha256": "a" * 63}),
            ("uppercase_uuid", {**base, "native_trial_id": _TRIAL_A.upper()}),
            ("compact_uuid", {**base, "native_trial_id": _TRIAL_A.replace("-", "")}),
            ("null_uuid", {**base, "native_trial_id": None}),
            ("empty_episodes", {**base, "episodes": {}}),
            ("episodes_not_object", {**base, "episodes": ["alice"]}),
            (
                "bad_role_in_episodes",
                {**base, "episodes": {"1bad": alice["episode_sha256"]}},
            ),
            (
                "uppercase_episode_sha",
                {**base, "episodes": {"alice": alice["episode_sha256"].upper()}},
            ),
            ("empty_steps", {**base, "steps": []}),
            ("steps_not_array", {**base, "steps": {}}),
            ("empty_step_group", {**base, "steps": [[]]}),
            ("step_group_not_array", {**base, "steps": ["alice"]}),
            ("non_string_role_in_step", {**base, "steps": [[1]]}),
            ("bad_role_in_step", {**base, "steps": [["bad.role"]]}),
        ]
        for label, doc in bad_docs:
            with self.subTest(case=label):
                with self.assertRaises(wire.TapeError):
                    read_world_order(*self._write_case([alice], doc))

        with self.subTest("duplicate_json_key_root"):
            raw = (
                f'{{"schema":"{WORLD_ORDER_SCHEMA}","schema":"{WORLD_ORDER_SCHEMA}",'
                f'"case_sha256":"{_CASE_A}","native_trial_id":"{_TRIAL_A}",'
                f'"episodes":{{"alice":"{alice["episode_sha256"]}"}},"steps":[["alice"]]}}'
            ).encode("utf-8")
            with self.assertRaisesRegex(wire.TapeError, "duplicate key"):
                read_world_order(*self._write_case([alice], None, raw_order=raw))

        with self.subTest("duplicate_json_key_episodes"):
            digest = alice["episode_sha256"]
            raw = (
                f'{{"schema":"{WORLD_ORDER_SCHEMA}","case_sha256":"{_CASE_A}",'
                f'"native_trial_id":"{_TRIAL_A}",'
                f'"episodes":{{"alice":"{digest}","alice":"{digest}"}},"steps":[["alice"]]}}'
            ).encode("utf-8")
            with self.assertRaisesRegex(wire.TapeError, "duplicate key"):
                read_world_order(*self._write_case([alice], None, raw_order=raw))
