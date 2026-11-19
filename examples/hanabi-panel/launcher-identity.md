# When equal behavior hides a misrouted worker

A run has two different questions. Does its measured behavior agree with the baseline? Does each returned world belong to the worker that the scheduler assigned? One answer cannot establish the other.

We took the 4 independent same-deck Linux returns from the [original hosted Hanabi control](https://github.com/PeterMakhnatch/harbor-seatlab/actions/runs/44560679986). We exchanged the **whole folders** for `d00.ll` and `d00.lr`; no recording bytes changed. Without a separate assignment source, all 8 registered comparisons still reported stable. Comparing the returned native UUID map with the original collection identified exactly the 2 exchanged labels. This is a reproduced routing limitation, not a model or game failure.

For the binding control we use a separate source: stock Harbor's `result.json` for each of the 4 local native launches. We select the native result using the registered run configuration's `trial_name`. We never obtain the expected identifier from the role tapes or their `order.json`. The [registry](local-launches.json) pins the original worklist and the 4 resulting UUIDs. Its canonical digest is `6ba8d4bad0138d1ca24e2ef470f0bc31426e59ea2865135a0c39db7b52e9e373`.

Expected controls: the complete local set should match; the same-case swap should refuse input; withholding only `d00.rr` should leave 4 stable and 4 unknown. No new game is required to check these statements. The registry binds routing only. It is not a signature or an attestation of a policy binary.

## Observed outcome

The independent exporter reconstructed the registered digest exactly from stock Harbor. On the 4 local native worlds, honest delivery gave 8 stable; withholding `d00.rr` gave 4 stable and 4 unknown. The unbound same-case swap still gave 8 stable. With the separately pinned registry, the collector identified `d00.ll` as the wrong native UUID, returned exit `2`, and published no output. See the [actual collection receipts](local-assignment-control.json).

To export an independent registry from the standard Hanabi launcher, install SeatLab and run:

```sh
python examples/hanabi-panel/native_registry.py local "$HARBOR_HANABI_ROOT" source/worklist.json --out launches.json
```

Keep the printed digest separately. The [hosted workflow](../../.github/workflows/native-hanabi.yml) reads each worker’s fresh stock `result.json` **before** exporting role data. It sends the launcher receipt and the role data in different artifacts. The coordinator uses the same exporter in `receipts` mode. If both channels share a dishonest issuer, this is not independent attestation.
