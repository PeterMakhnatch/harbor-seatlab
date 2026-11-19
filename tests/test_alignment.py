import tempfile
import unittest
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, cast

from seatlab import tape as wire
from seatlab.alignment import align
from seatlab.episodes import Episode, EpisodeFrame

_SOURCE = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_TARGET = "be656986-a740-4e46-999d-21410909878c"
_OTHER = "fe8487a3-882b-4697-baa0-248e8fed964c"
_SCHEMA = "seatlab.episode-pairs/v1"


def _frame(
    turn: int,
    action: wire.Action = None,
    *,
    slots: tuple[int, ...] = (1, 2),
    observed: bool = True,
    basis: Literal["bound", "reconstructed"] = "bound",
) -> EpisodeFrame:
    view: dict[str, object] = {
        "round": turn,
        "private_available_slots": list(slots),
        "slot_utility": {str(slot): 1 for slot in slots},
    }
    return {
        "turn": turn,
        "view": view,
        "view_sha256": wire.digest(view),
        "basis": basis,
        "observed": {"action": action} if observed else None,
    }


def _episode(
    trial: str,
    frames: list[EpisodeFrame],
    *,
    role: str = "receiver",
    case: str = "room",
    closed: bool = True,
) -> Episode:
    row: Episode = {
        "schema": "seatlab.episode/v1",
        "id": f"{trial}:{role}",
        "native_trial_id": trial,
        "role": role,
        "case_sha256": wire.digest(case),
        "closed": closed,
        "frames": frames,
        "episode_sha256": "",
    }
    row["episode_sha256"] = wire.digest(
        {key: value for key, value in row.items() if key != "episode_sha256"}
    )
    return row


def _receipt(episode: Episode | None) -> dict[str, str] | None:
    if episode is None:
        return None
    return {
        "native_trial_id": episode["native_trial_id"],
        "episode_sha256": episode["episode_sha256"],
    }


def _pair(
    pair_id: str,
    source: Episode | None = None,
    target: Episode | None = None,
    *,
    role: str = "receiver",
    case: str = "room",
) -> dict[str, object]:
    return {
        "id": pair_id,
        "case_sha256": wire.digest(case),
        "role": role,
        "source": _receipt(source),
        "target": _receipt(target),
    }


def _rows(report: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], report["pairs"])


class AlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)

    def _files(
        self,
        pairs: list[dict[str, object]],
        source: Iterable[Episode] = (),
        target: Iterable[Episode] = (),
    ) -> tuple[Path, Path, Path]:
        root = Path(tempfile.mkdtemp(dir=self.root))
        left, right, manifest = (
            root / "source.jsonl",
            root / "target.jsonl",
            root / "pairs.json",
        )
        wire.write_lines(left, source)
        wire.write_lines(right, target)
        manifest.write_bytes(wire.canonical({"schema": _SCHEMA, "pairs": pairs}))
        return left, right, manifest

    def _compare(self, source: Episode, target: Episode) -> dict[str, object]:
        pair = _pair("registered", source, target)
        return _rows(align(*self._files([pair], [source], [target])))[0]

    def test_counts_retain_all_registered_pairs_in_id_order(self) -> None:
        left = _episode(_SOURCE, [_frame(1), _frame(2, 2)])
        right = _episode(_TARGET, [_frame(1), _frame(2, 2)])
        lone = _episode(_OTHER, [_frame(1)], case="other")
        unknown = _pair("03-unavailable", case="unknown")
        one_arm = _pair("02-source-only", lone, case="other")
        closed = _pair("01-match", left, right)
        report = align(*self._files([unknown, one_arm, closed], [lone, left], [right]))
        self.assertEqual(set(report), {"schema", "counts", "pairs"})
        self.assertEqual(report["schema"], "seatlab.alignment/v1")
        self.assertEqual(
            report["counts"],
            {
                "pairs": 3,
                "matched_prefix": 2,
                "missing": 2,
                "unbound": 0,
                "missing_observation": 0,
                "input_changed": 0,
                "action_changed": 0,
                "length_changed": 0,
                "open_prefix": 0,
                "matched": 1,
            },
        )
        self.assertEqual(
            _rows(report),
            [
                {
                    **pair,
                    "status": status,
                    "matched_prefix": prefix,
                    "boundary_turn": None,
                    "source_action": None,
                    "target_action": None,
                    "source_view_sha256": None,
                    "target_view_sha256": None,
                }
                for pair, status, prefix in (
                    (closed, "matched", 2),
                    (one_arm, "missing", 0),
                    (unknown, "missing", 0),
                )
            ],
        )

    def test_equal_utility_does_not_hide_first_exact_action_change(self) -> None:
        # The invented view offers slots 1 and 2 with equal utility.
        for a, b in ((1, 2), (1, "1"), (None, 1)):
            with self.subTest(source_action=a, target_action=b):
                left = _episode(_SOURCE, [_frame(1), _frame(2, a), _frame(3, "later-left")])
                right = _episode(_TARGET, [_frame(1), _frame(2, b), _frame(3, "later-right")])
                row = self._compare(left, right)
                self.assertEqual(row["status"], "action_changed")
                self.assertEqual(row["matched_prefix"], 1)
                self.assertEqual(row["boundary_turn"], 2)
                self.assertEqual(row["source_action"], {"action": a})
                self.assertEqual(row["target_action"], {"action": b})
                self.assertEqual(row["source_view_sha256"], left["frames"][1]["view_sha256"])
                self.assertEqual(row["source_view_sha256"], row["target_view_sha256"])
                self.assertNotIn(b"later-", wire.canonical(row))

    def test_first_changed_view_takes_precedence_over_action_differences(self) -> None:
        left = _episode(_SOURCE, [_frame(1), _frame(2, 1, slots=(1,)), _frame(3, "later")])
        right = _episode(_TARGET, [_frame(1), _frame(2, 2, slots=(2,)), _frame(3, 2)])
        row = self._compare(left, right)
        self.assertEqual(row["status"], "input_changed")
        self.assertEqual(row["matched_prefix"], 1)
        self.assertEqual(row["boundary_turn"], 2)
        self.assertEqual(row["source_view_sha256"], left["frames"][1]["view_sha256"])
        self.assertEqual(row["target_view_sha256"], right["frames"][1]["view_sha256"])
        self.assertNotEqual(row["source_view_sha256"], row["target_view_sha256"])
        self.assertEqual(row["source_action"], {"action": 1})
        self.assertEqual(row["target_action"], {"action": 2})
        self.assertNotIn(b"private_available_slots", wire.canonical(row))
        self.assertNotIn(b"later", wire.canonical(row))

    def test_missing_observation_and_unbound_frame_do_not_compare_actions(self) -> None:
        for frame, status in (
            (_frame(2, observed=False, slots=(2,)), "missing_observation"),
            (_frame(2, 2, basis="reconstructed"), "unbound"),
        ):
            with self.subTest(status=status):
                left = _episode(_SOURCE, [_frame(1, 1), _frame(2)])
                right = _episode(_TARGET, [_frame(1, 1), frame, _frame(3, "later")])
                row = self._compare(left, right)
                self.assertEqual(row["status"], status)
                self.assertEqual(row["matched_prefix"], 1)
                self.assertEqual(row["boundary_turn"], 2)
                self.assertIsNone(row["source_action"])
                self.assertIsNone(row["target_action"])

    def test_open_prefix_and_length_change_are_not_action_divergences(self) -> None:
        for left_size, right_size, status, turn in (
            (1, 1, "open_prefix", None),
            (1, 2, "length_changed", 2),
            (2, 1, "length_changed", 2),
        ):
            with self.subTest(left_size=left_size, right_size=right_size):
                frames = [_frame(1, 1), _frame(2, "unpaired")]
                left = _episode(_SOURCE, frames[:left_size], closed=False)
                right = _episode(_TARGET, frames[:right_size])
                row = self._compare(left, right)
                self.assertEqual(row["status"], status)
                self.assertEqual(row["matched_prefix"], 1)
                self.assertEqual(row["boundary_turn"], turn)
                self.assertIsNone(row["source_action"])
                self.assertIsNone(row["target_action"])
                self.assertIsNone(row["source_view_sha256"])
                self.assertIsNone(row["target_view_sha256"])

    def test_same_native_trial_can_supply_distinct_roles_on_each_arm(self) -> None:
        left = [
            _episode(_SOURCE, [_frame(1, 2)], role=role) for role in ("receiver", "sender")
        ]
        right = [
            _episode(_TARGET, [_frame(1, 2)], role=role) for role in ("receiver", "sender")
        ]
        pairs = [
            _pair(a["role"], a, b, role=a["role"]) for a, b in zip(left, right, strict=True)
        ]
        rows = _rows(align(*self._files(pairs, left, reversed(right))))
        self.assertEqual(
            [(row["id"], row["status"]) for row in rows],
            [("receiver", "matched"), ("sender", "matched")],
        )

    def test_requires_strict_manifest_shape_and_canonical_identities(self) -> None:
        pair = _pair("registered")
        receipt = {"native_trial_id": _SOURCE, "episode_sha256": "0" * 64}
        for invalid, error in (
            ({**pair, "extra": 1}, "exactly"),
            ({**pair, "id": "bad label"}, "pair id"),
            ({**pair, "role": "2receiver"}, "role"),
            ({**pair, "case_sha256": "A" * 64}, "case_sha256"),
            ({**pair, "source": {}}, "exactly"),
            ({**pair, "source": {**receipt, "native_trial_id": _SOURCE.upper()}}, "UUID"),
            ({**pair, "source": {**receipt, "episode_sha256": "A" * 64}}, "episode_sha256"),
        ):
            with self.subTest(error=error, invalid=invalid):
                with self.assertRaisesRegex(wire.TapeError, error):
                    align(*self._files([invalid]))
        paths = self._files([])
        paths[2].write_bytes(b'{"schema":"seatlab.episode-pairs/v1","pairs":[],"pairs":[]}')
        with self.assertRaises(wire.TapeError):
            align(*paths)

    def test_rejects_duplicate_pair_or_trial_role_registrations(self) -> None:
        a = _episode(_SOURCE, [_frame(1)])
        for pairs, error in (
            ([_pair("same"), _pair("same", case="other")], "duplicate episode pair id"),
            ([_pair("one"), _pair("two")], "duplicate case_sha256 and role"),
            ([_pair("both", a, a)], "same native_trial_id"),
            (
                [_pair("one", source=a), _pair("two", source=a, case="other")],
                "duplicate source native_trial_id and role",
            ),
            (
                [_pair("one", target=a), _pair("two", target=a, case="other")],
                "duplicate target native_trial_id and role",
            ),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(wire.TapeError, error):
                    align(*self._files(pairs))

    def test_requires_matching_case_episode_hash_and_role(self) -> None:
        left = _episode(_SOURCE, [_frame(1)])
        right = _episode(_TARGET, [_frame(1)])
        pair = _pair("registered", left, right)
        bad_hash = {"native_trial_id": _SOURCE, "episode_sha256": "0" * 64}
        for invalid, error in (
            ({**pair, "case_sha256": wire.digest("wrong")}, "case_sha256 does not match"),
            ({**pair, "source": bad_hash}, "episode_sha256 does not match"),
            ({**pair, "role": "other"}, "source coverage mismatch"),
            ({**pair, "source": {**bad_hash, "native_trial_id": _OTHER}}, "source coverage"),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(wire.TapeError, error):
                    align(*self._files([invalid], [left], [right]))

    def test_tape_coverage_must_equal_each_arms_registered_receipts(self) -> None:
        left = _episode(_SOURCE, [_frame(1)])
        right = _episode(_TARGET, [_frame(1)])
        extra = _episode(_OTHER, [_frame(1)], case="not-registered")
        pair = _pair("registered", left, right)
        for pairs, source, target, error in (
            ([pair], [], [right], "source coverage mismatch: 1 missing, 0 unexpected"),
            ([pair], [left], [], "target coverage mismatch: 1 missing, 0 unexpected"),
            (
                [pair],
                [left, extra],
                [right],
                "source coverage mismatch: 0 missing, 1 unexpected",
            ),
            (
                [_pair("registered", left)],
                [left],
                [right],
                "target coverage mismatch: 0 missing, 1 unexpected",
            ),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(wire.TapeError, error):
                    align(*self._files(pairs, source, target))
