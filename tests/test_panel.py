"""Observed-panel selection over real sealed SeatLab studies."""

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from seatlab import author, tape
from seatlab.panel import panel_report
from seatlab.study import seal_study
from seatlab.study_contract import RECIPE_SCHEMA
from seatlab.world_order import WORLD_ORDER_SCHEMA

DESIGN_SCHEMA = "seatlab.panel-design/v1"
POS = ["/position/1"]


def _world(root, name, base, *, differ, closed=True, game=None):
    """Write a two-world, two-role case; return its two native trial ids."""
    case = tape.digest({"game": game or name})
    uids = []
    for side in (0, 1):
        uid = str(UUID(int=base + side))
        uids.append(uid)
        value = side if differ else 0
        episodes = []
        for role in ("speaker", "listener"):
            frames = []
            for turn in (1, 2):
                view = {
                    "signal": value if turn == 2 else 0,
                    "position": [12, 24],
                    "private": "keep-this-out-of-the-panel",
                }
                action = value if role == "speaker" else None
                frames.append(
                    author.accepted_frame(
                        turn=turn,
                        view=view,
                        acknowledged_sha256=tape.digest(view),
                        action=action,
                    )
                )
            episodes.append(
                author.episode_from_frames(
                    id=f"{uid}:{role}",
                    case_sha256=case,
                    role=role,
                    native_trial_id=uid,
                    closed=closed or side == 0,
                    frames=frames,
                )
            )
        tape.write_lines(root / f"{name}-{side}.jsonl", episodes)
        tape.write_lines(
            root / f"{name}-order{side}.json",
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
    return uids


def _check(name, cid, role, fields=None):
    row = dict(
        id=cid,
        kind="role-trajectory",
        source=f"{name}-0.jsonl",
        target=f"{name}-1.jsonl",
        source_order=f"{name}-order0.json",
        target_order=f"{name}-order1.json",
        role=role,
    )
    if fields is not None:
        row["fields"] = fields
    return row


def _criterion(cid, dimension, when, role, fields=None):
    row = {"id": cid, "dimension": dimension, "when": when, "role": role}
    if dimension == "selected_view":
        row["fields"] = fields
    return row


class ObservedPanel(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def seal(self, checks):
        recipe, manifest = (self.root / n for n in ("recipe.json", "study.json"))
        tape.write_lines(recipe, [{"schema": RECIPE_SCHEMA, "checks": checks}])
        return manifest, tape.digest(seal_study(recipe, manifest))

    def design(self, criteria, cases, name="design.json"):
        path = self.root / name
        path.write_text(
            json.dumps({"schema": DESIGN_SCHEMA, "criteria": criteria, "cases": cases})
        )
        return path

    def report(self, manifest, pin, design):
        return panel_report(manifest, design, expect_sha256=pin)

    def refused(self, manifest, pin, design):
        with self.assertRaises(Exception):
            panel_report(manifest, design, expect_sha256=pin)

    def two_cases(self):
        """alpha: worlds differ. beta: worlds identical."""
        self.uids = {
            "alpha": _world(self.root, "alpha", 100, differ=True),
            "beta": _world(self.root, "beta", 200, differ=False),
        }
        return self.seal(
            [
                _check("alpha", "a-spk", "speaker"),
                _check("alpha", "a-lis", "listener", POS),
                _check("beta", "b-spk", "speaker"),
                _check("beta", "b-lis", "listener", POS),
            ]
        )

    def test_complete_two_world_cases_select_fewest_covering_labels(self):
        manifest, pin = self.two_cases()
        criteria = [
            _criterion("raw_eq", "raw_view", "equal", "listener"),
            _criterion("act_diff", "accepted_action", "different", "speaker"),
            _criterion("pos_eq", "selected_view", "equal", "listener", POS),
        ]
        cases = {
            "beta": {"raw_eq": "b-lis", "act_diff": "b-spk", "pos_eq": "b-lis"},
            "alpha": {"raw_eq": "a-lis", "act_diff": "a-spk", "pos_eq": "a-lis"},
        }
        rep = self.report(manifest, pin, self.design(criteria, cases))
        self.assertEqual(rep["schema"], "seatlab.observed-panel/v1")
        self.assertEqual((rep["case_count"], rep["criterion_count"]), (2, 3))
        self.assertEqual(rep["study_sha256"], pin)
        sel = rep["selection"]
        self.assertEqual(sel["cases"], ["alpha", "beta"])
        self.assertEqual(sel["case_count"], 2)
        self.assertEqual(sel["covered_criteria"], ["act_diff", "pos_eq", "raw_eq"])
        self.assertEqual(sel["not_witnessed"], [])
        by_case = {c["id"]: c for c in rep["cases"]}
        self.assertEqual([c["id"] for c in rep["cases"]], ["alpha", "beta"])
        self.assertEqual(
            by_case["alpha"]["results"],
            {"act_diff": "witness", "pos_eq": "witness", "raw_eq": "opposite"},
        )
        self.assertEqual(
            by_case["beta"]["results"],
            {"act_diff": "opposite", "pos_eq": "witness", "raw_eq": "witness"},
        )
        self.assertEqual(by_case["alpha"]["case_sha256"], tape.digest({"game": "alpha"}))
        self.assertEqual(
            sorted(by_case["beta"]["native_trial_ids"]), sorted(self.uids["beta"])
        )
        crit = {c["id"]: c for c in rep["criteria"]}
        self.assertEqual([c["id"] for c in rep["criteria"]], ["act_diff", "pos_eq", "raw_eq"])
        self.assertEqual(crit["act_diff"]["state"], "witnessed")
        self.assertEqual(
            (crit["act_diff"]["witnesses"], crit["act_diff"]["opposites"]), (1, 1)
        )
        self.assertEqual(crit["act_diff"]["selected_witnesses"], ["alpha"])
        self.assertEqual(crit["pos_eq"]["selected_witnesses"], ["alpha", "beta"])
        self.assertNotIn("keep-this-out", json.dumps(rep))

    def test_one_case_suffices_when_it_witnesses_everything(self):
        manifest, pin = self.two_cases()
        criteria = [
            _criterion("act_diff", "accepted_action", "different", "speaker"),
            _criterion("pos_eq", "selected_view", "equal", "listener", POS),
        ]
        cases = {
            "alpha": {"act_diff": "a-spk", "pos_eq": "a-lis"},
            "beta": {"act_diff": "b-spk", "pos_eq": "b-lis"},
        }
        rep = self.report(manifest, pin, self.design(criteria, cases))
        self.assertEqual(rep["selection"]["cases"], ["alpha"])
        self.assertEqual(rep["selection"]["case_count"], 1)

    def test_no_witness_gives_empty_selection_and_opposite_only(self):
        manifest, pin = self.two_cases()
        criteria = [_criterion("act_diff", "accepted_action", "different", "speaker")]
        cases = {"beta": {"act_diff": "b-spk"}}
        rep = self.report(manifest, pin, self.design(criteria, cases))
        self.assertEqual(rep["selection"]["cases"], [])
        self.assertEqual(rep["selection"]["not_witnessed"], ["act_diff"])
        self.assertEqual(rep["criteria"][0]["state"], "opposite_only")
        self.assertEqual(rep["criteria"][0]["selected_witnesses"], [])

    def test_equal_action_survives_unknown_selected_view_and_check_reuse(self):
        _world(self.root, "gap", 300, differ=False)
        manifest, pin = self.seal([_check("gap", "gap-lis", "listener", ["/absent"])])
        criteria = [
            _criterion("act_eq", "accepted_action", "equal", "listener"),
            _criterion("absent_eq", "selected_view", "equal", "listener", ["/absent"]),
        ]
        cases = {"gap": {"act_eq": "gap-lis", "absent_eq": "gap-lis"}}
        rep = self.report(manifest, pin, self.design(criteria, cases))
        results = rep["cases"][0]["results"]
        self.assertEqual(results, {"absent_eq": "unknown", "act_eq": "witness"})
        crit = {c["id"]: c for c in rep["criteria"]}
        self.assertEqual(crit["act_eq"]["state"], "witnessed")
        self.assertEqual(crit["absent_eq"]["state"], "indeterminate")
        self.assertEqual(crit["absent_eq"]["unknown"], 1)
        self.assertEqual(rep["selection"]["cases"], ["gap"])
        self.assertEqual(rep["selection"]["not_witnessed"], ["absent_eq"])

    def test_unknown_is_not_a_known_negative(self):
        _world(self.root, "same", 400, differ=False)
        manifest, pin = self.seal(
            [
                _check("same", "s-absent", "listener", ["/absent"]),
                _check("same", "s-pos", "listener", POS),
            ]
        )
        criteria = [
            _criterion("unmeasured", "selected_view", "different", "listener", ["/absent"]),
            _criterion("known_no", "selected_view", "different", "listener", POS),
        ]
        cases = {"same": {"unmeasured": "s-absent", "known_no": "s-pos"}}
        rep = self.report(manifest, pin, self.design(criteria, cases))
        self.assertEqual(
            rep["cases"][0]["results"], {"known_no": "opposite", "unmeasured": "unknown"}
        )
        crit = {c["id"]: c for c in rep["criteria"]}
        self.assertEqual(crit["unmeasured"]["state"], "indeterminate")
        self.assertEqual(crit["known_no"]["state"], "opposite_only")
        self.assertEqual(rep["selection"]["cases"], [])
        self.assertEqual(rep["selection"]["not_witnessed"], ["known_no", "unmeasured"])

    def test_null_check_is_explicitly_unmeasured(self):
        manifest, pin = self.two_cases()
        criteria = [_criterion("act_diff", "accepted_action", "different", "speaker")]
        cases = {"alpha": {"act_diff": None}}
        rep = self.report(manifest, pin, self.design(criteria, cases))
        self.assertEqual(rep["cases"][0]["results"], {"act_diff": "unknown"})
        self.assertEqual(rep["criteria"][0]["state"], "indeterminate")
        self.assertEqual(rep["selection"]["cases"], [])

    def test_incomplete_difference_witnesses_but_incomplete_equality_does_not(self):
        _world(self.root, "open", 500, differ=True, closed=False)
        manifest, pin = self.seal(
            [
                _check("open", "o-spk", "speaker"),
                _check("open", "o-lis", "listener", POS),
            ]
        )
        criteria = [
            _criterion("act_diff", "accepted_action", "different", "speaker"),
            _criterion("pos_eq", "selected_view", "equal", "listener", POS),
        ]
        cases = {"open": {"act_diff": "o-spk", "pos_eq": "o-lis"}}
        rep = self.report(manifest, pin, self.design(criteria, cases))
        self.assertEqual(
            rep["cases"][0]["results"], {"act_diff": "witness", "pos_eq": "unknown"}
        )
        crit = {c["id"]: c for c in rep["criteria"]}
        self.assertEqual(crit["pos_eq"]["state"], "indeterminate")
        self.assertEqual(rep["selection"]["cases"], ["open"])
        self.assertEqual(rep["selection"]["not_witnessed"], ["pos_eq"])

    def test_wrong_pin_is_refused(self):
        manifest, pin = self.two_cases()
        design = self.design(
            [_criterion("act_diff", "accepted_action", "different", "speaker")],
            {"alpha": {"act_diff": "a-spk"}},
        )
        self.refused(manifest, "0" * 64, design)
        self.assertEqual(self.report(manifest, pin, design)["case_count"], 1)

    def test_bad_mappings_are_refused(self):
        manifest, pin = self.two_cases()
        act = _criterion("act_diff", "accepted_action", "different", "speaker")
        pos = _criterion("pos_eq", "selected_view", "equal", "listener", POS)
        bad = {
            "unknown check": ([act], {"alpha": {"act_diff": "nope"}}),
            "wrong role": ([act], {"alpha": {"act_diff": "a-lis"}}),
            "wrong fields": (
                [_criterion("p", "selected_view", "equal", "listener", ["/position/0"])],
                {"alpha": {"p": "a-lis"}},
            ),
            "missing criterion": ([act, pos], {"alpha": {"act_diff": "a-spk"}}),
            "extra criterion": ([act], {"alpha": {"act_diff": "a-spk", "zzz": None}}),
            "fields on action": (
                [dict(act, fields=None)],
                {"alpha": {"act_diff": "a-spk"}},
            ),
            "fields missing": (
                [{k: v for k, v in pos.items() if k != "fields"}],
                {"alpha": {"pos_eq": "a-lis"}},
            ),
            "bad dimension": (
                [dict(act, dimension="other")],
                {"alpha": {"act_diff": "a-spk"}},
            ),
            "bad when": ([dict(act, when="same")], {"alpha": {"act_diff": "a-spk"}}),
            "duplicate criterion id": (
                [act, act],
                {"alpha": {"act_diff": "a-spk"}},
            ),
            "identical alias": (
                [act, dict(act, id="alias")],
                {"alpha": {"act_diff": "a-spk", "alias": "a-spk"}},
            ),
            "check in two cases": (
                [act],
                {"alpha": {"act_diff": "a-spk"}, "other": {"act_diff": "a-spk"}},
            ),
        }
        for label, (criteria, cases) in bad.items():
            with self.subTest(label):
                self.refused(manifest, pin, self.design(criteria, cases, "bad.json"))
        wrong_schema = self.root / "schema.json"
        wrong_schema.write_text(
            json.dumps(
                {
                    "schema": "nope",
                    "criteria": [act],
                    "cases": {"alpha": {"act_diff": "a-spk"}},
                }
            )
        )
        self.refused(manifest, pin, wrong_schema)

    def test_same_case_digest_under_two_labels_is_refused(self):
        _world(self.root, "x", 600, differ=True, game="shared")
        _world(self.root, "y", 700, differ=False, game="shared")
        manifest, pin = self.seal(
            [_check("x", "x-spk", "speaker"), _check("y", "y-spk", "speaker")]
        )
        criteria = [_criterion("act_diff", "accepted_action", "different", "speaker")]
        self.refused(
            manifest,
            pin,
            self.design(criteria, {"x": {"act_diff": "x-spk"}, "y": {"act_diff": "y-spk"}}),
        )

    def test_one_case_may_not_mix_case_digests(self):
        _world(self.root, "p", 800, differ=True)
        _world(self.root, "q", 900, differ=True)
        manifest, pin = self.seal(
            [_check("p", "p-spk", "speaker"), _check("q", "q-spk", "speaker")]
        )
        criteria = [
            _criterion("act_diff", "accepted_action", "different", "speaker"),
            _criterion("raw_diff", "raw_view", "different", "speaker"),
        ]
        self.refused(
            manifest,
            pin,
            self.design(criteria, {"mixed": {"act_diff": "p-spk", "raw_diff": "q-spk"}}),
        )

    def test_one_check_serves_distinct_dimension_and_when_in_same_case(self):
        manifest, pin = self.two_cases()
        criteria = [
            _criterion("act_diff", "accepted_action", "different", "speaker"),
            _criterion("act_eq", "accepted_action", "equal", "speaker"),
            _criterion("raw_diff", "raw_view", "different", "speaker"),
        ]
        cases = {"alpha": {c["id"]: "a-spk" for c in criteria}}
        rep = self.report(manifest, pin, self.design(criteria, cases))
        self.assertEqual(
            rep["cases"][0]["results"],
            {"act_diff": "witness", "act_eq": "opposite", "raw_diff": "witness"},
        )
        self.assertEqual(rep["selection"]["cases"], ["alpha"])
        self.assertEqual(rep["selection"]["not_witnessed"], ["act_eq"])


if __name__ == "__main__":
    unittest.main()
