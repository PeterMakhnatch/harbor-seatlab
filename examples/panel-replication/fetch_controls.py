#!/usr/bin/env python3
"""Fetch the three pinned panel-replication control archives as plain data.

Usage: python fetch_controls.py NEW_DIR

Downloads three public release archives, checks each SHA-256 before parsing,
and writes their regular files under NEW_DIR/{source,unchanged,changed}.
Nothing from the archives is executed, imported or installed.
"""

import gzip
import hashlib
import io
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import PurePosixPath

BASE_URL = "https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/"

# (subdirectory, release path, sha256)
PINS = (
    (
        "source",
        "v0.13.0/native-speaker-listener-holdout.tar.gz",
        "5212d12fa6b1a57177abd6203e6a5d7fde66f4a923edc8902bc834811ce1060d",
    ),
    (
        "unchanged",
        "panel-control-data-v1/native-panel-unchanged-linux.tar.gz",
        "1e483339b87947fdad758e636421b2da88350834604101c7381d1c3842331d99",
    ),
    (
        "changed",
        "panel-control-data-v1/native-panel-changed-rule.tar.gz",
        "48e7815ab4ab95a514899a7323df6d6d6c6908e3d3d8015a13eeeacaca5e60b4",
    ),
)

MAX_DOWNLOAD = 8 * 1024 * 1024  # compressed bytes per archive
MAX_TAR = 32 * 1024 * 1024  # decompressed tar bytes per archive
MAX_FILE = 8 * 1024 * 1024  # bytes per member
MAX_MEMBERS = 1000
TIMEOUT = 30  # seconds, per network operation
CHUNK = 64 * 1024


class FetchError(Exception):
    pass


def read_limited(stream, limit, what):
    """Read a stream fully; fail if it holds more than `limit` bytes."""
    out = bytearray()
    while True:
        chunk = stream.read(CHUNK)
        if not chunk:
            return bytes(out)
        out += chunk
        if len(out) > limit:
            raise FetchError(f"{what} is larger than {limit} bytes")


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "fetch-controls"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return read_limited(response, MAX_DOWNLOAD, "download " + url)


def check_name(name, seen, seen_folded):
    """Accept only canonical relative POSIX paths without repeats."""
    if not name or "\0" in name or "\\" in name:
        raise FetchError(f"bad member name: {name!r}")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise FetchError(f"non-canonical member name: {name!r}")
    if PurePosixPath(name).is_absolute() or ":" in parts[0]:
        raise FetchError(f"absolute member name: {name!r}")
    if name in seen:
        raise FetchError(f"duplicate member name: {name!r}")
    folded = name.casefold()
    if folded in seen_folded:
        raise FetchError(f"ambiguous member name: {name!r}")
    seen.add(name)
    seen_folded.add(folded)


def extract_archive(data, dest):
    """Write the regular files of a .tar.gz held in `data` under `dest`."""
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        raw_tar = read_limited(gz, MAX_TAR, "decompressed tar")

    seen, seen_folded, files = set(), set(), []
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as tar:
        members = tar.getmembers()
        if len(members) > MAX_MEMBERS:
            raise FetchError("too many archive members")
        for member in members:
            if not member.isreg():
                raise FetchError(f"not a regular file: {member.name!r}")
            if member.size > MAX_FILE:
                raise FetchError(f"member too large: {member.name!r}")
            check_name(member.name, seen, seen_folded)
            files.append(member)

        # A file may not also be a directory prefix of another file.
        for name in seen:
            parts = name.split("/")
            for i in range(1, len(parts)):
                if "/".join(parts[:i]).casefold() in seen_folded:
                    raise FetchError(f"file and directory clash: {name!r}")

        os.makedirs(dest)
        for member in files:
            target = os.path.join(dest, *member.name.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            payload = read_limited(tar.extractfile(member), MAX_FILE, member.name)
            if len(payload) != member.size:
                raise FetchError(f"short member: {member.name!r}")
            with open(target, "xb") as out:
                out.write(payload)
    return len(files)


def main(argv):
    if len(argv) != 2:
        print("usage: python fetch_controls.py NEW_DIR", file=sys.stderr)
        return 2
    dest = os.path.abspath(argv[1])
    if os.path.lexists(dest):
        print(f"error: destination exists: {dest}", file=sys.stderr)
        return 1
    parent = os.path.dirname(dest)
    if not os.path.isdir(parent):
        print(f"error: parent directory missing: {parent}", file=sys.stderr)
        return 1

    stage = tempfile.mkdtemp(prefix=".fetch-controls-", dir=parent)
    try:
        lines = []
        for sub, path, pin in PINS:
            data = download(BASE_URL + path)
            digest = hashlib.sha256(data).hexdigest()
            if digest != pin:
                raise FetchError(f"SHA-256 mismatch for {path}: got {digest}")
            extract_archive(data, os.path.join(stage, sub))
            lines.append(f"{sub}: {os.path.join(dest, sub)} (sha256 {pin}, {path})")
        os.chmod(stage, 0o755)
        if os.path.lexists(dest):
            raise FetchError(f"destination appeared: {dest}")
        os.rename(stage, dest)
    except BaseException as exc:
        shutil.rmtree(stage, ignore_errors=True)  # only our own stage
        if isinstance(exc, (FetchError, OSError, tarfile.TarError, EOFError)):
            print(f"error: {exc}", file=sys.stderr)
            return 1
        raise
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
