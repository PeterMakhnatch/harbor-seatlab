import tempfile
import unittest
from pathlib import Path
from typing import cast

from seatlab import tape as wire
from seatlab.alignment import align
from seatlab.episodes import Episode, EpisodeFrame

_SOURCE = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_TARGET = "be656986-a740-4e46-999d-21410909878c"
_PAIR_SCHEMA = "seatlab.episode-pairs/v1"


def _frame(turn: int, view: dict[str, object], action: wire.Action = None) -> EpisodeFrame:
    return {
        "turn": turn,
        "view": view,
        "view_sha256": wire.digest(view),
        "basis": "bound",
        "observed": {"action": action},
    }


def _episode(trial: str, frames: list[EpisodeFrame], *, closed: bool = True) -> Episode:
    row: Episode = {
        "schema": "seatlab.episode/v1",
        "id": f"{trial}:receiver",
        "native_trial_id": trial,
        "role": "receiver",
        "case_sha256": wire.digest("room"),
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


def _row(report: dict[str, object]) -> dict[str, object]:
    return cast(list[dict[str, object]], report["pairs"])[0]


class AlignmentDetailsTests(unittest.TestCase):
    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)

    def _files(
        self, source: Episode | None, target: Episode | None
    ) -> tuple[Path, Path, Path]:
        root = Path(tempfile.mkdtemp(dir=self.root))
        left = root / "source.jsonl"
        right = root / "target.jsonl"
        manifest = root / "pairs.json"
        wire.write_lines(left, [] if source is None else [source])
        wire.write_lines(right, [] if target is None else [target])
        pair = {
            "id": "registered",
            "case_sha256": wire.digest("room"),
            "role": "receiver",
            "source": _receipt(source),
            "target": _receipt(target),
        }
        manifest.write_bytes(wire.canonical({"schema": _PAIR_SCHEMA, "pairs": [pair]}))
        return left, right, manifest

    def test_reports_only_paths_from_the_first_changed_input(self) -> None:
        first = _frame(1, {"prefix_only": "same"})
        left_view: dict[str, object] = {
            "queue": ["same", "left-value"],
            "board": {"work": "left-job"},
        }
        right_view: dict[str, object] = {
            "queue": ["same", "right-value"],
            "board": {"work": "right-job"},
        }
        left = _episode(
            _SOURCE,
            [first, _frame(2, left_view, "left-action"), _frame(3, {"later_only": 1})],
        )
        right = _episode(
            _TARGET,
            [first, _frame(2, right_view, "right-action"), _frame(3, {"later_only": 2})],
        )
        files = self._files(left, right)
        default = align(*files)
        self.assertEqual(wire.canonical(default), wire.canonical(align(*files, input_paths=None)))
        self.assertEqual(default["schema"], "seatlab.alignment/v1")
        self.assertNotIn("input_diff", _row(default))

        expected_paths = [
            {"path": "/board/work", "change": "value_changed"},
            {"path": "/queue/1", "change": "value_changed"},
        ]
        for limit in (1, 128):
            with self.subTest(limit=limit):
                detailed = align(*files, input_paths=limit)
                row = _row(detailed)
                self.assertEqual(row["status"], "input_changed")
                self.assertEqual(row["matched_prefix"], 1)
                self.assertEqual(row["boundary_turn"], 2)
                self.assertEqual(
                    row.pop("input_diff"),
                    {
                        "changes": 2,
                        "shown": min(limit, 2),
                        "truncated": limit == 1,
                        "paths": expected_paths[:limit],
                    },
                )
                self.assertEqual(detailed, default)

    def test_other_statuses_leave_the_report_unchanged(self) -> None:
        same = _frame(1, {"first": "same"})
        unbound = _frame(1, {"unbound_only": "secret"})
        unbound["basis"] = "reconstructed"
        unobserved = _frame(1, {"unobserved_only": "secret"})
        unobserved["observed"] = None
        different_action = _frame(1, same["view"], 1)
        later_left = _frame(2, {"later_only": 1})
        later_right = _frame(2, {"later_only": 2})
        for status, left_frames, right_frames in (
            ("missing", None, [unbound]),
            ("unbound", [same, later_left], [unbound, later_right]),
            ("missing_observation", [same, later_left], [unobserved, later_right]),
            ("action_changed", [same, later_left], [different_action, later_right]),
            ("length_changed", [same], [same, later_right]),
            ("open_prefix", [same], [same]),
            ("matched", [same], [same]),
        ):
            with self.subTest(status=status):
                left = None if left_frames is None else _episode(_SOURCE, left_frames)
                right = _episode(_TARGET, right_frames, closed=status != "open_prefix")
                files = self._files(left, right)
                detailed = align(*files, input_paths=2)
                self.assertEqual(_row(detailed)["status"], status)
                self.assertEqual(detailed, align(*files))

    def test_invalid_limit_fails_before_file_reads_even_without_rows(self) -> None:
        files = self._files(None, None)
        files[2].write_bytes(wire.canonical({"schema": _PAIR_SCHEMA, "pairs": []}))
        self.assertEqual(align(*files, input_paths=1)["pairs"], [])
        missing = self.root / "not-created"
        for paths in (files, (missing, missing, missing)):
            for limit in (0, -1, 129, False, True, 1.0, "1"):
                with self.subTest(manifest=paths[2], limit=limit):
                    with self.assertRaisesRegex(wire.TapeError, "input_paths"):
                        align(*paths, input_paths=cast(int, limit))

    def test_verifies_even_later_frames_before_returning_details(self) -> None:
        late = _frame(2, {"later": "before"})
        left = _episode(_SOURCE, [_frame(1, {"first": 1}), late])
        right = _episode(_TARGET, [_frame(1, {"first": 2})])
        late["view"]["later"] = "tampered"
        files = self._files(left, right)
        with self.assertRaisesRegex(wire.TapeError, "frame 2: view_sha256 does not match"):
            align(*files, input_paths=2)
