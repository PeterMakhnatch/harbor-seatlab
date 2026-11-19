"""Harbor host agent: runs observer.py inside the bound seat. No model, no shell input."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_REMOTE_DIR = "/tmp/seatlab-native-chat"
_REMOTE = f"{_REMOTE_DIR}/observer.py"
_SCHEMA = "seatlab.native-chat-seat/v1"
_RECEIPT = "communication-receipt.json"
_MAX_STDOUT = 4096
_EXEC_SEC = 330  # 120 readiness + 120 arm wait + 12 reply + margin
_ROLES = ("seat-1", "seat-2")
_ARMS = ("read", "no-read")
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_KEYS = (
    "schema",
    "role",
    "arm",
    "running",
    "armed",
    "probe_sent_sha256",
    "probe_read_sha256",
    "ack_sent_sha256",
    "ack_read_sha256",
    "explicit_probe_reads",
)


def validate_receipt(raw: str, role: str, arm: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > _MAX_STDOUT:
        raise ValueError("receipt too large")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("receipt is not JSON") from None
    if not isinstance(value, dict) or set(value) != set(_KEYS):
        raise ValueError("receipt keys are invalid")
    if value["schema"] != _SCHEMA or value["role"] != role or value["arm"] != arm:
        raise ValueError("receipt identity is invalid")
    for key in ("running", "armed"):
        if type(value[key]) is not bool:
            raise ValueError("receipt boolean is invalid")
    for key in _KEYS[5:9]:
        item = value[key]
        if item is not None and not (type(item) is str and _HEX64.match(item)):
            raise ValueError("receipt digest is invalid")
    reads = value["explicit_probe_reads"]
    if type(reads) is not int or reads < 0 or reads > 100_000:
        raise ValueError("receipt count is invalid")
    if (role == "seat-1" or arm == "no-read") and reads != 0:
        raise ValueError("receipt read count is invalid")
    if role == "seat-1" and (value["probe_read_sha256"] or value["ack_sent_sha256"]):
        raise ValueError("receipt role fields are invalid")
    if role == "seat-2" and (value["probe_sent_sha256"] or value["ack_read_sha256"]):
        raise ValueError("receipt role fields are invalid")
    if arm == "no-read" and (value["probe_read_sha256"] or value["ack_sent_sha256"]):
        raise ValueError("receipt arm fields are invalid")
    return {key: value[key] for key in _KEYS}


class ChatObserver(BaseAgent):
    @staticmethod
    @override
    def name() -> str:
        return "native-chat-observer"

    @override
    def version(self) -> str:
        return "1"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        arm: str,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if model_name is not None:
            raise ValueError("ChatObserver does not support model_name")
        if extra_env:
            raise ValueError("ChatObserver does not support extra_env")
        if arm not in _ARMS:
            raise ValueError("arm must be read or no-read")
        self._arm = arm
        super().__init__(logs_dir, None, extra_env=None, **kwargs)

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        return

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        del instruction
        if not isinstance(environment, SeatEnvironment):
            raise TypeError("ChatObserver requires the bound SeatEnvironment")
        role = environment.service
        if role not in _ROLES:
            raise ValueError("unsupported seat")
        context.metadata = {
            "schema": _SCHEMA,
            "role": role,
            "arm": self._arm,
            "stage": "running",
        }
        try:
            result = await environment.exec(f"mkdir -p {_REMOTE_DIR}", timeout_sec=10)
            if result.return_code != 0:
                raise RuntimeError("observer install failed")
            await environment.upload_file(
                Path(__file__).resolve().with_name("observer.py"), _REMOTE
            )
            result = await environment.exec(
                f"python3 {_REMOTE} {self._arm}", timeout_sec=_EXEC_SEC
            )
            if result.return_code != 0:
                raise RuntimeError("observer failed")
            receipt = validate_receipt(result.stdout or "", role, self._arm)
            path = Path(self.logs_dir) / _RECEIPT
            data = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
        except Exception:
            context.metadata = {
                "schema": _SCHEMA,
                "role": role,
                "arm": self._arm,
                "stage": "failed",
            }
            raise
        context.metadata = {
            "schema": _SCHEMA,
            "role": role,
            "arm": self._arm,
            "stage": "done",
            "running": receipt["running"],
            "armed": receipt["armed"],
            "explicit_probe_reads": receipt["explicit_probe_reads"],
        }
