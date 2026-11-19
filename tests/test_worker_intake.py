"""collect_jobs: worker returns for a build_matrix source become one verified collection.

Fixtures reuse test_matrix's synthetic two-role trial writer; no game or model runs.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import test_matrix as fixtures  # helpers only; TestCase stays out of this module's namespace
from seatlab.matrix import build_matrix
from seatlab.worker_intake import collect_jobs

SCHEMA = "seatlab.worker-collection/v1"
JOBS = ["d00.aa", "d00.ab", "d01.aa", "d01.ab"]
PINS = ("study_sha256", "design_sha256", "worklist_sha256", "candidate_matrix_sha256")
SOURCE_UUID = {"d00.aa": 100, "d00.ab": 101, "d01.aa": 102, "d01.ab": 103}
INVALID = (ValueError, OSError)


def _counts(collection):
    return collection["recheck"]


class CollectJobs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        # One equal pair and one different pair force selection of both cases.
        cases = {}
        for case in ("d00", "d01"):
            cases[case] = {}
            for arm in ("aa", "ab"):
                job = f"{case}.{arm}"
                fixtures._trial(
                    self.root / "in" / job, case, SOURCE_UUID[job], int(job == "d01.ab")
                )
                cases[case][arm] = job
        doc = {
            "schema": "seatlab.panel-matrix/v1",
            "contrasts": {"name": dict(fixtures.CONTRAST)},
            "criteria": [
                fixtures.CRITERIA[0],
                dict(fixtures.CRITERIA[0], id="differs", when="different"),
            ],
            "cases": cases,
        }
        (self.root / "in" / "matrix.json").write_text(json.dumps(doc))
        self.source = self.root / "source"
        self.pins = build_matrix(self.root / "in" / "matrix.json", self.source)
        self.returns = self.root / "returns"
        self.returns.mkdir()
        self.out = self.root / "collected"
        self.counter = 0

    # ---- helpers ----------------------------------------------------------

    def job(self, name, value=0, uuid=None):
        """Write one return for job `name`; fresh UUID unless given."""
        if uuid is None:
            self.counter += 1
            uuid = 1100 + self.counter
        fixtures._trial(self.returns / name, name.split(".")[0], uuid, value)

    def jobs(self, names=JOBS, **values):
        for name in names:
            self.job(name, values.get(name.replace(".", "_"), int(name == "d01.ab")))

    def receipt(self, **over):
        path = self.root / "receipt.json"
        path.write_text(json.dumps({**self.pins, **over}))
        return path

    def collect(self, **over):
        return collect_jobs(self.source, self.returns, self.receipt(**over), self.out)

    def published(self):
        return json.loads((self.out / "collection.json").read_text())

    def assertInvalid(self, **over):
        with self.assertRaises(INVALID):
            self.collect(**over)
        self.assertFalse(os.path.lexists(self.out))

    # ---- accepted outcomes ------------------------------------------------

    def test_complete_unchanged_returns_match(self):
        self.jobs()
        report = self.collect()
        self.assertEqual(report, self.published())
        self.assertEqual(report["schema"], SCHEMA)
        self.assertEqual(report["status"], "matched")
        self.assertEqual(report["jobs"], {"expected": JOBS, "received": JOBS, "missing": []})
        self.assertEqual(_counts(report)["unknown"], 0)
        self.assertEqual(_counts(report)["changed"], 0)
        self.assertTrue((self.out / "candidate").is_dir())
        self.assertTrue((self.out / "recheck.json").is_file())

    def test_changed_return_is_changed(self):
        self.jobs(d00_ab=1)
        report = self.collect()
        self.assertEqual(report["status"], "changed")
        self.assertEqual(_counts(report)["changed"], 2)
        self.assertEqual(report["jobs"]["missing"], [])

    def test_one_missing_job_is_incomplete_never_matched(self):
        self.jobs([j for j in JOBS if j != "d01.ab"])
        report = self.collect()
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["jobs"]["missing"], ["d01.ab"])
        self.assertEqual(report["jobs"]["received"], ["d00.aa", "d00.ab", "d01.aa"])
        self.assertEqual(_counts(report)["unknown"], 2)
        self.assertTrue((self.out / "recheck.json").is_file())

    def test_known_change_wins_over_missing(self):
        self.jobs([j for j in JOBS if j != "d01.ab"], d00_ab=1)
        report = self.collect()
        self.assertEqual(report["status"], "changed")
        self.assertEqual(report["jobs"]["missing"], ["d01.ab"])

    def test_no_comparable_pair_publishes_only_collection(self):
        self.jobs(["d00.aa", "d01.ab"])
        report = self.collect()
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["jobs"]["missing"], ["d00.ab", "d01.aa"])
        self.assertIsNone(report["recheck"])
        self.assertTrue((self.out / "collection.json").is_file())
        self.assertFalse((self.out / "candidate").exists())
        self.assertFalse((self.out / "recheck.json").exists())

    def test_empty_returns_are_all_missing(self):
        report = self.collect()
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["jobs"], {"expected": JOBS, "received": [], "missing": JOBS})
        self.assertIsNone(report["recheck"])
        self.assertEqual([p.name for p in self.out.iterdir()], ["collection.json"])

    # ---- invalid input publishes nothing ------------------------------------

    def test_corrupted_orphan_is_invalid(self):
        # d01.aa has no comparable partner, but its bytes are still checked.
        self.jobs(["d00.aa", "d00.ab", "d01.aa"])
        (self.returns / "d01.aa" / "episodes.jsonl").write_text("not json\n")
        self.assertInvalid()

    def test_valid_orphan_from_the_wrong_case_is_invalid(self):
        fixtures._trial(self.returns / "d00.aa", "unrelated", 1901, 0)
        self.assertInvalid()

    def test_truncated_file_set_is_invalid(self):
        self.jobs()
        (self.returns / "d00.ab" / "order.json").unlink()
        self.assertInvalid()

    def test_extra_directory_file_or_root_entry_is_invalid(self):
        self.jobs()
        (self.returns / "d00.aa" / "notes.txt").write_text("extra")
        self.assertInvalid()
        (self.returns / "d00.aa" / "notes.txt").unlink()
        fixtures._trial(self.returns / "d09.aa", "d09", 1999, 0)
        self.assertInvalid()
        # Even a clean sibling case outside the worklist stays an extra.
        (self.returns / "d09.aa").rename(self.root / "moved")
        (self.returns / "stray.txt").write_text("x")
        self.assertInvalid()

    def test_symlinks_are_invalid(self):
        self.jobs()
        (self.returns / "d00.aa" / "link").symlink_to(self.returns / "d00.ab" / "order.json")
        self.assertInvalid()
        (self.returns / "d00.aa" / "link").unlink()
        (self.returns / "d01.ab").rename(self.root / "elsewhere")
        (self.returns / "d01.ab").symlink_to(self.root / "elsewhere")
        self.assertInvalid()

    def test_return_reusing_a_source_uuid_is_invalid(self):
        self.jobs(["d00.aa"])
        self.job("d00.ab", uuid=SOURCE_UUID["d00.ab"])
        self.assertInvalid()

    def test_orphan_reusing_another_cases_source_uuid_is_invalid(self):
        self.jobs(["d00.aa", "d00.ab"])
        self.job("d01.aa", uuid=SOURCE_UUID["d00.aa"])  # orphan: d01.ab is absent
        self.assertInvalid()

    def test_duplicate_uuid_across_returns_is_invalid(self):
        self.job("d00.aa", uuid=1500)
        self.job("d00.ab", uuid=1501)
        self.job("d01.aa", uuid=1500)
        self.assertInvalid()

    def test_wrong_pins_are_invalid(self):
        self.jobs()
        for pin in PINS:
            with self.subTest(pin):
                self.assertInvalid(**{pin: "0" * 64})

    def test_crossed_worklist_and_template_pins_are_invalid(self):
        self.jobs()
        self.assertInvalid(
            worklist_sha256=self.pins["candidate_matrix_sha256"],
            candidate_matrix_sha256=self.pins["worklist_sha256"],
        )

    def test_swapped_source_worklist_and_template_files_are_invalid(self):
        self.jobs()
        work, template = self.source / "worklist.json", self.source / "candidate-matrix.json"
        work_bytes, template_bytes = work.read_bytes(), template.read_bytes()
        work.write_bytes(template_bytes)
        template.write_bytes(work_bytes)
        self.assertInvalid()

    def test_receipt_with_wrong_schema_or_missing_pin_is_invalid(self):
        self.jobs()
        self.assertInvalid(schema="seatlab.other/v1")
        path = self.root / "receipt.json"
        doc = {k: v for k, v in self.pins.items() if k != "study_sha256"}
        path.write_text(json.dumps(doc))
        with self.assertRaises(INVALID):
            collect_jobs(self.source, self.returns, path, self.out)
        self.assertFalse(os.path.lexists(self.out))

    def test_existing_output_is_never_replaced(self):
        self.jobs()
        self.out.mkdir()
        (self.out / "keep").write_text("mine")
        with self.assertRaises(INVALID):
            self.collect()
        self.assertEqual([p.name for p in self.out.iterdir()], ["keep"])
        self.assertEqual((self.out / "keep").read_text(), "mine")

    def test_source_and_returns_are_left_untouched(self):
        self.jobs()

        def snap(root):
            return {
                str(p.relative_to(root)): p.read_bytes()
                for p in sorted(root.rglob("*"))
                if p.is_file()
            }

        before = (snap(self.source), snap(self.returns))
        self.collect()
        self.assertEqual((snap(self.source), snap(self.returns)), before)


if __name__ == "__main__":
    unittest.main()
