"""Check three native PettingZoo cells. Payoff is not completion."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

ALLOWED = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONTEXT")
CELLS = (
    ("rps-copy-beat", "classic", "rps", {"p0": "rps-copy", "p1": "rps-beat-last"}, "joint"),
    ("connect-lh", "classic", "connect-four", {"p0": "low", "p1": "high"}, "alternating"),
    ("spread-llh", "particle", "", {"p0": "low", "p1": "low", "p2": "high"}, "joint"),
)
BANNED = ("/users/", "/home/", "/tmp/", "github.", "runner_temp", "${{")


def die(msg):
    print(f"check.py: {msg}", file=sys.stderr)
    raise SystemExit(1)


def env():
    return {key: os.environ[key] for key in ALLOWED if key in os.environ}


def load(path):
    if path.is_symlink() or not path.is_file():
        die(path.name)
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else die(path.name)


def groups(steps):
    if not isinstance(steps, list) or not steps:
        die("steps")
    out = []
    for group in steps:
        if (
            not isinstance(group, list)
            or not group
            or any(type(role) is not str for role in group)
        ):
            die("steps")
        out.append(sorted(group))
    return out


def live(cmd):
    proc = subprocess.run(cmd, env=env(), capture_output=True, check=False)
    if proc.returncode:
        last = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        die(last[-1] if last and last[-1].startswith("mismatch:") else "reference failed")
    try:
        value = json.loads(proc.stdout.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        die("reference")
    return value if isinstance(value, dict) else die("reference")


def near(left, right):
    nums = (
        type(left) in (int, float)
        and type(right) in (int, float)
        and type(left) is not bool
        and type(right) is not bool
    )
    return nums and math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-7)


def native(classic, particle, adapter, out):
    if out.exists() or not out.parent.is_dir():
        die("output")
    out.mkdir()
    (out / "references").mkdir()
    (out / "projections").mkdir()
    rows, seen, trials = [], set(), set()
    roots = {"classic": classic, "particle": particle}
    schemas = {"classic": "seatlab.pz-plan/v1", "particle": "seatlab.pz-particle-plan/v1"}
    for cid, suite, game, policies, group in CELLS:
        plan, claim = (
            load(roots[suite] / "plan.json"),
            load(roots[suite] / "attempts" / cid / "exit.json"),
        )
        hits = [
            cell
            for cell in plan.get("cells", [])
            if isinstance(cell, dict) and cell.get("id") == cid
        ]
        name = hits[0].get("trial_name") if len(hits) == 1 else None
        bad_name = not isinstance(name, str) or "/" in name or name in ("", ".", "..")
        if (
            plan.get("schema") != schemas[suite]
            or len(hits) != 1
            or hits[0].get("policies") != policies
            or bad_name
            or claim.get("exit_code") != 0
            or claim.get("trial_name") != name
        ):
            die(cid)
        trial = roots[suite] / "trials" / name
        world = trial / "artifacts" / "logs" / "world" / "pettingzoo.json"
        if (
            trial.is_symlink()
            or not trial.is_dir()
            or world.is_symlink()
            or not world.is_file()
        ):
            die(cid)
        folder = out / "projections" / cid
        if subprocess.run(
            [sys.executable, str(adapter / "export.py"), str(trial), str(folder)],
            env=env(),
            capture_output=True,
        ).returncode:
            die("export failed")
        outcomes, order = load(folder / "outcomes.json"), load(folder / "order.json")
        tid, steps, roles = (
            outcomes.get("native_trial_id"),
            groups(order.get("steps")),
            set(policies),
        )
        if (
            not isinstance(tid, str)
            or len(tid) != 36
            or tid in trials
            or order.get("native_trial_id") != tid
            or outcomes.get("completion") != 1
            or set(outcomes.get("returns") or {}) != roles
        ):
            die("trial")
        trials.add(tid)
        script = "reference.py" if game else "particle_reference.py"
        cmd = [sys.executable, str(adapter / script), game or str(world)]
        cmd += (
            [
                "--policies",
                f"{policies['p0']},{policies['p1']}",
                "--seed",
                "19",
                "--report",
                str(world),
            ]
            if game
            else ["--p2", policies["p2"]]
        )
        ref = live(cmd)
        if game:
            ends = ref.get("end_states") if isinstance(ref.get("end_states"), dict) else {}
            matched = (
                ref.get("schema") == "seatlab.pz-reference/v1"
                and ref.get("seed") == 19
                and ref.get("policies") == policies
                and ref.get("case_sha256") == outcomes.get("case_sha256")
                and ref.get("returns") == outcomes["returns"]
                and ends.get("terminated") == outcomes.get("terminated")
                and ends.get("truncated") == outcomes.get("truncated")
                and groups(ref.get("groups")) == steps
                and ref.get("step_count") == len(steps)
            )
            joint = ref.get("grouping") == "simultaneous" and steps == [["p0", "p1"]] * 6
            alt = (
                ref.get("grouping") == "turn-taking"
                and all(len(item) == 1 for item in steps)
                and all(steps[i] != steps[i + 1] for i in range(len(steps) - 1))
            )
            if (
                not matched
                or (group == "joint" and not joint)
                or (group == "alternating" and not alt)
            ):
                die("reference")
        else:
            returns = ref.get("returns") if isinstance(ref.get("returns"), dict) else {}
            term, cut = outcomes.get("terminated"), outcomes.get("truncated")
            close = all(
                near(outcomes["returns"].get(role), returns.get(role)) for role in roles
            )
            limited = (
                isinstance(term, dict)
                and isinstance(cut, dict)
                and all(term.get(role) is False and cut.get(role) is True for role in roles)
            )
            gaps = all(
                type(ref.get(key)) is not bool
                and isinstance(ref.get(key), (int, float))
                and math.isfinite(ref[key])
                for key in ("max_abs_observation_difference", "max_abs_return_difference")
            )
            if (
                ref.get("steps") != 4
                or ref.get("decisions") != 12
                or steps != [["p0", "p1", "p2"]] * 4
                or term != ref.get("terminated")
                or cut != ref.get("truncated")
                or not (close and limited and gaps)
            ):
                die("reference")
        mapped, found = order.get("episodes"), {}
        for line in (folder / "episodes.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            found[row.get("role")] = row
        if not isinstance(mapped, dict) or set(found) != roles or set(mapped) != roles:
            die("traces")
        for role, row in found.items():
            ident = f"pz:{tid}:{role}"
            real = (
                row.get("id") == ident
                and ident not in seen
                and row.get("native_trial_id") == tid
                and row.get("closed") is True
                and row.get("schema") == "seatlab.episode/v1"
                and isinstance(row.get("frames"), list)
                and row["frames"]
                and row.get("episode_sha256") == mapped[role]
            )
            if not real:
                die("traces")
            seen.add(ident)
        (out / "references" / f"{cid}.json").write_text(
            json.dumps(ref, sort_keys=True, allow_nan=False, ensure_ascii=True) + "\n",
            encoding="ascii",
        )
        rows.append(
            {
                "id": cid,
                "trial": tid,
                "completion": 1,
                "steps": steps,
                "returns": outcomes["returns"],
                "group": group,
            }
        )
    (out / "receipt.json").write_text(
        json.dumps(
            {"payoff_is_not_completion": True, "cells": rows}, sort_keys=True, allow_nan=False
        )
        + "\n",
        encoding="ascii",
    )
    for path in out.rglob("*"):
        if path.is_symlink() or (
            path.is_file()
            and any(part in path.read_text(encoding="utf-8").lower() for part in BANNED)
        ):
            die("unsafe projection")
    print(
        json.dumps({"payoff_is_not_completion": True, "cells": [row["id"] for row in rows]})
    )


def main():
    if (
        len(sys.argv) == 8
        and sys.argv[1] == "native"
        and sys.argv[4] == "--adapter"
        and sys.argv[6] == "--out"
    ):
        native(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[5]), Path(sys.argv[7]))
        return 0
    die("usage")


if __name__ == "__main__":
    raise SystemExit(main())
