# Role decision tape, version 1

A decision tape is UTF-8 JSON Lines. Each line describes one recorded decision. It separates **what the actor may see** from **what the analyst knows**. The harness sends only `view` to a policy. Do not place a native result, a transcript or a global world object in `view`.

| Field | Meaning |
| --- | --- |
| `schema` | `seatlab.decision/v1`. |
| `key` | Unique stable label for this decision and experimental cell. Paired comparisons require the same keys. |
| `role` | Actor that made the decision. |
| `native_trial_id` | Canonical native UUID. Null only for a design-only example. A shared UUID can have several decisions. |
| `basis` | `bound`: the accepted client action acknowledges exactly this canonical view. `reconstructed`: a trusted adapter derives the view from retained evidence. `design`: no native observation exists. |
| `view` | The actor-facing JSON object, with order preserved within arrays. |
| `view_sha256` | SHA-256 of sorted-key, ASCII-escaped compact JSON, no trailing newline, no non-finite numbers. It authenticates neither the host nor the actor. |
| `observed` | `null` means an action is unknown. `{"action":null}` means an explicit, observed null choice. |
| `answers` | Trusted offline score data. Use an array of `{action,optimal,feasible}` records for finite named actions or a compact integer domain. It never enters policy stdin. |

The compact answer format is `{"kind":"int-range","min":0,"max":719,"include_null":true,"optimal":[245,310],"feasible":[245,310]}`. The bounds are inclusive. The two lists have ascending distinct integers inside the bounds. In this example, a returned null is a completed decision that produces no optimal or feasible time. An integer outside the range is invalid. Use actual verified gold for real tapes, not these illustrative numbers. Score meanings are task-defined; set membership in a publisher's reference is not always the same as physical feasibility.

An adapter that slices a 12-item acknowledged batch into 12 single-item views **cannot** mark those projections `bound`. Their hash differs from the acknowledged digest. Use `reconstructed`. A separate actor report can remain `bound` only when its entire canonical view matches the accepted client commitment. A client can acknowledge bytes without reading or acting on the information they contain.

A replay writes one `seatlab.decision-result/v1` per input. It includes the source digest, actual intervention digest, bytes, policy, transform, status, action and score. A failed external policy writes an unmeasured row with a safe error code and no score. The viewer and comparator require all rows, including unmeasured ones. A replay is not a reward update, a new native trial or a new collective negotiation.

The comparison joins by stable key. It checks the role, native trial UUID, original view digest and `source_decision_sha256`. The last field covers the entire validated source record, including the private reference and the observed action. Changing the answer key cannot produce an apparent policy gain. Independently reannotated records need a separate study, even if their visible bytes match. Count newly correct, newly incorrect and unchanged decisions separately. Do not treat individual decisions as independent native trials.

## Trust boundary

The adapter and source files are trusted. Hashes detect mismatches, not forgery. The released experimental data is synthetic. Changing a view or a private answer without a new provenance record produces a different study. The external executable must be trusted too: a restricted data interface and environment are not operating-system isolation.