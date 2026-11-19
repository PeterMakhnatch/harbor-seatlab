import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seatlab import tape
from seatlab.episodes import read_episodes

_TRIAL = "12345678-1234-4abc-9def-0123456789ab"


def _frame(turn: int = 1, **changes: object) -> dict:
    view = {"signal": "café", "extra": {"x": 0}}
    return {
        "turn": turn,
        "basis": "bound",
        "view": view,
        "view_sha256": tape.digest(view),
        "observed": {"action": None},
        **changes,
    }


def _seal(row: dict) -> dict:
    payload = {key: value for key, value in row.items() if key != "episode_sha256"}
    return {**payload, "episode_sha256": tape.digest(payload)}


def _episode(**changes: object) -> dict:
    return _seal(
        {
            "schema": "seatlab.episode/v1",
            "id": "calendar:run_1.first-0",
            "case_sha256": tape.digest({"synthetic": True}),
            "role": "Planner_1-A",
            "native_trial_id": _TRIAL,
            "closed": True,
            "frames": [_frame()],
            **changes,
        }
    )


class EpisodeReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "episodes.jsonl"

    def _read(self, *rows: object) -> list:
        self.path.write_bytes(b"".join(tape.canonical(row) + b"\n" for row in rows))
        return list(read_episodes(self.path))

    def test_ordered_roles_for_one_trial_and_optional_observations(self) -> None:
        first = _episode(
            closed=True,
            frames=[
                _frame(observed=None),
                _frame(2, basis="reconstructed", observed={"action": "free-form"}),
                _frame(3, observed={"action": -17}),
                _frame(4, observed={"action": None}),
            ],
        )
        second = _episode(id="second", role="Partner", closed=False)
        raw = json.dumps(first, ensure_ascii=False).encode("utf-8")
        self.assertNotEqual(raw, tape.canonical(first))
        self.path.write_bytes(raw + b"\r\n" + tape.canonical(second))
        self.assertEqual(list(read_episodes(self.path)), [first, second])
        self.assertEqual(self._read(), [])

    def test_identity_and_root_field_types_are_strict(self) -> None:
        cases = (
            ("id", ""),
            ("id", "_invalid"),
            ("id", "non-ascii-é"),
            ("id", "a" * 257),
            ("id", 1),
            ("role", "1Planner"),
            ("role", "Planner.1"),
            ("role", "Plánner"),
            ("role", "a" * 33),
            ("role", False),
            ("native_trial_id", None),
            ("native_trial_id", _TRIAL.upper()),
            ("native_trial_id", _TRIAL.replace("-", "")),
            ("native_trial_id", "not-a-uuid"),
            ("case_sha256", "A" * 64),
            ("case_sha256", "a" * 63),
            ("case_sha256", 123),
            ("episode_sha256", "A" * 64),
            ("episode_sha256", "a" * 63),
            ("schema", "seatlab.episode/v2"),
            ("schema", {}),
            ("closed", 1),
            ("closed", "true"),
            ("frames", []),
            ("frames", {}),
            ("frames", [None]),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                row = _episode(**{key: value})
                if key == "episode_sha256":
                    row[key] = value
                with self.assertRaisesRegex(tape.TapeError, r":1:"):
                    self._read(row)

    def test_exact_root_frame_and_observation_fields(self) -> None:
        for key in _episode():
            row = _episode()
            del row[key]
            if key != "episode_sha256":
                row = _seal(row)
            with self.subTest(missing_root=key):
                with self.assertRaisesRegex(tape.TapeError, "episode must be an object"):
                    self._read(row)
        for key in _frame():
            frame = _frame()
            del frame[key]
            with self.subTest(missing_frame=key):
                with self.assertRaisesRegex(tape.TapeError, "frame 1 must be an object"):
                    self._read(_episode(frames=[frame]))
        cases = (
            (_episode(extra=True), "episode must be an object"),
            (_episode(frames=[_frame(extra=True)]), "frame 1 must be an object"),
            (_episode(frames=[_frame(observed={})]), "observed must be an object"),
            (
                _episode(frames=[_frame(observed={"action": 4, "extra": False})]),
                "observed must be an object",
            ),
            (_episode(frames=[_frame(observed=3)]), "observed must be an object"),
            ([], "episode must be an object"),
        )
        for row, error in cases:
            with self.subTest(row=row):
                with self.assertRaisesRegex(tape.TapeError, error):
                    self._read(row)

    def test_strict_frame_and_action_types(self) -> None:
        cases = (
            ("turn", True),
            ("turn", 1.0),
            ("turn", 0),
            ("turn", "1"),
            ("basis", "design"),
            ("basis", ["bound"]),
            ("view", []),
            ("view_sha256", "A" * 64),
            ("observed", {"action": False}),
            ("observed", {"action": True}),
            ("observed", {"action": 1.0}),
            ("observed", {"action": []}),
            ("observed", {"action": {}}),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                frame = {**_frame(), key: value}
                with self.assertRaisesRegex(tape.TapeError, r":1:"):
                    self._read(_episode(frames=[frame]))

    def test_duplicate_json_keys_are_rejected_at_any_depth(self) -> None:
        line = tape.canonical(_episode())
        for key in (b'"schema":', b'"basis":', b'"action":', b'"x":'):
            with self.subTest(key=key):
                self.path.write_bytes(line.replace(key, key + b"null," + key))
                with self.assertRaisesRegex(tape.TapeError, r":1: .*duplicate key"):
                    list(read_episodes(self.path))

    def test_view_and_episode_hashes_and_frame_order_are_independent(self) -> None:
        first = _frame(1)
        second = _frame(2, observed={"action": "choose:2"})
        row = _episode(frames=[first, second])
        changed_view = {"signal": "tampered"}
        changed = {**first, "view": changed_view}
        with self.assertRaisesRegex(tape.TapeError, "view_sha256 does not match view"):
            self._read(_seal({**row, "frames": [changed, second]}))
        changed["view_sha256"] = tape.digest(changed_view)
        with self.assertRaisesRegex(tape.TapeError, "episode_sha256 does not match episode"):
            self._read({**row, "frames": [changed, second]})
        with self.assertRaisesRegex(tape.TapeError, "turn must be a consecutive"):
            self._read(_seal({**row, "frames": [second, first]}))
        reordered = {**row, "frames": [{**second, "turn": 1}, {**first, "turn": 2}]}
        with self.assertRaisesRegex(tape.TapeError, "episode_sha256 does not match episode"):
            self._read(reordered)
        sealed = _seal(reordered)
        self.assertEqual(self._read(sealed), [sealed])

    def test_duplicate_episode_and_native_seat_have_line_errors(self) -> None:
        first = _episode()
        with self.assertRaisesRegex(tape.TapeError, r":2: duplicate episode id"):
            self._read(first, _episode(role="Partner"))
        with self.assertRaisesRegex(
            tape.TapeError, r":2: duplicate native_trial_id and role"
        ):
            self._read(first, _episode(id="another-id"))

    def test_binary_line_limit_and_framing(self) -> None:
        row = _episode()
        line = tape.canonical(row)
        with patch("seatlab.tape.MAX_LINE_BYTES", len(line)):
            for framing in (b"\r\n", b"\n", b""):
                self.path.write_bytes(line + framing)
                self.assertEqual(list(read_episodes(self.path)), [row])
            for extra in (b" \n", b" \r\n", b" ", b" " * (10 * len(line)) + b"\n"):
                self.path.write_bytes(line + extra)
                with self.assertRaisesRegex(tape.TapeError, r":1: JSON line exceeds"):
                    list(read_episodes(self.path))
        for malformed in (b"\n", b"\xff\n", b"{\n"):
            self.path.write_bytes(line + b"\n" + malformed)
            with self.assertRaisesRegex(tape.TapeError, r":2: invalid UTF-8 JSON"):
                list(read_episodes(self.path))
