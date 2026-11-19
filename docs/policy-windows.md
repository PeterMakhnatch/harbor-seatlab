# What context did the policy receive?

A native episode records the world observation and the accepted action. It does not record the bytes the executable received. `verify-windows` joins those episodes to the wrapper's context receipts. A mismatch is an error. The command does not invent a missing trial, role, action, or history item.

```sh
seatlab verify-windows "$EPISODES" "$RECEIPTS"
seatlab verify-windows "$EPISODES" "$RECEIPTS" --out "$POLICY_EPISODES"
seatlab context-audit "$POLICY_EPISODES" --field /current
seatlab context-audit "$POLICY_EPISODES" --field /history
```

The first command prints a verification report. `--out` also writes a new episode file. It does not replace an existing path. `context-audit` then asks whether that policy context can distinguish the recorded actions. Audit the original episodes separately when the question is about the raw world observation.

## Actor envelope

The policy input is one JSON object. It has exactly these fields:

| Field | Contract |
| --- | --- |
| `schema` | `seatlab.actor-window/v1` |
| `current` | The raw private view for this decision. |
| `history` | Prior turns, oldest to newest. Each item is `{"view": RAW, "action": ACCEPTED}`. |

`history` contains at most the declared depth. Depth is an integer from 0 through 16. Depth 0 has an empty history. A history item is the prior raw view and that turn's accepted action. It is not a previous policy envelope. Do not nest `seatlab.actor-window/v1` inside `history`.

The input has no future frame. It has no native UUID, case ID, turn number, world step, or clock. It has no other role's history and no target result. An explicit null action is recorded as `null`. A missing action is not a null action.

Count the canonical ASCII JSON bytes. Exclude the single framing newline. The encoding is the same compact form as a [decision digest](format.md). These are bytes, not tokens or dollars.

## Receipts

Provide one receipt file for the episodes you want to check. Each receipt belongs to one native trial UUID, one role, and one depth. It records the context the trusted wrapper sent and the action the executable returned.

Join every receipt to the native episode for that UUID and role. Require each native role row once. Reject a missing receipt, an extra receipt, a reconstructed view, a bad digest, or a history that does not match the accepted actions. Do not fill a gap.

The wrapper is trusted operator code. A matching receipt shows what that wrapper claims it sent. It is not proof of origin. It is not proof that the actor read a field. It is not proof that the world accepted the action. The native episode remains the authority for identity, observation, and acceptance.

## Published episodes

`--out` writes role episodes for the same native trial and role. It does not create a new trial. Each frame view is the verified actor envelope, not the raw world view. The raw observation remains available only as `current` inside that envelope, and as the original episode.

Use the published file with `context-audit`:

```sh
seatlab context-audit "$POLICY_EPISODES" --field /current --field /history
seatlab context-audit "$POLICY_EPISODES" --no-fields
```

`/current` selects the raw view inside the envelope. `/history` selects the prior window. A conflict there is about the policy context. It is not a claim about team reward or about a field the executable never received.

## Report

A successful report uses `seatlab.window-verification/v1` and `status: verified`. `counts` has exactly `native_trials`, `role_episodes`, `verified_decisions`, and `context_bytes`. `seats` separates each native trial and role. It includes that seat's depth, decision count, and context bytes. It does not include raw views or actions.

| Exit | Meaning |
| --- | --- |
| `0` | Every declared receipt matches its native episode. |
| `2` | Input is unreadable, malformed, or inconsistent, or `--out` already exists. |

No partial `--out` file is published after a failure.
