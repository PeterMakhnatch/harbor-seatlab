"""Collect independently returned native jobs and compare them with a pinned source panel.

The source is the output of ``build_matrix``. Four digests from its separately trusted
``seatlab.matrix-build/v1`` receipt pin the study, the design, the worklist and the
candidate-matrix template. Nothing here runs a game or a model: returns are snapshotted,
sealed and verified by the existing study code, then compared by ``recheck_panel``.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import cast

from . import tape as wire
from .launch_registry import verify_launch_registry
from .matrix import build_matrix
from .matrix_jobs import WORKLIST_SCHEMA, plan_native_jobs
from .panel import panel_report
from .recheck import recheck_panel
from .study_contract import valid_sha256
from .study_inputs import read_document
from .worker_returns import snapshot_returns

__all__ = ["COLLECTION_SCHEMA", "collect_jobs"]

COLLECTION_SCHEMA = "seatlab.worker-collection/v1"
_RECEIPT_SCHEMA = "seatlab.matrix-build/v1"
_PINS = ("study_sha256", "design_sha256", "worklist_sha256", "candidate_matrix_sha256")


def _pins(receipt: Path) -> dict[str, str]:
    document = read_document(receipt)
    if type(document) is not dict or document.get("schema") != _RECEIPT_SCHEMA:
        raise wire.TapeError(f"receipt must be a {_RECEIPT_SCHEMA} object")
    pins = {name: document.get(name) for name in _PINS}
    if not all(valid_sha256(value) for value in pins.values()):
        raise wire.TapeError("receipt must carry four lowercase SHA-256 pins")
    return cast(dict[str, str], pins)


def _verify_source(
    source: Path, pins: dict[str, str]
) -> tuple[dict[str, object], dict[str, str], set[str]]:
    """Return the template, each job’s native case digest and all source trial UUIDs."""
    info = os.lstat(source)
    if not stat.S_ISDIR(info.st_mode):
        raise wire.TapeError("source must be a real directory, not a symlink or file")
    report = panel_report(
        source / "study.json",
        source / "panel-design.json",
        expect_sha256=pins["study_sha256"],
    )
    design = read_document(source / "panel-design.json")
    worklist = read_document(source / "worklist.json")
    template = read_document(source / "candidate-matrix.json")
    for name, value in (
        ("design_sha256", design),
        ("worklist_sha256", worklist),
        ("candidate_matrix_sha256", template),
    ):
        if wire.digest(value) != pins[name]:
            raise wire.TapeError(f"source {name} does not match its receipt pin")
    if report["design_sha256"] != pins["design_sha256"]:
        raise wire.TapeError("source design does not match its receipt pin")
    if type(worklist) is not dict or worklist.get("schema") != WORKLIST_SCHEMA:
        raise wire.TapeError(f"source worklist must be a {WORKLIST_SCHEMA} object")
    if type(template) is not dict:
        raise wire.TapeError("source candidate matrix must be an object")
    # The worklist and template must be exactly what the verified study and design imply.
    try:
        derived = plan_native_jobs(
            cast(dict[str, object], template), cast(dict[str, object], design), report
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise wire.TapeError("source candidate matrix is malformed") from exc
    if derived != (worklist, template):
        raise wire.TapeError("source worklist and candidate matrix do not match the study")
    jobs = cast(list[dict[str, object]], worklist["jobs"])
    rows = cast(list[dict[str, object]], report["cases"])
    digests = {cast(str, row["id"]): row["case_sha256"] for row in rows}
    case_by_job = {
        cast(str, job["id"]): cast(str, digests[cast(str, job["case"])]) for job in jobs
    }
    if len(case_by_job) != len(jobs) or not all(
        valid_sha256(d) for d in case_by_job.values()
    ):
        raise wire.TapeError("source job must resolve to one known native case")
    uuids = {trial for row in rows for trial in cast(list[str], row["native_trial_ids"])}
    return cast(dict[str, object], template), case_by_job, uuids


def _status(recheck: dict[str, object] | None, missing: list[str]) -> str:
    """A known change wins; matched needs a clean recheck and no missing job."""
    if recheck is not None and recheck["status"] == "changed":
        return "changed"
    if recheck is None or missing:
        return "incomplete"
    return cast(str, recheck["status"])


def collect_jobs(
    source: Path,
    returns: Path,
    receipt: Path,
    out: Path,
    *,
    launch_registry: Path | None = None,
    expect_launch_sha256: str | None = None,
) -> dict[str, object]:
    """Verify returned native jobs against the pinned source and publish ``out``.

    ``out`` must not exist. The source and the returns are only read. Everything is built
    in a private stage beside ``out``; the collection receipt appears last. Any error,
    including a malformed, extra, symlinked or reused-UUID return, publishes nothing.
    """
    if out.name in ("", ".", ".."):
        raise wire.TapeError("output path must name a directory")
    if os.path.lexists(out):
        raise FileExistsError(f"refusing to overwrite existing output path: {out}")
    if not out.parent.is_dir():
        raise wire.TapeError(f"output parent directory does not exist: {out.parent}")
    if (launch_registry is None) != (expect_launch_sha256 is None):
        raise wire.TapeError(
            "supply both the launcher registry and its separately trusted digest"
        )
    pins = _pins(receipt)
    template, case_by_job, source_uuids = _verify_source(source, pins)
    expected = sorted(case_by_job)

    work = Path(tempfile.mkdtemp(prefix=".seatlab-collect-", dir=out.parent)).resolve()
    try:
        jobs_dir = work / "jobs"
        jobs_dir.mkdir()
        received = snapshot_returns(returns, case_by_job, jobs_dir, source_uuids)
        if launch_registry is not None and expect_launch_sha256 is not None:
            verify_launch_registry(
                launch_registry,
                expect_launch_sha256,
                pins["worklist_sha256"],
                set(expected),
                source_uuids,
                received,
            )
        have = set(received)
        missing = [job for job in expected if job not in have]

        cells = {
            case: {
                arm: ref if ref is not None and ref.removeprefix("jobs/") in have else None
                for arm, ref in row.items()
            }
            for case, row in cast(dict[str, dict[str, str | None]], template["cases"]).items()
        }
        contrasts = cast(dict[str, dict[str, object]], template["contrasts"])
        comparable = any(
            row[cast(str, spec["source"])] is not None
            and row[cast(str, spec["target"])] is not None
            for row in cells.values()
            for spec in contrasts.values()
        )

        publish = work / "publish"
        publish.mkdir()
        recheck: dict[str, object] | None = None
        if comparable:
            wire.write_lines(work / "matrix.json", [{**template, "cases": cells}])
            built = build_matrix(work / "matrix.json", publish / "candidate")
            recheck = recheck_panel(
                source / "study.json",
                publish / "candidate" / "study.json",
                design=source / "panel-design.json",
                candidate_design=publish / "candidate" / "panel-design.json",
                expect_sha256=pins["study_sha256"],
                expect_candidate_sha256=cast(str, built["study_sha256"]),
                expect_design_sha256=pins["design_sha256"],
                expect_candidate_design_sha256=cast(str, built["design_sha256"]),
            )
            wire.write_lines(publish / "recheck.json", [recheck])

        collection: dict[str, object] = {
            "schema": COLLECTION_SCHEMA,
            "status": _status(recheck, missing),
            "source_pins": pins,
            "assignment_basis": "launch-registry"
            if launch_registry is not None
            else "return-folder",
            "launch_registry_sha256": expect_launch_sha256,
            "native_trials": received,
            "jobs": {"expected": expected, "received": sorted(have), "missing": missing},
            "recheck": None if recheck is None else recheck["counts"],
        }
        wire.write_lines(publish / "collection.json", [collection])
        out.mkdir(mode=0o700)  # never replace a path another collector created
        try:
            for name in ("candidate", "recheck.json", "collection.json"):
                item = publish / name
                if item.exists():
                    os.rename(item, out / name)
        except BaseException:
            shutil.rmtree(out, ignore_errors=True)
            raise
        return collection
    finally:
        shutil.rmtree(work, ignore_errors=True)
