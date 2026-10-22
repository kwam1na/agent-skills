"""The fallback check: lint everything, after keeping a copy of the live view.

The copy shows what the delivery's markdown view said while this check ran.
"""

import os
import pathlib
import shutil
import subprocess
import sys

snapshots = pathlib.Path(os.environ["FIXTURE_SNAPSHOTS"])
snapshots.mkdir(parents=True, exist_ok=True)
view = pathlib.Path(os.environ["FIXTURE_STATE"]) / "delivery.md"
shutil.copyfile(view, snapshots / f"full-{len(list(snapshots.iterdir())) + 1}.md")
sys.exit(subprocess.run([sys.executable, "-B", "tools/lint.py", "src"]).returncode)
