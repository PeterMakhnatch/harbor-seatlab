from __future__ import annotations

import json
import os
import threading
import tomllib
import unittest
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from seatlab.tape import canonical, digest

TASK_DIR = Path(__file__).resolve().parents[1] / "examples" / "receipt-board" / "task"
ENV_DIR = TASK_DIR / "environment"
WORLD_MODULE = run_path(str(ENV_DIR / "world.py"))

ReceiptBoardServer = WORLD_MODULE["ReceiptBoardServer"]
ReceiptBoardWorld = WORLD_MODULE["ReceiptBoardWorld"]
WorldError = WORLD_MODULE["WorldError"]
build_world = WORLD_MODULE["build_world"]
canonical_digest = WORLD_MODULE["canonical_digest"]
canonical_json = WORLD_MODULE["canonical_json"]
load_case = WORLD_MODULE["load_case"]
load_mode = WORLD_MODULE["load_mode"]

EXPECTED_CASE = {"options": {"p1": ["amber", "cobalt"], "p2": ["mint", "coral"]}}


def _http_opener():
    return build_opener(ProxyHandler({}))


def _get_json(url: str) -> tuple[int, Any]:
    try:
        with _http_opener().open(url, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        exc.close()
        return exc.code, body


def _post_json(url: str, payload: object) -> tuple[int, Any]:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=raw,
    )
    try:
        with _http_opener().open(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        exc.close()
        return exc.code, body


class ReceiptBoardTaskFilesTests(unittest.TestCase):
    def test_task_manifest_and_constants(self) -> None:
        config = tomllib.loads((TASK_DIR / "task.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["schema_version"], "1.4")
        self.assertEqual(config["artifacts"], ["/logs/world/receipts.jsonl"])
        self.assertEqual(set(config["agents"].keys()), {"p1", "p2"})
        self.assertTrue(config["agents"]["p1"]["under_test"])
        self.assertTrue(config["agents"]["p2"]["under_test"])

        raw_case = json.loads((ENV_DIR / "case.json").read_text(encoding="utf-8"))
        self.assertEqual(raw_case, EXPECTED_CASE)
        raw_mode = json.loads((ENV_DIR / "mode.json").read_text(encoding="utf-8"))
        self.assertEqual(raw_mode, "full")

        for rel in (
            "instructions/p1.md",
            "instructions/p2.md",
            "solution/solve.sh",
            "tests/test.sh",
            "environment/Dockerfile",
            "environment/player/Dockerfile",
            "environment/docker-compose.yaml",
            "environment/world.py",
        ):
            self.assertTrue((TASK_DIR / rel).is_file(), f"missing {rel}")

        for script in ("solution/solve.sh", "tests/test.sh"):
            mode = os.stat(TASK_DIR / script).st_mode
            self.assertTrue(mode & 0o111, f"{script} must be executable")

    def test_canonical_digest_matches_seatlab_tape(self) -> None:
        for view in (
            {"role": "p1", "options": ["amber", "cobalt"]},
            {"role": "p1", "options": ["amber"]},
            {"role": "p2", "options": ["mint", "coral"]},
            {"role": "p2", "options": ["mint"]},
        ):
            self.assertEqual(canonical_json(view), canonical(view))
            self.assertEqual(canonical_digest(view), digest(view))


class ReceiptBoardWorldTests(unittest.TestCase):
    def test_full_and_first_modes_preserve_option_order(self) -> None:
        with TemporaryDirectory() as tmp:
            full_world = ReceiptBoardWorld(
                EXPECTED_CASE["options"],
                "full",
                receipts_path=Path(tmp) / "full.jsonl",
            )
            self.assertEqual(
                full_world.get_view("p1"),
                {"role": "p1", "options": ["amber", "cobalt"]},
            )
            self.assertEqual(
                full_world.get_view("p2"),
                {"role": "p2", "options": ["mint", "coral"]},
            )

            first_world = ReceiptBoardWorld(
                EXPECTED_CASE["options"],
                "first",
                receipts_path=Path(tmp) / "first.jsonl",
            )
            self.assertEqual(
                first_world.get_view("p1"),
                {"role": "p1", "options": ["amber"]},
            )
            self.assertEqual(
                first_world.get_view("p2"),
                {"role": "p2", "options": ["mint"]},
            )

    def test_rejects_unissued_wrong_digest_disallowed_action_and_duplicate(self) -> None:
        with TemporaryDirectory() as tmp:
            receipts_path = Path(tmp) / "receipts.jsonl"
            world = ReceiptBoardWorld(
                EXPECTED_CASE["options"],
                "first",
                receipts_path=receipts_path,
            )
            p1_first_view = {"role": "p1", "options": ["amber"]}
            p1_first_digest = digest(p1_first_view)
            p1_full_digest = digest({"role": "p1", "options": ["amber", "cobalt"]})

            # Must GET view before committing.
            with self.assertRaises(WorldError) as unissued:
                world.submit_note(
                    {
                        "role": "p1",
                        "action": "amber",
                        "acknowledged_sha256": p1_first_digest,
                    }
                )
            self.assertEqual(unissued.exception.status, 409)
            self.assertEqual(receipts_path.read_text(encoding="utf-8"), "")

            view = world.get_view("p1")
            self.assertEqual(view, p1_first_view)

            # Wrong digest rejected; nothing written.
            with self.assertRaises(WorldError) as bad_ack:
                world.submit_note(
                    {
                        "role": "p1",
                        "action": "amber",
                        "acknowledged_sha256": p1_full_digest,
                    }
                )
            self.assertEqual(bad_ack.exception.status, 400)
            self.assertEqual(receipts_path.read_text(encoding="utf-8"), "")

            # Action not in issued options rejected; nothing written.
            with self.assertRaises(WorldError) as bad_action:
                world.submit_note(
                    {
                        "role": "p1",
                        "action": "cobalt",
                        "acknowledged_sha256": p1_first_digest,
                    }
                )
            self.assertEqual(bad_action.exception.status, 400)
            self.assertEqual(receipts_path.read_text(encoding="utf-8"), "")

            # Valid commit appends exact receipt line.
            receipt = world.submit_note(
                {
                    "role": "p1",
                    "action": "amber",
                    "acknowledged_sha256": p1_first_digest,
                }
            )
            self.assertEqual(
                receipt,
                {
                    "role": "p1",
                    "turn": 1,
                    "view": p1_first_view,
                    "acknowledged_sha256": p1_first_digest,
                    "action": "amber",
                },
            )
            lines = receipts_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0]), receipt)

            # Second commit for same role rejected; file unchanged.
            with self.assertRaises(WorldError) as dup:
                world.submit_note(
                    {
                        "role": "p1",
                        "action": "amber",
                        "acknowledged_sha256": p1_first_digest,
                    }
                )
            self.assertEqual(dup.exception.status, 409)
            self.assertEqual(len(receipts_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_concurrent_http_submissions_commit_once_per_role(self) -> None:
        with TemporaryDirectory() as tmp:
            receipts_path = Path(tmp) / "receipts.jsonl"
            world = build_world(ENV_DIR, receipts_path=receipts_path)
            server = ReceiptBoardServer(("127.0.0.1", 0), world)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{port}"
                status, health = _get_json(f"{base}/health")
                self.assertEqual(status, 200)
                self.assertEqual(health, {"ok": True})

                barrier = threading.Barrier(4)
                results: dict[str, list[int]] = {"p1": [], "p2": []}
                results_lock = threading.Lock()

                def worker(role: str) -> None:
                    v_status, view = _get_json(f"{base}/view/{role}")
                    self.assertEqual(v_status, 200)
                    ack = digest(view)
                    action = view["options"][-1]
                    barrier.wait(timeout=5)
                    p_status, _ = _post_json(
                        f"{base}/note",
                        {"role": role, "action": action, "acknowledged_sha256": ack},
                    )
                    with results_lock:
                        results[role].append(p_status)

                workers = [
                    threading.Thread(target=worker, args=(role,))
                    for role in ("p1", "p1", "p2", "p2")
                ]
                for w in workers:
                    w.start()
                for w in workers:
                    w.join(timeout=10)
                    self.assertFalse(w.is_alive())

                self.assertEqual(sorted(results["p1"]), [201, 409])
                self.assertEqual(sorted(results["p2"]), [201, 409])

                lines = receipts_path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 2)
                by_role = {json.loads(line)["role"]: json.loads(line) for line in lines}
                self.assertEqual(set(by_role.keys()), {"p1", "p2"})
                self.assertEqual(by_role["p1"]["action"], "cobalt")
                self.assertEqual(by_role["p2"]["action"], "coral")
                self.assertEqual(by_role["p1"]["turn"], 1)
                self.assertEqual(by_role["p2"]["turn"], 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_load_mode_rejects_invalid_values(self) -> None:
        with TemporaryDirectory() as tmp:
            bad_mode = Path(tmp) / "mode.json"
            bad_mode.write_text('"second"\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_mode(bad_mode)

            bad_case = Path(tmp) / "case.json"
            bad_case.write_text('{"options":{"p1":["a"]}}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_case(bad_case)


if __name__ == "__main__":
    unittest.main()
