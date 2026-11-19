"""Create two fresh stock Harbor configs. Run the printed commands with Docker."""

import argparse
import json
import shlex
import shutil
from pathlib import Path
from uuid import uuid4


def prepare(out: Path) -> dict[str, str]:
    template = Path(__file__).resolve().parent / "task"
    out = out.expanduser().resolve()
    out.mkdir(mode=0o700)
    stamp = uuid4().hex[:16]
    plan: dict[str, str] = {}
    for mode in ("full", "first"):
        task = out / mode / "task"
        shutil.copytree(template, task)
        mode_file = task / "environment" / "mode.json"
        mode_file.write_text(json.dumps(mode) + "\n", encoding="utf-8")
        name = f"board-{stamp}-{mode}"
        trials = out / mode / "trials"
        config = {
            "schema_version": 1,
            "task": {"path": str(task)},
            "trials_dir": str(trials),
            "trial_name": name,
            "participants": [{"seat": seat, "name": "oracle"} for seat in ("p1", "p2")],
            "environment": {"type": "docker", "force_build": True, "delete": True},
        }
        config_path = out / f"{mode}.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        plan[mode] = str(trials / name)
    (out / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="New directory under an existing parent")
    args = parser.parse_args()
    try:
        plan = prepare(args.out)
    except OSError as exc:
        parser.exit(1, f"prepare: {exc}\n")
    out = args.out.expanduser().resolve()
    for mode in plan:
        print(shlex.join(["harbor", "trial", "start", "-c", str(out / f"{mode}.json")]))
    print(f"Trial paths: {out / 'plan.json'}")


if __name__ == "__main__":
    main()
