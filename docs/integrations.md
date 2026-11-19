# Where SeatLab fits

Use SeatLab when you can name a decision, what one participant could see, and how to check its action. Use native Harbor when a change can affect another participant's future behavior. Use Eval Lab when you need to connect those trials across jobs and compare task outcomes.

## Connect an Olam task

For a working task, start with the [receipt board](../examples/receipt-board/). The [author guide](authoring.md) shows how the trusted adapter turns a world-accepted actor receipt into a decision and a role episode.

1. Capture the complete role view at the action boundary. A database dump, game log, or final transcript is not that view.
2. Preserve the native trial UUID and distinguish a deliberate null action from an absent action. Store the private reference **outside** the actor view.
3. Write one [decision record](format.md) per decision. Mark exact, acknowledged bytes `bound`. Mark a reconstructed projection `reconstructed`. Run `seatlab describe` on the exported tape.
4. Run the original policy on the tape before changing it. Check the actions, not just the total reward. If you try a new channel, compare its input bytes to another native trial when possible.

Two independent implementations show the boundary. [Dispatch Handoff](https://github.com/PeterMakhnatch/harbor-multiagent/tree/9466ab669a364b26eb47d5d342da1271bbe95aa7/examples/seatlab) exports single decisions from a native shipment batch. Its projections are reconstructed. The [five-seat calendar adapter](https://github.com/PeterMakhnatch/harbor-multiagent/blob/d3e68d3a1103ae469371825c535684f07b586e39/adapters/masbench-scheduling/export_tape.py) exports the leader's complete, acknowledged proposal set. Neither adapter treats an acknowledgment as proof that a model understood a message.

## Join Eval Lab without replacing its Harbor installation

Use the [read-only bridge](https://github.com/PeterMakhnatch/harbor-multiagent/blob/9466ab669a364b26eb47d5d342da1271bbe95aa7/examples/seatlab/query_evallab.py) for the Dispatch Handoff adapter. Use one Python environment for the Olam fork and the existing Eval Lab environment for the census. Do not install one Harbor package over the other. The bridge takes a native dispatch trial or completed panel, not the offline example tapes.

```sh
scratch=$(mktemp -d)
"$EVAL_LAB/.venv/bin/python" "$OLAM/examples/seatlab/query_evallab.py" \
  --evallab-root "$EVAL_LAB" --scratch-dir "$scratch" \
  --trial "$NATIVE_TRIAL" --census-root "$JOB_DIRECTORY"
```

The [retained integration control](https://github.com/PeterMakhnatch/harbor-multiagent/blob/9466ab669a364b26eb47d5d342da1271bbe95aa7/examples/seatlab/baselines.md) has 1 native job, 1 trial and 2 seats. Eval Lab returns 1 shared reward. Neither seat supplies an input token count. The correct aggregate is **0 known values**, not 0 tokens or 2 independent trials. The query uses an in-memory census and a fresh scratch directory. It does not write to a shared catalog.

**Standalone trial control:** In [13 native team trials](https://github.com/PeterMakhnatch/harbor-multiagent/blob/e17621248a1dd54d56565ed35e043b2b960af0d4/examples/seatlab/native-model-results.md#can-stock-eval-lab-read-these-trials), stock `load_trial` returns 13 UUIDs, 26 seat records and 12 known shared scores. The read-only census finds all 13 paths and UUIDs, but projects no rewards because standalone trials lack a native job UUID. The optional bridge keeps missing jobs, trial scores and seat usage separate. It does not invent jobs to complete a join.

## Independent work for contributors

| What you own | Interface | Evidence to return |
| --- | --- | --- |
| A new task adapter | Native artifacts → `seatlab.decision/v1` | A small, redistributable real tape; correct basis and native UUIDs; a documented score and license. |
| A policy or agent harness | One canonical role view on stdin → one JSON action on stdout | Paired results on the same tape, including failures and the exact policy version. |
| A context change | A versioned transformation of the actor view | The original and changed input digests, changed decisions, and a negative control. |
| A runtime contribution | Native trial or seat lifecycle | A separate, minimal runtime branch with a real trial before and after. Do not bundle task policy into Harbor. |

Register case IDs before distributing a campaign. Give each worker disjoint cases and a new output directory. Never reuse another worker's native UUID as a new measurement. This lets task authors, harness authors, and data engineers work independently without sharing an API key or live Docker state.

## Basis for this design

Anthropic's [Project EVE](https://www.anthropic.com/research/project-eve-claude-plays-mmorpgs) explicitly notes that a replayed market cannot react to the agent. The same limitation applies to replaying one seat. Its [multi-agent study](https://www.anthropic.com/research/multiagent-systems) distinguishes parallel work from tasks where later decisions depend on earlier shared work. We use this distinction to test which message arrived, what the receiver could see, and what the receiver actually did. These examples do not reproduce or validate either paper's model results.