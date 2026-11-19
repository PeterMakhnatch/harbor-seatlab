# Test a dropped worker when another team really changed

This is a collector control, not a new estimate of agent performance. The recordings already exist. The [original study](README.md) compares 5 teams on 12 native speaker/listener seeds. Its changed listener rule ran in fresh stock Olam trials. The same native rewards still mark every one of the 10 changed-rule trials complete.

## Plan

1. Build a new portable source using [worker-source.json](worker-source.json) and the original release recordings.
2. Collect all 10 changed-rule team recordings. The registered experiment already found 8 changed behavior entries out of 18.
3. Omit **only `s101.plain-blind`** from a second collection. Keep every other native return byte-for-byte unchanged. Known changes on other teams should still fail the gate. Criteria that need the missing worker should become unknown, not stable or changed.
4. Compare the reports, source pins, and exit statuses. Do not run another game or change the old source bundle.

The generated source study pin is `45de22680d9700ba6f694fcb064908aafc152ed84a4da41d8dd6f414fe0ff017`. Source world and role data come from the [pinned public archives](fetch_controls.py). This tests a native simultaneous world independently of the alternating Hanabi example.
