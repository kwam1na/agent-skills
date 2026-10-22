"""Resolve a queue subject to its lane branch and the branch's tip on the remote."""

import json
import os
import subprocess
import sys

subject = sys.argv[1]
branch = "lane/" + subject
listed = subprocess.run(
    ["git", "ls-remote", "--heads", os.environ["FIXTURE_REMOTE"], "refs/heads/" + branch],
    capture_output=True, text=True, check=True,
).stdout.split()
if not listed:
    sys.exit(3)
print(json.dumps({"branch": branch, "head": listed[0]}))
