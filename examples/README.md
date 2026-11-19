# Where the examples come from

These tapes contain actual completed native Harbor decisions. They contain no model credentials, private host paths or human calendars. Their offline score data is public analyst input. It never enters the policy process.

## Dispatch

`dispatch.jsonl` contains all 72 decisions from the 6 measured arrival-policy trials in the [pinned public study](https://raw.githubusercontent.com/PeterMakhnatch/harbor-multiagent/1af673b0b8ce57353d73b2a8c435467832e7aed6/examples/seatlab/results/primary.json). The 2 seeds have 12 shipments each, repeated across 3 delivery conditions. Each tape row is a projection of one retained batch. Its basis is `reconstructed`, **not** a client receipt for that row.

```sh
curl -fL https://raw.githubusercontent.com/PeterMakhnatch/harbor-multiagent/1af673b0b8ce57353d73b2a8c435467832e7aed6/examples/seatlab/results/primary.json -o /tmp/seatlab-primary.json
seatlab from-handoff /tmp/seatlab-primary.json --panel arrival --out /tmp/seatlab-dispatch.jsonl
cmp /tmp/seatlab-dispatch.jsonl examples/dispatch.jsonl
```

SHA-256 of the distributed file: `0e63811a439ef5f75b816888f729c7f78b725728b56de320c87cfe08900eb399`.

The arrival policy reproduces all 72 native actions. Keeping only the highest observed revision changes 24 inputs and 10 choices. It adds 10 optimal choices and 7 physically feasible choices, with no losses. The 72 actor inputs shrink from 9,024 to 8,136 canonical bytes. All gains occur in the reordered-report condition. The transformation cannot repair stale reports when no newer report arrived. The external `my_policy.py` reaches the same 72 choices as the revised-input control without reading any answer data.

## Scheduling

`meeting.jsonl` contains 8 native coordinator decisions, one per trial. These are the first case of each published stratum in [MASBench Scheduling](https://github.com/BUPT-GAMMA/MASBench/tree/c0301a650db95958924bb080192c09fef39389a5/Scheduling), tested with 2 disclosure protocols. The source scenarios are synthetic. The 5 actors have separate private calendars. Only `m1` sees the submitted lists from all actors. The exported leader view matches the exact digest acknowledged at the native action commit, so all 8 have basis `bound`. Four observed actions are deliberate nulls. They are not missing measurements.

See [the native smoke evidence](https://raw.githubusercontent.com/PeterMakhnatch/harbor-multiagent/13a4f305d348424a84c5050a54cb13f2e7e7909d/adapters/masbench-scheduling/results/smoke.json). Reuse the [adapter](https://github.com/PeterMakhnatch/harbor-multiagent/tree/experiments/seatlab-masbench-calendars/adapters/masbench-scheduling) to run your own native trials and export `python export_tape.py --native YOUR_NATIVE_STUDY --out NEW.jsonl`. New runs have new UUIDs and file digests even if the actor inputs and choices match. `study.py audit` is not a substitute for native evidence.

```sh
out=$(mktemp -d)
seatlab replay examples/meeting.jsonl --policy observed --out "$out/native.jsonl"
seatlab replay examples/meeting.jsonl --policy intersection --out "$out/solver.jsonl"
seatlab compare "$out/native.jsonl" "$out/solver.jsonl"
```

SHA-256 of the distributed tape: `3dc7875f13138acf663e19e444ce6989525713c3e837d4d2338c60d6ca809076`. The score distinguishes original reference membership from physical feasibility. A null action does not produce a scheduled time. The calendar data and its derived reports retain their [original MIT license](MASBENCH-LICENSE.txt).