import tempfile
import unittest
from pathlib import Path
from typing import Any, Literal, cast

from seatlab import author, tape as wire
from seatlab.episodes import Episode, EpisodeFrame
from seatlab.team_alignment import TEAM_ALIGNMENT_SCHEMA, team_align
from seatlab.world_order import WORLD_ORDER_SCHEMA

_SOURCE = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_TARGET = "be656986-a740-4e46-999d-21410909878c"
_CASE_A = wire.digest({"world": "team", "case": "alpha"})
_CASE_B = wire.digest({"world": "team", "case": "beta"})


def _bound(turn: int, view: dict[str, object], action: wire.Action) -> EpisodeFrame:
    return author.accepted_frame(
        turn=turn,
        view=view,
        acknowledged_sha256=wire.digest(view),
        action=action,
    )


def _raw_frame(
    turn: int,
    view: dict[str, object],
    *,
    basis: Literal["bound", "reconstructed"] = "bound",
    observed: wire.Observation | None = None,
) -> EpisodeFrame:
    return {
        "turn": turn,
        "basis": basis,
        "view": view,
        "view_sha256": wire.digest(view),
        "observed": observed,
    }


def _episode(
    trial: str,
    role: str,
    frames: list[EpisodeFrame],
    *,
    case_sha256: str = _CASE_A,
    closed: bool = True,
) -> Episode:
    return author.episode_from_frames(
        id=f"{trial}:{role}",
        case_sha256=case_sha256,
        role=role,
        native_trial_id=trial,
        closed=closed,
        frames=frames,
    )


def _order(episodes: list[Episode], steps: list[list[str]]) -> dict[str, object]:
    first = episodes[0]
    return {
        "schema": WORLD_ORDER_SCHEMA,
        "case_sha256": first["case_sha256"],
        "native_trial_id": first["native_trial_id"],
        "episodes": {ep["role"]: ep["episode_sha256"] for ep in episodes},
        "steps": steps,
    }


class TeamAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)

    def _write_pair(
        self,
        source_episodes: list[Episode],
        source_steps: list[list[str]],
        target_episodes: list[Episode],
        target_steps: list[list[str]],
    ) -> tuple[Path, Path, Path, Path]:
        folder = Path(tempfile.mkdtemp(dir=self.root))
        src_tape = folder / "source.jsonl"
        dst_tape = folder / "target.jsonl"
        src_order = folder / "source-order.json"
        dst_order = folder / "target-order.json"
        wire.write_lines(src_tape, source_episodes)
        wire.write_lines(dst_tape, target_episodes)
        src_order.write_bytes(wire.canonical(_order(source_episodes, source_steps)))
        dst_order.write_bytes(wire.canonical(_order(target_episodes, target_steps)))
        return src_tape, dst_tape, src_order, dst_order

    def _run(
        self,
        source_episodes: list[Episode],
        source_steps: list[list[str]],
        target_episodes: list[Episode],
        target_steps: list[list[str]],
        *,
        input_paths: int | None = None,
    ) -> dict[str, object]:
        src, dst, src_order, dst_order = self._write_pair(
            source_episodes, source_steps, target_episodes, target_steps
        )
        return team_align(
            src,
            dst,
            source_order=src_order,
            target_order=dst_order,
            input_paths=input_paths,
        )

    def test_simultaneous_two_differences_and_redacted_input_paths_withhold_suffix(
        self,
    ) -> None:
        v1_a: dict[str, object] = {"step": 1, "secret": "shared-a"}
        v1_b: dict[str, object] = {"step": 1, "secret": "shared-b"}
        v2_a: dict[str, object] = {"step": 2, "dish": "soup-secret"}
        v2_b_src: dict[str, object] = {"step": 2, "pot": "src-secret", "extra": "only-src"}
        v2_b_dst: dict[str, object] = {"step": 2, "pot": "dst-secret", "extra": "only-dst"}
        v3_src: dict[str, object] = {"step": 3, "later": "later-src-value"}
        v3_dst: dict[str, object] = {"step": 3, "later": "later-dst-value"}

        src_eps = [
            _episode(
                _SOURCE,
                "chef0",
                [
                    _bound(1, v1_a, "wait"),
                    _bound(2, v2_a, "chop-src"),
                    _bound(3, v3_src, "suffix-src-0"),
                ],
            ),
            _episode(
                _SOURCE,
                "chef1",
                [
                    _bound(1, v1_b, "hold"),
                    _bound(2, v2_b_src, "plate-src"),
                    _bound(3, v3_src, "suffix-src-1"),
                ],
            ),
        ]
        dst_eps = [
            _episode(
                _TARGET,
                "chef0",
                [
                    _bound(1, v1_a, "wait"),
                    _bound(2, v2_a, "serve-dst"),
                    _bound(3, v3_dst, "suffix-dst-0"),
                    _bound(4, {"step": 4}, "extra-dst-0"),
                ],
            ),
            _episode(
                _TARGET,
                "chef1",
                [
                    _bound(1, v1_b, "hold"),
                    _bound(2, v2_b_dst, "plate-dst"),
                    _bound(3, v3_dst, "suffix-dst-1"),
                    _bound(4, {"step": 4}, "extra-dst-1"),
                ],
            ),
        ]
        src_steps = [["chef0", "chef1"], ["chef1", "chef0"], ["chef0", "chef1"]]
        dst_steps = [
            ["chef1", "chef0"],
            ["chef0", "chef1"],
            ["chef0", "chef1"],
            ["chef0", "chef1"],
        ]

        default = self._run(src_eps, src_steps, dst_eps, dst_steps)
        self.assertEqual(
            default,
            {
                "schema": TEAM_ALIGNMENT_SCHEMA,
                "case_sha256": _CASE_A,
                "source_native_trial_id": _SOURCE,
                "target_native_trial_id": _TARGET,
                "status": "step_changed",
                "matched_steps": 1,
                "boundary_step": 2,
                "source_steps": 3,
                "target_steps": 4,
                "source_withheld_steps": 1,
                "target_withheld_steps": 2,
                "source_roles": ["chef0", "chef1"],
                "target_roles": ["chef0", "chef1"],
                "roles": [
                    {"role": "chef0", "turn": 2, "status": "action_changed"},
                    {"role": "chef1", "turn": 2, "status": "input_changed"},
                ],
            },
        )
        encoded = wire.canonical(default)
        for forbidden in (
            b"secret",
            b"chop-src",
            b"serve-dst",
            b"plate-src",
            b"plate-dst",
            b"suffix-",
            b"later-",
            b"extra-dst",
        ):
            self.assertNotIn(forbidden, encoded)

        detailed = self._run(src_eps, src_steps, dst_eps, dst_steps, input_paths=1)
        roles = cast(list[dict[str, object]], detailed["roles"])
        self.assertNotIn("input_diff", roles[0])
        self.assertEqual(
            roles[1]["input_diff"],
            {
                "changes": 2,
                "shown": 1,
                "truncated": True,
                "paths": [{"path": "/extra", "change": "value_changed"}],
            },
        )
        detailed_encoded = wire.canonical(detailed)
        self.assertNotIn(b"src-secret", detailed_encoded)
        self.assertNotIn(b"dst-secret", detailed_encoded)
        self.assertNotIn(b"only-src", detailed_encoded)
        self.assertNotIn(b"later-", detailed_encoded)

    def test_swapped_writer_and_group_order_produces_identical_repeatable_report(
        self,
    ) -> None:
        v1_a: dict[str, object] = {"turn": 1, "who": "a"}
        v1_b: dict[str, object] = {"turn": 1, "who": "b"}
        v2_a: dict[str, object] = {"turn": 2, "who": "a"}
        v2_b: dict[str, object] = {"turn": 2, "who": "b"}

        src_a = _episode(_SOURCE, "alpha", [_bound(1, v1_a, 1), _bound(2, v2_a, "go")])
        src_b = _episode(_SOURCE, "beta", [_bound(1, v1_b, 2), _bound(2, v2_b, "ok")])
        dst_a = _episode(_TARGET, "alpha", [_bound(1, v1_a, 1), _bound(2, v2_a, "go")])
        dst_b = _episode(_TARGET, "beta", [_bound(1, v1_b, 2), _bound(2, v2_b, "ok")])

        first = self._run(
            [src_a, src_b],
            [["alpha", "beta"], ["beta", "alpha"]],
            [dst_a, dst_b],
            [["beta", "alpha"], ["alpha", "beta"]],
        )
        second = self._run(
            [src_b, src_a],
            [["beta", "alpha"], ["alpha", "beta"]],
            [dst_b, dst_a],
            [["alpha", "beta"], ["beta", "alpha"]],
        )
        self.assertEqual(wire.canonical(first), wire.canonical(second))
        self.assertEqual(first["status"], "matched")
        self.assertEqual(first["matched_steps"], 2)
        self.assertIsNone(first["boundary_step"])
        self.assertEqual(first["source_withheld_steps"], 0)
        self.assertEqual(first["target_withheld_steps"], 0)
        self.assertEqual(first["roles"], [])
        self.assertNotIn(str(self.root).encode("utf-8"), wire.canonical(first))

    def test_role_local_false_signal_after_prior_teammate_global_divergence(self) -> None:
        # Role "receiver" sees identical inputs and makes a different action on its own
        # turn 2, but teammate "sender" already diverged earlier at global step 1.
        s1_view: dict[str, object] = {"step": 1, "actor": "sender"}
        r1_view: dict[str, object] = {"step": 2, "actor": "receiver"}
        r2_view: dict[str, object] = {"step": 3, "actor": "receiver"}

        src_eps = [
            _episode(_SOURCE, "sender", [_bound(1, s1_view, "hint-red")]),
            _episode(
                _SOURCE,
                "receiver",
                [_bound(1, r1_view, "play-1"), _bound(2, r2_view, "play-2")],
            ),
        ]
        dst_eps = [
            _episode(_TARGET, "sender", [_bound(1, s1_view, "hint-blue")]),
            _episode(
                _TARGET,
                "receiver",
                [_bound(1, r1_view, "play-1"), _bound(2, r2_view, "discard-2")],
            ),
        ]
        steps = [["sender"], ["receiver"], ["receiver"]]

        report = self._run(src_eps, steps, dst_eps, steps)
        self.assertEqual(report["status"], "step_changed")
        self.assertEqual(report["matched_steps"], 0)
        self.assertEqual(report["boundary_step"], 1)
        self.assertEqual(report["source_withheld_steps"], 2)
        self.assertEqual(report["target_withheld_steps"], 2)
        self.assertEqual(report["source_roles"], ["sender"])
        self.assertEqual(report["target_roles"], ["sender"])
        self.assertEqual(
            report["roles"],
            [{"role": "sender", "turn": 1, "status": "action_changed"}],
        )

    def test_different_schedules_stop_with_order_changed_without_cross_role_alignment(
        self,
    ) -> None:
        v_shared: dict[str, object] = {"shared": True}
        src_eps = [
            _episode(_SOURCE, "p0", [_bound(1, v_shared, "a"), _bound(2, v_shared, "b")]),
            _episode(_SOURCE, "p1", [_bound(1, v_shared, "c")]),
        ]
        dst_eps = [
            _episode(_TARGET, "p0", [_bound(1, v_shared, "a"), _bound(2, v_shared, "b")]),
            _episode(_TARGET, "p1", [_bound(1, v_shared, "c")]),
        ]
        # Step 1 matches (p0); step 2 schedules p0 in source vs p1 in target.
        report = self._run(
            src_eps,
            [["p0"], ["p0"], ["p1"]],
            dst_eps,
            [["p0"], ["p1"], ["p0"]],
        )
        self.assertEqual(report["status"], "order_changed")
        self.assertEqual(report["matched_steps"], 1)
        self.assertEqual(report["boundary_step"], 2)
        self.assertEqual(report["source_withheld_steps"], 1)
        self.assertEqual(report["target_withheld_steps"], 1)
        self.assertEqual(report["source_roles"], ["p0"])
        self.assertEqual(report["target_roles"], ["p1"])
        self.assertEqual(report["roles"], [])

    def test_known_null_action_differs_from_missing_action_and_strict_action_types(
        self,
    ) -> None:
        view: dict[str, object] = {"turn": 1}

        # Both known null -> matched.
        matched = self._run(
            [_episode(_SOURCE, "p0", [_bound(1, view, None)])],
            [["p0"]],
            [_episode(_TARGET, "p0", [_bound(1, view, None)])],
            [["p0"]],
        )
        self.assertEqual(matched["status"], "matched")
        self.assertEqual(matched["matched_steps"], 1)

        # Known null vs missing observation -> missing_evidence (missing_action), not equal.
        missing = self._run(
            [_episode(_SOURCE, "p0", [_bound(1, view, None), _bound(2, {"turn": 2}, 1)])],
            [["p0"], ["p0"]],
            [
                _episode(
                    _TARGET,
                    "p0",
                    [_raw_frame(1, view, observed=None), _bound(2, {"turn": 2}, 99)],
                )
            ],
            [["p0"], ["p0"]],
        )
        self.assertEqual(missing["status"], "missing_evidence")
        self.assertEqual(missing["matched_steps"], 0)
        self.assertEqual(missing["boundary_step"], 1)
        self.assertEqual(missing["source_withheld_steps"], 1)
        self.assertEqual(missing["target_withheld_steps"], 1)
        self.assertEqual(
            missing["roles"],
            [{"role": "p0", "turn": 1, "status": "missing_action"}],
        )

        # Known null vs 0 and int 1 vs str "1" -> step_changed (action_changed).
        for left_action, right_action in ((None, 0), (1, "1"), (None, "")):
            with self.subTest(left_action=left_action, right_action=right_action):
                changed = self._run(
                    [_episode(_SOURCE, "p0", [_bound(1, view, left_action)])],
                    [["p0"]],
                    [_episode(_TARGET, "p0", [_bound(1, view, right_action)])],
                    [["p0"]],
                )
                self.assertEqual(changed["status"], "step_changed")
                self.assertEqual(
                    changed["roles"],
                    [{"role": "p0", "turn": 1, "status": "action_changed"}],
                )

    def test_step_precedence_prefers_concrete_change_over_unbound_or_missing_in_same_group(
        self,
    ) -> None:
        same_view: dict[str, object] = {"k": 1}
        diff_view: dict[str, object] = {"k": 2}
        # Within one simultaneous group:
        #   a: unbound (reconstructed) with differing view/action -> unbound
        #   b: bound with differing view AND missing action -> input_changed (view before obs)
        #   c: bound equal view with missing action -> missing_action
        #   d: bound equal view and equal action -> equal
        # Top-level must be step_changed because b has a concrete known difference.
        src_eps = [
            _episode(
                _SOURCE, "a", [_bound(1, same_view, "x"), _bound(2, same_view, "later")]
            ),
            _episode(
                _SOURCE, "b", [_bound(1, same_view, "x"), _bound(2, same_view, "later")]
            ),
            _episode(
                _SOURCE, "c", [_bound(1, same_view, "x"), _bound(2, same_view, "later")]
            ),
            _episode(
                _SOURCE, "d", [_bound(1, same_view, "x"), _bound(2, same_view, "later")]
            ),
        ]
        dst_eps = [
            _episode(
                _TARGET,
                "a",
                [
                    _raw_frame(1, diff_view, basis="reconstructed", observed={"action": "y"}),
                    _bound(2, diff_view, "later-2"),
                ],
            ),
            _episode(
                _TARGET,
                "b",
                [
                    _raw_frame(1, diff_view, basis="bound", observed=None),
                    _bound(2, diff_view, "z"),
                ],
            ),
            _episode(
                _TARGET,
                "c",
                [
                    _raw_frame(1, same_view, basis="bound", observed=None),
                    _bound(2, diff_view, "z"),
                ],
            ),
            _episode(_TARGET, "d", [_bound(1, same_view, "x"), _bound(2, diff_view, "z")]),
        ]
        steps = [["d", "c", "b", "a"], ["a", "b", "c", "d"]]

        report = self._run(src_eps, steps, dst_eps, steps, input_paths=4)
        self.assertEqual(report["status"], "step_changed")
        self.assertEqual(report["matched_steps"], 0)
        self.assertEqual(report["boundary_step"], 1)
        self.assertEqual(report["source_withheld_steps"], 1)
        self.assertEqual(report["target_withheld_steps"], 1)
        self.assertEqual(
            report["roles"],
            [
                {"role": "a", "turn": 1, "status": "unbound"},
                {
                    "role": "b",
                    "turn": 1,
                    "status": "input_changed",
                    "input_diff": {
                        "changes": 1,
                        "shown": 1,
                        "truncated": False,
                        "paths": [{"path": "/k", "change": "value_changed"}],
                    },
                },
                {"role": "c", "turn": 1, "status": "missing_action"},
                {"role": "d", "turn": 1, "status": "equal"},
            ],
        )

        # Absent evidence alone (unbound + equal) -> missing_evidence, later changes withheld.
        unbound_only = self._run(
            [
                _episode(
                    _SOURCE, "a", [_bound(1, same_view, "x"), _bound(2, same_view, "s1")]
                ),
                _episode(
                    _SOURCE, "b", [_bound(1, same_view, "x"), _bound(2, same_view, "s2")]
                ),
            ],
            [["a", "b"], ["a", "b"]],
            [
                _episode(
                    _TARGET,
                    "a",
                    [
                        _raw_frame(
                            1, diff_view, basis="reconstructed", observed={"action": "y"}
                        ),
                        _bound(2, diff_view, "t1"),
                    ],
                ),
                _episode(
                    _TARGET, "b", [_bound(1, same_view, "x"), _bound(2, diff_view, "t2")]
                ),
            ],
            [["a", "b"], ["a", "b"]],
            input_paths=4,
        )
        self.assertEqual(unbound_only["status"], "missing_evidence")
        self.assertEqual(unbound_only["boundary_step"], 1)
        self.assertEqual(unbound_only["source_withheld_steps"], 1)
        self.assertEqual(unbound_only["target_withheld_steps"], 1)
        self.assertEqual(
            unbound_only["roles"],
            [
                {"role": "a", "turn": 1, "status": "unbound"},
                {"role": "b", "turn": 1, "status": "equal"},
            ],
        )

    def test_open_and_uneven_observed_lengths_respect_closure_semantics(self) -> None:
        v1: dict[str, object] = {"step": 1}
        v2: dict[str, object] = {"step": 2}
        v3: dict[str, object] = {"step": 3}

        def trial(
            trial_id: str, turns: int, closed: bool
        ) -> tuple[list[Episode], list[list[str]]]:
            views = [v1, v2, v3][:turns]
            frames = [_bound(idx, view, f"a{idx}") for idx, view in enumerate(views, 1)]
            return [_episode(trial_id, "p0", frames, closed=closed)], [["p0"]] * turns

        # Both closed, uneven length -> length_changed, boundary at first extra step.
        s_eps, s_steps = trial(_SOURCE, 1, True)
        t_eps, t_steps = trial(_TARGET, 3, True)
        closed_uneven = self._run(s_eps, s_steps, t_eps, t_steps)
        self.assertEqual(closed_uneven["status"], "length_changed")
        self.assertEqual(closed_uneven["matched_steps"], 1)
        self.assertEqual(closed_uneven["boundary_step"], 2)
        self.assertEqual(closed_uneven["source_steps"], 1)
        self.assertEqual(closed_uneven["target_steps"], 3)
        self.assertEqual(closed_uneven["source_withheld_steps"], 0)
        self.assertEqual(closed_uneven["target_withheld_steps"], 1)
        self.assertEqual(closed_uneven["source_roles"], [])
        self.assertEqual(closed_uneven["target_roles"], ["p0"])
        self.assertEqual(closed_uneven["roles"], [])

        # Reverse direction (source longer, both closed).
        rev_closed = self._run(
            *trial(_SOURCE, 3, True),
            *trial(_TARGET, 1, True),
        )
        self.assertEqual(rev_closed["status"], "length_changed")
        self.assertEqual(rev_closed["matched_steps"], 1)
        self.assertEqual(rev_closed["boundary_step"], 2)
        self.assertEqual(rev_closed["source_withheld_steps"], 1)
        self.assertEqual(rev_closed["target_withheld_steps"], 0)
        self.assertEqual(rev_closed["source_roles"], ["p0"])
        self.assertEqual(rev_closed["target_roles"], [])

        # Open shorter (source open 1, target closed 3) -> matched_open, 2 steps withheld.
        open_shorter = self._run(*trial(_SOURCE, 1, False), *trial(_TARGET, 3, True))
        self.assertEqual(open_shorter["status"], "matched_open")
        self.assertEqual(open_shorter["matched_steps"], 1)
        self.assertIsNone(open_shorter["boundary_step"])
        self.assertEqual(open_shorter["source_withheld_steps"], 0)
        self.assertEqual(open_shorter["target_withheld_steps"], 2)
        self.assertEqual(open_shorter["source_roles"], [])
        self.assertEqual(open_shorter["target_roles"], [])
        self.assertEqual(open_shorter["roles"], [])

        # Closed shorter, open longer -> matched_open (both histories are not closed).
        open_longer = self._run(*trial(_SOURCE, 1, True), *trial(_TARGET, 3, False))
        self.assertEqual(open_longer["status"], "matched_open")
        self.assertEqual(open_longer["matched_steps"], 1)
        self.assertIsNone(open_longer["boundary_step"])
        self.assertEqual(open_longer["source_withheld_steps"], 0)
        self.assertEqual(open_longer["target_withheld_steps"], 2)

        # Equal length, one episode open in multi-role team -> matched_open.
        s_multi = [
            _episode(_SOURCE, "p0", [_bound(1, v1, "a1")], closed=True),
            _episode(_SOURCE, "p1", [_bound(1, v1, "b1")], closed=False),
        ]
        t_multi = [
            _episode(_TARGET, "p0", [_bound(1, v1, "a1")], closed=True),
            _episode(_TARGET, "p1", [_bound(1, v1, "b1")], closed=True),
        ]
        equal_open = self._run(s_multi, [["p0", "p1"]], t_multi, [["p0", "p1"]])
        self.assertEqual(equal_open["status"], "matched_open")
        self.assertEqual(equal_open["matched_steps"], 1)
        self.assertIsNone(equal_open["boundary_step"])
        self.assertEqual(equal_open["source_withheld_steps"], 0)
        self.assertEqual(equal_open["target_withheld_steps"], 0)

    def test_rejects_mismatched_case_same_trial_different_roles_and_bad_input_paths(
        self,
    ) -> None:
        v1: dict[str, object] = {"step": 1}
        src_a = _episode(_SOURCE, "p0", [_bound(1, v1, "a")], case_sha256=_CASE_A)
        dst_a = _episode(_TARGET, "p0", [_bound(1, v1, "a")], case_sha256=_CASE_A)
        dst_b = _episode(_TARGET, "p0", [_bound(1, v1, "a")], case_sha256=_CASE_B)
        dst_p1 = _episode(_TARGET, "p1", [_bound(1, v1, "a")], case_sha256=_CASE_A)

        with self.assertRaisesRegex(wire.TapeError, "case_sha256"):
            self._run([src_a], [["p0"]], [dst_b], [["p0"]])

        with self.assertRaisesRegex(wire.TapeError, "native_trial_id"):
            self._run([src_a], [["p0"]], [src_a], [["p0"]])

        with self.assertRaisesRegex(wire.TapeError, "role set"):
            self._run([src_a], [["p0"]], [dst_p1], [["p1"]])

        missing = self.root / "missing.jsonl"
        for bad_limit in (0, -1, 129, True, False, 1.5, "1"):
            with self.subTest(input_paths=bad_limit):
                with self.assertRaisesRegex(wire.TapeError, "input_paths"):
                    team_align(
                        missing,
                        missing,
                        source_order=missing,
                        target_order=missing,
                        input_paths=cast(Any, bad_limit),
                    )
        # Valid input_paths range boundaries work without error.
        ok = self._run([src_a], [["p0"]], [dst_a], [["p0"]], input_paths=128)
        self.assertEqual(ok["status"], "matched")

    def test_role_turn_tracks_local_consumption_not_global_world_step(self) -> None:
        # Step 1: p0 (turn 1). Step 2: p1 (turn 1). Step 3: p0 + p1 (local turns 2, 2).
        # At step 3, p1 diverges -> boundary_step is 3, local turns are 2.
        v: dict[str, object] = {"ok": True}
        src_eps = [
            _episode(_SOURCE, "p0", [_bound(1, v, "a"), _bound(2, v, "b")]),
            _episode(_SOURCE, "p1", [_bound(1, v, "c"), _bound(2, v, "d")]),
        ]
        dst_eps = [
            _episode(_TARGET, "p0", [_bound(1, v, "a"), _bound(2, v, "b")]),
            _episode(_TARGET, "p1", [_bound(1, v, "c"), _bound(2, v, "changed")]),
        ]
        steps = [["p0"], ["p1"], ["p0", "p1"]]
        report = self._run(src_eps, steps, dst_eps, steps)
        self.assertEqual(report["status"], "step_changed")
        self.assertEqual(report["matched_steps"], 2)
        self.assertEqual(report["boundary_step"], 3)
        self.assertEqual(report["source_withheld_steps"], 0)
        self.assertEqual(report["target_withheld_steps"], 0)
        self.assertEqual(
            report["roles"],
            [
                {"role": "p0", "turn": 2, "status": "equal"},
                {"role": "p1", "turn": 2, "status": "action_changed"},
            ],
        )
