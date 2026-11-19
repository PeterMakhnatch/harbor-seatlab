import json
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from seatlab import author
from seatlab import study as study_module
from seatlab import tape as wire
from seatlab.replay import replay
from seatlab.study import StudyMismatch, seal_study
from seatlab.study_contract import RECIPE_SCHEMA
from seatlab.study_relations import export_relations
from seatlab.world_order import WORLD_ORDER_SCHEMA

_SOURCE_TRIAL = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_TARGET_TRIAL = "be656986-a740-4e46-999d-21410909878c"
_TEAM_CASE = wire.digest({"world": "kitchen", "case": "joint-01"})
_ANSWERS: wire.IntRangeAnswers = {
    "kind": "int-range",
    "min": 0,
    "max": 10,
    "include_null": True,
    "optimal": [1, 2],
    "feasible": [1, 2, 3],
}
_FIELDS = {
    "schema",
    "study_sha256",
    "report_sha256",
    "check_id",
    "kind",
    "pair_index",
    "case_sha256",
    "source_native_trial_id",
    "target_native_trial_id",
    "role",
    "status",
    "boundary_unit",
    "boundary",
    "matched_prefix",
    "native_action_changed",
    "reproduced",
}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wire.canonical(value) + b"\n")


def _decision(case: str, side: str, action: wire.Action = 1) -> wire.Decision:
    view: dict[str, object] = {"reports": {"left": [1, 2], "right": [1, 2]}}
    return wire.decision(
        {
            "schema": wire.DECISION_SCHEMA,
            "key": f"{case}:{side}",
            "role": "receiver",
            "native_trial_id": _SOURCE_TRIAL if side == "source" else _TARGET_TRIAL,
            "basis": "bound",
            "view": view,
            "view_sha256": wire.digest(view),
            "observed": {"action": action},
            "answers": _ANSWERS,
        }
    )


def _cal_receipt(case: str, side: str, row: wire.Decision | None) -> dict[str, object]:
    return {
        "key": row["key"] if row is not None else f"{case}:{side}",
        "case_sha256": wire.digest(case),
        "native_trial_id": row["native_trial_id"] if row is not None else None,
        "decision_sha256": wire.decision_sha256(row) if row is not None else None,
    }


def _episode(
    trial: str,
    role: str,
    actions: list[wire.Action],
    *,
    case_sha256: str,
    closed: bool = True,
) -> dict[str, object]:
    frames = []
    for turn, action in enumerate(actions, 1):
        view: dict[str, object] = {"role": role, "turn": turn}
        frames.append(
            author.accepted_frame(
                turn=turn,
                view=view,
                acknowledged_sha256=wire.digest(view),
                action=action,
            )
        )
    return cast(
        dict[str, object],
        author.episode_from_frames(
            id=f"{trial}:{role}",
            case_sha256=case_sha256,
            role=role,
            native_trial_id=trial,
            closed=closed,
            frames=frames,
        ),
    )


def _receipt(episode: dict[str, object]) -> dict[str, object]:
    return {
        "native_trial_id": episode["native_trial_id"],
        "episode_sha256": episode["episode_sha256"],
    }


def _order_doc(
    episodes: list[dict[str, object]], steps: list[list[str]]
) -> dict[str, object]:
    return {
        "schema": WORLD_ORDER_SCHEMA,
        "case_sha256": episodes[0]["case_sha256"],
        "native_trial_id": episodes[0]["native_trial_id"],
        "episodes": {
            cast(str, episode["role"]): episode["episode_sha256"] for episode in episodes
        },
        "steps": steps,
    }


class StudyRelationsTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _stage(self) -> tuple[Path, Path, str]:
        """Seal one bundle holding a calibration, an alignment and a team check."""
        bundle = Path(tempfile.mkdtemp(dir=self.root))
        ok_source, gap_source, ok_target = (
            _decision("a-ok", "source"),
            _decision("b-gap", "source"),
            _decision("a-ok", "target"),
        )
        wire.write_lines(bundle / "cal-sources.jsonl", [ok_source, gap_source])
        wire.write_lines(bundle / "cal-tape.jsonl", [ok_source, gap_source, ok_target])
        replay(bundle / "cal-sources.jsonl", bundle / "cal-preds.jsonl", "intersection")
        _write_json(
            bundle / "cal-pairs.json",
            {
                "schema": "seatlab.calibration/v1",
                "pairs": [
                    {
                        "id": "a-ok",
                        "source": _cal_receipt("a-ok", "source", ok_source),
                        "target": _cal_receipt("a-ok", "target", ok_target),
                    },
                    {
                        "id": "b-gap",
                        "source": _cal_receipt("b-gap", "source", gap_source),
                        "target": _cal_receipt("b-gap", "target", None),
                    },
                ],
            },
        )

        matched = _episode(
            _SOURCE_TRIAL, "receiver", [1], case_sha256=wire.digest("matched-case")
        )
        matched_target = _episode(
            _TARGET_TRIAL, "receiver", [1], case_sha256=wire.digest("matched-case")
        )
        wire.write_lines(bundle / "ep-source.jsonl", [matched])
        wire.write_lines(bundle / "ep-target.jsonl", [matched_target])
        _write_json(
            bundle / "ep-pairs.json",
            {
                "schema": "seatlab.episode-pairs/v1",
                "pairs": [
                    {
                        "id": "matched-pair",
                        "case_sha256": matched["case_sha256"],
                        "role": "receiver",
                        "source": _receipt(matched),
                        "target": _receipt(matched_target),
                    },
                    {
                        "id": "missing-pair",
                        "case_sha256": wire.digest("absent-case"),
                        "role": "receiver",
                        "source": None,
                        "target": None,
                    },
                ],
            },
        )

        (bundle / "team").mkdir()
        team_source = [
            _episode(_SOURCE_TRIAL, "chef0", ["north", "east"], case_sha256=_TEAM_CASE),
            _episode(_SOURCE_TRIAL, "chef1", ["south", "west"], case_sha256=_TEAM_CASE),
        ]
        team_target = [
            _episode(_TARGET_TRIAL, "chef0", ["north", "south"], case_sha256=_TEAM_CASE),
            _episode(_TARGET_TRIAL, "chef1", ["south", "west"], case_sha256=_TEAM_CASE),
        ]
        steps = [["chef0", "chef1"], ["chef0", "chef1"]]
        wire.write_lines(bundle / "team" / "source.jsonl", team_source)
        wire.write_lines(bundle / "team" / "target.jsonl", team_target)
        _write_json(bundle / "team" / "source-order.json", _order_doc(team_source, steps))
        _write_json(bundle / "team" / "target-order.json", _order_doc(team_target, steps))

        recipe = bundle / "recipe.json"
        manifest = bundle / "manifest.json"
        _write_json(
            recipe,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "cal",
                        "kind": "calibration",
                        "tape": "cal-tape.jsonl",
                        "predictions": "cal-preds.jsonl",
                        "pairs": "cal-pairs.json",
                    },
                    {
                        "id": "align",
                        "kind": "alignment",
                        "source": "ep-source.jsonl",
                        "target": "ep-target.jsonl",
                        "pairs": "ep-pairs.json",
                    },
                    {
                        "id": "team",
                        "kind": "team-alignment",
                        "source": "team/source.jsonl",
                        "target": "team/target.jsonl",
                        "source_order": "team/source-order.json",
                        "target_order": "team/target-order.json",
                    },
                ],
            },
        )
        sealed = seal_study(recipe, manifest)
        return bundle, manifest, wire.digest(sealed)

    def test_emits_one_row_per_comparison_without_raw_payload(self) -> None:
        bundle, manifest, study_sha = self._stage()
        out = bundle / "relations.jsonl"

        summary = export_relations(manifest, out, expect_sha256=study_sha)

        self.assertEqual(
            summary,
            {
                "schema": "seatlab.study-relations-summary/v1",
                "study_sha256": study_sha,
                "rows": 5,
                "checks": 3,
            },
        )
        text = out.read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertEqual(set(row), _FIELDS)
            self.assertEqual(row["schema"], "seatlab.study-relation/v1")
            self.assertEqual(row["study_sha256"], study_sha)
        self.assertNotIn('"view"', text)
        self.assertNotIn('"observed"', text)
        self.assertNotIn('"action":', text)
        self.assertNotIn('"score"', text)
        self.assertNotIn('"reports"', text)

        # Calibration: report order is sorted by pair id, so a-ok precedes b-gap.
        ok, gap = rows[0], rows[1]
        self.assertEqual(
            (ok["check_id"], ok["kind"], ok["pair_index"]),
            ("cal", "calibration", 1),
        )
        self.assertEqual(ok["case_sha256"], wire.digest("a-ok"))
        self.assertEqual(ok["source_native_trial_id"], _SOURCE_TRIAL)
        self.assertEqual(ok["target_native_trial_id"], _TARGET_TRIAL)
        self.assertEqual(ok["status"], "reproduced")
        self.assertIsNone(ok["role"])
        self.assertIsNone(ok["boundary_unit"])
        self.assertIsNone(ok["boundary"])
        self.assertIsNone(ok["matched_prefix"])
        self.assertIs(ok["native_action_changed"], False)
        self.assertIs(ok["reproduced"], True)

        self.assertEqual((gap["check_id"], gap["pair_index"]), ("cal", 2))
        self.assertEqual(gap["case_sha256"], wire.digest("b-gap"))
        self.assertEqual(gap["source_native_trial_id"], _SOURCE_TRIAL)
        self.assertIsNone(gap["target_native_trial_id"])
        self.assertEqual(gap["status"], "missing")
        self.assertIsNone(gap["native_action_changed"])
        self.assertIs(gap["reproduced"], False)

        # Alignment: registered pairs in report order, missing arms stay null.
        matched, missing = rows[2], rows[3]
        self.assertEqual(
            (matched["check_id"], matched["kind"], matched["pair_index"]),
            ("align", "alignment", 1),
        )
        self.assertEqual(matched["case_sha256"], wire.digest("matched-case"))
        self.assertEqual(matched["source_native_trial_id"], _SOURCE_TRIAL)
        self.assertEqual(matched["target_native_trial_id"], _TARGET_TRIAL)
        self.assertEqual(matched["role"], "receiver")
        self.assertEqual(matched["status"], "matched")
        self.assertEqual(matched["boundary_unit"], "role_turn")
        self.assertIsNone(matched["boundary"])
        self.assertEqual(matched["matched_prefix"], 1)
        self.assertIsNone(matched["native_action_changed"])
        self.assertIsNone(matched["reproduced"])

        self.assertEqual((missing["check_id"], missing["pair_index"]), ("align", 2))
        self.assertEqual(missing["case_sha256"], wire.digest("absent-case"))
        self.assertIsNone(missing["source_native_trial_id"])
        self.assertIsNone(missing["target_native_trial_id"])
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(missing["boundary_unit"], "role_turn")
        self.assertIsNone(missing["boundary"])
        # No compared prefix exists, so no invented zero is published.
        self.assertIsNone(missing["matched_prefix"])

        # Team alignment: one row per check, world steps, no role.
        team = rows[4]
        self.assertEqual(
            (team["check_id"], team["kind"], team["pair_index"]),
            ("team", "team-alignment", 1),
        )
        self.assertEqual(team["case_sha256"], _TEAM_CASE)
        self.assertEqual(team["source_native_trial_id"], _SOURCE_TRIAL)
        self.assertEqual(team["target_native_trial_id"], _TARGET_TRIAL)
        self.assertIsNone(team["role"])
        self.assertEqual(team["status"], "step_changed")
        self.assertEqual(team["boundary_unit"], "world_step")
        self.assertEqual(team["boundary"], 2)
        self.assertEqual(team["matched_prefix"], 1)
        self.assertIsNone(team["native_action_changed"])
        self.assertIsNone(team["reproduced"])

    def test_runs_each_check_once_across_verification_and_export(self) -> None:
        bundle, manifest, study_sha = self._stage()
        out = bundle / "relations-once.jsonl"
        with patch("seatlab.study._run_check", wraps=study_module._run_check) as spy:
            export_relations(manifest, out, expect_sha256=study_sha)
        self.assertEqual(spy.call_count, 3)

    def test_wrong_pin_and_existing_output_publish_nothing(self) -> None:
        bundle, manifest, study_sha = self._stage()
        out = bundle / "relations-pin.jsonl"

        with self.assertRaisesRegex(StudyMismatch, "expected pin"):
            export_relations(manifest, out, expect_sha256="0" * 64)
        self.assertFalse(out.exists())

        with self.assertRaises(wire.TapeError):
            export_relations(manifest, out, expect_sha256="NOT-A-HEX-DIGEST")
        self.assertFalse(out.exists())

        existing = bundle / "relations-existing.jsonl"
        existing.write_text("keep\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            export_relations(manifest, existing, expect_sha256=study_sha)
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep\n")

    def test_failure_on_last_check_publishes_nothing(self) -> None:
        bundle, manifest, _ = self._stage()
        tampered = json.loads(manifest.read_text(encoding="utf-8"))
        tampered["reports"]["team"] = "f" * 64
        tampered_path = bundle / "tampered-manifest.json"
        _write_json(tampered_path, tampered)
        out = bundle / "relations-tampered.jsonl"

        with self.assertRaisesRegex(StudyMismatch, "report digest"):
            export_relations(tampered_path, out, expect_sha256=wire.digest(tampered))
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
