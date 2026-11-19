"""Calibrate source predictions against separately recorded native decisions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict, cast
from uuid import UUID

from . import tape as wire
from .context import ContextSpec
from .verification import action_equal, verify_calibration_result

__all__ = ["calibrate"]

_MANIFEST_SCHEMA = "seatlab.calibration/v1"
_RESULT_SCHEMA = "seatlab.calibration-result/v1"
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COUNTS = (
    "pairs",
    "source_available",
    "target_available",
    "source_missing",
    "target_missing",
    "target_acknowledged",
    "native_action_compared",
    "native_action_changed",
    "prediction_measured",
    "prediction_unmeasured",
    "prediction_opaque",
    "prediction_skipped",
    "input_compared",
    "input_matches",
    "action_compared",
    "action_matches",
    "score_compared",
    "score_matches",
    "coincident_action_matches",
    "reproduced",
)


class _Side(TypedDict):
    key: str
    case_sha256: str
    native_trial_id: str | None
    decision_sha256: str | None


class _Pair(TypedDict):
    id: str
    source: _Side
    target: _Side


def _valid_uuid(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _side(value: object, where: str) -> _Side:
    if not isinstance(value, dict) or value.keys() != _Side.__annotations__.keys():
        raise wire.TapeError(f"{where} must have exactly the documented side fields")
    fields = cast(dict[str, object], value)
    key, case = fields["key"], fields["case_sha256"]
    if not isinstance(key, str) or _LABEL.fullmatch(key) is None:
        raise wire.TapeError(f"{where} key must be a portable ASCII label")
    if not isinstance(case, str) or _SHA256.fullmatch(case) is None:
        raise wire.TapeError(f"{where} case_sha256 must be a lowercase SHA-256 digest")
    trial, decision = fields["native_trial_id"], fields["decision_sha256"]
    if trial is None and decision is None:
        return cast(_Side, value)
    if not _valid_uuid(trial):
        raise wire.TapeError(f"{where} requires a canonical native_trial_id UUID")
    if not isinstance(decision, str) or _SHA256.fullmatch(decision) is None:
        raise wire.TapeError(f"{where} requires a lowercase decision_sha256 digest")
    return cast(_Side, value)


def _read_manifest(path: Path) -> list[_Pair]:
    value = wire.decode(path.read_bytes())
    if not isinstance(value, dict) or value.keys() != {"schema", "pairs"}:
        raise wire.TapeError("invalid seatlab.calibration/v1 manifest")
    manifest = cast(dict[str, object], value)
    items = manifest["pairs"]
    if manifest["schema"] != _MANIFEST_SCHEMA or not isinstance(items, list):
        raise wire.TapeError("invalid seatlab.calibration/v1 manifest")
    pairs: list[_Pair] = []
    ids: set[str] = set()
    cases: set[str] = set()
    keys: set[str] = set()
    trial_sides: dict[str, str] = {}
    for value in cast(list[object], items):
        if not isinstance(value, dict) or value.keys() != _Pair.__annotations__.keys():
            raise wire.TapeError("calibration pair must have exactly the documented fields")
        item = cast(dict[str, object], value)
        pair_id = item["id"]
        if not isinstance(pair_id, str) or _LABEL.fullmatch(pair_id) is None:
            raise wire.TapeError("calibration pair id must be a portable ASCII label")
        if pair_id in ids:
            raise wire.TapeError(f"duplicate calibration pair id {pair_id!r}")
        ids.add(pair_id)
        source = _side(item["source"], f"pair {pair_id!r} source")
        target = _side(item["target"], f"pair {pair_id!r} target")
        case = source["case_sha256"]
        if case != target["case_sha256"]:
            raise wire.TapeError(f"pair {pair_id!r} has different case_sha256 digests")
        if case in cases:
            raise wire.TapeError(f"duplicate case_sha256 in pair {pair_id!r}")
        cases.add(case)
        for arm, side in (("source", source), ("target", target)):
            key, trial = side["key"], side["native_trial_id"]
            if key in keys:
                raise wire.TapeError(f"duplicate calibration side key {key!r}")
            keys.add(key)
            if trial is not None and trial_sides.setdefault(trial, arm) != arm:
                raise wire.TapeError(f"native_trial_id {trial!r} is used on both sides")
        pairs.append({"id": pair_id, "source": source, "target": target})
    return sorted(pairs, key=lambda pair: pair["id"])


def _check_coverage(name: str, expected: set[str], actual: set[str]) -> None:
    if expected != actual:
        missing, extra = expected - actual, actual - expected
        raise wire.TapeError(
            f"calibration {name} coverage mismatch: "
            f"{len(missing)} missing, {len(extra)} unexpected"
        )


def _get_decision(side: _Side, decisions: dict[str, wire.Decision]) -> wire.Decision | None:
    if side["native_trial_id"] is None:
        return None
    key = side["key"]
    row = decisions[key]
    if side["native_trial_id"] != row["native_trial_id"] or side[
        "decision_sha256"
    ] != wire.decision_sha256(row):
        raise wire.TapeError(f"calibration receipt does not match tape decision {key!r}")
    return row


def _arm_report(side: _Side, row: wire.Decision | None) -> dict[str, object]:
    return {
        **side,
        "basis": row["basis"] if row is not None else None,
        "observed": row["observed"] if row is not None else None,
    }


def _evaluate(
    pair: _Pair,
    source: wire.Decision | None,
    target: wire.Decision | None,
    prediction: wire.Result | None,
    context: ContextSpec | None,
    counts: dict[str, int],
) -> dict[str, object]:
    reasons: list[str] = []
    counts["source_available" if source is not None else "source_missing"] += 1
    counts["target_available" if target is not None else "target_missing"] += 1
    if source is None:
        reasons.append("source_missing")
    if target is None:
        reasons.append("target_missing")
    elif target["basis"] != "bound":
        reasons.append(f"target_{target['basis']}")
    elif target["observed"] is None:
        reasons.append("target_missing_observation")
    else:
        counts["target_acknowledged"] += 1

    source_observed = source["observed"] if source is not None else None
    target_observed = target["observed"] if target is not None else None
    native_action_changed = None
    if source_observed is not None and target_observed is not None:
        native_action_changed = not action_equal(
            source_observed["action"], target_observed["action"]
        )
        counts["native_action_compared"] += 1
        counts["native_action_changed"] += native_action_changed

    target_score = None
    if target is not None and target["basis"] == "bound" and target_observed is not None:
        try:
            target_score = wire.score(target, target_observed["action"])
        except wire.UnknownActionError:
            reasons.append("target_unscorable_action")

    prediction_report = None
    opaque = False
    if prediction is not None:
        source_digest = pair["source"]["decision_sha256"]
        assert source is not None and source_digest is not None
        verification = verify_calibration_result(
            source, prediction, source_digest, target, context
        )
        prediction_report = {**prediction, "input_verification": verification}
        counts[f"prediction_{prediction['status']}"] += 1
        if prediction["status"] == "unmeasured":
            reasons.append("prediction_unmeasured")
        if prediction["policy"] == "external":
            reasons.append("prediction_external_policy")
        if verification != "derived":
            reasons.append("prediction_context_unverified")
        opaque = prediction["policy"] == "external" or verification != "derived"
        if opaque:
            counts["prediction_opaque"] += 1
        if prediction["policy"] == "observed":
            counts["prediction_skipped"] += 1
            reasons.append("prediction_observed_policy")

    input_match = action_match = score_match = None
    if (
        prediction is not None
        and prediction["status"] == "measured"
        and prediction["policy"] != "observed"
        and target is not None
        and target_observed is not None
        and target_score is not None
    ):
        input_match = prediction["input_sha256"] == target["view_sha256"]
        action_match = action_equal(prediction["action"], target_observed["action"])
        score_match = prediction["score"] == target_score
    reproduced = False
    if source is None or target is None:
        status = "missing"
    elif opaque:
        status = "opaque"
    elif reasons:
        status = "skipped"
    else:
        assert (
            input_match is not None and action_match is not None and score_match is not None
        )
        counts["input_compared"] += 1
        if input_match:
            counts["input_matches"] += 1
            counts["action_compared"] += 1
            counts["action_matches"] += action_match
            counts["score_compared"] += 1
            counts["score_matches"] += score_match
        else:
            reasons.append("input_mismatch")
            counts["coincident_action_matches"] += action_match
        if not action_match:
            reasons.append("action_mismatch")
        if not score_match:
            reasons.append("score_mismatch")
        reproduced = input_match and action_match and score_match
        counts["reproduced"] += reproduced
        status = "reproduced" if reproduced else "mismatch"

    return {
        "id": pair["id"],
        "case_sha256": pair["source"]["case_sha256"],
        "source": _arm_report(pair["source"], source),
        "target": {**_arm_report(pair["target"], target), "score": target_score},
        "prediction": prediction_report,
        "status": status,
        "reasons": reasons,
        "input_match": input_match,
        "action_match": action_match,
        "score_match": score_match,
        "native_action_changed": native_action_changed,
        "reproduced": reproduced,
    }


def calibrate(
    tape: Path, replay: Path, pairs: Path, *, context: ContextSpec | None = None
) -> dict[str, object]:
    """Join a registered native cohort to source-only replay predictions.

    Return ``seatlab.calibration-result/v1`` with ``counts`` and pair rows sorted
    by ID. Tape coverage must equal all nonnull receipts. Replay coverage must
    equal the nonnull source receipts. Paired roles and offline answers must match.
    Null receipts have no rows. An observed or measured action can itself be null.
    Individual pair comparisons are null when not performed. Pair status is
    ``missing``, ``opaque``, ``skipped``, ``mismatch`` or ``reproduced``.

    Counters use these denominators:

    * ``pairs`` is the registered cohort. ``source_available`` and
      ``source_missing`` partition it by source receipt; ``target_available``
      and ``target_missing`` partition it by target receipt.
    * ``target_acknowledged`` counts bound targets with an observed action.
      ``native_action_compared`` counts pairs with 2 recorded native actions,
      regardless of view basis. ``native_action_changed`` counts their unequal
      actions, using exact action types. Completed null is an action.
    * ``prediction_measured`` and ``prediction_unmeasured`` partition the
      available source predictions by replay status. Within those predictions,
      ``prediction_opaque`` counts external policies or predictions without a
      verified context spec. ``prediction_skipped`` counts observed policies.
      Both counters are independent of replay status. Opaque rows show logged
      agreement with an acknowledged, scorable target when measured. Neither
      external nor observed policies run here.
    * ``input_compared`` counts measured deterministic predictions with checked
      inputs and an acknowledged, scorable target. ``input_matches`` counts
      equal digests. Opaque and observed predictions do not enter either count.
    * ``action_compared`` and ``score_compared`` each equal ``input_matches``.
      ``action_matches`` counts exact action equality in that set;
      ``score_matches`` counts equal offline score flags in that set.
      Pair rows also show action and score equality on input mismatch, and
      ``coincident_action_matches`` counts equal actions among those mismatches.
    * ``reproduced`` counts cases with equal input, action and score, not just
      equal reward. Missing, opaque and skipped cases do not count as failures.

    Verify replay scores against source answers and score native target actions
    independently. Recompute built-in transforms and deterministic policies on
    known inputs. A supplied context spec must match the ledger identifier and
    derive the committed input. Without the spec, a digest matching a tape view
    permits checking the byte count, not the derivation, and cannot reproduce.
    Hashes check consistency, not authenticity of the native receipt or ledger.
    """
    manifest = _read_manifest(pairs)
    decisions = {row["key"]: row for row in wire.read_tape(tape)}
    predictions = {row["key"]: row for row in wire.read_results(replay)}
    source_keys = {
        pair["source"]["key"]
        for pair in manifest
        if pair["source"]["native_trial_id"] is not None
    }
    target_keys = {
        pair["target"]["key"]
        for pair in manifest
        if pair["target"]["native_trial_id"] is not None
    }
    _check_coverage("tape", source_keys | target_keys, set(decisions))
    _check_coverage("replay", source_keys, set(predictions))

    counts = dict.fromkeys(_COUNTS, 0)
    counts["pairs"] = len(manifest)
    results = []
    for pair in manifest:
        source = _get_decision(pair["source"], decisions)
        target = _get_decision(pair["target"], decisions)
        if source is not None and target is not None:
            if source["role"] != target["role"]:
                raise wire.TapeError(f"pair {pair['id']!r} has different roles")
            if source["answers"] != target["answers"]:
                raise wire.TapeError(f"pair {pair['id']!r} has different offline answers")
        prediction = predictions.get(pair["source"]["key"])
        results.append(_evaluate(pair, source, target, prediction, context, counts))
    return {"schema": _RESULT_SCHEMA, "counts": counts, "pairs": results}
