"""Render a panel_report result as a new, offline page. No raw participant data."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .panel import PANEL_SCHEMA

__all__ = ["write_panel_html"]

_CSS = """
:root{color-scheme:light dark;font:16px/1.55 system-ui,sans-serif;--ink:#17273e;--paper:#f8fafc;--card:#fff;--line:#adbbca;--tag:#d7eee4;--tagink:#17503a}
@media(prefers-color-scheme:dark){:root{--ink:#e2e9f2;--paper:#142131;--card:#1b2b3f;--line:#63788f;--tag:#1c5141;--tagink:#e5fff1}}
*{box-sizing:border-box}body{background:var(--paper);color:var(--ink);margin:0}main{max-width:66rem;margin:auto;padding:clamp(1rem,4vw,3rem)}h1{font-size:clamp(2rem,6vw,4rem);line-height:1.02;margin:.3em 0}h2{margin-top:2rem}p{max-width:80ch}code{overflow-wrap:anywhere}summary{cursor:pointer;padding:.8rem .9rem}summary:focus-visible{outline:3px solid currentColor;outline-offset:1px}details{border:1px solid var(--line);border-radius:.55rem;background:var(--card);margin:.45rem 0}details>div{padding:0 1rem 1rem}dl,dd{margin:0}dt{font-weight:650;margin-top:.55rem}.pick{display:inline-block;background:var(--tag);color:var(--tagink);border-radius:.35rem;padding:.05rem .45rem}.fine{font-size:.9rem}ul{padding-inline-start:1.5rem}li{overflow-wrap:anywhere}header{border-bottom:2px solid var(--line);padding-bottom:1rem}
"""


def _codes(labels: list[str]) -> str:
    return ", ".join(f"<code>{escape(label)}</code>" for label in labels) or "None"


def write_panel_html(report: dict[str, Any], out: Path) -> None:
    """Write an exclusive, self-contained rendering of the verified panel report."""
    if report["schema"] != PANEL_SCHEMA:
        raise ValueError("expected an observed panel report")
    selected = report["selection"]["cases"]
    cases, criteria = report["cases"], report["criteria"]
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="referrer" content="no-referrer"><title>Observed regression panel | SeatLab</title>',
        f"<style>{_CSS}</style></head><body><main><header><p>SEATLAB / VERIFIED NATIVE EVIDENCE</p>",
        "<h1>Observed regression panel</h1>",
        f"<p><strong>{len(selected)} of {len(cases)} cases</strong> cover all {len(report['selection']['covered_criteria'])} witnessed criteria. ",
        f"The design includes {len(criteria)} criteria and references {report['known_native_trials']} distinct native trials.</p>",
        f'<p><strong>Selected cases:</strong> <span class="pick">{_codes(selected)}</span></p></header>',
        '<section aria-labelledby="criteria"><h2 id="criteria">What each case demonstrates</h2>',
        "<p>Open a criterion to see the cases. A witness matches the declared behavior. An opposite is a measured counterexample. Unknown means the recording does not establish either result.</p>",
    ]
    descriptions = {
        "witnessed": "Witnessed",
        "opposite_only": "Opposite only",
        "indeterminate": "Indeterminate",
    }
    for criterion in criteria:
        key = criterion["id"]
        parts.append(
            f"<details><summary><strong>{escape(key)}</strong> · {descriptions[criterion['state']]} · {criterion['witnesses']} of {len(cases)} cases</summary><div><dl>"
        )
        for state, title in (
            ("witness", "Witness"),
            ("opposite", "Opposite"),
            ("unknown", "Unknown"),
        ):
            names = [row["id"] for row in cases if row["results"][key] == state]
            parts.append(f"<dt>{title} ({len(names)})</dt><dd>{_codes(names)}</dd>")
        parts.append("</dl></div></details>")
    parts.extend(
        [
            '</section><section aria-labelledby="provenance"><h2 id="provenance">Native case records</h2>',
            "<p>A case can contain several different team runs. Each UUID identifies one native trial, not one comparison.</p>",
        ]
    )
    for case in cases:
        label, ids = case["id"], case["native_trial_ids"]
        badge = ' <span class="pick">Selected</span>' if label in selected else ""
        parts.append(
            f"<details><summary><strong>{escape(label)}</strong>{badge} · {len(ids)} native trials</summary><div><dl><dt>Case SHA-256</dt><dd>{_codes([case['case_sha256']]) if case['case_sha256'] else 'Unknown'}</dd><dt>Native trial UUIDs</dt><dd>{_codes(ids)}</dd></dl></div></details>"
        )
    parts.extend(
        [
            "</section><footer><h2>Limits and verification</h2>",
            "<p>This is the fewest named cases covering the behaviors observed in this study. It is not the fewest trials, a performance estimate, or a guarantee about future changes.</p>",
            f'<p class="fine">Verified study: {_codes([report["study_sha256"]])}<br>Design: {_codes([report["design_sha256"]])}</p></footer></main></body></html>',
        ]
    )
    html = "\n".join(parts) + "\n"
    with out.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(html)
