"""Tests use the public native receipt as input; private copies model partial delivery."""

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from compare_revisions import HARBOR, REVISIONS, compare


class RevisionCompleteness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        base = json.loads(Path(__file__).with_name("results.json").read_bytes())
        self.before, self.after = root / "before", root / "after"
        for ix, (folder, ref) in enumerate(zip((self.before, self.after), REVISIONS)):
            folder.mkdir()
            self.write(folder, "revision.json", {"task_commit": ref, "harbor_commit": HARBOR})
            self.write(folder, "plan.json", base["plan"])
            self.write(folder, "summary.json", base["execution"])
            for j, trial in enumerate(base["trials"]):
                self.write(
                    folder,
                    trial["arm"] + ".json",
                    {**trial, "native_trial_id": str(UUID(int=ix * 2 + j + 1))},
                )

    @staticmethod
    def write(folder, name, value):
        (folder / name).write_text(json.dumps(value), encoding="utf-8")

    def test_missing_export_does_not_make_false_negative_or_hide_other_arm(self):
        summary = json.loads((self.after / "summary.json").read_bytes())
        summary["arms"]["no-read"].update(evidence_present=False, evidence_exit_status=None)
        self.write(self.after, "summary.json", summary)
        (self.after / "no-read.json").unlink()
        report = compare(self.before, self.after)
        self.assertEqual(report["counts"], {"stable": 4, "changed": 0, "unknown": 4})
        self.assertIsNone(
            report["after"]["arms"]["no-read"]["facts"]["recipient_explicit_read"]
        )
        self.assertIs(
            report["before"]["arms"]["no-read"]["facts"]["recipient_explicit_read"], False
        )

    def test_failed_native_attempt_does_not_certify_a_valid_looking_export(self):
        summary = json.loads((self.after / "summary.json").read_bytes())
        summary["arms"]["read"]["harbor_exit_status"] = 1
        self.write(self.after, "summary.json", summary)
        result = compare(self.before, self.after)
        self.assertEqual(set(result["facts"]["read"].values()), {"unknown"})
        self.assertEqual(set(result["facts"]["no-read"].values()), {"stable"})

    def test_reused_trial_is_not_a_fresh_observation(self):
        trial = json.loads((self.before / "read.json").read_bytes())
        self.write(self.after, "read.json", trial)
        with self.assertRaisesRegex(ValueError, "duplicate native trial"):
            compare(self.before, self.after)
