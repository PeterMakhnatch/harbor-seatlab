# Join study relations to native trials in stock Eval Lab

`join.py` is an optional, read-only bridge between a SeatLab relations file and
native Harbor trials. It runs in the Python environment of a stock Eval Lab
checkout. It imports `evallab.results.load_trial` and reads native rewards only
through that loader. It writes no checkout, catalog, Parquet store or database.

Start with [the measured job and copy-paste example](measured.md). It needs 2 recorded native trial IDs, not a new Harbor run.

## Use Eval Lab as the source of worker assignments

`launches.py` is an optional, separate producer for teams that already keep native trial directories. It uses `evallab.results.load_trial` in your existing Eval Lab environment. You do **not** need to install SeatLab in that environment. The SeatLab process later checks the registry and the returned role recordings. [Two-task protocol and measured results](launch-study.md).

1. Take `worklist_sha256` from your pinned source receipt.
2. Have the scheduler record a private JSON object: each **worklist job ID** maps to its assigned **native trial directory**, or to `null` if no UUID is available. Use absolute paths or paths relative to this JSON file. Do not infer the mapping from returned role recordings.
3. Generate the public registry. Keep its printed digest in a coordinator-controlled channel:

```sh
"$EVAL_LAB/.venv/bin/python" -I examples/eval-lab/launches.py source/worklist.json private-assignments.json \
  --expect-worklist-sha256 "$WORKLIST_PIN" --out launches.json > launch-pin.json
```

4. In your SeatLab environment, collect the native returns:

```sh
seatlab study collect-jobs source returns --receipt source-receipt.json --out comparison \
  --launch-registry launches.json --expect-launch-sha256 "$(jq -r .launch_registry_sha256 launch-pin.json)"
```

The producer copies only the bytes of each assigned `result.json` into a temporary directory for the stock loader. It opens no role logs, reward files, catalog, artifact manifest or model service. The public registry contains only worklist job IDs, native UUIDs and a worklist digest. Keep `private-assignments.json` private. A missing explicit directory, absent `id`, duplicate UUID or wrong pin is invalid. Only an explicit `null` represents an unknown launch. A missing worker return is a different condition; it does not erase a known native ID.

## What the bridge is not

- The relations file is a **derivative**. The producer verifies the study pin.
  The bridge records that pin and the relations file digest. It does not rerun the study checks.
- The bridge is not a dashboard and not another catalog. It prints one JSON
  report on standard output.
- A relation is not a trial. Several relations can name the same native trial.
  Rewards therefore count **once per distinct native trial ID**, not once per
  relation.

## Produce the relations file

Use the registered study manifest and its verified pin:

```sh
WORK=$(mktemp -d)
.venv/bin/seatlab study relations "$MANIFEST" --expect-sha256 "$STUDY_SHA256" \
  --out "$WORK/relations.jsonl"
```

The producer writes `seatlab.study-relation/v1` rows. Each row keeps the check
ID, pair index, case digest, relation kind, verified status and boundary. It
carries native trial UUIDs or `null` for a registered missing arm. It carries no
actor input, action, label, prompt, reward or path.

## Join to native trials

```sh
EVAL_LAB=$HOME/Developer/eval-lab

"$EVAL_LAB/.venv/bin/python" examples/eval-lab/join.py "$WORK/relations.jsonl" \
  --expect-study-sha256 "$STUDY_SHA256" \
  --trials-dir "$NATIVE_ROOT" > "$WORK/join.json"
```

- `--trials-dir` scans one flat root. Each native trial directory is a child
  with `result.json`. A root that is itself a trial directory also works.
- `--trial` adds one explicit native trial directory. Both options repeat.
- `--expect-study-sha256` fails closed when the recorded study digest differs
  from the pin you already verified.
- At least one native input is required. The bridge refuses to report an
  all-missing join from an empty selection.

## What the report contains

| Field | Meaning |
| --- | --- |
| `relations.sha256` | Digest of the relations file you supplied, for pinning the derivative |
| `relations.verification` | Always `"not performed: derivative rows, not a sealed study"` |
| `coverage.registered_missing_sides` | Arms the registration marks `null`. Separate from a native miss |
| `coverage.referenced`, `found`, `missing`, `ambiguous`, `unreferenced` | Counts of distinct referenced UUIDs by resolution state |
| `coverage.skipped_entries` | Entries ignored while scanning a `--trials-dir` root |
| `coverage.distinct_jobs`, `job_trials`, `standalone_trials` | Native `config.job_id` shape of found trials |
| `coverage.role_executions`, `single_agent_executions`, `executions` | Recorded seat runs. `role_executions` groups by seat name |
| `usage.<metric>` | `known_ids`, `partial_ids`, `unknown_ids` and the sum over known IDs. Absent stays unknown, never zero |
| `paired.found_ids`, `scored_ids`, `mean_score`, `unscored_ids` | Shared-reward denominator over distinct native IDs only |
| `outcomes` | Relation counts grouped by kind, verified status and outcome |
| `relations.rows` | One row per relation in input order, with side states and outcome |
| `missing_native_ids`, `ambiguous_native_ids`, `unreferenced_native_ids` | UUID lists. Ambiguous means two supplied directories disagree for one UUID |

Outcomes are `equal`, `different` or `unknown`. `unknown` also carries a reason:
`registered-missing-side`, `native-missing`, `ambiguous` or `score-not-recorded`.
A score is read once per distinct native ID. Overlapping checks cannot add the
same score twice.

The verified status and the paired outcome measure different things. The status
compares the recorded boundary. The outcome compares the shared native reward.
A `matched` relation with `different` rewards, or an `action_changed` relation
with `equal` rewards, is a real observation. It is not a contradiction.

Ambiguous UUIDs and missing UUIDs contribute to no score, job or execution
denominator. Duplicate directory copies with identical content collapse to one
trial. Two directories with the same UUID and different records stay ambiguous.

## DuckDB queries

The relations file is line-delimited JSON. Query it directly, without the
bridge:

```sh
duckdb -c "
SELECT kind, status, count(*) AS pairs
FROM read_json_auto('$WORK/relations.jsonl')
GROUP BY ALL ORDER BY kind, status;"

duckdb -c "
SELECT kind,
       count(*) FILTER (WHERE source_native_trial_id IS NULL) AS source_missing,
       count(*) FILTER (WHERE target_native_trial_id IS NULL) AS target_missing,
       count(DISTINCT source_native_trial_id) AS source_ids
FROM read_json_auto('$WORK/relations.jsonl')
GROUP BY kind ORDER BY kind;"
```

Group the join result by relation shape:

```sh
jq '[.outcomes[] | {kind, status, outcome, relations}]' "$WORK/join.json"
```

Rewards are not written into the report rows. To read a native score, use the
loader or the Eval Lab census in your own process:

```python
from evallab.results import load_trial

record = load_trial(NATIVE_TRIAL)
print(record.id, record.primary_reward)
```

## Contract checks

The bridge fails closed with exit code 2 on a malformed relations file:

- the field set must equal `seatlab.study-relation/v1`;
- `pair_index` must run `1..n` per check in file order;
- `boundary_unit` must match the kind (`world_step`, `role_turn`, or `null`);
- only alignment rows carry a role, and only calibration rows carry
  `native_action_changed` and `reproduced`;
- trial UUIDs must be canonical lowercase UUIDs, and the two sides must differ;
- every row must name one study digest.

Reward, job and usage values come from `load_trial` only. The bridge parses no
native reward file and changes no native artifact.
