"""Verify a local SeatLab study for the composite GitHub Action.

Run as `python3 -I -S action/verify.py`. Inputs arrive only through environment
variables. The only code imported besides the standard library is the SeatLab
package that ships next to this file in the pinned action checkout. Nothing from
the workspace is imported or executed, and no network is used.

Failures print one fixed line and never echo study data, paths or exception text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_PATH = 1024

_SUMMARY = (
    "### SeatLab study verification\n\n"
    "| Item | Result |\n| --- | --- |\n"
    "| Verification | {verification} |\n"
    "| Verified checks | {checks} |\n"
    "| Experiment outcome | Not assessed by this action |\n\n"
    "Verification means the recorded files and analyses reproduce and the manifest "
    "matches the trusted pin. It does not mean a policy or experiment succeeded.\n"
)


class _Reject(Exception):
    """Input or runtime refusal. Carries no data on purpose."""


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise _Reject
    return value


def _manifest_path(workspace: Path, text: str) -> Path:
    if len(text) > _MAX_PATH or text.startswith("/") or "\\" in text:
        raise _Reject
    parts = text.split("/")
    for part in parts:
        if part in ("", ".", "..") or any(ord(c) < 32 or ord(c) == 127 for c in part):
            raise _Reject
    root = workspace.resolve(strict=True)
    lexical = root.joinpath(*parts)
    # Reject any symlink in the path: the resolved path must equal the lexical one.
    if lexical.resolve(strict=True) != lexical:
        raise _Reject
    return lexical


def _append(var: str, text: str) -> None:
    path = os.environ.get(var)
    if path:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)


def _run() -> int:
    if sys.version_info < (3, 11):
        raise _Reject
    sha = _env("SEATLAB_EXPECTED_SHA256")
    if _SHA256.fullmatch(sha) is None:
        raise _Reject
    workspace = Path(_env("GITHUB_WORKSPACE"))
    temp = Path(_env("RUNNER_TEMP")).resolve(strict=True)
    manifest = _manifest_path(workspace, _env("SEATLAB_MANIFEST"))
    _env("GITHUB_OUTPUT")

    # Trusted code only: the SeatLab source beside this file.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from seatlab.study import verify_study
    from seatlab.study_html import write_study_html

    result = verify_study(manifest, expect_sha256=sha)

    stage = temp / f".seatlab-stage-{uuid.uuid4().hex}"
    final = temp / f"seatlab-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o700)
    try:
        with open(stage / "verification.json", "x", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
        write_study_html(result, stage / "report.html")
        os.rename(stage, final)  # atomic publish; `final` is new and unique
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    count = len(result["checks"])
    try:
        _append(
            "GITHUB_OUTPUT",
            f"report={final / 'verification.json'}\n"
            f"html={final / 'report.html'}\n"
            f"checks={count}\n",
        )
    except BaseException:
        shutil.rmtree(final, ignore_errors=True)
        raise
    try:
        _append("GITHUB_STEP_SUMMARY", _SUMMARY.format(verification="Passed", checks=count))
    except OSError:
        pass  # the summary is optional; the verified artifacts are already outputs
    return 0


def main() -> int:
    try:
        return _run()
    except Exception as error:
        kind = "refused input" if isinstance(error, _Reject) else "verification failed"
        print(f"::error::SeatLab study verification failed ({kind}).", file=sys.stderr)
        try:
            _append(
                "GITHUB_STEP_SUMMARY",
                _SUMMARY.format(verification="Failed", checks="n/a"),
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
