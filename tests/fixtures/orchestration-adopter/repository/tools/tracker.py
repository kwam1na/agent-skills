"""The fixture adopter's tracker snapshot command: a fake tracker's children of one parent.

Usage: tracker.py <parent> <tracker data file>

The data file maps each parent to its child items, as the tracker lists them:
[{"reference", "title", "stateType", "blockedBy"}]. A parent the file does not
hold is a tracker failure. The snapshot goes to stdout as work-snapshot/1.
"""

import datetime
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main(argv: list[str]) -> int:
    parent, data = argv
    children = json.loads(Path(data).read_text(encoding="utf-8"))
    if parent not in children:
        print(f"the tracker has no item {parent}", file=sys.stderr)
        return 1
    items = []
    for child in children[parent]:
        item = {"subject": child["reference"], "title": child["title"], "state": child["stateType"]}
        if child.get("blockedBy"):
            item["blockedBy"] = child["blockedBy"]
        items.append(item)
    taken = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps({"takenAt": taken, "source": "tracker", "items": items}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
