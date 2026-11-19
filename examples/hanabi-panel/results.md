# One OpenSpiel deck exercises all 8 registered criteria

The [registration](https://github.com/PeterMakhnatch/harbor-seatlab/blob/204fe4a952/examples/hanabi-panel/registration.md) fixes deck 0 and all 4 teams before the candidate runs. The general SeatLab matrix compiler selects this deck using only the [previously released 32 native games](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.9.0/native-world-order.tar.gz).

The 4 new physical trials ran in unmodified Olam Harbor with the native OpenSpiel Hanabi engine. For each trial the exporter reconciles every world event with the actor's acknowledged view and accepted action. It never infers order by alternating player names.

| Fresh team | World events matched to seats | Native score (out of 25) |
| --- | ---: | ---: |
| LL | 55 | 0 |
| LR | 26 | 0 |
| RL | 16 | 0 |
| RR | 42 | 0 |

All four teams scored zero. They did not play the same game. In each of 4 substitutions the unchanged player saw a different private history and took a different sequence of actions. Those are the 8 source criteria. Every classification stayed the same across the fresh independent games: **8 stable, 0 changed, 0 unknown**. This is observed repeatability for 1 deck chosen from 8, not a representative sample or a policy ranking. The 8 checks are not independent outcomes.

The [native results](local-native.json) include the 4 UUIDs, game scores and decision counts. The [complete local recheck](local-recheck.json) identifies each independent source/candidate pair. All 8 original decks covered all 8 criteria. Deck 0 is the lexical tie-break, not a uniquely informative deck. The 7 unselected decks have no new result. For this check the full collector requires 32 games and the frozen panel requires 4.

## Reproduce this with no game engine

Download the [release](https://github.com/PeterMakhnatch/harbor-seatlab/releases/tag/v0.15.0). The source and candidate bundles contain only recordings and JSON. Install the release wheel in a virtual environment. Extract the two published bundles into separate directories. `receipts.json` holds both canonical study and design digests. Run the final `seatlab study recheck` command in the [author guide](../../docs/from-matrix.md) with the published pins. Do not replace the pins with hashes of an untrusted download.

To build new evidence, run the [native workflow](../../.github/workflows/native-hanabi.yml) in a fork. To adapt a different task, start with the [generic author guide](../../docs/from-matrix.md), not the Hanabi converter.

## Independent Linux repeat

The [hosted workflow](https://github.com/PeterMakhnatch/harbor-seatlab/actions/runs/44560679986) rebuilt the frozen source. Its worklist requested 4 native worlds, not 8 separate jobs for 8 criteria. The 4 runner jobs overlapped for 55 seconds. Each ran 1 team on stock Olam Harbor. A separate job checked the exported private views, accepted actions, and world order.

The 4 Linux worlds produced 139 decisions. All 8 criteria stayed stable; none changed or became unknown. Their UUIDs differ from the 32 original worlds and all 4 local repeats. This repeats one selected deck, not all 8 decks. Agreement of these criteria is not proof of a better team.

The [v0.15.0 release](https://github.com/PeterMakhnatch/harbor-seatlab/releases/tag/v0.15.0) contains the 3 portable bundles, all pins and both rechecks. The [task-owner guide](../../docs/from-matrix.md) shows how to use your own world recordings instead.
