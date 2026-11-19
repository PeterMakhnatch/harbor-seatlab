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

## Interactive scheduling

`cursor-source.jsonl` contains the native coordinator histories for cases `2/1` and `5/1`. They come from the [interactive calendar study](https://github.com/PeterMakhnatch/harbor-multiagent/blob/71f81e5a1634caa31cba4b650c90f53787fe5113/adapters/masbench-scheduling/interactive-results.md). Each of the 5 participants holds its own calendar. The coordinator receives a new set of replies after each accepted query. The [episode exporter](https://github.com/PeterMakhnatch/harbor-multiagent/blob/71f81e5a1634caa31cba4b650c90f53787fe5113/adapters/masbench-scheduling/export_episodes.py) checks each view acknowledgment and action against the retained native history. The source file SHA-256 is `e96d3352a027b02133e765616bf0c9cdb2edf0934db22e1a13a64e3dc5fa0bfc`.

`cursor_policy.py` is a separate policy implementation. Without `--budget-rounds`, it continues until it finds a common time or proves none remains. With `--budget-rounds 8`, it stops if the first 8 exchanges do not resolve the meeting. This is a budget on exchanges, not on tokens or seconds. The bundled material retains the [MASBench MIT notice](MASBENCH-LICENSE.txt).

`cursor-target.jsonl` contains separate native trials for those same 2 cases under the eight-round budget. `cursor-pairs.json` binds both arms by case, role, independent trial UUID and episode digest. The target file SHA-256 is `de9748fac4d5901dc830a64f0a3d212e3b9b55316ff3c7e1d00683d2cc1ec9ab`. The [registered forecast and 24-pair result](https://github.com/PeterMakhnatch/harbor-multiagent/blob/experiments/seatlab-cursor-budget/adapters/masbench-scheduling/prefix-results.md) distinguish a replay stopping at the boundary from a genuine target outcome.