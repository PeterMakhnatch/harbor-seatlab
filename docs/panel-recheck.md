# Panel recheck: compare a chosen case set across two independent runs

`seatlab study recheck` compares two independent, verified SeatLab studies. It uses only the case set that the **source** panel chose. It runs offline. It runs no model and no trial. The same inputs always give the same output.

## What it answers

For each source case and each design criterion, did the observed state stay the same in the candidate run? The result is a list of per-case, per-criterion outcomes. It is not a prediction.

## Command

```
seatlab study recheck SOURCE_MANIFEST CANDIDATE_MANIFEST \
  --design SRC.json --candidate-design TARGET.json \
  --expect-sha256 PIN --expect-candidate-sha256 CANDIDATE_PIN
```

- Both pins are study digests from `study seal`. They are not raw file digests. The source study does not bind the separate design. Use `--expect-design-sha256` and `--expect-candidate-design-sha256` to pin both designs in CI.
- Both studies must verify against their pins. Both designs must be valid panel designs. Each design file is read once. The digest in the report belongs to the bytes that were parsed.
- Stdout is JSON only. The command takes no report file as input.

Python entry point:

```python
recheck_panel(source: Path, candidate: Path, *, design: Path, candidate_design: Path,
              expect_sha256: str, expect_candidate_sha256: str) -> dict
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | `matched`: every entry is stable. At least 1 case is selected. |
| 1 | `changed`: at least 1 entry changed. Unknown entries elsewhere do not hide this. |
| 3 | `incomplete`: no change, but at least 1 entry is unknown, or 0 cases are selected. |
| 2 | Refusal. No report is printed. |

Shell consumer:

```sh
rc=0
report=$(seatlab study recheck src.json cand.json \
  --design src-design.json --candidate-design cand-design.json \
  --expect-sha256 "$SRC_PIN" --expect-candidate-sha256 "$CAND_PIN") || rc=$?
case "$rc" in
  0) echo "matched" ;;
  1) echo "changed"; printf '%s\n' "$report" > recheck.json; exit 1 ;;
  3) echo "incomplete: do not treat as pass"; printf '%s\n' "$report" > recheck.json; exit 1 ;;
  *) echo "refused: no report" >&2; exit 2 ;;
esac
```

This example also works when the outer shell uses `set -e`.

## Which cases are compared

- The **source panel** picks the cases. The command takes the sorted selection from the source design and study. It never picks a new minimum from the candidate.
- The candidate may contain any subset of the registered source cases. Only selected cases receive a comparison. An omitted selected case is unknown.
- Cases match by label. A candidate label that the source does not know is a refusal.
- No inference covers a case that the source did not select. A case outside the selection has no outcome. Do not read the report as a statement about it.

## What must match

1. **Criterion descriptors.** Each criterion matches by its own ID. Dimension, direction (`when`), role and `fields` must also match. Field paths are sorted before the comparison. A mismatch is a refusal.
2. **Check IDs.** Program check IDs are stable. If both designs map a case and criterion to a check, the IDs must be equal. A changed mapping is a refusal. The same ID must also use the same ordered relative role-tape and world-order paths. Changing which team a label measures is not a policy change.
3. **Null bindings.** A candidate may bind a criterion to `null`. The outcome is `unknown`.
4. **No new measurement.** If the source binds `null` for a case and criterion, the candidate may not add a check there. The entry stays `unknown`. The command does not work back to a value.
5. **Native case digests.** When both panels know a native case digest for a case, the digests must agree.
6. **New UUIDs.** The candidate must use new native trial UUIDs. If one UUID is known to both panels, the command refuses. It checks the IDs of all verified role-trajectory checks, including checks outside the design.
7. **Evidence.** A case with no usable data cannot pass. Unknown data cannot pass.

## Outcomes

Each selected case and criterion gets one state per side: `witness`, `opposite` or `unknown`.

| Source | Candidate | Status |
| --- | --- | --- |
| witness | witness | `stable` |
| opposite | opposite | `stable` |
| witness | opposite | `changed` |
| opposite | witness | `changed` |
| any unknown, missing or null | any | `unknown` |
| any | any unknown, missing or null | `unknown` |

Overall `status`:

- `changed` if any entry changed.
- `incomplete` if no entry changed and any entry is unknown, or if 0 cases are selected.
- `matched` otherwise.

## Report

Schema: `seatlab.panel-recheck/v1`.

```json
{
  "schema": "seatlab.panel-recheck/v1",
  "status": "matched | changed | incomplete",
  "source_study_sha256": "…",
  "candidate_study_sha256": "…",
  "source_design_sha256": "…",
  "candidate_design_sha256": "…",
  "selection": ["case-id", "…"],
  "counts": {"entries": 0, "stable": 0, "changed": 0, "unknown": 0},
  "cases": [
    {
      "id": "case-id",
      "source_native_trial_ids": ["…"],
      "candidate_native_trial_ids": ["…"],
      "criteria": {
        "criterion-id": {"source": "witness", "candidate": "opposite", "status": "changed"}
      }
    }
  ]
}
```

`entries` equals `stable + changed + unknown`. Lists are sorted. The output contains no original role values or host paths.

## Limits

- The report does not say why an entry changed. It does not name a cause or an actor.
- The report does not mark a host as authentic. A candidate cannot claim this.
- The command does not grade the teams. Read native rewards separately. Identical criterion states can hide different movements within a category.
- `incomplete` is not a pass. Unknown means the data cannot support a claim. Collect the missing check and run the command again.
- Read the [measured unchanged and changed controls](measured-recheck.md) before using this as a CI gate.
