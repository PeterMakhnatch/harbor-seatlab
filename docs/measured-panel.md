# Recompute the measured 2-world regression panel

Run 60 native teams once to learn which registered behaviors your worlds actually exercise. Keep all 12 cases for evaluation. Use the smaller panel only for a targeted repeat of the same 9 behaviors. This worked example uses 5 fixed speaker–listener teams on each of 12 new worlds. All 60 teams receive the same Harbor completion reward.

The blind listener ignores symbols. It makes every informed move on 5 worlds and differs on 7. A single world cannot test both observations. The exact panel is seeds **101 and 102**; one witnesses each. Thirty-five of the 66 two-seed choices cover all 9 criteria. The tool chooses the first minimum by case label. Repeating 5 teams on each selected seed requires 10 native trials, not 60. This is a trial-count comparison, not measured speed or quality.

## Try it without Docker or a model key

```sh
work=$(mktemp -d)
base=https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.13.0
python3 -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --no-deps "$base/harbor_seatlab-0.13.0-py3-none-any.whl"
curl -fsSL "$base/native-speaker-listener-holdout.tar.gz" -o "$work/native.tgz"
(cd "$work" && echo '5212d12fa6b1a57177abd6203e6a5d7fde66f4a923edc8902bc834811ce1060d  native.tgz' | shasum -a 256 -c -)
tar -xzf "$work/native.tgz" -C "$work"
pin=34b0bfde9b2c4e7a71e2906c9c17ba8d65239defe24cd9bd01575ff888bb0d7d
"$work/venv/bin/seatlab" study panel "$work/study.json" --design "$work/panel-design.json" --expect-sha256 "$pin" --html "$work/panel.html" > "$work/panel.json"
"$work/venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["selection"])' "$work/panel.json"
```

The release archive binds the design, native terminal outcomes, forecast, and stock Eval Lab summary. The study pin independently binds all 96 role comparisons and their 120 input files. The panel adds the canonical design digest to its output. Neither digest proves who originally ran a game. The complete study contains 60 distinct trial UUIDs and 120 participant executions. Its 96 comparisons reuse those trials.

| Criterion on one native world | Worlds with a witness | Registered worlds |
| --- | ---: | ---: |
| Blind listener takes identical moves / has identical physical inputs | 5 | 12 |
| Blind listener takes different moves / has different physical inputs | 7 | 12 |
| Matched rotations preserve listener moves / preserve physical inputs / change incoming signals | 12 each | 12 |
| Recorded moves differ with only the encoder or only the decoder changed | 12 each | 12 |

Each slash separates distinct, counted criteria. The comparison observes movements in 2 real runs; it does not identify a cause. Repeating a selected panel on a new platform tests repeatability on the same cases. It does not validate new cases, actors or world distributions. See the [schema and limits](coverage-panels.md), [prospective forecast](https://github.com/PeterMakhnatch/harbor-multiagent/blob/11905583a7a6a759f3469f6825f78c9f0c588bac/adapters/pettingzoo-seats/communication-experiment/held-out-forecast.json) and [native results](https://github.com/PeterMakhnatch/harbor-multiagent/tree/experiment/held-out-communication/adapters/pettingzoo-seats/communication-experiment).
