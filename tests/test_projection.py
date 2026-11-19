import sys
import unittest

from seatlab import tape as wire
from seatlab.projection import (
    MAX_FIELDS,
    MAX_POINTER_COMPONENTS,
    MAX_POINTER_LENGTH,
    Projection,
)


class ProjectionTests(unittest.TestCase):
    def test_whole_view_and_empty_projection_modes(self) -> None:
        view: dict[str, object] = {"b": 2, "a": [True, None, "x"]}
        original = wire.canonical(view)

        whole = Projection()
        self.assertIsNone(whole.fields)
        self.assertEqual(Projection(None), whole)
        self.assertEqual(whole.encode(view), original)

        role_only = Projection([])
        self.assertEqual(role_only.fields, ())
        self.assertEqual(Projection(()), role_only)
        self.assertEqual(role_only.encode(view), b"[]")
        self.assertEqual(role_only.encode({}), b"[]")
        self.assertEqual(wire.canonical(view), original)

    def test_nonempty_projection_sorts_pointers_and_ignores_input_order(self) -> None:
        raw_fields = ["/z", "/a/0", "/a", "/", "/\u00e9", "/\U00010000"]
        first = Projection(raw_fields)
        raw_fields.append("/mutated")
        second = Projection(tuple(reversed(first.fields or ())))

        expected_order = ("/", "/a", "/a/0", "/z", "/\u00e9", "/\U00010000")
        self.assertEqual(first.fields, expected_order)
        self.assertEqual(second.fields, expected_order)
        self.assertEqual(first, second)

        view: dict[str, object] = {
            "": "empty-key",
            "a": [10, 20],
            "\u00e9": True,
            "\U00010000": None,
        }
        expected_bytes = wire.canonical(
            [
                [True, "empty-key"],
                [True, [10, 20]],
                [True, 10],
                [False],
                [True, True],
                [True, None],
            ]
        )
        self.assertEqual(first.encode(view), expected_bytes)
        self.assertEqual(second.encode(view), expected_bytes)

    def test_rfc6901_escaping_empty_keys_and_literal_asterisk(self) -> None:
        view: dict[str, object] = {
            "": {"": "double-empty"},
            "a/b": 1,
            "m~n": 2,
            "~1": "tilde-one",
            "/": "slash-key",
            "/0": "slash-zero",
            "~/": "tilde-slash",
            "/~": "slash-tilde",
            "*": "literal-star",
            "items": [10, 20],
            "nested": {"*": "nested-star", "other": "ignored"},
        }
        projection = Projection(
            [
                "/",
                "//",
                "/a~1b",
                "/m~0n",
                "/~01",
                "/~1",
                "/~10",
                "/~0~1",
                "/~1~0",
                "/*",
                "/items/*",
                "/nested/*",
            ]
        )
        expected_by_pointer = {
            "/": [True, {"": "double-empty"}],
            "//": [True, "double-empty"],
            "/*": [True, "literal-star"],
            "/a~1b": [True, 1],
            "/items/*": [False],
            "/m~0n": [True, 2],
            "/nested/*": [True, "nested-star"],
            "/~01": [True, "tilde-one"],
            "/~0~1": [True, "tilde-slash"],
            "/~1": [True, "slash-key"],
            "/~10": [True, "slash-zero"],
            "/~1~0": [True, "slash-tilde"],
        }
        assert projection.fields is not None
        self.assertEqual(
            projection.encode(view),
            wire.canonical([expected_by_pointer[p] for p in projection.fields]),
        )
        self.assertEqual(Projection(["/*"]).encode({"other": 1}), b"[[false]]")

    def test_array_indexing_rules_and_scalar_traversal(self) -> None:
        huge_index = "9" * 400
        below_maxsize = str(sys.maxsize - 1)
        exact_maxsize = str(sys.maxsize)
        above_maxsize = str(sys.maxsize + 1)
        view: dict[str, object] = {
            "arr": ["zero", "one", *(["x"] * 8), "ten"],
            "obj": {"00": "leading-zero-key", "-1": "neg-key", "-": "dash-key"},
            "text": "abc",
            "num": 42,
            "flag": True,
            "empty": None,
        }
        cases = (
            ("/arr/0", [True, "zero"]),
            ("/arr/1", [True, "one"]),
            ("/arr/10", [True, "ten"]),
            ("/arr/11", [False]),
            ("/arr/00", [False]),
            ("/arr/01", [False]),
            ("/arr/-", [False]),
            ("/arr/-1", [False]),
            ("/arr/+0", [False]),
            ("/arr/+1", [False]),
            ("/arr/ 0", [False]),
            ("/arr/0 ", [False]),
            ("/arr/0\n", [False]),
            ("/arr/1.0", [False]),
            ("/arr/\u0660", [False]),
            ("/arr/foo", [False]),
            (f"/arr/{below_maxsize}", [False]),
            (f"/arr/{exact_maxsize}", [False]),
            (f"/arr/{above_maxsize}", [False]),
            (f"/arr/{huge_index}", [False]),
            ("/obj/00", [True, "leading-zero-key"]),
            ("/obj/-1", [True, "neg-key"]),
            ("/obj/-", [True, "dash-key"]),
            ("/text/0", [False]),
            ("/num/0", [False]),
            ("/flag/0", [False]),
            ("/empty/0", [False]),
        )
        for pointer, expected in cases:
            with self.subTest(pointer=pointer):
                self.assertEqual(
                    Projection([pointer]).encode(view),
                    wire.canonical([expected]),
                )

    def test_null_missing_and_falsy_values_remain_distinct(self) -> None:
        projection = Projection(["/value"])
        variants: dict[str, dict[str, object]] = {
            "missing": {},
            "null": {"value": None},
            "false": {"value": False},
            "true": {"value": True},
            "zero_int": {"value": 0},
            "zero_float": {"value": 0.0},
            "neg_zero_float": {"value": -0.0},
            "one_int": {"value": 1},
            "empty_str": {"value": ""},
            "null_str": {"value": "null"},
            "false_str": {"value": "false"},
            "empty_list": {"value": []},
            "empty_dict": {"value": {}},
        }
        encoded = {name: projection.encode(view) for name, view in variants.items()}
        self.assertEqual(encoded["missing"], b"[[false]]")
        self.assertEqual(encoded["null"], b"[[true,null]]")
        self.assertEqual(encoded["false"], b"[[true,false]]")
        self.assertEqual(encoded["zero_int"], b"[[true,0]]")
        self.assertEqual(encoded["empty_str"], b'[[true,""]]')
        self.assertEqual(len(set(encoded.values())), len(variants))

    def test_projection_does_not_mutate_views_or_leak_unselected_fields(self) -> None:
        projection = Projection(["/state/ready"])
        first: dict[str, object] = {
            "role": "alice",
            "case_sha256": "a" * 64,
            "state": {"ready": True, "secret": 1},
        }
        second: dict[str, object] = {
            "role": "bob",
            "case_sha256": "b" * 64,
            "state": {"ready": True, "secret": 2},
            "extra": [1, 2, 3],
        }
        first_before = wire.canonical(first)
        second_before = wire.canonical(second)

        self.assertEqual(projection.encode(first), projection.encode(second))
        self.assertEqual(wire.canonical(first), first_before)
        self.assertEqual(wire.canonical(second), second_before)
        with self.assertRaises(AttributeError):
            projection.fields = None  # type: ignore[misc]

    def test_rejects_invalid_inputs_duplicates_and_escapes_before_reading_views(self) -> None:
        invalid_cases: tuple[tuple[object, str], ...] = (
            ("/a", "sequence"),
            (b"/a", "sequence"),
            (bytearray(b"/a"), "sequence"),
            ({"/a"}, "sequence"),
            ({"/a": 1}, "sequence"),
            (123, "sequence"),
            ([""], "omit fields"),
            (["a"], "start with '/'"),
            (["a/b"], "start with '/'"),
            ([" /a"], "start with '/'"),
            ([None], "strings"),
            ([0], "strings"),
            ([["a"]], "strings"),
            ([b"/a"], "strings"),
            (["/a~"], "invalid escape"),
            (["/a~2b"], "invalid escape"),
            (["/a~b"], "invalid escape"),
            (["/~~0"], "invalid escape"),
            (["/\ud800"], "surrogate"),
            (["/a", "/a"], "duplicate"),
            (["/b", "/a", "/b"], "duplicate"),
            (["/", "/"], "duplicate"),
        )
        for fields, message in invalid_cases:
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, message):
                Projection(fields)  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "JSON object"):
            Projection(["/a"]).encode(["not", "an", "object"])  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "JSON object"):
            Projection(None).encode("not-an-object")  # type: ignore[arg-type]

    def test_strict_bounds_on_count_length_and_components(self) -> None:
        max_fields = [f"/f{i}" for i in range(MAX_FIELDS)]
        self.assertEqual(len(Projection(max_fields).fields or ()), MAX_FIELDS)
        with self.assertRaisesRegex(ValueError, str(MAX_FIELDS)):
            Projection([*max_fields, f"/f{MAX_FIELDS}"])

        max_length_pointer = "/" + ("a" * (MAX_POINTER_LENGTH - 1))
        self.assertEqual(Projection([max_length_pointer]).fields, (max_length_pointer,))
        with self.assertRaisesRegex(ValueError, str(MAX_POINTER_LENGTH)):
            Projection(["/" + ("a" * MAX_POINTER_LENGTH)])

        max_components_pointer = "/a" * MAX_POINTER_COMPONENTS
        self.assertEqual(
            Projection([max_components_pointer]).fields,
            (max_components_pointer,),
        )
        with self.assertRaisesRegex(ValueError, str(MAX_POINTER_COMPONENTS)):
            Projection(["/a" * (MAX_POINTER_COMPONENTS + 1)])

        empty_components_max = "/" * MAX_POINTER_COMPONENTS
        self.assertEqual(Projection([empty_components_max]).fields, (empty_components_max,))
        with self.assertRaisesRegex(ValueError, str(MAX_POINTER_COMPONENTS)):
            Projection(["/" * (MAX_POINTER_COMPONENTS + 1)])
