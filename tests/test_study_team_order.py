import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from seatlab import author, tape as wire
from seatlab.episodes import Episode
from seatlab.study import StudyMismatch, seal_study, verify_study
from seatlab.study_contract import (
    MANIFEST_SCHEMA,
    MAX_INPUT_PATHS,
    MIN_INPUT_PATHS,
    RECIPE_SCHEMA,
    TeamAlignmentCheck,
    parse_manifest,
    parse_recipe,
    referenced_paths,
)
from seatlab.team_alignment import team_align
from seatlab.world_order import WORLD_ORDER_SCHEMA

_SOURCE_TRIAL = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_TARGET_TRIAL = "be656986-a740-4e46-999d-21410909878c"
_CASE_SHA = wire.digest({"world": "kitchen", "case": "joint-01"})


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wire.canonical(value) + b"\n")


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _team_check(
    check_id: str = "team-main",
    *,
    source: str = "team/source.jsonl",
    target: str = "team/target.jsonl",
    source_order: str = "team/source-order.json",
    target_order: str = "team/target-order.json",
    input_paths: int | None = None,
) -> dict[str, object]:
    check: dict[str, object] = {
        "id": check_id,
        "kind": "team-alignment",
        "source": source,
        "target": target,
        "source_order": source_order,
        "target_order": target_order,
    }
    if input_paths is not None:
        check["input_paths"] = input_paths
    return check


def _episode(
    trial: str,
    role: str,
    actions: list[str],
    *,
    case_sha256: str = _CASE_SHA,
    closed: bool = True,
) -> Episode:
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
    return author.episode_from_frames(
        id=f"{trial}:{role}",
        case_sha256=case_sha256,
        role=role,
        native_trial_id=trial,
        closed=closed,
        frames=frames,
    )


def _order_doc(episodes: list[Episode], steps: list[list[str]]) -> dict[str, object]:
    return {
        "schema": WORLD_ORDER_SCHEMA,
        "case_sha256": episodes[0]["case_sha256"],
        "native_trial_id": episodes[0]["native_trial_id"],
        "episodes": {ep["role"]: ep["episode_sha256"] for ep in episodes},
        "steps": steps,
    }


def _stage_team_bundle(bundle: Path) -> None:
    (bundle / "team").mkdir()
    source_eps = [
        _episode(_SOURCE_TRIAL, "chef0", ["north", "east"]),
        _episode(_SOURCE_TRIAL, "chef1", ["south", "west"]),
    ]
    target_eps = [
        _episode(_TARGET_TRIAL, "chef0", ["north", "south"]),
        _episode(_TARGET_TRIAL, "chef1", ["south", "west"]),
    ]
    steps = [["chef0", "chef1"], ["chef0", "chef1"]]
    wire.write_lines(bundle / "team" / "source.jsonl", source_eps)
    wire.write_lines(bundle / "team" / "target.jsonl", target_eps)
    _write_json(bundle / "team" / "source-order.json", _order_doc(source_eps, steps))
    _write_json(bundle / "team" / "target-order.json", _order_doc(target_eps, steps))


class TeamAlignmentContractTests(unittest.TestCase):
    def test_parses_team_alignment_fields_bounds_and_referenced_paths(self) -> None:
        check_in = _team_check("kitchen-joint", input_paths=MIN_INPUT_PATHS)
        max_check = _team_check(
            "kitchen-max",
            source="shared/src.jsonl",
            target="shared/tgt.jsonl",
            source_order="shared/src-order.json",
            target_order="shared/tgt-order.json",
            input_paths=MAX_INPUT_PATHS,
        )
        parsed = parse_recipe({"schema": RECIPE_SCHEMA, "checks": [check_in, max_check]})
        self.assertEqual([check["id"] for check in parsed], ["kitchen-joint", "kitchen-max"])
        first = cast(TeamAlignmentCheck, parsed[0])
        self.assertEqual(first["kind"], "team-alignment")
        self.assertEqual(first["input_paths"], MIN_INPUT_PATHS)
        self.assertEqual(
            referenced_paths(parsed),
            [
                "shared/src-order.json",
                "shared/src.jsonl",
                "shared/tgt-order.json",
                "shared/tgt.jsonl",
                "team/source-order.json",
                "team/source.jsonl",
                "team/target-order.json",
                "team/target.jsonl",
            ],
        )

        # Returned check dict is not aliased to input.
        check_in["source_order"] = "mutated.json"
        self.assertEqual(first["source_order"], "team/source-order.json")

    def test_rejects_missing_extra_cross_kind_and_bad_boundary_values(self) -> None:
        for missing in ("source", "target", "source_order", "target_order"):
            with self.subTest(missing=missing):
                broken = _team_check()
                del broken[missing]
                with self.assertRaises(wire.TapeError):
                    parse_recipe({"schema": RECIPE_SCHEMA, "checks": [broken]})

        for extra_key, extra_val in (
            ("pairs", "team/pairs.json"),
            ("context", "team/ctx.json"),
            ("command", "seatlab team-align"),
            ("exec", ["/bin/sh"]),
            ("plugin", "custom"),
        ):
            with self.subTest(extra_key=extra_key):
                broken = _team_check()
                broken[extra_key] = extra_val
                with self.assertRaises(wire.TapeError):
                    parse_recipe({"schema": RECIPE_SCHEMA, "checks": [broken]})

        for bad_path in ("../escape.json", "/abs/order.json", "team/.hidden.json"):
            for field in ("source", "target", "source_order", "target_order"):
                with self.subTest(field=field, bad_path=bad_path):
                    broken = _team_check()
                    broken[field] = bad_path
                    with self.assertRaises(wire.TapeError):
                        parse_recipe({"schema": RECIPE_SCHEMA, "checks": [broken]})

        for bad_limit in (0, -1, MAX_INPUT_PATHS + 1, True, False, 1.0, "4", None):
            with self.subTest(input_paths=bad_limit):
                broken = _team_check()
                broken["input_paths"] = bad_limit
                with self.assertRaises(wire.TapeError):
                    parse_recipe({"schema": RECIPE_SCHEMA, "checks": [broken]})

        bad_kind = _team_check()
        bad_kind["kind"] = "script"
        with self.assertRaises(wire.TapeError):
            parse_recipe({"schema": RECIPE_SCHEMA, "checks": [bad_kind]})

    def test_manifest_requires_order_files_in_exact_file_coverage(self) -> None:
        check = _team_check("joint", input_paths=4)
        parsed = parse_recipe({"schema": RECIPE_SCHEMA, "checks": [check]})
        files = {
            path: {"bytes": index + 1, "sha256": f"{index + 1:064x}"}
            for index, path in enumerate(referenced_paths(parsed))
        }
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "checks": [check],
            "files": files,
            "reports": {"joint": "a" * 64},
        }
        self.assertEqual(parse_manifest(manifest)["files"].keys(), files.keys())

        for omitted in ("team/source-order.json", "team/target-order.json"):
            with self.subTest(omitted=omitted):
                missing_order = copy.deepcopy(manifest)
                del cast(dict[str, object], missing_order["files"])[omitted]
                with self.assertRaisesRegex(wire.TapeError, "files coverage mismatch"):
                    parse_manifest(missing_order)


class TeamAlignmentStudyTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_seal_verify_summary_shape_and_snapshot_isolation(self) -> None:
        bundle = Path(tempfile.mkdtemp(dir=self.root))
        _stage_team_bundle(bundle)
        recipe_path = bundle / "recipe.json"
        manifest_path = bundle / "study.json"
        _write_json(
            recipe_path,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [_team_check("kitchen-joint", input_paths=6)],
            },
        )

        seen_roots: list[Path] = []

        def _spy_team_align(
            source: Path,
            target: Path,
            *,
            source_order: Path,
            target_order: Path,
            input_paths: int | None = None,
        ) -> dict[str, object]:
            for path in (source, target, source_order, target_order):
                self.assertFalse(path.is_relative_to(bundle.resolve()))
                self.assertTrue(path.is_file())
            seen_roots.append(source.parents[1])
            return team_align(
                source,
                target,
                source_order=source_order,
                target_order=target_order,
                input_paths=input_paths,
            )

        with patch("seatlab.study.team_align", side_effect=_spy_team_align) as mock_align:
            manifest = seal_study(recipe_path, manifest_path)
            files = cast(dict[str, dict[str, object]], manifest["files"])
            self.assertEqual(
                sorted(files),
                [
                    "team/source-order.json",
                    "team/source.jsonl",
                    "team/target-order.json",
                    "team/target.jsonl",
                ],
            )
            for rel_path, receipt in files.items():
                disk_path = bundle / rel_path
                self.assertEqual(receipt["bytes"], disk_path.stat().st_size)
                self.assertEqual(receipt["sha256"], _file_digest(disk_path))
            verified = verify_study(manifest_path, expect_sha256=wire.digest(manifest))

        self.assertEqual(mock_align.call_count, 2)
        self.assertEqual(len(seen_roots), 2)
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["files"], 4)
        self.assertEqual(
            verified["bytes"],
            sum(cast(int, receipt["bytes"]) for receipt in files.values()),
        )
        expected_report = team_align(
            bundle / "team" / "source.jsonl",
            bundle / "team" / "target.jsonl",
            source_order=bundle / "team" / "source-order.json",
            target_order=bundle / "team" / "target-order.json",
            input_paths=6,
        )
        checks = cast(list[dict[str, object]], verified["checks"])
        self.assertEqual(
            checks,
            [
                {
                    "id": "kitchen-joint",
                    "kind": "team-alignment",
                    "report_sha256": wire.digest(expected_report),
                    "status": expected_report["status"],
                    "matched_steps": expected_report["matched_steps"],
                    "boundary_step": expected_report["boundary_step"],
                }
            ],
        )
        self.assertNotIn("counts", checks[0])

    def test_order_file_changed_after_sealing_fails_before_team_align(self) -> None:
        bundle = Path(tempfile.mkdtemp(dir=self.root))
        _stage_team_bundle(bundle)
        recipe_path = bundle / "recipe.json"
        manifest_path = bundle / "study.json"
        _write_json(recipe_path, {"schema": RECIPE_SCHEMA, "checks": [_team_check("joint")]})

        seal_study(recipe_path, manifest_path)

        order_path = bundle / "team" / "target-order.json"
        original = order_path.read_bytes()
        order_path.write_bytes(original + b" ")

        with patch("seatlab.study.team_align") as mock_align:
            with self.assertRaisesRegex(StudyMismatch, "team/target-order.json"):
                verify_study(manifest_path)
            mock_align.assert_not_called()

        # Restoring order file and tampering with report digest also fails cleanly.
        order_path.write_bytes(original)
        tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
        tampered["reports"]["joint"] = "0" * 64
        tampered_path = bundle / "tampered.json"
        _write_json(tampered_path, tampered)
        with self.assertRaisesRegex(StudyMismatch, "report digest"):
            verify_study(tampered_path)

    def test_missing_snapshot_input_fails_on_seal_and_verify(self) -> None:
        bundle = Path(tempfile.mkdtemp(dir=self.root))
        _stage_team_bundle(bundle)
        recipe_path = bundle / "recipe.json"
        manifest_path = bundle / "study.json"
        _write_json(recipe_path, {"schema": RECIPE_SCHEMA, "checks": [_team_check("joint")]})

        # Missing order file during seal fails without publishing manifest.
        missing_order = bundle / "team" / "source-order.json"
        saved_order = missing_order.read_bytes()
        missing_order.unlink()
        with self.assertRaises(FileNotFoundError):
            seal_study(recipe_path, manifest_path)
        self.assertFalse(manifest_path.exists())

        # Seal with all inputs present, then delete an order file before verify.
        missing_order.write_bytes(saved_order)
        seal_study(recipe_path, manifest_path)

        (bundle / "team" / "target-order.json").unlink()
        with patch("seatlab.study.team_align") as mock_align:
            with self.assertRaises(FileNotFoundError):
                verify_study(manifest_path)
            mock_align.assert_not_called()

    def test_old_calibration_and_alignment_manifest_and_summary_stay_byte_compatible(
        self,
    ) -> None:
        examples = Path(__file__).resolve().parents[1] / "examples"
        bundle = Path(tempfile.mkdtemp(dir=self.root))
        for name in ("cursor-source.jsonl", "cursor-target.jsonl", "cursor-pairs.json"):
            (bundle / name).write_bytes((examples / name).read_bytes())

        recipe_path = bundle / "recipe.json"
        manifest_path = bundle / "study.json"
        _write_json(
            recipe_path,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "cursor-legacy",
                        "kind": "alignment",
                        "source": "cursor-source.jsonl",
                        "target": "cursor-target.jsonl",
                        "pairs": "cursor-pairs.json",
                        "input_paths": 4,
                    }
                ],
            },
        )

        manifest = seal_study(recipe_path, manifest_path)
        verified = verify_study(manifest_path, expect_sha256=wire.digest(manifest))
        self.assertEqual(
            list(manifest.keys()),
            ["schema", "checks", "files", "reports"],
        )
        checks = cast(list[dict[str, object]], verified["checks"])
        self.assertEqual(len(checks), 1)
        self.assertEqual(
            set(checks[0].keys()),
            {"id", "kind", "report_sha256", "counts"},
        )
        self.assertNotIn("status", checks[0])
        self.assertNotIn("matched_steps", checks[0])
        self.assertNotIn("boundary_step", checks[0])


if __name__ == "__main__":
    unittest.main()
