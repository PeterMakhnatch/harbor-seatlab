"""Recheck an independent SeatLab study against a pinned source panel for the GitHub Action.

Run as `python3 -I -S action/recheck.py`. Inputs arrive only through environment
variables. Only the standard library, the trusted `verify` module beside this file and
the SeatLab package in the same pinned action checkout are imported. Nothing from the
workspace is imported or executed, and no network is used.

Exit status: 0 matched, 1 changed, 3 incomplete, 2 refused input or runtime failure.
Failures print one fixed line and never echo paths, values or exception text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(_HERE))
from verify import _Reject, _append, _env, _manifest_path  # noqa: E402

_SHA256 = re.compile(r"[0-9a-f]{64}")
_STATUSES = ("matched", "changed", "incomplete")
_COUNT_KEYS = ("entries", "stable", "changed", "unknown")

_SUMMARY = (
    "### SeatLab panel recheck\n\n"
    "| Item | Result |\n| --- | --- |\n"
    "| Status | {status} |\n"
    "| Entries | {entries} |\n"
    "| Stable | {stable} |\n"
    "| Changed | {changed} |\n"
    "| Unknown | {unknown} |\n"
)


def _run() -> int:
    if sys.version_info < (3, 11):
        raise _Reject
    pins = [
        _env(name)
        for name in (
            "SEATLAB_EXPECTED_SHA256",
            "SEATLAB_EXPECTED_CANDIDATE_SHA256",
            "SEATLAB_EXPECTED_DESIGN_SHA256",
            "SEATLAB_EXPECTED_CANDIDATE_DESIGN_SHA256",
        )
    ]
    if any(_SHA256.fullmatch(pin) is None for pin in pins):
        raise _Reject
    workspace = Path(_env("GITHUB_WORKSPACE"))
    temp = Path(_env("RUNNER_TEMP")).resolve(strict=True)
    source = _manifest_path(workspace, _env("SEATLAB_SOURCE"))
    candidate = _manifest_path(workspace, _env("SEATLAB_CANDIDATE"))
    design = _manifest_path(workspace, _env("SEATLAB_DESIGN"))
    candidate_design = _manifest_path(workspace, _env("SEATLAB_CANDIDATE_DESIGN"))
    _env("GITHUB_OUTPUT")

    # Trusted code only: the SeatLab source in the pinned action checkout.
    sys.path.insert(0, str(_HERE.parent / "src"))
    from seatlab.recheck import recheck_exit_code, recheck_panel

    report = recheck_panel(
        source,
        candidate,
        design=design,
        candidate_design=candidate_design,
        expect_sha256=pins[0],
        expect_candidate_sha256=pins[1],
        expect_design_sha256=pins[2],
        expect_candidate_design_sha256=pins[3],
    )
    status = report["status"]
    counts = report["counts"]
    if status not in _STATUSES or not isinstance(counts, dict):
        raise _Reject
    numbers = {key: counts[key] for key in _COUNT_KEYS}
    if any(type(value) is not int for value in numbers.values()):
        raise _Reject
    code = recheck_exit_code(report)

    # Never clean an externally named directory. Expose our unique private directory
    # only after the complete file is closed.
    final = Path(tempfile.mkdtemp(prefix="seatlab-recheck-", dir=temp))
    artifact = final / "recheck.json"
    try:
        fd = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
        _append(
            "GITHUB_OUTPUT",
            f"status={status}\nreport={artifact}\nentries={numbers['entries']}\n",
        )
    except BaseException:
        shutil.rmtree(final, ignore_errors=True)
        raise
    try:
        _append("GITHUB_STEP_SUMMARY", _SUMMARY.format(status=status, **numbers))
    except OSError:
        pass  # the summary is optional; the report and outputs are already published
    return code


def main() -> int:
    try:
        return _run()
    except Exception:
        print("::error::SeatLab panel recheck refused.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
