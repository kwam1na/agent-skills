#!/usr/bin/env python3
"""Resolve a ticket to the branch and head of its one open change request into the delivery base."""

import json
import re
import subprocess
import sys
from pathlib import Path

binding = json.loads((Path(__file__).resolve().parents[1] / "orchestration.json").read_text(encoding="utf-8"))
listed = subprocess.run(["gh", "pr", "list", "--repo", "kwam1na/agent-skills", "--base", binding["base"]["ref"],
                         "--state", "open", "--limit", "100", "--json", "headRefName,headRefOid,title"],
                        capture_output=True, text=True, check=True).stdout


def named(title: str) -> list[str]:
    """The keys a title opens with, as `<key>: ...`, `[<key>]: ...` or `[<key>, <key>]: ...`, read as the forge snapshot
    reads them, so a change request the line map counts for a key is the one its candidate step finds. A later ticket
    naming this one further on isn't taken for it."""
    opening = re.match(r"^\[([^\]]+)\]", title.strip()) or re.match(r"^([^\s:\[\]]+):\s", title.strip())
    return [part for part in re.split(r"[\s,]+", opening.group(1)) if part] if opening else []


pulls = [pull for pull in json.loads(listed) if sys.argv[1] in named(pull["title"])]
if len(pulls) != 1:
    sys.exit(f"{sys.argv[1]}: expected one open change request, found {len(pulls)}")
print(json.dumps({"branch": pulls[0]["headRefName"], "head": pulls[0]["headRefOid"]}))
