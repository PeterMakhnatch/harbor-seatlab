"""Read launcher receipts, never world/role evidence, to pin expected Hanabi assignments."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from uuid import UUID

from seatlab.study_inputs import read_document
from seatlab.tape import TapeError, canonical, digest


def _require(test, message):
    if not test:
        raise TapeError(message)


def _dir(path):
    _require(path.is_dir() and not path.is_symlink(), f"not a real directory: {path}")


def _object(path):
    value = read_document(path)
    _require(isinstance(value, dict), f"not an object: {path}")
    return value


def _id(record, job, name):
    _require(record.get("trial_name") == name, f"wrong native trial name for {job}")
    ident = record.get("id")
    _require(
        isinstance(ident, str) and str(UUID(ident)) == ident,
        f"invalid native trial id for {job}",
    )
    return ident


def _name(name, job):
    case, arm = job.split(".")
    _require(
        isinstance(name, str)
        and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
        and len(name) <= 128
        and name.endswith(f"-{case}-{arm}"),
        f"wrong launch name for {job}",
    )
    return name


def _from_native(root, jobs):
    _dir(root / "configs")
    _dir(root / "trials")
    result = {}
    for job in jobs:
        case, arm = job.split(".")
        name = _name(
            _object(root / "configs" / f"{case}-{arm.upper()}.json").get("trial_name"), job
        )
        folder = root / "trials" / name
        if folder.exists() or folder.is_symlink():
            _dir(folder)
        try:
            record = _object(folder / "result.json")
        except FileNotFoundError:
            result[job] = None
        else:
            result[job] = _id(record, job, name)
    return result


def _from_receipts(root, jobs):
    result = dict.fromkeys(jobs)
    for folder in root.iterdir():
        job = folder.name.removeprefix("launch-")
        _require(
            folder.name == f"launch-{job}" and job in result,
            f"unexpected launcher folder: {folder.name}",
        )
        _dir(folder)
        _require(
            {p.name for p in folder.iterdir()} == {"launch.json"},
            f"extra or missing launcher file for {job}",
        )
        record = _object(folder / "launch.json")
        _require(
            set(record) == {"schema", "job", "id", "trial_name"}
            and record["schema"] == "seatlab.launch-receipt/v1"
            and record["job"] == job,
            f"incorrect receipt for {job}",
        )
        result[job] = _id(record, job, _name(record["trial_name"], job))
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("mode", choices=("local", "receipts"))
    cli.add_argument(
        "root", type=Path, help="stock run root or separately downloaded launcher receipts"
    )
    cli.add_argument("worklist", type=Path)
    cli.add_argument("--out", type=Path, required=True, help="new registry file")
    args = cli.parse_args()
    try:
        _dir(args.root)
        wl = _object(args.worklist)
        _require(
            wl.get("schema") == "seatlab.native-worklist/v1"
            and isinstance(wl.get("jobs"), list),
            "wrong worklist",
        )
        jobs = []
        for row in wl["jobs"]:
            _require(
                isinstance(row, dict)
                and row.get("case") == "d00"
                and row.get("arm") in ("ll", "lr", "rl", "rr")
                and row.get("id") == "d00." + row["arm"]
                and row["id"] not in jobs,
                "unexpected Hanabi work order",
            )
            jobs.append(row["id"])
        _require(bool(jobs), "no scheduled jobs")
        trials = (_from_native if args.mode == "local" else _from_receipts)(args.root, jobs)
        assigned = [ident for ident in trials.values() if ident is not None]
        _require(len(assigned) == len(set(assigned)), "native UUID assigned twice")
        registry = {
            "schema": "seatlab.launch-registry/v1",
            "worklist_sha256": digest(wl),
            "trials": trials,
        }
        with args.out.open("xb") as output:
            output.write(canonical(registry) + b"\n")
    except (OSError, ValueError) as error:
        print(f"launcher registry: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"launch_registry_sha256": digest(registry)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
