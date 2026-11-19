import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from seatlab import tape
from seatlab.context import ContextSpec
from seatlab.partition import iter_source, load_plan, partition


def decision_row(index: int) -> tape.Decision:
    view = {"label": "salle é", "reports": [index, index + 1]}
    return tape.decision(
        {
            "schema": tape.DECISION_SCHEMA,
            "key": f"decision:{index}",
            "role": "dispatcher",
            "native_trial_id": None,
            "basis": "design",
            "view": view,
            "view_sha256": tape.digest(view),
            "observed": None,
            "answers": [{"action": index, "optimal": True, "feasible": True}],
        }
    )


class PartitionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.source = self.root / "source.jsonl"
        self.out = self.root / "parts"
        self.rows = [decision_row(index) for index in range(5)]
        tape.write_lines(self.source, self.rows)

    def test_round_robin_parts_and_plan_are_deterministic_across_source_encodings(self):
        reformatted_source = self.root / "source-copy.jsonl"
        # CRLF, reordered keys, UTF-8 strings, and no final newline are valid tape framing.
        reformatted_source.write_bytes(
            "\r\n".join(json.dumps(row, ensure_ascii=False) for row in self.rows).encode()
        )
        other = self.root / "different-name"
        manifest = partition(self.source, self.out, 2, "intersection")
        self.assertEqual(partition(reformatted_source, other, 2, "intersection"), manifest)

        contents = {
            "part-0001.jsonl": b"".join(
                tape.canonical(row) + b"\n" for row in self.rows[::2]
            ),
            "part-0002.jsonl": b"".join(
                tape.canonical(row) + b"\n" for row in self.rows[1::2]
            ),
        }
        expected = {
            "schema": "seatlab.partition/v1",
            "partition": "round-robin/v1",
            "decisions": 5,
            "shards": 2,
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "shard_sha256": [hashlib.sha256(data).hexdigest() for data in contents.values()],
            "policy": "intersection",
            "transform": "identity",
        }
        expected["plan_sha256"] = tape.digest(expected)
        self.assertEqual(manifest, expected)
        contents["plan.json"] = tape.canonical(expected) + b"\n"
        for directory in (self.out, other):
            self.assertEqual({part.name for part in directory.iterdir()}, contents.keys())
            self.assertEqual(directory.stat().st_mode & 0o077, 0)
            for name, data in contents.items():
                self.assertEqual((directory / name).read_bytes(), data)
                if name.startswith("part-"):
                    self.assertEqual((directory / name).stat().st_mode & 0o077, 0)

        plan = load_plan(other / "plan.json")
        self.assertEqual(plan.decisions, 5)
        self.assertEqual(plan.shards, 2)
        self.assertEqual(plan.policy, "intersection")
        self.assertEqual(plan.transform_id, "identity")
        self.assertIsNone(plan.context)
        self.assertEqual(plan.shard_sha256, tuple(expected["shard_sha256"]))
        self.assertEqual(list(iter_source(plan, reformatted_source)), self.rows)

    def test_source_verification_rejects_changes_to_order_content_and_count(self):
        partition(self.source, self.out, 2, "observed")
        plan = load_plan(self.out / "plan.json")
        changed = {**self.rows[0], "answers": []}
        for name, rows, message in (
            ("reordered", list(reversed(self.rows)), "source_sha256"),
            ("changed", [changed, *self.rows[1:]], "source_sha256"),
            ("short", self.rows[:-1], "source has 4 decisions; expected 5"),
            ("long", [*self.rows, decision_row(5)], "source has 6 decisions; expected 5"),
        ):
            with self.subTest(name=name):
                source = self.root / f"{name}.jsonl"
                tape.write_lines(source, rows)
                stream = iter_source(plan, source)
                for row in rows:
                    self.assertEqual(next(stream), row)
                with self.assertRaisesRegex(tape.TapeError, message):
                    next(stream)

    def test_source_verification_checks_individual_shards_even_when_source_hash_matches(self):
        manifest = partition(self.source, self.out, 2, "arrival")
        manifest["shard_sha256"].reverse()
        plan_path = self.write_rehashed_plan("wrong-order", manifest)
        plan = load_plan(plan_path)
        self.assertEqual(plan.source_sha256, manifest["source_sha256"])
        with self.assertRaisesRegex(tape.TapeError, "shard_sha256.*shard 1"):
            list(iter_source(plan, self.source))

    def test_context_plan_embeds_recipe_and_writes_standalone_worker_recipe(self):
        recipe = {
            "schema": "seatlab.context/v1",
            "ops": [{"op": "take", "path": ["reports"], "count": 1}],
        }
        transform_file = self.root / "recipe.json"
        transform_file.write_text(json.dumps(recipe, indent=2), encoding="utf-8")
        manifest = partition(
            self.source, self.out, 1, "external", transform_file=transform_file
        )
        transform_file.unlink()
        self.assertEqual(manifest["transform"], recipe)
        worker_recipe = self.out / "context.json"
        self.assertEqual(worker_recipe.read_bytes(), tape.canonical(recipe) + b"\n")
        context = ContextSpec.load(worker_recipe)
        plan = load_plan(self.out / "plan.json")
        self.assertEqual(plan.policy, "external")
        self.assertEqual(plan.context, context)
        self.assertEqual(plan.transform_id, f"context-v1:{tape.digest(recipe)}")
        self.assertEqual(
            context.apply(self.rows[0]["view"]), {"label": "salle é", "reports": [0]}
        )
        self.assertEqual(list(tape.read_tape(self.out / "part-0001.jsonl")), self.rows)

    def test_partition_rejects_recipe_too_large_to_publish_for_workers(self):
        recipe = {
            "schema": "seatlab.context/v1",
            "ops": [{"op": "take", "path": ["é" * 256] * 8, "count": 1}] * 6,
        }
        transform_file = self.root / "recipe.json"
        transform_file.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
        ContextSpec.load(transform_file)
        with self.assertRaisesRegex(tape.TapeError, "canonical context recipe exceeds"):
            partition(self.source, self.out, 1, "arrival", transform_file=transform_file)
        self.assertFalse(self.out.exists())

    def test_plan_rejects_malformed_fields_even_with_a_matching_digest(self):
        manifest = partition(self.source, self.out, 2, "revision", transform="latest")
        self.assertEqual(load_plan(self.out / "plan.json").transform_id, "latest")
        for index, changes in enumerate(
            (
                {"schema": "seatlab.partition/v2"},
                {"partition": "contiguous/v1"},
                {"shards": True},
                {"shards": 65},
                {"decisions": 5.0},
                {"decisions": 1},
                {"source_sha256": "A" * 64},
                {"shard_sha256": manifest["shard_sha256"][:1]},
                {"shard_sha256": ["0" * 64, None]},
                {"policy": "unknown"},
                {"policy": "observed"},
                {"transform": f"context-v1:{'0' * 64}"},
                {"transform": {"schema": "seatlab.context/v1", "ops": [{"op": "exec"}]}},
                {"command": ["not a manifest field"]},
            )
        ):
            with self.subTest(changes=changes):
                path = self.write_rehashed_plan(str(index), {**manifest, **changes})
                with self.assertRaises(tape.TapeError):
                    load_plan(path)

    def test_plan_rejects_mismatched_digest(self):
        manifest = partition(self.source, self.out, 2, "arrival")
        manifest["policy"] = "revision"
        path = self.root / "changed-plan.json"
        tape.write_lines(path, [manifest])
        with self.assertRaisesRegex(tape.TapeError, "plan_sha256 does not match"):
            load_plan(path)

    def test_partition_rejects_invalid_options(self):
        for shards, policy, transform in (
            (True, "arrival", "identity"),
            (0, "arrival", "identity"),
            (65, "arrival", "identity"),
            (2, "observed", "latest"),
            (2, "arrival", "custom"),
            (2, "not-a-policy", "identity"),
        ):
            with self.subTest(shards=shards, policy=policy, transform=transform):
                with self.assertRaises(tape.TapeError):
                    partition(self.source, self.out, shards, policy, transform=transform)
                self.assertFalse(self.out.exists())

    def test_partition_failure_leaves_no_final_plan(self):
        self.source.write_bytes(self.source.read_bytes() + b'{"schema":')
        with self.assertRaises(tape.TapeError):
            partition(self.source, self.out, 2, "arrival")
        self.assertFalse((self.out / "plan.json").exists())
        for count in (0, 1):
            source = self.root / f"small-{count}.jsonl"
            out = self.root / f"small-parts-{count}"
            tape.write_lines(source, self.rows[:count])
            with self.assertRaisesRegex(tape.TapeError, "at least one decision"):
                partition(source, out, 2, "arrival")
            self.assertFalse((out / "plan.json").exists())

    def test_existing_partition_is_not_overwritten(self):
        first = partition(self.source, self.out, 2, "arrival")
        with self.assertRaises(FileExistsError):
            partition(self.source, self.out, 2, "revision")
        self.assertEqual((self.out / "plan.json").read_bytes(), tape.canonical(first) + b"\n")
        self.assertEqual(list(tape.read_tape(self.out / "part-0001.jsonl")), self.rows[::2])

    def write_rehashed_plan(self, name: str, contents: dict) -> Path:
        data = {key: value for key, value in contents.items() if key != "plan_sha256"}
        data["plan_sha256"] = tape.digest(data)
        path = self.root / f"{name}.json"
        tape.write_lines(path, [data])
        return path
