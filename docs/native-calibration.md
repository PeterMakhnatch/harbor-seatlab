# Validate a replay against a different native trial

A replay is useful when its prediction survives a real change in the environment. A correct replay on its own source is not that test. This extension compares a local context intervention with a separately observed native treatment. It keeps the ordinary `compare` command strict: two different native trials can never pass as one recorded decision.

## Registered engineering check

For each released five-person scheduling case, start from the native coordinator's complete `full` proposals. An offline intervention keeps the first 8 starts from each of the 5 lists. It relabels the protocol as `first8`; the native coordinator sees this field too. Apply the same intersection policy. Compare this prediction with the *separate* native `first8` trial for that exact private case. The held-out object here is the other native execution, not the published calendar data. The public answers and offline protocol outcomes were already inspected. This is implementation calibration, not a blinded study or a model comparison.

Register a pair manifest with a case-content digest for each arm. Both digests must match. The two trial UUIDs must differ. Bind each side to its original decision record. Require a real acknowledged target view to count an input match. Compare canonical input digests before checking choices. If inputs differ, an equal final reward cannot establish that the replay reproduced the native interaction. Report any differing action and preserve legitimate nulls. Count unavailable trials as missing, not zero. Also count cases where the treatment actually changes the observed action. Do not quietly drop incomplete pairs.

The initial example has the first case in each of the 4 strata. The extended campaign registers all 200 cases and both native arms, whether measured or not. This is a prediction about a specific deterministic channel change with fixed actors; it is not a claim about replacing a live human, learned speaker, or adaptive conversation.

## Run the positive and negative controls

From the installed source checkout:

```sh
out=$(mktemp -d)
seatlab replay examples/meeting-sources.jsonl --policy intersection \
  --transform-file examples/meeting-first8.json --out "$out/truncated.jsonl"
seatlab calibrate examples/meeting.jsonl "$out/truncated.jsonl" \
  --pairs examples/meeting-pairs.json --transform-file examples/meeting-first8.json
seatlab replay examples/meeting-sources.jsonl --policy intersection --out "$out/unchanged.jsonl"
seatlab calibrate examples/meeting.jsonl "$out/unchanged.jsonl" --pairs examples/meeting-pairs.json
seatlab calibrate examples/meeting.jsonl "$out/truncated.jsonl" --pairs examples/meeting-pairs.json
```

| Source-side intervention | Registered pairs | Independently checked inputs | Matching native actions | Reproduced pairs |
| --- | ---: | ---: | ---: | ---: |
| First 8, with recipe | 4 | 4 | 4 | 4 |
| No intervention | 4 | 4 | 0 | 0 |
| First 8, recipe withheld | 4 | 0 | Not verifiable | 0 |

For case `calendar:2-1`, the source coordinator chooses time `245`. The separate target coordinator submits `null`. The recipe predicts `null`, while the unchanged replay predicts `245`. All 4 target nulls are real decisions. Withholding the recipe leaves 4 opaque predictions; it does not make them wrong. Inspect `pairs` for the role, source and target receipts, actual choices, and mismatch reasons.

Add `--html "$out/report.html"` to any `calibrate` command to write a local inspector. Open that file in a browser. Filter by status or case ID, then expand a case to see the 3 actions and the native receipts. The HTML works without a network connection. The JSON report still goes to stdout. An existing HTML file is never replaced.

The [native exporter](https://github.com/PeterMakhnatch/harbor-multiagent/tree/experiments/seatlab-masbench-calendars/adapters/masbench-scheduling) produces the pair manifest and both tapes from registered trial directories. Use `--all-cases` and the pinned dataset to retain missing scheduled cases. Do not use a complete local simulation to fill an absent native record.

## Calibrating an external (`--exec`) policy

`calibrate` never executes an external policy, so external predictions remain `status: "opaque"` and never earn strict `counts.reproduced` credit. To inspect how an external policy's logged predictions align with separately recorded native trials without trusting unattested execution, pass the same `--transform-file` recipe used during replay:

```sh
out=$(mktemp -d)
seatlab replay examples/meeting-sources.jsonl \
  --transform-file examples/meeting-first8.json \
  --out "$out/ext-truncated.jsonl" \
  --exec /absolute/path/to/your_policy.py
seatlab calibrate examples/meeting.jsonl "$out/ext-truncated.jsonl" \
  --pairs examples/meeting-pairs.json \
  --transform-file examples/meeting-first8.json
```

Put `--exec` last on `seatlab replay`, after `--transform-file` and `--out`. Built-in-only cohorts omit all external observation fields and produce byte-identical v1 reports.

When a cohort contains at least one external prediction, the report adds top-level `external_observations` counts and an `external_observation` object on each external prediction pair (`{"input_match", "action_match", "score_match", "execution_attested": false}`). Each comparison field is `true`, `false`, or `null` when not compared. Strict `counts` and `reproduced` remain unchanged.

| `external_observations` counter | Denominator / eligibility rule |
| --- | --- |
| `predictions` | All pairs whose source prediction uses `policy: "external"`. |
| `inputs_compared` | External predictions with a derived input (`input_verification == "derived"`, via `identity`, `latest`, or the supplied `--transform-file`) and a present target with `basis == "bound"`. Evaluated even if external execution failed (`status == "unmeasured"`) or the target action is unknown (`observed == null`). Excludes missing, unbound (`reconstructed` / `design`), or undescribed-context cases. |
| `input_matches` | Subset of `inputs_compared` where `prediction.input_sha256 == target.view_sha256`. |
| `actions_compared` | Subset of `input_matches` where the external prediction is `status == "measured"` (an explicit `null` action is a valid measured action) and the target has a recorded `observed` action. Never credits coincident actions when inputs differ. |
| `action_matches` | Subset of `actions_compared` with type-exact equality (`prediction.action == target.observed.action` and identical JSON type). |
| `scores_compared` | Subset of `actions_compared` where the native target action is scorable against offline `answers` and `prediction.score` is present. |
| `score_matches` | Subset of `scores_compared` where `prediction.score == target.score`. |

Every external pair sets `external_observation.execution_attested` to `false`: SeatLab checks the derived input commitment and offline score consistency, not which executable, model, or parameters produced the logged action.