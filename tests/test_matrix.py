"""build_matrix: one compact matrix -> sealed study + panel design, rechecked offline.

Matrix paths are taken relative to the matrix file's directory.
"""

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from seatlab import author, tape
from seatlab.matrix import build_matrix
from seatlab.panel import panel_report
from seatlab.recheck import recheck_panel
from seatlab.study_inputs import read_document
from seatlab.world_order import WORLD_ORDER_SCHEMA

CONTRAST = {"source": "aa", "target": "ab", "role": "speaker"}
CRITERIA = [
    {"id": "kept", "contrast": "name", "dimension": "accepted_action", "when": "equal"}
]


def _trial(folder, game, n, value):
    """One synthetic test fixture: two roles, episodes.jsonl + order.json only."""
    folder.mkdir(parents=True)
    uid, case = str(UUID(int=n)), tape.digest({"game": game})
    episodes = []
    for role in ("speaker", "listener"):
        frames = []
        for turn in (1, 2):
            view = {"signal": value if turn == 2 else 0, "position": [12, 24]}
            frames.append(
                author.accepted_frame(
                    turn=turn,
                    view=view,
                    acknowledged_sha256=tape.digest(view),
                    action=value if role == "speaker" else None,
                )
            )
        episodes.append(
            author.episode_from_frames(
                id=f"{uid}:{role}",
                case_sha256=case,
                role=role,
                native_trial_id=uid,
                closed=True,
                frames=frames,
            )
        )
    tape.write_lines(folder / "episodes.jsonl", episodes)
    tape.write_lines(
        folder / "order.json",
        [
            {
                "schema": WORLD_ORDER_SCHEMA,
                "case_sha256": case,
                "native_trial_id": uid,
                "episodes": {e["role"]: e["episode_sha256"] for e in episodes},
                "steps": [["listener", "speaker"], ["listener", "speaker"]],
            }
        ],
    )


class MatrixBuild(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def inputs(self, tag, base, dirs, values):
        """dirs: {case: (aa_dir, ab_dir)}; ab behaviour is values[case]."""
        for i, (case, (a, b)) in enumerate(dirs.items()):
            _trial(self.root / tag / a, case, base + 2 * i, 0)
            _trial(self.root / tag / b, case, base + 2 * i + 1, values[case])

    def matrix(self, tag, cases, **over):
        doc = {
            "schema": "seatlab.panel-matrix/v1",
            "contrasts": {"name": dict(CONTRAST)},
            "criteria": [dict(c) for c in CRITERIA],
            "cases": cases,
        }
        doc.update(over)
        path = self.root / tag / "matrix.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(doc))
        return path

    def build(self, matrix, name="out"):
        out = self.root / name
        return build_matrix(matrix, out), out

    def pair(self, cand_values, cand_cases=None):
        """Independent source and candidate roots, different folders and UUIDs."""
        src_dirs = {"d00": ("ext/one", "ext/two"), "d01": ("ext/three", "ext/four")}
        cand_dirs = {"d00": ("x/p", "x/q"), "d01": ("y/r", "y/s")}
        self.inputs("src", 100, src_dirs, {"d00": 0, "d01": 0})
        self.inputs("cand", 1100, cand_dirs, cand_values)

        def names(d):
            return {c: {"aa": a, "ab": b} for c, (a, b) in d.items()}

        src = self.build(self.matrix("src", names(src_dirs)), "src-out")
        cand = self.build(self.matrix("cand", cand_cases or names(cand_dirs)), "cand-out")
        return src, cand

    def recheck(self, src, cand):
        (sr, so), (cr, co) = src, cand
        return recheck_panel(
            so / "study.json",
            co / "study.json",
            design=so / "panel-design.json",
            candidate_design=co / "panel-design.json",
            expect_sha256=sr["study_sha256"],
            expect_candidate_sha256=cr["study_sha256"],
        )

    # ---- consumer-visible behaviour ---------------------------------------

    def test_different_roots_and_ids_give_same_endpoints_and_matched_recheck(self):
        src, cand = self.pair({"d00": 0, "d01": 0})
        for receipt, out in (src, cand):
            self.assertEqual(receipt["schema"], "seatlab.matrix-build/v1")
            self.assertEqual(receipt["native_trials"], 4)
            checks = read_document(out / "recipe.json")["checks"]
            self.assertEqual([c["id"] for c in checks], ["d00.name", "d01.name"])
            self.assertEqual(checks[0]["source"], "cells/d00/aa/episodes.jsonl")
            self.assertEqual(checks[0]["target_order"], "cells/d00/ab/order.json")
            self.assertTrue((out / "cells/d01/ab/order.json").is_file())
            self.assertEqual(
                receipt["design_sha256"],
                panel_report(
                    out / "study.json",
                    out / "panel-design.json",
                    expect_sha256=receipt["study_sha256"],
                )["design_sha256"],
            )
        self.assertNotEqual(src[0]["study_sha256"], cand[0]["study_sha256"])
        report = self.recheck(src, cand)
        self.assertEqual(report["status"], "matched")
        self.assertEqual(report["counts"]["unknown"], 0)

    def test_changed_candidate_behaviour_flips_recheck(self):
        src, cand = self.pair({"d00": 1, "d01": 0})
        report = self.recheck(src, cand)
        self.assertEqual(report["status"], "changed")
        self.assertEqual(report["counts"]["changed"], 1)

    def test_absent_arm_is_unknown_not_pass_and_not_copied(self):
        cases = {"d00": {"aa": "x/p", "ab": None}, "d01": {"aa": "y/r", "ab": "y/s"}}
        src, cand = self.pair({"d00": 0, "d01": 0}, cases)
        self.assertEqual(
            read_document(cand[1] / "panel-design.json")["cases"]["d00"], {"kept": None}
        )
        self.assertEqual(
            [c["id"] for c in read_document(cand[1] / "recipe.json")["checks"]], ["d01.name"]
        )
        self.assertFalse((cand[1] / "cells/d00").exists())
        report = self.recheck(src, cand)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["counts"]["unknown"], 1)

    def test_same_bytes_reordered_objects_give_same_pins(self):
        dirs = {"d00": ("a/1", "a/2"), "d01": ("b/1", "b/2")}
        self.inputs("in", 10, dirs, {"d00": 0, "d01": 1})
        cases = {c: {"aa": a, "ab": b} for c, (a, b) in dirs.items()}
        first = self.build(self.matrix("in", cases), "o1")[0]
        doc = json.loads((self.root / "in/matrix.json").read_text())
        doc["cases"] = {
            c: dict(reversed(list(v.items()))) for c, v in reversed(list(cases.items()))
        }
        doc = dict(reversed(list(doc.items())))
        (self.root / "in/matrix.json").write_text(json.dumps(doc, indent=4))
        second = self.build(self.root / "in/matrix.json", "o2")[0]
        self.assertEqual(first, second)

    # ---- refusals leave nothing publishable ------------------------------

    def refused(self, matrix):
        out = self.root / "refused"
        with self.assertRaises((ValueError, OSError)):
            build_matrix(matrix, out)
        self.assertFalse((out / "study.json").exists())

    def test_bad_contents_or_paths_publish_nothing(self):
        dirs = {"d00": ("a/1", "a/2")}
        self.inputs("in", 10, dirs, {"d00": 1})
        ok = {"d00": {"aa": "a/1", "ab": "a/2"}}
        (self.root / "in/a/2/episodes.jsonl").write_text("not json\n")
        self.refused(self.matrix("in", ok))
        _trial(self.root / "in/c/1", "d00", 50, 0)
        (self.root / "in/c/1/order.json").unlink()
        _trial(self.root / "in/c/2", "d00", 51, 0)
        self.refused(self.matrix("in", {"d00": {"aa": "c/1", "ab": "c/2"}}))
        for bad in ("/abs/path", "../a/1", "missing/dir"):
            with self.subTest(bad):
                self.refused(self.matrix("in", {"d00": {"aa": bad, "ab": "a/2"}}))

    def test_folder_reuse_between_arms_is_refused(self):
        self.inputs("in", 10, {"d00": ("a/1", "a/2")}, {"d00": 0})
        self.refused(self.matrix("in", {"d00": {"aa": "a/1", "ab": "a/1"}}))

    def test_measured_arm_cannot_reuse_another_arms_uuid_via_independent_paths(self):
        for name, uid in (("a", 10), ("b", 11), ("c", 11)):
            _trial(self.root / "in" / name, "d00", uid, 0)
        contrasts = {
            "left": {"source": "a", "target": "b", "role": "speaker"},
            "right": {"source": "a", "target": "c", "role": "listener"},
        }
        criteria = [dict(CRITERIA[0], id=name, contrast=name) for name in contrasts]
        self.refused(
            self.matrix(
                "in", {"d00": {n: n for n in "abc"}}, contrasts=contrasts, criteria=criteria
            )
        )

    def test_existing_output_is_not_replaced(self):
        self.inputs("in", 10, {"d00": ("a/1", "a/2")}, {"d00": 0})
        matrix = self.matrix("in", {"d00": {"aa": "a/1", "ab": "a/2"}})
        out = self.root / "taken"
        out.mkdir()
        (out / "keep").write_text("mine")
        with self.assertRaises((ValueError, OSError)):
            build_matrix(matrix, out)
        self.assertEqual((out / "keep").read_text(), "mine")
        self.assertEqual([p.name for p in out.iterdir()], ["keep"])

    def test_malformed_labels_are_refused(self):
        self.inputs("in", 10, {"d00": ("a/1", "a/2")}, {"d00": 0})
        ok = {"aa": "a/1", "ab": "a/2"}
        for bad in ("Upper", "", "a.b", "-x", "x" * 29, "a b"):
            with self.subTest(case=bad):
                self.refused(self.matrix("in", {bad: ok}))
            with self.subTest(contrast=bad):
                crit = [dict(CRITERIA[0], contrast=bad)]
                self.refused(
                    self.matrix(
                        "in", {"d00": ok}, contrasts={bad: dict(CONTRAST)}, criteria=crit
                    )
                )
            with self.subTest(arm=bad):
                con = {"name": dict(CONTRAST, target=bad)}
                self.refused(
                    self.matrix("in", {"d00": {"aa": "a/1", bad: "a/2"}}, contrasts=con)
                )

    def test_structural_misuse_is_refused(self):
        self.inputs("in", 10, {"d00": ("a/1", "a/2")}, {"d00": 0})
        ok = {"aa": "a/1", "ab": "a/2"}
        self.refused(
            self.matrix("in", {"d00": ok}, contrasts={"name": dict(CONTRAST, target="aa")})
        )
        self.refused(self.matrix("in", {"d00": dict(ok, extra="a/1")}))
        self.refused(self.matrix("in", {"d00": {"aa": "a/1"}}))
        self.refused(
            self.matrix("in", {"d00": {"aa": "a/1", "ab": None}})
        )  # nothing measured
        self.refused(self.matrix("in", {"d00": ok}, criteria=[]))


if __name__ == "__main__":
    unittest.main()
