# Find the first changed step across the team

A role's first different action need not be the team's first difference. A partner may have changed the world earlier. Use `team-align` when the task world records which roles acted in each step. Use [role alignment](episodes.md) when only the individual histories are available.

## Record the order the world used

Export one [episode](episodes.md) per participating role from each native trial. Write an order file for each trial. The file has 5 fields:

| Field | Meaning |
| --- | --- |
| `schema` | `seatlab.world-order/v1` |
| `case_sha256` | The case shared by the roles, including the deal or environment seed when relevant. |
| `native_trial_id` | The UUID recorded by Harbor for this trial. |
| `episodes` | A map from every role name to its canonical episode digest. |
| `steps` | An array of nonempty groups of roles, in the order the world accepted their decisions. |

In a simultaneous game, `[["chef0","chef1"],["chef0","chef1"]]` describes 2 joint steps. The order of roles **inside** a step has no meaning. In an alternating game, `[["p0"],["p1"],["p0"]]` describes 3 separate decisions. Each occurrence consumes the next frame for that role. Every frame must appear exactly once.

Do not group separate moves just because their timestamps match. Do not split a joint transition according to which container wrote first. If the world did not preserve a reliable order, use the role-level report instead. A digest detects a changed file. It cannot prove that the producer recorded the right order or that a model understood a view.

The [kitchen adapter](../examples/world-order/from-kitchen.py) binds each joint step to both chefs' receipts and the native world log. The [Hanabi adapter](../examples/world-order/from-hanabi.py) binds each alternating move to the world and the acting seat's episode. Neither reads timestamps to infer an order.

## Compare two native trials

```sh
seatlab team-align source-episodes.jsonl target-episodes.jsonl \
  --source-order source-order.json --target-order target-order.json \
  --input-paths 4
```

Both tapes must cover the same case and roles, with different native trial UUIDs. The report counts matched world steps, not seat decisions. It stops at the first group with a different role set, changed evidence or missing evidence. It does not compare a role's next decision against the wrong partner turn.

At the boundary, a role can have a different input, or a different action on equal recorded input. A missing action differs from a recorded `null` action. An unbound view cannot establish agreement. Other roles in the same joint step can still provide evidence. The report does not sort those roles by presumed cause. `--input-paths` can show changed field names, not field values. Treat sensitive field names as private.

A matching prefix is not a matching terminal result. If the shorter recording is open, the report does not claim a different trajectory length. Once the histories differ, the report withholds later steps. Use a new native trial or an explicit task simulator to measure the later outcome. A native verifier, not SeatLab, owns the reward.

## Share the comparison

Use the `team-alignment` check in a [portable study](portable-studies.md) to pin both tapes, both order files and the report. A reader needs no task code, model, Docker daemon or credentials.

The [native game study](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.9.0/native-world-order.tar.gz) contains 32 Hanabi trials and 6 kitchen trials. Extract it and run:

```sh
seatlab study verify study.json \
  --expect-sha256 b59355f4770b6d834d328816ea90bc7b99dead6ef90bb8d83879965c8d177b97
seatlab team-align kitchen/A-TT.jsonl kitchen/A-WT.jsonl \
  --source-order kitchen/A-TT.order.json --target-order kitchen/A-WT.order.json
```

The two chefs act together. At step 1, the substituted chef changes its action. The other chef's input and action still match at that step. This is stronger evidence than sorting the two log writes by arrival time. For an alternating example, see the [matched-deck Hanabi result](https://github.com/PeterMakhnatch/harbor-multiagent/blob/1f9da5ecb0267e25d60bc034404a4601902f7504/adapters/hanabi-crossplay/results.md).
