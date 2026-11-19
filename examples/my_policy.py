"""A trusted dispatch policy: read one projected observation and print one decision."""

import json
import sys

view = json.load(sys.stdin)
reports = view["messages"]
newest = max(reports, key=lambda report: report["revision"], default=None)
allowed = view["pooled_available"]
if allowed is None:
    allowed = newest["available"] if newest is not None else view["utilities"].keys()
bay = max(allowed, key=view["utilities"].__getitem__, default=None)
print(json.dumps({"action": bay}))
