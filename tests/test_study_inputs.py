import hashlib
import json
import os
import socket
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from seatlab import study_inputs
from seatlab.study_inputs import (
    MAX_DOCUMENT_BYTES,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    read_document,
    snapshot,
)
from seatlab.tape import TapeError


def _receipt(data: bytes) -> dict[str, object]:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class ReadDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def test_reads_strict_json_and_allows_symlinked_ancestor_directory(self) -> None:
        real_dir = self.root / "real"
        real_dir.mkdir()
        doc = real_dir / "recipe.json"
        doc.write_text('{"schema":"seatlab.study-recipe/v1","checks":[]}\n', encoding="utf-8")

        self.assertEqual(
            read_document(doc), {"schema": "seatlab.study-recipe/v1", "checks": []}
        )

        # Models host ancestor aliases such as /var -> /private/var on macOS.
        alias_dir = self.root / "alias"
        try:
            alias_dir.symlink_to(real_dir, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assertEqual(
            read_document(alias_dir / "recipe.json"),
            {"schema": "seatlab.study-recipe/v1", "checks": []},
        )

    def test_rejects_non_strict_json_and_oversize_documents(self) -> None:
        cases = (
            (b'{"a":1,"a":2}', "duplicate key"),
            (b'{"x":NaN}', "finite"),
            (b"\xff", "UTF-8 JSON"),
        )
        for index, (payload, message) in enumerate(cases):
            with self.subTest(message=message):
                path = self.root / f"bad-{index}.json"
                path.write_bytes(payload)
                with self.assertRaisesRegex(TapeError, message):
                    read_document(path)

        exact = self.root / "exact.json"
        with patch.object(study_inputs, "MAX_DOCUMENT_BYTES", 32):
            exact.write_bytes(b'{"a":1}' + b" " * 25)
            self.assertEqual(read_document(exact), {"a": 1})

            oversize = self.root / "oversize.json"
            oversize.write_bytes(b'{"a":1}' + b" " * 26)
            with self.assertRaisesRegex(TapeError, "exceeds 32 bytes"):
                read_document(oversize)

        self.assertEqual(MAX_DOCUMENT_BYTES, 2 * 1024 * 1024)

    def test_rejects_symlinks_directories_and_nonregular_files(self) -> None:
        real = self.root / "real.json"
        real.write_text("{}", encoding="utf-8")

        sub = self.root / "subdir"
        sub.mkdir()
        with self.assertRaisesRegex(TapeError, "regular file"):
            read_document(sub)

        link = self.root / "link.json"
        try:
            link.symlink_to(real)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(TapeError, "symlink"):
            read_document(link)

        if hasattr(os, "mkfifo"):
            fifo = self.root / "pipe.json"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(TapeError, "regular file"):
                read_document(fifo)


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def test_copies_shared_and_nested_files_only_and_cleans_up_on_exit(self) -> None:
        shared = b'{"shared":true}\n'
        nested = b'{"nested":[1,2,3]}\n'
        extra = b"do not copy me\n"

        (self.root / "shared.json").write_bytes(shared)
        (self.root / "checks" / "arm-a").mkdir(parents=True)
        (self.root / "checks" / "arm-a" / "tape.jsonl").write_bytes(nested)
        (self.root / "unreferenced.txt").write_bytes(extra)
        (self.root / "unreferenced-dir").mkdir()
        (self.root / "unreferenced-dir" / "secret.txt").write_bytes(extra)

        with snapshot(
            self.root,
            ["checks/arm-a/tape.jsonl", "shared.json", "shared.json"],
        ) as (private_root, receipts):
            self.assertNotEqual(private_root, self.root.resolve())
            self.assertEqual(
                list(receipts.keys()),
                ["checks/arm-a/tape.jsonl", "shared.json"],
            )
            self.assertEqual(
                receipts,
                {
                    "checks/arm-a/tape.jsonl": _receipt(nested),
                    "shared.json": _receipt(shared),
                },
            )
            self.assertEqual(
                (private_root / "checks" / "arm-a" / "tape.jsonl").read_bytes(), nested
            )
            self.assertEqual((private_root / "shared.json").read_bytes(), shared)
            self.assertFalse((private_root / "unreferenced.txt").exists())
            self.assertFalse((private_root / "unreferenced-dir").exists())
            snap_dir = private_root

        self.assertFalse(snap_dir.exists())

    def test_snapshot_is_isolated_from_later_input_changes_and_deletions(self) -> None:
        original = b'{"decision":1}\n'
        source = self.root / "data" / "tape.jsonl"
        source.parent.mkdir()
        source.write_bytes(original)

        with snapshot(self.root, ["data/tape.jsonl"]) as (private_root, receipts):
            source.write_bytes(b'{"decision":999}\n')
            source.unlink()

            copied = private_root / "data" / "tape.jsonl"
            self.assertEqual(copied.read_bytes(), original)
            self.assertEqual(receipts["data/tape.jsonl"], _receipt(original))

    def test_unreferenced_symlinks_and_pipes_are_never_touched(self) -> None:
        wanted = b"ok\n"
        (self.root / "wanted.jsonl").write_bytes(wanted)
        try:
            (self.root / "dangling-unused").symlink_to(self.root / "does-not-exist")
        except OSError:
            pass
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.root / "unused-fifo")

        with snapshot(self.root, ["wanted.jsonl"]) as (private_root, receipts):
            self.assertEqual(receipts, {"wanted.jsonl": _receipt(wanted)})
            self.assertEqual((private_root / "wanted.jsonl").read_bytes(), wanted)
            self.assertFalse((private_root / "dangling-unused").exists())
            self.assertFalse((private_root / "unused-fifo").exists())

    def test_allows_symlinked_root_and_rejects_internal_symlinks_and_escapes(self) -> None:
        real_root = self.root / "bundle"
        real_root.mkdir()
        outside = self.root / "outside.json"
        outside.write_text('{"secret":true}', encoding="utf-8")

        good = real_root / "good.json"
        good.write_bytes(b"{}\n")
        nested_dir = real_root / "nested"
        nested_dir.mkdir()
        (nested_dir / "item.json").write_bytes(b"[]\n")

        alias_root = self.root / "bundle-alias"
        try:
            alias_root.symlink_to(real_root, target_is_directory=True)
            (real_root / "file-link.json").symlink_to(good)
            (real_root / "escape-link.json").symlink_to(outside)
            (real_root / "dir-link").symlink_to(nested_dir, target_is_directory=True)
            (real_root / "outside-dir").symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        with snapshot(alias_root, ["good.json"]) as (private_root, receipts):
            self.assertEqual(receipts, {"good.json": _receipt(b"{}\n")})
            self.assertEqual((private_root / "good.json").read_bytes(), b"{}\n")

        for bad_path in (
            "file-link.json",
            "escape-link.json",
            "dir-link/item.json",
            "outside-dir/outside.json",
        ):
            with self.subTest(bad_path=bad_path), self.assertRaisesRegex(TapeError, "symlink"):
                with snapshot(real_root, [bad_path]):
                    pass

    def test_rejects_directories_fifos_and_unix_sockets(self) -> None:
        subdir = self.root / "as-dir"
        subdir.mkdir()
        with self.assertRaisesRegex(TapeError, "regular file"):
            with snapshot(self.root, ["as-dir"]):
                pass

        if hasattr(os, "mkfifo"):
            fifo = self.root / "stream.fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(TapeError, "regular file"):
                with snapshot(self.root, ["stream.fifo"]):
                    pass

        if hasattr(socket, "AF_UNIX"):
            with TemporaryDirectory() as tmp_sock_dir:
                sock_dir = Path(tmp_sock_dir)
                sock_path = sock_dir / "s.sock"
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    try:
                        server.bind(str(sock_path))
                    except OSError:
                        # Fall back to /tmp if default temp path exceeds AF_UNIX length.
                        with TemporaryDirectory(dir="/tmp") as short_dir:
                            sock_dir = Path(short_dir)
                            sock_path = sock_dir / "s.sock"
                            server.bind(str(sock_path))
                            self.assertTrue(stat.S_ISSOCK(os.lstat(sock_path).st_mode))
                            with self.assertRaisesRegex(TapeError, "regular file"):
                                with snapshot(sock_dir, ["s.sock"]):
                                    pass
                        return
                    self.assertTrue(stat.S_ISSOCK(os.lstat(sock_path).st_mode))
                    with self.assertRaisesRegex(TapeError, "regular file"):
                        with snapshot(sock_dir, ["s.sock"]):
                            pass
                finally:
                    server.close()

    def test_rejects_prefix_collisions_and_invalid_or_empty_path_components(self) -> None:
        (self.root / "a").write_bytes(b"file")
        with self.assertRaisesRegex(TapeError, "collision"):
            with snapshot(self.root, ["a", "a/b.json"]):
                pass

        for bad in (
            "",
            "/abs.json",
            "dir/",
            "dir//file.json",
            "dir/./file.json",
            "../escape.json",
            "dir/../file.json",
            ".hidden.json",
            "dir/.hidden/file.json",
            r"dir\file.json",
        ):
            with self.subTest(bad=bad), self.assertRaises(TapeError):
                with snapshot(self.root, [bad]):
                    pass

        with self.assertRaisesRegex(TapeError, "sequence"):
            with snapshot(self.root, "not-a-sequence.json"):
                pass

    def test_enforces_per_file_and_total_byte_bounds_without_loading_whole_file(self) -> None:
        self.assertEqual(MAX_FILE_BYTES, 64 * 1024 * 1024)
        self.assertEqual(MAX_TOTAL_BYTES, 512 * 1024 * 1024)

        chunk_a = b"a" * 40
        chunk_b = b"b" * 30
        chunk_c = b"c" * 35
        (self.root / "a.bin").write_bytes(chunk_a)
        (self.root / "b.bin").write_bytes(chunk_b)
        (self.root / "c.bin").write_bytes(chunk_c)

        with (
            patch.object(study_inputs, "_CHUNK_BYTES", 16),
            patch.object(study_inputs, "MAX_FILE_BYTES", 40),
            patch.object(study_inputs, "MAX_TOTAL_BYTES", 70),
        ):
            with snapshot(self.root, ["a.bin", "b.bin"]) as (private_root, receipts):
                self.assertEqual(receipts["a.bin"], _receipt(chunk_a))
                self.assertEqual(receipts["b.bin"], _receipt(chunk_b))
                self.assertEqual((private_root / "a.bin").read_bytes(), chunk_a)

            with self.assertRaisesRegex(TapeError, "exceeds 70 bytes"):
                with snapshot(self.root, ["a.bin", "b.bin", "c.bin"]):
                    pass

        (self.root / "too-big.bin").write_bytes(b"x" * 41)
        with (
            patch.object(study_inputs, "_CHUNK_BYTES", 16),
            patch.object(study_inputs, "MAX_FILE_BYTES", 40),
            patch.object(study_inputs, "MAX_TOTAL_BYTES", 100),
        ):
            with self.assertRaisesRegex(TapeError, "exceeds 40 bytes"):
                with snapshot(self.root, ["too-big.bin"]):
                    pass

    def test_streaming_reads_stay_bounded_and_cleanup_on_mid_copy_error(self) -> None:
        payload = json.dumps({"rows": list(range(200))}).encode("utf-8")
        (self.root / "streamed.json").write_bytes(payload)
        created_dirs: list[Path] = []
        real_temp_dir = study_inputs.TemporaryDirectory

        def tracking_temp_dir(*, prefix: str) -> TemporaryDirectory[str]:
            directory = real_temp_dir(prefix=prefix)
            created_dirs.append(Path(directory.name))
            return directory

        with (
            patch.object(study_inputs, "TemporaryDirectory", tracking_temp_dir),
            patch.object(study_inputs, "_CHUNK_BYTES", 32),
            patch.object(study_inputs, "MAX_FILE_BYTES", len(payload) - 1),
        ):
            with self.assertRaisesRegex(TapeError, "exceeds"):
                with snapshot(self.root, ["streamed.json"]):
                    pass

        self.assertEqual(len(created_dirs), 1)
        self.assertFalse(created_dirs[0].exists())

        with patch.object(study_inputs, "_CHUNK_BYTES", 32):
            with snapshot(self.root, ["streamed.json"]) as (private_root, receipts):
                self.assertEqual(receipts["streamed.json"], _receipt(payload))
                self.assertEqual((private_root / "streamed.json").read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
