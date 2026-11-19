import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from seatlab import tape as wire
from seatlab.alignment import align
from seatlab.calibration import calibrate
from seatlab.context import ContextSpec
from seatlab.episodes import Episode, EpisodeFrame
from seatlab.replay import replay
from seatlab.study import StudyMismatch, seal_study, verify_study
from seatlab.study_contract import MANIFEST_SCHEMA, RECIPE_SCHEMA

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
_SOURCE_TRIAL = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_TARGET_TRIAL = "be656986-a740-4e46-999d-21410909878c"
_ALT_SOURCE_TRIAL = "fe8487a3-882b-4697-baa0-248e8fed964c"
_ALT_TARGET_TRIAL = "3b713757-9cb6-46ea-8b42-9c7d94a01f24"
_ANSWERS: wire.IntRangeAnswers = {
    "kind": "int-range",
    "min": 0,
    "max": 10,
    "include_null": True,
    "optimal": [1, 2],
    "feasible": [1, 2, 3],
}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wire.canonical(value) + b"\n")


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decision(
    case: str,
    side: str,
    action: wire.Action = 1,
    *,
    view: dict[str, object] | None = None,
    basis: wire.Basis = "bound",
    observed: bool = True,
) -> wire.Decision:
    if view is None:
        view = {"reports": {"left": [1, 2], "right": [1, 2]}}
    return wire.decision(
        {
            "schema": wire.DECISION_SCHEMA,
            "key": f"{case}:{side}",
            "role": "receiver",
            "native_trial_id": _SOURCE_TRIAL if side == "source" else _TARGET_TRIAL,
            "basis": basis,
            "view": view,
            "view_sha256": wire.digest(view),
            "observed": {"action": action} if observed else None,
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


def _episode_frame(
    turn: int,
    view: dict[str, object],
    action: wire.Action = None,
    *,
    basis: str = "bound",
    observed: bool = True,
) -> EpisodeFrame:
    return cast(
        EpisodeFrame,
        {
            "turn": turn,
            "view": view,
            "view_sha256": wire.digest(view),
            "basis": basis,
            "observed": {"action": action} if observed else None,
        },
    )


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


class StudyTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _stage_examples_bundle(self) -> Path:
        bundle = Path(tempfile.mkdtemp(dir=self.root))
        for name in (
            "meeting.jsonl",
            "meeting-sources.jsonl",
            "meeting-pairs.json",
            "meeting-first8.json",
            "cursor-source.jsonl",
            "cursor-target.jsonl",
            "cursor-pairs.json",
        ):
            shutil.copy2(_EXAMPLES / name, bundle / name)

        spec = ContextSpec.load(bundle / "meeting-first8.json")
        replay(
            bundle / "meeting-sources.jsonl",
            bundle / "meeting-first8-pred.jsonl",
            "intersection",
            transform=spec,
        )
        replay(
            bundle / "meeting-sources.jsonl",
            bundle / "meeting-unchanged-pred.jsonl",
            "intersection",
            transform="identity",
        )
        return bundle

    def test_seal_and_verify_repository_examples_with_positive_and_negative_controls(
        self,
    ) -> None:
        bundle = self._stage_examples_bundle()
        # Extra unreferenced file must not enter the manifest or affect verification.
        extra_note = bundle / "notes.txt"
        extra_note.write_text("unrelated file", encoding="utf-8")

        recipe_path = bundle / "study-recipe.json"
        manifest_path = bundle / "study.json"
        recipe = {
            "schema": RECIPE_SCHEMA,
            "checks": [
                {
                    "id": "meeting-first8-verified",
                    "kind": "calibration",
                    "tape": "meeting.jsonl",
                    "predictions": "meeting-first8-pred.jsonl",
                    "pairs": "meeting-pairs.json",
                    "context": "meeting-first8.json",
                },
                {
                    "id": "meeting-unchanged-control",
                    "kind": "calibration",
                    "tape": "meeting.jsonl",
                    "predictions": "meeting-unchanged-pred.jsonl",
                    "pairs": "meeting-pairs.json",
                },
                {
                    "id": "meeting-first8-withheld-context",
                    "kind": "calibration",
                    "tape": "meeting.jsonl",
                    "predictions": "meeting-first8-pred.jsonl",
                    "pairs": "meeting-pairs.json",
                },
                {
                    "id": "cursor-budget-align",
                    "kind": "alignment",
                    "source": "cursor-source.jsonl",
                    "target": "cursor-target.jsonl",
                    "pairs": "cursor-pairs.json",
                    "input_paths": 8,
                },
            ],
        }
        _write_json(recipe_path, recipe)

        manifest = seal_study(recipe_path, manifest_path)
        self.assertEqual(manifest["schema"], MANIFEST_SCHEMA)
        self.assertTrue(manifest_path.is_file())
        self.assertEqual(wire.decode(manifest_path.read_bytes()), manifest)

        files = cast(dict[str, dict[str, object]], manifest["files"])
        self.assertNotIn("notes.txt", files)
        self.assertEqual(
            sorted(files),
            [
                "cursor-pairs.json",
                "cursor-source.jsonl",
                "cursor-target.jsonl",
                "meeting-first8-pred.jsonl",
                "meeting-first8.json",
                "meeting-pairs.json",
                "meeting-unchanged-pred.jsonl",
                "meeting.jsonl",
            ],
        )
        for rel_path, receipt in files.items():
            disk_path = bundle / rel_path
            self.assertEqual(receipt["bytes"], disk_path.stat().st_size)
            self.assertEqual(receipt["sha256"], _file_digest(disk_path))

        # Mutating an unreferenced file does not affect verification.
        extra_note.write_text("changed unrelated file", encoding="utf-8")

        study_sha = wire.digest(manifest)
        verified = verify_study(manifest_path, expect_sha256=study_sha)
        self.assertEqual(verified["schema"], "seatlab.study-verification/v1")
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["study_sha256"], study_sha)
        self.assertEqual(verified["files"], len(files))
        self.assertEqual(
            verified["bytes"],
            sum(cast(int, receipt["bytes"]) for receipt in files.values()),
        )

        # Verify order preservation and exact report digests against direct analyzer calls.
        expected_first8 = calibrate(
            bundle / "meeting.jsonl",
            bundle / "meeting-first8-pred.jsonl",
            bundle / "meeting-pairs.json",
            context=ContextSpec.load(bundle / "meeting-first8.json"),
        )
        expected_unchanged = calibrate(
            bundle / "meeting.jsonl",
            bundle / "meeting-unchanged-pred.jsonl",
            bundle / "meeting-pairs.json",
        )
        expected_withheld = calibrate(
            bundle / "meeting.jsonl",
            bundle / "meeting-first8-pred.jsonl",
            bundle / "meeting-pairs.json",
        )
        expected_align = align(
            bundle / "cursor-source.jsonl",
            bundle / "cursor-target.jsonl",
            bundle / "cursor-pairs.json",
            input_paths=8,
        )

        checks = cast(list[dict[str, object]], verified["checks"])
        self.assertEqual(
            [check["id"] for check in checks],
            [
                "meeting-first8-verified",
                "meeting-unchanged-control",
                "meeting-first8-withheld-context",
                "cursor-budget-align",
            ],
        )
        self.assertEqual(checks[0]["report_sha256"], wire.digest(expected_first8))
        self.assertEqual(cast(dict[str, int], checks[0]["counts"])["reproduced"], 4)
        self.assertEqual(checks[1]["report_sha256"], wire.digest(expected_unchanged))
        self.assertEqual(cast(dict[str, int], checks[1]["counts"])["reproduced"], 0)
        self.assertEqual(cast(dict[str, int], checks[1]["counts"])["input_compared"], 4)
        self.assertEqual(checks[2]["report_sha256"], wire.digest(expected_withheld))
        self.assertEqual(cast(dict[str, int], checks[2]["counts"])["prediction_opaque"], 4)
        self.assertEqual(cast(dict[str, int], checks[2]["counts"])["reproduced"], 0)
        self.assertEqual(checks[3]["report_sha256"], wire.digest(expected_align))
        self.assertEqual(checks[3]["counts"], expected_align["counts"])
        for check in checks:
            self.assertNotIn("external_observations", check)

    def test_genuine_missing_opaque_and_incompatible_outcomes_verify_cleanly(self) -> None:
        bundle = Path(tempfile.mkdtemp(dir=self.root))
        # Calibration with missing target, opaque external prediction, and valid pair.
        src_missing = _decision("missing-target", "source", action=1)
        src_opaque = _decision("opaque-case", "source", action=1)
        tgt_opaque = _decision("opaque-case", "target", action=2)
        wire.write_lines(bundle / "cal-tape.jsonl", [src_missing, src_opaque, tgt_opaque])

        external_pred: wire.Result = {
            "schema": wire.RESULT_SCHEMA,
            "key": src_opaque["key"],
            "role": src_opaque["role"],
            "native_trial_id": src_opaque["native_trial_id"],
            "basis": "bound",
            "source_decision_sha256": wire.decision_sha256(src_opaque),
            "source_view_sha256": src_opaque["view_sha256"],
            "input_sha256": src_opaque["view_sha256"],
            "input_bytes": len(wire.canonical(src_opaque["view"])),
            "policy": "external",
            "transform": "identity",
            "status": "measured",
            "action": 2,
            "score": {"optimal": True, "feasible": True},
            "error": None,
        }
        missing_pred: wire.Result = {
            "schema": wire.RESULT_SCHEMA,
            "key": src_missing["key"],
            "role": src_missing["role"],
            "native_trial_id": src_missing["native_trial_id"],
            "basis": "bound",
            "source_decision_sha256": wire.decision_sha256(src_missing),
            "source_view_sha256": src_missing["view_sha256"],
            "input_sha256": src_missing["view_sha256"],
            "input_bytes": len(wire.canonical(src_missing["view"])),
            "policy": "intersection",
            "transform": "identity",
            "status": "measured",
            "action": 1,
            "score": {"optimal": True, "feasible": True},
            "error": None,
        }
        wire.write_lines(bundle / "cal-preds.jsonl", [missing_pred, external_pred])
        _write_json(
            bundle / "cal-pairs.json",
            {
                "schema": "seatlab.calibration/v1",
                "pairs": [
                    {
                        "id": "missing-target",
                        "source": _cal_receipt("missing-target", "source", src_missing),
                        "target": _cal_receipt("missing-target", "target", None),
                    },
                    {
                        "id": "opaque-case",
                        "source": _cal_receipt("opaque-case", "source", src_opaque),
                        "target": _cal_receipt("opaque-case", "target", tgt_opaque),
                    },
                ],
            },
        )

        # Alignment with an unbound pair, an unobserved pair, and an absent pair.
        left_unbound = _episode(
            _SOURCE_TRIAL,
            [_episode_frame(1, {"slot": 1}, action=1, basis="reconstructed")],
            case="unbound-case",
        )
        right_unbound = _episode(
            _TARGET_TRIAL,
            [_episode_frame(1, {"slot": 1}, action=1, basis="bound")],
            case="unbound-case",
        )
        left_unobserved = _episode(
            _ALT_SOURCE_TRIAL,
            [_episode_frame(1, {"slot": 2}, action=1, basis="bound")],
            case="unobserved-case",
        )
        right_unobserved = _episode(
            _ALT_TARGET_TRIAL,
            [_episode_frame(1, {"slot": 2}, action=1, basis="bound", observed=False)],
            case="unobserved-case",
        )
        wire.write_lines(bundle / "ep-source.jsonl", [left_unbound, left_unobserved])
        wire.write_lines(bundle / "ep-target.jsonl", [right_unbound, right_unobserved])
        _write_json(
            bundle / "ep-pairs.json",
            {
                "schema": "seatlab.episode-pairs/v1",
                "pairs": [
                    {
                        "id": "unbound-pair",
                        "case_sha256": wire.digest("unbound-case"),
                        "role": "receiver",
                        "source": {
                            "native_trial_id": left_unbound["native_trial_id"],
                            "episode_sha256": left_unbound["episode_sha256"],
                        },
                        "target": {
                            "native_trial_id": right_unbound["native_trial_id"],
                            "episode_sha256": right_unbound["episode_sha256"],
                        },
                    },
                    {
                        "id": "unobserved-pair",
                        "case_sha256": wire.digest("unobserved-case"),
                        "role": "receiver",
                        "source": {
                            "native_trial_id": left_unobserved["native_trial_id"],
                            "episode_sha256": left_unobserved["episode_sha256"],
                        },
                        "target": {
                            "native_trial_id": right_unobserved["native_trial_id"],
                            "episode_sha256": right_unobserved["episode_sha256"],
                        },
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

        recipe_path = bundle / "recipe.json"
        manifest_path = bundle / "manifest.json"
        _write_json(
            recipe_path,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "cal-gaps",
                        "kind": "calibration",
                        "tape": "cal-tape.jsonl",
                        "predictions": "cal-preds.jsonl",
                        "pairs": "cal-pairs.json",
                    },
                    {
                        "id": "align-gaps",
                        "kind": "alignment",
                        "source": "ep-source.jsonl",
                        "target": "ep-target.jsonl",
                        "pairs": "ep-pairs.json",
                    },
                ],
            },
        )

        seal_study(recipe_path, manifest_path)
        summary = verify_study(manifest_path)
        self.assertEqual(summary["status"], "verified")
        checks = cast(list[dict[str, object]], summary["checks"])
        cal_counts = cast(dict[str, int], checks[0]["counts"])
        self.assertEqual(cal_counts["pairs"], 2)
        self.assertEqual(cal_counts["target_missing"], 1)
        self.assertEqual(cal_counts["prediction_opaque"], 1)
        self.assertEqual(cal_counts["reproduced"], 0)
        self.assertEqual(
            checks[0]["external_observations"],
            {
                "predictions": 1,
                "inputs_compared": 1,
                "input_matches": 1,
                "actions_compared": 1,
                "action_matches": 1,
                "scores_compared": 1,
                "score_matches": 1,
            },
        )
        align_counts = cast(dict[str, int], checks[1]["counts"])
        self.assertEqual(align_counts["pairs"], 3)
        self.assertEqual(align_counts["missing"], 1)
        self.assertEqual(align_counts["unbound"], 1)
        self.assertEqual(align_counts["missing_observation"], 1)
        self.assertEqual(align_counts["matched"], 0)
        self.assertEqual(align_counts["matched_prefix"], 0)
        self.assertNotIn("external_observations", checks[1])

    def test_verify_validates_pin_before_opening_referenced_files(self) -> None:
        bundle = self._stage_examples_bundle()
        recipe_path = bundle / "recipe.json"
        manifest_path = bundle / "manifest.json"
        _write_json(
            recipe_path,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "cursor",
                        "kind": "alignment",
                        "source": "cursor-source.jsonl",
                        "target": "cursor-target.jsonl",
                        "pairs": "cursor-pairs.json",
                    }
                ],
            },
        )
        seal_study(recipe_path, manifest_path)

        # Remove referenced files; any snapshot attempt would raise FileNotFoundError.
        (bundle / "cursor-source.jsonl").unlink()
        (bundle / "cursor-target.jsonl").unlink()
        (bundle / "cursor-pairs.json").unlink()

        with self.assertRaisesRegex(StudyMismatch, "expected pin"):
            verify_study(manifest_path, expect_sha256="0" * 64)

        with self.assertRaisesRegex(wire.TapeError, "expect_sha256"):
            verify_study(manifest_path, expect_sha256="NOT-A-HEX-DIGEST")

        # Also verify malformed uppercase pin is rejected as TapeError, not StudyMismatch.
        with self.assertRaises(wire.TapeError):
            verify_study(manifest_path, expect_sha256="A" * 64)

    def test_verify_detects_file_tampering_and_report_digest_mismatch(self) -> None:
        bundle = self._stage_examples_bundle()
        recipe_path = bundle / "recipe.json"
        manifest_path = bundle / "manifest.json"
        _write_json(
            recipe_path,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "meeting-ok",
                        "kind": "calibration",
                        "tape": "meeting.jsonl",
                        "predictions": "meeting-first8-pred.jsonl",
                        "pairs": "meeting-pairs.json",
                        "context": "meeting-first8.json",
                    }
                ],
            },
        )
        manifest = seal_study(recipe_path, manifest_path)

        # 1. File byte/hash tampering raises StudyMismatch before calibration runs.
        context_file = bundle / "meeting-first8.json"
        original_context = context_file.read_bytes()
        context_file.write_bytes(original_context + b" ")
        with patch("seatlab.study.calibrate") as mock_calibrate:
            with self.assertRaisesRegex(StudyMismatch, "meeting-first8.json"):
                verify_study(manifest_path)
            mock_calibrate.assert_not_called()

        # Restore original file and tamper only the report digest in the manifest.
        context_file.write_bytes(original_context)
        tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
        tampered["reports"]["meeting-ok"] = "f" * 64
        tampered_path = bundle / "tampered-manifest.json"
        _write_json(tampered_path, tampered)
        with self.assertRaisesRegex(StudyMismatch, "report digest"):
            verify_study(tampered_path)

        # Re-verifying the untampered manifest still passes with its pinned digest.
        verified = verify_study(manifest_path, expect_sha256=wire.digest(manifest))
        self.assertEqual(verified["status"], "verified")

    def test_verify_preserves_optional_external_observations_from_report(self) -> None:
        bundle = self._stage_examples_bundle()
        recipe_path = bundle / "recipe.json"
        manifest_path = bundle / "manifest.json"
        _write_json(
            recipe_path,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "meeting-ext",
                        "kind": "calibration",
                        "tape": "meeting.jsonl",
                        "predictions": "meeting-first8-pred.jsonl",
                        "pairs": "meeting-pairs.json",
                        "context": "meeting-first8.json",
                    }
                ],
            },
        )
        real_report = calibrate(
            bundle / "meeting.jsonl",
            bundle / "meeting-first8-pred.jsonl",
            bundle / "meeting-pairs.json",
            context=ContextSpec.load(bundle / "meeting-first8.json"),
        )
        enriched = {
            **real_report,
            "external_observations": {"records": 4, "providers": ["stub"]},
        }
        with patch("seatlab.study.calibrate", return_value=enriched):
            manifest = seal_study(recipe_path, manifest_path)
            verified = verify_study(manifest_path, expect_sha256=wire.digest(manifest))
        checks = cast(list[dict[str, object]], verified["checks"])
        self.assertEqual(
            checks[0]["external_observations"],
            {"records": 4, "providers": ["stub"]},
        )

    def test_seal_enforces_siblings_no_overwrite_and_atomic_failure(self) -> None:
        bundle = self._stage_examples_bundle()
        other_dir = Path(tempfile.mkdtemp(dir=self.root))
        recipe_path = bundle / "recipe.json"
        _write_json(
            recipe_path,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "cursor",
                        "kind": "alignment",
                        "source": "cursor-source.jsonl",
                        "target": "cursor-target.jsonl",
                        "pairs": "cursor-pairs.json",
                    }
                ],
            },
        )

        # Different directory rejected.
        with self.assertRaisesRegex(wire.TapeError, "same directory"):
            seal_study(recipe_path, other_dir / "manifest.json")
        self.assertFalse((other_dir / "manifest.json").exists())

        # Existing output refused, including overwriting the recipe itself.
        existing = bundle / "existing.json"
        existing.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            seal_study(recipe_path, existing)
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

        with self.assertRaises(FileExistsError):
            seal_study(recipe_path, recipe_path)

        # Failing analysis must not publish a partial manifest.
        bad_recipe = bundle / "bad-recipe.json"
        bad_out = bundle / "bad-manifest.json"
        (bundle / "corrupt.json").write_text("{not valid json", encoding="utf-8")
        _write_json(
            bad_recipe,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "broken",
                        "kind": "alignment",
                        "source": "cursor-source.jsonl",
                        "target": "cursor-target.jsonl",
                        "pairs": "corrupt.json",
                    }
                ],
            },
        )
        with self.assertRaises(wire.TapeError):
            seal_study(bad_recipe, bad_out)
        self.assertFalse(os.path.lexists(bad_out))

    def test_nested_relative_paths_and_no_subprocess_execution(self) -> None:
        bundle = self._stage_examples_bundle()
        nested = bundle / "arms" / "interactive"
        nested.mkdir(parents=True)
        for name in ("cursor-source.jsonl", "cursor-target.jsonl", "cursor-pairs.json"):
            shutil.move(str(bundle / name), str(nested / name))

        recipe_path = bundle / "recipe.json"
        manifest_path = bundle / "manifest.json"
        _write_json(
            recipe_path,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "nested-cursor",
                        "kind": "alignment",
                        "source": "arms/interactive/cursor-source.jsonl",
                        "target": "arms/interactive/cursor-target.jsonl",
                        "pairs": "arms/interactive/cursor-pairs.json",
                    }
                ],
            },
        )

        with patch("subprocess.Popen", side_effect=AssertionError("subprocess forbidden")):
            manifest = seal_study(recipe_path, manifest_path)
            verified = verify_study(manifest_path, expect_sha256=wire.digest(manifest))
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["files"], 3)


if __name__ == "__main__":
    unittest.main()
