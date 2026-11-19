"""Aggregate validated decision tapes and paired replay results."""

from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from .tape import Result, canonical, read_results, read_tape

_SOURCE_FIELDS: tuple[
    Literal["role", "native_trial_id", "source_view_sha256", "source_decision_sha256"], ...
] = (
    "role",
    "native_trial_id",
    "source_view_sha256",
    "source_decision_sha256",
)
_METRICS: tuple[Literal["optimal", "feasible"], ...] = ("optimal", "feasible")


class _PairTotals:
    def __init__(self) -> None:
        self.counts = dict.fromkeys(
            (
                "paired",
                "measured_pairs",
                "incomplete_pairs",
                "changed_actions",
                "changed_inputs",
                "both_optimal",
                "gained_optimal",
                "lost_optimal",
                "neither_optimal",
                "both_feasible",
                "gained_feasible",
                "lost_feasible",
                "neither_feasible",
                "baseline_unmeasured",
                "candidate_unmeasured",
                "baseline_completed_null",
                "candidate_completed_null",
                "baseline_input_bytes",
                "candidate_input_bytes",
                "design_pairs",
            ),
            0,
        )
        self.baseline_errors: Counter[str] = Counter()
        self.candidate_errors: Counter[str] = Counter()
        self.native_trials: set[str] = set()

    def add(self, left: Result, right: Result) -> None:
        counts = self.counts
        counts["paired"] += 1
        trial = left["native_trial_id"]
        if trial is None:
            counts["design_pairs"] += 1
        else:
            self.native_trials.add(trial)
        counts["changed_inputs"] += left["input_sha256"] != right["input_sha256"]
        counts["baseline_input_bytes"] += left["input_bytes"]
        counts["candidate_input_bytes"] += right["input_bytes"]

        left_score, right_score = left["score"], right["score"]
        left_error, right_error = left["error"], right["error"]
        if left_error is not None:
            counts["baseline_unmeasured"] += 1
            self.baseline_errors[left_error] += 1
        elif left["action"] is None:
            counts["baseline_completed_null"] += 1
        if right_error is not None:
            counts["candidate_unmeasured"] += 1
            self.candidate_errors[right_error] += 1
        elif right["action"] is None:
            counts["candidate_completed_null"] += 1

        if left_score is None or right_score is None:
            counts["incomplete_pairs"] += 1
            return
        counts["measured_pairs"] += 1
        left_action, right_action = left["action"], right["action"]
        counts["changed_actions"] += (
            type(left_action) is not type(right_action) or left_action != right_action
        )
        for metric in _METRICS:
            if left_score[metric]:
                outcome = "both" if right_score[metric] else "lost"
            else:
                outcome = "gained" if right_score[metric] else "neither"
            counts[f"{outcome}_{metric}"] += 1

    def summary(self) -> dict[str, object]:
        return {
            **self.counts,
            "native_trials": len(self.native_trials),
            "input_bytes_saved": (
                self.counts["baseline_input_bytes"] - self.counts["candidate_input_bytes"]
            ),
            "baseline_errors": dict(sorted(self.baseline_errors.items())),
            "candidate_errors": dict(sorted(self.candidate_errors.items())),
        }


def compare(baseline: Path, candidate: Path) -> dict[str, object]:
    """Compare the same decisions, counting outcomes only where both sides measured.

    Input counts and sizes include incomplete pairs. Byte savings are signed, and
    trial counts describe provenance, not independent samples for inference.
    This report checks the recorded commitments, but cannot authenticate the ledgers.
    """
    remaining = {row["key"]: row for row in read_results(baseline)}
    missing_from_baseline = 0
    total = _PairTotals()
    by_role: defaultdict[str, _PairTotals] = defaultdict(_PairTotals)
    by_trial: defaultdict[str, _PairTotals] = defaultdict(_PairTotals)

    for right in read_results(candidate):
        key = right["key"]
        left = remaining.pop(key, None)
        if left is None:
            missing_from_baseline += 1
            continue
        for field in _SOURCE_FIELDS:
            if left[field] != right[field]:
                raise ValueError(f"paired result {key!r} disagrees on {field}")
        if (
            left["input_sha256"] == right["input_sha256"]
            and left["input_bytes"] != right["input_bytes"]
        ):
            raise ValueError(f"paired result {key!r} has inconsistent input_bytes")

        total.add(left, right)
        by_role[left["role"]].add(left, right)
        trial = left["native_trial_id"]
        if trial is not None:
            by_trial[trial].add(left, right)

    if missing_from_baseline or remaining:
        raise ValueError(
            "result key sets differ: "
            f"{missing_from_baseline} missing from baseline, "
            f"{len(remaining)} missing from candidate"
        )
    return {
        "schema": "seatlab.paired-replay/v1",
        **total.summary(),
        "by_role": {role: totals.summary() for role, totals in sorted(by_role.items())},
        "by_native_trial": {
            trial: totals.summary() for trial, totals in sorted(by_trial.items())
        },
    }


def describe(tape_path: Path) -> dict[str, object]:
    """Summarize tape coverage without returning views, actions, or answers."""
    by_basis: Counter[str] = Counter()
    by_role: Counter[str] = Counter()
    trials: set[str] = set()
    view_lengths: set[int] = set()
    decisions = observed = observed_completed_null = 0
    for row in read_tape(tape_path):
        decisions += 1
        by_basis[row["basis"]] += 1
        by_role[row["role"]] += 1
        trial = row["native_trial_id"]
        if trial is not None:
            trials.add(trial)
        observation = row["observed"]
        if observation is not None:
            observed += 1
            if observation["action"] is None:
                observed_completed_null += 1
        view_lengths.add(len(canonical(row["view"])))

    return {
        "schema": "seatlab.tape-description/v1",
        "decisions": decisions,
        "by_basis": dict(sorted(by_basis.items())),
        "by_role": dict(sorted(by_role.items())),
        "native_trials": len(trials),
        "observed": observed,
        "observed_completed_null": observed_completed_null,
        "unobserved": decisions - observed,
        "distinct_view_lengths": sorted(view_lengths),
    }
