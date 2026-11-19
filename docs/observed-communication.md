# Recompute a listener's physical and communication history

The archived study compares observed native roles, not alternate simulated futures. It reuses the 15 original Olam speaker–listener trials. The 37 checks are not 37 more native trials. Every comparison stops before an incompatible world schedule and never infers a cause.

The 15 trials are 5 teams on each of 3 fixed seeds. For each of 12 baseline/partner pairs the recipe registers 3 separate observations: the listener's 8 physical fields, its 3 incoming channel fields, and the speaker's unmodified view. A final check requests a field that neither listener recorded. All 37 results reproduce from **30 files**: one role episode tape and one authoritative order per native trial.

| Measured comparison | Observed result |
| --- | --- |
| Both partners rotate the meaning of all 3 symbols | On all 3 seeds the first different speaker action is at world step 1. The first different listener channel is at step 2. |
| Listener's 8 physical fields under the same matched rotation | Equal on all 60 paired listener decisions. All 60 accepted moves are equal too. |
| Only one partner rotates its convention | In 6 of 6 comparisons a listener move later differs. The physical input first differs on the next world step. |
| An absent listener field on seed 5 | The selected input is unknown on all 20 steps. All 20 observed actions are still known and equal. The relation for `native_action_changed` is `false`, not `null`. |

A difference in message bytes does not imply a difference in movement. Matching movement alone does not establish communication. A missing observation does not erase an action that the native trial actually recorded. Do not combine counts across overlapping checks.

## Run it offline

Use Python 3.11 or later. The initial downloads need network access. Neither the install nor the study uses a model or a running Harbor service.

```sh
work=$(mktemp -d)
base=https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.12.0
python3 -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --no-deps "$base/harbor_seatlab-0.12.0-py3-none-any.whl"
curl -fsSL "$base/native-role-trajectories.tar.gz" -o "$work/study.tgz"
(cd "$work" && echo '4b0ae0ae253dc19e64552c886a5759b7cfd0000eaafde86c611a47c258f6bb6f  study.tgz' | shasum -a 256 -c -)
tar -xzf "$work/study.tgz" -C "$work"
pin=ddb733a7df677d2371b99050ab3337f03fbb88a5d628b4d6f1ef2b4cd9c761a3
"$work/venv/bin/seatlab" study verify "$work/study.json" --expect-sha256 "$pin" --html "$work/report.html"
"$work/venv/bin/seatlab" study relations "$work/study.json" --expect-sha256 "$pin" --out "$work/new.jsonl"
cmp "$work/new.jsonl" "$work/derived/relations.jsonl"
```

Open `report.html` offline. Expand `physical-s5-plain-plain-vs-s5-rotate-rotate`. Its full listener input differs on 19 of 20 steps. The selected physical inputs and accepted moves do not differ. Compare with `missing-listener-pointer` before interpreting a zero count.

[Public report](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.12.0/role-trajectories.html) · [Recipe guide](trajectory-studies.md) · [Original native results and independent simulation](https://github.com/PeterMakhnatch/harbor-multiagent/blob/d191c83d4bfce200e1302766601b1d7f0a1056a7/adapters/pettingzoo-seats/communication-experiment/results.md).
