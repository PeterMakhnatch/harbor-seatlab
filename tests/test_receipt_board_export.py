from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from seatlab import tape as wire
from seatlab.alignment import align
from seatlab.calibration import calibrate
from seatlab.context import ContextSpec
from seatlab.episodes import read_episodes
from seatlab.prefix import prefix
from seatlab.replay import replay

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "receipt-board"
_EXPORT_PATH = _EXAMPLE_DIR / "export.py"
_POLICY_PATH = _EXAMPLE_DIR / "policy.py"

_SOURCE_UUID = "11111111-2222-4333-8444-5555555555ab"
_TARGET_UUID = "66666666-7777-4888-9999-aaaaaaaaaaaa"
_CASE: dict[str, dict[str, list[str]]] = {
    "options": {
        "p1": ["amber", "cobalt"],
        "p2": ["mint", "coral"],
    }
}
_FIRST_SPEC = ContextSpec(
    {
        "schema": "seatlab.context/v1",
        "ops": [{"op": "take", "path": ["options"], "count": 1}],
    }
)


def _load_export_module() -> Any:
    spec = importlib.util.spec_from_file_location("_receipt_board_export", _EXPORT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_case(root: Path, payload: object = _CASE) -> Path:
    path = root / "case.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _seat_result(
    seat: str,
    *,
    under_test: bool = True,
    exception_info: object = None,
    finished_at: str | None = "2026-11-19T12:00:05Z",
) -> dict[str, object]:
    return {
        "seat": seat,
        "under_test": under_test,
        "agent_info": {"name": "oracle", "version": "1.0.0"},
        "agent_execution": {
            "started_at": "2026-11-19T12:00:00Z",
            "finished_at": finished_at,
        },
        "exception_info": exception_info,
    }


def _make_trial(
    root: Path,
    name: str,
    *,
    trial_id: str,
    mode: str,
    actions: dict[str, str] | None = None,
    reward: float | int = 1.0,
    rewards_dict: dict[str, object] | None = None,
    reward_txt: str | None = "1\n",
    reward_json: object | None = None,
    seat_results: list[dict[str, object]] | None = None,
    exception_info: object = None,
    finished_at: str | None = "2026-11-19T12:00:10Z",
    reward_valid: bool | None = True,
    receipts_override: list[dict[str, object]] | None = None,
    receipts_raw: str | None = None,
    write_receipts: bool = True,
) -> Path:
    trial_dir = root / name
    trial_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {
        "schema_version": 1,
        "id": trial_id,
        "task_name": "receipt-board",
        "trial_name": name,
        "finished_at": finished_at,
        "exception_info": exception_info,
        "verifier_result": {
            "rewards": {"reward": reward} if rewards_dict is None else rewards_dict
        },
        "seat_results": (
            [_seat_result("p1"), _seat_result("p2")]
            if seat_results is None
            else seat_results
        ),
    }
    if reward_valid is not None:
        result["reward_valid"] = reward_valid
    (trial_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")

    if reward_txt is not None or reward_json is not None:
        verifier_dir = trial_dir / "verifier"
        verifier_dir.mkdir(parents=True, exist_ok=True)
        if reward_txt is not None:
            (verifier_dir / "reward.txt").write_text(reward_txt, encoding="utf-8")
        if reward_json is not None:
            (verifier_dir / "reward.json").write_text(
                json.dumps(reward_json), encoding="utf-8"
            )

    if write_receipts:
        world_dir = trial_dir / "artifacts" / "logs" / "world"
        world_dir.mkdir(parents=True, exist_ok=True)
        receipts_path = world_dir / "receipts.jsonl"
        if receipts_raw is not None:
            receipts_path.write_text(receipts_raw, encoding="utf-8")
        else:
            if receipts_override is None:
                rows: list[dict[str, object]] = []
                for role in ("p1", "p2"):
                    options = (
                        list(_CASE["options"][role])
                        if mode == "full"
                        else [_CASE["options"][role][0]]
                    )
                    view = {"role": role, "options": options}
                    action = (
                        options[-1]
                        if actions is None
                        else actions[role]
                    )
                    rows.append(
                        {
                            "role": role,
                            "turn": 1,
                            "view": view,
                            "acknowledged_sha256": wire.digest(view),
                            "action": action,
                        }
                    )
            else:
                rows = receipts_override
            receipts_path.write_bytes(
                b"".join(wire.canonical(row) + b"\n" for row in rows)
            )
    return trial_dir


class ReceiptBoardExportTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.exporter = _load_export_module()
        self.case_path = _write_case(self.root)

    def _export(
        self,
        source_dir: Path,
        target_dir: Path,
        out_name: str = "published",
        *,
        case_path: Path | None = None,
    ) -> tuple[Path, dict[str, object]]:
        out_dir = self.root / out_name
        summary = self.exporter.export_trials(
            source_dir,
            target_dir,
            out_dir,
            case_path=self.case_path if case_path is None else case_path,
        )
        return out_dir, summary

    def test_valid_export_integrates_with_replay_calibrate_prefix_and_align(self) -> None:
        source_dir = _make_trial(
            self.root, "source", trial_id=_SOURCE_UUID, mode="full", reward=1.0
        )
        target_dir = _make_trial(
            self.root, "target", trial_id=_TARGET_UUID, mode="first", reward=1.0
        )
        out_dir, summary = self._export(source_dir, target_dir)

        expected_files = {
            "sources.jsonl",
            "decisions.jsonl",
            "source-episodes.jsonl",
            "target-episodes.jsonl",
            "pairs.json",
            "episode-pairs.json",
            "summary.json",
        }
        self.assertEqual({p.name for p in out_dir.iterdir()}, expected_files)

        sources = list(wire.read_tape(out_dir / "sources.jsonl"))
        decisions = list(wire.read_tape(out_dir / "decisions.jsonl"))
        source_episodes = list(read_episodes(out_dir / "source-episodes.jsonl"))
        target_episodes = list(read_episodes(out_dir / "target-episodes.jsonl"))

        self.assertEqual(len(sources), 2)
        self.assertEqual(len(decisions), 4)
        self.assertEqual(len(source_episodes), 2)
        self.assertEqual(len(target_episodes), 2)
        self.assertEqual([row["role"] for row in sources], ["p1", "p2"])
        self.assertTrue(all(row["basis"] == "bound" for row in decisions))
        self.assertEqual(
            [row["observed"] for row in sources],
            [{"action": "cobalt"}, {"action": "coral"}],
        )

        # Offline answers mark the last original option optimal and both feasible.
        for row in decisions:
            full_options = _CASE["options"][row["role"]]
            self.assertEqual(
                row["answers"],
                [
                    {"action": full_options[0], "optimal": False, "feasible": True},
                    {"action": full_options[1], "optimal": True, "feasible": True},
                ],
            )

        # Summary records the single shared team reward separately per trial.
        self.assertEqual(summary["case"], _CASE)
        self.assertEqual(summary["case_sha256"], wire.digest(_CASE))
        self.assertEqual(summary["source"]["native_trial_id"], _SOURCE_UUID)
        self.assertEqual(summary["target"]["native_trial_id"], _TARGET_UUID)
        self.assertEqual(summary["source"]["team_reward"], 1.0)
        self.assertEqual(summary["target"]["team_reward"], 1.0)

        # External policy selects last visible option; downstream tools all validate.
        policy_cmd = [sys.executable, str(_POLICY_PATH)]
        pred_first = self.root / "pred-first.jsonl"
        replay(
            out_dir / "sources.jsonl",
            pred_first,
            policy_cmd,
            transform=_FIRST_SPEC,
        )
        cal = calibrate(
            out_dir / "decisions.jsonl",
            pred_first,
            out_dir / "pairs.json",
            context=_FIRST_SPEC,
        )
        self.assertEqual(cal["counts"]["pairs"], 2)
        self.assertEqual(cal["counts"]["target_acknowledged"], 2)
        self.assertEqual(cal["counts"]["native_action_changed"], 2)
        if "external_observations" in cal:
            ext = cal["external_observations"]
            self.assertEqual(ext["predictions"], 2)
            self.assertEqual(ext["inputs_compared"], 2)
            self.assertEqual(ext["input_matches"], 2)
            self.assertEqual(ext["actions_compared"], 2)
            self.assertEqual(ext["action_matches"], 2)
            self.assertEqual(ext["scores_compared"], 2)
            self.assertEqual(ext["score_matches"], 2)

        pref = prefix(out_dir / "source-episodes.jsonl", policy_cmd)
        self.assertEqual(pref["counts"]["matched"], 2)

        aligned = align(
            out_dir / "source-episodes.jsonl",
            out_dir / "target-episodes.jsonl",
            out_dir / "episode-pairs.json",
        )
        self.assertEqual(aligned["counts"]["pairs"], 2)
        self.assertEqual(aligned["counts"]["input_changed"], 2)

    def test_accepts_non_oracle_policy_action_in_issued_options(self) -> None:
        source_dir = _make_trial(
            self.root,
            "source",
            trial_id=_SOURCE_UUID,
            mode="full",
            actions={"p1": "amber", "p2": "mint"},
            reward=1.0,
        )
        target_dir = _make_trial(
            self.root,
            "target",
            trial_id=_TARGET_UUID,
            mode="first",
            actions={"p1": "amber", "p2": "mint"},
            reward=1.0,
        )
        out_dir, _ = self._export(source_dir, target_dir)
        sources = list(wire.read_tape(out_dir / "sources.jsonl"))
        self.assertEqual(
            [row["observed"] for row in sources],
            [{"action": "amber"}, {"action": "mint"}],
        )

    def test_accepts_trial_without_verifier_reward_file_on_disk(self) -> None:
        source_dir = _make_trial(
            self.root,
            "source",
            trial_id=_SOURCE_UUID,
            mode="full",
            reward=1.0,
            reward_txt=None,
        )
        target_dir = _make_trial(
            self.root,
            "target",
            trial_id=_TARGET_UUID,
            mode="first",
            reward=0.0,
            reward_txt="0\n",
        )
        out_dir, summary = self._export(source_dir, target_dir)
        self.assertTrue((out_dir / "summary.json").is_file())
        self.assertEqual(summary["source"]["team_reward"], 1.0)
        self.assertEqual(summary["target"]["team_reward"], 0.0)

    def test_rejects_mismatched_verifier_reward_file(self) -> None:
        source_dir = _make_trial(
            self.root,
            "source",
            trial_id=_SOURCE_UUID,
            mode="full",
            reward=1.0,
            reward_txt="0\n",
        )
        target_dir = _make_trial(
            self.root, "target", trial_id=_TARGET_UUID, mode="first", reward=1.0
        )
        out_dir = self.root / "bad-reward"
        with self.assertRaisesRegex(ValueError, "verifier/reward.txt"):
            self.exporter.export_trials(
                source_dir, target_dir, out_dir, case_path=self.case_path
            )
        self.assertFalse(out_dir.exists())

    def test_rejects_per_role_rewards_in_result_json(self) -> None:
        source_dir = _make_trial(
            self.root,
            "source",
            trial_id=_SOURCE_UUID,
            mode="full",
            rewards_dict={"p1": 1.0, "p2": 1.0},
            reward_txt=None,
        )
        target_dir = _make_trial(
            self.root, "target", trial_id=_TARGET_UUID, mode="first"
        )
        out_dir = self.root / "per-role-reward"
        with self.assertRaisesRegex(ValueError, "shared team 'reward'"):
            self.exporter.export_trials(
                source_dir, target_dir, out_dir, case_path=self.case_path
            )
        self.assertFalse(out_dir.exists())

    def test_rejects_same_trial_uuid_for_source_and_target(self) -> None:
        source_dir = _make_trial(
            self.root, "source", trial_id=_SOURCE_UUID, mode="full"
        )
        target_dir = _make_trial(
            self.root, "target", trial_id=_SOURCE_UUID, mode="first"
        )
        out_dir = self.root / "same-uuid"
        with self.assertRaisesRegex(ValueError, "distinct canonical native_trial_id"):
            self.exporter.export_trials(
                source_dir, target_dir, out_dir, case_path=self.case_path
            )
        self.assertFalse(out_dir.exists())

    def test_rejects_noncanonical_uuid_or_exception_or_untested_seat(self) -> None:
        target_dir = _make_trial(
            self.root, "target", trial_id=_TARGET_UUID, mode="first"
        )
        cases = (
            (
                "upper-uuid",
                {"trial_id": _SOURCE_UUID.upper()},
                "canonical lowercase",
            ),
            (
                "trial-exc",
                {"exception_info": {"exception_type": "AgentTimeoutError"}},
                "recorded exception",
            ),
            (
                "seat-exc",
                {
                    "seat_results": [
                        _seat_result("p1"),
                        _seat_result(
                            "p2", exception_info={"exception_type": "RuntimeError"}
                        ),
                    ]
                },
                "recorded exception",
            ),
            (
                "not-under-test",
                {
                    "seat_results": [
                        _seat_result("p1", under_test=True),
                        _seat_result("p2", under_test=False),
                    ]
                },
                "under_test=true",
            ),
            (
                "missing-seat",
                {"seat_results": [_seat_result("p1")]},
                "seat_results must contain exactly",
            ),
            (
                "unterminated-seat",
                {
                    "seat_results": [
                        _seat_result("p1", finished_at=None),
                        _seat_result("p2"),
                    ]
                },
                "terminal agent_execution",
            ),
        )
        for label, kwargs, pattern in cases:
            with self.subTest(label=label):
                params: dict[str, Any] = {"trial_id": _SOURCE_UUID, "mode": "full"}
                params.update(kwargs)
                source_dir = _make_trial(self.root, f"src-{label}", **params)
                out_dir = self.root / f"out-{label}"
                with self.assertRaisesRegex(ValueError, pattern):
                    self.exporter.export_trials(
                        source_dir, target_dir, out_dir, case_path=self.case_path
                    )
                self.assertFalse(out_dir.exists())

    def test_rejects_missing_duplicate_cross_case_or_unissued_receipts(self) -> None:
        source_ok = _make_trial(
            self.root, "source-ok", trial_id=_SOURCE_UUID, mode="full"
        )
        target_ok = _make_trial(
            self.root, "target-ok", trial_id=_TARGET_UUID, mode="first"
        )

        # Missing receipts file.
        missing_receipts = _make_trial(
            self.root,
            "missing-receipts",
            trial_id=_SOURCE_UUID,
            mode="full",
            write_receipts=False,
        )
        with self.assertRaisesRegex(ValueError, "missing required file"):
            self._export(missing_receipts, target_ok, "out-missing-receipts")
        self.assertFalse((self.root / "out-missing-receipts").exists())

        # Missing p2 receipt.
        p1_view = {"role": "p1", "options": ["amber", "cobalt"]}
        p1_only = _make_trial(
            self.root,
            "p1-only",
            trial_id=_SOURCE_UUID,
            mode="full",
            receipts_override=[
                {
                    "role": "p1",
                    "turn": 1,
                    "view": p1_view,
                    "acknowledged_sha256": wire.digest(p1_view),
                    "action": "cobalt",
                }
            ],
        )
        with self.assertRaisesRegex(ValueError, "missing receipts for role"):
            self._export(p1_only, target_ok, "out-p1-only")
        self.assertFalse((self.root / "out-p1-only").exists())

        # Duplicate p1 receipt.
        dup_p1 = _make_trial(
            self.root,
            "dup-p1",
            trial_id=_SOURCE_UUID,
            mode="full",
            receipts_override=[
                {
                    "role": "p1",
                    "turn": 1,
                    "view": p1_view,
                    "acknowledged_sha256": wire.digest(p1_view),
                    "action": "cobalt",
                },
                {
                    "role": "p1",
                    "turn": 1,
                    "view": p1_view,
                    "acknowledged_sha256": wire.digest(p1_view),
                    "action": "amber",
                },
            ],
        )
        with self.assertRaisesRegex(ValueError, "duplicate receipt"):
            self._export(dup_p1, target_ok, "out-dup-p1")
        self.assertFalse((self.root / "out-dup-p1").exists())

        # Deliberate cross-case: target has wrong options / full view instead of first-only.
        wrong_target = _make_trial(
            self.root, "wrong-target", trial_id=_TARGET_UUID, mode="full"
        )
        with self.assertRaisesRegex(ValueError, "does not match expected 'first' view"):
            self._export(source_ok, wrong_target, "out-wrong-target")
        self.assertFalse((self.root / "out-wrong-target").exists())

        # Deliberate cross-case: tampered case.json constant.
        wrong_case_dir = self.root / "wrong-case-dir"
        wrong_case_dir.mkdir()
        wrong_case = _write_case(
            wrong_case_dir,
            {"options": {"p1": ["red", "blue"], "p2": ["mint", "coral"]}},
        )
        with self.assertRaisesRegex(ValueError, "expected receipt-board case constant"):
            self._export(source_ok, target_ok, "out-wrong-case", case_path=wrong_case)
        self.assertFalse((self.root / "out-wrong-case").exists())

        # Action outside issued options (choosing second option in first-only target).
        out_of_view_target = _make_trial(
            self.root,
            "out-of-view-target",
            trial_id=_TARGET_UUID,
            mode="first",
            actions={"p1": "cobalt", "p2": "mint"},
        )
        with self.assertRaisesRegex(ValueError, "not in issued options"):
            self._export(source_ok, out_of_view_target, "out-unissued-action")
        self.assertFalse((self.root / "out-unissued-action").exists())

        # Mismatched acknowledged_sha256 rejected by seatlab.author.accepted_frame.
        bad_ack_target = _make_trial(
            self.root,
            "bad-ack",
            trial_id=_TARGET_UUID,
            mode="first",
            receipts_override=[
                {
                    "role": "p1",
                    "turn": 1,
                    "view": {"role": "p1", "options": ["amber"]},
                    "acknowledged_sha256": "0" * 64,
                    "action": "amber",
                },
                {
                    "role": "p2",
                    "turn": 1,
                    "view": {"role": "p2", "options": ["mint"]},
                    "acknowledged_sha256": wire.digest(
                        {"role": "p2", "options": ["mint"]}
                    ),
                    "action": "mint",
                },
            ],
        )
        with self.assertRaisesRegex(ValueError, "acknowledged_sha256"):
            self._export(source_ok, bad_ack_target, "out-bad-ack")
        self.assertFalse((self.root / "out-bad-ack").exists())

    def test_refuses_existing_output_directory_and_leaves_no_partial_publish(self) -> None:
        source_dir = _make_trial(
            self.root, "source", trial_id=_SOURCE_UUID, mode="full"
        )
        target_dir = _make_trial(
            self.root, "target", trial_id=_TARGET_UUID, mode="first"
        )
        existing = self.root / "already-here"
        existing.mkdir()
        sentinel = existing / "keep.txt"
        sentinel.write_text("untouched", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            self.exporter.export_trials(
                source_dir, target_dir, existing, case_path=self.case_path
            )
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")
        self.assertEqual([p.name for p in existing.iterdir()], ["keep.txt"])

    def test_policy_cli_selects_last_visible_option_and_rejects_malformed_view(
        self,
    ) -> None:
        proc = subprocess.run(
            [sys.executable, str(_POLICY_PATH)],
            input=json.dumps({"role": "p1", "options": ["amber", "cobalt"]}),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"action": "cobalt"})

        proc_single = subprocess.run(
            [sys.executable, str(_POLICY_PATH)],
            input=json.dumps({"role": "p2", "options": ["mint"]}),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc_single.returncode, 0, proc_single.stderr)
        self.assertEqual(json.loads(proc_single.stdout), {"action": "mint"})

        bad_proc = subprocess.run(
            [sys.executable, str(_POLICY_PATH)],
            input=json.dumps(
                {"role": "p1", "options": ["amber"], "answers": [{"action": "amber"}]}
            ),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(bad_proc.returncode, 0)

    def test_export_cli_returns_nonzero_and_leaves_no_partial_dir_on_error(
        self,
    ) -> None:
        source_dir = _make_trial(
            self.root, "cli-src", trial_id=_SOURCE_UUID, mode="full"
        )
        target_dir = _make_trial(
            self.root, "cli-tgt", trial_id=_SOURCE_UUID, mode="first"
        )
        out_dir = self.root / "cli-out"
        rc = self.exporter.main(
            [str(source_dir), str(target_dir), "--out", str(out_dir)]
        )
        self.assertEqual(rc, 1)
        self.assertFalse(out_dir.exists())
