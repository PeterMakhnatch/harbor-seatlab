# Export native Harbor trials to SeatLab

Use SeatLab's Python recording helpers to turn completed native Harbor trials into validated decision tapes and role-local episodes. Keep the native Harbor runtime and SeatLab in separate Python environments. SeatLab uses only the Python standard library and never imports Harbor or runs Docker.

## Public recording API

Import the three recording helpers from `seatlab.author`, together with canonical encoding, hashing, and atomic publication from `seatlab.tape`:

```python
from pathlib import Path
from seatlab.author import accepted_frame, decision_from_frame, episode_from_frames
from seatlab.tape import canonical, decision_sha256, digest, write_lines
```

| Helper | Purpose |
| --- | --- |
| `accepted_frame(turn=..., view=..., acknowledged_sha256=..., action=...)` | Build one validated `bound` episode frame from a world-accepted actor receipt. |
| `decision_from_frame(key=..., role=..., native_trial_id=..., frame=..., answers=...)` | Build one validated `seatlab.decision/v1` record from a validated frame plus offline `answers`. |
| `episode_from_frames(id=..., case_sha256=..., role=..., native_trial_id=..., closed=..., frames=...)` | Seal an ordered 1-based sequence of validated frames into one `seatlab.episode/v1` record with `episode_sha256`. |

### Record only real actor receipts as `bound`

Pass the actual `acknowledged_sha256` digest that the actor returned and the world accepted for that exact `view`. Do not recompute `digest(view)` inside your adapter to satisfy `accepted_frame`. Recomputing the digest from a partial or reconstructed view hides mismatches and turns a reconstructed record into a false `bound` claim.

```python
# receipt comes from the world's retained log (for example, artifacts/logs/world/receipts.jsonl).
# receipt["acknowledged_sha256"] is the digest returned by the actor and checked by the world.
frame = accepted_frame(
    turn=receipt["turn"],
    view=receipt["view"],
    acknowledged_sha256=receipt["acknowledged_sha256"],
    action=receipt["action"],
)
decision = decision_from_frame(
    key=f"receipt-board:{role}:{mode}",
    role=role,
    native_trial_id=native_trial_id,
    frame=frame,
    answers=offline_answers,
)
episode = episode_from_frames(
    id=f"receipt-board:{role}:{mode}",
    case_sha256=case_sha256,
    role=role,
    native_trial_id=native_trial_id,
    closed=True,
    frames=[frame],
)
```

Use `accepted_frame` only when all three hold:

- The actor received the complete `view` object.
- The actor returned `acknowledged_sha256` for that canonical view and the world accepted it.
- The world accepted `action` (an accepted `None` becomes `observed={"action": None}`).

When no action was accepted, emit a frame with `observed=None` directly instead of calling `accepted_frame`. When the adapter derives or slices a view from retained logs instead of keeping the exact acknowledged object, emit `basis="reconstructed"` directly instead.

All three helpers validate inputs and deep-copy mutable containers (`view`, `observed`, `answers`), so later caller mutations cannot invalidate `view_sha256` or `episode_sha256`. Publish JSON Lines with `write_lines(path, records)`; it stages and links atomically and refuses to overwrite an existing file. Matching SHA-256 digests check internal byte consistency; they do not authenticate the adapter, actor, or native trial origin.

## Keep team rewards separate from seat grades

In the receipt board, `result.json` contains a shared team reward in `verifier_result.rewards.reward`. Other tasks can use a different verifier contract. SeatLab's `answers` field grades one seat's local action offline. It never enters policy stdin.

Do not copy a shared team reward into per-seat scores, and do not promote a seat's offline score to a native team reward. Export both separately:

- Keep each native trial's shared `team_reward` alongside its canonical `native_trial_id` in trial-level metadata (for example, `summary.json`).
- Keep per-seat offline `answers` inside `seatlab.decision/v1` records, never inside `view` and never inside `seatlab.episode/v1`.
- Preserve `native_trial_id` from `result.json` for every exported decision and episode, and reject trials with exceptions, missing terminal timestamps, or reused trial UUIDs across arms.

## Try the complete adapter

The [receipt-board walkthrough](../examples/receipt-board/README.md) has 2 starting points. Download the native evidence to try it without Docker. Or run both worlds with stock Olam Harbor and export your own. Both routes use the same detached-worker replay and native comparison. The [external calibration guide](native-calibration.md) specifies when input, action and score comparisons are eligible.

## Checklist for your own Harbor adapter

1. Log each role's complete delivered `view`, the actor-returned `acknowledged_sha256`, and the world-accepted `action` as a task artifact (see `examples/receipt-board/task/task.toml` and `environment/world.py`).
2. Read the completed Harbor trial directory without importing Harbor; validate `result.json` (canonical `id`, no `exception_info`, terminal `finished_at`, shared `verifier_result.rewards`, completed `seat_results`).
3. Build bound frames with `seatlab.author.accepted_frame`, passing the logged `acknowledged_sha256` directly rather than hashing the view in the adapter.
4. Build decisions with `decision_from_frame` (offline `answers` supplied by the adapter) and role-local episodes with `episode_from_frames` (no `answers` or privileged state).
5. Publish tapes with `seatlab.tape.write_lines` and pair manifests (`seatlab.calibration/v1`, `seatlab.episode-pairs/v1`) atomically into a new directory, keeping native team rewards separate from per-seat grades.

See also [the receipt-board walkthrough](../examples/receipt-board/README.md), [the decision tape format](format.md), [native calibration](native-calibration.md), [parallel replay](parallel-replay.md), and [role-local episodes](episodes.md).
