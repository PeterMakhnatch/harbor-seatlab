"""Register and verify a deterministic split of a decision tape for offline replay."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast, get_args

from . import replay as engine
from . import tape as wire
from . import viewwork
from .context import ContextSpec, _MAX_SPEC_BYTES

_SCHEMA = "seatlab.partition/v1"
_VIEWS_ONLY_SCHEMA = "seatlab.partition/v2"
_VIEWS_ONLY_DELIVERY = "views-only"
_PARTITION = "round-robin/v1"
_FIELDS = {
    "schema",
    "partition",
    "decisions",
    "shards",
    "source_sha256",
    "shard_sha256",
    "policy",
    "transform",
    "plan_sha256",
}
_VIEWS_ONLY_FIELDS = _FIELDS | {"namespace", "delivery"}
_MAX_SHARDS = 64
_MAX_PLAN_BYTES = 128 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_NAMESPACE = re.compile(r"[0-9a-f]{32}")
_POLICIES = get_args(wire.Policy)


@dataclass(frozen=True, slots=True)
class ShardPlan:
    """Commitments to the ordered tape, its parts, and the replay configuration."""

    decisions: int
    shards: int
    source_sha256: str
    shard_sha256: tuple[str, ...]
    policy: wire.Policy
    transform_id: str
    context: ContextSpec | None
    plan_sha256: str
    namespace: str | None = None


def _shard_count(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_SHARDS:
        raise wire.TapeError(f"shards must be an integer from 1 to {_MAX_SHARDS}")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise wire.TapeError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _namespace(value: object) -> str:
    if not isinstance(value, str) or _NAMESPACE.fullmatch(value) is None:
        raise wire.TapeError("namespace must be a 32-character lowercase hex string")
    return value


def _read_json(path: Path, limit: int, name: str) -> object:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise wire.TapeError(f"{name} exceeds {limit} bytes")
    return wire.decode(data)


def _configuration(
    policy: object, transform: object, *, views_only: bool = False
) -> tuple[wire.Policy, str, ContextSpec | None]:
    if not isinstance(policy, str) or policy not in _POLICIES:
        raise wire.TapeError("unknown replay policy")
    if views_only and policy == "observed":
        raise wire.TapeError("view-only partitions do not accept the observed policy")
    if isinstance(transform, str) and transform in ("identity", "latest"):
        context = None
        transform_id = transform
    elif isinstance(transform, dict):
        try:
            context = ContextSpec(transform)
        except ValueError as exc:
            raise wire.TapeError(f"invalid context recipe: {exc}") from exc
        # Workers load the canonical LF-terminated recipe through ContextSpec.load.
        if len(wire.canonical(transform)) + 1 > _MAX_SPEC_BYTES:
            raise wire.TapeError(f"canonical context recipe exceeds {_MAX_SPEC_BYTES} bytes")
        transform_id = context.identifier
    else:
        raise wire.TapeError("unknown replay transform")
    if policy == "observed" and transform_id != "identity":
        raise wire.TapeError("observed requires the identity transform")
    return cast(wire.Policy, policy), transform_id, context


def _canonical_line(row: object) -> bytes:
    data = wire.canonical(row)
    if len(data) > wire.MAX_LINE_BYTES:
        raise wire.TapeError("canonical JSON line exceeds the byte limit")
    return data + b"\n"


def _resolved_view(
    row: wire.Decision, transform_id: str, context: ContextSpec | None
) -> dict[str, object]:
    try:
        if context is None:
            return engine._transform(row["view"], transform_id)
        return context.apply(row["view"])
    except ValueError as exc:
        raise wire.TapeError(f"cannot derive replay view for {row['key']!r}: {exc}") from exc


def partition(
    tape: Path,
    out: Path,
    shards: int,
    policy: str,
    *,
    transform: str = "identity",
    transform_file: Path | None = None,
    namespace: str | None = None,
) -> dict[str, object]:
    """Write an exclusive directory and seal its plan only after writing all parts.

    This registers the replay configuration. It does not run any policy or establish
    that a worker ran the registered policy. A failed partition may leave an unsealed
    directory, but it does not publish a plan.
    """
    shards = _shard_count(shards)
    validated_namespace = _namespace(namespace) if namespace is not None else None
    views_only = validated_namespace is not None
    if not isinstance(transform, str):
        raise wire.TapeError("unknown replay transform")
    recipe: object = transform
    if transform_file is not None:
        if transform != "identity":
            raise wire.TapeError("transform and transform_file are mutually exclusive")
        recipe = _read_json(transform_file, _MAX_SPEC_BYTES, "context recipe")
        if not isinstance(recipe, dict):
            raise wire.TapeError("context recipe must be an object")
    policy, transform_id, context = _configuration(policy, recipe, views_only=views_only)

    out.mkdir(mode=0o700)
    source_hash = hashlib.sha256()
    part_hashes = [hashlib.sha256() for _ in range(shards)]
    decisions = 0
    names = [f"part-{index:04d}.jsonl" for index in range(1, shards + 1)]
    with TemporaryDirectory(dir=out, prefix=".seatlab-") as temporary:
        staging = Path(temporary)
        with ExitStack() as stack:
            parts = [
                stack.enter_context(
                    os.fdopen(
                        os.open(staging / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
                        "wb",
                    )
                )
                for name in names
            ]
            for decisions, row in enumerate(wire.read_tape(tape), start=1):
                part = (decisions - 1) % shards
                source_data = _canonical_line(row)
                if validated_namespace is None:
                    part_data = source_data
                else:
                    view = _resolved_view(row, transform_id, context)
                    request = viewwork.make_request(
                        view, validated_namespace, policy, transform_id, decisions
                    )
                    part_data = _canonical_line(request)
                parts[part].write(part_data)
                source_hash.update(source_data)
                part_hashes[part].update(part_data)
            if decisions < shards:
                raise wire.TapeError("each shard requires at least one decision")
            for stream in parts:
                stream.flush()
                os.fsync(stream.fileno())
        for name in names:
            os.link(staging / name, out / name)

    if context is not None and not views_only:
        wire.write_lines(out / "context.json", (recipe,))
    manifest: dict[str, object] = {
        "schema": _VIEWS_ONLY_SCHEMA if views_only else _SCHEMA,
        "partition": _PARTITION,
        "decisions": decisions,
        "shards": shards,
        "source_sha256": source_hash.hexdigest(),
        "shard_sha256": [part_hash.hexdigest() for part_hash in part_hashes],
        "policy": policy,
        "transform": recipe,
    }
    if validated_namespace is not None:
        manifest["namespace"] = validated_namespace
        manifest["delivery"] = _VIEWS_ONLY_DELIVERY
    manifest["plan_sha256"] = wire.digest(manifest)
    wire.write_lines(out / "plan.json", (manifest,))
    return manifest


def load_plan(path: Path) -> ShardPlan:
    """Validate a manifest and compile its embedded transform without running it."""
    manifest = _read_json(path, _MAX_PLAN_BYTES, "partition plan")
    if not isinstance(manifest, dict):
        raise wire.TapeError("partition plan must have exactly the documented fields")
    manifest = cast(dict[str, object], manifest)
    schema = manifest.get("schema")
    if schema == _SCHEMA:
        views_only = False
        expected_fields = _FIELDS
    elif schema == _VIEWS_ONLY_SCHEMA:
        views_only = True
        expected_fields = _VIEWS_ONLY_FIELDS
    else:
        raise wire.TapeError("unknown partition plan schema or algorithm")
    if manifest.keys() != expected_fields:
        raise wire.TapeError("partition plan must have exactly the documented fields")
    if manifest["partition"] != _PARTITION:
        raise wire.TapeError("unknown partition plan schema or algorithm")
    namespace: str | None = None
    if views_only:
        if manifest["delivery"] != _VIEWS_ONLY_DELIVERY:
            raise wire.TapeError("unknown partition delivery mode")
        namespace = _namespace(manifest["namespace"])
    shards = _shard_count(manifest["shards"])
    decisions = manifest["decisions"]
    if type(decisions) is not int or decisions < shards:
        raise wire.TapeError("decisions must be an integer at least as large as shards")
    source_sha256 = _sha256(manifest["source_sha256"], "source_sha256")
    parts = manifest["shard_sha256"]
    if not isinstance(parts, list) or len(parts) != shards:
        raise wire.TapeError("shard_sha256 must contain one digest per shard")
    shard_sha256 = tuple(_sha256(part, "shard_sha256 entry") for part in parts)
    policy, transform_id, context = _configuration(
        manifest["policy"], manifest["transform"], views_only=views_only
    )
    plan_sha256 = _sha256(manifest.pop("plan_sha256"), "plan_sha256")
    if wire.digest(manifest) != plan_sha256:
        raise wire.TapeError("plan_sha256 does not match the partition plan")
    return ShardPlan(
        decisions,
        shards,
        source_sha256,
        shard_sha256,
        policy,
        transform_id,
        context,
        plan_sha256,
        namespace,
    )


def iter_registered(
    plan: ShardPlan, tape: Path
) -> Iterator[tuple[wire.Decision, viewwork.WorkerView | None]]:
    """Yield registered source rows and optional worker views, checking commitments."""
    source_hash = hashlib.sha256()
    part_hashes = [hashlib.sha256() for _ in range(plan.shards)]
    decisions = 0
    for decisions, row in enumerate(wire.read_tape(tape), start=1):
        source_data = _canonical_line(row)
        request: viewwork.WorkerView | None = None
        if plan.namespace is None:
            part_data = source_data
        else:
            view = _resolved_view(row, plan.transform_id, plan.context)
            request = viewwork.make_request(
                view, plan.namespace, plan.policy, plan.transform_id, decisions
            )
            part_data = _canonical_line(request)
        source_hash.update(source_data)
        part_hashes[(decisions - 1) % plan.shards].update(part_data)
        yield row, request
    if decisions != plan.decisions:
        raise wire.TapeError(f"source has {decisions} decisions; expected {plan.decisions}")
    if source_hash.hexdigest() != plan.source_sha256:
        raise wire.TapeError("source_sha256 does not match the source tape")
    for index, (part_hash, expected) in enumerate(
        zip(part_hashes, plan.shard_sha256, strict=True), start=1
    ):
        if part_hash.hexdigest() != expected:
            raise wire.TapeError(f"shard_sha256 does not match source rows for shard {index}")


def iter_source(plan: ShardPlan, tape: Path) -> Iterator[wire.Decision]:
    """Yield decisions in source order, checking the count and digests at exhaustion."""
    for row, _ in iter_registered(plan, tape):
        yield row
