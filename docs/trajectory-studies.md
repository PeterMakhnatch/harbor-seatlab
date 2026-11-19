# Publish a role trajectory in a portable study

A [portable study](portable-studies.md) can contain a fourth kind of check, `role-trajectory`. It publishes the report of [`role-trajectory`](role-trajectory.md): one role, followed along the shared world schedule of two native trials. A reader verifies it offline, with the same pin and file receipts as the other kinds.

Requires SeatLab v0.12.0 or later. Older manifests, their pins, their JSON output and their HTML stay unchanged. They do not use the new kind.

The check gives observations. It does not say why a view or an action changed.

## Register the check

The check needs 7 keys, and no others. `fields` is optional.

| Key | Meaning |
| --- | --- |
| `id` | A portable identifier, unique in the study. |
| `kind` | `role-trajectory` |
| `source`, `target` | The episode file of each native trial. |
| `source_order`, `target_order` | The [world-order file](world-order.md) of each native trial. |
| `role` | The registered role to follow. |
| `fields` | Optional. A non-empty array of unique JSON Pointers (RFC 6901) into the role view. |

Without `fields`, the selected view is the complete raw role view. `null` and `[]` are errors. Do not write them to mean "all". The 4 paths are inputs of the study. They are relative to the recipe, and they have the same limits as in other kinds. SeatLab keeps the order of your `fields` array in the manifest, so it is part of the pin. The report itself lists the pointers sorted.

Everything in the recipe is data. SeatLab runs no script from it. It computes the report from the private snapshot of the 4 files, not from any program that you ship.

## A complete recipe from a real study

The [v0.11.0 native speaker-listener study](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.11.0/native-speaker-listener.tar.gz) has 15 native trials. This recipe compares 2 trials of seed 5 for the listener `p1`. It makes 2 checks. The first follows 8 physical pointers. The second follows 3 channel pointers.

```sh
work=$(mktemp -d)
curl -fsSL https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.11.0/native-speaker-listener.tar.gz -o "$work/data.tgz"
(cd "$work" && echo '92e2c01fee3317bd14b5315a70b4dd1d7914e31cd7e3ee0da5e752ab4022f579  data.tgz' | shasum -a 256 -c -)
mkdir "$work/raw" "$work/study"
tar -xzf "$work/data.tgz" -C "$work/raw"
a="$work/raw/cells/s5-plain-plain"; b="$work/raw/cells/s5-rotate-rotate"
cp "$a/episodes.jsonl" "$work/study/plain-episodes.jsonl"
cp "$a/order.json" "$work/study/plain-order.json"
cp "$b/episodes.jsonl" "$work/study/rotate-episodes.jsonl"
cp "$b/order.json" "$work/study/rotate-order.json"
```

Write `$work/study/recipe.json`:

```json
{
  "schema": "seatlab.study-recipe/v1",
  "checks": [
    {
      "id": "p1-physical", "kind": "role-trajectory",
      "source": "plain-episodes.jsonl", "target": "rotate-episodes.jsonl",
      "source_order": "plain-order.json", "target_order": "rotate-order.json",
      "role": "p1",
      "fields": ["/observation/0", "/observation/1", "/observation/2", "/observation/3",
                 "/observation/4", "/observation/5", "/observation/6", "/observation/7"]
    },
    {
      "id": "p1-channel", "kind": "role-trajectory",
      "source": "plain-episodes.jsonl", "target": "rotate-episodes.jsonl",
      "source_order": "plain-order.json", "target_order": "rotate-order.json",
      "role": "p1",
      "fields": ["/observation/8", "/observation/9", "/observation/10"]
    }
  ]
}
```

Then seal, verify and render:

```sh
cd "$work/study"
STUDY_SHA256=$(seatlab study seal recipe.json --out study.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["study_sha256"])')
seatlab study verify study.json --expect-sha256 "$STUDY_SHA256" --html report.html
seatlab study relations study.json --expect-sha256 "$STUDY_SHA256" --out relations.jsonl
```

`seal` prints `study_sha256`. Record it, and give it to the reader by a trusted channel as `STUDY_SHA256`. The study is yours: this document does not publish a pin for it. Ship `study.json` and the 4 input files. The reader does not need `recipe.json`.

Both checks share the same 4 files. SeatLab snapshots them once. A check is an analysis, not a copy of the trial.

## What verification shows

`study verify` recomputes each report from the snapshot and compares the report digest. A report that disagrees with the pin is a failed verification (exit `1`). A report that shows a different view, a different action, unknowns, a changed schedule or an open trial is a valid result. It verifies.

The verified summary of each check has exactly 11 keys. It contains no raw views, actions or UUIDs:

`id`, `kind`, `report_sha256`, `role`, `fields`, `status`, `schedule`, `role_decisions`, `raw_view`, `selected_view`, `accepted_action`.

`fields` is the sorted list from the report, or `null` when the check selects the complete view. `schedule`, `role_decisions` and the 3 dimensions have the meaning in [role-trajectory](role-trajectory.md). Each dimension has `equal`, `different`, `unknown`, `first_different_step` and `first_unknown_step`.

## The HTML page

Open each `role-trajectory` check in `report.html` to see:

- the role, and the selected fields as escaped code. For a complete view, the page says that no pointer was selected;
- the shared schedule: its status, its matched steps against both lengths, and the boundary step;
- 3 separate sections, one each for the raw view, the selected view and the accepted action. Each shows equal, different and unknown counts, the first different step and the first unknown step.

A step that is `null` shows as "Not reached. The missing value is not zero." A count of zero shows as a count. The status is the schedule status. It is not a reward. The page never adds the 3 dimensions or the checks together. It does not show raw views, field values or actions.

## The relation row

`study relations` writes one row for each `role-trajectory` check. It has the real native trial UUIDs of the 2 trials, the check's role and `pair_index` 1.

| Field | Value |
| --- | --- |
| `kind` | `role-trajectory` |
| `status` | The schedule status. |
| `boundary_unit` | `world_step` |
| `boundary` | The schedule boundary step, or `null`. |
| `matched_prefix` | `null`. Matching schedules are not a common prefix of decisions. |
| `native_action_changed` | `true` if an observed accepted action differs. `false` only for a complete schedule with at least 1 role decision, no unknown accepted action and no accepted action difference. Otherwise `null`. |
| `reproduced` | `null`. A study does not forecast a trial. |

The 2 checks above give 2 rows for the same 2 trials. Count distinct UUIDs to count trials. Do not add `role_decisions` or step counts across checks. The optional SeatLab example for stock Eval Lab accepts the new kind and the `world_step` unit. It does not change these counts.

## Measured on seed 5

The `p1-physical` check measures the pair of native trials of seed 5, plain speaker and plain listener against rotate speaker and rotate listener. Native `role-trajectory` on the same files, with the same 8 pointers, gives:

- The first different raw listener view is at step 2. The raw views differ.
- The 8 selected observation components are equal in all 20 steps.
- All 20 accepted listener actions are equal.

On this seed, the raw listener views differ on 19 of 20 steps. All 19 differences start after the first transmitted symbol. The [complete 37-check study](observed-communication.md) gives the independently verifiable physical, channel, and action counts for all 12 pairs.

These are measurements of 1 pair of native trials. They do not show that a participant understands or ignores language.

## Limits

- The author chooses the files, the role and the fields. SeatLab cannot show that an omitted trial or pointer never existed.
- The case digest, the trial UUIDs and the role set must be valid for [world-order files](world-order.md). Otherwise `seal` stops with exit `2` and writes nothing.
- A changed input file, a changed manifest or a wrong pin stops `verify` with exit `1`. No HTML or relation file is written.
- A digest detects a change. It does not authenticate the publisher or a native trial.
