"""Check replay rows against committed source decisions without running programs."""

from __future__ import annotations

from hashlib import sha256

from . import replay as engine
from . import tape as wire
from .context import ContextSpec


def action_equal(left: wire.Action, right: wire.Action) -> bool:
    return type(left) is type(right) and left == right


def _check_input(
    source: wire.Decision,
    result: wire.Result,
    target: wire.Decision | None,
    context: ContextSpec | None,
) -> tuple[dict[str, object] | None, str]:
    transform = result["transform"]
    claimed = result["input_sha256"]
    key = source["key"]
    if transform == "identity":
        # The result validator ties the identity input digest to its source digest.
        view = source["view"]
        if result["input_bytes"] != len(wire.canonical(view)):
            raise wire.TapeError(f"replay input byte count does not match {key!r}")
        return view, "derived"
    if transform == "latest" or context is not None:
        if transform != "latest" and (context is None or transform != context.identifier):
            raise wire.TapeError(f"replay context identifier does not match {key!r}")
        try:
            if transform == "latest":
                view = engine._transform(source["view"], transform)
            else:
                assert context is not None
                view = context.apply(source["view"])
        except ValueError as exc:
            raise wire.TapeError(f"cannot derive replay input for {key!r}") from exc
        data = wire.canonical(view)
        if claimed != sha256(data).hexdigest() or result["input_bytes"] != len(data):
            raise wire.TapeError(f"replay input commitment does not match {key!r}")
        return view, "derived"
    if claimed == source["view_sha256"]:
        view = source["view"]
    elif target is not None and claimed == target["view_sha256"]:
        view = target["view"]
    else:
        # Calibration can report a commitment whose recipe was not supplied.
        return None, "unavailable"
    if result["input_bytes"] != len(wire.canonical(view)):
        raise wire.TapeError(f"replay input byte count does not match {key!r}")
    return view, "known_view"


def _check_deterministic_result(
    source: wire.Decision, result: wire.Result, view: dict[str, object]
) -> None:
    key = source["key"]
    try:
        # The caller excludes external policies, so no process arguments are used.
        action = engine._choose(source, view, result["policy"], (), b"", 0)
    except engine._PolicyFailure as exc:
        if result["error"] != exc.reason:
            raise wire.TapeError(f"replay status does not match policy for {key!r}") from exc
        return
    except ValueError as exc:
        raise wire.TapeError(f"replay policy cannot use committed input for {key!r}") from exc
    if result["status"] == "measured":
        if not action_equal(action, result["action"]):
            raise wire.TapeError(
                f"replay action does not match deterministic policy for {key!r}"
            )
        return
    try:
        wire.score(source, action)
    except wire.UnknownActionError as exc:
        if result["error"] != "invalid_action":
            raise wire.TapeError(f"replay status does not match policy for {key!r}") from exc
        return
    raise wire.TapeError(f"replay omits a deterministic measurement for {key!r}")


def _verify_result(
    source: wire.Decision,
    result: wire.Result,
    source_decision_sha256: str,
    target: wire.Decision | None,
    context: ContextSpec | None,
    *,
    check_observed: bool,
) -> str:
    key = source["key"]
    basis = source["basis"]
    if basis == "bound" and result["transform"] != "identity":
        basis = "reconstructed"
    if (
        result["key"] != key
        or result["role"] != source["role"]
        or result["native_trial_id"] != source["native_trial_id"]
        or result["basis"] != basis
        or result["source_view_sha256"] != source["view_sha256"]
        or result["source_decision_sha256"] != source_decision_sha256
    ):
        raise wire.TapeError(f"replay source does not match tape decision {key!r}")

    if result["status"] == "measured":
        try:
            expected_score = wire.score(source, result["action"])
        except wire.UnknownActionError as exc:
            raise wire.TapeError(f"replay claims an unscorable action for {key!r}") from exc
        if result["score"] != expected_score:
            raise wire.TapeError(f"replay score does not match offline answers for {key!r}")

    view, input_verification = _check_input(source, result, target, context)
    policy = result["policy"]
    if view is not None and policy != "external" and (policy != "observed" or check_observed):
        _check_deterministic_result(source, result, view)
    return input_verification


def verify_calibration_result(
    source: wire.Decision,
    result: wire.Result,
    source_decision_sha256: str,
    target: wire.Decision | None,
    context: ContextSpec | None,
) -> str:
    """Check a calibration row, allowing opaque input and skipping observed replay."""
    return _verify_result(
        source, result, source_decision_sha256, target, context, check_observed=False
    )


def verify_worker_result(
    source: wire.Decision,
    result: wire.Result,
    policy: wire.Policy,
    transform_id: str,
    context: ContextSpec | None,
) -> None:
    """Check a validated worker row against its registered recipe and source.

    External results attest neither execution nor choice. Only their input and
    score can be checked beyond the status constraints enforced by the wire format.
    """
    key = source["key"]
    if result["policy"] != policy:
        raise wire.TapeError(f"replay policy does not match registered policy for {key!r}")
    if result["transform"] != transform_id:
        raise wire.TapeError(
            f"replay transform does not match registered transform for {key!r}"
        )
    if context is None:
        if transform_id not in ("identity", "latest"):
            raise wire.TapeError(f"replay context spec is required for {key!r}")
    elif transform_id in ("identity", "latest"):
        raise wire.TapeError(f"replay context identifier does not match {key!r}")
    _verify_result(
        source, result, wire.decision_sha256(source), None, context, check_observed=True
    )
