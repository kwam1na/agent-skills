"""Fail when any source file under the given folder is not one line ending in a newline."""

import pathlib
import sys

untidy = []
for path in sorted(pathlib.Path(sys.argv[1]).glob("*.txt")):
    contents = path.read_text(encoding="utf-8")
    if not contents.endswith("\n") or contents.count("\n") != 1:
        untidy.append(path.name)
if untidy:
    print("untidy sources: " + ", ".join(untidy), file=sys.stderr)
    sys.exit(1)
