"""Render a verified study summary as one offline HTML page.

The page uses only the in-memory verification object. It does not open study inputs.
"""

from __future__ import annotations

from base64 import b64encode
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import cast

__all__ = ["write_study_html"]

_SCHEMA = "seatlab.study-verification/v1"
_START = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
_ID = _START | frozenset("_.-")
_HEX = frozenset("0123456789abcdef")
_CAL = (
    "pairs",
    "source_available",
    "target_available",
    "source_missing",
    "target_missing",
    "target_acknowledged",
    "native_action_compared",
    "native_action_changed",
    "prediction_measured",
    "prediction_unmeasured",
    "prediction_opaque",
    "prediction_skipped",
    "input_compared",
    "input_matches",
    "action_compared",
    "action_matches",
    "score_compared",
    "score_matches",
    "coincident_action_matches",
    "reproduced",
)
_EXT = (
    "predictions",
    "inputs_compared",
    "input_matches",
    "actions_compared",
    "action_matches",
    "scores_compared",
    "score_matches",
)
_ALIGN = (
    "missing",
    "unbound",
    "missing_observation",
    "input_changed",
    "action_changed",
    "length_changed",
    "open_prefix",
    "matched",
)
_TEAM = frozenset(
    {
        "matched",
        "matched_open",
        "order_changed",
        "step_changed",
        "missing_evidence",
        "length_changed",
    }
)
_CSS = (
    ":root{color-scheme:light dark}"
    "body{margin:0;background:#fff;color:#161616;font:1rem/1.5 system-ui,sans-serif}"
    "main{max-width:64rem;margin:auto;padding:1rem}h1{font-size:1.6rem;line-height:1.2}"
    ":focus-visible{outline:3px solid currentColor;outline-offset:3px}"
    "dl{display:grid;gap:.75rem 1.25rem;margin:.75rem 0;"
    "grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr))}"
    "dt{font-weight:700}dd,summary,code{margin:0;overflow-wrap:anywhere}"
    "details{border:2px solid currentColor;margin:.75rem 0}"
    "summary{cursor:pointer;padding:.75rem}.body{padding:0 .75rem .75rem}"
    ".badge{border:2px solid currentColor;padding:0 .35rem}"
    "@media (prefers-color-scheme:dark){body{background:#101010;color:#f4f4f4}}"
)


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _ident(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 64
        or value[0] not in _START
        or any(char not in _ID for char in value)
    ):
        raise ValueError("check id must be a portable ASCII identifier")
    return value


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise ValueError(f"{name} must be an object with string keys")
    return cast(dict[str, object], value)


def _nums(value: object, keys: tuple[str, ...], name: str) -> dict[str, int]:
    raw = _mapping(value, name)
    if any(key not in raw for key in keys):
        raise ValueError(f"{name} is missing a required count")
    out: dict[str, int] = {}
    for key in keys:
        count = raw[key]
        if type(count) is not int or count < 0:
            raise ValueError(f"{name}.{key} must be a non-negative integer")
        out[key] = count
    return out


def _of(count: int, total: int, unit: str) -> str:
    if count > total:
        raise ValueError("count exceeds its denominator")
    return "Not compared" if total == 0 else f"{count} of {total} {unit}"


def _cell(term: str, detail: str) -> str:
    return f"<div><dt>{escape(term, quote=True)}</dt><dd>{detail}</dd></div>"


def _words(token: str) -> str:
    text = token.replace("_", " ").replace("-", " ")
    return escape(text[:1].upper() + text[1:], quote=True)


def _code(value: str) -> str:
    return f"<code>{escape(value, quote=True)}</code>"


def _calibration(counts: dict[str, int], external: dict[str, int] | None) -> str:
    if counts["action_compared"] != counts["input_matches"]:
        raise ValueError("action comparison does not match equal inputs")
    mismatches = counts["input_compared"] - counts["input_matches"]
    if mismatches < 0:
        raise ValueError("input matches exceed accepted inputs")
    compared = counts["native_action_compared"]
    rows = (
        _cell("Registered pairs", str(counts["pairs"]))
        + _cell("Reproduced", _of(counts["reproduced"], counts["pairs"], "registered pairs"))
        + _cell("Accepted inputs", str(counts["input_compared"]))
        + _cell(
            "Equal input digests",
            _of(counts["input_matches"], counts["input_compared"], "accepted inputs"),
        )
        + _cell("Actions compared after equal inputs", str(counts["action_compared"]))
        + _cell(
            "Matched actions",
            _of(counts["action_matches"], counts["action_compared"], "compared actions"),
        )
        + _cell("Pairs with two native actions", str(compared))
        + _cell(
            "Native action changes",
            _of(counts["native_action_changed"], compared, "pairs with two native actions"),
        )
        + _cell("Input mismatches", f"{mismatches} (accepted inputs minus equal digests)")
        + _cell(
            "Coincident action matches",
            _of(counts["coincident_action_matches"], mismatches, "input mismatches"),
        )
    )
    note = (
        "<p>Reproduced uses registered pairs. Missing, opaque, and skipped pairs stay in "
        "that total. They are not a separate failure rate.</p>"
    )
    if external is None:
        absent = (
            "<p>No external observations were reported. That is not a zero match count.</p>"
        )
        return note + f"<dl>{rows}</dl>" + absent
    observed = (
        _cell("External predictions", str(external["predictions"]))
        + _cell("External inputs compared", str(external["inputs_compared"]))
        + _cell(
            "External input matches",
            _of(external["input_matches"], external["inputs_compared"], "external inputs"),
        )
        + _cell("External actions compared", str(external["actions_compared"]))
        + _cell(
            "External action matches",
            _of(external["action_matches"], external["actions_compared"], "external actions"),
        )
        + _cell("External scores compared", str(external["scores_compared"]))
        + _cell(
            "External score matches",
            _of(external["score_matches"], external["scores_compared"], "external scores"),
        )
    )
    return (
        note + f"<dl>{rows}</dl><h3>External observations</h3>"
        "<p>Logged external matches are not reproduced counts. Execution is not attested.</p>"
        f"<dl>{observed}</dl>"
    )


def _alignment(counts: dict[str, int]) -> str:
    if sum(counts[key] for key in _ALIGN) != counts["pairs"]:
        raise ValueError("alignment statuses must partition registered pairs")
    rows = _cell("Registered pairs", str(counts["pairs"]))
    for key in _ALIGN:
        rows += _cell(_words(key), _of(counts[key], counts["pairs"], "registered pairs"))
    turns = f"{counts['matched_prefix']}. This is a sum across pairs, not a pair rate."
    return f"<dl>{rows}{_cell('Matched prefix turns', turns)}</dl>"


def _team(item: dict[str, object]) -> str:
    if "status" not in item or "matched_steps" not in item or "boundary_step" not in item:
        raise ValueError("team-alignment check is missing a required field")
    status, matched, boundary = item["status"], item["matched_steps"], item["boundary_step"]
    if status not in _TEAM:
        raise ValueError("team-alignment status is not supported")
    if type(matched) is not int or matched < 0:
        raise ValueError("matched_steps must be a non-negative integer")
    if boundary is None:
        step = "Not reached. The missing value is not zero."
    elif type(boundary) is int and boundary >= 1:
        step = str(boundary)
    else:
        raise ValueError("boundary_step must be a positive integer or null")
    prefix = f"{matched}. No step total is in this summary, so this is not a rate."
    rows = _cell("Status", _words(str(status))) + _cell("Matched prefix steps", prefix)
    return f"<dl>{rows}{_cell('Boundary step', step)}</dl>"


def _check(item: object, seen: set[str]) -> str:
    check = _mapping(item, "check")
    check_id = _ident(check.get("id"))
    if check_id in seen:
        raise ValueError("check id is duplicated")
    seen.add(check_id)
    kind = check.get("kind")
    if kind not in ("calibration", "alignment", "team-alignment"):
        raise ValueError("check kind is not supported")
    digest = _sha(check.get("report_sha256"), "report digest")
    if kind == "team-alignment":
        body = _team(check)
    else:
        keys = _CAL if kind == "calibration" else ("pairs", "matched_prefix", *_ALIGN)
        counts = _nums(check.get("counts"), keys, "counts")
        if kind == "calibration":
            external = (
                _nums(check["external_observations"], _EXT, "external_observations")
                if "external_observations" in check
                else None
            )
            body = _calibration(counts, external)
        else:
            body = _alignment(counts)
    safe_id = escape(check_id, quote=True)
    identity = _cell("Check ID", safe_id) + _cell("Report SHA-256", _code(digest))
    return (
        f'<details id="check-{safe_id}">\n<summary><strong>{safe_id}</strong> '
        f'<span class="badge">{_words(str(kind))}</span> '
        f"Report SHA-256 {_code(digest)}</summary>\n"
        f'<div class="body"><dl>{identity}</dl>{body}</div></details>'
    )


def _render(document: dict[str, object]) -> str:
    if document.get("schema") != _SCHEMA or document.get("status") != "verified":
        raise ValueError(f"study HTML requires verified {_SCHEMA}")
    study = _sha(document.get("study_sha256"), "study_sha256")
    files, size = document.get("files"), document.get("bytes")
    if type(files) is not int or files < 0 or type(size) is not int or size < 0:
        raise ValueError("files and bytes must be non-negative integers")
    checks = document.get("checks")
    if type(checks) is not list or not 1 <= len(checks) <= 128:
        raise ValueError("checks must contain 1 to 128 items")
    seen: set[str] = set()
    rows = "\n".join(_check(item, seen) for item in checks)
    digest = b64encode(sha256(_CSS.encode("utf-8")).digest()).decode("ascii")
    policy = (
        "default-src 'none'; script-src 'none'; "
        f"style-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'"
    )
    header = (
        _cell("Manifest SHA-256", _code(study))
        + _cell("Declared files", str(files))
        + _cell("Declared bytes", str(size))
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{policy}">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Verified study</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n<main>\n<h1>Verified study</h1>\n"
        "<p>Status: verified. Verified means these analyses reproduce. "
        "It does not mean a policy succeeded.</p>\n"
        f'<dl>{header}</dl>\n<section aria-labelledby="checks-heading">\n'
        '<h2 id="checks-heading">Checks</h2>\n'
        "<p>Each check keeps its own denominators. "
        "This page does not add counts across checks.</p>\n"
        f"{rows}\n</section>\n</main>\n</body>\n</html>\n"
    )


def write_study_html(verification: dict[str, object], out: Path) -> None:
    """Write a validated study verification to a new self-contained HTML file."""
    if type(verification) is not dict:
        raise ValueError(f"study HTML requires verified {_SCHEMA}")
    document = _render(verification)
    # Exclusive creation also rejects both existing and dangling output symlinks.
    with out.open("x", encoding="utf-8", newline="\n") as file:
        file.write(document)
