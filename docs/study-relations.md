# Join a verified study to native trials

A [portable study](portable-studies.md) reports counts and boundaries per check. Eval Lab knows native trials by their UUIDs. `seatlab study relations` connects the two. It writes one JSON Lines row for each registered comparison in a verified study. Each row names its check, its case digest and both native trial UUIDs. The rows contain no actor views, actions, rewards, answers, prompts or file paths.

The command uses no database or service. Load the file with any JSON Lines reader, then join on the UUID columns.

## Export the relations

```sh
seatlab study relations study.json --expect-sha256 "$STUDY_SHA256" --out relations.jsonl
```

The trusted pin is required. SeatLab first runs the complete [`study verify`](portable-studies.md#verify-a-study) procedure: the manifest pin, every file receipt and every recomputed report digest. It writes the relation file only after the last check verifies. It reads each input once, from the same private snapshot that it verifies. On success, stdout shows a JSON summary with 4 fields:

| Field | Meaning |
| --- | --- |
| `schema` | `seatlab.study-relations-summary/v1` |
| `study_sha256` | The verified canonical manifest digest. |
| `rows` | The number of relation rows, one per registered comparison. |
| `checks` | The number of registered checks. |

| Exit | Meaning |
| --- | --- |
| `0` | The study verifies and the new relation file is complete. |
| `1` | The trusted pin, a file receipt or a recomputed report differs. No file is written. |
| `2` | Input is unreadable, malformed or unsupported, the output path exists, or publication failed. No file is written. |

The output path must not exist. SeatLab writes a temporary file in the same directory, then links it to the new name. A reader never sees a partial file. An existing file is never replaced, even if it has the same content.

## Fields

Every row has all 16 fields. A `null` value means that the report has no value for this comparison. It never means zero.

| Field | Meaning |
| --- | --- |
| `schema` | `seatlab.study-relation/v1` |
| `study_sha256` | The canonical manifest digest. It equals the pin that you supplied. |
| `report_sha256` | The verified report digest for this check. It equals `checks[].report_sha256` from `study verify`. |
| `check_id` | The registered check identifier. |
| `kind` | `team-alignment`, `calibration`, `alignment` or `role-trajectory`. |
| `pair_index` | The 1-based position of the comparison within its check, in report order. A team alignment or role trajectory has 1 row. |
| `case_sha256` | The registered digest of the private task case. |
| `source_native_trial_id`, `target_native_trial_id` | Canonical native UUIDs. `null` if the publisher registered that arm as unavailable. |
| `role` | The named role for `alignment` and `role-trajectory`. `null` for the other kinds. |
| `status` | The status of this comparison in the verified report. |
| `boundary_unit` | `world_step` for `team-alignment` and `role-trajectory`, `role_turn` for `alignment`, `null` for `calibration`. |
| `boundary` | The reported boundary. For a role trajectory this is a **schedule** boundary, not its first changed view or action. |
| `matched_prefix` | Equal decision steps or turns for the alignment kinds. Otherwise `null`. Matching schedules need not have matching decisions. |
| `native_action_changed` | Calibration: whether two recorded actions differ. Role trajectory: `true` if any observed accepted action differs. A negative requires a complete schedule and at least 1 action, all known and equal. Otherwise `null`. |
| `reproduced` | Calibration only: whether input, action and score all match. |

For `team-alignment`, `boundary` and `matched_prefix` come from `boundary_step` and `matched_steps`. For `alignment`, they come from `boundary_turn` and `matched_prefix`. Calibration rows set both to `null`, and the alignment kinds set `native_action_changed` and `reproduced` to `null`. Read `status` first. For example, a `missing` alignment row has no compared turns, so its `matched_prefix` is not a measurement.

See the [role-trajectory recipe](trajectory-studies.md) for a measured example. An absent selected field makes the selected view unknown. It does not erase an independently observed action.

The rows identify cases and checks on purpose. They are not anonymous. Treat check identifiers and case digests as you treat the study itself.

## Rows are comparisons, not trials

One trial can appear in many rows. A role alignment registers one pair per case and role, so a two-role team gives 2 rows for the same 2 trials. Several checks in one study can reuse a trial. Count distinct UUIDs to count trials. Do not count rows.

`world_step` and `role_turn` are different units. One world step can contain decisions from several roles. Do not add `matched_prefix` values across units, kinds or checks. Native rewards stay with the native verifier; join them from Eval Lab by UUID and count each trial once.

A missing UUID stays `null`. SeatLab does not fill it from another check or from a later run. The publisher selects and registers the pairs. A relation file cannot show that an unregistered trial never existed.

## Query the file with DuckDB

```sql
CREATE TABLE relations AS
SELECT * FROM read_json('relations.jsonl', format = 'newline_delimited', columns = {
  schema: 'VARCHAR', study_sha256: 'VARCHAR', report_sha256: 'VARCHAR',
  check_id: 'VARCHAR', kind: 'VARCHAR', pair_index: 'BIGINT', case_sha256: 'VARCHAR',
  source_native_trial_id: 'UUID', target_native_trial_id: 'UUID', role: 'VARCHAR',
  status: 'VARCHAR', boundary_unit: 'VARCHAR', boundary: 'BIGINT',
  matched_prefix: 'BIGINT', native_action_changed: 'BOOLEAN', reproduced: 'BOOLEAN'
});

-- Distinct native trials, not rows.
SELECT count(DISTINCT trial) AS native_trials
FROM (
  SELECT source_native_trial_id AS trial FROM relations
  UNION ALL
  SELECT target_native_trial_id FROM relations
)
WHERE trial IS NOT NULL;

-- Matched steps per check, kept in their own unit.
SELECT check_id, boundary_unit, count(*) AS comparisons,
       count(matched_prefix) AS measured, sum(matched_prefix) AS matched
FROM relations
WHERE boundary_unit IS NOT NULL
GROUP BY check_id, boundary_unit
ORDER BY check_id;
```

The explicit column list keeps an all-`null` column typed. `count(matched_prefix)` shows how many rows have a value. Compare it with `comparisons` before you read `matched`. To add native results, join a table of Eval Lab trials on `source_native_trial_id` or `target_native_trial_id`.

## Rebuild the bytes yourself

Each line is the canonical JSON of one row, followed by a line feed. Canonical JSON uses sorted keys, no spaces, ASCII escapes and no non-finite numbers, as described in the [decision format](format.md). Any reader can check a file without SeatLab:

```sh
python3 - relations.jsonl <<'EOF'
import hashlib, json, sys
data = open(sys.argv[1], "rb").read()
rows = [json.loads(line) for line in data.splitlines()]
rebuilt = b"".join(
    json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
               allow_nan=False).encode("ascii") + b"\n"
    for row in rows
)
assert rebuilt == data, "not canonical"
print(len(rows), hashlib.sha256(data).hexdigest())
EOF
```

The same study, pin and SeatLab version produce the same bytes. Record the file digest with the pin if you share the file. Each `report_sha256` matches the digest that `study verify` prints for that check. A digest detects a change. It does not authenticate the publisher or a native trial.

## Try the native calendar study

This example uses the published calendar study from [real team decisions](evidence.md) and the v0.11.0 wheel.

```sh
work=$(mktemp -d)
python3 -m venv "$work/venv"
releases=https://github.com/PeterMakhnatch/harbor-seatlab/releases/download
"$work/venv/bin/python" -m pip install --no-deps "$releases/v0.11.0/harbor_seatlab-0.11.0-py3-none-any.whl"
curl -fsSL "$releases/v0.8.0/native-calendar-200.tar.gz" -o "$work/data.tar.gz"
(cd "$work" && echo '5717b49b4a0f52a7e8624ef60ae94f6d4d789173e0088d6cba202a549964a9fc  data.tar.gz' | shasum -a 256 -c -)
tar -xzf "$work/data.tar.gz" -C "$work"
"$work/venv/bin/seatlab" study relations "$work/study.json" \
  --expect-sha256 b140630534d5776c33b52a95aaaa6fc76eae0f73cf9161737541bbcad69ba5d9 \
  --out "$work/relations.jsonl"
```

To join the rows to a stock Eval Lab installation, see the optional [Eval Lab example](../examples/eval-lab/). The relation file does not need it.
