# SeatLab

**Replay one agent's decision with different context, without rerunning the team.** SeatLab is a standard-library Python CLI. It does not need Harbor, Docker, an API key, or a running model. Native worlds produce evidence. SeatLab asks a separate question: what does a policy do when you change only the input it receives?

## Try it on real decisions

Use Python 3.11 or later. The example contains observations and choices from 6 native Harbor trials, not hand-written answers.

```sh
git clone https://github.com/PeterMakhnatch/harbor-seatlab.git
cd harbor-seatlab
python3 -m venv .venv
.venv/bin/python -m pip install .
run=$(mktemp -d)
.venv/bin/seatlab describe examples/dispatch.jsonl
.venv/bin/seatlab replay examples/dispatch.jsonl --policy arrival --out "$run/raw.jsonl"
.venv/bin/seatlab replay examples/dispatch.jsonl --policy arrival --transform latest --out "$run/latest.jsonl"
.venv/bin/seatlab compare "$run/raw.jsonl" "$run/latest.jsonl"
```

The inventory agent reports which bays are available. The dispatch agent sees utilities and a sequence of reports, not the true availability. `arrival` uses the last report it sees. `latest` keeps the report with the highest explicit revision. Compare the intervention against `--policy observed` to include actual retained native actions rather than another scripted decision. SeatLab never labels a new replay as a new native trial.

For the source, see the [native experiment and data](https://github.com/PeterMakhnatch/harbor-multiagent/tree/experiments/seatlab-handoff/examples/seatlab). The [second task](https://github.com/PeterMakhnatch/harbor-multiagent/tree/experiments/seatlab-masbench-calendars/adapters/masbench-scheduling) ports 200 published five-person scheduling scenarios to Olam. That task distinguishes physical feasibility from exact membership in the publisher's answer key. [Olam Harbor](https://github.com/olamlabs/harbor-multiagent) still owns the trial lifecycle and reward.

## Check a replay against another real trial

Can you predict what a different team run will actually do? The source coordinator receives full availability lists. In a separate native run, the same 5 actors report only their first 8 times. Predict the second run from the first. The prediction never reads the second run.

```sh
.venv/bin/seatlab replay examples/meeting-sources.jsonl --policy intersection \
  --transform-file examples/meeting-first8.json --out "$run/predicted.jsonl"
.venv/bin/seatlab calibrate examples/meeting.jsonl "$run/predicted.jsonl" \
  --pairs examples/meeting-pairs.json --transform-file examples/meeting-first8.json \
  --html "$run/report.html"
```

Open `report.html` in the output directory to inspect each native pair. The file needs no server. The same command still prints machine-readable JSON.

On these 4 cases, all 4 native actions change from a time to an explicit null. The replay reproduces all 4 target inputs, actions, and scores. The unchanged-input control reproduces none. This tests a deterministic channel change, not a model's general ability. See the [protocol and negative controls](docs/native-calibration.md).

## Find where interactive replay stops

Once a policy changes an action, its team may not send the same next message. Test the candidate on the common history, then compare a different native run. SeatLab does not assign a reward to the unseen future.

```sh
.venv/bin/seatlab prefix examples/cursor-source.jsonl \
  --exec "$PWD/.venv/bin/python" "$PWD/examples/cursor_policy.py" --budget-rounds 8
.venv/bin/seatlab align examples/cursor-source.jsonl examples/cursor-target.jsonl \
  --pairs examples/cursor-pairs.json
```

In case `2/1`, both native coordinators receive the same input at decision 9. One asks for the next reply; the other stops. In case `5/1`, all 5 decisions match. Across the [24 registered pairs](https://github.com/PeterMakhnatch/harbor-multiagent/blob/experiments/seatlab-cursor-budget/adapters/masbench-scheduling/prefix-results.md), all 20 forecast changes and 4 complete matches agree with fresh native trials. See [the episode format](docs/episodes.md).

## Bring a policy

Use `--exec` only with an executable **you trust**. SeatLab is not a sandbox. The process has your operating-system permissions. The harness passes only the canonical role view on standard input. It does not pass the observed action, reference answers, run identity, CLI environment credentials, or source path as policy input. Executables do not run implicitly.

```sh
.venv/bin/seatlab replay examples/dispatch.jsonl --out "$run/mine.jsonl" \
  --exec "$PWD/.venv/bin/python" "$PWD/examples/my_policy.py"
.venv/bin/seatlab compare "$run/raw.jsonl" "$run/mine.jsonl"
```

Write one `{"action": ...}` JSON object to stdout. SeatLab records an unmeasured decision if the executable times out or returns an invalid action. It does not turn a tool failure into a score of zero. A completed scheduling decision with `action: null` is distinct from an unknown action.

## What belongs where

| Job | Owner |
| --- | --- |
| Give each participant its private world view, commit an action, verify the shared result | The Harbor task |
| Export a task's safe observations and authoritative offline answers | A task adapter |
| Run local policies on those observations, compare interventions and count missing outcomes | SeatLab |
| Join native jobs, trial UUIDs, and per-seat results into a larger evaluation | Eval Lab |

Use [the data contract](docs/format.md) to add an adapter. Use [the evidence note](examples/README.md) to regenerate examples and understand their limits. Recording the same SHA-256 twice is not independent attestation. A client acknowledgment is not proof of understanding. These tools do not detect cheating or claim to explain a model's private reasoning.

For a real Eval Lab query, native source branches, and separate contributor roles, see [where SeatLab fits](docs/integrations.md).

License: [Apache-2.0](LICENSE). This project is independent of Olam Labs and the upstream Harbor project.