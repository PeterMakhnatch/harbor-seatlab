# Where a multi-turn replay must stop

A receiver can answer a recorded request a second time. The sender does not react to that new answer. Use `seatlab prefix` to find the last decision for which the role's original history still applies. Use a **different native trial** to learn what the world actually does next.

A matching sequence of one role's actions is necessary, but not sufficient, to predict the next native input. Another participant can change the world. An unchanged actor may receive a different input and still choose exactly the same action. Only `align` checks a second native trajectory for that case.

## Export one role at a time

An episode is one JSONL record. Use one role and one native trial UUID per record. More than one role can come from the same trial. Keep their histories separate.

| Field | Contract |
| --- | --- |
| `schema`, `id` | `seatlab.episode/v1` and a unique portable identifier |
| `case_sha256`, `role`, `native_trial_id` | Digest of the private task case, role name and canonical native UUID |
| `closed` | `true` only if the native interaction has a recorded end; otherwise `false` |
| `frames` | Nonempty array in native order. Turn numbers start at 1 and have no gaps. |
| `episode_sha256` | SHA-256 of the entire canonical record **without** this field |

Each frame has exactly `turn`, `basis`, `view`, `view_sha256`, and `observed`. `view` is the complete JSON object delivered at this decision. The digest covers its canonical bytes. Use `bound` for an acknowledged view and `reconstructed` for one recovered indirectly. `observed: null` means that no accepted action is known. `observed: {"action": null}` is an accepted null action. Other actions are strings or integers; booleans are not integers here. See [canonical encoding](format.md) before implementing a producer in another language.

The episode has no reference answers, reward table, simulator state or policy command. A digest checks consistency. It does not authenticate the producer or establish that an actor understood an input. Do not publish private role observations without permission.

## Run a different policy

```sh
seatlab prefix "$SOURCE_EPISODES" --policy observed
seatlab prefix "$SOURCE_EPISODES" --exec "$PWD/.venv/bin/python" "$PWD/examples/cursor_policy.py"
seatlab prefix "$SOURCE_EPISODES" --exec "$PWD/.venv/bin/python" "$PWD/examples/cursor_policy.py" --budget-rounds 8
```

By default the trusted executable gets one current view on stdin. With `--history`, it gets `{"view": CURRENT, "history": [{"view": PRIOR, "action": ACCEPTED}, ...]}`. Only earlier matching turns for the **same** role appear. Each turn uses a fresh process. Put `--exec` last. The executable runs with your operating-system permissions; this feature is not a sandbox.

The checker validates every episode before the first policy call. For each episode it stops at the first different action, missing observation, unbound view or policy failure. It never sends the later views of that episode to the policy. `matched_prefix` counts turns before the boundary. `boundary_turn` is 1-based; `withheld_suffix` counts later turns, not the boundary. A complete match on an open episode covers only its recorded prefix. `--policy observed` checks the recording against itself. It is not an independently reproduced decision. No path assigns a reward to an unrealized future.

## Compare with a new native trial

```sh
seatlab align "$SOURCE_EPISODES" "$TARGET_EPISODES" --pairs "$EPISODE_PAIRS"
```

The manifest uses `seatlab.episode-pairs/v1`. Its `pairs` array contains `{id, case_sha256, role, source, target}`. Each arm is either `{native_trial_id, episode_sha256}` or `null` if unavailable. Check the case identifier against the original registration, not just the completed cases. Keep missing arms in the manifest. Never pair a trial with itself.

`align` checks the actual actor input before the action. Different inputs produce `input_changed`, not a policy attribution. An `action_changed` row identifies the first different native action after an equal history and equal current input. Different recorded lengths and open episodes are not evidence of disagreement. The command does not replay a policy or award a team score. Compare the boundary turn and action against the source-only forecast; read any later reward from the target's native verifier.
