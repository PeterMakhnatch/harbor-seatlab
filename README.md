# SeatLab

**Find the first decision that changes when you change one agent's context or partner.** SeatLab compares what each participant could see and what it actually did. It can replay a decision offline or compare two real team runs. The CLI uses the Python standard library. It needs no model, API key, Docker, or Harbor installation to analyze a recording.

**[Open the live evidence gallery](https://petermakhnatch.github.io/harbor-seatlab/).** See why 4 Connect Four teams get the same payoff after different moves. Compare their decisions, then try the 200-case calendar study. No account or download is required. The [offline guide](docs/evidence.md) explains how to verify the underlying records.

**[Try a real policy regression in 5 minutes](docs/measured-recheck.md).** All 10 Harbor trials complete after one listener rule changes. SeatLab flags 8 of 18 registered behavior checks. Independently repeating unchanged code on Linux flags none. Download the public tapes or [use the offline CI gate](actions/panel-recheck/action.yml).

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

The [complete experiment](https://github.com/PeterMakhnatch/harbor-multiagent/blob/b5f7a33511775604f9507e64acb37f11e23bbcab/adapters/masbench-scheduling/cohort-results.md) repeats this on 200 published cases with 400 independent native trials. The projected input, action and score matched 200/200 target trials. The team found the published earliest time on only 15/200 targets. Reproducing a decision does not make it a good decision.

## Find where interactive replay stops

Once a policy changes an action, its team may not send the same next message. Test the candidate on the common history, then compare a different native run. SeatLab does not assign a reward to the unseen future.

```sh
.venv/bin/seatlab prefix examples/cursor-source.jsonl \
  --exec "$PWD/.venv/bin/python" "$PWD/examples/cursor_policy.py" --budget-rounds 8
.venv/bin/seatlab align examples/cursor-source.jsonl examples/cursor-target.jsonl \
  --pairs examples/cursor-pairs.json
```

In case `2/1`, both native coordinators receive the same input at decision 9. One asks for the next reply; the other stops. In case `5/1`, all 5 decisions match. Across the [24 registered pairs](https://github.com/PeterMakhnatch/harbor-multiagent/blob/experiments/seatlab-cursor-budget/adapters/masbench-scheduling/prefix-results.md), all 20 forecast changes and 4 complete matches agree with fresh native trials. See [the episode format](docs/episodes.md).

If the world records which roles act together, use [team alignment](docs/world-order.md). It finds the first changed decision step across the whole team. A kitchen can accept 2 moves at once. A card game can accept 1 move at a time. Sorting actor logs by time is not a substitute for either rule.

To compare a role after the team's first change, use [role trajectories](docs/role-trajectory.md). The command counts changed inputs, selected observation fields, and accepted actions separately. It pairs only matching native schedules. On 3 speaker–listener seeds, a shared symbol rotation changed the messages but none of the listener's 60 moves.

## Bring a policy

Use `--exec` only with an executable **you trust**. SeatLab is not a sandbox. The process has your operating-system permissions. The harness passes only the canonical role view on standard input. It does not pass the observed action, reference answers, run identity, CLI environment credentials, or source path as policy input. Executables do not run implicitly.

```sh
.venv/bin/seatlab replay examples/dispatch.jsonl --out "$run/mine.jsonl" \
  --exec "$PWD/.venv/bin/python" "$PWD/examples/my_policy.py"
.venv/bin/seatlab compare "$run/raw.jsonl" "$run/mine.jsonl"
```

Write one `{"action": ...}` JSON object to stdout. SeatLab records an unmeasured decision if the executable times out or returns an invalid action. It does not turn a tool failure into a score of zero. A completed scheduling decision with `action: null` is distinct from an unknown action.

## Which context fields can a role lose?

Run `seatlab context-audit examples/cursor-source.jsonl --field /round`. Keeping only the round number makes at least 4 of the 29 recorded actions impossible to reproduce with a deterministic, memoryless policy. The full views have no contradictions, but none repeat. That is not proof of generalization. Supply `--heldout OTHER.jsonl` to measure matches, disagreements, unseen inputs, and ties on different native cases. The [context audit guide](docs/context-audit.md) explains the bound and when to run a new native trial.

## Give independent workers separate cases

Use `seatlab partition --views-only` to give each worker only projected actor views and opaque request IDs. Workers run `seatlab act` and return actions, not scores. The coordinator keeps the original tape and grades the actions with `seatlab merge`. Missing, repeated, or incompatible receipts prevent publication. The output uses source order, not worker completion order. The [2-worker example](docs/parallel-replay.md) also covers trusted workers that receive full decision tapes.

## Record your own Harbor task

The [receipt board](examples/receipt-board/) is a complete 2-seat Olam task. Run 2 stock trials, export the views the actors acknowledged, and check a detached policy against the second trial. The [author guide](docs/authoring.md) shows the 3 Python helpers. The offline path needs no Harbor installation. The native path uses Docker and no model key.

## Verify someone else's study offline

Run `seatlab study verify study.json --expect-sha256 "$STUDY_SHA256" --html report.html`. It checks the input bytes, recomputes every registered report and writes an offline summary you can open in a browser. It runs no code from the study. The [portable study guide](docs/portable-studies.md) shows how to publish one. A verified report can still contain a failed policy or missing evidence.

For continuous integration, use the [pinned GitHub Action](docs/github-action.md). It verifies the same study from another repository. The consumer example tests a valid pin and an expected refusal against the same real Harbor job.

Use [`seatlab study relations`](docs/study-relations.md) to join those comparisons to Eval Lab by native trial UUID. The [measured job example](examples/eval-lab/measured.md) keeps 4 role executions separate from 2 shared rewards. It needs no game rerun.

## Choose a short native regression run

Use [`seatlab study panel`](docs/coverage-panels.md) when running every team on every world is too expensive. Name the observed behaviors the short run must exercise. SeatLab verifies the source study, then selects the fewest recorded worlds that cover those behaviors. It reports what no world demonstrated and what the recordings could not measure.

In the [12-world worked example](docs/measured-panel.md), a listener that ignores messages matches all informed moves on 5 worlds and differs on 7. Only 2 worlds are needed to exercise all 9 specified behaviors. A repeat of all 5 teams then needs 10 native trials rather than 60. Use the full cohort for performance estimates; the selected panel is a regression check.

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