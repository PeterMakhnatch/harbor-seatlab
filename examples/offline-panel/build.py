#!/usr/bin/env python3
"""Build the offline-panel zipapp deterministically (stdlib only, Python 3.11+).

Usage: python3 build.py --wheel PATH --archives DIR --output NEW_FILE
Run the result with: python3 -I -S NEW_FILE NEW_DIR

Compression policy: every member is ZIP_STORED. The tarballs are already
gzip-compressed, and stored members avoid zlib-version differences, so the
bytes are identical across Python builds, macOS and Linux. Fixed timestamp,
mode 0644, create_system=3, members sorted by name.
"""
import argparse
import hashlib
import io
import os
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent  # trusted: sources live in this repo
DEMO = HERE / "demo.py"
FETCH = HERE.parent / "panel-replication" / "fetch_controls.py"
WHEEL_SHA256 = "d7f90f782b073166ca1837818a4d569554681eb91dc0697cb5afe3bca5abfa57"
DATE = (1980, 1, 1, 0, 0, 0)
WHEEL_OK = re.compile(r"^seatlab/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py$")
DISTINFO = "harbor_seatlab-0.14.0.dist-info/"


def die(msg):
    sys.exit(f"build.py: {msg}")


def read_verified(path, sha256, what):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        die(f"SHA-256 mismatch for {what}: {path}")
    return data


def load_pins():
    # Trusted sibling source, not a downloaded archive member.
    import runpy

    return {sub: (path.rsplit("/", 1)[-1], digest)
            for sub, path, digest in runpy.run_path(str(FETCH))["PINS"]}


def wheel_members(wheel_bytes):
    out = {}
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename
            if name == DISTINFO + "licenses/LICENSE":
                out["LICENSE"] = zf.read(info)
                continue
            if info.is_dir() or name.startswith(DISTINFO):
                continue
            mode = info.external_attr >> 16
            if mode and (mode & 0o170000) != 0o100000:
                die(f"wheel member is not a regular file: {name}")
            if not WHEEL_OK.match(name):
                die(f"unexpected wheel path: {name}")
            out[name] = zf.read(info)
    if "LICENSE" not in out or "seatlab/__init__.py" not in out:
        die("wheel lacks package or license")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wheel", required=True)
    ap.add_argument("--archives", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    out = Path(args.output)
    if os.path.lexists(out):
        die(f"refusing to overwrite: {out}")

    members = wheel_members(read_verified(args.wheel, WHEEL_SHA256, "wheel"))
    members["__main__.py"] = DEMO.read_bytes()
    members["fetch_controls.py"] = FETCH.read_bytes()
    for sub, (fname, digest) in load_pins().items():
        members[f"data/{sub}.tar.gz"] = read_verified(
            Path(args.archives) / fname, digest, f"archive {sub}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name in sorted(members):
            zi = zipfile.ZipInfo(name, DATE)
            zi.compress_type = zipfile.ZIP_STORED
            zi.create_system = 3
            zi.external_attr = 0o100644 << 16
            zf.writestr(zi, members[name])

    # Exclusive temp file in the target directory, then link: never partial/overwrite.
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(buf.getvalue())
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, out)  # fails if OUTPUT appeared meanwhile
    finally:
        os.unlink(tmp)


if __name__ == "__main__":
    main()
