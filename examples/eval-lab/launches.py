"""Write a seatlab.launch-registry/v1 from a coordinator's job -> native trial mapping.

Run in the Eval Lab Python environment. Imports only ``evallab.results.load_trial`` (no
SeatLab). Reads the worklist, the private ASSIGNMENTS mapping, and each mapped trial's
native ``result.json`` ``id``. Reads no behavior/role data, no logs, and does not use
the native reward. Stdout is only the registry SHA-256 and counts; paths, names and
payloads are never printed.

This is not independent attestation of the policy binary or of launcher honesty: it
only records the native UUIDs the trusted coordinator says were issued.

Usage: python launches.py WORKLIST ASSIGNMENTS --expect-worklist-sha256 HEX --out NEW
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from uuid import UUID

SCHEMA = "seatlab.launch-registry/v1"
WORKLIST_SCHEMA = "seatlab.native-worklist/v1"
MAX_BYTES = 2 * 1024 * 1024
MAX_NATIVE_BYTES = 64 * 1024 * 1024
_JOB = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class Reject(Exception):
    pass


def need(ok: object, reason: str = "invalid input shape") -> None:
    if not ok:
        raise Reject(reason)


def _pairs(items):
    out = {}
    for key, value in items:
        need(key not in out, "duplicate JSON key")
        out[key] = value
    return out


def _bad_constant(_name):
    raise Reject("non-finite JSON number")


def regular(path: Path) -> os.stat_result:
    """lstat a regular, non-symlink file."""
    info = os.lstat(path)
    need(stat.S_ISREG(info.st_mode))
    return info


def read_regular(path: Path, limit: int) -> bytes:
    need(regular(path).st_size <= limit, "input exceeds size limit")
    fd = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(fd, "rb") as stream:
        need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "not a regular file")
        data = stream.read(limit + 1)
    need(len(data) <= limit, "input exceeds size limit")
    return data


def decode(data: bytes) -> object:
    return json.loads(
        data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant
    )


def read_json(path: Path) -> object:
    return decode(read_regular(path, MAX_BYTES))


def canonical(value: object) -> bytes:
    # ensure_ascii=True matches SeatLab's digest(); registry content is ASCII-only anyway.
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def native_id(directory: Path, stage: Path) -> str:
    from evallab.results import load_trial

    need(stat.S_ISDIR(os.lstat(directory).st_mode), "not a native trial directory")
    # Pass stock Eval Lab one exact result snapshot, never companion logs, manifests or paths.
    raw = read_regular(directory / "result.json", MAX_NATIVE_BYTES)
    doc = decode(raw)
    need(isinstance(doc, dict) and isinstance(doc.get("id"), str), "no explicit native UUID")
    (stage / "result.json").write_bytes(raw)
    record = load_trial(stage)
    value = doc["id"]
    need(value == str(record.id) and str(UUID(value)) == value, "incorrect native UUID")
    return value


def build(worklist_path: Path, assignments_path: Path, expect: str) -> tuple[dict, str, int]:
    need(_SHA.fullmatch(expect))
    worklist = read_json(worklist_path)
    need(isinstance(worklist, dict) and worklist.get("schema") == WORKLIST_SCHEMA)
    need(
        hashlib.sha256(canonical(worklist)).hexdigest() == expect, "worklist digest mismatch"
    )
    rows = worklist.get("jobs")
    need(isinstance(rows, list) and rows)
    jobs = []
    for row in rows:
        need(
            isinstance(row, dict) and type(row.get("id")) is str and _JOB.fullmatch(row["id"])
        )
        jobs.append(row["id"])
    need(len(set(jobs)) == len(jobs))
    assignments = read_json(assignments_path)
    need(
        isinstance(assignments, dict) and set(assignments) == set(jobs),
        "assigned job set differs from worklist",
    )
    base = assignments_path.parent
    trials: dict[str, str | None] = {}
    with tempfile.TemporaryDirectory(prefix="seatlab-native-id-") as scratch:
        stage = Path(scratch)
        for job in jobs:
            target = assignments[job]
            if target is None:
                trials[job] = None
                continue
            need(type(target) is str and target and "\0" not in target)
            trials[job] = native_id(base / target, stage)
    ids = [value for value in trials.values() if value is not None]
    need(len(set(ids)) == len(ids), "native UUID assigned twice")
    registry = {"schema": SCHEMA, "worklist_sha256": expect, "trials": trials}
    return registry, hashlib.sha256(canonical(registry)).hexdigest(), len(ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("worklist", type=Path)
    parser.add_argument("assignments", type=Path)
    parser.add_argument("--expect-worklist-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True, help="new registry file")
    args = parser.parse_args()
    out = None
    try:
        registry, sha, assigned = build(
            args.worklist, args.assignments, args.expect_worklist_sha256
        )
        payload = canonical(registry) + b"\n"
        fd = os.open(
            args.out,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        out = args.out
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
    except (
        Exception
    ) as exc:  # Omit paths, private record fields and exception text from other libraries.
        if out is not None:
            try:
                os.unlink(out)
            except OSError:
                pass
        message = (
            str(exc)
            if isinstance(exc, Reject)
            else (
                "stock Eval Lab Python environment required"
                if isinstance(exc, ImportError)
                else "cannot read native result or create new output"
            )
        )
        print(f"launches: {message}", file=sys.stderr)
        return 2
    total = len(registry["trials"])
    print(
        json.dumps(
            {
                "launch_registry_sha256": sha,
                "jobs": total,
                "assigned": assigned,
                "unassigned": total - assigned,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
