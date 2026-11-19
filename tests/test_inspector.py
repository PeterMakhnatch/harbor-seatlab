import tempfile
import unittest
from base64 import b64encode
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import cast

from seatlab import tape as wire
from seatlab.calibration import calibrate
from seatlab.inspector import write_inspector
from seatlab.replay import replay

# These case IDs, decisions, and receipt UUIDs are synthetic test data.
_CASE = "fixture:seat-03"
_SOURCE = "00000000-0000-4000-8000-000000000001"
_TARGET = "00000000-0000-4000-8000-000000000002"
_ACTION = '</script><script id="forged">alert(1)</script><img src=x onerror=alert(2)> & é'
_PRIVATE = "private-fixture-content-must-not-appear"


def _decision(
    side: str, action: wire.Action, *, score_null: bool = True, start: int = 1
) -> wire.Decision:
    view = {"reports": {"left": [start], "right": []}}
    answers: list[wire.Answer] = [
        {"action": value, "optimal": True, "feasible": True}
        for value in (1, _ACTION, _PRIVATE)
    ]
    if score_null:
        answers.append({"action": None, "optimal": False, "feasible": False})
    return wire.decision(
        {
            "schema": wire.DECISION_SCHEMA,
            "key": f"{_CASE}:{side}",
            "role": "receiver",
            "native_trial_id": _SOURCE if side == "source" else _TARGET,
            "basis": "bound",
            "view": view,
            "view_sha256": wire.digest(view),
            "observed": {"action": action},
            "answers": answers,
        }
    )


def _receipt(side: str, decision: wire.Decision | None) -> dict[str, object]:
    return {
        "key": f"{_CASE}:{side}",
        "case_sha256": wire.digest(_CASE),
        "native_trial_id": decision["native_trial_id"] if decision is not None else None,
        "decision_sha256": wire.decision_sha256(decision) if decision is not None else None,
    }


class InspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _calibration(
        self, source: wire.Decision | None, target: wire.Decision | None, *, opaque: bool = False
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory(dir=self.root) as name:
            directory = Path(name)
            tape_path = directory / "decisions.jsonl"
            sources_path = directory / "sources.jsonl"
            results_path = directory / "predictions.jsonl"
            pairs_path = directory / "pairs.json"
            wire.write_lines(tape_path, (row for row in (source, target) if row is not None))
            wire.write_lines(sources_path, () if source is None else (source,))
            pairs_path.write_bytes(
                wire.canonical(
                    {
                        "schema": "seatlab.calibration/v1",
                        "pairs": [
                            {
                                "id": _CASE,
                                "source": _receipt("source", source),
                                "target": _receipt("target", target),
                            }
                        ],
                    }
                )
            )
            replay(sources_path, results_path, "observed" if opaque else "intersection")
            if opaque:
                # Mark a synthetic prediction opaque without starting any external process.
                result = next(wire.read_results(results_path))
                result["policy"] = "external"
                results_path.write_bytes(wire.canonical(result) + b"\n")
            return calibrate(tape_path, results_path, pairs_path)

    def _render(self, result: dict[str, object], name: str = "inspector.html") -> str:
        out = self.root / name
        write_inspector(result, out)
        return out.read_text(encoding="utf-8")

    def test_renders_verified_null_receipts_and_accessible_controls(self) -> None:
        source, target = _decision("source", 1), _decision("target", None)
        document = self._render(self._calibration(source, target))
        for text in (
            "<dt>Registered pairs</dt><dd>1</dd>",
            "<dt>Independently reproduced</dt><dd>1</dd>",
            "<dt>Changed native actions</dt><dd>1</dd>",
            f'<details id="case-{_CASE}" data-case-id="{_CASE}" data-status="reproduced">',
            f'href="#case-{_CASE}"',
            "<dt>Source native action</dt><dd><code>1</code></dd>",
            "<dt>Predicted action</dt><dd>No selection (explicit null)</dd>",
            "<dt>Independently observed target action</dt><dd>No selection (explicit null)</dd>",
            "<dt>Native action changed</dt><dd>Yes</dd>",
            "<dt>Role</dt><dd><code>receiver</code></dd>",
            '<label for="status-filter">Status</label>',
            '<label for="case-search">Search by case ID</label>',
            'id="visible-count" role="status"',
            "Cases shown: 1 of 1.",
            "All cases are shown. Filtering needs JavaScript.",
            f"<code>{_SOURCE}</code>",
            f"<code>{_TARGET}</code>",
            f"<code>{wire.decision_sha256(source)}</code>",
            f"<code>{wire.decision_sha256(target)}</code>",
            f'<dt>Predicted input SHA-256</dt><dd><code>{source["view_sha256"]}</code></dd>',
        ):
            self.assertIn(text, document)
        for label in ("Input match", "Action match", "Score match"):
            self.assertIn(f"<dt>{label}</dt><dd>Yes</dd>", document)
        for status in ("all", "reproduced", "mismatch", "missing", "skipped", "opaque"):
            self.assertIn(f'<option value="{status}">', document)
        self.assertNotIn(_PRIVATE, document)

    def test_escapes_actions_and_omits_unlisted_data_in_opaque_case(self) -> None:
        result = self._calibration(
            _decision("source", _ACTION), _decision("target", _ACTION), opaque=True
        )
        result["raw_report"] = _PRIVATE
        pair = cast(list[dict[str, object]], result["pairs"])[0]
        cast(dict[str, object], pair["source"])["view"] = {"private": _PRIVATE}
        cast(dict[str, object], pair["target"])["answers"] = [_PRIVATE]
        cast(dict[str, object], pair["prediction"])["policy_argv"] = [_PRIVATE]

        document = self._render(result)
        self.assertEqual(document.count(escape(_ACTION, quote=True)), 3)
        self.assertNotIn(_ACTION, document)
        self.assertNotIn('<script id="forged">', document)
        self.assertNotIn("<img", document)
        self.assertNotIn(_PRIVATE, document)
        self.assertIn('data-status="opaque"', document)
        self.assertIn("<dt>Independently reproduced</dt><dd>0</dd>", document)
        self.assertIn("<code>prediction_external_policy</code>", document)
        self.assertIn("<dt>Input match</dt><dd>Yes (derived context)</dd>", document)
        for label in ("Action match", "Score match"):
            self.assertIn(
                f"<dt>{label}</dt><dd>Yes (logged external result; execution not attested)</dd>",
                document,
            )
            self.assertNotIn(f"<dt>{label}</dt><dd>Yes</dd>", document)
        self.assertIn("<dt>Native action changed</dt><dd>No</dd>", document)

        self.assertIn("default-src 'none'", document)
        self.assertNotIn("unsafe-inline", document)
        self.assertNotIn("innerHTML", document)
        for tag, directive in (("script", "script-src"), ("style", "style-src")):
            body = document.split(f"<{tag}>", 1)[1].split(f"</{tag}>", 1)[0]
            hashed = b64encode(sha256(body.encode("utf-8")).digest()).decode("ascii")
            self.assertIn(f"{directive} 'sha256-{hashed}'", document)

    def test_input_mismatch_qualifies_coincident_action_and_score(self) -> None:
        document = self._render(
            self._calibration(_decision("source", None), _decision("target", None, start=2))
        )
        self.assertIn('data-status="mismatch"', document)
        self.assertIn("<code>input_mismatch</code>", document)
        self.assertIn("<dt>Independently reproduced</dt><dd>0</dd>", document)
        self.assertIn("<dt>Input match</dt><dd>No</dd>", document)
        for label in ("Action match", "Score match"):
            self.assertIn(f"<dt>{label}</dt><dd>Yes (inputs differ)</dd>", document)

    def test_missing_or_unmeasured_prediction_is_not_explicit_null(self) -> None:
        missing = self._render(self._calibration(None, _decision("target", None)), "missing.html")
        self.assertIn('data-status="missing"', missing)
        self.assertIn("<dt>Predicted action</dt><dd>Not measured</dd>", missing)
        self.assertIn(
            "<dt>Independently observed target action</dt><dd>No selection (explicit null)</dd>",
            missing,
        )
        skipped = self._render(
            self._calibration(
                _decision("source", 1, score_null=False), _decision("target", 1, score_null=False)
            ),
            "skipped.html",
        )
        self.assertIn('data-status="skipped"', skipped)
        self.assertIn("<code>prediction_unmeasured</code>", skipped)
        self.assertIn("<dt>Predicted action</dt><dd>Not measured</dd>", skipped)
        self.assertNotIn("No selection (explicit null)", skipped)
        for label in ("Input match", "Action match", "Score match"):
            self.assertIn(f"<dt>{label}</dt><dd>Not measured</dd>", skipped)

    def test_rejects_an_unrecognized_schema_without_creating_output(self) -> None:
        out = self.root / "inspector.html"
        results: tuple[dict[str, object], ...] = ({}, {"schema": "seatlab.calibration-result/v2"})
        for result in results:
            with self.assertRaisesRegex(ValueError, "seatlab.calibration-result/v1"):
                write_inspector(result, out)
            self.assertFalse(out.exists())

    def test_never_overwrites_files_or_follows_output_symlinks(self) -> None:
        result = self._calibration(None, None)
        existing = self.root / "existing.html"
        existing.write_bytes(b"keep these bytes")
        missing = self.root / "missing.html"
        for destination in (existing, missing):
            link = self.root / f"link-{destination.name}"
            link.symlink_to(destination)
            with self.assertRaises(FileExistsError):
                write_inspector(result, link)
            self.assertEqual(link.readlink(), destination)
        with self.assertRaises(FileExistsError):
            write_inspector(result, existing)
        self.assertEqual(existing.read_bytes(), b"keep these bytes")
        self.assertFalse(missing.exists())
