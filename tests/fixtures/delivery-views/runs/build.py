#!/usr/bin/env python3
"""Builds the runs page's recorded runs commands from the runtime's captured output (tests/fixtures/run-events/runs).

    python3 -B tests/fixtures/delivery-views/runs/build.py [--check]

Each captured file under tests/fixtures/run-events/runs/ holds the runtime's own ``runs list --json`` and, per run,
``runs view`` and ``runs show``. The captures all ran in fresh stores, so they share run ids and times: each run gets
its own id and each capture its own hour, and nothing else changes. The result is ``runs-commands.json`` beside this
script, in the shape replay-runs.py answers from. With ``--check`` it exits 1 when the file differs from a rebuild.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAPTURED = HERE.parents[2] / "fixtures" / "run-events" / "runs"
# Oldest first: each capture's hour, so newest first is a fixed order.
HOURS = {
    "displaced-then-partial": "03",
    "ended-plan-then-execute": "04",
    "open-waiting-on-an-agent": "05",
    "open-review-entered-again": "06",
    "open-execute-entered-again": "07",
    "open-execute-inside-plan": "08",
}
RUNS_DIR = "/private/fixture/.git/managed-delivery/runs"


def build() -> dict:
    commands: dict[str, str] = {}
    listed = []
    for name, hour in HOURS.items():
        text = (CAPTURED / f"{name}.json").read_text(encoding="utf-8")
        captured = json.loads(text)
        for run_id in sorted(captured["runs"], reverse=True):
            # Longest first, so run-...-10 is never caught by run-...-1.
            text = text.replace(run_id, f"run-{name}-{run_id.rsplit('-', 1)[-1].lstrip('0') or '0'}")
        text = text.replace("2026-11-20T09:", f"2026-11-20T{hour}:")
        captured = json.loads(text)
        listed.extend(captured["list"]["runs"])
        for run_id, read in captured["runs"].items():
            commands[f"runs view {run_id} --json"] = json.dumps(read["view"], ensure_ascii=False)
            commands[f"runs show {run_id} --json"] = json.dumps(read["show"], ensure_ascii=False)
    inventory = {"spec": "run-inventory/1", "runsDir": RUNS_DIR, "runs": listed,
                 "total": {"count": len(listed), "bytes": sum(row["bytes"] for row in listed)},
                 "returned": len(listed), "truncated": False}
    return {"runs list --json": json.dumps(inventory, ensure_ascii=False), **dict(sorted(commands.items()))}


def main() -> int:
    target = HERE / "runs-commands.json"
    built = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if sys.argv[1:] == ["--check"]:
        return 0 if target.read_text(encoding="utf-8") == built else 1
    target.write_text(built, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
