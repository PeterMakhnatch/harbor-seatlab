"""Read bounded study documents and snapshot referenced regular files offline.

Standard OS guards (`lstat`, `O_NOFOLLOW`/`O_NONBLOCK` where available, `fstat`, and
containment checks under the resolved study root) reject symlinks, non-regular files,
and host path escapes across Python 3.11 on Linux, macOS, and Windows. These checks
isolate study verification from unreferenced or later-modified inputs; they do not defend
against an actively hostile concurrent filesystem mutating directories mid-read.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

from . import tape as wire
from .study_contract import FileReceipt, validate_path

__all__ = [
    "MAX_DOCUMENT_BYTES",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "read_document",
    "snapshot",
]

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


@contextmanager
def _open_regular_file(path: Path, label: str) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    """Open one existing regular, non-symlink file without blocking on pipes or sockets."""
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise wire.TapeError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise wire.TapeError(f"{label} must be a regular file: {path}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1)):
            raise wire.TapeError(f"{label} must not be a symlink: {path}") from exc
        raise
    try:
        fd_info = os.fstat(fd)
        if not stat.S_ISREG(fd_info.st_mode):
            raise wire.TapeError(f"{label} must be a regular file: {path}")
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as stream:
        yield stream, fd_info


def read_document(path: Path) -> object:
    """Decode one strict UTF-8 JSON regular file within MAX_DOCUMENT_BYTES.

    Only ``path`` itself is checked against being a symlink so callers may pass paths
    under resolved or host-aliased ancestors (such as ``/var`` on macOS).
    """
    limit = MAX_DOCUMENT_BYTES
    with _open_regular_file(path, "study document") as (stream, info):
        if info.st_size > limit:
            raise wire.TapeError(f"study document exceeds {limit} bytes")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = stream.read(min(_CHUNK_BYTES, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise wire.TapeError(f"study document exceeds {limit} bytes")
            chunks.append(chunk)
    return wire.decode(b"".join(chunks))


def _normalize_paths(paths: Sequence[str]) -> list[str]:
    if isinstance(paths, (str, bytes)):
        raise wire.TapeError("study input paths must be a sequence of relative paths")
    ordered = sorted({validate_path(raw) for raw in paths})
    known = set(ordered)
    for rel in ordered:
        parts = rel.split("/")
        for depth in range(1, len(parts)):
            prefix = "/".join(parts[:depth])
            if prefix in known:
                raise wire.TapeError(
                    f"study input path collision: {prefix!r} is both a file and a "
                    f"directory prefix for {rel!r}"
                )
    return ordered


def _resolve_input_path(base_root: Path, rel: str) -> Path:
    parts = rel.split("/")
    current = base_root
    for part in parts[:-1]:
        current = current / part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise wire.TapeError(f"study input path must not traverse a symlink: {rel}")
        if not stat.S_ISDIR(info.st_mode):
            raise wire.TapeError(f"study input path component is not a directory: {rel}")
    target = current / parts[-1]
    info = os.lstat(target)
    if stat.S_ISLNK(info.st_mode):
        raise wire.TapeError(f"study input must not be a symlink: {rel}")
    if not stat.S_ISREG(info.st_mode):
        raise wire.TapeError(f"study input must be a regular file: {rel}")
    resolved = target.resolve(strict=True)
    if not resolved.is_relative_to(base_root) or os.path.normcase(
        str(resolved)
    ) != os.path.normcase(str(target)):
        raise wire.TapeError(f"study input path resolves outside study root: {rel}")
    return target


@contextmanager
def snapshot(
    root: Path, paths: Sequence[str]
) -> Iterator[tuple[Path, dict[str, FileReceipt]]]:
    """Copy only the sorted unique requested regular files into a private temp snapshot.

    Computes each file's byte count and SHA-256 in a single streaming pass without holding
    entire files in memory, enforces MAX_FILE_BYTES and MAX_TOTAL_BYTES, and removes the
    snapshot directory on exit. Does not defend against an actively hostile concurrent
    filesystem modifying the source tree during copy.
    """
    ordered = _normalize_paths(paths)
    base_root = root.resolve(strict=True)
    if not base_root.is_dir():
        raise wire.TapeError(f"study root must be a directory: {root}")
    file_limit = MAX_FILE_BYTES
    total_limit = MAX_TOTAL_BYTES

    with TemporaryDirectory(prefix="seatlab-study-") as temporary:
        private_root = Path(temporary).resolve()
        receipts: dict[str, FileReceipt] = {}
        total_bytes = 0
        for rel in ordered:
            source_path = _resolve_input_path(base_root, rel)
            dest_path = private_root.joinpath(*rel.split("/"))
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with _open_regular_file(source_path, f"study input {rel!r}") as (source, info):
                if info.st_size > file_limit:
                    raise wire.TapeError(
                        f"study input {rel!r} exceeds {file_limit} bytes"
                    )
                if total_bytes + info.st_size > total_limit:
                    raise wire.TapeError(f"study snapshot exceeds {total_limit} bytes")
                digest = hashlib.sha256()
                file_bytes = 0
                try:
                    dest_stream = dest_path.open("xb")
                except FileExistsError as exc:
                    raise wire.TapeError(
                        f"study input path collides on this filesystem: {rel!r}"
                    ) from exc
                with dest_stream:
                    while True:
                        remaining = min(
                            _CHUNK_BYTES,
                            file_limit + 1 - file_bytes,
                            total_limit + 1 - total_bytes,
                        )
                        chunk = source.read(remaining)
                        if not chunk:
                            break
                        file_bytes += len(chunk)
                        if file_bytes > file_limit:
                            raise wire.TapeError(
                                f"study input {rel!r} exceeds {file_limit} bytes"
                            )
                        total_bytes += len(chunk)
                        if total_bytes > total_limit:
                            raise wire.TapeError(
                                f"study snapshot exceeds {total_limit} bytes"
                            )
                        digest.update(chunk)
                        dest_stream.write(chunk)
                receipts[rel] = {"bytes": file_bytes, "sha256": digest.hexdigest()}
        yield private_root, receipts
