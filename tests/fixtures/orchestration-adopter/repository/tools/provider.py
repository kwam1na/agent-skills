"""The adopter's affected-checks provider.

A source change selects the lint check, unless the binding already runs it on
every candidate. Generated files, tools and the binding are covered by those
checks; anything else is reported uncovered. While the work root holds a
provider-down marker, the provider is unavailable.
"""

import json
import pathlib
import sys

request = json.load(sys.stdin)
if (pathlib.Path(sys.argv[1]) / "provider-down").exists():
    print("the provider's index is unavailable", file=sys.stderr)
    sys.exit(3)
binding = json.loads(pathlib.Path(".agents/orchestration.json").read_text(encoding="utf-8"))
always = {check["id"] for check in binding["alwaysRun"]}
checks, uncovered = [], []
for change in request["changes"]:
    path = change["path"]
    if path.startswith("src/"):
        if "lint" not in always and not checks:
            checks.append({
                "id": "lint",
                "argv": [sys.executable, "-B", "tools/lint.py", "src"],
                "cwd": ".",
                "reasons": ["a source file changed"],
                "prerequisites": [],
            })
    elif not path.startswith(("generated/", "tools/", ".agents/")):
        uncovered.append(path)
print(json.dumps({"checks": checks, "widened": False, "uncovered": uncovered}))
