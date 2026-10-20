"""Fail when any source file under the given folder is not one line ending in a newline.

While the work root holds a move-base file, this check also lands one commit
on the base as it runs, once, the way another merge could land between a
candidate's check and its merge.
"""

import json
import pathlib
import shutil
import subprocess
import sys

folder, work_root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
marker = work_root / "move-base"
if marker.exists():
    marker.unlink()
    base = json.loads(pathlib.Path(".agents/orchestration.json").read_text(encoding="utf-8"))["base"]
    remote = subprocess.run(["git", "remote", "get-url", base["remote"]], capture_output=True, text=True,
                            check=True).stdout.strip()
    elsewhere = work_root / "another merge"
    shutil.rmtree(elsewhere, ignore_errors=True)
    subprocess.run(["git", "clone", "--quiet", "--branch", base["ref"], remote, str(elsewhere)], check=True)
    (elsewhere / "src" / "gamma.txt").write_text("gamma two\n", encoding="utf-8")
    subprocess.run([sys.executable, "-B", "tools/regenerate.py"], cwd=elsewhere, check=True)
    subprocess.run(["git", "commit", "--quiet", "--all", "-m", "Another merge lands"], cwd=elsewhere, check=True)
    subprocess.run(["git", "push", "--quiet", remote, "HEAD:" + base["ref"]], cwd=elsewhere, check=True)
    shutil.rmtree(elsewhere, ignore_errors=True)

untidy = []
for path in sorted(folder.glob("*.txt")):
    contents = path.read_text(encoding="utf-8")
    if not contents.endswith("\n") or contents.count("\n") != 1:
        untidy.append(path.name)
if untidy:
    print("untidy sources: " + ", ".join(untidy), file=sys.stderr)
    sys.exit(1)
