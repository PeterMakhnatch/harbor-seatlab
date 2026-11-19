"""Seat-side native chat observer. Standard library only.

Usage: python3 observer.py read|no-read
Uses only /, /ready, /update and /say. Submits no game actions.
Prints one JSON object that has the fixed receipt fields. It never prints chat text,
the challenge, the token, a URL or a response body.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = "seatlab.native-chat-seat/v1"
MARKER = "seatlab:armed:v1"
PROBE_RE = re.compile(r"\Aseatlab:probe:v1:[0-9a-f]{32}\Z")
ACK_RE = re.compile(r"\Aseatlab:ack:v1:([0-9a-f]{64})\Z")
READY_SEC = 120.0
CALL_SEC = 5.0
ARM_WAIT_SEC = 120.0
ACK_WAIT_SEC = {"read": 12.0, "no-read": 8.0}
PROBE_WAIT_SEC = 10.0
NO_READ_WAIT_SEC = 8.0
POLL_SEC = 0.25
MAX_BODY = 1_048_576
TOKEN_FILE = "/run/mindustry/token"
SEATS = ("seat-1", "seat-2")
PEER = {"seat-1": "seat-2", "seat-2": "seat-1"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # never forward the token
        return None


class Client:
    """Talks only to the one configured game base URL. Token is held in memory only."""

    def __init__(self) -> None:
        base = os.environ.get("MINDUSTRY_SIM_URL", "").rstrip("/")
        parts = urllib.parse.urlsplit(base)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.query
            or parts.fragment
        ):
            raise ValueError("bad game URL")
        self._base = base
        with open(TOKEN_FILE, encoding="utf-8") as handle:
            self._token = handle.read(4096).strip()
        if not self._token:
            raise ValueError("empty token")
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect
        )

    def call(self, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(
            self._base + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
            },
            method="GET" if body is None else "POST",
        )
        with self._opener.open(request, timeout=CALL_SEC) as response:
            raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise ValueError("response too large")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or value.get("error"):
            raise ValueError("game error")
        return value


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_result(role: str, arm: str) -> dict:
    return {
        "schema": SCHEMA,
        "role": role,
        "arm": arm,
        "running": False,
        "armed": False,
        "probe_sent_sha256": None,
        "probe_read_sha256": None,
        "ack_sent_sha256": None,
        "ack_read_sha256": None,
        "explicit_probe_reads": 0,
    }


def barrier(client: Client, role: str) -> bool:
    deadline = time.monotonic() + READY_SEC
    registered = False
    while time.monotonic() < deadline:
        try:
            if not registered:
                if client.call("/").get("you") != role:
                    return False
                client.call("/ready", {})
                registered = True
            view = client.call("/update")
            if view.get("game_over") is True:
                return False
            status = view.get("status")
            if isinstance(status, dict) and status.get("phase") == "running":
                return True
        except (OSError, ValueError):
            pass
        time.sleep(POLL_SEC)
    return False


def say(client: Client, role: str, text: str) -> bool:
    try:
        message = client.call("/say", {"text": text}).get("message")
    except (OSError, ValueError):
        return False
    return (
        isinstance(message, dict)
        and message.get("from") == role
        and message.get("text") == text
    )


def latest_chat(view: dict, sender: str) -> str | None:
    status = view.get("status")
    chat = status.get("chat") if isinstance(status, dict) else None
    if (
        isinstance(chat, dict)
        and chat.get("from") == sender
        and isinstance(chat.get("text"), str)
    ):
        return chat["text"]
    return None


def poll(client: Client, sender: str, accept, seconds: float, counter: list | None = None):
    """Poll GET /update until accept(text) is truthy or the deadline passes."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if counter is not None:
            counter[0] += 1
        try:
            text = latest_chat(client.call("/update"), sender)
        except (OSError, ValueError):
            text = None
        if text is not None:
            found = accept(text)
            if found:
                return text
        time.sleep(POLL_SEC)
    return None


def run_seat_1(client: Client, result: dict) -> None:
    arm = result["arm"]
    if poll(client, "seat-2", lambda t: t == MARKER, ARM_WAIT_SEC) is None:
        return
    result["armed"] = True
    probe = "seatlab:probe:v1:" + secrets.token_hex(16)
    if not say(client, "seat-1", probe):
        return
    digest = sha(probe)
    result["probe_sent_sha256"] = digest

    def good_ack(text: str) -> bool:
        match = ACK_RE.match(text)
        return bool(match) and match.group(1) == digest

    if poll(client, "seat-2", good_ack, ACK_WAIT_SEC[arm]) is not None:
        result["ack_read_sha256"] = digest


def run_seat_2(client: Client, result: dict) -> None:
    arm = result["arm"]
    if not say(client, "seat-2", MARKER):
        return
    result["armed"] = True
    if arm == "no-read":
        time.sleep(NO_READ_WAIT_SEC)  # no further API calls
        return
    counter = [0]
    try:
        probe = poll(
            client, "seat-1", lambda t: bool(PROBE_RE.match(t)), PROBE_WAIT_SEC, counter
        )
    finally:
        result["explicit_probe_reads"] = counter[0]
    if probe is None:
        return
    digest = sha(probe)
    result["probe_read_sha256"] = digest
    if say(client, "seat-2", "seatlab:ack:v1:" + digest):
        result["ack_sent_sha256"] = digest


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("read", "no-read"):
        print("usage: observer.py read|no-read", file=sys.stderr)
        return 2
    role = os.environ.get("MINDUSTRY_SEAT", "")
    if role not in SEATS:
        print("unsupported seat", file=sys.stderr)
        return 2
    result = new_result(role, argv[1])
    try:
        client = Client()
        result["running"] = barrier(client, role)
        if result["running"]:
            (run_seat_1 if role == "seat-1" else run_seat_2)(client, result)
    except Exception:
        print("observer failed", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
