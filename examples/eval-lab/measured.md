# Join a real Harbor job without multiplying its reward

A study relation is a comparison, not a participant or a trial. This example reproduces an actual stock Harbor job from [two recorded native trials](https://github.com/PeterMakhnatch/harbor-multiagent/blob/5da42e965f2f8f491badfa3009bac5ab9f1c8a6a/examples/seatlab/receipt-job-results.md). Both roles change input on their first turn. The team still receives reward 1 in both trials. Eval Lab reads 1 job, 2 trial UUIDs and 4 role executions. It reads **2 shared rewards**, not 4. It finds no token measurements. Unknown usage is not zero usage.

## Try the published records

Use Python 3.11 or later and a stock [Eval Lab checkout](https://github.com/PeterMakhnatch/eval-lab/tree/b9dd192458c88f1fa6b898be292a7eb94fe15a96) with `evallab.results` installed. Run the following from this SeatLab checkout. `join.py` does not write into Eval Lab or its database.

```sh
work=$(mktemp -d)
release=https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.11.0
python3 -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --no-deps "$release/harbor_seatlab-0.11.0-py3-none-any.whl"
curl -fsSL "$release/native-job-join.tar.gz" -o "$work/job.tar.gz"
(cd "$work" && echo '5e42706b7a2ded1d212df819635ce1202609a09560f2f08a7c362e7975171453  job.tar.gz' | shasum -a 256 -c -)
tar -xzf "$work/job.tar.gz" -C "$work"
pin=f289c8c89a12bcb8c25753a8b3f5a5ac389825baceba874921e6e2587556110d
"$work/venv/bin/seatlab" study relations "$work/study.json" --expect-sha256 "$pin" --out "$work/relations.jsonl"
"$EVAL_LAB/.venv/bin/python" examples/eval-lab/join.py "$work/relations.jsonl" \
  --expect-study-sha256 "$pin" --trials-dir "$work/native" > "$work/join.json"
cmp "$work/relations.jsonl" "$work/derived/relations.jsonl"
cmp "$work/join.json" "$work/derived/join.json"
```

`native/` contains only allowlisted fields from the original `result.json` files. It excludes configuration paths, prompts, agent internals and host logs. These projections reproduce the stock reader's UUIDs, participants, recorded rewards and missing usage. They are not full trial directories for rerunning or regrading the game. The archive digest covers the projections. The study pin independently covers its 3 inputs and comparison report. Neither digest authenticates the original publisher.

## How the join behaves on harder records

| Measured dataset | Relation rows | Referenced native IDs | IDs found | Known shared rewards | Role executions found |
| --- | ---: | ---: | ---: | ---: | ---: |
| Two-role native job | 2 | 2 | 2 | 2 | 4 |
| Same job, target directory withheld | 2 | 2 | 1 | 1 | 2 |
| Five-role calendar dataset | 400 | 400 | 400 | 400 | 2,000 |
| Multi-world PettingZoo study | 13 | 16 | 13 | 13 | 28 |

Withholding the target changes both job comparisons to `unknown`. The reward of the one remaining trial does not complete either comparison. In the calendar study, 2 checks reuse the same 200 pairs. In each check 185 pairs have different native rewards and 15 have equal rewards. They are still just 200 distinct case pairs. The 400 native trials have 215 recorded passes. In the PettingZoo study, the stock reader does not receive raw native result directories for the 3 Linux trials. All 3 corresponding reward comparisons remain unknown, even though the separate SeatLab study contains their decision evidence. Game payoffs are not Harbor completion rewards.

See [the measured machine-readable summaries](results.json) and [the join contract](README.md).