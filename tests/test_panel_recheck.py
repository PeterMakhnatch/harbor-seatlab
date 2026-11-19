"""Offline recheck of a baseline-selected panel against an independent sealed study."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import seatlab
from seatlab import tape
from seatlab.panel import panel_report
from seatlab.study import seal_study
from seatlab.study_contract import RECIPE_SCHEMA

from seatlab.recheck import recheck_panel
from test_panel import _check, _criterion, _world

DESIGN_SCHEMA = "seatlab.panel-design/v1"
POS = ["/position/1"]
REPORT_KEYS = {
    "schema",
    "status",
    "source_study_sha256",
    "candidate_study_sha256",
    "source_design_sha256",
    "candidate_design_sha256",
    "selection",
    "counts",
    "cases",
}
CRITERIA = [
    _criterion("act_diff", "accepted_action", "different", "speaker"),
    _criterion("raw_eq", "raw_view", "equal", "listener"),
    _criterion("pos_eq", "selected_view", "equal", "listener", POS),
]


def _row(prefix):
    return {"act_diff": f"{prefix}-spk", "raw_eq": f"{prefix}-lis", "pos_eq": f"{prefix}-lis"}


def _checks(names):
    rows = []
    for name in names:
        p = name[0]
        rows += [
            _check(name, f"{p}-spk", "speaker"),
            _check(name, f"{p}-lis", "listener", POS),
        ]
    return rows


class PanelRecheck(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def build(self, where, worlds, extra_checks=()):
        """worlds: {name: (base, differ[, game])}; returns (manifest, pin)."""
        for name, spec in worlds.items():
            _world(
                where, name, spec[0], differ=spec[1], game=spec[2] if len(spec) > 2 else None
            )
        recipe, manifest = where / "recipe.json", where / "study.json"
        checks = _checks(worlds) + list(extra_checks)
        tape.write_lines(recipe, [{"schema": RECIPE_SCHEMA, "checks": checks}])
        return manifest, tape.digest(seal_study(recipe, manifest))

    def design(self, where, cases, criteria=None, name="design.json"):
        path = where / name
        path.write_text(
            json.dumps(
                {
                    "schema": DESIGN_SCHEMA,
                    "criteria": CRITERIA if criteria is None else criteria,
                    "cases": cases,
                }
            )
        )
        return path

    def both(
        self,
        source_worlds=None,
        cand_worlds=None,
        *,
        src_cases=None,
        cand_cases=None,
        src_criteria=None,
        cand_criteria=None,
        cand_extra=(),
    ):
        fixture = Path(tempfile.mkdtemp(dir=self.root))
        self.src, self.cand = fixture / "src", fixture / "cand"
        self.src.mkdir()
        self.cand.mkdir()
        sw = source_worlds or {"alpha": (100, True), "beta": (200, False)}
        cw = cand_worlds or {"alpha": (1100, True), "beta": (1200, False)}
        self.sman, self.spin = self.build(self.src, sw)
        self.cman, self.cpin = self.build(self.cand, cw, cand_extra)
        self.sdes = self.design(
            self.src,
            src_cases or {n: _row(n[0]) for n in sw},
            src_criteria,
        )
        self.cdes = self.design(
            self.cand,
            cand_cases or {n: _row(n[0]) for n in cw},
            cand_criteria,
        )

    def args(self):
        return (self.sman, self.cman)

    def kwargs(self, **over):
        base = dict(
            design=self.sdes,
            candidate_design=self.cdes,
            expect_sha256=self.spin,
            expect_candidate_sha256=self.cpin,
        )
        base.update(over)
        return base

    def recheck(self, **over):
        return recheck_panel(*self.args(), **self.kwargs(**over))

    def refused(self, **over):
        with self.assertRaises(ValueError):
            self.recheck(**over)

    # ---- outcomes -------------------------------------------------------

    def test_stable_when_independent_run_agrees(self):
        self.both()
        rep = self.recheck()
        self.assertEqual(rep["schema"], "seatlab.panel-recheck/v1")
        self.assertEqual(set(rep), REPORT_KEYS)
        self.assertEqual(rep["status"], "matched")
        self.assertEqual(
            rep["counts"], {"entries": 6, "stable": 6, "changed": 0, "unknown": 0}
        )
        self.assertEqual(rep["selection"], ["alpha", "beta"])
        self.assertEqual(rep["source_study_sha256"], self.spin)
        self.assertEqual(rep["candidate_study_sha256"], self.cpin)
        self.assertEqual(
            rep["source_design_sha256"],
            panel_report(self.sman, self.sdes, expect_sha256=self.spin)["design_sha256"],
        )
        self.assertEqual(
            rep["candidate_design_sha256"],
            panel_report(self.cman, self.cdes, expect_sha256=self.cpin)["design_sha256"],
        )
        by = {c["id"]: c for c in rep["cases"]}
        self.assertEqual(list(by), ["alpha", "beta"])
        self.assertEqual(
            by["alpha"]["criteria"]["act_diff"],
            {"source": "witness", "candidate": "witness", "status": "stable"},
        )
        self.assertEqual(
            by["alpha"]["criteria"]["raw_eq"],
            {"source": "opposite", "candidate": "opposite", "status": "stable"},
        )
        self.assertEqual(
            set(by["alpha"]),
            {"id", "source_native_trial_ids", "candidate_native_trial_ids", "criteria"},
        )
        self.assertEqual(len(by["alpha"]["source_native_trial_ids"]), 2)
        self.assertEqual(len(by["alpha"]["candidate_native_trial_ids"]), 2)
        self.assertFalse(
            set(by["alpha"]["source_native_trial_ids"])
            & set(by["alpha"]["candidate_native_trial_ids"])
        )

    def test_output_has_no_values_paths_or_authenticity_claim(self):
        self.both()
        text = json.dumps(self.recheck())
        self.assertNotIn("keep-this-out", text)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("authentic", text.lower())

    def test_changed_witness_to_opposite_and_back(self):
        self.both(cand_worlds={"alpha": (1100, False), "beta": (1200, False)})
        rep = self.recheck()
        self.assertEqual(rep["status"], "changed")
        self.assertEqual(
            rep["counts"], {"entries": 6, "stable": 4, "changed": 2, "unknown": 0}
        )
        alpha = rep["cases"][0]["criteria"]
        self.assertEqual(
            alpha["act_diff"],
            {"source": "witness", "candidate": "opposite", "status": "changed"},
        )
        self.assertEqual(
            alpha["raw_eq"],
            {"source": "opposite", "candidate": "witness", "status": "changed"},
        )
        self.assertEqual(alpha["pos_eq"]["status"], "stable")

    def test_null_binding_is_unknown_and_incomplete(self):
        row = _row("a")
        row["act_diff"] = None
        self.both(cand_cases={"alpha": row, "beta": _row("b")})
        rep = self.recheck()
        self.assertEqual(rep["status"], "incomplete")
        self.assertEqual(
            rep["counts"], {"entries": 6, "stable": 5, "changed": 0, "unknown": 1}
        )
        self.assertEqual(
            rep["cases"][0]["criteria"]["act_diff"],
            {"source": "witness", "candidate": "unknown", "status": "unknown"},
        )

    def test_candidate_may_omit_source_case_yielding_unknown(self):
        self.both(cand_worlds={"alpha": (1100, True)})
        rep = self.recheck()
        self.assertEqual(rep["selection"], ["alpha", "beta"])
        self.assertEqual(rep["status"], "incomplete")
        self.assertEqual(
            rep["counts"], {"entries": 6, "stable": 3, "changed": 0, "unknown": 3}
        )
        beta = rep["cases"][1]
        self.assertEqual(beta["id"], "beta")
        self.assertEqual(beta["candidate_native_trial_ids"], [])
        for cell in beta["criteria"].values():
            self.assertEqual(cell["candidate"], "unknown")
            self.assertEqual(cell["status"], "unknown")

    def test_change_wins_over_unknown(self):
        self.both(cand_worlds={"alpha": (1100, False)})
        rep = self.recheck()
        self.assertEqual(rep["status"], "changed")
        self.assertEqual(rep["counts"]["changed"], 2)
        self.assertEqual(rep["counts"]["unknown"], 3)

    def test_source_unknown_stays_unknown(self):
        row = _row("a")
        row["act_diff"] = None
        self.both(
            source_worlds={"alpha": (100, True)},
            cand_worlds={"alpha": (1100, True)},
            src_cases={"alpha": row},
            cand_cases={"alpha": row},
        )
        rep = self.recheck()
        cell = rep["cases"][0]["criteria"]["act_diff"]
        self.assertEqual(
            cell, {"source": "unknown", "candidate": "unknown", "status": "unknown"}
        )
        self.assertEqual(rep["status"], "incomplete")

    def test_empty_selection_is_incomplete(self):
        crit = [CRITERIA[0]]
        self.both(
            src_cases={"beta": {"act_diff": "b-spk"}},
            cand_cases={"beta": {"act_diff": "b-spk"}},
            source_worlds={"beta": (200, False)},
            cand_worlds={"beta": (1200, False)},
            src_criteria=crit,
            cand_criteria=crit,
        )
        rep = self.recheck()
        self.assertEqual(rep["selection"], [])
        self.assertEqual(rep["cases"], [])
        self.assertEqual(
            rep["counts"], {"entries": 0, "stable": 0, "changed": 0, "unknown": 0}
        )
        self.assertEqual(rep["status"], "incomplete")

    # ---- selection comes from the baseline only --------------------------

    def test_selection_is_baseline_not_candidate_minimum(self):
        crit = [CRITERIA[0], CRITERIA[2]]
        # baseline: alpha alone witnesses both; candidate: only beta would.
        self.both(
            cand_worlds={"alpha": (1100, False), "beta": (1200, True)},
            src_criteria=crit,
            cand_criteria=crit,
            src_cases={
                "alpha": {"act_diff": "a-spk", "pos_eq": "a-lis"},
                "beta": {"act_diff": "b-spk", "pos_eq": "b-lis"},
            },
            cand_cases={
                "alpha": {"act_diff": "a-spk", "pos_eq": "a-lis"},
                "beta": {"act_diff": "b-spk", "pos_eq": "b-lis"},
            },
        )
        own = panel_report(self.cman, self.cdes, expect_sha256=self.cpin)
        self.assertEqual(own["selection"]["cases"], ["beta"])
        rep = self.recheck()
        self.assertEqual(rep["selection"], ["alpha"])
        self.assertEqual([c["id"] for c in rep["cases"]], ["alpha"])
        self.assertEqual(rep["counts"]["entries"], 2)
        self.assertEqual(rep["status"], "changed")

    def test_extra_original_candidate_case_is_allowed_but_not_compared(self):
        crit = [CRITERIA[0], CRITERIA[2]]
        self.both(
            src_criteria=crit,
            cand_criteria=crit,
            src_cases={
                "alpha": {"act_diff": "a-spk", "pos_eq": "a-lis"},
                "beta": {"act_diff": "b-spk", "pos_eq": "b-lis"},
            },
            cand_cases={
                "alpha": {"act_diff": "a-spk", "pos_eq": "a-lis"},
                "beta": {"act_diff": "b-spk", "pos_eq": "b-lis"},
            },
        )
        rep = self.recheck()
        self.assertEqual([c["id"] for c in rep["cases"]], ["alpha"])
        self.assertEqual(rep["status"], "matched")

    # ---- refusals --------------------------------------------------------

    def test_relative_treatment_endpoints_must_match_even_for_symmetric_checks(self):
        self.both()
        doc = json.loads((self.cand / "recipe.json").read_text())
        for check in doc["checks"]:
            check["source"], check["target"] = check["target"], check["source"]
            check["source_order"], check["target_order"] = (
                check["target_order"],
                check["source_order"],
            )
        recipe = self.cand / "swapped.json"
        recipe.write_text(json.dumps(doc))
        self.cman = self.cand / "swapped-study.json"
        self.cpin = tape.digest(seal_study(recipe, self.cman))
        self.refused()

    def test_trusted_design_pin_rejects_a_valid_different_design(self):
        self.both()
        good = tape.digest(json.loads(self.sdes.read_text()))
        self.assertEqual(self.recheck(expect_design_sha256=good)["status"], "matched")
        self.refused(expect_design_sha256="0" * 64)
        self.refused(expect_candidate_design_sha256="0" * 64)

    def test_wrong_pins_are_refused(self):
        self.both()
        self.assertEqual(self.recheck()["status"], "matched")
        self.refused(expect_sha256="0" * 64)
        self.refused(expect_candidate_sha256="0" * 64)
        self.refused(expect_sha256=self.cpin, expect_candidate_sha256=self.spin)
        self.refused(expect_sha256="not-a-digest")

    def test_candidate_may_not_reuse_native_trial_ids(self):
        self.both(cand_worlds={"alpha": (100, True), "beta": (1200, False)})
        self.refused()

    def test_same_study_as_both_sides_is_refused(self):
        self.both()
        with self.assertRaises(ValueError):
            recheck_panel(
                self.sman,
                self.sman,
                design=self.sdes,
                candidate_design=self.sdes,
                expect_sha256=self.spin,
                expect_candidate_sha256=self.spin,
            )

    def test_conflicting_native_case_digest_is_refused(self):
        self.both(cand_worlds={"alpha": (1100, True, "another-game"), "beta": (1200, False)})
        self.refused()

    def test_unknown_candidate_label_is_refused(self):
        rows = {"alpha": _row("a"), "beta": _row("b"), "zeta": _row("z")}
        self.both(
            cand_worlds={"alpha": (1100, True), "beta": (1200, False), "zeta": (1300, True)},
            cand_cases=rows,
        )
        self.refused()

    def test_conflicting_criterion_descriptors_are_refused(self):
        variants = {
            "when": dict(CRITERIA[1], when="different"),
            "role": dict(CRITERIA[1], role="speaker"),
            "dimension": _criterion("raw_eq", "accepted_action", "equal", "listener"),
            "fields": _criterion(
                "pos_eq", "selected_view", "equal", "listener", ["/position/0"]
            ),
            "fields null": _criterion("pos_eq", "selected_view", "equal", "listener", None),
        }
        for label, replacement in variants.items():
            with self.subTest(label):
                crit = [c for c in CRITERIA if c["id"] != replacement["id"]] + [replacement]
                self.both(cand_criteria=crit)
                self.refused()
        with self.subTest("missing criterion"):
            self.both(
                cand_criteria=CRITERIA[:2],
                cand_cases={
                    n: {k: v for k, v in _row(n[0]).items() if k != "pos_eq"}
                    for n in ("alpha", "beta")
                },
            )
            self.refused()

    def test_equal_fields_in_other_order_still_match(self):
        crit = [
            CRITERIA[0],
            _criterion(
                "two", "selected_view", "equal", "listener", ["/position/1", "/position/0"]
            ),
        ]
        crit2 = [
            CRITERIA[0],
            _criterion(
                "two", "selected_view", "equal", "listener", ["/position/0", "/position/1"]
            ),
        ]
        cases = {n: {"act_diff": f"{n[0]}-spk", "two": None} for n in ("alpha", "beta")}
        self.both(src_criteria=crit, cand_criteria=crit2, src_cases=cases, cand_cases=cases)
        self.assertEqual(self.recheck()["selection"], ["alpha"])

    def test_changed_check_mapping_is_refused(self):
        extra = [_check("alpha", "a-spk2", "speaker")]
        row = _row("a")
        row["act_diff"] = "a-spk2"
        self.both(cand_extra=extra, cand_cases={"alpha": row, "beta": _row("b")})
        self.refused()

    def test_newly_adding_a_check_where_source_has_none_is_refused(self):
        row = _row("a")
        row["act_diff"] = None
        # candidate measures what the source left unmeasured: refused, not reverse engineered
        self.both(
            src_cases={"alpha": row, "beta": _row("b")},
            cand_cases={"alpha": _row("a"), "beta": _row("b")},
        )
        self.refused()

    def test_empty_case_without_any_measurement_cannot_pass(self):
        nulls = {"act_diff": None, "raw_eq": None, "pos_eq": None}
        self.both(
            src_cases={"alpha": _row("a"), "beta": _row("b")},
            cand_cases={"alpha": nulls, "beta": _row("b")},
        )
        rep = self.recheck()
        self.assertNotEqual(rep["status"], "matched")
        self.assertEqual(rep["counts"]["unknown"], 3)

    # ---- command line ----------------------------------------------------

    def cli(self, *extra, **over):
        kw = self.kwargs(**over)
        env = dict(os.environ, PYTHONPATH=str(Path(seatlab.__file__).resolve().parents[1]))
        cmd = [
            sys.executable,
            "-m",
            "seatlab",
            "study",
            "recheck",
            str(self.sman),
            str(self.cman),
            "--design",
            str(kw["design"]),
            "--candidate-design",
            str(kw["candidate_design"]),
            "--expect-sha256",
            kw["expect_sha256"],
            "--expect-candidate-sha256",
            kw["expect_candidate_sha256"],
        ]
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=self.root)

    def test_cli_exit_codes(self):
        self.both()
        done = self.cli()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), self.recheck())

        self.both(cand_worlds={"alpha": (1100, False), "beta": (1200, False)})
        done = self.cli()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(json.loads(done.stdout)["status"], "changed")

        self.both(cand_worlds={"alpha": (1100, True)})
        done = self.cli()
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(json.loads(done.stdout)["status"], "incomplete")

        refused = self.cli(expect_sha256="0" * 64)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
