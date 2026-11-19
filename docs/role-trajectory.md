# Follow one role along a shared schedule

`team-align` stops forever at the first global input or action divergence. `role-trajectory` does not. It keeps measuring one role while the world schedule of both native trials stays the same. Use it to see how one role's view and action change after a partner changed the world.

The report gives observations. It does not say why a view or an action changed. Use a new native trial or a task simulator to test a cause.

Requires SeatLab v0.12.0 or later.

## Inputs

You need the same files as [team alignment](world-order.md): one episode file and one [world-order file](world-order.md) for each native trial.

```sh
seatlab role-trajectory source-episodes.jsonl target-episodes.jsonl \
  --source-order source-order.json --target-order target-order.json \
  --role listener \
  --field /observation/0 --field /observation/1
```

`--field` takes a JSON Pointer (RFC 6901) into the role view. It is optional and repeatable. Without it, the selected view is the complete raw view. An empty field list is an error.

SeatLab reads each order file with `read_world_order`. It requires:

- the same case digest in both files;
- different native trial UUIDs;
- the same registered role set, which contains `--role`.

The same function in Python:

```python
from pathlib import Path
from seatlab.trajectory import role_trajectory

report = role_trajectory(
    Path("source-episodes.jsonl"),
    Path("target-episodes.jsonl"),
    source_order=Path("source-order.json"),
    target_order=Path("target-order.json"),
    role="listener",
    fields=["/observation/0"],  # or None
)
```

SeatLab does not guess a world order. It does not run or sandbox actors.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | A valid report. This includes negative and incomplete results. |
| 2 | Malformed input, for example a mismatched case, an unknown role or an empty field list. |

## The schedule

The command compares only world steps that both order files schedule in the same way. A step in which another role alone acts still counts as matched. The command stops before the first changed simultaneous role group. It never re-aligns by sorting actions or by a role's local turn number.

The role's measured turns follow the common schedule. They continue after another role's action changed the world.

`schedule` has 4 keys:

| Key | Meaning |
| --- | --- |
| `matched_steps` | Count of world steps with the same role group in both files. |
| `boundary_step` | The first step with a different group. When one order is longer, the first extra step. `null` when both lengths are equal. |
| `source_steps` | Length of the source order. |
| `target_steps` | Length of the target order. |

## Status

`status` is `complete` only when both trials are closed and both orders are the same through both ends. A feature mismatch does not change this. Otherwise:

| Status | Meaning |
| --- | --- |
| `order_changed` | The schedules differ at `boundary_step`. |
| `length_changed` | Both trials are closed and the common schedule ends before one of them. |
| `open` | At least one trial is open. |

Measured evidence from earlier steps stays in the report when the status is not `complete`.

## The 3 dimensions

The report compares 3 observable dimensions independently: `raw_view`, `selected_view` and `accepted_action`. Each has exactly 5 keys:

| Key | Meaning |
| --- | --- |
| `equal` | Count of measured turns with equal evidence. |
| `different` | Count of measured turns with different evidence. |
| `unknown` | Count of turns that SeatLab cannot decide. |
| `first_different_step` | World step of the first difference, or `null`. |
| `first_unknown_step` | World step of the first unknown, or `null`. |

`equal + different + unknown` for each dimension equals `role_decisions`. Report denominators for each component separately. Do not merge them into one score.

- **`raw_view`.** Uses the strict view digest. A turn is unknown when either view is not bound.
- **`selected_view`.** Compares strict canonical JSON of the selected pointers. If either side lacks an expected projected field, the whole comparison for that turn is unknown, also when both sides lack it. An explicit JSON `null` is present.
- **`accepted_action`.** Needs an observed action and bound views on both sides. It uses the existing strict `action_equal`. A `null` action is a real action. It differs from no evidence.

A missing actor-frame input basis gives unknown. It does not give a difference. A missing action does not hide a measured input difference. An unbound view also marks the action unknown.

The report never contains raw views, field values or actions.

## Output keys

`schema` (`seatlab.role-trajectory/v1`), `source_native_trial_id`, `target_native_trial_id`, `case_sha256`, `role`, `fields` (sorted list, or `null`), `status`, `schedule`, `role_decisions`, `raw_view`, `selected_view`, `accepted_action`.

## Real result: speaker and listener

Download the [v0.11.0 native study](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.11.0/native-speaker-listener.tar.gz). Each of its 15 trials is a separate 20-step native MPE2 world. Three fixed seeds have 5 teams each. This example uses a matched pair from seed 5. The analysis needs no live game, model or credentials.

```sh
work=$(mktemp -d)
curl -fsSL https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.11.0/native-speaker-listener.tar.gz -o "$work/data.tgz"
(cd "$work" && echo '92e2c01fee3317bd14b5315a70b4dd1d7914e31cd7e3ee0da5e752ab4022f579  data.tgz' | shasum -a 256 -c -)
tar -xzf "$work/data.tgz" -C "$work"
a="$work/cells/s5-plain-plain"; b="$work/cells/s5-rotate-rotate"
seatlab role-trajectory "$a/episodes.jsonl" "$b/episodes.jsonl" \
  --source-order "$a/order.json" --target-order "$b/order.json" --role p1 \
  --field /observation/0 --field /observation/1 --field /observation/2 --field /observation/3 \
  --field /observation/4 --field /observation/5 --field /observation/6 --field /observation/7
```

Plain speaker and plain listener against rotate speaker and rotate listener:

- The speaker symbol changes at step 1.
- The first changed raw view for the listener (`p1`) is at step 2.
- The first 8 listener observation components, `/observation/0` through `/observation/7`, are equal in all 20 steps.
- All 20 accepted listener actions are equal.

Plain speaker and plain listener against plain speaker and rotate listener:

- The first different listener action is at step 2.
- The first different physical input is at step 3.

These are measurements for 2 native pairs on seed 5. Equal movements under different codebooks do not establish that a participant understands or ignores language.

## Compare with team alignment

| | `team-align` | `role-trajectory` |
| --- | --- | --- |
| Scope | All roles | One named role |
| After the first change | Stops for good | Continues on the common schedule |
| Stops at | First global input or action divergence | First changed role group in the order |
| Result | One boundary | Counts and first steps for 3 dimensions |

Use `team-align` to find where the team first diverges. Use `role-trajectory` to measure what happens to one role after that point.
