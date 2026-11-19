"""Verify an optional, separately trusted launcher identity registry.

The registry only binds logical job ids to the native UUIDs a launcher issued. It
does not attest the policy binary or that the worker executed honestly.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from .study_contract import valid_sha256
from .study_inputs import read_document
from .tape import TapeError, digest

SCHEMA = "seatlab.launch-registry/v1"


def _canonical_uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def verify_launch_registry(
    path: Path,
    expected_sha256: str,
    worklist_sha256: str,
    expected_jobs: set[str],
    source_uuids: set[str],
    received: dict[str, str],
) -> None:
    """Raise TapeError unless the pinned registry matches the plan and returned folders."""
    if not valid_sha256(expected_sha256):
        raise TapeError("expected launch registry sha256 must be lowercase hex")
    doc = read_document(path)
    if digest(doc) != expected_sha256:
        raise TapeError("launch registry digest does not match the pinned digest")
    if type(doc) is not dict or set(doc) != {"schema", "worklist_sha256", "trials"}:
        raise TapeError("launch registry must have exactly schema, worklist_sha256, trials")
    if doc["schema"] != SCHEMA:
        raise TapeError("launch registry schema is unsupported")
    if doc["worklist_sha256"] != worklist_sha256:
        raise TapeError("launch registry belongs to a different worklist")
    trials = doc["trials"]
    if type(trials) is not dict or set(trials) != expected_jobs:
        raise TapeError("launch registry jobs do not exactly match the plan")
    seen: set[str] = set()
    for job, uuid in trials.items():
        if uuid is None:
            continue
        if not _canonical_uuid(uuid):
            raise TapeError(f"launch registry job {job} has a non-canonical native UUID")
        if uuid in seen:
            raise TapeError(f"launch registry reuses native UUID {uuid}")
        if uuid in source_uuids:
            raise TapeError(f"launch registry UUID for job {job} collides with the source")
        seen.add(uuid)
    for job, uuid in received.items():
        if job not in trials:
            raise TapeError(f"received job {job} is not in the launch registry")
        if trials[job] is None or trials[job] != uuid:
            raise TapeError(f"received job {job} does not match its launch registry UUID")
