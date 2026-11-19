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

License: [Apache-2.0](LICENSE). This project is independent of Olam Labs and the upstream Harbor project.