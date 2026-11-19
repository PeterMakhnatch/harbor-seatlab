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
