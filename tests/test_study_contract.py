import copy
import unittest

from seatlab.study_contract import (
    MANIFEST_SCHEMA,
    MAX_CHECKS,
    MAX_INPUT_PATHS,
    MAX_PATH_COMPONENTS,
    MAX_PATH_LENGTH,
    MIN_INPUT_PATHS,
    RECIPE_SCHEMA,
    AlignmentCheck,
    CalibrationCheck,
    Check,
    FileReceipt,
    StudyManifest,
    parse_manifest,
    parse_recipe,
    referenced_paths,
    valid_sha256,
    validate_path,
)
from seatlab.tape import TapeError

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "0123456789abcdef" * 4


def _calibration_check(
    check_id: str = "cal-main",
    *,
    tape: str = "inputs/tape.jsonl",
    predictions: str = "inputs/preds.jsonl",
    pairs: str = "inputs/pairs.json",
    context: str | None = None,
) -> dict[str, object]:
    check: dict[str, object] = {
        "id": check_id,
        "kind": "calibration",
        "tape": tape,
        "predictions": predictions,
        "pairs": pairs,
    }
    if context is not None:
        check["context"] = context
    return check


def _alignment_check(
    check_id: str = "align-main",
    *,
    source: str = "episodes/source.jsonl",
    target: str = "episodes/target.jsonl",
    pairs: str = "episodes/pairs.json",
    input_paths: int | None = None,
) -> dict[str, object]:
    check: dict[str, object] = {
        "id": check_id,
        "kind": "alignment",
        "source": source,
        "target": target,
        "pairs": pairs,
    }
    if input_paths is not None:
        check["input_paths"] = input_paths
    return check


def _manifest_for(checks: list[dict[str, object]]) -> dict[str, object]:
    parsed = parse_recipe({"schema": RECIPE_SCHEMA, "checks": checks})
    files: dict[str, FileReceipt] = {
        path: {"bytes": index * 10, "sha256": f"{index:064x}"}
        for index, path in enumerate(referenced_paths(parsed))
    }
    reports = {check["id"]: f"{index + 1:064x}" for index, check in enumerate(parsed)}
    return {
        "schema": MANIFEST_SCHEMA,
        "checks": checks,
        "files": files,
        "reports": reports,
    }


class PathAndDigestTests(unittest.TestCase):
    def test_valid_sha256_accepts_only_canonical_lowercase_hex(self) -> None:
        self.assertTrue(valid_sha256(_DIGEST_C))
        for bad in (
            _DIGEST_C.upper(),
            _DIGEST_C[:-1] + "A",
            _DIGEST_C[:-1],
            _DIGEST_C + "0",
            _DIGEST_C + "\n",
            "g" * 64,
            b"a" * 64,
            0,
            None,
        ):
            with self.subTest(bad=bad):
                self.assertFalse(valid_sha256(bad))

    def test_validate_path_rejects_aliasing_traversal_hidden_and_ambiguous_forms(self) -> None:
        # Valid boundary cases: single segment, nested, max components, max length.
        self.assertEqual(validate_path("a"), "a")
        self.assertEqual(validate_path("dir_1/file-2.jsonl"), "dir_1/file-2.jsonl")
        max_parts = "/".join(f"d{i}" for i in range(MAX_PATH_COMPONENTS))
        self.assertEqual(validate_path(max_parts), max_parts)
        max_len = "a" * MAX_PATH_LENGTH
        self.assertEqual(validate_path(max_len), max_len)

        too_many_parts = "/".join(f"d{i}" for i in range(MAX_PATH_COMPONENTS + 1))
        too_long = "a" * (MAX_PATH_LENGTH + 1)

        invalid_paths: tuple[object, ...] = (
            "",
            None,
            123,
            b"inputs/tape.jsonl",
            "/",
            "/abs/path.json",
            "inputs/tape.jsonl/",
            "inputs//tape.jsonl",
            "./inputs/tape.jsonl",
            "inputs/./tape.jsonl",
            "../tape.jsonl",
            "inputs/../tape.jsonl",
            ".hidden",
            "inputs/.env",
            ".git/config",
            "inputs\\tape.jsonl",
            "C:/inputs/tape.jsonl",
            "inputs:tape.jsonl",
            "inputs/tape.jsonl\n",
            "inputs/tape.jsonl\x00",
            "inputs/tape name.jsonl",
            " inputs/tape.jsonl",
            "inputs/tape.jsonl ",
            "inputs/café.jsonl",
            "inputs/-leading-dash.jsonl",
            "inputs/_leading_underscore.jsonl",
            "-flag/tape.jsonl",
            "inputs/$var.jsonl",
            "inputs/tape;rm.jsonl",
            too_many_parts,
            too_long,
        )
        for bad in invalid_paths:
            with self.subTest(bad=bad):
                with self.assertRaises(TapeError):
                    validate_path(bad)


class RecipeContractTests(unittest.TestCase):
    def test_recipe_preserves_order_and_deduplicates_referenced_paths(self) -> None:
        checks_in = [
            _alignment_check(
                "z-align",
                source="shared/ep.jsonl",
                target="b/target.jsonl",
                pairs="shared/pairs.json",
                input_paths=MIN_INPUT_PATHS,
            ),
            _calibration_check(
                "a-cal",
                tape="shared/ep.jsonl",
                predictions="a/preds.jsonl",
                pairs="shared/pairs.json",
                context="a/ctx.json",
            ),
            _alignment_check(
                "m-align",
                source="m/source.jsonl",
                target="m/target.jsonl",
                pairs="m/pairs.json",
                input_paths=MAX_INPUT_PATHS,
            ),
        ]
        parsed = parse_recipe({"schema": RECIPE_SCHEMA, "checks": checks_in})
        self.assertEqual([check["id"] for check in parsed], ["z-align", "a-cal", "m-align"])
        self.assertEqual(
            referenced_paths(parsed),
            [
                "a/ctx.json",
                "a/preds.jsonl",
                "b/target.jsonl",
                "m/pairs.json",
                "m/source.jsonl",
                "m/target.jsonl",
                "shared/ep.jsonl",
                "shared/pairs.json",
            ],
        )
        # Returned objects are fresh copies, not aliases of input dicts.
        checks_in[0]["source"] = "mutated.jsonl"
        first = parsed[0]
        self.assertEqual(first["kind"], "alignment")
        if first["kind"] == "alignment":
            self.assertEqual(first["source"], "shared/ep.jsonl")

    def test_recipe_enforces_check_count_and_unique_id_bounds(self) -> None:
        with self.assertRaises(TapeError):
            parse_recipe({"schema": RECIPE_SCHEMA, "checks": []})

        max_checks = [_calibration_check(f"c{i}") for i in range(MAX_CHECKS)]
        parsed_max = parse_recipe({"schema": RECIPE_SCHEMA, "checks": max_checks})
        self.assertEqual(len(parsed_max), MAX_CHECKS)

        too_many = [_calibration_check(f"c{i}") for i in range(MAX_CHECKS + 1)]
        with self.assertRaises(TapeError):
            parse_recipe({"schema": RECIPE_SCHEMA, "checks": too_many})

        duplicate_ids = [_calibration_check("same"), _alignment_check("same")]
        with self.assertRaises(TapeError):
            parse_recipe({"schema": RECIPE_SCHEMA, "checks": duplicate_ids})

        # Valid id length boundary 64 vs invalid 65, plus disallowed prefixes/chars.
        id64 = "A" + "b0_.-" * 12 + "xyz"
        self.assertEqual(len(id64), 64)
        parsed_id64 = parse_recipe(
            {"schema": RECIPE_SCHEMA, "checks": [_calibration_check(id64)]}
        )
        self.assertEqual(parsed_id64[0]["id"], id64)
        bad_ids = (
            "",
            "a" * 65,
            "-bad",
            ".bad",
            "_bad",
            "bad/id",
            "bad:id",
            "bad id",
            "a\n",
        )
        for bad_id in bad_ids:
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(TapeError):
                    parse_recipe(
                        {"schema": RECIPE_SCHEMA, "checks": [_calibration_check(bad_id)]}
                    )

    def test_alignment_input_paths_bounds_and_non_bool(self) -> None:
        for valid in (MIN_INPUT_PATHS, 32, MAX_INPUT_PATHS):
            parsed = parse_recipe(
                {"schema": RECIPE_SCHEMA, "checks": [_alignment_check(input_paths=valid)]}
            )
            self.assertEqual(parsed[0].get("input_paths"), valid)

        for invalid in (0, -1, MAX_INPUT_PATHS + 1, True, False, 1.0, "1", None):
            with self.subTest(input_paths=invalid):
                check = _alignment_check()
                check["input_paths"] = invalid
                with self.assertRaises(TapeError):
                    parse_recipe({"schema": RECIPE_SCHEMA, "checks": [check]})

    def test_rejects_extra_command_like_or_cross_kind_fields_and_null_optionals(self) -> None:
        # Reject executable/shell/network/policy fields at recipe and check levels.
        for extra_key, extra_val in (
            ("exec", ["/bin/sh", "-c", "true"]),
            ("command", "seatlab replay"),
            ("shell", True),
            ("url", "https://example.com/study.json"),
            ("policy", "external"),
        ):
            with self.subTest(level="recipe", extra_key=extra_key):
                with self.assertRaises(TapeError):
                    parse_recipe(
                        {
                            "schema": RECIPE_SCHEMA,
                            "checks": [_calibration_check()],
                            extra_key: extra_val,
                        }
                    )
            with self.subTest(level="calibration", extra_key=extra_key):
                cal = _calibration_check()
                cal[extra_key] = extra_val
                with self.assertRaises(TapeError):
                    parse_recipe({"schema": RECIPE_SCHEMA, "checks": [cal]})
            with self.subTest(level="alignment", extra_key=extra_key):
                aln = _alignment_check()
                aln[extra_key] = extra_val
                with self.assertRaises(TapeError):
                    parse_recipe({"schema": RECIPE_SCHEMA, "checks": [aln]})

        # Cross-kind optional fields and explicit null optional fields must fail.
        cal_with_input_paths = _calibration_check()
        cal_with_input_paths["input_paths"] = 4
        with self.assertRaises(TapeError):
            parse_recipe({"schema": RECIPE_SCHEMA, "checks": [cal_with_input_paths]})

        cal_null_context = _calibration_check()
        cal_null_context["context"] = None
        with self.assertRaises(TapeError):
            parse_recipe({"schema": RECIPE_SCHEMA, "checks": [cal_null_context]})

        aln_with_context = _alignment_check()
        aln_with_context["context"] = "inputs/ctx.json"
        with self.assertRaises(TapeError):
            parse_recipe({"schema": RECIPE_SCHEMA, "checks": [aln_with_context]})

        # Unknown schema or check kind.
        with self.assertRaises(TapeError):
            parse_recipe({"schema": MANIFEST_SCHEMA, "checks": [_calibration_check()]})
        bad_kind = _calibration_check()
        bad_kind["kind"] = "shell"
        with self.assertRaises(TapeError):
            parse_recipe({"schema": RECIPE_SCHEMA, "checks": [bad_kind]})


class ManifestContractTests(unittest.TestCase):
    def test_manifest_accepts_exact_coverage_and_preserves_order(self) -> None:
        raw = _manifest_for(
            [
                _alignment_check("check-2", input_paths=8),
                _calibration_check("check-1", context="inputs/ctx.json"),
            ]
        )
        manifest: StudyManifest = parse_manifest(raw)
        self.assertEqual(manifest["schema"], MANIFEST_SCHEMA)
        self.assertEqual([check["id"] for check in manifest["checks"]], ["check-2", "check-1"])
        self.assertEqual(
            sorted(manifest["files"].keys()),
            referenced_paths(manifest["checks"]),
        )
        self.assertEqual(set(manifest["reports"].keys()), {"check-2", "check-1"})

    def test_manifest_rejects_missing_extra_or_aliased_file_coverage(self) -> None:
        base = _manifest_for([_calibration_check("cal", context="inputs/ctx.json")])

        missing_file = copy.deepcopy(base)
        files = missing_file["files"]
        assert isinstance(files, dict)
        del files["inputs/ctx.json"]
        with self.assertRaises(TapeError):
            parse_manifest(missing_file)

        extra_file = copy.deepcopy(base)
        extra_files = extra_file["files"]
        assert isinstance(extra_files, dict)
        extra_files["inputs/unreferenced.json"] = {"bytes": 1, "sha256": _DIGEST_A}
        with self.assertRaises(TapeError):
            parse_manifest(extra_file)

        aliased_file = copy.deepcopy(base)
        aliased_files = aliased_file["files"]
        assert isinstance(aliased_files, dict)
        aliased_files["./inputs/tape.jsonl"] = aliased_files.pop("inputs/tape.jsonl")
        with self.assertRaises(TapeError):
            parse_manifest(aliased_file)

    def test_manifest_rejects_missing_extra_or_noncanonical_report_coverage(self) -> None:
        base = _manifest_for([_calibration_check("cal"), _alignment_check("aln")])

        missing_report = copy.deepcopy(base)
        reports = missing_report["reports"]
        assert isinstance(reports, dict)
        del reports["aln"]
        with self.assertRaises(TapeError):
            parse_manifest(missing_report)

        extra_report = copy.deepcopy(base)
        extra_reports = extra_report["reports"]
        assert isinstance(extra_reports, dict)
        extra_reports["ghost"] = _DIGEST_B
        with self.assertRaises(TapeError):
            parse_manifest(extra_report)

        uppercase_report = copy.deepcopy(base)
        up_reports = uppercase_report["reports"]
        assert isinstance(up_reports, dict)
        up_reports["cal"] = _DIGEST_A.upper()
        with self.assertRaises(TapeError):
            parse_manifest(uppercase_report)

    def test_manifest_rejects_invalid_receipt_types_and_extra_fields(self) -> None:
        base = _manifest_for([_calibration_check("cal")])

        for bad_bytes in (-1, True, False, 1.5, "10", None):
            with self.subTest(bad_bytes=bad_bytes):
                mutated = copy.deepcopy(base)
                files = mutated["files"]
                assert isinstance(files, dict)
                files["inputs/tape.jsonl"] = {"bytes": bad_bytes, "sha256": _DIGEST_A}
                with self.assertRaises(TapeError):
                    parse_manifest(mutated)

        extra_receipt_field = copy.deepcopy(base)
        files = extra_receipt_field["files"]
        assert isinstance(files, dict)
        files["inputs/tape.jsonl"] = {
            "bytes": 0,
            "sha256": _DIGEST_A,
            "url": "https://example.com/tape.jsonl",
        }
        with self.assertRaises(TapeError):
            parse_manifest(extra_receipt_field)

        extra_manifest_field = copy.deepcopy(base)
        extra_manifest_field["signature"] = "fake"
        with self.assertRaises(TapeError):
            parse_manifest(extra_manifest_field)

        wrong_schema = copy.deepcopy(base)
        wrong_schema["schema"] = RECIPE_SCHEMA
        with self.assertRaises(TapeError):
            parse_manifest(wrong_schema)

    def test_manifest_allows_zero_byte_file_receipt(self) -> None:
        raw = _manifest_for([_alignment_check("aln")])
        files = raw["files"]
        assert isinstance(files, dict)
        files["episodes/source.jsonl"] = {"bytes": 0, "sha256": _DIGEST_A}
        manifest = parse_manifest(raw)
        self.assertEqual(manifest["files"]["episodes/source.jsonl"]["bytes"], 0)

    def test_typed_aliases_accept_valid_check_dicts(self) -> None:
        cal: CalibrationCheck = {
            "id": "cal",
            "kind": "calibration",
            "tape": "t.jsonl",
            "predictions": "p.jsonl",
            "pairs": "pairs.json",
        }
        aln: AlignmentCheck = {
            "id": "aln",
            "kind": "alignment",
            "source": "s.jsonl",
            "target": "t.jsonl",
            "pairs": "ep.json",
            "input_paths": 12,
        }
        checks: list[Check] = [cal, aln]
        self.assertEqual(
            referenced_paths(checks),
            ["ep.json", "p.jsonl", "pairs.json", "s.jsonl", "t.jsonl"],
        )


if __name__ == "__main__":
    unittest.main()
