"""Compare only fully attested native chat attempts across two frozen Olam task revisions."""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

REVISIONS = (
    "124b275aa9e248ca18836432dc51658b4ac4f573",
    "6ee0648ce6b1e5c5bbcc6616e736ca54b5c42dac",
)
HARBOR = "4a194016336ccd1ce3fcc799b10158ca0375eb63"
ARMS = ("read", "no-read")
FACTS = (
    "transmit_accepted",
    "recipient_explicit_read",
    "dependent_reply_accepted",
    "sender_saw_ack",
)


def require(ok, msg):
    if not ok:
        raise ValueError(msg)


def document(path, schema=None):
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= 2_097_152,
        "expected a regular public receipt",
    )
    value = json.loads(path.read_bytes())
    require(
        isinstance(value, dict) and (schema is None or value.get("schema") == schema),
        "incorrect public receipt schema",
    )
    return value


def read_side(root, revision):
    require(
        document(root / "revision.json")
        == {"task_commit": revision, "harbor_commit": HARBOR},
        "incorrect source revision",
    )
    plan = document(root / "plan.json", "seatlab.mindustry-chat-plan/v1")
    require(
        plan.get("arms") == list(ARMS)
        and plan.get("retries") == 0
        and plan.get("participants") == 2,
        "incorrect native plan",
    )
    outcomes = document(root / "summary.json", "seatlab.mindustry-chat-summary/v1").get(
        "arms"
    )
    require(
        isinstance(outcomes, dict) and set(outcomes) == set(ARMS), "incorrect scheduled arms"
    )
    arms = {}
    for arm, execution in outcomes.items():
        require(
            isinstance(execution, dict)
            and all(
                type(execution.get(k)) is bool
                for k in ("evidence_present", "evidence_timed_out", "harbor_timed_out")
            )
            and all(
                execution.get(k) is None or type(execution[k]) is int
                for k in ("evidence_exit_status", "harbor_exit_status")
            ),
            "invalid execution summary",
        )
        path = root / f"{arm}.json"
        require(
            execution["evidence_present"] == (path.exists() or path.is_symlink()),
            "evidence receipt disagrees with execution",
        )
        trial = (
            document(path, "seatlab.native-chat-evidence/v1")
            if execution["evidence_present"]
            else None
        )
        ident, status, facts = None, "absent", dict.fromkeys(FACTS)
        if trial is not None:
            require(
                trial.get("arm") == arm
                and trial.get("status") in ("missing", "insufficient", "invalid", "complete"),
                "incorrect arm receipt",
            )
            ident, status = trial["native_trial_id"], trial["status"]
            require(
                ident is None or (isinstance(ident, str) and str(UUID(ident)) == ident),
                "incorrect native UUID",
            )
            observed = trial.get("facts")
            require(
                isinstance(observed, dict)
                and set(observed) == set(FACTS)
                and all(x is None or type(x) is bool for x in observed.values()),
                "incorrect recorded facts",
            )
            if status == "complete":
                require(
                    ident is not None
                    and trial.get("evidence")
                    == dict.fromkeys(
                        ("result", "journal", "sender_receipt", "receiver_receipt"), "ok"
                    ),
                    "inconsistent complete receipt",
                )
                if (
                    execution["harbor_exit_status"] == 0
                    and execution["evidence_exit_status"] == 0
                    and not execution["harbor_timed_out"]
                    and not execution["evidence_timed_out"]
                ):
                    facts = observed
        arms[arm] = {
            "native_trial_id": ident,
            "evidence": status,
            "execution": {
                k: execution[k]
                for k in (
                    "harbor_exit_status",
                    "harbor_timed_out",
                    "evidence_exit_status",
                    "evidence_timed_out",
                    "evidence_present",
                )
            },
            "facts": facts,
        }
    return {"task_commit": revision, "granted_cpus": plan["granted_cpus"], "arms": arms}


def compare(before, after):
    b, a = (
        read_side(folder, revision) for folder, revision in zip((before, after), REVISIONS)
    )
    ids = [
        t["native_trial_id"]
        for side in (b, a)
        for t in side["arms"].values()
        if t["native_trial_id"] is not None
    ]
    require(len(ids) == len(set(ids)), "duplicate native trial UUID")
    counts = {"stable": 0, "changed": 0, "unknown": 0}
    results = {}
    for arm in ARMS:
        results[arm] = {}
        for fact in FACTS:
            x, y = (side["arms"][arm]["facts"][fact] for side in (b, a))
            state = "unknown" if x is None or y is None else "stable" if x == y else "changed"
            results[arm][fact] = state
            counts[state] += 1
    return {
        "schema": "seatlab.mindustry-task-revision/v1",
        "harbor_commit": HARBOR,
        "before": b,
        "after": a,
        "facts": results,
        "counts": counts,
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("before", type=Path)
    cli.add_argument("after", type=Path)
    cli.add_argument("--out", type=Path, required=True)
    args = cli.parse_args()
    try:
        result = compare(args.before, args.after)
        with args.out.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write("\n")
    except (OSError, ValueError, KeyError) as err:
        print(f"cannot compare native receipts: {err}", file=sys.stderr)
        return 2
    print(json.dumps(result["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
