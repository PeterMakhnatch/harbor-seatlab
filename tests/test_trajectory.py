"""Behavioral tests for the frozen ``role_trajectory`` consumer contract."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from seatlab import author, tape as wire
from seatlab.cli import main
from seatlab.episodes import Episode, EpisodeFrame
from seatlab.trajectory import role_trajectory
from seatlab.world_order import WORLD_ORDER_SCHEMA

_SRC = "dd19e118-5159-4831-bc8e-365d571e6b6b"
_DST = "be656986-a740-4e46-999d-21410909878c"
_CASE_A = wire.digest({"world": "trajectory", "case": "alpha"})
_CASE_B = wire.digest({"world": "trajectory", "case": "beta"})
_SCHEMA = "seatlab.role-trajectory/v1"
_TOP_KEYS = {
    "schema",
    "source_native_trial_id",
    "target_native_trial_id",
    "case_sha256",
    "role",
    "fields",
    "status",
    "schedule",
    "role_decisions",
    "raw_view",
    "selected_view",
    "accepted_action",
}

NO = object()  # sentinel: the frame carries no observed action at all


def f(view: dict[str, object], action: Any = "go", basis: str = "bound") -> tuple[Any, ...]:
    return (view, action, basis)


def _frames(specs: list[tuple[Any, ...]]) -> list[EpisodeFrame]:
    frames: list[EpisodeFrame] = []
    for turn, (view, action, basis) in enumerate(specs, 1):
        if basis == "bound" and action is not NO:
            frames.append(
                author.accepted_frame(
                    turn=turn, view=view, acknowledged_sha256=wire.digest(view), action=action
                )
            )
        else:
            frames.append(
                {
                    "turn": turn,
                    "basis": basis,  # type: ignore[typeddict-item]
                    "view": view,
                    "view_sha256": wire.digest(view),
                    "observed": None if action is NO else {"action": action},
                }
            )
    return frames


def _dim(
    equal: int = 0,
    different: int = 0,
    unknown: int = 0,
    first_different_step: int | None = None,
    first_unknown_step: int | None = None,
) -> dict[str, object]:
    return {
        "equal": equal,
        "different": different,
        "unknown": unknown,
        "first_different_step": first_different_step,
        "first_unknown_step": first_unknown_step,
    }


class RoleTrajectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    # -- fixtures ---------------------------------------------------------------------------

    def _episodes(
        self,
        trial: str,
        case: str,
        roles: dict[str, list[tuple[Any, ...]]],
        open_roles: tuple[str, ...],
    ) -> list[Episode]:
        return [
            author.episode_from_frames(
                id=f"{trial}:{role}",
                case_sha256=case,
                role=role,
                native_trial_id=trial,
                closed=role not in open_roles,
                frames=_frames(specs),
            )
            for role, specs in roles.items()
        ]

    def _pair(
        self,
        src_roles: dict[str, list[tuple[Any, ...]]],
        src_steps: list[list[str]],
        dst_roles: dict[str, list[tuple[Any, ...]]],
        dst_steps: list[list[str]],
        *,
        src_open: tuple[str, ...] = (),
        dst_open: tuple[str, ...] = (),
        src_trial: str = _SRC,
        dst_trial: str = _DST,
        dst_case: str = _CASE_A,
    ) -> dict[str, Path]:
        folder = Path(tempfile.mkdtemp(dir=self.root))
        paths = {
            "source": folder / "s.jsonl",
            "target": folder / "t.jsonl",
            "source_order": folder / "s-order.json",
            "target_order": folder / "t-order.json",
        }
        for side, trial, case, roles, steps, open_roles in (
            ("source", src_trial, _CASE_A, src_roles, src_steps, src_open),
            ("target", dst_trial, dst_case, dst_roles, dst_steps, dst_open),
        ):
            episodes = self._episodes(trial, case, roles, open_roles)
            wire.write_lines(paths[side], episodes)
            paths[f"{side}_order"].write_bytes(
                wire.canonical(
                    {
                        "schema": WORLD_ORDER_SCHEMA,
                        "case_sha256": case,
                        "native_trial_id": trial,
                        "episodes": {e["role"]: e["episode_sha256"] for e in episodes},
                        "steps": steps,
                    }
                )
            )
        return paths

    def _run(self, paths: dict[str, Path], role: str, fields: Any = None) -> dict[str, Any]:
        return role_trajectory(
            paths["source"],
            paths["target"],
            source_order=paths["source_order"],
            target_order=paths["target_order"],
            role=role,
            fields=fields,
        )  # type: ignore[return-value]

    def _single(
        self, turns: list[tuple[tuple[Any, ...], tuple[Any, ...]]], fields: Any = None
    ):
        steps = [["a"] for _ in turns]
        paths = self._pair(
            {"a": [s for s, _ in turns]}, steps, {"a": [t for _, t in turns]}, steps
        )
        return self._run(paths, "a", fields)

    # -- tests ------------------------------------------------------------------------------

    def test_exact_complete_report_listing_order_irrelevant_and_features_do_not_gate(
        self,
    ) -> None:
        b = [f({"b": i}) for i in range(3)]
        src = {
            "a": [f({"x": 1, "y": 1}, "go"), f({"x": 2, "y": 9}, "stay")],
            "b": b,
        }
        dst = {
            "a": [f({"x": 1, "y": 1}, "go"), f({"x": 3, "y": 9}, "turn")],
            "b": b,
        }
        paths = self._pair(
            src,
            [["b", "a"], ["b"], ["a", "b"]],
            dst,
            [["a", "b"], ["b"], ["b", "a"]],
        )
        result = self._run(paths, "a", ["/y"])
        self.assertEqual(
            result,
            {
                "schema": _SCHEMA,
                "source_native_trial_id": _SRC,
                "target_native_trial_id": _DST,
                "case_sha256": _CASE_A,
                "role": "a",
                "fields": ["/y"],
                "status": "complete",
                "schedule": {
                    "matched_steps": 3,
                    "boundary_step": None,
                    "source_steps": 3,
                    "target_steps": 3,
                },
                "role_decisions": 2,
                "raw_view": _dim(1, 1, 0, 3, None),
                "selected_view": _dim(2, 0, 0, None, None),
                "accepted_action": _dim(1, 1, 0, 3, None),
            },
        )

    def test_fields_are_sorted_and_no_fields_selects_whole_view(self) -> None:
        turns = [(f({"x": 1, "y": 1}), f({"x": 1, "y": 2})), (f({"x": 1}), f({"x": 1}))]
        whole = self._single(turns)
        self.assertIsNone(whole["fields"])
        self.assertEqual(whole["selected_view"], whole["raw_view"])
        self.assertEqual(whole["raw_view"], _dim(1, 1, 0, 1, None))
        picked = self._single(turns, ["/y", "/x"])
        self.assertEqual(picked["fields"], ["/x", "/y"])
        # /y is absent on turn 2 for both sides, so the entire selection is unknown there.
        self.assertEqual(picked["selected_view"], _dim(0, 1, 1, 1, 2))
        self.assertEqual(picked["raw_view"], _dim(1, 1, 0, 1, None))

    def test_projected_presence_null_and_strict_types(self) -> None:
        turns = [
            (f({}), f({"y": 1})),  # one side absent -> unknown
            (f({}), f({})),  # both absent -> unknown, not equal
            (f({"y": None}), f({"y": None})),  # explicit null is present and equal
            (f({"y": None}), f({})),  # null vs absent -> unknown
            (f({"y": 1}), f({"y": "1"})),  # strict: int vs str
            (f({"y": 1}), f({"y": True})),  # strict: int vs bool
        ]
        result = self._single(turns, ["/y"])
        self.assertEqual(result["role_decisions"], 6)
        self.assertEqual(result["selected_view"], _dim(1, 2, 3, 5, 1))
        self.assertEqual(result["raw_view"], _dim(2, 4, 0, 1, None))
        self.assertEqual(result["accepted_action"], _dim(6))

    def test_one_absent_pointer_makes_whole_selection_unknown_even_if_other_differs(
        self,
    ) -> None:
        turns = [
            (f({"a": 1}), f({"a": 2, "b": 1})),
            (f({"a": 1, "b": 2}), f({"a": 1, "b": 2, "c": 5})),
            (f({"a": 1, "b": 2}), f({"a": 1, "b": 3})),
        ]
        result = self._single(turns, ["/a", "/b"])
        self.assertEqual(result["selected_view"], _dim(1, 1, 1, 3, 1))

    def test_rfc6901_escapes_and_array_indexes(self) -> None:
        view: dict[str, object] = {"k/x": 1, "k~x": 2, "arr": [0, 1]}
        cases = {
            "/k~1x": "equal",
            "/k~0x": "equal",
            "/arr/1": "equal",
            "/arr/01": "unknown",
            "/arr/2": "unknown",
            "/k/x": "unknown",
        }
        for pointer, expected in cases.items():
            with self.subTest(pointer=pointer):
                result = self._single([(f(view), f(view))], [pointer])
                self.assertEqual(result["selected_view"][expected], 1)
                self.assertEqual(
                    result["selected_view"]["unknown"], int(expected == "unknown")
                )

    def test_unbound_missing_action_null_and_strict_action_matrix(self) -> None:
        turns = [
            (f({"v": 1}), f({"v": 1})),  # 1 equal / equal
            (f({"v": 1}, "go", "reconstructed"), f({"v": 1})),  # 2 unbound
            (f({"v": 1}, NO), f({"v": 2})),  # 3 view differs, source action missing
            (f({"v": 1}, None), f({"v": 1}, None)),  # 4 null == null
            (f({"v": 1}, None), f({"v": 1}, "go")),  # 5 null vs action
            (f({"v": 1}, NO), f({"v": 1}, None)),  # 6 no evidence vs null
            (f({"v": 1}, "1"), f({"v": 2}, 1)),  # 7 view differs and "1" != 1
            (f({"v": 1}, NO), f({"v": 1}, NO)),  # 8 no evidence on both
        ]
        result = self._single(turns)
        self.assertEqual(result["role_decisions"], 8)
        self.assertEqual(result["raw_view"], _dim(5, 2, 1, 3, 2))
        self.assertEqual(result["accepted_action"], _dim(2, 2, 4, 5, 2))
        selected = result["selected_view"]
        self.assertEqual(selected["equal"] + selected["different"] + selected["unknown"], 8)

    def test_alternating_order_change_stops_and_does_not_realign_by_turn(self) -> None:
        a = [f({"v": "same"}), f({"v": "same"})]
        src = {"a": a, "b": [f({"v": 1}), f({"v": "S"})]}
        dst = {"a": a, "b": [f({"v": 1}), f({"v": "D"})]}
        src_steps = [["a"], ["b"], ["a"], ["b"]]
        dst_steps = [["a"], ["b"], ["b"], ["a"]]
        for src_open in ((), ("b",)):
            paths = self._pair(src, src_steps, dst, dst_steps, src_open=src_open)
            expected_schedule = {
                "matched_steps": 2,
                "boundary_step": 3,
                "source_steps": 4,
                "target_steps": 4,
            }
            for role in ("a", "b"):
                with self.subTest(role=role, src_open=src_open):
                    result = self._run(paths, role)
                    self.assertEqual(result["status"], "order_changed")
                    self.assertEqual(result["schedule"], expected_schedule)
                    self.assertEqual(result["role_decisions"], 1)
                    self.assertEqual(result["raw_view"], _dim(1))
                    self.assertEqual(result["accepted_action"], _dim(1))

    def test_simultaneous_group_change_withholds_whole_group(self) -> None:
        src = {"a": [f({"v": 1})], "b": [f({"v": 1})]}
        dst = {"a": [f({"v": 2})], "b": [f({"v": 2})]}
        paths = self._pair(src, [["a", "b"]], dst, [["a"], ["b"]])
        result = self._run(paths, "a")
        self.assertEqual(result["status"], "order_changed")
        self.assertEqual(
            result["schedule"],
            {"matched_steps": 0, "boundary_step": 1, "source_steps": 1, "target_steps": 2},
        )
        self.assertEqual(result["role_decisions"], 0)
        for key in ("raw_view", "selected_view", "accepted_action"):
            self.assertEqual(result[key], _dim())

    def test_prefix_before_changed_group_is_kept(self) -> None:
        src = {"a": [f({"v": 1}), f({"v": 5})], "b": [f({"v": 1})]}
        dst = {"a": [f({"v": 1}), f({"v": 6})], "b": [f({"v": 1})]}
        paths = self._pair(src, [["a"], ["a", "b"]], dst, [["a"], ["a"], ["b"]])
        result = self._run(paths, "a")
        self.assertEqual(result["status"], "order_changed")
        self.assertEqual(result["schedule"]["matched_steps"], 1)
        self.assertEqual(result["schedule"]["boundary_step"], 2)
        self.assertEqual(result["raw_view"], _dim(1))

    def test_walk_continues_past_other_roles_changes_unlike_team_align(self) -> None:
        src = {
            "a": [f({"v": 1}), f({"v": 2}, "x")],
            "b": [f({"v": 1}, "left")],
        }
        dst = {
            "a": [f({"v": 1}), f({"v": 3}, "x")],
            "b": [f({"v": 1}, "right")],
        }
        steps = [["a"], ["b"], ["a"]]
        paths = self._pair(src, steps, dst, steps)
        a = self._run(paths, "a")
        self.assertEqual(a["status"], "complete")
        self.assertEqual(a["schedule"]["matched_steps"], 3)
        self.assertEqual(a["role_decisions"], 2)
        self.assertEqual(a["raw_view"], _dim(1, 1, 0, 3, None))
        self.assertEqual(a["accepted_action"], _dim(2))
        b = self._run(paths, "b")
        self.assertEqual(b["role_decisions"], 1)
        self.assertEqual(b["accepted_action"], _dim(0, 1, 0, 2, None))
        self.assertEqual(b["raw_view"], _dim(1))

    def test_length_changed_when_closed_and_open_when_not(self) -> None:
        base_a = [f({"v": 1}), f({"v": 2})]
        shorter = {"a": base_a[:1], "b": [f({"v": 0})]}
        longer = {"a": base_a, "b": [f({"v": 0})]}
        short_steps = [["a"], ["b"]]
        long_steps = [["a"], ["b"], ["a"]]
        paths = self._pair(shorter, short_steps, longer, long_steps)
        result = self._run(paths, "a")
        self.assertEqual(result["status"], "length_changed")
        self.assertEqual(
            result["schedule"],
            {"matched_steps": 2, "boundary_step": 3, "source_steps": 2, "target_steps": 3},
        )
        self.assertEqual(result["role_decisions"], 1)
        reverse = self._run(self._pair(longer, long_steps, shorter, short_steps), "a")
        self.assertEqual(reverse["status"], "length_changed")
        self.assertEqual(
            reverse["schedule"],
            {"matched_steps": 2, "boundary_step": 3, "source_steps": 3, "target_steps": 2},
        )
        for kwargs in ({"src_open": ("b",)}, {"dst_open": ("b",)}):
            with self.subTest(kwargs=kwargs):
                opened = self._run(
                    self._pair(shorter, short_steps, longer, long_steps, **kwargs), "a"
                )
                self.assertEqual(opened["status"], "open")
                self.assertEqual(opened["schedule"]["matched_steps"], 2)
                self.assertEqual(opened["schedule"]["boundary_step"], 3)
                self.assertEqual(opened["role_decisions"], 1)

    def test_open_equal_length_keeps_measured_evidence(self) -> None:
        src = {"a": [f({"v": 1})], "b": [f({"v": 1})]}
        dst = {"a": [f({"v": 2})], "b": [f({"v": 1})]}
        steps = [["a"], ["b"]]
        closed = self._run(self._pair(src, steps, dst, steps), "a")
        opened = self._run(self._pair(src, steps, dst, steps, dst_open=("a",)), "a")
        self.assertEqual(closed["status"], "complete")
        self.assertEqual(opened["status"], "open")
        self.assertEqual(
            opened["schedule"],
            {"matched_steps": 2, "boundary_step": None, "source_steps": 2, "target_steps": 2},
        )
        for key in ("role_decisions", "raw_view", "selected_view", "accepted_action"):
            self.assertEqual(opened[key], closed[key])
        self.assertEqual(opened["raw_view"], _dim(0, 1, 0, 1, None))

    def test_output_contains_no_raw_values(self) -> None:
        secret_v, secret_a, secret_f = "VIEWSECRET_91", "ACTSECRET_17", "FIELDSECRET_33"
        turns = [
            (f({"s": secret_v, "p": secret_f}, secret_a), f({"s": "x", "p": "y"}, "other")),
        ]
        for fields in (None, ["/p"]):
            result = self._single(turns, fields)
            self.assertEqual(set(result), _TOP_KEYS)
            blob = wire.canonical(result)
            for secret in (secret_v, secret_a, secret_f, b"other", b'"y"'):
                needle = secret if isinstance(secret, bytes) else secret.encode()
                self.assertNotIn(needle, blob)

    def test_malformed_inputs_raise_value_error(self) -> None:
        roles = {"a": [f({"v": 1})], "b": [f({"v": 1})]}
        steps = [["a"], ["b"]]
        good = self._pair(roles, steps, roles, steps)
        self.assertEqual(self._run(good, "a")["status"], "complete")
        bad = {
            "different case": self._pair(roles, steps, roles, steps, dst_case=_CASE_B),
            "same trial": self._pair(roles, steps, roles, steps, dst_trial=_SRC),
            "role set differs": self._pair(
                roles, steps, {**roles, "c": [f({"v": 1})]}, [*steps, ["c"]]
            ),
        }
        for name, paths in bad.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self._run(paths, "a")
        with self.assertRaises(ValueError):
            self._run(good, "z")
        for fields in ([], ["x"], [""], ["/x", "/x"], "/x", [1], [None]):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    self._run(good, "a", fields)

    # -- CLI --------------------------------------------------------------------------------

    def _cli(self, paths: dict[str, Path], *extra: str, role: str | None = "a"):
        argv = [
            "role-trajectory",
            str(paths["source"]),
            str(paths["target"]),
            "--source-order",
            str(paths["source_order"]),
            "--target-order",
            str(paths["target_order"]),
        ]
        if role is not None:
            argv += ["--role", role]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main([*argv, *extra])
            except SystemExit as exit_:
                code = int(exit_.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_cli_emits_json_with_exit_zero_for_negative_and_open_results(self) -> None:
        src = {"a": [f({"x": 1, "y": 1}), f({"x": 1})], "b": [f({"v": 1})]}
        dst = {"a": [f({"x": 2, "y": 1}), f({"x": 1})], "b": [f({"v": 1})]}
        order_changed = self._pair(src, [["a"], ["b"], ["a"]], dst, [["a"], ["a"], ["b"]])
        code, out, err = self._cli(order_changed, "--field", "/y", "--field", "/x")
        self.assertEqual((code, err), (0, ""))
        document = json.loads(out)
        self.assertEqual(document, self._run(order_changed, "a", ["/y", "/x"]))
        self.assertEqual(document["fields"], ["/x", "/y"])
        self.assertEqual(document["status"], "order_changed")
        code, out, _ = self._cli(order_changed)
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["fields"])
        opened = self._pair(
            src, [["a"], ["b"], ["a"]], src, [["a"], ["b"], ["a"]], dst_open=("b",)
        )
        code, out, _ = self._cli(opened)
        self.assertEqual((code, json.loads(out)["status"]), (0, "open"))

    def test_cli_exits_two_for_malformed_inputs(self) -> None:
        roles = {"a": [f({"v": 1})]}
        good = self._pair(roles, [["a"]], roles, [["a"]])
        same_trial = self._pair(roles, [["a"]], roles, [["a"]], dst_trial=_SRC)
        missing = {**good, "target_order": self.root / "absent.json"}
        for name, paths, extra, role in (
            ("same trial", same_trial, (), "a"),
            ("missing order file", missing, (), "a"),
            ("unregistered role", good, (), "z"),
            ("root pointer", good, ("--field", ""), "a"),
            ("bad pointer", good, ("--field", "x"), "a"),
            ("duplicate pointer", good, ("--field", "/x", "--field", "/x"), "a"),
            ("missing role option", good, (), None),
        ):
            with self.subTest(name):
                code, out, err = self._cli(paths, *extra, role=role)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertNotEqual(err, "")


if __name__ == "__main__":
    unittest.main()
