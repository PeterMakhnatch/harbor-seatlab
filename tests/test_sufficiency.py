import itertools
import json
import tempfile
import unittest
from pathlib import Path
from typing import Literal, cast

from seatlab import tape as wire
from seatlab.episodes import Episode, EpisodeFrame
from seatlab.sufficiency import audit_context

_T1 = "11111111-1111-4111-8111-111111111111"
_T2 = "22222222-2222-4222-8222-222222222222"
_T3 = "33333333-3333-4333-8333-333333333333"
_T4 = "44444444-4444-4444-8444-444444444444"
_T5 = "55555555-5555-4555-8555-555555555555"


def _frame(
    turn: int,
    view: dict[str, object],
    action: wire.Action = None,
    *,
    observed: bool = True,
    basis: Literal["bound", "reconstructed"] = "bound",
) -> EpisodeFrame:
    return {
        "turn": turn,
        "basis": basis,
        "view": view,
        "view_sha256": wire.digest(view),
        "observed": {"action": action} if observed else None,
    }


def _episode(
    trial: str,
    frames: list[EpisodeFrame],
    *,
    role: str = "planner",
    case: str | None = None,
    episode_id: str | None = None,
    closed: bool = True,
) -> Episode:
    row: Episode = {
        "schema": "seatlab.episode/v1",
        "id": episode_id if episode_id is not None else f"{trial}:{role}",
        "case_sha256": wire.digest(trial if case is None else case),
        "role": role,
        "native_trial_id": trial,
        "closed": closed,
        "frames": frames,
        "episode_sha256": "",
    }
    row["episode_sha256"] = wire.digest(
        {key: value for key, value in row.items() if key != "episode_sha256"}
    )
    return row


def _brute_force_minimum_errors(samples: list[tuple[object, wire.Action]]) -> int:
    """Enumerate every deterministic lookup table from input class to observed action."""
    classes = sorted({key for key, _ in samples}, key=repr)
    actions = list({(type(action), action) for _, action in samples})
    best = len(samples)
    for choice in itertools.product(actions, repeat=len(classes)):
        table = dict(zip(classes, choice, strict=True))
        errors = sum(1 for key, action in samples if table[key] != (type(action), action))
        if errors < best:
            best = errors
    return best


def _section(mapping: dict[str, object], key: str) -> dict[str, object]:
    return cast(dict[str, object], mapping[key])


def _witnesses(report: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], _section(report, "fit")["witnesses"])


class SufficiencyAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)

    def _write(self, name: str, *episodes: Episode) -> Path:
        path = Path(tempfile.mkdtemp(dir=self.root)) / name
        wire.write_lines(path, episodes)
        return path

    def test_enumerated_deterministic_mapping_optimum_matches_bound(self) -> None:
        # Three distinct full views collide into two projected classes under ["/signal"].
        # Class ("planner", "A"): ["go", "go", "stop"] -> 1 error.
        # Class ("planner", "B"): ["left", "right"] -> 1 error.
        # Full view: ("A", 1) has ["go", "stop"] -> 1 error; others unique -> 0 errors.
        samples: list[tuple[dict[str, object], wire.Action]] = [
            ({"signal": "A", "detail": 1}, "go"),
            ({"signal": "A", "detail": 1}, "stop"),
            ({"signal": "A", "detail": 2}, "go"),
            ({"signal": "B", "detail": 1}, "left"),
            ({"signal": "B", "detail": 2}, "right"),
        ]
        episodes = [
            _episode(
                _T1,
                [
                    _frame(1, samples[0][0], samples[0][1]),
                    _frame(2, samples[1][0], samples[1][1]),
                ],
            ),
            _episode(
                _T2,
                [
                    _frame(1, samples[2][0], samples[2][1]),
                    _frame(2, samples[3][0], samples[3][1]),
                ],
            ),
            _episode(_T3, [_frame(1, samples[4][0], samples[4][1])]),
        ]
        train = self._write("train.jsonl", *episodes)

        report = audit_context(train, fields=["/signal"])
        self.assertEqual(report["schema"], "seatlab.context-audit/v1")
        self.assertEqual(report["fields"], ["/signal"])
        self.assertIsNone(report["heldout"])

        expected_full_errors = _brute_force_minimum_errors(
            [(("planner", wire.canonical(view)), action) for view, action in samples]
        )
        expected_selected_errors = _brute_force_minimum_errors(
            [(("planner", view["signal"]), action) for view, action in samples]
        )

        fit = _section(report, "fit")
        self.assertEqual(
            fit["counts"],
            {
                "episodes": 3,
                "cases": 3,
                "trials": 3,
                "frames": 5,
                "eligible": 5,
                "excluded_unbound": 0,
                "excluded_missing": 0,
            },
        )
        self.assertEqual(
            fit["full"],
            {
                "classes": 4,
                "repeated_classes": 1,
                "repeated_frames": 2,
                "conflicting_classes": 1,
                "minimum_errors": expected_full_errors,
                "maximum_matches": len(samples) - expected_full_errors,
            },
        )
        self.assertEqual(
            fit["selected"],
            {
                "classes": 2,
                "repeated_classes": 2,
                "repeated_frames": 5,
                "conflicting_classes": 2,
                "minimum_errors": expected_selected_errors,
                "maximum_matches": len(samples) - expected_selected_errors,
            },
        )
        self.assertEqual(
            fit["additional_minimum_errors"],
            expected_selected_errors - expected_full_errors,
        )

        # Full-view mode (fields=None) agrees with full metrics and has zero delta.
        full_only = audit_context(train, fields=None)
        self.assertIsNone(full_only["fields"])
        full_fit = _section(full_only, "fit")
        self.assertEqual(full_fit["selected"], full_fit["full"])
        self.assertEqual(full_fit["additional_minimum_errors"], 0)

        # Field order is canonicalized lexicographically.
        multi_a = audit_context(train, fields=["/signal", "/detail"])
        multi_b = audit_context(train, fields=["/detail", "/signal"])
        self.assertEqual(multi_a["fields"], ["/detail", "/signal"])
        self.assertEqual(wire.canonical(multi_a), wire.canonical(multi_b))

    def test_missing_observation_unbound_precedence_and_observed_null_action(self) -> None:
        # Unbound + missing observation counts only as excluded_unbound (unbound first).
        # Accepted null ({"action": None}) is a real action distinct from other types
        # and distinct from missing observation (observed=None).
        # Missing projected field is distinct from a present null field in the view.
        ep1 = _episode(
            _T1,
            [
                _frame(1, {"flag": None}, None),
                _frame(2, {"flag": None}, "act"),
                _frame(3, {}, "other"),
                _frame(4, {"flag": 0}, 0),
                _frame(5, {"flag": "0"}, "0"),
                _frame(6, {"flag": "unseen"}, observed=False, basis="reconstructed"),
                _frame(7, {"flag": "unbound_obs"}, "act", basis="reconstructed"),
                _frame(8, {"flag": "missing_obs"}, observed=False, basis="bound"),
            ],
        )
        train = self._write("train.jsonl", ep1)

        report = audit_context(train, fields=["/flag"])
        fit = _section(report, "fit")
        self.assertEqual(
            fit["counts"],
            {
                "episodes": 1,
                "cases": 1,
                "trials": 1,
                "frames": 8,
                "eligible": 5,
                "excluded_unbound": 2,
                "excluded_missing": 1,
            },
        )
        # Projected classes for "/flag":
        #   present None -> [None, "act"] (2 frames, 1 conflict, 1 min error)
        #   missing      -> ["other"]     (distinct from present None)
        #   present 0    -> [0]           (distinct from "0")
        #   present "0"  -> ["0"]
        self.assertEqual(
            fit["selected"],
            {
                "classes": 4,
                "repeated_classes": 1,
                "repeated_frames": 2,
                "conflicting_classes": 1,
                "minimum_errors": 1,
                "maximum_matches": 4,
            },
        )
        self.assertEqual(fit["full"], fit["selected"])
        self.assertEqual(fit["additional_minimum_errors"], 0)

        # Typed action distinction: 1 vs "1" vs None on identical view must conflict.
        ep_types = _episode(
            _T2,
            [
                _frame(1, {"k": "same"}, 1),
                _frame(2, {"k": "same"}, "1"),
                _frame(3, {"k": "same"}, None),
            ],
        )
        typed_report = audit_context(self._write("typed.jsonl", ep_types))
        self.assertEqual(
            _section(typed_report, "fit")["selected"],
            {
                "classes": 1,
                "repeated_classes": 1,
                "repeated_frames": 3,
                "conflicting_classes": 1,
                "minimum_errors": 2,
                "maximum_matches": 1,
            },
        )

    def test_roles_never_collide_even_with_identical_views_or_empty_projection(self) -> None:
        shared_view: dict[str, object] = {"room": "alpha", "note": "secret-note"}
        ep_a = _episode(_T1, [_frame(1, shared_view, "accept")], role="planner", case="c1")
        ep_b = _episode(_T1, [_frame(1, shared_view, "reject")], role="partner", case="c1")
        ep_c = _episode(_T2, [_frame(1, shared_view, "accept")], role="planner", case="c2")
        ep_d = _episode(_T2, [_frame(1, shared_view, "reject")], role="partner", case="c2")
        train = self._write("roles.jsonl", ep_a, ep_b, ep_c, ep_d)

        # Even with fields=() (role identity only), planner and partner stay separate.
        report = audit_context(train, fields=())
        self.assertEqual(report["fields"], [])
        fit = _section(report, "fit")
        self.assertEqual(
            fit["counts"],
            {
                "episodes": 4,
                "cases": 2,
                "trials": 2,
                "frames": 4,
                "eligible": 4,
                "excluded_unbound": 0,
                "excluded_missing": 0,
            },
        )
        self.assertEqual(
            fit["selected"],
            {
                "classes": 2,
                "repeated_classes": 2,
                "repeated_frames": 4,
                "conflicting_classes": 0,
                "minimum_errors": 0,
                "maximum_matches": 4,
            },
        )
        self.assertEqual(fit["full"], fit["selected"])
        self.assertEqual(_witnesses(report), [])

        # Adding a conflict inside one role affects only that role.
        ep_conflict = _episode(
            _T3, [_frame(1, shared_view, "defer")], role="planner", case="c3"
        )
        report2 = audit_context(
            self._write("roles_conflict.jsonl", ep_a, ep_b, ep_c, ep_d, ep_conflict),
            fields=(),
        )
        selected2 = _section(_section(report2, "fit"), "selected")
        self.assertEqual(selected2["conflicting_classes"], 1)
        self.assertEqual(selected2["minimum_errors"], 1)
        self.assertEqual([w["role"] for w in _witnesses(report2)], ["planner"])

    def test_heldout_rejects_shared_trial_or_case_even_across_roles_or_all_excluded(
        self,
    ) -> None:
        train = self._write(
            "train.jsonl",
            _episode(_T1, [_frame(1, {"x": 1}, "a")], role="planner", case="case-1"),
        )
        clean_heldout = _episode(
            _T5, [_frame(1, {"x": 1}, "a")], role="planner", case="case-clean"
        )

        # 1. Shared native_trial_id (different case, different role).
        leak_trial = self._write(
            "leak_trial.jsonl",
            clean_heldout,
            _episode(_T1, [_frame(1, {"x": 1}, "a")], role="partner", case="case-2"),
        )
        with self.assertRaises(ValueError):
            audit_context(train, heldout=leak_trial)

        # 2. Shared case_sha256 (different trial, different role).
        leak_case = self._write(
            "leak_case.jsonl",
            _episode(_T2, [_frame(1, {"x": 1}, "a")], role="partner", case="case-1"),
        )
        with self.assertRaises(ValueError):
            audit_context(train, heldout=leak_case)

        # 3. Leak inside an all-excluded held-out episode (unbound / missing obs).
        leak_excluded_case = self._write(
            "leak_excluded_case.jsonl",
            _episode(
                _T2,
                [_frame(1, {"x": 1}, observed=False, basis="reconstructed")],
                role="partner",
                case="case-1",
            ),
        )
        with self.assertRaises(ValueError):
            audit_context(train, heldout=leak_excluded_case)

        leak_excluded_trial = self._write(
            "leak_excluded_trial.jsonl",
            _episode(
                _T1,
                [_frame(1, {"x": 1}, observed=False, basis="bound")],
                role="partner",
                case="case-9",
            ),
        )
        with self.assertRaises(ValueError):
            audit_context(train, heldout=leak_excluded_trial)

        # 4. Leak where the train episode itself had zero eligible frames.
        train_all_excluded = self._write(
            "train_excluded.jsonl",
            _episode(
                _T1,
                [_frame(1, {"x": 1}, observed=False, basis="reconstructed")],
                role="planner",
                case="case-1",
            ),
        )
        heldout_same_case = self._write(
            "heldout_same_case.jsonl",
            _episode(_T2, [_frame(1, {"x": 1}, "a")], role="planner", case="case-1"),
        )
        with self.assertRaises(ValueError):
            audit_context(train_all_excluded, heldout=heldout_same_case)

    def test_heldout_scoring_ties_unseen_majority_and_no_online_update(self) -> None:
        # Train classes under fields=["/k"]:
        #   k="maj":  "a" x2, "b" x1 -> unique majority "a" (unequal counts)
        #   k="tie":  "x" x1, "y" x1 -> tied (must not predict either)
        #   k="null": None x2, "n" x1 -> unique majority None (accepted null predicts)
        train = self._write(
            "train.jsonl",
            _episode(
                _T1,
                [
                    _frame(1, {"k": "maj", "d": 1}, "a"),
                    _frame(2, {"k": "maj", "d": 1}, "a"),
                    _frame(3, {"k": "maj", "d": 2}, "b"),
                    _frame(4, {"k": "tie", "d": 1}, "x"),
                    _frame(5, {"k": "tie", "d": 2}, "y"),
                    _frame(6, {"k": "null", "d": 1}, None),
                    _frame(7, {"k": "null", "d": 2}, None),
                    _frame(8, {"k": "null", "d": 3}, "n"),
                ],
                role="planner",
                case="train-1",
            ),
        )

        # Heldout checks:
        # - Repeated "b" on k="maj" must all disagree (never flips majority from "a").
        # - Repeated "x" on k="tie" must all stay tied (never breaks tie).
        # - Repeated "z" on k="new" must all stay unseen (never inserts into lookup).
        # - k="null" predicts accepted None; different role "partner" is unseen.
        heldout = self._write(
            "heldout.jsonl",
            _episode(
                _T2,
                [
                    _frame(1, {"k": "maj", "d": 1}, "a"),
                    _frame(2, {"k": "maj", "d": 9}, "b"),
                    _frame(3, {"k": "maj", "d": 9}, "b"),
                    _frame(4, {"k": "maj", "d": 9}, "b"),
                    _frame(5, {"k": "tie", "d": 1}, "x"),
                    _frame(6, {"k": "tie", "d": 1}, "x"),
                    _frame(7, {"k": "new", "d": 1}, "z"),
                    _frame(8, {"k": "new", "d": 1}, "z"),
                    _frame(9, {"k": "null", "d": 9}, None),
                    _frame(10, {"k": "maj", "d": 1}, "a", basis="reconstructed"),
                    _frame(11, {"k": "maj", "d": 1}, observed=False, basis="bound"),
                ],
                role="planner",
                case="held-1",
            ),
            _episode(
                _T3,
                [_frame(1, {"k": "maj", "d": 1}, "a")],
                role="partner",
                case="held-2",
            ),
        )

        train_only = audit_context(train, fields=["/k"])
        report = audit_context(train, fields=["/k"], heldout=heldout)
        self.assertEqual(report["fit"], train_only["fit"])

        ho = _section(report, "heldout")
        self.assertEqual(
            ho["counts"],
            {
                "episodes": 2,
                "cases": 2,
                "trials": 2,
                "frames": 12,
                "eligible": 10,
                "excluded_unbound": 1,
                "excluded_missing": 1,
            },
        )
        self.assertEqual(
            ho["selected"],
            {"matched": 2, "disagreed": 3, "tied": 2, "unseen": 3},
        )
        self.assertEqual(
            ho["full"],
            {"matched": 3, "disagreed": 0, "tied": 0, "unseen": 7},
        )

    def test_witnesses_are_permutation_invariant_bounded_and_redact_private_values(
        self,
    ) -> None:
        view_a: dict[str, object] = {
            "group": "SECRET-GROUP-G1",
            "private_payload": "TOP-SECRET-VIEW-ALPHA",
        }
        view_b: dict[str, object] = {
            "group": "SECRET-GROUP-G1",
            "private_payload": "TOP-SECRET-VIEW-BETA",
        }
        view_c: dict[str, object] = {
            "group": "SECRET-GROUP-G2",
            "private_payload": "TOP-SECRET-VIEW-GAMMA",
        }
        view_d: dict[str, object] = {
            "group": "SECRET-GROUP-G2",
            "private_payload": "TOP-SECRET-VIEW-DELTA",
        }

        ep1 = _episode(
            _T2,
            [
                _frame(1, view_a, "SECRET-ACTION-ONE"),
                _frame(2, view_b, "SECRET-ACTION-TWO"),
            ],
            role="planner",
            case="c-1",
            episode_id="PRIVATE-HUMAN-EPISODE-ID-1",
        )
        ep2 = _episode(
            _T1,
            [
                _frame(1, view_a, "SECRET-ACTION-ONE"),
                _frame(2, view_b, "SECRET-ACTION-THREE"),
            ],
            role="planner",
            case="c-2",
            episode_id="PRIVATE-HUMAN-EPISODE-ID-2",
        )
        ep3 = _episode(
            _T3,
            [
                _frame(1, view_c, "SECRET-ACTION-FOUR"),
                _frame(2, view_d, "SECRET-ACTION-FIVE"),
            ],
            role="analyst",
            case="c-3",
            episode_id="PRIVATE-HUMAN-EPISODE-ID-3",
        )
        ep4 = _episode(
            _T4,
            [
                _frame(1, {"group": "NO-CONFLICT", "private_payload": "NEVER-1"}, "ok"),
                _frame(2, {"group": "NO-CONFLICT", "private_payload": "NEVER-2"}, "ok"),
            ],
            role="analyst",
            case="c-4",
            episode_id="PRIVATE-HUMAN-EPISODE-ID-4",
        )

        # Permuting episode order in the input JSONL yields byte-identical reports.
        ordered = self._write("ordered.jsonl", ep1, ep2, ep3, ep4)
        permuted = self._write("permuted.jsonl", ep4, ep3, ep1, ep2)

        report_a = audit_context(ordered, fields=["/group"], witnesses=10)
        report_b = audit_context(permuted, fields=["/group"], witnesses=10)
        self.assertEqual(wire.canonical(report_a), wire.canonical(report_b))

        witnesses = _witnesses(report_a)
        self.assertEqual(len(witnesses), 2)
        keys = [(w["role"], w["projection_sha256"]) for w in witnesses]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual([w["role"] for w in witnesses], ["analyst", "planner"])

        expected_proj = {
            "analyst": wire.digest([[True, "SECRET-GROUP-G2"]]),
            "planner": wire.digest([[True, "SECRET-GROUP-G1"]]),
        }
        expected_counts = {"analyst": 2, "planner": 4}
        by_trial = {ep["native_trial_id"]: ep for ep in (ep1, ep2, ep3, ep4)}
        for witness in witnesses:
            role = cast(str, witness["role"])
            self.assertEqual(set(witness), {"role", "projection_sha256", "count", "examples"})
            self.assertEqual(witness["projection_sha256"], expected_proj[role])
            self.assertEqual(witness["count"], expected_counts[role])
            examples = cast(list[dict[str, object]], witness["examples"])
            self.assertEqual(len(examples), 2)
            self.assertLess(
                cast(str, examples[0]["action_sha256"]),
                cast(str, examples[1]["action_sha256"]),
            )
            for ex in examples:
                self.assertEqual(
                    set(ex),
                    {
                        "native_trial_id",
                        "turn",
                        "episode_sha256",
                        "view_sha256",
                        "action_sha256",
                    },
                )
                src_ep = by_trial[cast(str, ex["native_trial_id"])]
                src_frame = src_ep["frames"][cast(int, ex["turn"]) - 1]
                self.assertEqual(ex["episode_sha256"], src_ep["episode_sha256"])
                self.assertEqual(ex["view_sha256"], src_frame["view_sha256"])
                self.assertEqual(ex["action_sha256"], wire.digest(src_frame["observed"]))

        # Verify planner examples pick the 2 smallest action hashes and earliest
        # (native_trial_id, turn) receipt for each action.
        planner_hashes = {
            wire.digest({"action": "SECRET-ACTION-ONE"}): (_T1, 1),
            wire.digest({"action": "SECRET-ACTION-TWO"}): (_T2, 2),
            wire.digest({"action": "SECRET-ACTION-THREE"}): (_T1, 2),
        }
        expected_planner_pair = [(h, planner_hashes[h]) for h in sorted(planner_hashes)[:2]]
        planner_examples = cast(list[dict[str, object]], witnesses[1]["examples"])
        self.assertEqual(
            [
                (ex["action_sha256"], (ex["native_trial_id"], ex["turn"]))
                for ex in planner_examples
            ],
            expected_planner_pair,
        )

        # No raw view values, projected values, action strings, human IDs, or paths.
        serialized = json.dumps(report_a)
        for sensitive in (
            "SECRET-GROUP",
            "TOP-SECRET-VIEW",
            "SECRET-ACTION",
            "PRIVATE-HUMAN-EPISODE-ID",
            "NO-CONFLICT",
            "NEVER-1",
            "ordered.jsonl",
            str(self.root),
            '"view"',
        ):
            with self.subTest(sensitive=sensitive):
                self.assertNotIn(sensitive, serialized)

        # Witness limit caps returned witnesses, including 0.
        capped = audit_context(ordered, fields=["/group"], witnesses=1)
        none_w = audit_context(ordered, fields=["/group"], witnesses=0)
        self.assertEqual(len(_witnesses(capped)), 1)
        self.assertEqual(_witnesses(none_w), [])

    def test_zero_eligible_frames_and_invalid_inputs(self) -> None:
        all_excluded = self._write(
            "zero_eligible.jsonl",
            _episode(
                _T1,
                [
                    _frame(1, {"x": 1}, "a", basis="reconstructed"),
                    _frame(2, {"x": 1}, observed=False, basis="bound"),
                ],
                case="c1",
            ),
        )
        heldout_excluded = self._write(
            "heldout_zero.jsonl",
            _episode(_T2, [_frame(1, {"x": 1}, observed=False, basis="bound")], case="c2"),
        )
        report = audit_context(all_excluded, fields=["/x"], heldout=heldout_excluded)
        json.dumps(report, allow_nan=False)
        fit = _section(report, "fit")
        self.assertEqual(
            fit["counts"],
            {
                "episodes": 1,
                "cases": 1,
                "trials": 1,
                "frames": 2,
                "eligible": 0,
                "excluded_unbound": 1,
                "excluded_missing": 1,
            },
        )
        zero_summary = {
            "classes": 0,
            "repeated_classes": 0,
            "repeated_frames": 0,
            "conflicting_classes": 0,
            "minimum_errors": 0,
            "maximum_matches": 0,
        }
        self.assertEqual(fit["full"], zero_summary)
        self.assertEqual(fit["selected"], zero_summary)
        self.assertEqual(fit["additional_minimum_errors"], 0)
        self.assertEqual(_witnesses(report), [])
        ho = _section(report, "heldout")
        self.assertEqual(
            ho["counts"],
            {
                "episodes": 1,
                "cases": 1,
                "trials": 1,
                "frames": 1,
                "eligible": 0,
                "excluded_unbound": 0,
                "excluded_missing": 1,
            },
        )
        zero_heldout = {"matched": 0, "disagreed": 0, "tied": 0, "unseen": 0}
        self.assertEqual(ho["full"], zero_heldout)
        self.assertEqual(ho["selected"], zero_heldout)

        # Empty episode files must be rejected for train and heldout.
        empty = self._write("empty.jsonl")
        valid = self._write(
            "valid.jsonl", _episode(_T5, [_frame(1, {"x": 1}, "a")], case="c5")
        )
        with self.assertRaises(ValueError):
            audit_context(empty)
        with self.assertRaises(ValueError):
            audit_context(valid, heldout=empty)

        # Invalid witnesses bounds (outside 0..100 or bool).
        for bad_witnesses in (-1, 101, True, False):
            with self.subTest(witnesses=bad_witnesses):
                with self.assertRaises(ValueError):
                    audit_context(valid, witnesses=bad_witnesses)


if __name__ == "__main__":
    unittest.main()
