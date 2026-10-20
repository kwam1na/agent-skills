"""Rebuild generated/catalog.txt from the sources: one line per source file."""

import pathlib

lines = []
for path in sorted(pathlib.Path("src").glob("*.txt")):
    first = path.read_text(encoding="utf-8").splitlines()[0]
    lines.append(f"{path.name}: {first}\n")
target = pathlib.Path("generated/catalog.txt")
target.parent.mkdir(exist_ok=True)
target.write_text("".join(lines), encoding="utf-8")
