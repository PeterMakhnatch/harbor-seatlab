"""Make a Hanabi matrix from the pinned v0.9 source archive or fresh world-ordered trials.

source ARCHIVE --out NEW_FOLDER
candidate FLAT --decks 0 [more...] --out NEW_FOLDER
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import stat
import tarfile
from pathlib import Path

PIN = "ddb23f1fde776eb833e3f4671d53bb7523ce6df02b242e6f190a1417cb24cd29"
DECKS = tuple(f"d{n:02}" for n in range(8))
ARMS = ("ll", "lr", "rl", "rr")
LEAVES = (("jsonl", "episodes.jsonl"), ("order.json", "order.json"))
CONTRASTS = {
    "ll-lr-p0": ("ll", "lr", "p0"),
    "ll-rl-p1": ("ll", "rl", "p1"),
    "rr-rl-p0": ("rr", "rl", "p0"),
    "rr-lr-p1": ("rr", "lr", "p1"),
}
DIMENSIONS = (("view", "raw_view"), ("action", "accepted_action"))


def matrix_doc(selected):
    criteria = []
    for name in CONTRASTS:
        for tag, dim in DIMENSIONS:
            criteria.append(
                {
                    "id": "%s-%s-differs" % (name, tag),
                    "contrast": name,
                    "dimension": dim,
                    "when": "different",
                }
            )
    cases = {}
    for d in DECKS:
        cases[d] = {a: ("cells/%s/%s" % (d, a) if d in selected else None) for a in ARMS}
    return {
        "schema": "seatlab.panel-matrix/v1",
        "contrasts": {
            n: {"source": s, "target": t, "role": r} for n, (s, t, r) in CONTRASTS.items()
        },
        "criteria": criteria,
        "cases": cases,
    }


def materialize(source: Path, out: Path, selected: set[str], *, archive: bool) -> None:
    if not selected or not selected.issubset(DECKS):
        raise ValueError("select at least one deck numbered 0 to 7")
    tar = None
    if archive:
        if not stat.S_ISREG(source.lstat().st_mode) or source.stat().st_size != 92600:
            raise ValueError("expected the 92600-byte v0.9.0 native-world-order.tar.gz")
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != PIN:
            raise ValueError("source archive does not match the registered SHA-256")
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    try:
        out.mkdir(mode=0o700)
        try:
            for deck in sorted(selected):
                for arm in ARMS:
                    cell = out / "cells" / deck / arm
                    cell.mkdir(parents=True)
                    for suffix, leaf in LEAVES:
                        name = f"{deck}-{arm.upper()}.{suffix}"
                        if tar is None:
                            path = source / name
                            s = path.lstat()
                            if not stat.S_ISREG(s.st_mode) or s.st_size > 64 * 1024 * 1024:
                                raise ValueError(f"expected a regular trial file: {name}")
                            shutil.copyfile(path, cell / leaf)
                        else:
                            member = tar.getmember("hanabi/" + name)
                            if not member.isreg():
                                raise ValueError(f"expected a regular archive member: {name}")
                            with (
                                tar.extractfile(member) as src,
                                (cell / leaf).open("xb") as dst,
                            ):
                                shutil.copyfileobj(src, dst)
            (out / "matrix.json").write_text(
                json.dumps(matrix_doc(selected), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except BaseException:
            shutil.rmtree(out)
            raise
    finally:
        if tar is not None:
            tar.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_subparsers(dest="mode", required=True)
    for name in ("source", "candidate"):
        command = modes.add_parser(name)
        command.add_argument("input", type=Path)
        command.add_argument("--out", type=Path, required=True)
        if name == "candidate":
            command.add_argument(
                "--decks", required=True, nargs="+", type=int, choices=range(8)
            )
    a = p.parse_args()
    selected = set(DECKS) if a.mode == "source" else {DECKS[n] for n in a.decks}
    try:
        materialize(a.input, a.out, selected, archive=a.mode == "source")
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        p.exit(2, f"{p.prog}: {exc}\n")
