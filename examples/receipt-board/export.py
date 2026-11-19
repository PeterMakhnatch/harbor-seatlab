"""Export two completed Harbor receipt-board trials into portable SeatLab artifacts.

This task-specific adapter reads two plain Harbor trial directories (a full-option source
trial and a first-option target trial) without importing Harbor or running any task code.
Matching SHA-256 digests check internal consistency against canonical JSON bytes; they do
not authenticate the adapter, actor, or native trial origin.
"""

from __future__ import annotations

import argparse
import errno
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal, TypedDict, cast
from uuid import UUID

from seatlab.author import accepted_frame, decision_from_frame, episode_from_frames
from seatlab.episodes import Episode
from seatlab.tape import (
    Answer,
    Decision,
    TapeError,
    canonical,
    decision_sha256,
    decode,
    digest,
    write_lines,
)

_ROLES: tuple[str, ...] = ("p1", "p2")
_ARM_MODE: dict[str, Literal["full", "first"]] = {"source": "full", "target": "first"}
_EXPECTED_CASE: dict[str, dict[str, list[str]]] = {
    "options": {
        "p1": ["amber", "cobalt"],
        "p2": ["mint", "coral"],
    }
}
_DEFAULT_CASE_PATH = (
    Path(__file__).resolve().parent / "task" / "environment" / "case.json"
)
_RECEIPT_FIELDS = frozenset({"role", "turn", "view", "acknowledged_sha256", "action"})
_CALIBRATION_SCHEMA = "seatlab.calibration/v1"
_EPISODE_PAIRS_SCHEMA = "seatlab.episode-pairs/v1"
_SUMMARY_SCHEMA = "seatlab.receipt-board-export/v1"


class _TrialData(TypedDict):
    native_trial_id: str
    reward: float | int
    decisions: dict[str, Decision]
    episodes: dict[str, Episode]


def _read_no_symlink(root: Path, relative: Path, label: str) -> bytes:
    """Read a file under root without traversing symlinks."""
    resolved_root = root.resolve()
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label}: symlink is not allowed ({relative.as_posix()})")
    if not current.is_file():
        raise ValueError(f"{label}: missing required file {relative.as_posix()}")
    if current.resolve() != resolved_root / relative:
        raise ValueError(f"{label}: path escapes root ({relative.as_posix()})")
    return current.read_bytes()


def _load_case(path: Path = _DEFAULT_CASE_PATH) -> tuple[dict[str, list[str]], str]:
    """Load and verify the fixed receipt-board case constant."""
    if path.is_symlink():
        raise ValueError(f"case file must not be a symlink: {path}")
    if not path.is_file():
        raise ValueError(f"missing case file: {path}")
    try:
        raw = decode(path.read_bytes())
    except (OSError, TapeError) as exc:
        raise ValueError(f"invalid case file {path}: {exc}") from exc
    if raw != _EXPECTED_CASE:
        raise ValueError(
            f"case file {path} does not match the expected receipt-board case constant"
        )
    options = cast(dict[str, list[str]], cast(dict[str, Any], raw)["options"])
    return {role: list(options[role]) for role in _ROLES}, digest(raw)


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError(f"{label}: expected a canonical UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid UUID {value!r}") from exc
    if str(parsed) != value or parsed.int == 0:
        raise ValueError(f"{label}: UUID must be canonical lowercase and nonzero")
    return value


def _check_no_exception(record: dict[str, Any], label: str) -> None:
    if "exception_info" not in record:
        raise ValueError(f"{label}: missing exception_info field")
    exception = record["exception_info"]
    if exception is None:
        return
    exc_type = exception.get("exception_type") if isinstance(exception, dict) else None
    if isinstance(exc_type, str) and exc_type:
        raise ValueError(f"{label}: native trial recorded exception {exc_type!r}")
    raise ValueError(f"{label}: native trial recorded an exception")


def _finite_number(value: object, label: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: expected a finite numeric reward")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label}: reward must be finite")
    return value


def _check_reward_files(
    trial_dir: Path, expected_reward: float | int, label: str
) -> None:
    """Validate verifier/reward.txt or verifier/reward.json when present."""
    verifier_dir = trial_dir / "verifier"
    if not os.path.lexists(verifier_dir):
        return
    if verifier_dir.is_symlink() or not verifier_dir.is_dir():
        raise ValueError(f"{label}: verifier path must be a real directory")

    reward_json = Path("verifier/reward.json")
    reward_txt = Path("verifier/reward.txt")
    if os.path.lexists(trial_dir / reward_json):
        try:
            parsed = decode(_read_no_symlink(trial_dir, reward_json, label))
        except TapeError as exc:
            raise ValueError(f"{label}: invalid verifier/reward.json: {exc}") from exc
        if not isinstance(parsed, dict) or parsed.keys() != {"reward"}:
            raise ValueError(
                f"{label}: verifier/reward.json must be an object with exact key 'reward'"
            )
        file_reward = _finite_number(parsed["reward"], f"{label} verifier/reward.json")
        if file_reward != expected_reward:
            raise ValueError(
                f"{label}: verifier/reward.json ({file_reward!r}) does not match "
                f"result.json reward ({expected_reward!r})"
            )
    if os.path.lexists(trial_dir / reward_txt):
        try:
            raw_text = (
                _read_no_symlink(trial_dir, reward_txt, label).decode("utf-8").strip()
            )
        except UnicodeDecodeError as exc:
            raise ValueError(f"{label}: verifier/reward.txt is not valid UTF-8") from exc
        if not raw_text:
            raise ValueError(f"{label}: verifier/reward.txt is empty")
        try:
            file_reward = float(raw_text)
        except ValueError as exc:
            raise ValueError(f"{label}: invalid numeric verifier/reward.txt") from exc
        file_reward = _finite_number(file_reward, f"{label} verifier/reward.txt")
        if file_reward != expected_reward:
            raise ValueError(
                f"{label}: verifier/reward.txt ({file_reward!r}) does not match "
                f"result.json reward ({expected_reward!r})"
            )


def _read_result(trial_dir: Path, label: str) -> tuple[str, float | int]:
    """Validate result.json for a genuine completed two-seat Harbor trial."""
    try:
        raw = decode(_read_no_symlink(trial_dir, Path("result.json"), label))
    except TapeError as exc:
        raise ValueError(f"{label}: invalid result.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: result.json must be a JSON object")
    result = cast(dict[str, Any], raw)

    version = result.get("schema_version", 1)
    if type(version) is not int or version != 1:
        raise ValueError(f"{label}: unsupported result.json schema_version {version!r}")

    native_trial_id = _canonical_uuid(result.get("id"), f"{label} result.json id")
    _check_no_exception(result, f"{label} result.json")

    finished_at = result.get("finished_at")
    if not isinstance(finished_at, str) or not finished_at:
        raise ValueError(f"{label}: result.json is missing terminal finished_at")

    if "reward_valid" in result and result["reward_valid"] is not True:
        raise ValueError(f"{label}: result.json reward_valid is not true")

    if result.get("step_results") is not None:
        raise ValueError(f"{label}: expected a multi-seat trial, not a multi-step trial")
    if result.get("agent_result") is not None:
        raise ValueError(f"{label}: expected a multi-seat trial, not a single-agent trial")

    verifier_result = result.get("verifier_result")
    if not isinstance(verifier_result, dict):
        raise ValueError(f"{label}: result.json is missing verifier_result")
    rewards = verifier_result.get("rewards")
    if not isinstance(rewards, dict) or rewards.keys() != {"reward"}:
        raise ValueError(
            f"{label}: result.json verifier_result.rewards must contain only a single "
            "shared team 'reward'"
        )
    reward = _finite_number(
        rewards["reward"], f"{label} verifier_result.rewards['reward']"
    )

    seat_results = result.get("seat_results")
    if not isinstance(seat_results, list) or len(seat_results) != len(_ROLES):
        raise ValueError(
            f"{label}: result.json seat_results must contain exactly the roles {_ROLES}"
        )
    seen_roles: set[str] = set()
    for entry in seat_results:
        if not isinstance(entry, dict):
            raise ValueError(f"{label}: seat_results entry must be an object")
        role = entry.get("seat")
        if not isinstance(role, str) or role not in _ROLES:
            raise ValueError(f"{label}: unexpected seat {role!r} in seat_results")
        if role in seen_roles:
            raise ValueError(f"{label}: duplicate seat {role!r} in seat_results")
        seen_roles.add(role)
        if entry.get("under_test") is not True:
            raise ValueError(f"{label}: seat {role!r} must have under_test=true")
        _check_no_exception(entry, f"{label} seat {role!r}")
        execution = entry.get("agent_execution")
        if not isinstance(execution, dict) or not all(
            isinstance(execution.get(field), str) and execution[field]
            for field in ("started_at", "finished_at")
        ):
            raise ValueError(
                f"{label}: seat {role!r} is missing terminal agent_execution timestamps"
            )
    if seen_roles != set(_ROLES):
        raise ValueError(f"{label}: seat_results must contain exactly roles {_ROLES}")

    _check_reward_files(trial_dir, reward, label)
    return native_trial_id, reward


def _expected_view(
    role: str, full_options: list[str], mode: Literal["full", "first"]
) -> dict[str, object]:
    visible = list(full_options) if mode == "full" else [full_options[0]]
    return {"role": role, "options": visible}


def _offline_answers(full_options: list[str]) -> list[Answer]:
    optimal_option = full_options[-1]
    return [
        {
            "action": option,
            "optimal": option == optimal_option,
            "feasible": True,
        }
        for option in full_options
    ]


def _read_receipts(
    trial_dir: Path,
    arm: str,
    mode: Literal["full", "first"],
    case_options: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    """Read and validate one unique turn-1 receipt per role from world artifacts."""
    label = f"{arm} trial"
    rel = Path("artifacts/logs/world/receipts.jsonl")
    raw_bytes = _read_no_symlink(trial_dir, rel, label)
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label}: {rel.as_posix()} is not valid UTF-8") from exc

    lines = text.splitlines()
    if not lines:
        raise ValueError(f"{label}: {rel.as_posix()} contains no receipts")

    by_role: dict[str, dict[str, Any]] = {}
    for line_no, raw_line in enumerate(lines, 1):
        where = f"{label} {rel.as_posix()}:{line_no}"
        if not raw_line.strip():
            raise ValueError(f"{where}: blank line is not allowed in receipts.jsonl")
        try:
            item = decode(raw_line)
        except TapeError as exc:
            raise ValueError(f"{where}: invalid JSON: {exc}") from exc
        if not isinstance(item, dict) or item.keys() != _RECEIPT_FIELDS:
            raise ValueError(
                f"{where}: receipt must have exact fields "
                "role, turn, view, acknowledged_sha256, action"
            )
        receipt = cast(dict[str, Any], item)
        role = receipt["role"]
        if not isinstance(role, str) or role not in _ROLES:
            raise ValueError(f"{where}: unknown role {role!r}")
        if role in by_role:
            raise ValueError(f"{where}: duplicate receipt for role {role!r}")
        if type(receipt["turn"]) is not int or receipt["turn"] != 1:
            raise ValueError(f"{where}: receipt for {role!r} must have turn=1")

        expected_view = _expected_view(role, case_options[role], mode)
        if receipt["view"] != expected_view:
            raise ValueError(
                f"{where}: view for role {role!r} does not match expected {mode!r} view"
            )
        expected_options = cast(list[str], expected_view["options"])
        action = receipt["action"]
        if not isinstance(action, str) or action not in expected_options:
            raise ValueError(
                f"{where}: action {action!r} for role {role!r} is not in issued options "
                f"{expected_options!r}"
            )
        by_role[role] = receipt

    missing = [role for role in _ROLES if role not in by_role]
    if missing:
        raise ValueError(f"{label}: missing receipts for role(s): {', '.join(missing)}")
    return by_role


def _load_trial(
    trial_dir: Path,
    arm: str,
    case_options: dict[str, list[str]],
    case_sha256: str,
) -> _TrialData:
    if not trial_dir.is_dir():
        raise ValueError(f"{arm} trial path is not a directory: {trial_dir}")
    mode = _ARM_MODE[arm]
    native_trial_id, reward = _read_result(trial_dir, f"{arm} trial")
    receipts = _read_receipts(trial_dir, arm, mode, case_options)

    decisions: dict[str, Decision] = {}
    episodes: dict[str, Episode] = {}
    for role in _ROLES:
        receipt = receipts[role]
        try:
            frame = accepted_frame(
                turn=receipt["turn"],
                view=receipt["view"],
                acknowledged_sha256=receipt["acknowledged_sha256"],
                action=receipt["action"],
            )
            decisions[role] = decision_from_frame(
                key=f"receipt-board:{role}:{mode}",
                role=role,
                native_trial_id=native_trial_id,
                frame=frame,
                answers=_offline_answers(case_options[role]),
            )
            episodes[role] = episode_from_frames(
                id=f"receipt-board:{role}:{mode}",
                case_sha256=case_sha256,
                role=role,
                native_trial_id=native_trial_id,
                closed=True,
                frames=[frame],
            )
        except TapeError as exc:
            raise ValueError(f"{arm} trial role {role!r}: {exc}") from exc

    return {
        "native_trial_id": native_trial_id,
        "reward": reward,
        "decisions": decisions,
        "episodes": episodes,
    }


def _write_json(path: Path, value: object) -> None:
    if os.path.lexists(path):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
    data = canonical(value) + b"\n"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".seatlab-", suffix=".tmp", delete=False
        ) as output:
            temp_path = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _calibration_pairs(
    source: _TrialData,
    target: _TrialData,
    case_options: dict[str, list[str]],
    case_sha256: str,
) -> dict[str, object]:
    pairs: list[dict[str, object]] = []
    for role in _ROLES:
        # seatlab.calibration/v1 requires a distinct case_sha256 per pair entry; bind each
        # seat pair to the full case digest and its role-specific options.
        seat_case_sha256 = digest(
            {"case_sha256": case_sha256, "role": role, "options": case_options[role]}
        )
        src_row = source["decisions"][role]
        tgt_row = target["decisions"][role]
        pairs.append(
            {
                "id": f"receipt-board:{role}",
                "source": {
                    "key": src_row["key"],
                    "case_sha256": seat_case_sha256,
                    "native_trial_id": src_row["native_trial_id"],
                    "decision_sha256": decision_sha256(src_row),
                },
                "target": {
                    "key": tgt_row["key"],
                    "case_sha256": seat_case_sha256,
                    "native_trial_id": tgt_row["native_trial_id"],
                    "decision_sha256": decision_sha256(tgt_row),
                },
            }
        )
    return {"schema": _CALIBRATION_SCHEMA, "pairs": pairs}


def _episode_pairs(
    source: _TrialData,
    target: _TrialData,
    case_sha256: str,
) -> dict[str, object]:
    pairs: list[dict[str, object]] = []
    for role in _ROLES:
        src_ep = source["episodes"][role]
        tgt_ep = target["episodes"][role]
        pairs.append(
            {
                "id": f"receipt-board:{role}",
                "case_sha256": case_sha256,
                "role": role,
                "source": {
                    "native_trial_id": src_ep["native_trial_id"],
                    "episode_sha256": src_ep["episode_sha256"],
                },
                "target": {
                    "native_trial_id": tgt_ep["native_trial_id"],
                    "episode_sha256": tgt_ep["episode_sha256"],
                },
            }
        )
    return {"schema": _EPISODE_PAIRS_SCHEMA, "pairs": pairs}


def export_trials(
    source_trial: Path,
    target_trial: Path,
    out_dir: Path,
    *,
    case_path: Path = _DEFAULT_CASE_PATH,
) -> dict[str, object]:
    """Validate two native Harbor trial directories and publish a new export directory."""
    if os.path.lexists(out_dir):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), out_dir)
    if not out_dir.parent.is_dir():
        raise ValueError(f"output parent directory does not exist: {out_dir.parent}")

    case_options, case_sha256 = _load_case(case_path)
    source = _load_trial(source_trial, "source", case_options, case_sha256)
    target = _load_trial(target_trial, "target", case_options, case_sha256)

    if source["native_trial_id"] == target["native_trial_id"]:
        raise ValueError(
            "source and target trials must have distinct canonical native_trial_id UUIDs"
        )

    source_decisions = [source["decisions"][role] for role in _ROLES]
    all_decisions = [
        row
        for role in _ROLES
        for row in (source["decisions"][role], target["decisions"][role])
    ]
    source_episodes = [source["episodes"][role] for role in _ROLES]
    target_episodes = [target["episodes"][role] for role in _ROLES]
    pairs_manifest = _calibration_pairs(source, target, case_options, case_sha256)
    episode_pairs_manifest = _episode_pairs(source, target, case_sha256)
    summary: dict[str, object] = {
        "schema": _SUMMARY_SCHEMA,
        "case": {"options": case_options},
        "case_sha256": case_sha256,
        "roles": list(_ROLES),
        "source": {
            "mode": "full",
            "native_trial_id": source["native_trial_id"],
            "team_reward": source["reward"],
        },
        "target": {
            "mode": "first",
            "native_trial_id": target["native_trial_id"],
            "team_reward": target["reward"],
        },
    }

    # Stage all files in a sibling temporary directory and publish once complete.
    staging = Path(tempfile.mkdtemp(dir=out_dir.parent, prefix=".seatlab-export-"))
    try:
        write_lines(staging / "sources.jsonl", source_decisions)
        write_lines(staging / "decisions.jsonl", all_decisions)
        write_lines(staging / "source-episodes.jsonl", source_episodes)
        write_lines(staging / "target-episodes.jsonl", target_episodes)
        _write_json(staging / "pairs.json", pairs_manifest)
        _write_json(staging / "episode-pairs.json", episode_pairs_manifest)
        _write_json(staging / "summary.json", summary)
        if os.path.lexists(out_dir):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), out_dir)
        os.rename(staging, out_dir)
    except Exception:
        for child in staging.iterdir():
            child.unlink(missing_ok=True)
        staging.rmdir()
        raise

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_trial",
        metavar="SOURCE_TRIAL",
        type=Path,
        help="completed Harbor trial directory for the full-option source arm",
    )
    parser.add_argument(
        "target_trial",
        metavar="TARGET_TRIAL",
        type=Path,
        help="completed Harbor trial directory for the first-option target arm",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        metavar="NEW_DIRECTORY",
        help="new directory to publish exported SeatLab files into",
    )
    args = parser.parse_args(argv)
    try:
        summary = export_trials(args.source_trial, args.target_trial, args.out)
    except (ValueError, OSError, TapeError) as exc:
        print(f"export.py: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
