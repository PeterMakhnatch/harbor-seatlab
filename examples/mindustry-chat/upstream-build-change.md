# Apply one native communication check to an actual upstream change

**Registration.** Compare the original Olam task immediately before and at [Olam’s portable-build change](https://github.com/olamlabs/multi-agent-mindustry/commit/6ee0648ce6b1e5c5bbcc6616e736ca54b5c42dac). The change moves the gateway and simulation build contexts under `environment/` and changes camera wiring. This is not a SeatLab-injected policy change. We make no claim about cloud builders or cameras.

| Source | Git revision |
| --- | --- |
| Before | `124b275aa9e248ca18836432dc51658b4ac4f573` |
| After | `6ee0648ce6b1e5c5bbcc6616e736ca54b5c42dac` |
| Unchanged stock Olam Harbor | `4a194016336ccd1ce3fcc799b10158ca0375eb63` |

Each revision gets one standard Linux worker, two native trials (`read`, `no-read`), identical scripted participants and the [frozen evidence rules](protocol.md). No retries, patched tasks, paid runners, model calls or survivor scoring. Keep the real resolved game and Harbor Git revisions next to every result. No raw journal, host configuration or credential goes in a public artifact.

**Prediction.** If a world runs and the registered inputs are measurable, the journal accepts one challenge in both arms. Only the read-enabled recipient explicitly records that challenge and sends the dependent reply that the sender observes. This predicts 4 observable yes/no facts per arm. A missing world, invalid receipt, unavailable journal, or timed-out trial is not a negative result. Withhold that observation. No prediction about the native survival reward or game skill. One attempt per arm cannot estimate a frequency.

**Run.** From the public companion repository, select the `portable-build-change` scope of the native Mindustry workflow. It dispatches the two fixed revisions in parallel. The `reference` scope retains the original single-revision run. Reuse the same conditions for both sides; do not include a follow-up rescue run in this comparison.

## Measured result

Both task revisions accepted the challenge in both conditions. With the read enabled, the receiver recorded the challenge and sent the correct reply. The original sender saw that reply. Without the read, neither the receiver nor the journal recorded a reply. This does **not** show the world hid the message.

| Observed fact | Parent, read | Child, read | Parent, no-read | Child, no-read |
| --- | --- | --- | --- | --- |
| Game accepted the challenge | Yes | Yes | Yes | Yes |
| Recipient explicitly recorded the challenge | Yes | Yes | No | No |
| Game accepted a reply tied to the challenge | Yes | Yes | No | No |
| Original sender recorded the reply | Yes | Yes | No | No |

**8 stable comparisons, 0 changed, 0 unknown.** These are 4 related facts in 2 conditions, not 8 independent samples. Each revision used 2 fresh native trials. None of their 4 UUIDs matches the earlier reference control. All 4 trials withheld the survival reward. This shows compatibility for this chat path on hosted Linux Docker, not for cloud builds, cameras, survival, or arbitrary agents.

[Original two-worker native CI run](https://github.com/PeterMakhnatch/harbor-seatlab/actions/runs/44567215783) · [Verified aggregate](upstream-build-results.json) · [Exact public worker inputs](portable-build/). The retained inputs contain only the 10 small JSON files from the two public artifacts. No game installation, Harbor installation, model, or network access is needed to compare them again.

```sh
# Run from the repository root with Python 3.11 or later.
tmpout=$(mktemp -d)
python3 -I -S examples/mindustry-chat/compare_revisions.py \
  examples/mindustry-chat/portable-build/before \
  examples/mindustry-chat/portable-build/after --out "$tmpout/result.json"
cmp "$tmpout/result.json" examples/mindustry-chat/upstream-build-results.json
```
