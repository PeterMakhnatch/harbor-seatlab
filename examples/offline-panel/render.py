"""Render this measured case study from 3 byte-pinned v0.14.0 CLI outputs."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path

PINS = (
    "fa2b58ba04da7948fdd3945b0910f7e31f1dc6b1476ae98182e1e4eda7a6d932",
    "5365b7562e10100dd0d78064d0ef01ca6801e9d45747bbad176858b2aa3f5557",
    "20899c0ecc5c4c97ffcb551700e7f6b973db6863335a7c373232ceffc183e122",
)
REPO = "https://github.com/PeterMakhnatch/harbor-seatlab"
FORK = "https://github.com/PeterMakhnatch/harbor-multiagent"
CSS = """
:root{color-scheme:light;--ink:#182925;--muted:#425a50;--rule:#c8d1c8}
*{box-sizing:border-box}body{margin:0;background:#fbfcf8;color:var(--ink);font:17px/1.55 system-ui,sans-serif}
main,nav,footer{max-width:48rem;margin:auto;padding:1.2rem}nav{border-bottom:1px solid var(--rule)}
h1{font-size:clamp(2.15rem,6vw,3.75rem);line-height:1.03;max-width:17ch;margin:.4em 0}h2{font-size:1.4rem;margin-top:1.8em}p{max-width:66ch}
a{color:#075342;text-underline-offset:.16em}a:focus-visible,summary:focus-visible{outline:3px solid #b84102;outline-offset:3px}
.eyebrow{font-size:.8em;font-weight:700;text-transform:uppercase;letter-spacing:.08em}.lead{font-size:1.23rem}.rule{border-left:.35rem solid #087458;padding:.25rem 0 .25rem 1rem}
table{width:100%;border-collapse:collapse;margin:.75rem 0}caption{text-align:left;color:var(--muted);font-size:.9em}th,td{text-align:left;padding:.55em .45em;border-bottom:1px solid var(--rule);vertical-align:top}td[data-state=changed]{background:#fff1ca;font-weight:650}td[data-state=unknown]{background:#e7e9ed;font-weight:650}
summary{padding:.55rem .25rem;cursor:pointer;font-weight:650}details{border-top:1px solid var(--rule)}code{overflow-wrap:anywhere}footer{border-top:1px solid var(--rule)}
@media(max-width:32rem){main,nav,footer{padding:.85rem}table{font-size:.83em}th,td{padding:.45em .2em}}
"""

def checked(path, expected):
    with path.open("rb") as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"{path.name}: not the published control report")
    return json.loads(raw)

def table(caption, columns, rows):
    return ("<table><caption>" + html.escape(caption) + "</caption><thead><tr>"
            + "".join(f'<th scope="col">{html.escape(c)}</th>' for c in columns)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")

def row(label, values):
    return ('<tr><th scope="row">' + html.escape(label) + '</th>'
            + ''.join(f'<td data-state="{html.escape(str(x))}">{html.escape(str(x))}</td>' for x in values) + '</tr>')

def render(docs):
    matched, changed, incomplete = docs
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>All 10 teams finish. One listener changes. | SeatLab</title><style>' + CSS + '</style></head><body>',
             '<nav aria-label="Evidence"><a href="index.html">SeatLab evidence gallery</a> / Regression control</nav><main>',
             '<p class="eyebrow">Field report · Cooperative communication · MPE2</p><h1>All 10 teams finish. One listener changes.</h1>',
             '<p class="lead">Would you ship a change to a listener that always moves toward landmark 1 instead of landmark 0? The Harbor completion score alone cannot answer that question.</p>',
             '<p>A speaker sees the target. A listener sees the landmarks and the speaker’s signal. One control listener ignores that signal. We changed only its fixed target. The task measures completion separately from the game’s physical return.</p>',
             '<h2>What the same 2 worlds reveal</h2><p>The original study ran 5 teams on each of 12 seeds. It selected seeds 101 and 102 before collecting either candidate. Each candidate then ran 10 new native trials.</p>']
    parts.append(table('Entries are world × criterion, not independent trials.', ('Control','Stable','Changed','Unknown'),
                       (row(label, (doc['counts'][k] for k in ('stable','changed','unknown')))
                        for label,doc in zip(('Unchanged code on Linux','One listener rule changes','Withhold one binding'),docs,strict=True))))
    parts.append('<p class="rule">The blind listener now differs from the informed listener on seed 101 and matches on seed 102. Four overlapping checks switch on each seed. That is 2 changed worlds, not 8 independent failures.</p>')
    parts.append('<h2>Inspect each registered check</h2><p><strong>Witness</strong> means the named condition occurred. <strong>Opposite</strong> means the measured condition went the other way. Unknown means evidence is missing, not that the role failed.</p>')
    for case in changed['cases']:
        parts.append(f'<details><summary>Seed {html.escape(case["id"][1:])}: changed-rule control</summary>')
        parts.append(table('Compare the original trial contrast with the same contrast in fresh trials.', ('Criterion','Before','After','Result'),
                           (row(cid.replace('-',' '),(cell['source'],cell['candidate'],cell['status'])) for cid,cell in sorted(case['criteria'].items()))))
        parts.append('</details>')
    absent=[(case['id'],key) for case in incomplete['cases'] for key,cell in case['criteria'].items() if cell['status']=='unknown']
    parts.append(f'<p>The incomplete control withholds <code>{html.escape(absent[0][1])}</code> for seed {html.escape(absent[0][0][1:])}. It reuses the unchanged trials and adds no trial.</p>')
    native={u for doc in docs for case in doc['cases'] for column in ('source_native_trial_ids','candidate_native_trial_ids') for u in case[column]}
    parts.extend([f'<h2>What the results do not say</h2><p>The original study and both candidates have 80 distinct native trial IDs. These reports reference {len(native)} IDs on the selected panel. The teams are fixed programs. A match is not a guarantee about untested seeds, policies, or language models.</p>',
        f'<h2>Use it</h2><ul><li><a href="{REPO}/blob/main/docs/measured-recheck.md">Reproduce the 3 CLI reports offline</a> with one download and Python.</li>',
        f'<li><a href="{FORK}/actions/runs/44553270796">See the CI gate running in a separate Harbor repository</a>.</li>',
        f'<li><a href="{REPO}/releases/download/v0.14.0/positive-stock.json">Inspect the stock Eval Lab join</a>: 10 native trials, 20 role executions, 10 shared rewards.</li></ul>',
        f'<footer><a href="{FORK}/blob/ee0127b2c1/adapters/pettingzoo-seats/communication-experiment/panel-change-protocol.md">Preregistered intervention</a> · <a href="{FORK}/blob/34dc0e8b24/adapters/pettingzoo-seats/communication-experiment/panel-change-results.md">Native results</a> · <a href="{REPO}/releases/tag/v0.14.0">Public inputs and CLI reports</a></footer></main></body></html>'])
    return "\n".join(parts) + "\n"

if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("reports",nargs=3,type=Path,metavar="REPORT",help="matched, changed, incomplete JSON (in that order)")
    try:
        print(render([checked(path,pin) for path,pin in zip(p.parse_args().reports,PINS,strict=True)]),end="")
    except (ValueError,OSError) as exc:
        p.exit(2,f"render: {exc}\n")
