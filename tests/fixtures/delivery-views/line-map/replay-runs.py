#!/usr/bin/env python3
"""A runs command that answers from a case's recorded runs-commands.json.

    python3 replay-runs.py <runs-commands.json> runs view <id> --json

Prints the recorded stdout for the arguments after the file, or exits 1 when the
recording holds null or nothing for them.
"""

import json
import sys

recorded = json.loads(open(sys.argv[1], encoding="utf-8").read())
answer = recorded.get(" ".join(sys.argv[2:]))
if answer is None:
    raise SystemExit(1)
sys.stdout.write(answer)
