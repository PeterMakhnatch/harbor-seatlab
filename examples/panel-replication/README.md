# Repeat a selected native panel on a new machine

This task runs the cases the verified panel actually selects, rather than hard-coding a smoke set. Read the [registered Linux protocol](https://github.com/PeterMakhnatch/harbor-multiagent/blob/4424442bc8/adapters/pettingzoo-seats/communication-experiment/panel-replication-protocol.md) before interpreting the result. All 5 policies run once per selected world. The run uses stock Olam Harbor, pinned MPE2 and no model keys.

The [workflow](../../.github/workflows/native-selected-panel.yml) checks the wheel hash and original dataset hash separately. Its `BASE` directory is data only. It runs code from pinned repositories, not from the archive. You can also run the same command on a machine with Docker and the pinned dependencies. `--root` and `--public` must not exist; the driver refuses to overwrite an attempt. Source and target must have distinct native UUIDs. It never treats an absent fresh trial as a matching outcome.

```sh
python repeat.py BASE --adapter /absolute/pettingzoo-seats --harbor /absolute/bin/harbor \
  --root /absolute/new-run --public /absolute/new-public --expect-sha256 34b0bfde9b2c4e7a71e2906c9c17ba8d65239defe24cd9bd01575ff888bb0d7d
```

The receipt separates exact accepted actions, exact actor-view digests, world order, and per-role game returns within 1e-6. It also compares each selected case's criterion state to its earlier observation. The released files contain case IDs and numbers rather than host logs or model text. A completed job can report disagreement or missing cells. Read `receipt.json`; a green job alone does not establish agreement. This is a repeat of the same cases, not an independent sample of new worlds.
