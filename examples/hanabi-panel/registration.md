# Prospective replication of a measured Hanabi regression panel

The input for selecting the panel is the [v0.9.0 native world-order archive](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.9.0/native-world-order.tar.gz), SHA-256 `ddb23f1fde776eb833e3f4671d53bb7523ce6df02b242e6f190a1417cb24cd29`. It contains 32 OpenSpiel games: 8 decks, 4 two-player teams per deck. All games used stock Olam Harbor. The underlying public deck results are already known. This is a repeatability and authoring test, not a blind forecast of an unseen game.

We compare the same physical player under four partner substitutions: `LL→LR` for p0, `LL→RL` for p1, `RR→RL` for p0, and `RR→LR` for p1. For each substitution, the two registered criteria ask whether the acknowledged raw view and the accepted action trajectory differ. They do not test final game score. These 8 criteria overlap.

The general matrix compiler seals the source at `9b93b09d4d5d53ebe6c3aa185ff217f8a309cad628ddd0ea71570cea3648e51b`, design `6e46a22efecdea98366db2ddc132123bdb18b7e95dd06646e2d4f82a4934950a`. The exact cover selects the first minimum in lexical order: **deck d00**. That deck covers all 8 measured criteria. Other decks are not needed for this specific repeat. This does not establish world or task coverage beyond those criteria.

## Freeze before four new games

Run all 4 teams (`LL`, `LR`, `RL`, `RR`) once on deck 0. Use independent native trial UUIDs, the unchanged reference rules, the frozen deck, and native containers. No retries, substitutions, or inferred order. The existing Linux evidence for deck 0 has role tapes but no retained world order. Do not use it for this endpoint. Recover order from each **new game's recorded world events** and reconcile that order with each seat before computing a per-criterion result. Mark other decks unmeasured in the candidate matrix.

Expected result: all 8 selected entries retain their source classification. A changed entry rejects the expectation. A missing receipt, unknown cell, invalid input or reused UUID cannot support a match. Publish what the runs produce, including failures. The source [selection receipt](source-selection.json) precedes new trial creation.
