from __future__ import annotations

import itertools
import random
import unittest

from seatlab.panel_cover import minimum_panel


def brute(case_witnesses):
    targets = set().union(*case_witnesses.values()) if case_witnesses else set()
    labels = sorted(case_witnesses)
    for size in range(len(labels) + 1):
        for combo in itertools.combinations(labels, size):  # lexicographic order
            if set().union(*(case_witnesses[c] for c in combo)) >= targets:
                return combo
    raise AssertionError("unreachable")


def fs(*items):
    return frozenset(items)


class MinimumPanelTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(minimum_panel({}), ())
        self.assertEqual(minimum_panel({"a": fs(), "b": fs()}), ())

    def test_greedy_counterexample(self):
        cases = {
            "g": fs("1", "2", "4", "5"),
            "b1": fs("1", "2", "3"),
            "b2": fs("4", "5", "6"),
        }
        self.assertEqual(minimum_panel(cases), ("b1", "b2"))

    def test_triangle_tie(self):
        cases = {"bc": fs("y", "z"), "ac": fs("x", "z"), "ab": fs("x", "y")}
        self.assertEqual(minimum_panel(cases), ("ab", "ac"))

    def test_input_order_and_duplicates(self):
        items = [
            ("c", fs("x", "y")),
            ("a", fs("x")),
            ("b", fs("y")),
            ("d", fs("x", "y")),
            ("e", fs()),
        ]
        for perm in itertools.permutations(items):
            self.assertEqual(minimum_panel(dict(perm)), ("c",))
        self.assertEqual(minimum_panel({"z": fs("x"), "m": fs("x"), "e": fs()}), ("m",))

    def test_random_against_exhaustive(self):
        rng = random.Random(7)
        for _ in range(300):
            n_targets = rng.randint(0, 6)
            targets = [f"t{i}" for i in range(n_targets)]
            cases = {
                f"c{i}": frozenset(t for t in targets if rng.random() < 0.4)
                for i in range(rng.randint(0, 9))
            }
            self.assertEqual(minimum_panel(cases), brute(cases), cases)

    def test_limits_16_targets_256_cases(self):
        targets = [f"t{i:02d}" for i in range(16)]
        cases = {f"c{i:03d}": fs(targets[i % 16]) for i in range(256)}
        self.assertEqual(minimum_panel(cases), tuple(f"c{i:03d}" for i in range(16)))
        rng = random.Random(11)
        cases = {
            f"c{i:03d}": frozenset(t for t in targets if rng.random() < 0.25)
            for i in range(256)
        }
        picked = minimum_panel(cases)
        self.assertEqual(
            set().union(*(cases[c] for c in picked)), set().union(*cases.values())
        )
        self.assertEqual(list(picked), sorted(picked))


if __name__ == "__main__":
    unittest.main()
