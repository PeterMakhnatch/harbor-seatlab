# The same team reward hid 2 changed decisions

Both registered native teams earned `1`. Both seats changed their actions when their views lost the last option. The public adapter recovered the changes from actor-acknowledged receipts. An independent worker saw only its projected view. It did not receive the native action or the offline answer table.

| Seat | Source action: 2 options | Target action: 1 option | Target feasible offline | Target optimal offline |
| --- | --- | --- | --- | --- |
| p1 | cobalt | amber | yes | no |
| p2 | coral | mint | yes | no |

The full native trial is `309d5978-f599-452b-bfdd-f14e857e3779`. The first-option trial is `5a543683-1fd4-490d-94f3-2b39a42e47aa`. Both used stock [Olam Harbor](https://github.com/olamlabs/harbor-multiagent/tree/6aeb609e31fd77e5f89ce3bcb018db8045520fb7), 2 nonroot oracle seats, and the same task case. [The registration](https://github.com/PeterMakhnatch/harbor-seatlab/blob/8f97a2902925bb25198612cb1e7df273e9bd253f/examples/receipt-board/protocol.md) preceded both trials.

## What the analysis verified

- The coordinator split 2 source decisions between 2 detached worker processes. Neither request contained the source ID, native action or offline answers. Merging the worker results in reverse order produced the same bytes as serial replay. Ledger SHA-256: `f69c3766e03e0abe4b0e9dacb884c1f7345171011616cfa157c82dd135b23439`.
- Keeping the first option reproduced the 2 target **input digests**. The 2 logged worker actions and their 2 offline scores matched those targets. With unchanged source inputs, 0 of 2 input digests matched. The analysis correctly declined to compare either action under that control.
- Both external predictions remained **opaque**. Strict reproductions: **0**, even in the positive arm. Comparing logged actions is not evidence of which process produced them.
- The independent native episode comparison found a different input at turn 1 for each role. Replaying only the source episodes matched both source actions. Source agreement alone would not have tested the intervention.

A [separate Linux CI job](https://github.com/PeterMakhnatch/harbor-seatlab/actions/runs/44520087321) started stock Docker and ran the 2 worlds again. Both team grades were `1`. Both target inputs and logged actions matched. The native UUIDs differed from the registered study. This is a replication, not 2 additional trials in the original denominator.

## Inspect or reproduce it

[Download the evidence](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.7.0/native-receipt-board.tar.gz) (SHA-256 `2c65c87b1a13c467a563069253b29f9501ce05affb2ce5aea4e9e8c98eafe2db`). It contains the 7 adapter exports, both control ledgers, the independent CI outputs, and minimal native projections. The native projections omit machine paths and are not entire Harbor trial results. They include the verbatim accepted receipts and verifier reward files. They suffice to regenerate all 7 exports byte for byte:

```sh
# From the installed SeatLab source checkout; DATA is the extracted archive directory.
.venv/bin/python examples/receipt-board/export.py \
  "$DATA/native-projections/full" "$DATA/native-projections/first" --out "$DATA/check"
for f in sources.jsonl decisions.jsonl source-episodes.jsonl target-episodes.jsonl \
    pairs.json episode-pairs.json summary.json; do cmp "$DATA/$f" "$DATA/check/$f"; done
```

Use the [walkthrough](README.md) to replay these data or run a fresh native trial. This is an end-to-end adapter test, not a benchmark of model intelligence or evidence that independent people adopted the tool.
