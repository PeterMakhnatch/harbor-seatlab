"""Build a sealed role-trajectory study and panel design from one compact matrix."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import cast

from . import tape as wire
from .matrix_jobs import plan_native_jobs
from .matrix_plan import compile_matrix
from .panel import panel_report
from .study import seal_study
from .study_inputs import read_document, snapshot

__all__ = ["build_matrix"]

_SCHEMA = "seatlab.matrix-build/v1"


def build_matrix(matrix_path: Path, out: Path) -> dict[str, object]:
    """Snapshot matrix inputs, seal and verify them in a stage, then publish ``out``.

    ``out`` must not exist. It is created exclusively and filled by renames from a private
    stage on the same filesystem, with the study manifest moved in last. On any failure
    the directory this call created is removed.
    """
    if out.name in ("", ".", ".."):
        raise wire.TapeError("output path must name a directory")
    if os.path.lexists(out):
        raise FileExistsError(f"refusing to overwrite existing output path: {out}")
    if not out.parent.is_dir():
        raise wire.TapeError(f"output parent directory does not exist: {out.parent}")
    document = read_document(matrix_path)
    recipe, design, files = compile_matrix(document)
    stage = Path(tempfile.mkdtemp(prefix=".seatlab-matrix-", dir=out.parent)).resolve()
    created = False
    try:
        with snapshot(matrix_path.parent, sorted(set(files.values()))) as (private, _):
            for dest_rel, src_rel in sorted(files.items()):
                dest = stage.joinpath(*dest_rel.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(private.joinpath(*src_rel.split("/")), dest)
        wire.write_lines(stage / "recipe.json", [recipe])
        wire.write_lines(stage / "panel-design.json", [design])
        manifest = seal_study(stage / "recipe.json", stage / "study.json")
        study_sha256 = wire.digest(manifest)
        design_sha256 = wire.digest(design)
        report = panel_report(
            stage / "study.json", stage / "panel-design.json", expect_sha256=study_sha256
        )
        if report["known_native_trials"] != len(files) // 2:
            raise wire.TapeError(
                "each measured case/arm must name a distinct native trial UUID"
            )
        jobs, template = plan_native_jobs(cast(dict[str, object], document), design, report)
        wire.write_lines(stage / "worklist.json", [jobs])
        wire.write_lines(stage / "candidate-matrix.json", [template])
        receipt: dict[str, object] = {
            "schema": _SCHEMA,
            "study_sha256": study_sha256,
            "design_sha256": design_sha256,
            "worklist_sha256": wire.digest(jobs),
            "candidate_matrix_sha256": wire.digest(template),
            "cases": report["case_count"],
            "checks": len(cast(list[object], manifest["checks"])),
            "native_trials": report["known_native_trials"],
        }
        os.mkdir(out, mode=0o700)
        created = True
        for name in (
            "cells",
            "recipe.json",
            "panel-design.json",
            "worklist.json",
            "candidate-matrix.json",
            "study.json",
        ):
            os.rename(stage / name, out / name)  # `out` is ours and new; manifest last
        created = False
        return receipt
    finally:
        if created:
            shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(stage, ignore_errors=True)
