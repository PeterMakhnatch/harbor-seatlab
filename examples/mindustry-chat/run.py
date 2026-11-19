"""Run the two fixed Mindustry chat attempts (read, no-read) with stock Harbor.

usage: python examples/mindustry-chat/run.py ORIGINAL_TASK --harbor ABS --out NEW_ROOT

Private files (configs, trials, game logs) stay under NEW_ROOT/private.
Only NEW_ROOT/public/{plan,read,no-read,summary}.json are meant for upload.
Both attempts always run once and are always reported. No retries.
Exit statuses are recorded; no game score is read or reported.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

HERE = Path(__file__).resolve().parent
ARMS = ("read", "no-read")
ALLOWED = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONTEXT")
ATTEMPT_SECONDS = 18 * 60  # far below the task default of 17+ hours
EVIDENCE_SECONDS = 120


def cpus():
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def clean_env():
    env = {key: os.environ[key] for key in ALLOWED if key in os.environ}
    env["PYTHONPATH"] = str(HERE)  # parent of native_seat.py
    return env


def run(cmd, timeout, cwd):
    """Run once in its own process group. Return (exit status, timed_out)."""
    try:
        proc = subprocess.Popen(
            cmd,
            env=clean_env(),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None, False
    try:
        return proc.wait(timeout=timeout), False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        return None, True
    except OSError:
        return None, False


def config(task, private, arm, granted):
    name = f"mindustry-chat-{uuid4().hex[:12]}-{arm}"
    env = {"type": "docker", "delete": True}
    if granted < 8:
        env["override_cpus"] = granted
    participants = [
        {
            "seat": seat,
            "import_path": "native_seat:ChatObserver",
            "model_name": None,
            "kwargs": {"arm": arm},
        }
        for seat in ("seat-1", "seat-2")
    ]
    cfg = {
        "schema_version": 1,
        "task": {"path": str(task)},
        "trials_dir": str(private / arm / "trials"),
        "trial_name": name,
        "participants": participants,
        "environment": env,
    }
    path = private / f"{arm}.json"
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path, private / arm / "trials" / name


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path, help="absolute original task directory")
    parser.add_argument(
        "--harbor", type=Path, required=True, help="absolute harbor executable"
    )
    parser.add_argument("--out", type=Path, required=True, help="new output root")
    args = parser.parse_args()
    task, harbor, out = args.task, args.harbor, args.out.expanduser().resolve()
    if not (
        task.is_absolute() and task.is_dir() and harbor.is_absolute() and harbor.is_file()
    ):
        parser.error("task and --harbor must be existing absolute paths")
    private, public = out / "private", out / "public"
    out.mkdir(mode=0o700)
    private.mkdir()
    public.mkdir()
    granted = cpus()

    write(
        public / "plan.json",
        {
            "schema": "seatlab.mindustry-chat-plan/v1",
            "arms": list(ARMS),
            "participants": 2,
            "granted_cpus": granted,
            "cpu_override": granted < 8,
            "attempt_timeout_seconds": ATTEMPT_SECONDS,
            "retries": 0,
        },
    )

    summary = {"schema": "seatlab.mindustry-chat-summary/v1", "arms": {}}
    for arm in ARMS:
        cfg, trial = config(task, private, arm, granted)
        status, timed_out = run(
            [str(harbor), "trial", "start", "-c", str(cfg)], ATTEMPT_SECONDS, private
        )
        # Collect even if the trial directory is missing; evidence.py does not need Harbor.
        evidence = public / f"{arm}.json"
        ev_status, ev_timeout = run(
            [
                sys.executable,
                str(HERE / "evidence.py"),
                str(trial),
                "--arm",
                arm,
                "--out",
                str(evidence),
            ],
            EVIDENCE_SECONDS,
            private,
        )
        summary["arms"][arm] = {
            "harbor_exit_status": status,
            "harbor_timed_out": timed_out,
            "evidence_exit_status": ev_status,
            "evidence_timed_out": ev_timeout,
            "evidence_present": evidence.is_file(),
        }
    write(public / "summary.json", summary)


if __name__ == "__main__":
    main()
