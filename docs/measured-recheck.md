# A passing score can hide a changed listener

Harbor measures whether a team completes its task. SeatLab can answer a different release question: **did my policy or context change an observed team behavior we intended to keep?** This example compares a fixed set of 2 native worlds against independently collected candidates. It does not require a Harbor install, Docker, an API key or a model judge.

## Reproduce the 3 controls with one download

Use Python 3.11 or later. Start in a new directory. No installation, Docker, account, or API key is necessary.

```sh
curl -fLsS https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.14.0/seatlab-controls.pyz -o seatlab-controls.pyz
printf '%s  seatlab-controls.pyz\n' b6dbb22bd487a1ef5d8a99547bd142ac65281fac21bce14c883d7996eaac077d | shasum -a 256 -c - && python3 -I -S seatlab-controls.pyz evidence
```

After the download, the program needs no network. It contains the released CLI and 3 pinned native data archives. It recomputes and checks each complete JSON report against the public release. The command refuses to replace an existing `evidence` directory. A successful demo exits `0`; the 3 inner gate exits are `0` (matched), `1` (changed), and `3` (incomplete). The third comparison withholds one binding and creates no trials. See the [interactive field report](https://petermakhnatch.github.io/harbor-seatlab/panel-recheck.html).

## Run the general CLI on the changed candidate

Use Python 3.11 or later. Clone this public repository at `v0.14.0`, or download its source. Start in its root.

```sh
work=$(mktemp -d)
python3 -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --no-deps .
python3 -I -S examples/panel-replication/fetch_controls.py "$work/data"
base="$work/data/source"
candidate="$work/data/changed"
rc=0
"$work/venv/bin/seatlab" study recheck "$base/study.json" "$candidate/study.json" \
  --design "$base/panel-design.json" --candidate-design "$candidate/panel-design.json" \
  --expect-sha256 34b0bfde9b2c4e7a71e2906c9c17ba8d65239defe24cd9bd01575ff888bb0d7d \
  --expect-candidate-sha256 17e326fbd4109b816fb914c6f096730f792299acac2c5091f476f2b96849ba58 \
  --expect-design-sha256 bf74b26265eba2206a4f9e4382ecb8cd8bd342fab666db67bb7d8ee2dfa26611 \
  --expect-candidate-design-sha256 6fade1255de1cfbda0bf2e741523d8af7f7629c0d530a418bd5241c78ab2bc55 > "$work/recheck.json" || rc=$?
printf 'SeatLab exit: %s\n' "$rc"
"$work/venv/bin/python" -c 'import json, sys; print(json.load(open(sys.argv[1]))["counts"])' "$work/recheck.json"
```

Expected exit: **1**. Expected counts: 18 entries, 8 changed, 10 stable and 0 unknown. Exit `1` alone does not establish a change. Require a valid `seatlab.panel-recheck/v1` report with `status: changed`. A missing report is an execution failure. The downloader checks each archive before unpacking it. The analysis itself runs offline and imports no code from the evidence. The `unchanged` directory provides the independently collected Linux negative control. Its candidate study pin is `f69b4276ad27a50a14ab80dd67e832907048425b92ddb9d0317a1233a7cc51ce` and its design pin is the same as the changed control's.

## What actually ran

The source contains 5 programmed teams on each of 12 world seeds. The source selected seeds **101 and 102** before either candidate ran. Each candidate then ran 5 teams on each selected seed. Across the source and 2 candidates there are 80 distinct native trial UUIDs. The baseline has 60, not 96. Its 96 role comparisons reuse physical trials.

| Observed result | Unchanged code, GitHub Linux | Blind listener changes its fixed target |
| --- | ---: | ---: |
| Native Harbor completions | 10 / 10 | 10 / 10 |
| Accepted actions equal to the corresponding source team | 400 / 400 | 392 / 400 |
| Acknowledged private role views equal to source | 400 / 400 | 369 / 400 |
| Stable registered case-by-criterion entries | 18 / 18 | 10 / 18 |
| Changed entries | 0 / 18 | 8 / 18 |

The blind listener now differs from the informed listener on seed 101, and matches on 102. The other 4 team definitions reproduce every original action and view. Four overlapping criteria switch on each seed. These are **2 changed worlds**, not 8 independent failures. Identical categories do not imply that every underlying action matches on a new task. We checked the native tapes separately here.

The [Linux run](https://github.com/PeterMakhnatch/harbor-seatlab/actions/runs/44549885787) collected new physical trials. The [changed-rule registration](https://github.com/PeterMakhnatch/harbor-multiagent/blob/ee0127b2c1/adapters/pettingzoo-seats/communication-experiment/panel-change-protocol.md) predates the intervention; its [native results](https://github.com/PeterMakhnatch/harbor-multiagent/blob/34dc0e8b24/adapters/pettingzoo-seats/communication-experiment/panel-change-results.md) record what happened. The [data release](https://github.com/PeterMakhnatch/harbor-seatlab/releases/tag/panel-control-data-v1) contains both candidates. An additional CI control deliberately withholds one binding in an otherwise complete design; the exit becomes `3`, with 17 stable and 1 unknown. It is not another set of native trials.

## Use this in a real repository

Set 4 trusted pins outside the submitted data: the source and candidate study pins and their respective design pins. Stage the two bundles under your checkout. Use `PeterMakhnatch/harbor-seatlab/actions/panel-recheck` at a reviewed commit. Do not set `continue-on-error` in a real gate. A changed or incomplete panel fails. Invalid inputs fail without publishing a report. The [workflow](../.github/workflows/measured-controls.yml) intentionally captures expected failures to check each exit. The [command contract](panel-recheck.md) explains missing cases, unknown evidence, and new native UUIDs. See the [smaller panel](measured-panel.md) for how the source selected the cases.

An [independent consumer repository](https://github.com/PeterMakhnatch/harbor-multiagent/actions/runs/44553270796) runs the published action. The [stock Eval Lab join](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.14.0/positive-stock.json) resolves all 10 changed-candidate trial IDs. It counts 20 role executions, but only 10 shared rewards. All 10 rewards are 1.
