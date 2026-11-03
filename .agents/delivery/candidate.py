#!/usr/bin/env python3
"""Resolve a ticket to the branch and head of its one open change request into the delivery base."""

import json
import subprocess
import sys
from pathlib import Path

binding = json.loads((Path(__file__).resolve().parents[1] / "orchestration.json").read_text(encoding="utf-8"))
listed = subprocess.run(["gh", "pr", "list", "--repo", "kwam1na/agent-skills", "--base", binding["base"]["ref"],
                         "--state", "open", "--limit", "100", "--json", "headRefName,headRefOid,title"],
                        capture_output=True, text=True, check=True).stdout
pulls = [pull for pull in json.loads(listed) if sys.argv[1] in pull["title"]]
if len(pulls) != 1:
    sys.exit(f"{sys.argv[1]}: expected one open change request, found {len(pulls)}")
print(json.dumps({"branch": pulls[0]["headRefName"], "head": pulls[0]["headRefOid"]}))
