# Can a smaller view still distinguish the recorded actions?

A task author wants to remove fields from an agent's context. Before changing a live world, check whether two acknowledged views become identical even though the same role submitted different actions. `context-audit` finds such pairs. The report includes receipts for the original decisions without printing the view or action values.

```sh
seatlab context-audit examples/cursor-source.jsonl --field /round
seatlab context-audit examples/cursor-source.jsonl --field /bound --field /replies
seatlab context-audit examples/cursor-source.jsonl --no-fields --witnesses 1
```

The default uses the entire current view. `--no-fields` gives a policy only the role name. Each `--field` selects a JSON Pointer from the actor's current view. Roles always remain separate. Use `/a~1b` for the key `a/b`, `/a~0b` for `a~b`, and `/players/0` for the first player in an array. The tool treats a missing value differently from a present JSON `null`. It never uses a native UUID, case ID, turn number, previous action, or future input as an implicit policy feature.

## Read the bound

`fit.counts` shows all frames and the eligible denominator. Only acknowledged views with an observed action qualify. An accepted null action qualifies. Reconstructed views and missing actions do not. Open episodes can contribute their recorded observations, not an implied ending.

Within each role and identical selected view, count the occurrences of each action. A deterministic current-view policy can choose only one of them. If the counts are 4, 2, and 1, the best such policy can match at most 4 of those 7 recorded decisions. At least 3 disagreements are unavoidable. Sum the minimum disagreement count across groups to obtain `minimum_errors`.

`fit.full` uses the unmodified actor view. `fit.selected` uses the chosen fields. `additional_minimum_errors` reports how many more disagreements become unavoidable after the selection. A conflict even with the full view can reflect memory, a different policy version, or a different model generation. Do not attribute it to the removed fields. Use a single policy cohort when you want a statement about one policy.

**Zero fit disagreements do not establish sufficiency.** An input unique to every turn has zero disagreements by construction. Check `repeated_frames` and `repeated_classes`. Counts weight each eligible recorded decision once; a long negotiation therefore contributes more than a short one.

## Test exact lookup on different cases

```sh
seatlab context-audit train.jsonl --heldout heldout.jsonl --field /replies
```

Split by case before running the command. A native UUID or case digest in both inputs is an error. More than one role can belong to the same native trial within a split. The held-out lookup selects the unique most frequent action fitted to that role and view. It abstains on an unseen projected view or a tie. It never learns from held-out actions.

Report `matched`, `disagreed`, `tied`, and `unseen` against the eligible held-out denominator. Low coverage means the observations do not repeat across the split. It does not mean another algorithm cannot generalize. These are comparisons to **recorded actions**, not meeting feasibility, team reward, causation, or model quality. Use `prefix` and a new native trial to test what actually changes when an acting policy uses less context.

In the [registered calendar study](https://github.com/PeterMakhnatch/harbor-multiagent/blob/11dc5bfe54/adapters/masbench-scheduling/context-audit-results.md), the fit set contains 321 decisions. All 321 full views are different. The replies-only representation also has 0 fit conflicts, but its only 8 held-out matches are initial requests. It does not predict a single held-out response to actual replies. An independent SQL implementation reproduces every count. Use this distinction before spending on a smaller live prompt.

## Privacy and reproducibility

The report includes the selected field names, role identifiers and a bounded number of receipts. A witness identifies two eligible frames with different action digests and the same projected input within a role. The action digest covers the object `{"action": value}`. The projection digest covers the canonical complete view in full mode, or the canonical array of selected values in sorted pointer order. A present value is `[true, value]`; a missing value is `[false]`. Role identity is a separate grouping key. SHA-256 is not anonymization: an observer can enumerate a small action set. Use `--witnesses 0` to omit individual receipts.
