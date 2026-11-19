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
mkdir -p candidate-input/jobs
cp source/candidate-matrix.json candidate-input/matrix.json
jq -c '.jobs[] | {id,case,arm,criteria}' source/worklist.json
```

Give each job to one worker. Have it run a **new native world** for that case and arm. Put its 2 exported files in `candidate-input/jobs/<id>/`. A worker need not receive another worker's world. An empty worklist is not a passing result. A missing required folder is an error. To record an intentionally absent run, set its arm to `null` and preserve the changed input separately.

The [hosted example](../.github/workflows/native-hanabi.yml) checks both handoff hashes before collecting independent workers. They are canonical JSON digests (`seatlab.tape.digest`), **not** checksums of the file including its newline. Your runner owns assignment, launch and retries. Keep the case, arm and contrast labels when using a different runner.

```sh
seatlab study build-matrix candidate-input/matrix.json --out candidate > candidate-pin.json
seatlab study recheck source/study.json candidate/study.json \
  --design source/panel-design.json --candidate-design candidate/panel-design.json \
  --expect-sha256 "$(jq -er .study_sha256 source-pin.json)" \
  --expect-design-sha256 "$(jq -er .design_sha256 source-pin.json)" \
  --expect-candidate-sha256 "$(jq -er .study_sha256 candidate-pin.json)" \
  --expect-candidate-design-sha256 "$(jq -er .design_sha256 candidate-pin.json)"
```

Archive the **original** source pins separately from future candidates. In CI, use those trusted values. Recomputing both source pins from an untrusted download does not establish trust. The study pin is a canonical digest, not a file checksum.

Exit `0` means every selected entry is stable. Exit `1` means at least one changed. Exit `3` means an incomplete result without a known change. Exit `2` refuses invalid inputs and emits no report. A missing selected case is unknown. Nonselected cases receive no recheck result. Stable criteria do not prove identical play, a better team, or coverage of unmeasured behavior.

[See a native OpenSpiel example](../examples/hanabi-panel/registration.md) or use the [CI gate](panel-recheck.md).
