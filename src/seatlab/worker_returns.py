"""Take a bounded snapshot of complete worker folders, including unpaired returns."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from . import tape as wire
from .study_inputs import MAX_DOCUMENT_BYTES, snapshot
from .world_order import read_world_order

_FILES = ("episodes.jsonl", "order.json")


def _layout(root: Path, expected: dict[str, str]) -> dict[str, tuple[int, ...]]:
    """Check the exact return tree; record metadata so ordinary concurrent edits refuse."""
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise wire.TapeError("worker returns must be a directory, not a file or symlink")
    result = {}
    with os.scandir(root) as entries:
        for entry in entries:
            job = entry.name
            if job not in expected or not entry.is_dir(follow_symlinks=False):
                raise wire.TapeError(f"unexpected worker return: {job!r}")
            leaves = set()
            with os.scandir(entry.path) as files:
                for item in files:
                    leaf = item.name
                    if leaf not in _FILES or not item.is_file(follow_symlinks=False):
                        raise wire.TapeError(f"unexpected native return file: {job}/{leaf}")
                    info = item.stat(follow_symlinks=False)
                    result[f"{job}/{leaf}"] = (
                        info.st_dev,
                        info.st_ino,
                        info.st_size,
                        info.st_mtime_ns,
                        info.st_ctime_ns,
                    )
                    leaves.add(leaf)
            if len(leaves) != len(_FILES):
                raise wire.TapeError(f"worker {job!r} returned an incomplete file set")
    return result


def snapshot_returns(
    root: Path, case_by_job: dict[str, str], destination: Path, source_uuids: set[str]
) -> dict[str, str]:
    """Verify each received trial against its assigned native case, then move the private copy.

    The caller owns the empty private destination and removes it on failure. The producer
    must finish publishing its folders before collection. This checks consistency, not the
    identity of the worker, executable, or recorder.
    """
    before = _layout(root, case_by_job)
    seen = set(source_uuids)
    trials = {}
    with snapshot(root, sorted(before)) as (private, receipts):
        if _layout(root, case_by_job) != before:
            raise wire.TapeError("native returns changed during collection")
        for job, case_digest in sorted(case_by_job.items()):
            key = f"{job}/order.json"
            if key not in receipts:
                continue
            if receipts[key]["bytes"] > MAX_DOCUMENT_BYTES:
                raise wire.TapeError(f"worker {job!r} order is too large")
            path = private / job
            world = read_world_order(path / "episodes.jsonl", path / "order.json")
            if world.case_sha256 != case_digest:
                raise wire.TapeError(f"worker {job!r} returned the wrong native case")
            uid = world.native_trial_id
            if uid in seen:
                raise wire.TapeError(f"worker {job!r} reuses a native trial UUID")
            seen.add(uid)
            trials[job] = uid
            shutil.move(str(path), str(destination / job))
    return trials
