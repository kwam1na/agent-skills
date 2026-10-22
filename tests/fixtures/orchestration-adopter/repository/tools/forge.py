"""The fixture adopter's forge snapshot command: a fake forge's change requests.

Usage: forge.py <forge data file>

The data file lists the change requests the forge holds, as it reports them:
[{"subject", "state", "changeRequest", "head"?}], with state one of open,
draft, merged or closed. A data file that cannot be read is a forge failure.
The snapshot goes to stdout as work-snapshot/1.
"""

import datetime
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main(argv: list[str]) -> int:
    (data,) = argv
    try:
        requests = json.loads(Path(data).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"the forge did not answer: {error}", file=sys.stderr)
        return 1
    items = []
    for request in requests:
        item = {"subject": request["subject"], "state": request["state"], "changeRequest": request["changeRequest"]}
        if request.get("head"):
            item["head"] = request["head"]
        items.append(item)
    taken = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps({"takenAt": taken, "source": "forge", "items": items}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
