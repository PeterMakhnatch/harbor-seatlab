# Give a colleague an offline study they can verify

A portable study records which files and analysis reports belong together. A reader can check the files and recompute the reports without Harbor, Docker, a model, or the original analyst. SeatLab does not execute scripts from the study. It does not replay the world.

## Verify a study

Install the SeatLab version named by the publisher. Extract the publisher's data with your usual archive tools. Obtain the expected manifest digest from the publisher through a trusted channel. Then run:

```sh
seatlab study verify ./study.json --expect-sha256 "$STUDY_SHA256"
```

The digest covers the canonical JSON manifest, not its indentation or final newline. Verification hashes the exact bytes of every declared input file. It recomputes each report and checks its canonical digest. The output lists counts or a world-step boundary per check. It does not sum seat counts across checks or turn them into team scores.

A verified study can contain missing native trials, a negative control with no matching inputs, or logged external actions with no proof of execution. `status: verified` means the published analysis reproduces, not that the policy succeeds. With no trusted digest, verification establishes internal consistency only. It does not authenticate a publisher, a native trial, or a worker. Undeclared files are not verified.

To share a readable offline summary, add `--html study.html` to `study verify`. SeatLab writes a new HTML file only after verification succeeds. It uses the verified counts and boundary steps, not raw participant observations. The report has no scripts or external resources. Open each check to see its denominator and report digest.

### Try the published study

This example uses actual native observations from MASBench Scheduling, Overcooked-AI and the receipt board. Verification needs Python 3.11 or later. It does not need a model key or Docker.

```sh
work=$(mktemp -d)
python3 -m venv "$work/venv"
base=https://github.com/PeterMakhnatch/harbor-seatlab/releases/download
"$work/venv/bin/python" -m pip install --no-deps "$base/v0.10.0/harbor_seatlab-0.10.0-py3-none-any.whl"
curl -fsSL "$base/v0.8.0/portable-native-studies.tar.gz" -o "$work/studies.tar.gz"
tar -xzf "$work/studies.tar.gz" -C "$work"
"$work/venv/bin/seatlab" study verify "$work/study.json" \
  --expect-sha256 5c05f220396f3d74fba87c2610349ec9d17a445b01cf295c32dd0355e7541fa3 \
  --html "$work/report.html"
```

The command verifies 33 input files and 13 analyses. For example, all 4 projected calendar decisions reproduce. The unchanged-input control reproduces none. The kitchen repeat matches both roles, but each changed partner alters a recorded history. On both receipt-board runs, the external worker actions agree while strict policy reproduction remains zero. Some checks reuse trials. The archive has SHA-256 `59a7805804f6564e5671f16e106465d41fd4c904f2737226727d92c77512c7c9`; it is distinct from the canonical manifest pin above.

## Publish a study

Place the shareable inputs in one directory. Review them for sensitive data before distribution. For a calibration, keep the combined native decision tape, source prediction ledger and registered pair manifest. Include the context recipe when the claim depends on a transformed view. For a native alignment, keep both role episode tapes and the registered episode pairs.

Write `recipe.json` beside the inputs. This example registers 2 checks with different denominators:

```json
{
  "schema": "seatlab.study-recipe/v1",
  "checks": [
    {"id":"projected","kind":"calibration","tape":"decisions.jsonl","predictions":"predictions.jsonl","pairs":"pairs.json","context":"first.json"},
    {"id":"native-roles","kind":"alignment","source":"source-episodes.jsonl","target":"target-episodes.jsonl","pairs":"episode-pairs.json","input_paths":4}
  ]
}
```

For [team alignment](world-order.md), use `kind: "team-alignment"` with `id`, `source`, `target`, `source_order` and `target_order`. Both order files are inputs to the study. `input_paths` is optional.

To follow one native role beyond the first changed input, use [`role-trajectory`](trajectory-studies.md). Register the same 4 files, the role name, and optionally the JSON Pointers to observe. The verified report keeps raw inputs, selected inputs and accepted actions separate.

Run `seatlab study seal recipe.json --out study.json`. The output path must be new and beside the recipe. SeatLab reads a private snapshot of the referenced files. It publishes the manifest only after every analysis completes. Record the printed `study_sha256` separately. Distribute `study.json` and all its input files with their relative names intact. The reader does not need `recipe.json` or any executable from the author.

SeatLab 0.12 adds `role-trajectory`. Version 0.9 adds `team-alignment` to the original `calibration` and `alignment` checks. No kind accepts commands, plugins, URLs, parent-directory paths or symlinks. The publisher selects the evidence. SeatLab cannot establish that an omitted trial never existed. Give the reader the tested SeatLab version.

| Exit | Meaning |
| --- | --- |
| `0` | All declared file receipts and analysis reports agree. Inspect the reported outcomes separately. |
| `1` | A trusted manifest pin, file receipt, or recomputed report differs. |
| `2` | Input is unreadable, malformed or unsupported, or publication failed. |
