"""Recording fake for the forge's merge.

It records each call, then lands the candidate the way a squash-style forge
merge would: the expected head's tree on top of the checked base. Like a real
forge it refuses a head that is not the branch tip and pushes with a lease;
the runner's own tests own those guards, which nothing here exercises.
"""

import os
import subprocess
import sys

sys.path.insert(0, "tools")
from record import record

base, branch, expected, base_head = sys.argv[1:5]
remote = os.environ["FIXTURE_REMOTE"]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


record("merge", branch, expected, base_head)
tip = git("ls-remote", "--heads", remote, "refs/heads/" + branch).split()
if not tip or tip[0] != expected:
    record("merge-refused", branch)
    sys.exit(1)
git("fetch", "--quiet", remote, "+refs/heads/" + branch + ":refs/fixture/candidate")
tree = git("rev-parse", expected + "^{tree}")
commit = git("commit-tree", tree, "-p", base_head, "-p", expected, "-m", "Merge " + branch)
git("push", "--quiet", "--force-with-lease=refs/heads/" + base + ":" + base_head, remote,
    commit + ":refs/heads/" + base)
