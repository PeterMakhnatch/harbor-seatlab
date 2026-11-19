"""Receipt-board world: serves role-scoped views and logs acknowledged commits.

HTTP topology adapted from Olam Harbor's examples/tasks/hello-seats world server.
This server records cooperative turn-1 acknowledgments for two prespecified roles
(p1, p2); it does not authenticate seat identity or defend against adversarial
clients.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast

ROLES: tuple[str, ...] = ("p1", "p2")
MODES: tuple[str, ...] = ("full", "first")
MAX_BODY_BYTES = 16 * 1024
DEFAULT_PORT = 8080
DEFAULT_RECEIPTS_PATH = Path("/logs/world/receipts.jsonl")
DEFAULT_WORLD_DIR = Path(__file__).resolve().parent


def canonical_json(value: object) -> bytes:
    """Encode a JSON value in canonical compact ASCII form without a newline."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def canonical_digest(value: object) -> str:
    """Return the lowercase SHA-256 hex digest of canonical JSON bytes."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _strict_loads(raw: bytes) -> object:
    def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        obj: dict[str, object] = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"Duplicate JSON key: {key!r}")
            obj[key] = value
        return obj

    def _reject_constant(token: str) -> object:
        raise ValueError(f"Non-finite JSON constant: {token!r}")

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_constant,
    )


def load_case(path: Path) -> dict[str, list[str]]:
    """Load and validate environment/case.json."""
    parsed = _strict_loads(path.read_bytes())
    if not isinstance(parsed, dict) or parsed.keys() != {"options"}:
        raise ValueError("case.json must be an object with exact key 'options'")
    options = parsed["options"]
    if not isinstance(options, dict) or set(options.keys()) != set(ROLES):
        raise ValueError(f"case.json options must define exact roles {ROLES}")
    validated: dict[str, list[str]] = {}
    for role in ROLES:
        values = options[role]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or not item for item in values)
        ):
            raise ValueError(f"options for {role!r} must be a non-empty list of strings")
        if len(set(values)) != len(values):
            raise ValueError(f"options for {role!r} must be distinct")
        validated[role] = list(values)
    return validated


def load_mode(path: Path) -> Literal["full", "first"]:
    """Load and validate environment/mode.json ('full' or 'first')."""
    parsed = _strict_loads(path.read_bytes())
    if not isinstance(parsed, str) or parsed not in MODES:
        raise ValueError("mode.json must be JSON string 'full' or 'first'")
    return cast(Literal["full", "first"], parsed)


class WorldError(Exception):
    """HTTP-mappable world validation or state error."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ReceiptBoardWorld:
    """Per-role view issuance and atomic turn-1 receipt commitment."""

    def __init__(
        self,
        case_options: dict[str, list[str]],
        mode: Literal["full", "first"],
        receipts_path: Path = DEFAULT_RECEIPTS_PATH,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"Unsupported mode: {mode!r}")
        if set(case_options.keys()) != set(ROLES):
            raise ValueError(f"Expected exact roles {ROLES}")
        self._views: dict[str, dict[str, object]] = {}
        self._digests: dict[str, str] = {}
        for role in ROLES:
            items = list(case_options[role])
            if not items or any(not isinstance(item, str) or not item for item in items):
                raise ValueError(f"Invalid options for role {role!r}")
            visible = list(items) if mode == "full" else [items[0]]
            view: dict[str, object] = {"role": role, "options": visible}
            self._views[role] = view
            self._digests[role] = canonical_digest(view)
        self._mode = mode
        self._receipts_path = receipts_path
        self._issued: set[str] = set()
        self._committed: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()
        self._receipts_path.parent.mkdir(parents=True, exist_ok=True)
        self._receipts_path.touch(exist_ok=True)

    @property
    def mode(self) -> Literal["full", "first"]:
        return self._mode

    @property
    def receipts_path(self) -> Path:
        return self._receipts_path

    def get_view(self, role: str) -> dict[str, object]:
        """Issue and return the role-scoped view for a valid role."""
        if role not in self._views:
            raise WorldError(404, f"unknown role: {role!r}")
        with self._lock:
            self._issued.add(role)
            view = self._views[role]
            return {"role": view["role"], "options": list(cast(list[str], view["options"]))}

    def submit_note(self, payload: object) -> dict[str, object]:
        """Validate and commit one turn-1 note per role; append receipt only on success."""
        if not isinstance(payload, dict):
            raise WorldError(400, "body must be a JSON object")
        expected_fields = {"role", "action", "acknowledged_sha256"}
        if payload.keys() != expected_fields:
            raise WorldError(
                400,
                "body must contain exact fields role, action, acknowledged_sha256",
            )
        role = payload["role"]
        action = payload["action"]
        ack = payload["acknowledged_sha256"]
        if not isinstance(role, str) or role not in self._views:
            raise WorldError(400, "unknown role")
        if not isinstance(action, str) or not action:
            raise WorldError(400, "action must be a non-empty string")
        if not isinstance(ack, str) or not ack:
            raise WorldError(400, "acknowledged_sha256 must be a non-empty string")

        with self._lock:
            if role in self._committed:
                raise WorldError(409, f"role {role!r} already committed")
            if role not in self._issued:
                raise WorldError(409, f"view not yet issued for role {role!r}")

            view = self._views[role]
            allowed_options = cast(list[str], view["options"])
            expected_digest = self._digests[role]

            if ack != expected_digest:
                raise WorldError(400, "acknowledged_sha256 does not match issued view")
            if action not in allowed_options:
                raise WorldError(400, "action is not in issued options")

            receipt: dict[str, object] = {
                "role": role,
                "turn": 1,
                "view": {"role": role, "options": list(allowed_options)},
                "acknowledged_sha256": ack,
                "action": action,
            }
            line = canonical_json(receipt) + b"\n"
            fd = os.open(
                self._receipts_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o644,
            )
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
            self._committed[role] = receipt
            return {
                "role": role,
                "turn": 1,
                "view": {"role": role, "options": list(allowed_options)},
                "acknowledged_sha256": ack,
                "action": action,
            }


class ReceiptBoardServer(ThreadingHTTPServer):
    """Threaded HTTP server owning one ReceiptBoardWorld instance."""

    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        world: ReceiptBoardWorld,
    ) -> None:
        self.world = world
        super().__init__(server_address, ReceiptBoardHandler)


class ReceiptBoardHandler(BaseHTTPRequestHandler):
    """Minimal JSON HTTP handler for /health, /view/<role>, and /note."""

    timeout = 5.0

    @property
    def _world(self) -> ReceiptBoardWorld:
        return cast(ReceiptBoardServer, self.server).world

    def log_message(self, format: str, *args: object) -> None:
        return

    def _reply(self, status: int, body: object) -> None:
        data = canonical_json(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> object:
        lengths = self.headers.get_all("Content-Length", [])
        if (
            len(lengths) != 1
            or self.headers.get("Transfer-Encoding") is not None
            or self.headers.get_content_type() != "application/json"
        ):
            raise WorldError(400, "expected length-delimited application/json body")
        try:
            length = int(lengths[0])
        except ValueError as exc:
            raise WorldError(400, "invalid Content-Length") from exc
        if not 0 < length <= MAX_BODY_BYTES:
            raise WorldError(400, "body length out of bounds")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise WorldError(400, "incomplete request body")
        try:
            return _strict_loads(raw)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise WorldError(400, "invalid JSON body") from exc

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, {"ok": True})
            return
        if self.path.startswith("/view/"):
            role = self.path.removeprefix("/view/")
            try:
                view = self._world.get_view(role)
            except WorldError as exc:
                self._reply(exc.status, {"error": exc.message})
                return
            self._reply(200, view)
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/note":
            self._reply(404, {"error": "not found"})
            return
        try:
            payload = self._read_json_body()
            receipt = self._world.submit_note(payload)
        except WorldError as exc:
            self._reply(exc.status, {"error": exc.message})
            return
        except OSError:
            self._reply(500, {"error": "could not persist receipt"})
            return
        self._reply(201, receipt)


def build_world(
    world_dir: Path = DEFAULT_WORLD_DIR,
    receipts_path: Path = DEFAULT_RECEIPTS_PATH,
) -> ReceiptBoardWorld:
    """Construct a world from case.json and mode.json in world_dir."""
    case_options = load_case(world_dir / "case.json")
    mode = load_mode(world_dir / "mode.json")
    return ReceiptBoardWorld(case_options, mode, receipts_path=receipts_path)


def main() -> None:
    world = build_world()
    with ReceiptBoardServer(("0.0.0.0", DEFAULT_PORT), world) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
