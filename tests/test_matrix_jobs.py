from __future__ import annotations

import unittest

from seatlab.matrix_jobs import plan_native_jobs


class PlanNativeJobsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = {
            "schema": "seatlab.panel-matrix/v1",
            "contrasts": {
                "ab": {"source": "a", "target": "b", "role": "r"},
                "ac": {"source": "a", "target": "c", "role": "r"},
            },
            "criteria": [
                {"id": "k1", "contrast": "ab", "dimension": "raw_view", "when": "different"},
                {"id": "k2", "contrast": "ac", "dimension": "raw_view", "when": "different"},
            ],
            "cases": {
                "one": {"a": "x/1a", "b": "x/1b", "c": "x/1c"},
                "two": {"a": "x/2a", "b": "x/2b", "c": None},
                "three": {"a": "x/3a", "b": "x/3b", "c": "x/3c"},
            },
        }
        self.design = {
            "cases": {
                "one": {"k1": "one.ab", "k2": "one.ac"},
                "two": {"k1": "two.ab", "k2": None},
                "three": {"k1": "three.ab", "k2": "three.ac"},
            }
        }
        self.report = {
            "study_sha256": "a" * 64,
            "design_sha256": "d" * 64,
            "selection": {"cases": ["two", "one"]},
        }

    def test_selected_cases_get_both_arms_deduped(self) -> None:
        worklist, template = plan_native_jobs(self.matrix, self.design, self.report)
        self.assertEqual(worklist["selected_cases"], ["one", "two"])
        self.assertEqual(worklist["source_study_sha256"], "a" * 64)
        self.assertEqual(worklist["source_design_sha256"], "d" * 64)
        self.assertEqual(
            worklist["jobs"],
            [
                {"id": "one.a", "case": "one", "arm": "a", "criteria": ["k1", "k2"]},
                {"id": "one.b", "case": "one", "arm": "b", "criteria": ["k1"]},
                {"id": "one.c", "case": "one", "arm": "c", "criteria": ["k2"]},
                {"id": "two.a", "case": "two", "arm": "a", "criteria": ["k1"]},
                {"id": "two.b", "case": "two", "arm": "b", "criteria": ["k1"]},
            ],
        )
        self.assertEqual(worklist["unmeasured"], [{"case": "two", "criterion": "k2"}])
        cases = template["cases"]
        self.assertEqual(
            cases["one"], {"a": "jobs/one.a", "b": "jobs/one.b", "c": "jobs/one.c"}
        )
        self.assertEqual(cases["two"], {"a": "jobs/two.a", "b": "jobs/two.b", "c": None})
        self.assertEqual(cases["three"], {"a": None, "b": None, "c": None})
        self.assertEqual(template["contrasts"], self.matrix["contrasts"])

    def test_empty_selection_has_no_jobs(self) -> None:
        self.report["selection"] = {"cases": []}
        worklist, template = plan_native_jobs(self.matrix, self.design, self.report)
        self.assertEqual(worklist["jobs"], [])
        self.assertEqual(worklist["unmeasured"], [])
        self.assertTrue(
            all(v is None for row in template["cases"].values() for v in row.values())
        )


if __name__ == "__main__":
    unittest.main()
