"""Split verified native Hanabi episodes by trial and recover order from world events.

Input: a private native run root, its collect.py JSON report, and its combined episode
export. Output: a NEW flat folder suitable for prepare.py candidate. Nothing runs a world.
"""

from __future__ import annotations

import argparse
import json
import re
import runpy
import shutil
from collections import defaultdict
from pathlib import Path

from seatlab.episodes import read_episodes
from seatlab.tape import write_lines


def export(root: Path, report_path: Path, episodes: Path, out: Path) -> None:
    report = json.loads(report_path.read_bytes())
    if report.get("schema") != "seatlab.hanabi-collect/v1":
        raise ValueError("expected a native Hanabi collector report")
    groups = defaultdict(list)
    for episode in read_episodes(episodes):
        groups[episode["native_trial_id"]].append(episode)
    verified = [
        r for r in report["cells"] if r["status"] in ("matched", "valid_prediction_differs")
    ]
    ids = [r["native_trial_id"] for r in verified]
    if not ids or len(set(ids)) != len(ids) or set(ids) != groups.keys():
        raise ValueError("measured cells and distinct exported trial UUIDs must agree")
    world_order = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "world-order" / "from-hanabi.py")
    )["build_order"]
    out.mkdir(mode=0o700)
    try:
        for row in verified:
            deck, arm, uid, name = (
                row[k] for k in ("deck_id", "pairing", "native_trial_id", "trial_name")
            )
            if (
                type(deck) is not int
                or not 0 <= deck < 8
                or arm not in ("LL", "LR", "RL", "RR")
            ):
                raise ValueError("unexpected native case or team")
            if not isinstance(name, str) or not re.fullmatch(
                r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}", name
            ):
                raise ValueError("invalid trial directory name")
            if {ep["role"] for ep in groups[uid]} != {"p0", "p1"}:
                raise ValueError("both Hanabi seats must have an exported episode")
            stem = f"d{deck:02}-{arm}"
            path = out / f"{stem}.jsonl"
            write_lines(path, groups[uid])
            order = world_order(
                path, trial_dir=root / "trials" / name, hanabi_log=None, native_trial_id=uid
            )
            write_lines(out / f"{stem}.order.json", [order])
    except BaseException:
        shutil.rmtree(out)
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("native_root", type=Path)
    p.add_argument("report", type=Path)
    p.add_argument("episodes", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    try:
        export(a.native_root, a.report, a.episodes, a.out)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        p.exit(2, f"{p.prog}: {exc}\n")
