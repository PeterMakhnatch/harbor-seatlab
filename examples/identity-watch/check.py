#!/usr/bin/env python3
"""Seatlab native-identity watch: does upstream HEAD (or upstream + proposal) pass the frozen seat test?"""

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

PIN = "0e2b6a1289681996a691bbecec4448eaada083e8"
TEST_REL = "tests/unit/trial/test_seats.py"
TEST_NAME = "test_seat_trial_retains_reserved_id_in_result"
EXPECTED = ("assert result.id == reserved_id", "assert saved_result.id == reserved_id")
TIMEOUT = 120
RUNNER = (
    "import sys, pathlib\n"
    "src = pathlib.Path(sys.argv[1]).resolve()\n"
    "sys.path.insert(0, str(src))\n"
    "import harbor.trial.trial as t\n"
    "if src not in pathlib.Path(t.__file__).resolve().parents:\n"
    "    sys.exit(97)\n"
    "import pytest\n"
    "sys.exit(int(pytest.main(sys.argv[2:])))\n"
)
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def git(repo, *args, **kw):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        env=GIT_ENV,
        timeout=TIMEOUT,
        **kw,
    )


def rev(repo, ref="HEAD"):
    p = git(repo, "rev-parse", "--verify", ref + "^{commit}")
    if p.returncode:
        raise ValueError("not a git commit")
    return p.stdout.decode().strip()


def extract(repo, treeish, dest):
    # This proposal changes the top-level Harbor package only. Do not copy its full history or task assets.
    with tempfile.TemporaryFile() as archive:
        subprocess.run(
            ["git", "-C", str(repo), "archive", treeish, "--", "src", "pyproject.toml"],
            stdout=archive,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
            check=True,
        )
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r:") as tf:
            tf.extractall(dest, filter="data")


def classify(rc, junit):
    """Return 'pass', 'uuid_mismatch' or 'inconclusive' from a pytest run."""
    try:
        if junit.stat().st_size > 1_048_576:
            return "inconclusive"
        cases = list(ET.parse(junit).getroot().iter("testcase"))
    except (OSError, ET.ParseError):
        return "inconclusive"
    if (
        len(cases) != 1
        or cases[0].get("name") != TEST_NAME
        or not (cases[0].get("classname") or "").endswith("test_seats")
    ):
        return "inconclusive"
    kids = [c.tag for c in cases[0]]
    if rc == 0 and not any(k in ("failure", "error", "skipped") for k in kids):
        return "pass"
    if (
        rc == 1
        and kids.count("failure") == 1
        and "error" not in kids
        and "skipped" not in kids
    ):
        f = cases[0].find("failure")
        text = (f.get("message") or "") + "\n" + (f.text or "")
        if (
            any(line.strip() in EXPECTED for line in text.splitlines())
            and "00000000-0000-0000-0000-000000000001" in text
        ):
            return "uuid_mismatch"
    return "inconclusive"


def run_test(root, test_bytes, python, work):
    """Run the frozen test against the source tree at `root`; return a classification."""
    work.mkdir()
    (work / "test_seats.py").write_bytes(test_bytes)
    junit = work / "junit.xml"
    args = [
        str(python),
        "-I",
        "-B",
        "-c",
        RUNNER,
        str(root / "src"),
        "-c",
        str(root / "pyproject.toml"),
        "--rootdir",
        str(work),
        "-p",
        "no:cacheprovider",
        "--tb=short",
        "--junitxml",
        str(junit),
        f"{work / 'test_seats.py'}::{TEST_NAME}",
    ]
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(work), "LC_ALL": "C.UTF-8"}
    try:
        p = subprocess.run(
            args,
            cwd=work,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "inconclusive"
    state = classify(p.returncode, junit)
    if state == "inconclusive":
        print(
            f"Could not classify the single frozen test (pytest exit {p.returncode}).",
            file=sys.stderr,
        )
    return state


def evaluate(upstream, proposal, python, scratch, receipt):
    receipt["upstream_sha"] = up_sha = rev(upstream)
    receipt["proposal_sha"] = rev(proposal)
    if receipt["proposal_sha"] != PIN:
        raise ValueError("proposal is not the pinned checkout")
    shown = git(proposal, "show", f"{PIN}:{TEST_REL}")
    if shown.returncode:
        raise ValueError("frozen test missing")
    test_bytes = shown.stdout

    extract(upstream, up_sha, scratch / "base")
    receipt["baseline"] = base = run_test(
        scratch / "base", test_bytes, python, scratch / "work-base"
    )
    if base == "pass":
        return "upstream_satisfies"
    if base != "uuid_mismatch":
        return "inconclusive"

    if (
        git(upstream, "fetch", "--no-tags", "--quiet", str(proposal), "HEAD").returncode
        or rev(upstream, "FETCH_HEAD") != PIN
    ):
        return "inconclusive"
    m = git(upstream, "merge-tree", "--write-tree", up_sha, PIN)
    if m.returncode == 1:
        return "patch_conflict"
    tree = m.stdout.decode().split("\n", 1)[0].strip()
    if m.returncode or len(tree) < 40:
        return "inconclusive"
    receipt["prospective_tree"] = tree
    extract(upstream, tree, scratch / "cand")
    receipt["candidate"] = cand = run_test(
        scratch / "cand", test_bytes, python, scratch / "work-cand"
    )
    return {"pass": "patch_fixes", "uuid_mismatch": "still_fails"}.get(cand, "inconclusive")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("upstream")
    ap.add_argument("proposal")
    ap.add_argument("test_python")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    receipt = {
        "schema": "seatlab.native-identity-watch/v1",
        "upstream_sha": None,
        "proposal_sha": None,
        "prospective_tree": None,
        "baseline": None,
        "candidate": None,
        "status": "inconclusive",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="seatlab-watch-") as d:
            receipt["status"] = evaluate(
                Path(a.upstream).resolve(),
                Path(a.proposal).resolve(),
                Path(a.test_python).absolute(),
                Path(d),
                receipt,
            )
    except (
        OSError,
        ValueError,
        EOFError,
        tarfile.TarError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"Cannot exercise native contract: {type(exc).__name__}", file=sys.stderr)
        receipt["status"] = "inconclusive"
    try:
        with open(a.out, "x", encoding="utf-8") as fh:
            json.dump(receipt, fh, indent=2, sort_keys=True)
            fh.write("\n")
    except OSError:
        return 2
    print(receipt["status"])
    return {
        "upstream_satisfies": 0,
        "patch_fixes": 0,
        "still_fails": 1,
        "patch_conflict": 1,
    }.get(receipt["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
