# Can a stock Eval Lab checkout supply launch identities for different task types?

**Registered protocol, before running the bridge.** A coordinator owns the original per-job native trial folder mapping. A separate worker returns role evidence. Use `evallab.results.load_trial` in an unchanged Eval Lab checkout to turn the coordinator mapping into a SeatLab launch registry. Do not import SeatLab in that process or infer a job name from its evidence. The opposite process uses only the SeatLab package; it does not import Eval Lab.

1. Alternating OpenSpiel Hanabi: read the 4 registered native sender folders. Predict the same launcher-registry digest as the independent stock-Harbor exporter. All 8 behavior comparisons should still match. Swapping `d00.ll` and `d00.lr` should be refused only when bound to the genuine registry.
2. Simultaneous MPE2 speaker/listener: read the 10 registered native sender folders for the changed listener rule. Predict 10 stable and 8 changed. Withhold the 1 published return (`s101.plain-blind`). Predict 10 stable, 4 changed and 4 unknown, even though the coordinator still knows the withheld trial ID. No new world execution, native scoring or inference is required.
3. Refuse a missing explicit native path, absent work order, wrong worklist pin or duplicate launch. A deliberate `null` denotes no recorded native identity. Do not treat a typo or malformed result as deliberate missingness.

A match establishes independent *extraction* from the scheduler’s retained results. It does not establish that the scheduler is impartial or that its policy binaries match their labels. Human-maintained job assignments remain an explicit trust boundary. The private job-to-path mapping must never be included in a shared archive.

## Result

The stock Eval Lab checkout (`b9dd192458c`) read 14 different native trial results. Its Python environment did not import SeatLab. The independent collector used the publicly released v0.17.0 wheel in another Python environment. The producer never received the returned role tapes.

| Input to the SeatLab collector | Actual result |
| --- | --- |
| 4 alternating Hanabi returns, real assignments | 8 stable, 0 changed, 0 unknown. The digest exactly matched the earlier stock Harbor exporter. |
| Same 4 returns, 2 entire folders exchanged | Exit 2; wrong UUID for `d00.ll`. No collection directory. |
| 10 simultaneous speaker/listener returns, changed actor | 10 stable, 8 changed, 0 unknown. |
| Same speaker/listener runs, `s101.plain-blind` withheld | 10 stable, 4 changed, 4 unknown. The scheduler still knows the withheld UUID. |

The optional bridge passed 12 independently authored black-box controls on **each** cohort. They include explicit null, an absent result UUID, an unavailable explicit path, duplicate identity, a wrong pin, and preserving existing output. [Sanitized public receipts](launch-results.json) · [Run it yourself](README.md#use-eval-lab-as-the-source-of-worker-assignments). No private scheduler mappings, paths, native payloads or role recordings are in these receipts. No new Harbor trials or model calls were needed.
