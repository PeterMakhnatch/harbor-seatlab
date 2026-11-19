"""Offline checks for sender identity and receipt validation."""

import json
import unittest

import observer
from native_seat import validate_receipt


class ObserverTests(unittest.TestCase):
    def test_probe_and_ack_shape(self):
        probe = "seatlab:probe:v1:" + "ab" * 16
        self.assertTrue(observer.PROBE_RE.match(probe))
        self.assertFalse(observer.PROBE_RE.match(probe + "\n"))
        ack = "seatlab:ack:v1:" + observer.sha(probe)
        self.assertEqual(observer.ACK_RE.match(ack).group(1), observer.sha(probe))

    def test_latest_chat_checks_sender(self):
        view = {"status": {"chat": {"from": "seat-1", "text": "x"}}}
        self.assertEqual(observer.latest_chat(view, "seat-1"), "x")
        self.assertIsNone(observer.latest_chat(view, "seat-2"))

    def test_validate_receipt(self):
        result = observer.new_result("seat-2", "no-read")
        raw = json.dumps(result)
        self.assertEqual(validate_receipt(raw, "seat-2", "no-read"), result)
        with self.assertRaises(ValueError):
            validate_receipt(raw, "seat-1", "no-read")
        result["explicit_probe_reads"] = 1
        with self.assertRaises(ValueError):
            validate_receipt(json.dumps(result), "seat-2", "no-read")
        result = observer.new_result("seat-2", "read")
        result["extra"] = 1
        with self.assertRaises(ValueError):
            validate_receipt(json.dumps(result), "seat-2", "read")


if __name__ == "__main__":
    unittest.main()
