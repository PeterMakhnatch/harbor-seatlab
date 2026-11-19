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
_TRAJ = frozenset(
    {
        "id",
        "kind",
        "report_sha256",
        "role",
        "fields",
        "status",
        "schedule",
        "role_decisions",
        "raw_view",
        "selected_view",
        "accepted_action",
    }
)
_TRAJ_STATUS = frozenset({"complete", "order_changed", "length_changed", "open"})
_SCHED = frozenset({"matched_steps", "boundary_step", "source_steps", "target_steps"})
_DIMS = (
    ("raw_view", "Raw view"),
    ("selected_view", "Selected view"),
    ("accepted_action", "Accepted action"),
)
_DIM_KEYS = frozenset(
    {"equal", "different", "unknown", "first_different_step", "first_unknown_step"}
)
_CSS = (
    ":root{color-scheme:light dark;--bg:#f6f5f2;--card:#fff;--ink:#1c1917;"
    "--line:#c8c4be;--focus:#1d4ed8}"
    "body{margin:0;background:var(--bg);color:var(--ink);font:1rem/1.5 system-ui,sans-serif}"
    "main{max-width:68rem;margin:auto;padding:1.25rem}"
    "h1{margin:0 0 .35rem;font-size:1.7rem;line-height:1.15}"
    "h2{margin:1.35rem 0 .35rem;font-size:1.15rem}p{max-width:44rem}"
    ".mast{padding-bottom:1rem;margin-bottom:.25rem;border-bottom:1px solid var(--line)}"
    ":focus-visible{outline:3px solid var(--focus);outline-offset:3px}"
    "dl{display:grid;gap:.4rem 1.25rem;margin:.3rem 0 0;"
    "grid-template-columns:repeat(auto-fit,minmax(min(100%,15rem),1fr))}"
    "dt{font-size:.8rem;font-weight:650}dd,code{margin:0;overflow-wrap:anywhere}"
    "dd{font-variant-numeric:tabular-nums}"
    "details{background:var(--card);border:1px solid var(--line);border-radius:.5rem;"
    "margin:.5rem 0}"
    "summary{cursor:pointer;padding:.65rem .9rem}"
    ".row{display:grid;gap:.3rem .9rem;align-items:baseline;"
    "grid-template-columns:minmax(7rem,1.2fr) auto minmax(14rem,2fr)}"
    ".id,.result{font-weight:700}.id{overflow-wrap:anywhere}"
    ".kind{border:1px solid var(--line);border-radius:999px;padding:.05rem .5rem;"
    "font-size:.8rem;font-weight:650;white-space:nowrap}"
    ".result{font-variant-numeric:tabular-nums}"
    ".num{display:inline-block;min-width:4ch;text-align:right}"
    ".body{padding:0 .9rem .75rem}"
    "@media (max-width:42rem){.row{grid-template-columns:1fr}.kind{justify-self:start}}"
    "@media (prefers-color-scheme:dark){:root{--bg:#121211;--card:#1c1b19;"
    "--ink:#f5f5f4;--line:#57534e;--focus:#93c5fd}}"
    "@media (forced-colors:active){details,.kind{border-color:CanvasText}"
    ":focus-visible{outline-color:Highlight}}"
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


def _pair_scan(count: int, total: int, tail: str) -> str:
    if count > total:
        raise ValueError("count exceeds its denominator")
    if total == 0:
        return "No registered pairs"
    return f'<span class="num">{count}</span> of <span class="num">{total}</span> {tail}'


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


def _team(item: dict[str, object]) -> tuple[str, str]:
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
    if status == "matched_open":
        scan = f'<span class="num">{matched}</span> matching steps, open prefix'
    elif boundary is None:
        scan = f'<span class="num">{matched}</span> matching steps'
    else:
        scan = f'Step <span class="num">{boundary}</span>: {_words(str(status))}'
    prefix = f"{matched}. No step total is in this summary, so this is not a rate."
    rows = _cell("Status", _words(str(status))) + _cell("Matched prefix steps", prefix)
    return f"<dl>{rows}{_cell('Boundary step', step)}</dl>", scan


def _step(value: object, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer or null")
    return value


def _missing_step(step: int | None) -> str:
    return "Not reached. The missing value is not zero." if step is None else str(step)


def _selected(value: object) -> str:
    if value is None:
        return _cell(
            "Selected fields", "None. The selected view is the complete raw role view."
        )
    if type(value) is not list or not value:
        raise ValueError("fields must be null or a non-empty list")
    items = []
    for pointer in value:
        if type(pointer) is not str or not pointer.startswith("/"):
            raise ValueError("fields must contain JSON Pointers")
        items.append(f"<li>{_code(pointer)}</li>")
    if len(set(value)) != len(value):
        raise ValueError("fields must be unique")
    return _cell(
        "Selected fields", f"{len(value)} named, in canonical order<ul>{''.join(items)}</ul>"
    )


def _dimension(raw: object, label: str, decisions: int, matched: int) -> str:
    dim = _mapping(raw, label)
    if set(dim) != _DIM_KEYS:
        raise ValueError(f"{label} must have exactly the five trajectory keys")
    tally = _nums(dim, ("equal", "different", "unknown"), label)
    if sum(tally.values()) != decisions:
        raise ValueError(f"{label} counts must sum to role decisions")
    first_different = _step(dim["first_different_step"], f"{label}.first_different_step")
    first_unknown = _step(dim["first_unknown_step"], f"{label}.first_unknown_step")
    if any(step is not None and step > matched for step in (first_different, first_unknown)):
        raise ValueError(f"{label} first step exceeds the shared schedule")
    if (first_different is None) != (tally["different"] == 0):
        raise ValueError(f"{label}.first_different_step does not match its count")
    if (first_unknown is None) != (tally["unknown"] == 0):
        raise ValueError(f"{label}.first_unknown_step does not match its count")
    rows = ""
    for key, term in (("equal", "Equal"), ("different", "Different"), ("unknown", "Unknown")):
        count = tally[key]
        rows += _cell(term, f"{count} of {decisions} role decisions" if decisions else "0")
    rows += _cell("First different step", _missing_step(first_different))
    rows += _cell("First unknown step", _missing_step(first_unknown))
    return f"<h3>{escape(label, quote=True)}</h3><dl>{rows}</dl>"


def _trajectory(item: dict[str, object]) -> tuple[str, str]:
    if set(item) != _TRAJ:
        raise ValueError("role-trajectory check must have exactly the summary keys")
    role, status = item["role"], item["status"]
    if type(role) is not str or not role:
        raise ValueError("role must be a non-empty string")
    if status not in _TRAJ_STATUS:
        raise ValueError("role-trajectory status is not supported")
    decisions = item["role_decisions"]
    if type(decisions) is not int or decisions < 0:
        raise ValueError("role_decisions must be a non-negative integer")
    schedule = _mapping(item["schedule"], "schedule")
    if set(schedule) != _SCHED:
        raise ValueError("schedule must have exactly the four trajectory keys")
    counts = _nums(schedule, ("matched_steps", "source_steps", "target_steps"), "schedule")
    matched, source, target = (
        counts["matched_steps"],
        counts["source_steps"],
        counts["target_steps"],
    )
    boundary = _step(schedule["boundary_step"], "schedule.boundary_step")
    if decisions > matched:
        raise ValueError("role decisions exceed the shared schedule")
    if matched > min(source, target):
        raise ValueError("matched steps exceed a schedule length")
    if boundary is None:
        if status in ("order_changed", "length_changed") or not source == target == matched:
            raise ValueError("schedule has no boundary but is not fully matched")
    elif status == "complete" or boundary != matched + 1 or boundary > max(source, target):
        raise ValueError("schedule boundary is inconsistent")
    dims = "".join(_dimension(item[key], label, decisions, matched) for key, label in _DIMS)
    shared = (
        _cell("Role", _code(role))
        + _selected(item["fields"])
        + _cell("Schedule status", _words(str(status)))
        + _cell("Matched world steps", f"{matched} of {source} source, {target} target steps")
        + _cell("Boundary step", _missing_step(boundary))
        + _cell("Role decisions", str(decisions))
    )
    body = (
        "<h3>Shared schedule</h3>"
        "<p>Status describes the world-step schedule. It is not a reward or a score. "
        "The three dimensions below are independent and are not added together. "
        "They are observations of one role. They do not give a cause.</p>"
        f"<dl>{shared}</dl>{dims}"
    )
    scan = (
        f"Role {escape(role, quote=True)}: {_words(str(status))} schedule, "
        f'<span class="num">{decisions}</span> role decisions'
    )
    return body, scan


def _check(item: object, seen: set[str]) -> str:
    check = _mapping(item, "check")
    check_id = _ident(check.get("id"))
    if check_id in seen:
        raise ValueError("check id is duplicated")
    seen.add(check_id)
    kind = check.get("kind")
    if kind not in ("calibration", "alignment", "team-alignment", "role-trajectory"):
        raise ValueError("check kind is not supported")
    digest = _sha(check.get("report_sha256"), "report digest")
    if kind == "team-alignment":
        body, scan = _team(check)
    elif kind == "role-trajectory":
        body, scan = _trajectory(check)
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
            scan = _pair_scan(counts["reproduced"], counts["pairs"], "decisions reproduced")
        else:
            body = _alignment(counts)
            scan = _pair_scan(counts["matched"], counts["pairs"], "histories matched")
    safe_id = escape(check_id, quote=True)
    identity = _cell("Check ID", safe_id) + _cell("Report SHA-256", _code(digest))
    kind_label = _words(str(kind))
    return (
        f'<details id="check-{safe_id}">\n<summary>'
        f'<span class="row"><span class="id">{safe_id}</span>'
        f'<span class="kind">{kind_label}</span>'
        f'<span class="result">{scan}</span></span></summary>\n'
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
        f"<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
        '<header class="mast">\n<h1>Verified study</h1>\n'
        "<p>Status: verified. Verified means these analyses reproduce. "
        "It does not mean a policy succeeded.</p>\n"
        f'<dl>{header}</dl>\n</header>\n<section aria-labelledby="checks-heading">\n'
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
