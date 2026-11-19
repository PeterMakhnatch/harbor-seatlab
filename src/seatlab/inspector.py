"""Render an allowlisted, offline view of a native calibration result."""

from __future__ import annotations

from base64 import b64encode
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Literal, TypedDict, cast

from .tape import Action, Observation

__all__ = ["write_inspector"]

_SCHEMA = "seatlab.calibration-result/v1"
_STATUSES = {
    "reproduced": "Reproduced",
    "mismatch": "Mismatch",
    "missing": "Missing",
    "skipped": "Skipped",
    "opaque": "Opaque",
}
_NOT_MEASURED = "Not measured"

_STYLES = """
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { margin: 0; background: Canvas; color: CanvasText; line-height: 1.5; }
main { max-width: 65rem; margin: auto; padding: 1rem; }
h1, h2 { line-height: 1.2; }
a { color: LinkText; }
:focus-visible { outline: 2px solid Highlight; outline-offset: 3px; }
[hidden] { display: none; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr));
  gap: 0.75rem 1.25rem;
}
.grid > div, label { min-width: 0; }
.counter dd { font-size: 1.5rem; font-weight: 700; }
dt { font-weight: 600; }
dd { margin: 0; overflow-wrap: anywhere; }
label { display: block; font-weight: 600; }
input, select { display: block; box-sizing: border-box; width: 100%; font: inherit; }
details { border: 1px solid GrayText; border-radius: 0.375rem; margin-block: 0.5rem; }
summary { cursor: pointer; overflow-wrap: anywhere; padding: 0.75rem; }
.case-body { padding: 0 0.75rem 0.75rem; }
.badge { border: 1px solid GrayText; border-radius: 0.25rem; padding: 0 0.35rem; }
code { overflow-wrap: anywhere; }
.action { white-space: pre-wrap; }
"""

_FILTER = """
const statusFilter = document.getElementById('status-filter');
const caseSearch = document.getElementById('case-search');
const visibleCount = document.getElementById('visible-count');
const rows = document.querySelectorAll('#cases details');

function updateFilters() {
  const status = statusFilter.value;
  const query = caseSearch.value.trim().toLowerCase();
  let shown = 0;
  for (const row of rows) {
    const matchesStatus = status === 'all' || status === row.dataset.status;
    const matchesSearch = row.dataset.caseId.toLowerCase().includes(query);
    row.hidden = !(matchesStatus && matchesSearch);
    if (!row.hidden) shown++;
  }
  visibleCount.textContent = `Cases shown: ${shown} of ${rows.length}.`;
}
statusFilter.addEventListener('change', updateFilters);
caseSearch.addEventListener('input', updateFilters);
updateFilters();
document.getElementById('filters').hidden = false;
"""


class _Counts(TypedDict):
    pairs: int
    reproduced: int
    native_action_changed: int


class _Arm(TypedDict):
    native_trial_id: str | None
    decision_sha256: str | None
    observed: Observation | None


class _Prediction(TypedDict):
    role: str
    status: Literal["measured", "unmeasured"]
    action: Action
    input_sha256: str


class _Pair(TypedDict):
    id: str
    source: _Arm
    target: _Arm
    prediction: _Prediction | None
    status: str
    reasons: list[str]
    input_match: bool | None
    action_match: bool | None
    score_match: bool | None
    native_action_changed: bool | None


class _Report(TypedDict):
    counts: _Counts
    pairs: list[_Pair]


def _hash_source(text: str) -> str:
    digest = b64encode(sha256(text.encode("utf-8")).digest()).decode("ascii")
    return f"'sha256-{digest}'"


def _code(value: str | None) -> str:
    return _NOT_MEASURED if value is None else f"<code>{escape(value, quote=True)}</code>"


def _action(value: Action) -> str:
    if value is None:
        return "No selection (explicit null)"
    if isinstance(value, str):
        return f'<code class="action">“{escape(value, quote=True)}”</code>'
    return f"<code>{value}</code>"


def _observed(arm: _Arm) -> str:
    observed = arm["observed"]
    return _NOT_MEASURED if observed is None else _action(observed["action"])


def _check(value: bool | None, qualification: str = "") -> str:
    if value is None:
        return _NOT_MEASURED
    return ("Yes" if value else "No") + qualification


def _render_case(pair: _Pair) -> str:
    case_id = escape(pair["id"], quote=True)
    status = pair["status"]
    status_label = _STATUSES[status]
    source, target, prediction = pair["source"], pair["target"], pair["prediction"]
    predicted_action = _NOT_MEASURED
    role_field = ""
    predicted_digest = None
    if prediction is not None:
        role_field = f'<div><dt>Role</dt><dd>{_code(prediction["role"])}</dd></div>'
        predicted_digest = prediction["input_sha256"]
        if prediction["status"] == "measured":
            predicted_action = _action(prediction["action"])

    # Opaque rows can carry logged equality without independently verified inputs.
    unverified = " (not verified)" if status not in ("reproduced", "mismatch") else ""
    input_match = pair["input_match"]
    comparison_note = unverified
    if status == "mismatch" and input_match is False:
        comparison_note = " (inputs differ)"
    reasons = ", ".join(_code(reason) for reason in pair["reasons"]) or "None"

    return f"""<details id="case-{case_id}" data-case-id="{case_id}" data-status="{status}">
  <summary><strong>{case_id}</strong> <span class="badge">{status_label}</span></summary>
  <div class="case-body">
    <dl class="grid">
      <div><dt>Source native action</dt><dd>{_observed(source)}</dd></div>
      <div><dt>Predicted action</dt><dd>{predicted_action}</dd></div>
      <div><dt>Independently observed target action</dt><dd>{_observed(target)}</dd></div>
    </dl>
    <dl class="grid">
      <div><dt>Input match</dt><dd>{_check(input_match, unverified)}</dd></div>
      <div><dt>Action match</dt><dd>{_check(pair["action_match"], comparison_note)}</dd></div>
      <div><dt>Score match</dt><dd>{_check(pair["score_match"], comparison_note)}</dd></div>
      <div><dt>Native action changed</dt><dd>{_check(pair["native_action_changed"])}</dd></div>
    </dl>
    <dl class="grid">
      <div><dt>Source trial UUID</dt><dd>{_code(source["native_trial_id"])}</dd></div>
      <div><dt>Target trial UUID</dt><dd>{_code(target["native_trial_id"])}</dd></div>
      {role_field}
      <div><dt>Source decision SHA-256</dt><dd>{_code(source["decision_sha256"])}</dd></div>
      <div><dt>Target decision SHA-256</dt><dd>{_code(target["decision_sha256"])}</dd></div>
      <div><dt>Predicted input SHA-256</dt><dd>{_code(predicted_digest)}</dd></div>
    </dl>
    <p><strong>Reason codes:</strong> {reasons}</p>
    <a href="#case-{case_id}">Link to this case</a>
  </div>
</details>"""


def _render(report: _Report) -> str:
    counts = report["counts"]
    rows = "\n".join(_render_case(pair) for pair in report["pairs"])
    options = "\n".join(
        f'<option value="{value}">{label}</option>' for value, label in _STATUSES.items()
    )
    policy = (
        f"default-src 'none'; script-src {_hash_source(_FILTER)}; "
        f"style-src {_hash_source(_STYLES)}; base-uri 'none'; form-action 'none'"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="{policy}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Native calibration inspector</title>
  <style>{_STYLES}</style>
</head>
<body>
<main>
  <header>
    <h1>Native calibration inspector</h1>
    <p>A reproduction requires matching input, action, and score from a separate native trial.</p>
    <dl class="grid counter">
      <div><dt>Registered pairs</dt><dd>{counts["pairs"]}</dd></div>
      <div><dt>Independently reproduced</dt><dd>{counts["reproduced"]}</dd></div>
      <div><dt>Changed native actions</dt><dd>{counts["native_action_changed"]}</dd></div>
    </dl>
  </header>
  <section aria-labelledby="case-heading">
    <h2 id="case-heading">Cases</h2>
    <p>Missing, skipped, and opaque cases do not establish a reproduction.</p>
    <noscript><p>All cases are shown. Filtering needs JavaScript.</p></noscript>
    <div id="filters" class="grid" hidden>
      <div>
        <label for="status-filter">Status</label>
        <select id="status-filter">
          <option value="all">All statuses</option>
          {options}
        </select>
      </div>
      <div>
        <label for="case-search">Search by case ID</label>
        <input type="search" id="case-search" autocomplete="off">
      </div>
    </div>
    <p id="visible-count" role="status" aria-atomic="true">
      Cases shown: {len(report["pairs"])} of {len(report["pairs"])}.
    </p>
    <div id="cases">
      {rows}
    </div>
  </section>
</main>
<script>{_FILTER}</script>
</body>
</html>
"""


def write_inspector(calibration: dict[str, object], out: Path) -> None:
    """Write a validated calibration result to a new self-contained HTML file."""
    if calibration.get("schema") != _SCHEMA:
        raise ValueError(f"inspector requires {_SCHEMA}")
    document = _render(cast(_Report, calibration))
    # Exclusive creation also rejects both existing and dangling output symlinks.
    with out.open("x", encoding="utf-8", newline="\n") as file:
        file.write(document)
