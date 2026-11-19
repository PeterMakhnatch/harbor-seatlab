# Test a dropped worker when another team really changed

This is a collector control, not a new estimate of agent performance. The recordings already exist. The [original study](README.md) compares 5 teams on 12 native speaker/listener seeds. Its changed listener rule ran in fresh stock Olam trials. The same native rewards still mark every one of the 10 changed-rule trials complete.

## Plan

1. Build a new portable source using [worker-source.json](worker-source.json) and the original release recordings.
2. Collect all 10 changed-rule team recordings. The registered experiment already found 8 changed behavior entries out of 18.
3. Omit **only `s101.plain-blind`** from a second collection. Keep every other native return byte-for-byte unchanged. Known changes on other teams should still fail the gate. Criteria that need the missing worker should become unknown, not stable or changed.
4. Compare the reports, source pins, and exit statuses. Do not run another game or change the old source bundle.

The generated source study pin is `45de22680d9700ba6f694fcb064908aafc152ed84a4da41d8dd6f414fe0ff017`. Source world and role data come from the [pinned public archives](fetch_controls.py). This tests a native simultaneous world independently of the alternating Hanabi example.

## Result

| Changed-rule returns received | Stable | Changed | Unknown | Exit |
|---|---:|---:|---:|---:|
| All 10 | 10 | 8 | 0 | 1 |
| 9; `s101.plain-blind` absent | 10 | 4 | 4 | 1 |

Removing one worker makes 4 previously changed comparisons unknown. It does not erase the 4 observed changes on the other seed. The 18 entries share 10 native trials. They are not 18 independent failures or successes. The [collection receipts](worker-results.json) identify all received native UUIDs and all missing logical jobs.

## Reproduce without Docker or a model

Use Python 3.11+, `jq`, and SeatLab 0.16. Run from the repository root. The first command downloads the pinned public recordings; the rest works offline.

```sh
python3 examples/panel-replication/fetch_controls.py controls
cp examples/panel-replication/worker-source.json controls/source/matrix.json
seatlab study build-matrix controls/source/matrix.json --out source > source-pin.json
mkdir full partial
jq -r '.jobs[]|[.id,.case,.arm]|@tsv' source/worklist.json | while read -r id c a; do
  mkdir "full/$id"
  cp "controls/changed/cells/$c-$a/episodes.jsonl" "controls/changed/cells/$c-$a/order.json" "full/$id/"
  if [ "$id" != s101.plain-blind ]; then cp -R "full/$id" "partial/$id"; fi
done
seatlab study collect-jobs source full --receipt source-pin.json --out all-result
seatlab study collect-jobs source partial --receipt source-pin.json --out partial-result
```

Both collection commands intentionally return exit `1`. Run them as separate commands. Compare `all-result/collection.json` with `partial-result/collection.json`. The [alternating-turn control](../hanabi-panel/results.md) uses different native recordings.
