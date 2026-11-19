#!/usr/bin/env python3
"""Print the compatibility-check plan as deterministic JSON (public GitHub API, no auth)."""
import argparse
import datetime
import json
import re
import sys
import urllib.request

API = "https://api.github.com"
UPSTREAM = "olamlabs/harbor-multiagent"
PERSONAL = "PeterMakhnatch/harbor-multiagent"
PERSONAL_PR = 6
PERSONAL_HEAD = "0e2b6a1289681996a691bbecec4448eaada083e8"
INCOMING_PR = 13
WINDOW = (datetime.date(2026, 11, 20), datetime.date(2026, 11, 26))
SHA = re.compile(r"[0-9a-f]{40}")


def get(path):
    req = urllib.request.Request(
        API + path,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "compat-watch-planner",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def head_info(pr):
    """Return (state, head_repo_full_name or None, head_sha) from a PR object."""
    if pr["state"] not in {"open", "closed"}:
        raise ValueError("unknown PR state")
    head = pr["head"]
    repo = head["repo"]
    sha = head["sha"]
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("bad head sha")
    return pr["state"], (repo["full_name"] if repo else None), sha


def plan():
    state, repo, sha = head_info(get(f"/repos/{PERSONAL}/pulls/{PERSONAL_PR}"))
    if state != "open" or repo != PERSONAL or sha != PERSONAL_HEAD:
        return []

    main_sha = get(f"/repos/{UPSTREAM}/git/ref/heads/main")["object"]["sha"]
    if not SHA.fullmatch(main_sha):
        raise ValueError("bad main sha")
    include = [{"name": "main", "revision": main_sha}]

    state, repo, sha = head_info(get(f"/repos/{UPSTREAM}/pulls/{INCOMING_PR}"))
    if state == "open" and repo == UPSTREAM and sha != main_sha:
        include.append({"name": "incoming-sync", "revision": sha})
    return include


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", action="store_true", help="bypass the calendar window only")
    args = ap.parse_args()

    today = datetime.datetime.now(datetime.timezone.utc).date()
    if not args.manual and not WINDOW[0] <= today <= WINDOW[1]:
        include = []
    else:
        try:
            include = plan()
        except Exception as exc:  # network or schema failure: no fake pass
            print(f"planner: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    print(json.dumps({"include": include}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
