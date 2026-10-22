"""Fail when any source file under the given folder is marked broken."""

import pathlib
import sys

broken = [path.name for path in sorted(pathlib.Path(sys.argv[1]).glob("*.txt"))
          if "BROKEN" in path.read_text(encoding="utf-8")]
if broken:
    print("broken sources: " + ", ".join(broken), file=sys.stderr)
    sys.exit(1)
