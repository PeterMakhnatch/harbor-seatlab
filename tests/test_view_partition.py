import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from seatlab import tape, viewwork
from seatlab.context import ContextSpec
from seatlab.partition import iter_registered, iter_source, load_plan, partition

_NAMESPACE_A = "0123456789abcdef0123456789abcdef"
_NAMESPACE_B = "fedcba9876543210fedcba9876543210"
_TRIAL = "dd19e118-5159-4831-bc8e-365d571e6b6b"


def _handoff_decision(index: int, *, secret_tag: str = "secret") -> tape.Decision:
    view: dict[str, object] = {
        "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
        "messages": [
            {"revision": 2, "available": ["B", "C"]},
            {"revision": 1, "available": ["A", "D"]},
        ],
        "pooled_available": None,
    }
    return tape.decision(
        {
            "schema": tape.DECISION_SCHEMA,
            "key": f"{secret_tag}-case:{index}",
            "role": f"role_{secret_tag}",
            "native_trial_id": _TRIAL,
            "basis": "bound",
            "view": view,
            "view_sha256": tape.digest(view),
            "observed": {"action": f"{secret_tag}-observed-{index}"},
            "answers": [
                {"action": "A", "optimal": index % 2 == 0, "feasible": True},
                {"action": "B", "optimal": index % 2 == 1, "feasible": True},
                {
                    "action": f"{secret_tag}-answer-{index}",
                    "optimal": False,
                    "feasible": False,
                },
            ],
        }
    )


def _calendar_decision(index: int, *, secret_tag: str = "hidden") -> tape.Decision:
    view: dict[str, object] = {
        "label": "salle é",
        "reports": [index, index + 1, index + 2],
    }
    return tape.decision(
        {
            "schema": tape.DECISION_SCHEMA,
            "key": f"{secret_tag}-key:{index}",
            "role": "dispatcher",
            "native_trial_id": None,
            "basis": "design",
            "view": view,
            "view_sha256": tape.digest(view),
            "observed": None,
            "answers": [
                {"action": index, "optimal": True, "feasible": True},
                {
                    "action": f"{secret_tag}-label-{index}",
                    "optimal": False,
                    "feasible": False,
                },
            ],
        }
    )


class ViewPartitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.source = self.root / "source.jsonl"
        self.out = self.root / "parts"
        self.rows = [_handoff_decision(index) for index in range(5)]
        tape.write_lines(self.source, self.rows)

    def write_rehashed_plan(self, name: str, contents: dict[str, object]) -> Path:
        data = {key: value for key, value in contents.items() if key != "plan_sha256"}
        data["plan_sha256"] = tape.digest(data)
        path = self.root / f"{name}.json"
        tape.write_lines(path, [data])
        return path

    def test_views_only_reproducible_across_source_encodings_and_hides_private_fields(
        self,
    ) -> None:
        reformatted = self.root / "source-reformatted.jsonl"
        reformatted.write_bytes(
            "\r\n".join(json.dumps(row, ensure_ascii=False) for row in self.rows).encode()
        )
        other = self.root / "parts-other"
        manifest = partition(
            self.source,
            self.out,
            2,
            "revision",
            transform="latest",
            namespace=_NAMESPACE_A,
        )
        self.assertEqual(
            partition(
                reformatted,
                other,
                2,
                "revision",
                transform="latest",
                namespace=_NAMESPACE_A,
            ),
            manifest,
        )

        expected_requests = [
            viewwork.make_request(
                {
                    **row["view"],
                    "messages": [{"revision": 2, "available": ["B", "C"]}],
                },
                _NAMESPACE_A,
                "revision",
                "latest",
                ordinal,
            )
            for ordinal, row in enumerate(self.rows, start=1)
        ]
        part1 = b"".join(tape.canonical(req) + b"\n" for req in expected_requests[::2])
        part2 = b"".join(tape.canonical(req) + b"\n" for req in expected_requests[1::2])
        expected_manifest: dict[str, object] = {
            "schema": "seatlab.partition/v2",
            "partition": "round-robin/v1",
            "delivery": "views-only",
            "namespace": _NAMESPACE_A,
            "decisions": 5,
            "shards": 2,
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "shard_sha256": [
                hashlib.sha256(part1).hexdigest(),
                hashlib.sha256(part2).hexdigest(),
            ],
            "policy": "revision",
            "transform": "latest",
        }
        expected_manifest["plan_sha256"] = tape.digest(expected_manifest)
        self.assertEqual(manifest, expected_manifest)

        for directory in (self.out, other):
            self.assertEqual(
                {entry.name for entry in directory.iterdir()},
                {"part-0001.jsonl", "part-0002.jsonl", "plan.json"},
            )
            self.assertEqual(directory.stat().st_mode & 0o077, 0)
            self.assertEqual((directory / "part-0001.jsonl").read_bytes(), part1)
            self.assertEqual((directory / "part-0002.jsonl").read_bytes(), part2)
            self.assertEqual(
                (directory / "plan.json").read_bytes(),
                tape.canonical(expected_manifest) + b"\n",
            )
            for part_name in ("part-0001.jsonl", "part-0002.jsonl"):
                raw = (directory / part_name).read_bytes()
                self.assertEqual((directory / part_name).stat().st_mode & 0o077, 0)
                for forbidden in (
                    b"secret-case",
                    b"role_secret",
                    b"secret-observed",
                    b"secret-answer",
                    _TRIAL.encode("ascii"),
                    b'"answers"',
                    b'"observed"',
                    b'"key"',
                    b'"role"',
                    b'"native_trial_id"',
                    b'"view_sha256"',
                    str(manifest["source_sha256"]).encode("ascii"),
                ):
                    self.assertNotIn(forbidden, raw)

        self.assertEqual(
            list(viewwork.read_requests(self.out / "part-0001.jsonl")),
            expected_requests[::2],
        )
        self.assertEqual(
            list(viewwork.read_requests(self.out / "part-0002.jsonl")),
            expected_requests[1::2],
        )

        plan = load_plan(self.out / "plan.json")
        self.assertEqual(plan.namespace, _NAMESPACE_A)
        self.assertEqual(plan.decisions, 5)
        self.assertEqual(plan.shards, 2)
        self.assertEqual(plan.policy, "revision")
        self.assertEqual(plan.transform_id, "latest")
        self.assertIsNone(plan.context)
        self.assertEqual(
            list(iter_registered(plan, reformatted)),
            list(zip(self.rows, expected_requests, strict=True)),
        )
        self.assertEqual(list(iter_source(plan, reformatted)), self.rows)

    def test_different_namespaces_keep_source_receipt_but_change_public_requests_and_shards(
        self,
    ) -> None:
        out_a = self.root / "ns-a"
        out_b = self.root / "ns-b"
        manifest_a = partition(self.source, out_a, 2, "arrival", namespace=_NAMESPACE_A)
        manifest_b = partition(self.source, out_b, 2, "arrival", namespace=_NAMESPACE_B)

        self.assertEqual(manifest_a["source_sha256"], manifest_b["source_sha256"])
        self.assertNotEqual(manifest_a["shard_sha256"], manifest_b["shard_sha256"])
        self.assertNotEqual(manifest_a["plan_sha256"], manifest_b["plan_sha256"])

        requests_a = list(viewwork.read_requests(out_a / "part-0001.jsonl"))
        requests_b = list(viewwork.read_requests(out_b / "part-0001.jsonl"))
        self.assertEqual(
            [row["view"] for row in requests_a],
            [row["view"] for row in requests_b],
        )
        self.assertTrue(
            set(row["request_id"] for row in requests_a).isdisjoint(
                row["request_id"] for row in requests_b
            )
        )

    def test_request_ids_do_not_depend_on_private_labels_or_scoring_fields(self) -> None:
        source_b = self.root / "source-different-secrets.jsonl"
        rows_b = [_handoff_decision(index, secret_tag="other") for index in range(5)]
        tape.write_lines(source_b, rows_b)

        out_a = self.root / "public-a"
        out_b = self.root / "public-b"
        manifest_a = partition(self.source, out_a, 2, "arrival", namespace=_NAMESPACE_A)
        manifest_b = partition(source_b, out_b, 2, "arrival", namespace=_NAMESPACE_A)

        self.assertNotEqual(manifest_a["source_sha256"], manifest_b["source_sha256"])
        self.assertEqual(manifest_a["shard_sha256"], manifest_b["shard_sha256"])
        self.assertEqual(
            (out_a / "part-0001.jsonl").read_bytes(),
            (out_b / "part-0001.jsonl").read_bytes(),
        )
        self.assertEqual(
            (out_a / "part-0002.jsonl").read_bytes(),
            (out_b / "part-0002.jsonl").read_bytes(),
        )

    def test_context_recipe_views_only_transforms_views_without_publishing_context_json(
        self,
    ) -> None:
        source = self.root / "calendar.jsonl"
        rows = [_calendar_decision(index) for index in range(4)]
        tape.write_lines(source, rows)
        recipe = {
            "schema": "seatlab.context/v1",
            "ops": [{"op": "take", "path": ["reports"], "count": 1}],
        }
        recipe_path = self.root / "recipe.json"
        recipe_path.write_text(json.dumps(recipe, indent=2), encoding="utf-8")

        manifest = partition(
            source,
            self.out,
            2,
            "external",
            transform_file=recipe_path,
            namespace=_NAMESPACE_A,
        )
        recipe_path.unlink()

        self.assertFalse((self.out / "context.json").exists())
        self.assertEqual(manifest["transform"], recipe)
        self.assertEqual(manifest["schema"], "seatlab.partition/v2")
        self.assertEqual(manifest["delivery"], "views-only")

        plan = load_plan(self.out / "plan.json")
        self.assertEqual(plan.namespace, _NAMESPACE_A)
        self.assertEqual(plan.context, ContextSpec(recipe))
        self.assertEqual(plan.transform_id, f"context-v1:{tape.digest(recipe)}")

        part1 = list(viewwork.read_requests(self.out / "part-0001.jsonl"))
        part2 = list(viewwork.read_requests(self.out / "part-0002.jsonl"))
        self.assertEqual(
            [item["view"] for item in part1],
            [{"label": "salle é", "reports": [0]}, {"label": "salle é", "reports": [2]}],
        )
        self.assertEqual(
            [item["view"] for item in part2],
            [{"label": "salle é", "reports": [1]}, {"label": "salle é", "reports": [3]}],
        )
        registered = list(iter_registered(plan, source))
        self.assertEqual(registered[0][1], part1[0])
        self.assertEqual(registered[1][1], part2[0])
        self.assertEqual(registered[2][1], part1[1])
        self.assertEqual(registered[3][1], part2[1])

    def test_v1_partition_remains_unchanged_and_iter_registered_yields_none_views(
        self,
    ) -> None:
        manifest = partition(self.source, self.out, 2, "arrival")
        self.assertEqual(manifest["schema"], "seatlab.partition/v1")
        self.assertNotIn("namespace", manifest)
        self.assertNotIn("delivery", manifest)
        plan = load_plan(self.out / "plan.json")
        self.assertIsNone(plan.namespace)
        self.assertEqual(
            list(iter_registered(plan, self.source)),
            [(row, None) for row in self.rows],
        )
        self.assertEqual(list(iter_source(plan, self.source)), self.rows)

    def test_rejects_invalid_namespace_shards_and_observed_before_creating_output_dir(
        self,
    ) -> None:
        for shards, policy, namespace in (
            (2, "observed", _NAMESPACE_A),
            (0, "arrival", _NAMESPACE_A),
            (65, "arrival", _NAMESPACE_A),
            (2, "arrival", ""),
            (2, "arrival", "a" * 31),
            (2, "arrival", "a" * 33),
            (2, "arrival", "A" * 32),
            (2, "arrival", "g" * 32),
            (2, "arrival", 123456),
        ):
            with self.subTest(shards=shards, policy=policy, namespace=namespace):
                with self.assertRaises(tape.TapeError):
                    partition(
                        self.source,
                        self.out,
                        shards,
                        policy,
                        namespace=namespace,  # type: ignore[arg-type]
                    )
                self.assertFalse(self.out.exists())

    def test_v2_plan_rejects_malformed_fields_even_with_matching_digest(self) -> None:
        manifest = partition(self.source, self.out, 2, "arrival", namespace=_NAMESPACE_A)
        for index, changes in enumerate(
            (
                {"delivery": "full"},
                {"delivery": None},
                {"namespace": "A" * 32},
                {"namespace": "0" * 31},
                {"namespace": None},
                {"policy": "observed"},
                {"schema": "seatlab.partition/v1"},
                {"extra": "field"},
            )
        ):
            with self.subTest(changes=changes):
                path = self.write_rehashed_plan(f"bad-v2-{index}", {**manifest, **changes})
                with self.assertRaises(tape.TapeError):
                    load_plan(path)

        for missing in ("namespace", "delivery"):
            with self.subTest(missing=missing):
                reduced = {key: value for key, value in manifest.items() if key != missing}
                path = self.write_rehashed_plan(f"missing-{missing}", reduced)
                with self.assertRaises(tape.TapeError):
                    load_plan(path)

    def test_iter_registered_checks_count_source_and_view_shard_digests(self) -> None:
        manifest = partition(self.source, self.out, 2, "arrival", namespace=_NAMESPACE_A)
        plan = load_plan(self.out / "plan.json")
        changed = {**self.rows[0], "answers": []}
        for name, rows, message in (
            ("reordered", list(reversed(self.rows)), "source_sha256"),
            ("changed", [changed, *self.rows[1:]], "source_sha256"),
            ("short", self.rows[:-1], "source has 4 decisions; expected 5"),
            ("long", [*self.rows, _handoff_decision(5)], "source has 6 decisions; expected 5"),
        ):
            with self.subTest(name=name):
                source = self.root / f"{name}.jsonl"
                tape.write_lines(source, rows)
                stream = iter_registered(plan, source)
                for row in rows:
                    yielded_row, yielded_request = next(stream)
                    self.assertEqual(yielded_row, row)
                    self.assertIsNotNone(yielded_request)
                with self.assertRaisesRegex(tape.TapeError, message):
                    next(stream)

        shard_digests = manifest["shard_sha256"]
        assert isinstance(shard_digests, list)
        swapped = {**manifest, "shard_sha256": list(reversed(shard_digests))}
        swapped_plan = load_plan(self.write_rehashed_plan("swapped-shards", swapped))
        with self.assertRaisesRegex(tape.TapeError, "shard_sha256.*shard 1"):
            list(iter_registered(swapped_plan, self.source))

    def test_zero_too_few_duplicate_and_transform_errors_do_not_publish_plan(self) -> None:
        for count in (0, 1):
            source = self.root / f"count-{count}.jsonl"
            out = self.root / f"out-count-{count}"
            tape.write_lines(source, self.rows[:count])
            with self.assertRaisesRegex(tape.TapeError, "at least one decision"):
                partition(source, out, 2, "arrival", namespace=_NAMESPACE_A)
            self.assertFalse((out / "plan.json").exists())
            self.assertFalse((out / "part-0001.jsonl").exists())

        duplicate_source = self.root / "duplicate.jsonl"
        duplicate_source.write_bytes(
            tape.canonical(self.rows[0]) + b"\n" + tape.canonical(self.rows[0]) + b"\n"
        )
        dup_out = self.root / "out-dup"
        with self.assertRaisesRegex(tape.TapeError, "duplicate record key"):
            partition(duplicate_source, dup_out, 1, "arrival", namespace=_NAMESPACE_A)
        self.assertFalse((dup_out / "plan.json").exists())
        self.assertFalse((dup_out / "part-0001.jsonl").exists())

        bad_transform_source = self.root / "non-handoff.jsonl"
        tape.write_lines(bad_transform_source, [_calendar_decision(0), _calendar_decision(1)])
        bad_transform_out = self.root / "out-bad-transform"
        with self.assertRaisesRegex(tape.TapeError, "cannot derive replay view"):
            partition(
                bad_transform_source,
                bad_transform_out,
                2,
                "arrival",
                transform="latest",
                namespace=_NAMESPACE_A,
            )
        self.assertFalse((bad_transform_out / "plan.json").exists())
        self.assertFalse((bad_transform_out / "part-0001.jsonl").exists())

        bad_recipe = self.root / "missing-field-recipe.json"
        bad_recipe.write_text(
            json.dumps(
                {
                    "schema": "seatlab.context/v1",
                    "ops": [{"op": "take", "path": ["missing"], "count": 1}],
                }
            ),
            encoding="utf-8",
        )
        bad_recipe_out = self.root / "out-bad-recipe"
        with self.assertRaisesRegex(tape.TapeError, "cannot derive replay view"):
            partition(
                self.source,
                bad_recipe_out,
                2,
                "arrival",
                transform_file=bad_recipe,
                namespace=_NAMESPACE_A,
            )
        self.assertFalse((bad_recipe_out / "plan.json").exists())
        self.assertFalse((bad_recipe_out / "part-0001.jsonl").exists())

    def test_rejects_oversize_canonical_worker_packet_without_publishing_plan(self) -> None:
        # Build a valid source decision close to MAX_LINE_BYTES where a context relabel
        # grows the transformed WorkerView canonical JSON line past MAX_LINE_BYTES.
        payload = "x" * (tape.MAX_LINE_BYTES - 320)
        view: dict[str, object] = {"tag": "a", "blob": payload}
        row = tape.decision(
            {
                "schema": tape.DECISION_SCHEMA,
                "key": "k0",
                "role": "r",
                "native_trial_id": None,
                "basis": "design",
                "view": view,
                "view_sha256": tape.digest(view),
                "observed": None,
                "answers": [],
            }
        )
        self.assertLessEqual(len(tape.canonical(row)), tape.MAX_LINE_BYTES)
        source = self.root / "oversize.jsonl"
        tape.write_lines(source, [row])

        recipe = self.root / "grow.json"
        recipe.write_text(
            json.dumps(
                {
                    "schema": "seatlab.context/v1",
                    "ops": [
                        {"op": "relabel", "path": ["tag"], "from": "a", "to": "b" * 256},
                    ],
                }
            ),
            encoding="utf-8",
        )
        # Ensure the transformed view itself creates an oversize WorkerView packet.
        context = ContextSpec.load(recipe)
        transformed = context.apply(view)
        packet = viewwork.make_request(
            transformed, _NAMESPACE_A, "external", context.identifier, 1
        )
        self.assertGreater(len(tape.canonical(packet)), tape.MAX_LINE_BYTES)
        with self.assertRaisesRegex(tape.TapeError, "exceeds the byte limit"):
            partition(
                source,
                self.out,
                1,
                "external",
                transform_file=recipe,
                namespace=_NAMESPACE_A,
            )
        self.assertFalse((self.out / "plan.json").exists())
        self.assertFalse((self.out / "part-0001.jsonl").exists())

    def test_existing_view_partition_is_not_overwritten(self) -> None:
        first = partition(self.source, self.out, 2, "arrival", namespace=_NAMESPACE_A)
        with self.assertRaises(FileExistsError):
            partition(self.source, self.out, 2, "revision", namespace=_NAMESPACE_B)
        self.assertEqual((self.out / "plan.json").read_bytes(), tape.canonical(first) + b"\n")
