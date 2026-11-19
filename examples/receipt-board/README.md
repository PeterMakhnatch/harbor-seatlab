# Receipt board example

A minimal two-seat Harbor task (`p1`, `p2`) that records real actor view receipts and exports them into portable SeatLab tapes using `seatlab.author`.

- **Source arm (`full`)**: `p1` sees `["amber", "cobalt"]`; `p2` sees `["mint", "coral"]`.
- **Target arm (`first`)**: `p1` sees `["amber"]`; `p2` sees `["mint"]`.
- **Oracle and replay policy**: choose the last visible option (`options[-1]`).
- **Native verifier vs offline seat grade**: `task/tests/test.sh` writes one shared team reward (`1` if both seats leave a record and submit any permitted option with a valid receipt, `0` otherwise). `export.py` keeps each trial's shared `team_reward` in `summary.json` and grades each seat's choice offline in `answers` (last full option is `optimal: true`; both original options are `feasible: true`). See [protocol.md](protocol.md) for hypotheses and scope limits.

## Prerequisites

- **Offline path:** Python 3.11 or later and the SeatLab source checkout. No Docker, Harbor installation or model key.
- **Native path:** Docker and Compose, `jq`, and `uv` 0.13.5 or later. Use Python 3.12 for the pinned Olam runtime. Its environment stays separate from SeatLab.

Install SeatLab in its own environment:

```sh
git clone https://github.com/PeterMakhnatch/harbor-seatlab.git
cd harbor-seatlab
python3 -m venv .venv
.venv/bin/python -m pip install .
```

## Path A: Replay-only from the published portable bundle (no Docker)

Download the published native evidence into a fresh directory. The exported files are at the archive root.

```sh
work=$(mktemp -d)
curl -fL https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.7.0/native-receipt-board.tar.gz \
  -o "$work/native-receipt-board.tar.gz"
mkdir "$work/bundle"
tar -xzf "$work/native-receipt-board.tar.gz" -C "$work/bundle"
```

Continue directly to [Replay with detached view-only workers](#replay-with-detached-view-only-workers).

## Path B: Run two fresh native Harbor trials, then export

1. From the SeatLab checkout, install the pinned stock Olam runtime in a separate directory:

```sh
work=$(mktemp -d)
git clone https://github.com/olamlabs/harbor-multiagent.git "$work/olam"
git -C "$work/olam" checkout 6aeb609e31fd77e5f89ce3bcb018db8045520fb7
uv sync --project "$work/olam" --locked --no-dev --python 3.12
```

2. From your `harbor-seatlab` checkout, generate two stock Harbor configs in a fresh directory (`OUT` must not exist yet):

```sh
.venv/bin/python examples/receipt-board/prepare.py "$work/native"
```

`prepare.py` writes `$work/native/full.json`, `$work/native/first.json`, and `$work/native/plan.json` (mapping `"full"` and `"first"` to the two absolute native trial folders) and prints both stock Harbor commands.

3. Run both trials using Harbor's environment (requires Docker):

```sh
"$work/olam/.venv/bin/harbor" trial start -c "$work/native/full.json"
"$work/olam/.venv/bin/harbor" trial start -c "$work/native/first.json"
```

4. Export both completed native trial folders using SeatLab's environment (no Harbor import, no Docker):

```sh
source_trial=$(jq -r .full "$work/native/plan.json")
target_trial=$(jq -r .first "$work/native/plan.json")
.venv/bin/python examples/receipt-board/export.py \
  "$source_trial" "$target_trial" --out "$work/bundle"
```

`export.py` reads `result.json`, optional verifier reward files, and `artifacts/logs/world/receipts.jsonl`. For each role it calls `seatlab.author.accepted_frame` with the receipt's actual `acknowledged_sha256` (never recomputed by the adapter), then `decision_from_frame` and `episode_from_frames`. On success it atomically creates `$work/bundle` containing `sources.jsonl`, `decisions.jsonl`, `source-episodes.jsonl`, `target-episodes.jsonl`, `pairs.json`, `episode-pairs.json`, and `summary.json`.

## Replay with detached view-only workers

From your `harbor-seatlab` checkout (zero Docker), partition `$work/bundle/sources.jsonl` into view-only worker requests, run detached workers with `policy.py`, merge and score centrally, and calibrate against the separately recorded native target trial:

```sh
namespace=$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(16))')
.venv/bin/seatlab partition "$work/bundle/sources.jsonl" \
  --policy external --shards 2 --views-only "$namespace" \
  --transform-file examples/receipt-board/first.json \
  --out "$work/plan"

for n in 1 2; do
  mkdir "$work/worker-$n"
  cp "$work/plan/part-000$n.jsonl" "$work/worker-$n/requests.jsonl"
done

policy_py="$PWD/examples/receipt-board/policy.py"
py="$PWD/.venv/bin/python"
cli="$PWD/.venv/bin/seatlab"
(cd "$work/worker-1" && "$cli" act requests.jsonl \
  --out actions.jsonl --exec "$py" "$policy_py") &
a=$!
(cd "$work/worker-2" && "$cli" act requests.jsonl \
  --out actions.jsonl --exec "$py" "$policy_py") &
b=$!
wait "$a" && wait "$b"

.venv/bin/seatlab merge --plan "$work/plan/plan.json" \
  --tape "$work/bundle/sources.jsonl" \
  --out "$work/merged.jsonl" \
  "$work/worker-2/actions.jsonl" "$work/worker-1/actions.jsonl"

.venv/bin/seatlab calibrate "$work/bundle/decisions.jsonl" "$work/merged.jsonl" \
  --pairs "$work/bundle/pairs.json" \
  --transform-file examples/receipt-board/first.json
```

Workers receive only `requests.jsonl` (opaque `request_id` and transformed `view`), never `answers`, recorded native actions, or `plan.json`. Put `--exec` last and run only executables you trust.

### What to inspect in the outputs

- **Strict calibration vs `external_observations`**: Because `policy: "external"` is never re-executed by `calibrate`, strict pair statuses stay `"opaque"` and strict `counts.reproduced` stays `0`. Independently, `external_observations` (and each pair's `external_observation` with `"execution_attested": false`) checks derived input digests against the target's acknowledged views and compares logged actions and offline scores. If both native trials ran the oracle policy and the `first.json` transform matches the target views, `external_observations` should show matching inputs, actions, and scores while strict `counts.reproduced` remains `0`.
- **Unchanged-input negative control**: Running the same replay without `--transform-file examples/receipt-board/first.json` keeps full two-option views. Under that control, target input digests differ, so `external_observations.input_matches` and `external_observations.actions_compared` stay `0`.
- **Team reward vs seat grade**: Inspect `$work/bundle/summary.json` for each trial's shared native `team_reward` alongside its `native_trial_id`. Under the task verifier, both arms can earn `team_reward: 1` even when removing the second option changes both seats' actions from `optimal: true` to `optimal: false` offline.
- **Role-local episodes**:

```sh
.venv/bin/seatlab prefix "$work/bundle/source-episodes.jsonl" \
  --exec "$py" "$policy_py"
.venv/bin/seatlab align "$work/bundle/source-episodes.jsonl" \
  "$work/bundle/target-episodes.jsonl" \
  --pairs "$work/bundle/episode-pairs.json" --input-paths 8
```

For full adapter conventions and API contracts, see [docs/authoring.md](../../docs/authoring.md).
