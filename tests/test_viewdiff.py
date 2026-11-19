import json
import unittest
from typing import cast

from seatlab import tape as wire
from seatlab.viewdiff import diff_input


class ViewDiffTests(unittest.TestCase):
    def test_object_keys_sort_by_codepoint_and_escape_pointer_tokens(self) -> None:
        keys = ("\U00010000", "\ue000", "é", "~/", "z", "a/b", "")
        left: dict[str, object] = {key: {"/~0": 0} for key in keys}
        right: dict[str, object] = {key: {"/~0": 1} for key in reversed(keys)}

        result = diff_input(left, right, limit=128)

        self.assertEqual(
            result["paths"],
            [
                {"path": f"/{key}/~1~00", "change": "value_changed"}
                for key in ("", "a~1b", "z", "~0~1", "é", "\ue000", "\U00010000")
            ],
        )

    def test_arrays_sort_numerically_and_stop_at_missing_positions(self) -> None:
        left: dict[str, object] = {"items": [0] * 11}
        right: dict[str, object] = {"items": [1, 0, 1, *([0] * 7), 1, {"nested": 3}]}

        expected = [
            {"path": f"/items/{index}", "change": "value_changed"} for index in (0, 2, 10)
        ]
        self.assertEqual(
            diff_input(left, right, limit=128)["paths"],
            [*expected, {"path": "/items/11", "change": "added"}],
        )
        self.assertEqual(
            diff_input(right, left, limit=128)["paths"],
            [*expected, {"path": "/items/11", "change": "removed"}],
        )

    def test_null_is_distinct_from_a_missing_field(self) -> None:
        self.assertEqual(
            diff_input({"a": None, "same": None}, {"b": None, "same": None}, limit=2),
            {
                "changes": 2,
                "shown": 2,
                "truncated": False,
                "paths": [
                    {"path": "/a", "change": "removed"},
                    {"path": "/b", "change": "added"},
                ],
            },
        )

    def test_equal_python_compounds_can_contain_different_json_types(self) -> None:
        left: dict[str, object] = {"items": [{"bool": True, "float": 1.0}, False]}
        right: dict[str, object] = {"items": [{"bool": 1, "float": 1}, 0]}
        self.assertEqual(left, right)
        self.assertEqual(
            diff_input(left, right, limit=128)["paths"],
            [
                {"path": "/items/0/bool", "change": "type_changed"},
                {"path": "/items/0/float", "change": "type_changed"},
                {"path": "/items/1", "change": "type_changed"},
            ],
        )

    def test_scalar_values_follow_canonical_encoding(self) -> None:
        left = cast(dict[str, object], wire.decode('{"same":1e0,"zero":-0.0,"flag":true}'))
        right = cast(dict[str, object], wire.decode('{"same":1.0,"zero":0.0,"flag":false}'))

        self.assertEqual(
            diff_input(left, right, limit=128)["paths"],
            [
                {"path": "/flag", "change": "value_changed"},
                {"path": "/zero", "change": "value_changed"},
            ],
        )

    def test_output_omits_scalar_values_and_descendants_of_replaced_fields(self) -> None:
        left: dict[str, object] = {
            "gone": {"private_key": "old-secret"},
            "swap": {"before": 9918},
            "v": 3081,
            "text": "left-secret",
        }
        right: dict[str, object] = {
            "new": {"hidden_key": "new-secret"},
            "swap": [7628],
            "v": "now-secret",
            "text": "right-secret",
        }

        result = diff_input(left, right, limit=128)

        self.assertEqual(
            result,
            {
                "changes": 5,
                "shown": 5,
                "truncated": False,
                "paths": [
                    {"path": "/gone", "change": "removed"},
                    {"path": "/new", "change": "added"},
                    {"path": "/swap", "change": "type_changed"},
                    {"path": "/text", "change": "value_changed"},
                    {"path": "/v", "change": "type_changed"},
                ],
            },
        )
        serialized = json.dumps(result)
        private_markers = (
            "private_key",
            "hidden_key",
            "before",
            "secret",
            "9918",
            "7628",
            "3081",
        )
        for private in private_markers:
            with self.subTest(private=private):
                self.assertNotIn(private, serialized)

    def test_limit_caps_records_but_not_count(self) -> None:
        left: dict[str, object] = {"later": 1, "items": [0] * 130}
        right: dict[str, object] = {"later": 2, "items": [1] * 130}

        for limit in (1, 128):
            with self.subTest(limit=limit):
                self.assertEqual(
                    diff_input(left, right, limit=limit),
                    {
                        "changes": 131,
                        "shown": limit,
                        "truncated": True,
                        "paths": [
                            {"path": f"/items/{index}", "change": "value_changed"}
                            for index in range(limit)
                        ],
                    },
                )

    def test_limit_rejects_non_integer_and_out_of_range_values(self) -> None:
        for limit in (False, True, 1.0, "1", None, 0, -1, 129):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                diff_input({}, {}, limit=cast(int, limit))

    def test_equal_objects_have_no_changes(self) -> None:
        self.assertEqual(
            diff_input({"a": [{}]}, {"a": [{}]}, limit=1),
            {"changes": 0, "shown": 0, "truncated": False, "paths": []},
        )
