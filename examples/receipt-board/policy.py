"""Select the last visible option from one receipt-board actor view.

Reads a single JSON object ``{"role": ..., "options": [...]}`` on stdin and prints
``{"action": ...}`` on stdout. No offline grade or Harbor code is imported or read.
"""

from __future__ import annotations

import json
import sys


def decide(view: object) -> str:
    if not isinstance(view, dict) or view.keys() != {"role", "options"}:
        raise ValueError("expected a receipt-board view with exact fields role and options")
    role, options = view["role"], view["options"]
    if not isinstance(role, str) or not role:
        raise ValueError("role must be a nonempty string")
    if (
        not isinstance(options, list)
        or not options
        or any(not isinstance(option, str) or not option for option in options)
    ):
        raise ValueError("options must be a nonempty list of nonempty strings")
    return options[-1]


def main() -> int:
    action = decide(json.load(sys.stdin))
    print(json.dumps({"action": action}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
