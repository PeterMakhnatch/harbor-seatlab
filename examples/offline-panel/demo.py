"""Recompute three measured SeatLab controls from the evidence inside this zipapp."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

from fetch_controls import MAX_DOWNLOAD, PINS, FetchError, extract_archive
from seatlab.cli import main as seatlab

SOURCE = "34b0bfde9b2c4e7a71e2906c9c17ba8d65239defe24cd9bd01575ff888bb0d7d"
SOURCE_DESIGN = "bf74b26265eba2206a4f9e4382ecb8cd8bd342fab666db67bb7d8ee2dfa26611"
FULL = "6fade1255de1cfbda0bf2e741523d8af7f7629c0d530a418bd5241c78ab2bc55"
WITHHELD = "d6967074a4ceea39adb157e4698fd0240ee112aa5d77781cca589255cbf0c59e"
UNCHANGED = "f69b4276ad27a50a14ab80dd67e832907048425b92ddb9d0317a1233a7cc51ce"
CHANGED = "17e326fbd4109b816fb914c6f096730f792299acac2c5091f476f2b96849ba58"
# A process failure is not a regression: the CLI must reproduce the complete output bytes.
CONTROLS = (
    ("matched", "unchanged", UNCHANGED, FULL, 0, "fa2b58ba04da7948fdd3945b0910f7e31f1dc6b1476ae98182e1e4eda7a6d932"),
    ("changed", "changed", CHANGED, FULL, 1, "5365b7562e10100dd0d78064d0ef01ca6801e9d45747bbad176858b2aa3f5557"),
    ("incomplete", "unchanged", UNCHANGED, WITHHELD, 3, "20899c0ecc5c4c97ffcb551700e7f6b973db6863335a7c373232ceffc183e122"),
)


def unpack(dest: Path) -> None:
    with zipfile.ZipFile(sys.argv[0]) as app:
        for arm, _, expected in PINS:
            info = app.getinfo(f"data/{arm}.tar.gz")
            if info.file_size > MAX_DOWNLOAD:
                raise ValueError("oversized embedded evidence")
            data = app.read(info)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"{arm}: embedded native evidence has the wrong digest")
            extract_archive(data, dest / arm)


def run(dest: Path) -> None:
    dest.mkdir(mode=0o700)  # Never replace a previous run, even if it is empty.
    unpack(dest)
    source = dest / "source"
    design = json.loads((dest / "unchanged/panel-design.json").read_bytes())
    design["cases"]["s101"]["rotation-signal-different"] = None
    withheld = dest / "withheld-design.json"
    withheld.write_text(json.dumps(design, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    reports = []
    for status, arm, study_pin, design_pin, wanted_exit, report_pin in CONTROLS:
        candidate = dest / arm
        design_file = withheld if status == "incomplete" else candidate / "panel-design.json"
        argv = ["study", "recheck", str(source / "study.json"), str(candidate / "study.json"),
                "--design", str(source / "panel-design.json"), "--candidate-design", str(design_file),
                "--expect-sha256", SOURCE, "--expect-candidate-sha256", study_pin,
                "--expect-design-sha256", SOURCE_DESIGN, "--expect-candidate-design-sha256", design_pin]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = seatlab(argv)
        data = buffer.getvalue().encode("utf-8")
        if code != wanted_exit or hashlib.sha256(data).hexdigest() != report_pin:
            raise ValueError(f"{status}: did not reproduce the released report")
        reports.append((status, code, data))
    for status, code, data in reports:
        (dest / f"recheck-{status}.json").write_bytes(data)
        n = json.loads(data)["counts"]
        print(f"{status:10}  CLI exit {code}  {n['stable']:2} stable  {n['changed']:2} changed  {n['unknown']:2} unknown")
    print("All 3 reports match the public release byte for byte.")
    print("The incomplete control withholds evidence. It adds no native trials.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reproduce measured SeatLab controls offline. No pip, Docker, network, or API key.")
    parser.add_argument("output", type=Path, help="new directory for the native evidence and 3 JSON reports")
    args = parser.parse_args()
    try:
        run(args.output.absolute())
    except FileExistsError:
        parser.exit(2, "Output exists. Choose a new directory.\n")
    except (ValueError, OSError, KeyError, FetchError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"Demo did not complete: {exc}\n")
