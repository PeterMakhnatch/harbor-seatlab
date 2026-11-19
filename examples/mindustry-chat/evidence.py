#!/usr/bin/env python3
"""Join the original game journal with the two participant receipts.

Usage: python evidence.py TRIAL --arm {read,no-read} --out NEW.json

Output holds only fixed codes, booleans, digests-derived facts, the trial UUID
and the native reward. It never copies message text, paths, tokens, errors or
raw record fields. A fact that the data cannot support is null, never false.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import uuid

SCHEMA = "seatlab.native-chat-evidence/v1"
RECEIPT_SCHEMA = "seatlab.native-chat-seat/v1"
SENDER, RECEIVER = "seat-1", "seat-2"
ARMED_TEXT = "seatlab:armed:v1"
PROBE_RE = re.compile(r"seatlab:probe:v1:[0-9a-f]{32}")
ACK_RE = re.compile(r"seatlab:ack:v1:([0-9a-f]{64})")
HEX64 = re.compile(r"[0-9a-f]{64}")
RECEIPT_KEYS = {
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
}
DIGEST_FIELDS = (
    "probe_sent_sha256",
    "probe_read_sha256",
    "ack_sent_sha256",
    "ack_read_sha256",
)

MAX_LINE = 1 << 20
MAX_LINES = 2_000_000
MAX_BYTES = 512 << 20
MAX_JSON_FILE = 8 << 20
MAX_RECEIPT = 64 << 10
MAX_RELEVANT = 64  # more candidate records than this is already invalid

JOURNAL_PATHS = (
    ("artifacts", "logs", "sim", "journal.jsonl"),
    ("logs", "sim", "journal.jsonl"),
)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _read_json_file(path, limit):
    """Return (state, value); state in ok/missing/bad. Never raises."""
    try:
        if not os.path.isfile(path):
            return "missing", None
        if os.path.getsize(path) > limit:
            return "bad", None
        with open(path, "rb") as fh:
            data = fh.read(limit + 1)
        if len(data) > limit:
            return "bad", None
        return "ok", json.loads(data.decode("utf-8"))
    except (OSError, ValueError):
        return "bad", None


# ---------------------------------------------------------------- native result
def read_native(trial):
    """Return (trial_id, reward, disposition, result_state)."""
    state, res = _read_json_file(os.path.join(trial, "result.json"), MAX_JSON_FILE)
    if state == "missing":
        return None, None, "result_missing", "missing"
    if state == "bad" or not isinstance(res, dict):
        return None, None, "result_unreadable", "bad"
    trial_id = None
    for key in ("id", "trial_id"):
        cand = res.get(key)
        if isinstance(cand, str):
            try:
                if str(uuid.UUID(cand)) == cand.lower():
                    trial_id = cand.lower()
                    break
            except ValueError:
                pass
    vr = res.get("verifier_result")
    valid = res.get("reward_valid")
    if valid is None and isinstance(vr, dict):
        valid = vr.get("reward_valid")
    if not isinstance(vr, dict):
        return trial_id, None, "reward_not_emitted", "ok"
    rewards = vr.get("rewards")
    value = rewards.get("reward") if isinstance(rewards, dict) else None
    if not _is_int(value) and not (isinstance(value, float) and math.isfinite(value)):
        return trial_id, None, "reward_not_emitted", "ok"
    if valid is not True:
        return trial_id, None, "reward_withheld_not_valid", "ok"
    return trial_id, value, "reward_emitted", "ok"


# ---------------------------------------------------------------- journal
def _classify(text):
    """Return kind for text that claims to be one of our protocol messages."""
    if text == ARMED_TEXT:
        return "armed"
    if PROBE_RE.fullmatch(text):
        return "probe"
    if ACK_RE.fullmatch(text):
        return "ack"
    if text.startswith("seatlab:"):
        return "malformed"
    return None


def read_journal(trial):
    """Return (state, events, reasons). state: ok/missing/incomplete."""
    path = None
    for parts in JOURNAL_PATHS:
        cand = os.path.join(trial, *parts)
        if os.path.isfile(cand):
            path = cand
            break
    if path is None:
        return "missing", [], set()
    events, reasons = [], set()
    seen_seq = set()
    total = lines = 0
    incomplete = False
    try:
        with open(path, "rb") as fh:
            while True:
                raw = fh.readline(MAX_LINE + 1)
                if not raw:
                    break
                total += len(raw)
                lines += 1
                if len(raw) > MAX_LINE or lines > MAX_LINES or total > MAX_BYTES:
                    incomplete = True
                    break
                if b"seatlab:" not in raw:
                    continue  # cheap skip; cannot be ours
                try:
                    entry = json.loads(raw)
                except ValueError:
                    reasons.add("journal_line_unparsable")
                    continue
                ev = _entry_event(entry, seen_seq, reasons)
                if ev is not None:
                    events.append(ev)
                    if len(events) > MAX_RELEVANT:
                        reasons.add("too_many_protocol_records")
                        break
    except OSError:
        return "missing", [], set()
    return ("incomplete" if incomplete else "ok"), events, reasons


def _entry_event(entry, seen_seq, reasons):
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if not isinstance(value, dict) or value.get("type") != "say":
        return None
    args, result = value.get("arguments"), value.get("result")
    text = args.get("text") if isinstance(args, dict) else None
    if not isinstance(text, str):
        return None
    kind = _classify(text)
    if kind is None:
        return None
    seq = entry.get("sequence")
    if not _is_int(seq) or seq in seen_seq:
        reasons.add("bad_or_duplicate_sequence")
        return None
    seen_seq.add(seq)
    if not isinstance(result, dict) or "error" in result or result.get("ok") is False:
        return None  # not accepted by the game
    msg = result.get("message")
    seat = value.get("seat")
    if (
        not isinstance(msg, dict)
        or msg.get("text") != text
        or msg.get("from") != seat
        or not isinstance(seat, str)
        or msg.get("audience", "team") != "team"
    ):
        reasons.add("accepted_post_shape_mismatch")
        return None
    if kind == "malformed":
        reasons.add("malformed_protocol_message")
        return None
    return {"seq": seq, "seat": seat, "kind": kind, "text": text}


# ---------------------------------------------------------------- receipts
def read_receipt(trial, role, arm):
    path = os.path.join(trial, "seats", role, "agent", "communication-receipt.json")
    state, rec = _read_json_file(path, MAX_RECEIPT)
    if state == "missing":
        return "missing", None
    if state == "bad" or not isinstance(rec, dict) or set(rec) != RECEIPT_KEYS:
        return "invalid", None
    if (
        rec["schema"] != RECEIPT_SCHEMA
        or rec["role"] != role
        or rec["arm"] != arm
        or not isinstance(rec["running"], bool)
        or not isinstance(rec["armed"], bool)
        or not _is_int(rec["explicit_probe_reads"])
        or rec["explicit_probe_reads"] < 0
    ):
        return "invalid", None
    for f in DIGEST_FIELDS:
        v = rec[f]
        if v is not None and not (isinstance(v, str) and HEX64.fullmatch(v)):
            return "invalid", None
    return "ok", rec


# ---------------------------------------------------------------- join
def analyze(trial, arm):
    reasons = set()
    trial_id, reward, disposition, result_state = read_native(trial)
    jstate, events, jreasons = read_journal(trial)
    reasons |= jreasons
    r1s, r1 = read_receipt(trial, SENDER, arm)
    r2s, r2 = read_receipt(trial, RECEIVER, arm)
    if r1s == "invalid" or r2s == "invalid":
        reasons.add("receipt_invalid")

    armed = [e for e in events if e["kind"] == "armed"]
    probes = [e for e in events if e["kind"] == "probe"]
    acks = [e for e in events if e["kind"] == "ack"]
    for group, seat in ((armed, RECEIVER), (probes, SENDER), (acks, RECEIVER)):
        if any(e["seat"] != seat for e in group):
            reasons.add("wrong_sender")
        if len(group) > 1:
            reasons.add("duplicate_protocol_message")
    digest = None
    if probes:
        digest = sha256_text(probes[0]["text"])
        if not armed or armed[0]["seq"] > probes[0]["seq"]:
            reasons.add("probe_not_after_arming")
    ack_ok = False
    if acks:
        if not probes:
            reasons.add("ack_without_probe")
        elif ACK_RE.fullmatch(acks[0]["text"]).group(1) != digest:
            reasons.add("ack_digest_mismatch")
        elif acks[0]["seq"] < probes[0]["seq"]:
            reasons.add("ack_before_probe")
        else:
            ack_ok = True

    journal_ok = jstate == "ok"
    if journal_ok:
        _check_receipts(r1, r2, arm, bool(armed), digest, ack_ok, reasons)
    elif jstate == "incomplete":
        reasons.add("journal_incomplete")

    invalid = bool(reasons - {"journal_incomplete"})
    # journal evidence that exists but is incomplete cannot prove absence
    facts = {
        k: None
        for k in (
            "transmit_accepted",
            "recipient_explicit_read",
            "dependent_reply_accepted",
            "sender_saw_ack",
        )
    }
    if not invalid:
        if journal_ok:
            facts["transmit_accepted"] = bool(probes)
            facts["dependent_reply_accepted"] = ack_ok
        elif jstate == "incomplete" and probes:
            facts["transmit_accepted"] = True
        if r2 is not None and (digest is not None or journal_ok):
            facts["recipient_explicit_read"] = bool(
                digest is not None
                and r2["probe_read_sha256"] == digest
                and r2["explicit_probe_reads"] >= 1
            )
        if r1 is not None and (digest is not None or journal_ok):
            facts["sender_saw_ack"] = bool(
                digest is not None and r1["ack_read_sha256"] == digest
            )

    present = [result_state == "ok", jstate != "missing", r1s != "missing", r2s != "missing"]
    if invalid:
        status = "invalid"
    elif not any(present):
        status = "missing"
    elif all(present) and jstate == "ok" and trial_id is not None:
        status = "complete"
    else:
        status = "insufficient"

    protocol = None
    if status == "invalid":
        protocol = "invalid"
    elif status == "complete":
        ready = all(rec["running"] and rec["armed"] for rec in (r1, r2))
        if not ready:
            protocol = "not_established"
        elif arm == "read":
            protocol = (
                "established" if all(v is True for v in facts.values()) else "not_established"
            )
        else:
            protocol = (
                "no_read_observed"
                if (
                    facts["transmit_accepted"] is True
                    and facts["recipient_explicit_read"] is False
                    and facts["dependent_reply_accepted"] is False
                )
                else "not_established"
            )

    return {
        "schema": SCHEMA,
        "native_trial_id": trial_id,
        "status": status,
        "arm": arm,
        "protocol_result": protocol,
        "evidence": {
            "result": result_state,
            "journal": jstate,
            "sender_receipt": r1s,
            "receiver_receipt": r2s,
        },
        "facts": facts,
        "native_reward": reward,
        "native_reward_disposition": disposition,
        "reasons": sorted(reasons),
        "notes": (
            "no-read does not show the world withheld the message; "
            "a living core when the programs stop is not a score of zero"
        ),
    }


def _check_receipts(r1, r2, arm, armed_ok, digest, ack_ok, reasons):
    def bad(code):
        reasons.add(code)

    for rec in (r1, r2):
        if rec is not None and rec["armed"] and not armed_ok:
            bad("receipt_claims_unaccepted_arming")
    if r1 is not None:
        if (
            r1["probe_read_sha256"] is not None
            or r1["ack_sent_sha256"] is not None
            or r1["explicit_probe_reads"] != 0
        ):
            bad("receipt_wrong_role_fields")
        if r1["probe_sent_sha256"] is not None and r1["probe_sent_sha256"] != digest:
            bad("sent_digest_mismatch")
        if r1["ack_read_sha256"] is not None and not (
            ack_ok and r1["ack_read_sha256"] == digest
        ):
            bad("ack_read_claim_unsupported")
    if r2 is not None:
        if r2["probe_sent_sha256"] is not None or r2["ack_read_sha256"] is not None:
            bad("receipt_wrong_role_fields")
        pr, reads = r2["probe_read_sha256"], r2["explicit_probe_reads"]
        if pr is not None and (pr != digest or reads < 1):
            bad("read_claim_unsupported")
        if r2["ack_sent_sha256"] is not None and not (
            ack_ok and r2["ack_sent_sha256"] == digest
        ):
            bad("ack_sent_claim_unsupported")
        if ack_ok and pr is None:
            bad("reply_without_recorded_read")
        if arm == "no-read" and (
            pr is not None or reads > 0 or ack_ok or r2["ack_sent_sha256"] is not None
        ):
            bad("no_read_arm_contradiction")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("trial")
    ap.add_argument("--arm", required=True, choices=("read", "no-read"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = analyze(a.trial, a.arm)
    parent = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(parent, exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
