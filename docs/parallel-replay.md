# Distribute a replay without a shared scheduler

Use this workflow for **independent decisions**, not consecutive turns in an episode. It requires no Docker daemon, queue, shared database, network service or model key. A coordinator registers the input and the policy before distribution. Workers run the normal `replay` command. The coordinator verifies returned rows against the original tape. Nothing launches workers or executes a policy during merge.

## Try 2 workers on actual native decisions

Install SeatLab and run these commands from the source checkout. All outputs must be new.

```sh
out=$(mktemp -d)
tape=examples/meeting-sources.jsonl
seatlab partition "$tape" --policy intersection --shards 2 \
  --transform-file examples/meeting-first8.json --out "$out/plan"

seatlab replay "$out/plan/part-0001.jsonl" --policy intersection \
  --transform-file "$out/plan/context.json" --out "$out/worker-a.jsonl" &
a=$!
seatlab replay "$out/plan/part-0002.jsonl" --policy intersection \
  --transform-file "$out/plan/context.json" --out "$out/worker-b.jsonl" &
b=$!
wait "$a" && wait "$b"

seatlab merge --plan "$out/plan/plan.json" --tape "$tape" \
  --out "$out/merged.jsonl" "$out/worker-b.jsonl" "$out/worker-a.jsonl"
seatlab calibrate examples/meeting.jsonl "$out/merged.jsonl" \
  --pairs examples/meeting-pairs.json --transform-file "$out/plan/context.json"
```

Send each trusted worker its own `part-*.jsonl`, the replay command, and the context recipe if needed. Keep the source and `plan.json` with the coordinator. A worker tape includes `observed` (the recorded native action, when known) **and** `answers` (the offline scoring table). Only the role view enters the policy on stdin. The worker can read its tape. Do not distribute it to untrusted workers or use it as a blind evaluation.

For a custom policy, register `partition ... --policy external`. On each POSIX worker, run `seatlab replay part-0001.jsonl --out worker-a.jsonl --exec /absolute/path/to/policy`. Put `--exec` last. Put a context recipe or timeout **before** it. Distribute the exact policy artifact and arguments separately; record their digest with the plan. A named `--transform latest` also travels in the command, not in a recipe file.

## Recover after a worker stops

Supply the available ledgers to `merge`. It reports the missing keys and does not create a merged ledger. If a local `replay` process stops before publication, it leaves **no** ledger for that part. Run that whole part again and supply exactly 1 ledger for it. Do not supply both an original and a replacement. A returned `unmeasured` row is complete evidence of a failed measurement, not a missing worker or a successful score. Do not replace it to improve the study. A measured `null` action is different from both.

| Exit | Meaning | Ledger published? |
| --- | --- | --- |
| `0` | Every registered key has exactly 1 consistent result. | Yes, in the original source order. |
| `1` | A key is missing, foreign, repeated across ledgers, conflicting, or incompatible. See the JSON report. | No. |
| `2` | An input is malformed (including a key repeated inside one ledger), the source does not match its registration, or publication fails. | No new ledger. |

The merger validates the full source, role, basis, transformed input, allowed action and score. It can recompute built-in deterministic policies. For `--policy external`, it cannot attest which executable, remote model, or credentials a worker used. Pin those separately. It never re-executes an external call, certifies individual worker assignments, or turns a local replay score into a native team reward. Permuting ledger paths does not change the complete output.

## Share work safely

The plan contains the ordered source digest, part digests, partition rule, policy family and context recipe. The digest of the plan excludes its own digest field. Record that digest in a separately trusted location **before** giving work to others. SHA-256 checks consistency; editing a file and recomputing its digest is not authentication.

SeatLab uses at most 64 nonempty parts. The first source row goes to part 1, the second to part 2, and so on, then assignment repeats. Whitespace differences in JSON input do not create a new cohort; changing a decision or the order of decisions does. `plan.json` is the completion marker for a partition. Do not distribute an interrupted directory without that file.

## Reproduction on native examples

Separate worker processes replayed the two bundled source tapes. The coordinator received the ledgers in reverse order. The merged files matched the serial replay byte for byte.

| Source | Policy | Workers | Decisions | SHA-256 of serial and merged ledger |
| --- | --- | ---: | ---: | --- |
| `meeting-sources.jsonl` | `intersection` with `meeting-first8.json` | 2 | 4 | `30f917055cdecd6627c2ce477e988ff84ada9b306e8ccf7809422c13528fc6e8` |
| `dispatch.jsonl` | `arrival`, unchanged input | 8 | 72 | `eaf7e56adcd84a1aa30304498ac6126e30cbae1144f8668d12c478ec857ebcbc` |

On both tapes, 3 separate negative controls returned exit `1` without writing a merged ledger: no results, one worker absent, and one worker submitted twice. These checks establish consistency. They do not measure a speed improvement.
