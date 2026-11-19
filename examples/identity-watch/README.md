# Keep a tested Olam contribution ready while upstream moves

The [personal-fork UUID proposal](https://github.com/PeterMakhnatch/harbor-multiagent/pull/6) lets a scheduler reserve the trial ID before native agents start. This gives a separate result collector a stable way to match the returned world to its job. The [worker-routing control](../hanabi-panel/launcher-identity.md) shows why checking behavior alone cannot replace this link.

[Open the compatibility workflow.](https://github.com/PeterMakhnatch/harbor-seatlab/actions/workflows/reserved-id-watch.yml) It reads official Olam `main` and the official [pending sync](https://github.com/olamlabs/harbor-multiagent/pull/13) while that sync remains open. It never comments, pushes, or opens a PR upstream. It checks only the existing reserved-ID lifecycle test, not all of Harbor or a Docker world.

## What runs

The planner resolves immutable Git revisions. Each standard public Linux worker installs that revision’s locked runtime. It runs the proposal’s frozen test against the original source. If the completed trial returns the wrong ID, the worker integrates the proposed patch in a temporary Git tree. It runs the **same test** on that tree. The test checks both the returned result and the saved result. Each worker retains a compact JSON receipt for 14 days.

| Outcome | Meaning |
| --- | --- |
| `patch_fixes` | The unmodified source returns a different ID. The proposed integration passes the frozen test. |
| `upstream_satisfies` | The unmodified source already passes. The worker does not claim the proposal is still needed. |
| `still_fails` / `patch_conflict` | The proposal no longer fixes this contract, or Git cannot integrate it without a decision. |
| `inconclusive` | The worker cannot measure the contract. A skipped test, missing dependency, or unrelated failure is not a UUID mismatch. A setup failure before the reader starts produces no receipt. |

## Time and permissions

There are 7 scheduled dates: November 20–26, 2026, at 10:37 AM ET. Public GitHub schedules can start late. No test runs if the personal PR closes, its head changes, or the scheduled date is outside this window. Manual runs can bypass the date but not the ownership and revision checks. The schedule does not run models, Docker worlds, or paid cloud services. At most 2 test workers run per eligible day. Each has a 10-minute timeout.

Disable it immediately with `gh workflow disable reserved-id-watch.yml --repo PeterMakhnatch/harbor-seatlab`. The existing [real Docker trial](https://github.com/PeterMakhnatch/harbor-multiagent/pull/6) and [prospective sync result](https://github.com/PeterMakhnatch/harbor-multiagent/pull/6#issuecomment-7015964221) remain available after the watch ends.
