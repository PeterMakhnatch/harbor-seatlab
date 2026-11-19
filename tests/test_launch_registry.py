import json
import tempfile
import unittest
from pathlib import Path

from seatlab.launch_registry import SCHEMA, verify_launch_registry
from seatlab.tape import TapeError, digest

A = "aaaa1111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"
SRC = "44444444-4444-4444-8444-444444444444"
W = "a" * 64


class LaunchRegistryTest(unittest.TestCase):
    def run_case(self, trials, received, jobs=("j1", "j2"), pin=None, **over):
        doc = {"schema": SCHEMA, "worklist_sha256": W, "trials": trials}
        doc.update(over)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reg.json"
            path.write_text(json.dumps(doc))
            verify_launch_registry(path, pin or digest(doc), W, set(jobs), {SRC}, received)

    def fails(self, *args, **kw):
        with self.assertRaises(TapeError):
            self.run_case(*args, **kw)

    def test_valid_with_unissued_and_missing(self):
        self.run_case({"j1": A, "j2": None}, {"j1": A})
        self.run_case({"j1": A, "j2": B}, {})

    def test_unissued_return_fails(self):
        self.fails({"j1": A, "j2": None}, {"j2": B})
        self.fails({"j1": A, "j2": None}, {"j2": A})

    def test_crossed_jobs_fail(self):
        self.fails({"j1": A, "j2": B}, {"j1": B, "j2": A})

    def test_pin_swap_fails(self):
        self.fails({"j1": A, "j2": B}, {}, pin="b" * 64)
        self.fails({"j1": A, "j2": B}, {}, worklist_sha256="c" * 64)

    def test_duplicate_including_missing_fails(self):
        self.fails({"j1": A, "j2": A}, {"j1": A})
        self.fails({"j1": A, "j2": A}, {})

    def test_expected_subset_and_missing_jobs(self):
        self.fails({"j1": A}, {"j1": A})
        self.fails({"j1": A, "j2": B, "j3": C}, {"j1": A})
        self.fails({"j1": A, "j2": B}, {"j3": C})

    def test_shape_and_uuid_rules(self):
        self.fails({"j1": A.upper(), "j2": None}, {})
        self.fails({"j1": A.replace("-", ""), "j2": None}, {})
        self.fails({"j1": SRC, "j2": None}, {})
        self.fails({"j1": A, "j2": None}, {}, schema="other")
        self.fails({"j1": A, "j2": None}, {}, extra=1)


if __name__ == "__main__":
    unittest.main()
