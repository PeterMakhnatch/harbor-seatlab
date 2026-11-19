import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence as ev  # noqa: E402

UUID = "123e4567-e89b-42d3-a456-426614174000"
PROBE = "seatlab:probe:v1:" + "ab" * 16
D = ev.sha256_text(PROBE)
ACK = "seatlab:ack:v1:" + D
ARMED = "seatlab:armed:v1"


def say(seq, seat, text, error=False, frm=None):
    res = (
        {"error": "x"}
        if error
        else {
            "message": {
                "id": "message-%d" % seq,
                "from": frm or seat,
                "team": "sharded",
                "audience": "team",
                "to": [],
                "reply_to": None,
                "text": text,
                "data": None,
            }
        }
    )
    return {
        "sequence": seq,
        "value": {"type": "say", "seat": seat, "arguments": {"text": text}, "result": res},
    }


def receipt(role, arm, **kw):
    r = {
        "schema": ev.RECEIPT_SCHEMA,
        "role": role,
        "arm": arm,
        "running": True,
        "armed": False,
        "probe_sent_sha256": None,
        "probe_read_sha256": None,
        "ack_sent_sha256": None,
        "ack_read_sha256": None,
        "explicit_probe_reads": 0,
    }
    r.update(kw)
    return r


def build(root, arm, journal, r1=None, r2=None, result=None):
    def w(path, obj, lines=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            if lines:
                for o in obj:
                    fh.write(json.dumps(o) + "\n")
            else:
                json.dump(obj, fh)

    if journal is not None:
        w(os.path.join(root, "artifacts/logs/sim/journal.jsonl"), journal, True)
    for role, r in (("seat-1", r1), ("seat-2", r2)):
        if r is not None:
            w(os.path.join(root, "seats", role, "agent", "communication-receipt.json"), r)
    if result is not None:
        w(os.path.join(root, "result.json"), result)


def good_result(reward=12.5, valid=True):
    return {
        "id": UUID,
        "reward_valid": valid,
        "verifier_result": {"rewards": {"reward": reward}},
    }


class T(unittest.TestCase):
    def run_case(self, arm, **kw):
        with tempfile.TemporaryDirectory() as d:
            build(d, arm, **kw)
            return ev.analyze(d, arm)

    def read_full(self, **over):
        j = [say(1, "seat-2", ARMED), say(2, "seat-1", PROBE), say(3, "seat-2", ACK)]
        r1 = receipt("seat-1", "read", armed=True, probe_sent_sha256=D, ack_read_sha256=D)
        r2 = receipt(
            "seat-2",
            "read",
            armed=True,
            probe_read_sha256=D,
            ack_sent_sha256=D,
            explicit_probe_reads=1,
        )
        kw = dict(journal=j, r1=r1, r2=r2, result=good_result())
        kw.update(over)
        return self.run_case("read", **kw)

    def test_accepted_and_read_established(self):
        o = self.read_full()
        self.assertEqual(o["status"], "complete")
        self.assertEqual(o["protocol_result"], "established")
        self.assertEqual(o["native_trial_id"], UUID)
        self.assertEqual(o["native_reward"], 12.5)
        self.assertTrue(all(v is True for v in o["facts"].values()))
        self.assertNotIn(PROBE, json.dumps(o))

    def test_no_read_accepted_but_not_read(self):
        j = [say(1, "seat-2", ARMED), say(2, "seat-1", PROBE)]
        o = self.run_case(
            "no-read",
            journal=j,
            result=good_result(),
            r1=receipt("seat-1", "no-read", armed=True, probe_sent_sha256=D),
            r2=receipt("seat-2", "no-read", armed=True),
        )
        self.assertEqual(o["status"], "complete")
        self.assertEqual(o["protocol_result"], "no_read_observed")
        self.assertIs(o["facts"]["transmit_accepted"], True)
        self.assertIs(o["facts"]["recipient_explicit_read"], False)
        self.assertIs(o["facts"]["dependent_reply_accepted"], False)
        self.assertIn("does not show", o["notes"])

    def test_no_read_arm_with_reply_is_invalid(self):
        o = self.run_case(
            "no-read",
            result=good_result(),
            journal=[say(1, "seat-2", ARMED), say(2, "seat-1", PROBE), say(3, "seat-2", ACK)],
            r1=receipt("seat-1", "no-read", probe_sent_sha256=D),
            r2=receipt("seat-2", "no-read", armed=True),
        )
        self.assertEqual(o["status"], "invalid")
        self.assertIsNone(o["facts"]["transmit_accepted"])

    def test_absent_trial_is_missing_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            o = ev.analyze(os.path.join(d, "nope"), "read")
        self.assertEqual(o["status"], "missing")
        self.assertIsNone(o["native_trial_id"])
        self.assertIsNone(o["native_reward"])
        self.assertEqual(o["native_reward_disposition"], "result_missing")
        self.assertTrue(all(v is None for v in o["facts"].values()))

    def test_missing_receipt_is_null_not_false(self):
        j = [say(1, "seat-2", ARMED), say(2, "seat-1", PROBE)]
        o = self.run_case(
            "read",
            journal=j,
            result=good_result(),
            r1=receipt("seat-1", "read", probe_sent_sha256=D),
        )
        self.assertEqual(o["status"], "insufficient")
        self.assertIsNone(o["facts"]["recipient_explicit_read"])
        self.assertIs(o["facts"]["transmit_accepted"], True)
        self.assertIsNone(o["protocol_result"])

    def test_reward_zero_vs_absent(self):
        o0 = self.read_full(result=good_result(0.0))
        self.assertEqual(o0["native_reward"], 0.0)
        res = {"id": UUID, "reward_valid": False, "verifier_result": None}
        o1 = self.read_full(result=res)
        self.assertIsNone(o1["native_reward"])
        self.assertEqual(o1["native_reward_disposition"], "reward_not_emitted")
        o2 = self.read_full(result=good_result(3, valid=False))
        self.assertIsNone(o2["native_reward"])
        self.assertEqual(o2["native_reward_disposition"], "reward_withheld_not_valid")

    def test_wrong_digest_ack_invalid(self):
        bad = "seatlab:ack:v1:" + "0" * 64
        o = self.read_full(
            journal=[say(1, "seat-2", ARMED), say(2, "seat-1", PROBE), say(3, "seat-2", bad)]
        )
        self.assertEqual(o["status"], "invalid")
        self.assertIn("ack_digest_mismatch", o["reasons"])
        self.assertIsNone(o["facts"]["dependent_reply_accepted"])

    def test_wrong_digest_in_receipt_invalid(self):
        r2 = receipt(
            "seat-2",
            "read",
            armed=True,
            probe_read_sha256="1" * 64,
            ack_sent_sha256=D,
            explicit_probe_reads=1,
        )
        o = self.read_full(r2=r2)
        self.assertEqual(o["status"], "invalid")

    def test_rejected_post_not_counted(self):
        j = [say(1, "seat-2", ARMED), say(2, "seat-1", PROBE, error=True)]
        o = self.run_case(
            "read",
            journal=j,
            result=good_result(),
            r1=receipt("seat-1", "read"),
            r2=receipt("seat-2", "read", armed=True),
        )
        self.assertEqual(o["status"], "complete")
        self.assertIs(o["facts"]["transmit_accepted"], False)
        self.assertEqual(o["protocol_result"], "not_established")

    def test_poll_without_receiving_probe_is_not_a_digest_claim(self):
        o = self.run_case(
            "read",
            journal=[say(1, "seat-2", ARMED), say(2, "seat-1", PROBE)],
            result=good_result(),
            r1=receipt("seat-1", "read", armed=True, probe_sent_sha256=D),
            r2=receipt("seat-2", "read", armed=True, explicit_probe_reads=4),
        )
        self.assertEqual(o["status"], "complete")
        self.assertEqual(o["protocol_result"], "not_established")
        self.assertIs(o["facts"]["recipient_explicit_read"], False)

    def test_probe_before_arming_and_duplicates_invalid(self):
        o = self.read_full(journal=[say(1, "seat-1", PROBE), say(2, "seat-2", ARMED)])
        self.assertIn("probe_not_after_arming", o["reasons"])
        o = self.read_full(
            journal=[
                say(1, "seat-2", ARMED),
                say(2, "seat-1", PROBE),
                say(3, "seat-1", PROBE),
                say(4, "seat-2", ACK),
            ]
        )
        self.assertIn("duplicate_protocol_message", o["reasons"])
        self.assertEqual(o["status"], "invalid")

    def test_spoofed_seat_and_wrong_sender_invalid(self):
        o = self.read_full(
            journal=[
                say(1, "seat-2", ARMED),
                say(2, "seat-1", PROBE, frm="seat-2"),
                say(3, "seat-2", ACK),
            ]
        )
        self.assertEqual(o["status"], "invalid")
        o = self.read_full(
            journal=[say(1, "seat-2", ARMED), say(2, "seat-2", PROBE), say(3, "seat-2", ACK)]
        )
        self.assertIn("wrong_sender", o["reasons"])

    def test_receipt_extra_key_invalid(self):
        r2 = receipt(
            "seat-2",
            "read",
            armed=True,
            probe_read_sha256=D,
            ack_sent_sha256=D,
            explicit_probe_reads=1,
        )
        r2["extra"] = 1
        o = self.read_full(r2=r2)
        self.assertEqual(o["status"], "invalid")
        self.assertIn("receipt_invalid", o["reasons"])

    def test_bad_uuid_is_null(self):
        res = good_result()
        res["id"] = "not-a-uuid"
        self.assertIsNone(self.read_full(result=res)["native_trial_id"])

    def test_oversized_line_leaves_journal_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            j = [say(1, "seat-2", ARMED), say(2, "seat-1", PROBE), say(3, "seat-2", ACK)]
            build(
                d,
                "read",
                j,
                receipt("seat-1", "read", armed=True, probe_sent_sha256=D, ack_read_sha256=D),
                receipt(
                    "seat-2",
                    "read",
                    armed=True,
                    probe_read_sha256=D,
                    ack_sent_sha256=D,
                    explicit_probe_reads=1,
                ),
                good_result(),
            )
            p = os.path.join(d, "artifacts/logs/sim/journal.jsonl")
            with open(p, "a") as fh:
                fh.write("x" * (ev.MAX_LINE + 10) + "\n")
            out = os.path.join(d, "o", "new.json")
            self.assertEqual(ev.main([d, "--arm", "read", "--out", out]), 0)
            with open(out) as fh:
                o = json.load(fh)
            self.assertEqual(o["status"], "insufficient")
            self.assertIsNone(o["protocol_result"])


if __name__ == "__main__":
    unittest.main()
