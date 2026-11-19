# Build a regression panel for your Harbor task

Start with a group of real cases and at least 2 configurations of your agents. An **arm** names one team configuration. Define the differences you care about before collecting the candidate.

## 1. Export the native recordings

For each measured case and arm, export 2 files. Other files in the trial directory are not inputs.

```
source-input/matrix.json
source-input/runs/c1-base/episodes.jsonl
source-input/runs/c1-base/order.json
source-input/runs/c1-variant/episodes.jsonl
source-input/runs/c1-variant/order.json
```

Use the [author API](authoring.md) for the role episodes. A bound view needs an acknowledgement from the participant. Hashing a reconstructed view does not turn it into an acknowledgement. Record actual world steps in `order.json`; do not infer order from file names, clocks, or the expected rules. Read the [world-order contract](world-order.md). Keep native shared rewards separate. Digests check consistency, not the identity of the recorder.

## 2. Describe the task

This illustrative matrix compares one seat with 2 different partners:

```json
{
  "schema": "seatlab.panel-matrix/v1",
  "contrasts": {
    "base-vs-variant": {"source": "base", "target": "variant", "role": "seat-a"}
  },
  "criteria": [
    {"id": "sees-differ", "contrast": "base-vs-variant", "dimension": "raw_view", "when": "different"}
  ],
  "cases": {
    "c1": {"base": "runs/c1-base", "variant": "runs/c1-variant"},
    "c2": {"base": "runs/c2-base", "variant": null}
  }
}
```

All folder paths are relative to `matrix.json`. Every case lists every arm. A JSON `null` is a declaration that a run is absent. It is not an action or a failed reward. A non-null pair with missing or malformed files is invalid. An arm without any measured comparison is not copied. Each measured case/arm needs a different physical trial UUID.

Use `raw_view`, `selected_view` or `accepted_action` for `dimension`. Use `different` or `equal` for `when`. For `selected_view`, set `fields` on the contrast to a list of JSON Pointers, or omit it for the whole view. The matrix supports 1–16 criteria, 1–16 contrasts, 1–256 cases and at most 128 measured comparison checks. At least one pair must be measured. Case, arm and contrast labels match `[a-z0-9][a-z0-9_-]{0,27}`.

## 3. Select cases, then run fresh worlds

The examples use `jq` to read the receipts. Output folders must be new. No command here runs a model or a world.

```sh
seatlab study build-matrix source-input/matrix.json --out source > source-pin.json
seatlab study panel source/study.json --design source/panel-design.json \
  --expect-sha256 "$(jq -er .study_sha256 source-pin.json)" > panel.json
jq '.selection' panel.json
```

### Send the selected cases to independent native workers

The source build writes `worklist.json` and `candidate-matrix.json`. Keep their receipt pins separately. The worklist has one job per distinct case and arm, even if several criteria need it. It includes both arms for each measured comparison, including an opposite result. It has no command, credentials, source trial UUID or machine-specific path.

```sh
mkdir returned
jq -c '.jobs[] | {id,case,arm,criteria}' source/worklist.json
```

Give each job to one worker. Have it run a **new native world** for that case and arm. Put its 2 exported files in `returned/<id>/`. Leave that folder absent if the worker returns nothing. A worker does not need another worker's world. Keep raw Harbor output elsewhere.

```sh
seatlab study collect-jobs source returned --receipt source-pin.json --out result
jq '{status,jobs,recheck}' result/collection.json
```

The collector checks the original study, design, worklist and candidate template against the source receipt. It does not modify the plan. It checks every returned recording, even one whose partner is missing. A malformed return, extra file, wrong native case, or reused trial UUID is invalid. It is never counted as missing. A known change takes precedence over missing evidence. The collection receipt keeps the source pins and each returned native UUID, including an unpaired return.

The return folder is a **trusted scheduling label**, not proof of executable identity. The collector cannot detect two workers that secretly swap policies on the same case. For that claim, retain independently attested policy or container identities from your runner. Finish publishing a worker folder before running the collector.

If 2 compatible teams return, the output also contains a sealed `candidate/` and `recheck.json`. If no comparison is possible, it writes only `collection.json`. Do not count that as a successful behavior test. An empty return folder is valid evidence that no jobs returned. A missing return root is an input error.

### Bind worker folders to the scheduler

Two teams can play the same case with completely different native trial UUIDs. Their behavior criteria may still agree. If you need to detect a misrouted return, record the mapping from logical job ID to the UUID in the **launcher's native result**, separately from the returned role recordings. Do not derive this map from the return folders you intend to verify.

```sh
seatlab study collect-jobs source returned --receipt source-pin.json --out bound-result \
  --launch-registry launches.json --expect-launch-sha256 "$TRUSTED_LAUNCH_DIGEST"
```

A `seatlab.launch-registry/v1` document contains `worklist_sha256` and `trials`. `trials` maps **every** scheduled ID to its native trial UUID, or `null` if the launcher has no native result. Reject a returned UUID assigned to another job, including on the same case. A recorded launch with no return stays missing. A different worklist, an unknown job ID, or a reused source UUID is invalid. A registry never determines a behavior score.

Keep the canonical registry digest in a coordinator-controlled channel. This binds job identity to the recorded native trial. It does not attest which executable, model revision, or container actually ran. On an untrusted scheduler, you need an independent attestation before making those claims.

For manual integrations, use `source/candidate-matrix.json` as the new input matrix. `worklist_sha256` and `candidate_matrix_sha256` are canonical JSON digests, not checksums of newline-terminated files. Your scheduler still owns assignment, launch and retries. The [hosted example](../.github/workflows/native-hanabi.yml) runs one stock world per independent worker.

Archive the **original** source pins separately from future candidates. In CI, use those trusted values. Recomputing both source pins from an untrusted download does not establish trust. The study pin is a canonical digest, not a file checksum.

Exit `0` means every selected entry is stable. Exit `1` means at least one changed. Exit `3` means an incomplete result without a known change. Exit `2` refuses invalid inputs and emits no report. A missing selected case is unknown. Nonselected cases receive no recheck result. Stable criteria do not prove identical play, a better team, or coverage of unmeasured behavior.

[See a native OpenSpiel example](../examples/hanabi-panel/registration.md), [reproduce a dropped worker alongside a real policy change](../examples/panel-replication/worker-control.md), or use the [CI gate](panel-recheck.md).
