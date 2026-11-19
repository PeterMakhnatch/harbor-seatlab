"""Make one cursor decision from one coordinator view. No Harbor dependency."""

import argparse
import json
import sys


def decide(view: dict, budget: int | None) -> str | None:
    if view.keys() == {"role", "mode", "horizon", "duration", "slots_per_day", "busy"}:
        if view["role"] != "m1" or view["mode"] != "cursor":
            raise ValueError("expected the cursor coordinator's initial view")
        return "query:0"
    if view.keys() != {"round", "bound", "replies"}:
        raise ValueError("expected an acknowledged cursor round")
    number, bound, replies = view["round"], view["bound"], view["replies"]
    if type(number) is not int or number < 1 or type(bound) is not int or bound < 0:
        raise ValueError("invalid round number or bound")
    if not isinstance(replies, dict) or replies.keys() != {"m1", "m2", "m3", "m4", "m5"}:
        raise ValueError("expected one reply from each seat")
    ticks = tuple(replies.values())
    if any(tick is not None and (type(tick) is not int or tick < bound) for tick in ticks):
        raise ValueError("invalid start time")
    if None in ticks:
        return None
    next_bound = max(ticks)
    if all(tick == next_bound for tick in ticks):
        return f"choose:{next_bound}"
    if budget is not None and number >= budget:
        return None
    return f"query:{next_bound}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--budget-rounds", type=int, help="stop after this many complete rounds"
    )
    args = parser.parse_args()
    if args.budget_rounds is not None and args.budget_rounds < 1:
        parser.error("--budget-rounds must be positive")
    print(json.dumps({"action": decide(json.load(sys.stdin), args.budget_rounds)}))
