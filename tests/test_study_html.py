import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast
from unittest.mock import patch

from seatlab import tape as wire
from seatlab.cli import main
from seatlab.replay import replay
from seatlab.study import seal_study, verify_study
from seatlab.study_contract import RECIPE_SCHEMA

_SOURCE = "00000000-0000-4000-8000-000000000001"
_TARGET = "00000000-0000-4000-8000-000000000002"
_VIEW_SECRET = "RAWSECRET_seatlab_view_b41d88"
_ACTION_SECRET = "RAWSECRET_seatlab_action_7f3a9c"
_INJECTED = "RAWSECRET_seatlab_field_c91e2b</details><script>leak</script>"
_ANSWERS: wire.IntRangeAnswers = {
    "kind": "int-range",
    "min": 0,
    "max": 10,
    "include_null": True,
    "optimal": [1, 2],
    "feasible": [1, 2, 3],
}
_LABEL_GAP = r"(?:\s|<[^>]*>)*"
_EXTERNAL_LABEL = r"external(?:ly)?(?:[^0-9<]|<[^>]*>){0,120}"


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def _decision(
    case: str,
    side: str,
    action: wire.Action,
    view: dict[str, object],
    answers: list[wire.Answer] | wire.IntRangeAnswers,
) -> wire.Decision:
    return wire.decision(
        {
            "schema": wire.DECISION_SCHEMA,
            "key": f"{case}:{side}",
            "role": "receiver",
            "native_trial_id": _SOURCE if side == "source" else _TARGET,
            "basis": "bound",
            "view": view,
            "view_sha256": wire.digest(view),
            "observed": {"action": action},
            "answers": answers,
        }
    )


def _receipt(case: str, side: str, row: wire.Decision) -> dict[str, object]:
    return {
        "key": row["key"],
        "case_sha256": wire.digest(case),
        "native_trial_id": row["native_trial_id"],
        "decision_sha256": wire.decision_sha256(row),
    }


def _pairs(case: str, source: wire.Decision, target: wire.Decision) -> dict[str, object]:
    return {
        "schema": "seatlab.calibration/v1",
        "pairs": [
            {
                "id": case,
                "source": _receipt(case, "source", source),
                "target": _receipt(case, "target", target),
            }
        ],
    }


def _external_result(source: wire.Decision, action: wire.Action) -> wire.Result:
    view = source["view"]
    data = wire.canonical(view)
    return wire.validate_result(
        {
            "schema": wire.RESULT_SCHEMA,
            "key": source["key"],
            "role": source["role"],
            "native_trial_id": source["native_trial_id"],
            "basis": source["basis"],
            "source_decision_sha256": wire.decision_sha256(source),
            "source_view_sha256": source["view_sha256"],
            "input_sha256": wire.digest(view),
            "input_bytes": len(data),
            "policy": "external",
            "transform": "identity",
            "status": "measured",
            "action": action,
            "score": wire.score(source, action),
            "error": None,
        }
    )


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(wire.canonical(value) + b"\n")


def _sections(document: str) -> list[str]:
    sections: list[str] = []
    start = 0
    while True:
        open_at = document.find("<details", start)
        if open_at < 0:
            return sections
        close_at = document.find("</details>", open_at)
        if close_at < 0:
            return sections
        sections.append(document[open_at:close_at])
        start = close_at + len("</details>")


def _section(document: str, check_id: str) -> str:
    matches = [section for section in _sections(document) if check_id in section]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one details block for {check_id}, found {len(matches)}"
        )
    return matches[0]


class StudyHtmlCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _seal_contrast_study(self) -> tuple[Path, dict[str, object]]:
        bundle = self.root / "bundle"
        bundle.mkdir()
        measured_view = {"reports": {"left": [1, 2], "right": [1, 2]}}
        measured_source = _decision("measured-case", "source", 2, measured_view, _ANSWERS)
        measured_target = _decision("measured-case", "target", 1, measured_view, _ANSWERS)
        secret_view = {
            "reports": {"left": [1], "right": [1]},
            "environmental_secret": _VIEW_SECRET,
        }
        secret_answers: list[wire.Answer] = [
            {"action": _ACTION_SECRET, "optimal": True, "feasible": True}
        ]
        external_source = _decision(
            "logged-case", "source", _ACTION_SECRET, secret_view, secret_answers
        )
        external_target = _decision(
            "logged-case", "target", _ACTION_SECRET, secret_view, secret_answers
        )

        wire.write_lines(bundle / "measured-sources.jsonl", [measured_source])
        replay(
            bundle / "measured-sources.jsonl",
            bundle / "measured-predictions.jsonl",
            "intersection",
        )
        wire.write_lines(bundle / "measured-tape.jsonl", [measured_source, measured_target])
        _write_json(
            bundle / "measured-pairs.json",
            _pairs("measured-case", measured_source, measured_target),
        )
        wire.write_lines(bundle / "logged-tape.jsonl", [external_source, external_target])
        wire.write_lines(
            bundle / "logged-predictions.jsonl",
            [_external_result(external_source, _ACTION_SECRET)],
        )
        _write_json(
            bundle / "logged-pairs.json",
            _pairs("logged-case", external_source, external_target),
        )
        recipe = bundle / "recipe.json"
        manifest = bundle / "study.json"
        _write_json(
            recipe,
            {
                "schema": RECIPE_SCHEMA,
                "checks": [
                    {
                        "id": "measured-native",
                        "kind": "calibration",
                        "tape": "measured-tape.jsonl",
                        "predictions": "measured-predictions.jsonl",
                        "pairs": "measured-pairs.json",
                    },
                    {
                        "id": "logged-worker",
                        "kind": "calibration",
                        "tape": "logged-tape.jsonl",
                        "predictions": "logged-predictions.jsonl",
                        "pairs": "logged-pairs.json",
                    },
                ],
            },
        )
        seal_study(recipe, manifest)
        return manifest, verify_study(manifest)

    def _assert_labeled(self, section: str, label: str, value: int) -> None:
        number = str(value)
        pattern = (
            rf"(?i)(?:{label}){_LABEL_GAP}{re.escape(number)}(?!\d)"
            rf"|(?<!\d){re.escape(number)}{_LABEL_GAP}(?:{label})"
        )
        self.assertRegex(section, pattern, f"{label}={value} is not visible")

    def _assert_standalone(self, document: str) -> None:
        self.assertRegex(document.lstrip(), r"(?i)<!doctype html>")
        self.assertEqual(len(re.findall(r"(?i)<h1[\s>]", document)), 1)
        self.assertNotRegex(document, r"(?i)<script[\s>]")
        self.assertRegex(document, r"""default-src\s+['"]none['"]""")
        self.assertNotRegex(document, r"https?://")

    def _assert_evidence_difference(self, document: str, verified: dict[str, object]) -> None:
        checks = cast(list[dict[str, object]], verified["checks"])
        measured = next(check for check in checks if check["id"] == "measured-native")
        logged = next(check for check in checks if check["id"] == "logged-worker")
        measured_counts = cast(dict[str, int], measured["counts"])
        logged_counts = cast(dict[str, int], logged["counts"])
        external = cast(dict[str, int], logged["external_observations"])
        self.assertGreater(measured_counts["reproduced"], 0)
        self.assertEqual(logged_counts["reproduced"], 0)
        self.assertGreater(external["action_matches"], 0)
        self.assertNotEqual(logged_counts["reproduced"], external["action_matches"])
        self.assertNotIn("external_observations", measured)

        measured_html = _section(document, "measured-native")
        logged_html = _section(document, "logged-worker")
        self.assertIn(str(measured["report_sha256"]), measured_html)
        self.assertIn(str(logged["report_sha256"]), logged_html)
        self._assert_labeled(measured_html, r"reproduced", measured_counts["reproduced"])
        self._assert_labeled(logged_html, r"reproduced", logged_counts["reproduced"])
        self._assert_labeled(measured_html, r"registered|\bpairs\b", measured_counts["pairs"])
        self._assert_labeled(logged_html, r"registered|\bpairs\b", logged_counts["pairs"])
        self.assertRegex(
            logged_html,
            rf"(?i){_EXTERNAL_LABEL}{external['action_matches']}(?!\d)",
        )
        self.assertNotRegex(
            logged_html,
            rf"(?i)reproduced{_LABEL_GAP}{external['action_matches']}(?!\d)",
        )
        self.assertIn("No external observations were reported", measured_html)
        self.assertIn("That is not a zero match count", measured_html)
        self.assertNotIn("External action matches", measured_html)
        self.assertIn("Logged external matches are not reproduced counts", logged_html)

    def test_real_parser_accepts_html_only_on_verify(self) -> None:
        for command, present in (("verify", True), ("seal", False)):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    main(["study", command, "--help"])
            self.assertEqual(caught.exception.code, 0)
            if present:
                self.assertIn("--html", stdout.getvalue())
            else:
                self.assertNotIn("--html", stdout.getvalue())

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                main(["study", "verify", "study.json", "--html"])
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse((self.root / "study.json").exists())

    def test_verified_report_separates_measured_and_external_evidence(self) -> None:
        manifest, verified = self._seal_contrast_study()
        code, out, err = _invoke(["study", "verify", str(manifest)])
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(out, _json(verified))

        html = self.root / "study.html"
        code, out, err = _invoke(
            [
                "study",
                "verify",
                str(manifest),
                "--expect-sha256",
                str(verified["study_sha256"]),
                "--html",
                str(html),
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(out, _json(verified))
        document = html.read_text(encoding="utf-8")
        self._assert_standalone(document)
        self._assert_evidence_difference(document, verified)
        raw = html.read_bytes()
        self.assertNotIn(_VIEW_SECRET.encode(), raw)
        self.assertNotIn(_ACTION_SECRET.encode(), raw)

    def test_pin_mismatch_leaves_no_html(self) -> None:
        manifest, verified = self._seal_contrast_study()
        digest = str(verified["study_sha256"])
        wrong = ("0" if digest.startswith("1") else "1") + digest[1:]
        fresh = self.root / "fresh.html"
        kept = self.root / "kept.html"
        kept.write_bytes(b"untouched")
        for destination in (fresh, kept):
            code, out, err = _invoke(
                [
                    "study",
                    "verify",
                    str(manifest),
                    "--expect-sha256",
                    wrong,
                    "--html",
                    str(destination),
                ]
            )
            self.assertEqual(code, 1, err)
            self.assertEqual(out, "")
            self.assertIn("seatlab:", err)
        self.assertFalse(fresh.exists())
        self.assertEqual(kept.read_bytes(), b"untouched")

    def test_preexisting_destination_is_not_overwritten(self) -> None:
        manifest, _verified = self._seal_contrast_study()
        existing = self.root / "existing.html"
        payload = b"keep these bytes"
        existing.write_bytes(payload)
        link = self.root / "link.html"
        link.symlink_to(existing)
        missing = self.root / "missing-target.html"
        dangling = self.root / "dangling.html"
        dangling.symlink_to(missing)
        for destination in (existing, link, dangling):
            code, out, err = _invoke(
                ["study", "verify", str(manifest), "--html", str(destination)]
            )
            self.assertEqual(code, 2, err)
            self.assertEqual(out, "")
            self.assertTrue(err.startswith("seatlab:"))
        self.assertEqual(existing.read_bytes(), payload)
        self.assertEqual(link.readlink(), existing)
        self.assertEqual(dangling.readlink(), missing)
        self.assertFalse(missing.exists())

    def test_output_errors_exit_2_and_leave_no_html(self) -> None:
        html = self.root / "absent.html"
        code, out, err = _invoke(
            ["study", "verify", str(self.root / "absent.json"), "--html", str(html)]
        )
        self.assertEqual(code, 2, err)
        self.assertEqual(out, "")
        self.assertFalse(html.exists())
        self.assertTrue(err.startswith("seatlab:"))

        manifest, _verified = self._seal_contrast_study()
        bad = self.root / "bad-pin.html"
        code, out, err = _invoke(
            [
                "study",
                "verify",
                str(manifest),
                "--expect-sha256",
                "NOT-A-HEX-DIGEST",
                "--html",
                str(bad),
            ]
        )
        self.assertEqual(code, 2, err)
        self.assertEqual(out, "")
        self.assertFalse(bad.exists())

    def test_injected_canary_does_not_leak(self) -> None:
        manifest, verified = self._seal_contrast_study()
        poisoned = cast(dict[str, object], json.loads(json.dumps(verified)))
        poisoned["environmental_secret"] = _INJECTED
        poisoned["view"] = {"raw": _INJECTED}
        poisoned["action"] = _INJECTED
        poisoned["unsupported"] = {"argv": [_INJECTED], "payload": _INJECTED}
        checks = cast(list[dict[str, object]], poisoned["checks"])
        for check in checks:
            check["seat_view"] = {"secret": _INJECTED}
            check["raw_action"] = _INJECTED
        html = self.root / "injected.html"
        with patch("seatlab.cli.verify_study", return_value=poisoned):
            code, out, err = _invoke(["study", "verify", str(manifest), "--html", str(html)])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), poisoned)
        text = html.read_text(encoding="utf-8")
        self.assertNotIn(_INJECTED, text)
        self.assertNotIn(_VIEW_SECRET, text)
        self.assertNotIn(_ACTION_SECRET, text)
        self.assertNotRegex(text, r"(?i)<script[\s>]")
        self._assert_evidence_difference(text, poisoned)
