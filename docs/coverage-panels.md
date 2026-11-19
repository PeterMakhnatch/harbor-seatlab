# Coverage panels: pick a small observed set of cases

`seatlab study panel` verifies a [portable trajectory study](observed-communication.md). It chooses the fewest case labels whose recorded checks witness every criterion that any case witnesses. It runs no model and no trial. The study verifier checks the native files. Panel selection uses verified counts and does not print raw role values.

## Purpose

Smoke suites are expensive. Run the whole suite once and record trajectory studies. Then use a panel to choose a cheaper subset that still shows each behavior difference you saw. The panel reports observed coverage. It does not predict that the subset will find future faults. It is not a minimum number of trials. It is not a statistically representative sample.

## Command

```
seatlab study panel MANIFEST --expect-sha256 PIN --design DESIGN.json
```

Stdout is JSON only. Add `--html panel.html` to create a self-contained, keyboard-accessible report. An existing file is never replaced. `PIN` is the study digest returned by `study seal`, not the raw file digest. Python entry point: `panel_report(manifest: Path, design: Path, *, expect_sha256: str) -> dict`.

## Design file

The design is strict and rectangular. Unknown keys are errors.

```json
{
  "schema": "seatlab.panel-design/v1",
  "criteria": [
    {"id": "raw-differs",  "dimension": "raw_view",        "when": "different", "role": "agent"},
    {"id": "raw-equal",    "dimension": "raw_view",        "when": "equal",     "role": "agent"},
    {"id": "sel-differs",  "dimension": "selected_view",   "when": "different", "role": "agent",
     "fields": ["/tool", "/args/path"]},
    {"id": "act-differs",  "dimension": "accepted_action", "when": "different", "role": "agent"}
  ],
  "cases": {
    "login":  {"raw-differs": "chk-login-1", "raw-equal": "chk-login-1",
               "sel-differs": "chk-login-2", "act-differs": null},
    "search": {"raw-differs": "chk-search-1", "raw-equal": "chk-search-1",
               "sel-differs": null, "act-differs": "chk-search-3"}
  }
}
```

(The check ids above are placeholders. Use real ids from your manifest.)

Syntax:

- `criteria`: at most 16. `id` and `role` are labels. `dimension` is `raw_view`, `selected_view` or `accepted_action`. `when` is `different` or `equal`.
- `fields`: required for `selected_view`, forbidden for other dimensions. `null` means the full view. Otherwise give a non-empty list of JSON Pointers. The list must match the check's selected fields.
- `cases`: at most 256. Each case maps every criterion id to a check id or `null`. Missing or extra criteria are errors.
- `null` means "not measured". The panel reports `unknown` for it. It never invents a value or a range.
- Ids are data only. Reports sort by id. Ties use the minimum rule below.

## Evidence required

Every referenced check must:

1. exist in the manifest, and the manifest must be fully verified against `PIN`;
2. have kind `role-trajectory`, with a role (and selected fields) that match the criterion;
3. share one native case digest with all other checks in the same case name.

Also required:

- Different case labels must not share a digest.
- No native trial UUID may appear in two cases.
- You may reuse one check inside one case for distinct `(dimension, when)` criteria. The example uses `chk-login-1` for `different` and for `equal`. You may not repeat the same `(check, dimension, when)` under another criterion id.

## Semantics

For each criterion the panel first classifies the observed dimension of the check:

- `different`: any observed difference, even if the check is still open.
- `equal`: only if the check status is complete, role decisions > 0, unknown = 0, and every role decision is known equal.
- `unknown`: all other cases.

It then compares the observed class with `when`:

| Observed vs `when` | Result |
| --- | --- |
| same | `witness` |
| the other known class | `opposite` |
| unknown or `null` | `unknown` |

The criterion state is:

- `witnessed`: at least one witness.
- `opposite_only`: no witness, and every case is explicitly `opposite`.
- `indeterminate`: no witness, and at least one case is `unknown`.

## Selection and minimality

Targets are the criteria with at least one witness. The panel picks the fewest case labels whose witnesses cover all targets. Among sets of that size it picks the lexicographically smallest sorted tuple of labels. If no criterion has a witness, the set is empty. Unwitnessed criteria stay in the report.

The search is exact, so the size is the true minimum for the given observations. An exact dynamic program keeps the shortest and lexicographically first case tuple for each reachable coverage mask. There are at most 65,536 masks. The tie rule makes the output deterministic.

## Output

Schema `seatlab.observed-panel/v1`:

- `study_sha256`, `design_sha256` (canonical design digest), `case_count`, `criterion_count`, `known_native_trials`.
- `selection`: `cases`, `case_count`, `covered_criteria`, `not_witnessed` (all sorted).
- `criteria[]`: `id`, `state`, `witnesses`, `opposites`, `unknown`, `selected_witnesses`.
- `cases[]`: `id`, `case_sha256` (hex or `null`), `native_trial_ids`, `results` (criterion to `witness`, `opposite` or `unknown`).

## Costs

The verifier reads and hashes each input file and recomputes the registered checks. It makes no model call and no network call. It uses only the standard library and shows no host paths. Selection work is bounded by 256 cases times the number of reachable masks.

## Limits and train/test caveat

- The panel shows what the studied runs witnessed. A criterion that no case witnessed is not covered. Read `not_witnessed`, `opposite_only` and `indeterminate` before you trust the panel.
- Choose a panel on one set of runs and judge it on different runs. If you test a panel on the same runs that selected it, coverage is 100% by construction. That proves nothing. Keep held-out runs, and repeat the study after the agent or suite changes.
- Do not read a small panel as a quality score. It is a cost-cutting subset with recorded evidence.
- The [12-world worked example](measured-panel.md) includes a verified release and all native comparisons.
