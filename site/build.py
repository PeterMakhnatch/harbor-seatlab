#!/usr/bin/env python3
"""Build the SeatLab evidence gallery as a static GitHub Pages directory.

The output holds only the hand-written front door (index.html, plus style.css
when present) and three public HTML reports from the v0.10.0 release. Every
report must match its SHA256 pin, whether it comes from the release or from
--assets-dir. The output directory must not exist yet; a failed build leaves
nothing behind. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SITE = Path(__file__).resolve().parent
RELEASE = "https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.10.0/"
ASSETS = {
    "calendar.html": "2bdf39992ffeec879ffa09cb0a1bc5479603f898f185e3bbe188c7d416e91310",
    "team.html": "6488ff422ac2207362226c0d15d303e19dfaaec9dd88c6f164f93c64284d1722",
    "pettingzoo.html": "81c9b8fb96ea50f52e3b296c3bba007e05ce58c02a7437b9bb468f8f2ab81ca3",
}
REQUIRED_PAGES = ("index.html",)
OPTIONAL_PAGES = ("style.css",)
MAX_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 30


class BuildError(Exception):
    pass


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise BuildError(f"refusing non-HTTPS redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnlyRedirects())


def _check(name: str, data: bytes) -> bytes:
    if len(data) > MAX_BYTES:
        raise BuildError(f"{name}: larger than {MAX_BYTES} bytes")
    digest = hashlib.sha256(data).hexdigest()
    if digest != ASSETS[name]:
        raise BuildError(f"{name}: sha256 {digest} does not match pin {ASSETS[name]}")
    return data


def _read_regular(path: Path, limit: int) -> bytes:
    """Read a regular, non-symlink file of at most `limit` bytes."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise BuildError(f"{path}: missing") from None
    if not stat.S_ISREG(info.st_mode):
        raise BuildError(f"{path}: not a regular file")
    if info.st_size > limit:
        raise BuildError(f"{path}: larger than {limit} bytes")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise BuildError(f"{path}: larger than {limit} bytes")
    return data


def _fetch(name: str, timeout: float) -> bytes:
    """Fetch one report with a byte cap and a total deadline of `timeout` seconds."""
    url = RELEASE + name
    request = urllib.request.Request(url, headers={"User-Agent": "seatlab-gallery-build"})
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    size = 0
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise BuildError(f"{url}: HTTP {response.status}")
            if urllib.parse.urlsplit(response.geturl()).scheme != "https":
                raise BuildError(f"{url}: ended on non-HTTPS URL")
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise BuildError(f"{url}: larger than {MAX_BYTES} bytes")
                if time.monotonic() > deadline:
                    raise BuildError(f"{url}: took longer than {timeout} seconds")
                chunks.append(chunk)
    except (urllib.error.URLError, OSError) as error:
        raise BuildError(f"{url}: {error}") from None
    return _check(name, b"".join(chunks))


def _asset(name: str, assets_dir: Path | None, timeout: float) -> bytes:
    if assets_dir is None:
        return _fetch(name, timeout)
    return _check(name, _read_regular(assets_dir / name, MAX_BYTES))


def build(out: Path, assets_dir: Path | None, timeout: float) -> list[str]:
    if os.path.lexists(out):
        raise BuildError(f"{out}: already exists; choose a new output directory")
    files: dict[str, bytes] = {}
    for name in REQUIRED_PAGES:
        files[name] = _read_regular(SITE / name, MAX_BYTES)
    for name in OPTIONAL_PAGES:
        if os.path.lexists(SITE / name):
            files[name] = _read_regular(SITE / name, MAX_BYTES)
    for name in ASSETS:
        files[name] = _asset(name, assets_dir, timeout)

    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        for name, data in files.items():
            (staging / name).write_bytes(data)
        staging.chmod(0o755)
        for name in files:
            (staging / name).chmod(0o644)
        if os.path.lexists(out):
            raise BuildError(f"{out}: appeared during the build")
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="new output directory (must not exist)")
    parser.add_argument(
        "--assets-dir",
        type=Path,
        help="build offline from this directory; each report must still match its pin",
    )
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS, help="seconds per network read")
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be in (0, 300]")
    try:
        names = build(args.out.absolute(), args.assets_dir, args.timeout)
    except BuildError as error:
        print(f"build.py: {error}", file=sys.stderr)
        return 1
    for name in names:
        print(args.out / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
