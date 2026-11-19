"""Exercise a sealed native observer as both a reader and an Eval Lab consumer."""

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from seatlab import author, tape
from seatlab.study import StudyMismatch, seal_study, verify_study
from seatlab.study_contract import RECIPE_SCHEMA, parse_recipe
from seatlab.study_html import write_study_html
from seatlab.study_relations import export_relations
from seatlab.world_order import WORLD_ORDER_SCHEMA


def _trial(root, side, *, closed=True):
    uid = str(UUID(int=side + 1))
    case = tape.digest({"game": "two-symbols"})
    episodes = []
    for role in ("speaker", "listener"):
        frames = []
        for turn in (1, 2):
            view = {
                "signal": side if turn == 2 else 0,
                "position": [12, 24],
                "private": "keep-this-out-of-the-summary",
            }
            action = side if role == "speaker" else None
            frames.append(
                author.accepted_frame(
                    turn=turn, view=view, acknowledged_sha256=tape.digest(view), action=action
                )
            )
        episodes.append(
            author.episode_from_frames(
                id=f"{uid}:{role}",
                case_sha256=case,
                role=role,
                native_trial_id=uid,
                closed=closed,
                frames=frames,
            )
        )
    tape.write_lines(root / f"{side}.jsonl", episodes)
    tape.write_lines(
        root / f"order{side}.json",
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
    return uid


class PortableTrajectory(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.pair = [_trial(self.root, s) for s in (0, 1)]
        base = dict(
            kind="role-trajectory",
            source="0.jsonl",
            target="1.jsonl",
            source_order="order0.json",
            target_order="order1.json",
            role="listener",
        )
        self.checks = [
            dict(base, id="physical", fields=["/position/1", "/position/0"]),
            dict(base, id="absent", fields=["/absent<&"]),
            dict(base, id="speaker", role="speaker"),
        ]

    def seal(self):
        recipe, manifest = (self.root / name for name in ("recipe.json", "study.json"))
        tape.write_lines(recipe, [{"schema": RECIPE_SCHEMA, "checks": self.checks}])
        doc = seal_study(recipe, manifest)
        return manifest, tape.digest(doc)

    def test_independent_views_actions_and_role_joins(self):
        path, pin = self.seal()
        doc = verify_study(path, expect_sha256=pin)
        self.assertEqual((doc["status"], doc["files"]), ("verified", 4))
        physical, missing, speaker = doc["checks"]
        self.assertEqual(physical["raw_view"]["first_different_step"], 2)
        self.assertEqual(
            (physical["selected_view"]["equal"], physical["accepted_action"]["equal"]), (2, 2)
        )
        self.assertEqual(missing["selected_view"]["unknown"], 2)
        self.assertEqual(speaker["accepted_action"]["different"], 2)
        self.assertNotIn("keep-this-out", json.dumps(doc))
        html = self.root / "report.html"
        write_study_html(doc, html)
        text = html.read_text()
        self.assertIn("/absent&lt;&amp;", text)
        self.assertNotIn("keep-this-out", text)
        self.assertIn("First unknown step", text)
        out = self.root / "relations.jsonl"
        export_relations(path, out, expect_sha256=pin)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual([r["native_action_changed"] for r in rows], [False, False, True])
        for r in rows:
            self.assertEqual(
                [r["source_native_trial_id"], r["target_native_trial_id"]], self.pair
            )
            self.assertEqual(
                (r["matched_prefix"], r["reproduced"], r["boundary_unit"]),
                (None, None, "world_step"),
            )

    def test_open_agreement_is_not_a_negative_action_result(self):
        for name in ("1.jsonl", "order1.json"):
            (self.root / name).unlink()
        _trial(self.root, 1, closed=False)
        path, pin = self.seal()
        out = self.root / "relations.jsonl"
        export_relations(path, out, expect_sha256=pin)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual(
            [(r["status"], r["native_action_changed"]) for r in rows],
            [("open", None), ("open", None), ("open", True)],
        )

    def test_changed_input_and_wrong_pin_publish_no_rows(self):
        path, pin = self.seal()
        out = self.root / "refused.jsonl"
        with self.assertRaises(StudyMismatch):
            export_relations(path, out, expect_sha256="0" * 64)
        with (self.root / "0.jsonl").open("ab") as f:
            f.write(b"\n")
        with self.assertRaises(StudyMismatch):
            export_relations(path, out, expect_sha256=pin)
        self.assertFalse(out.exists())

    def test_invalid_pointer_lists_do_not_silently_mean_whole_view(self):
        for fields in (None, [], ["x"], ["/x", "/x"], ["/bad~3"], [7]):
            with self.subTest(fields=fields), self.assertRaises(tape.TapeError):
                parse_recipe(
                    {"schema": RECIPE_SCHEMA, "checks": [dict(self.checks[0], fields=fields)]}
                )
