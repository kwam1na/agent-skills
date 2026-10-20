"""The adopter's affected-checks provider.

A source change selects the format check and the lint check, leaving out any
check the binding already runs on every candidate. Generated files, tools and
the binding are covered by those checks; anything else is reported uncovered.

While the work root holds a provider-down file, the provider is unavailable
for any request that changes a path the file lists.
"""

import json
import pathlib
import sys

request = json.load(sys.stdin)
marker = pathlib.Path(sys.argv[1]) / "provider-down"
if marker.exists():
    down = set(marker.read_text(encoding="utf-8").split())
    if any(change["path"] in down for change in request["changes"]):
        print("the provider's index is unavailable", file=sys.stderr)
        sys.exit(3)
binding = json.loads(pathlib.Path(".agents/orchestration.json").read_text(encoding="utf-8"))
always = {check["id"] for check in binding["alwaysRun"]}
checks, uncovered = [], []


def select(check_id: str, tool: str, reason: str) -> None:
    if check_id in always or any(check["id"] == check_id for check in checks):
        return
    checks.append({
        "id": check_id,
        "argv": [sys.executable, "-B", tool, "src"],
        "cwd": ".",
        "reasons": [reason],
        "prerequisites": [],
    })


for change in request["changes"]:
    path = change["path"]
    if path.startswith("src/"):
        select("format", "tools/format.py", "a source file changed")
        select("lint", "tools/lint.py", "a source file changed")
    elif not path.startswith(("generated/", "tools/", ".agents/")):
        uncovered.append(path)
print(json.dumps({"checks": checks, "widened": False, "uncovered": uncovered}))
