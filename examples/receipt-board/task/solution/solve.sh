#!/bin/bash
set -euo pipefail

exec python3 - <<'PY'
import hashlib
import json
import os
from urllib.request import ProxyHandler, Request, build_opener

seat = os.environ["SEAT"]
if seat not in ("p1", "p2"):
    raise ValueError(f"unexpected SEAT: {seat!r}")

base = "http://world:8080"
http = build_opener(ProxyHandler({}))

with http.open(f"{base}/view/{seat}", timeout=10) as response:
    if response.status != 200:
        raise RuntimeError(f"GET /view/{seat} returned status {response.status}")
    view = json.loads(response.read().decode("utf-8"))

if not isinstance(view, dict) or set(view.keys()) != {"role", "options"}:
    raise ValueError(f"unexpected view object: {view!r}")
if view["role"] != seat:
    raise ValueError(f"view role {view['role']!r} does not match SEAT {seat!r}")
options = view["options"]
if (
    not isinstance(options, list)
    or not options
    or any(not isinstance(item, str) or not item for item in options)
):
    raise ValueError(f"invalid options in view: {options!r}")

canonical_view = json.dumps(
    view,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=True,
    allow_nan=False,
).encode("ascii")
ack_sha256 = hashlib.sha256(canonical_view).hexdigest()
action = options[-1]

payload = json.dumps(
    {"role": seat, "action": action, "acknowledged_sha256": ack_sha256},
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=True,
    allow_nan=False,
).encode("ascii")

request = Request(
    f"{base}/note",
    method="POST",
    headers={"Content-Type": "application/json"},
    data=payload,
)
with http.open(request, timeout=10) as response:
    if response.status != 201:
        raise RuntimeError(f"POST /note returned status {response.status}")
    receipt = json.loads(response.read().decode("utf-8"))

if receipt.get("role") != seat or receipt.get("action") != action:
    raise ValueError(f"unexpected receipt response: {receipt!r}")
print(f"{seat} committed {action}")
PY
