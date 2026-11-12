from __future__ import annotations

import contextlib
import copy
import errno
import gc
import hashlib
import io
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

import agent_skills
from agent_skills.validate import LEAKAGE_PATTERNS
from quiet_git import quiet_clone, quieten


ROOT = Path(__file__).resolve().parents[1]
# The product under test: this repository, or the extracted release candidate that
# qualification puts first on the path, so its shipped scripts are the ones exercised.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
SKILL = PRODUCT_ROOT / "skills/orchestrate-work"
SCRIPTS = SKILL / "scripts"
SCHEMA_FILE = SKILL / "references/schemas.json"


def schema(name: str) -> dict:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))[f"{name}/1"]
PY = sys.executable


def _load(name: str):
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))
        sys.dont_write_bytecode = previous


state_tool = _load("delivery_state")
queue = _load("merge_queue")


# The fixture adopter's tools. Each is a small script committed to the base.
TOOLS = {
    "candidate.py": """
import json, os, subprocess, sys
subject = sys.argv[1]
branch = subject
listed = subprocess.run(["git", "ls-remote", "--heads", os.environ["MQ_REMOTE"], "refs/heads/" + branch],
                        capture_output=True, text=True, check=True).stdout.split()
if not listed:
    sys.exit(3)
print(json.dumps({"branch": branch, "head": listed[0]}))
""",
    "regen.py": """
import os, pathlib, time
root = pathlib.Path(".")
# Bytes in, bytes out, so line endings pass through unchanged.
text = b"".join(path.read_bytes() for path in sorted((root / "src").glob("*.txt")))
if os.environ.get("MQ_REGEN") == "unstable":
    text += str(time.time_ns()).encode() + b"\\n"
(root / "gen").mkdir(exist_ok=True)
(root / "gen" / "generated.txt").write_bytes(text)
if os.environ.get("MQ_REGEN") == "twice":
    # Settles only on a third pass.
    count = root / "gen" / "count.txt"
    count.write_text(str(min(int(count.read_text()) + 1 if count.exists() else 1, 2)))
""",
    "fingerprint.py": """
import pathlib, subprocess, sys
base = subprocess.run(["git", "merge-base", "HEAD", sys.argv[1]], capture_output=True, text=True, check=True).stdout
path = pathlib.Path("notes")
path.mkdir(exist_ok=True)
(path / "fingerprint.txt").write_text(base)
if len(sys.argv) > 2:
    pathlib.Path(sys.argv[2]).write_text("undeclared\\n")
""",
    "check.py": """
import os, pathlib, subprocess, sys, time
name = sys.argv[1]
with open(os.environ["MQ_LOG"], "a") as log:
    log.write("check " + name + " " + os.environ.get("MQ_PROFILE_HEAD", "-") + "\\n")
args = sys.argv[2:]
if args[:1] == ["--sleep-child"]:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pathlib.Path(os.environ["MQ_LOG"] + ".child").write_text(str(child.pid))
    time.sleep(60)
if args[:1] == ["--fail-with"]:
    # Fails with one line per failing test id, as a test runner prints them, but
    # only with the candidate's change: the base itself passes.
    if pathlib.Path("src/app.txt").read_text() == "app one\\n":
        sys.exit(0)
    for test in args[1:]:
        print("FAIL: " + test + " boom")
    print("some other output")
    sys.exit(1)
if args[:1] == ["--base-flake"]:
    # Fails for real on a tree carrying the candidate's file; on the base, fails on a flaky test, once or always.
    if pathlib.Path("src/candidate.txt").exists():
        print("FAIL: test_real boom")
        sys.exit(1)
    flag = pathlib.Path(os.environ["MQ_LOG"] + ".base-flaked")
    if args[1] == "always" or not flag.exists():
        flag.write_text("")
        print("FAIL: test_launcher_cancel flaked on the base")
        sys.exit(1)
    sys.exit(0)
if args[:1] == ["--flaky-once"]:
    # Fails the first time only, naming its tests; a flag file remembers the first run.
    flag = pathlib.Path(os.environ["MQ_LOG"] + ".flaked-" + name)
    if not flag.exists():
        flag.write_text("")
        for test in args[1:]:
            print("FAIL: " + test + " flaked")
        sys.exit(1)
    sys.exit(0)
if args[:1] == ["--hang-once"]:
    # Says it started, then hangs while the hang flag exists, so a test can kill the runner mid-check.
    pathlib.Path(os.environ["MQ_LOG"] + ".started").write_text(str(os.getpid()))
    while pathlib.Path(os.environ["MQ_LOG"] + ".hang").exists():
        time.sleep(0.1)
    sys.exit(0)
if args[:1] == ["--lead-writes"]:
    # The lead edits another queue item while this check runs.
    sys.path.insert(0, os.environ["MQ_SCRIPTS"])
    import delivery_state
    directory = pathlib.Path(os.environ["MQ_STATE"])
    current = delivery_state.read_state(directory)
    delivery_state.patch(directory, writer="lead", version=current["version"],
                         selector="queue[subject=other]", changes={"title": "Renamed by the lead"})
    sys.exit(0)
if args[:1] == ["--snapshot"]:
    # What the views show while this check runs.
    import json
    state = json.loads((pathlib.Path(os.environ["MQ_STATE"]) / "state.json").read_text(encoding="utf-8"))
    now = state["meta"]["checking"]["now"]
    with open(os.environ["MQ_LOG"] + ".snap", "a") as snap:
        snap.write(json.dumps({"check": name, "steps": now["steps"] if now else None,
                               "queue": state["queue"][0]["checks"]}) + "\\n")
    sys.exit(1 if args[1:2] == ["fail"] else 0)
if args[:1] == ["--no-material"]:
    sys.exit(5 if pathlib.Path(".lease-material").exists() else 0)
if args[:1] == ["--always-fail"]:
    sys.exit(1)
import hashlib
built = pathlib.Path(os.environ["MQ_LOG"] + ".built-" + hashlib.sha256(str(pathlib.Path.cwd()).encode()).hexdigest()[:16])
if args[:1] == ["--build"]:
    built.write_text("built\\n")
    sys.exit(0)
if args[:1] == ["--needs-build"]:
    if not built.exists():
        sys.exit(2)
    sys.exit(1 if "FAIL" in pathlib.Path("src/app.txt").read_text() else 0)
if args[:1] == ["--escape"]:
    # A grandchild that leaves the group but keeps the output pipes open.
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    time.sleep(60)
if args[:1] == ["--dirty"]:
    pathlib.Path("stray-output.txt").write_text("left behind\\n")
    sys.exit(0)
if args[:1] == ["--implicit"]:
    # Fails on a change the check does not name.
    sys.exit(1 if "FAIL" in pathlib.Path("src/app.txt").read_text() else 0)
for path in args:
    if "FAIL" in pathlib.Path(path).read_text():
        sys.exit(1)
""",
    "provider.py": """
import json, os, pathlib, sys, time
request = json.load(sys.stdin)
mode = pathlib.Path("tools/provider-mode.txt").read_text().strip()
py = sys.executable
def check(cid, *args, **more):
    item = {"id": cid, "argv": [py, "tools/check.py", cid, *args], "cwd": ".", "reasons": ["changed"], "prerequisites": []}
    item.update(more)
    return item
if mode == "fail":
    sys.exit(2)
if mode == "refuse":
    if os.environ.get("MQ_STEP_TOKEN"):
        sys.stderr.write("asked with " + os.environ["MQ_STEP_TOKEN"] + "\\n")
    sys.stderr.write("over the budget: unit (400s), e2e (700s)\\n")
    sys.exit(3)
if mode == "malformed":
    print("not json")
    sys.exit(0)
if mode == "timeout":
    time.sleep(30)
checks, uncovered = [], []
for change in request["changes"]:
    path = change["path"]
    if path.startswith("src/"):
        if not any(item["id"] == "unit" for item in checks):
            checks.append(check("unit", "src/app.txt"))
    elif path.startswith("tests/"):
        checks.append(check("test:" + path, path))
    elif not path.startswith(("gen/", "notes/", "tools/", ".agents/")):
        uncovered.append(path)
if mode == "empty":
    checks, uncovered = [], []
if mode == "cycle":
    checks = [check("a", prerequisites=["b"]), check("b", prerequisites=["a"])]
if mode == "outside":
    checks = [check("escape", cwd="..")]
if mode == "profile":
    checks = [check("profiled", environmentProfile="missing")]
if mode == "ordered":
    checks = [check("second", prerequisites=["first"]), check("first")]
if mode == "new-cwd":
    tool = str(pathlib.Path("tools/check.py").resolve())
    checks = [{"id": "in-new-dir", "argv": [py, tool, "in-new-dir", "--always-fail"], "cwd": "newdir",
               "reasons": ["changed"], "prerequisites": []}]
if mode == "trio":
    checks = [check("a", "--snapshot"), check("b", "--snapshot", "fail", "src/app.txt"), check("c", "--snapshot"),
              check("d", "--snapshot", prerequisites=["b"])]
if mode == "implicit":
    checks = [check("implicit", "--implicit")]
if mode == "shadow":
    checks = [{"id": "always", "argv": [py, "-c", "pass"], "cwd": ".", "reasons": ["shadow"], "prerequisites": []}]
if mode == "dummy":
    checks = [check("dummy")]
if mode == "titled":
    checks = [check("unit", "src/app.txt", title="Unit tests (webapp) <b>&"), check("plain")]
if mode == "long-title":
    checks = [check("unit", "src/app.txt", title="Behaviour scenario: " + "x" * 400)]
if mode == "exact-title":
    checks = [check("unit", "src/app.txt", title="T" * 200)]
if mode == "empty-title":
    checks = [check("unit", "src/app.txt", title="")]
if mode == "prereq":
    checks = [check("build", "--build"), check("needs-build", "--needs-build", prerequisites=["build"])]
if mode == "dirty":
    checks = [check("dirty", "--dirty")]
if mode == "escape":
    checks = [check("escape", "--escape", "src/app.txt", timeoutSeconds=2)]
if mode == "ceiling":
    checks = [check("long", timeoutSeconds=999)]
if mode == "slow-child":
    checks = [check("slow", "--sleep-child", "src/app.txt", timeoutSeconds=2)]
print(json.dumps({"checks": checks, "widened": mode == "widened", "uncovered": uncovered}))
""",
    "forge.py": """
import json, os, subprocess, sys
remote = os.environ["MQ_REMOTE"]
def tip(branch):
    listed = subprocess.run(["git", "ls-remote", "--heads", remote, "refs/heads/" + branch],
                            capture_output=True, text=True, check=True).stdout.split()
    return listed[0] if listed else None
def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
with open(os.environ["MQ_LOG"], "a") as log:
    log.write("forge " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[1] == "ready":
    sys.exit(1 if os.environ.get("MQ_READY") == "refuse" else 0)
branch, expected = sys.argv[2], sys.argv[3]
if os.environ.get("MQ_MERGE") == "hang":
    import time
    time.sleep(30)
if os.environ.get("MQ_MERGE") == "loud":
    # A refusal that says why, and echoes what a careless helper might: secrets among the reason.
    sys.stderr.write("POST /repos/acme/shop/pulls/7/merge\\n" + "x" * int(os.environ.get("MQ_MERGE_PAD", "0"))
                     + "token=ghx_keyedvalue123 Authorization: Bearer abc.def.ghi\\n"
                     # On a line of their own, so only the environment and token-shape rules can remove them.
                     + os.environ.get("MQ_FORGE_TOKEN", "") + " ghp_" + "A" * 36 + "\\nmerge refused: Head branch was modified. Review and try the merge again.\\n")
    sys.exit(1)
if os.environ.get("MQ_MERGE") == "refuse" or tip(branch) != expected:
    sys.exit(1)
onto = sys.argv[sys.argv.index("--onto") + 1] if "--onto" in sys.argv else "main"
base = tip(onto)
git("fetch", "--quiet", remote, "+refs/heads/" + onto + ":refs/forge/main", "+refs/heads/" + branch + ":refs/forge/candidate")
tree = git("rev-parse", expected + "^{tree}")
if os.environ.get("MQ_MERGE") == "unverified":
    tree = git("rev-parse", base + "^{tree}")
parents = ["-p", expected, "-p", base] if os.environ.get("MQ_MERGE") == "swapped" else ["-p", base, "-p", expected]
if os.environ.get("MQ_MERGE") == "squash":
    # One new commit on the base with the candidate's tree, as a squash merge makes.
    parents = ["-p", base]
commit = git("commit-tree", tree, *parents, "-m", "Merge " + branch)
# The forge is the adopter's own tool: it, not the runner, owns its push settings.
git("-c", "push.gpgSign=false", "push", "--quiet", "--force-with-lease=refs/heads/" + onto + ":" + base, remote,
    commit + ":refs/heads/" + onto)
""",
    "lease.py": """
import os, pathlib, sys
with open(os.environ["MQ_LOG"], "a") as log:
    log.write("lease " + sys.argv[1] + "\\n")
if sys.argv[1] == "on":
    pathlib.Path(".lease-material").write_text("held\\n")
else:
    pathlib.Path(".lease-material").unlink(missing_ok=True)
    if os.environ.get("MQ_WITHDRAW") == "fail":
        sys.stderr.write("withdraw refused for " + os.environ.get("MQ_STEP_TOKEN", "") + "\\n")
        sys.exit(1)
""",
    "leadinput.py": """
import os, pathlib, sys, time
with open(os.environ["MQ_LOG"], "a") as log:
    log.write("input material=" + str(pathlib.Path(".lease-material").exists()) + "\\n")
mode = os.environ.get("MQ_INPUT")
if mode == "fail":
    sys.exit(4)
if mode == "timeout":
    time.sleep(30)
pathlib.Path("gen-api").mkdir(exist_ok=True)
(pathlib.Path("gen-api") / "api.txt").write_text("api for " + "".join(sorted(p.name for p in pathlib.Path("src").glob("*.txt"))) + "\\n")
""",
    "hold.py": """
import os, pathlib, sys, time
# Fails at once on a tree carrying the candidate's file; on the base, runs until the test says how it ended.
if pathlib.Path("src/candidate.txt").exists():
    print("FAIL: test_held boom")
    sys.exit(1)
ended = pathlib.Path(os.environ["MQ_LOG"] + ".ended")
for _ in range(1500):
    if ended.exists():
        sys.exit(int(ended.read_text()))
    time.sleep(0.02)
sys.exit(70)
""",
    "release.py": """
import os, sys
with open(os.environ["MQ_LOG"], "a") as log:
    log.write("release " + sys.argv[1] + "\\n")
sys.exit(1 if sys.argv[1] in os.environ.get("MQ_RELEASE_FAIL", "").split(",") else 0)
""",
}


def run(*args: str, cwd: Path, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t"},
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {completed.stderr}")
    return completed.stdout.strip()


def seed_base(remote: Path, files: dict[str, str]) -> None:
    """Commit ``files`` as main's first commit straight into a bare ``remote``, with no checkout to write and remove."""
    stream = io.BytesIO()
    stream.write(f"commit refs/heads/main\ncommitter t <t@t> {int(time.time())} +0000\ndata 4\nbase\n".encode())
    for relative, text in files.items():
        data = text.encode("utf-8")
        stream.write(f"M 100644 inline {relative}\ndata {len(data)}\n".encode() + data + b"\n")
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "fastimport.unpackLimit=1", "fast-import", "--quiet"], cwd=remote,
                   input=stream.getvalue(), capture_output=True, check=True)


def check_entry(cid: str, *args: str, **more: object) -> dict:
    entry = {"id": cid, "argv": [PY, "tools/check.py", cid, *args], "cwd": ".", "reasons": [cid], "prerequisites": []}
    entry.update(more)
    return entry


class Adopter:
    """A remote, the operator's clone, a lane clone and a delivery state."""

    def __init__(self, root: Path, binding_changes: dict | None = None, mode: str = "normal") -> None:
        self.root = root
        self.remote = root / "remote.git"
        self.operator = root / "operator"
        self.state = root / "state"
        self.work = root / "work"
        self.log = root / "log.txt"
        self.log.write_text("")
        run("init", "--quiet", "--bare", "--initial-branch=main", str(self.remote), cwd=root)
        quieten(self.remote)
        files = {f"tools/{name}": text.lstrip() for name, text in TOOLS.items()}
        files |= {"tools/provider-mode.txt": mode + "\n", "src/app.txt": "app one\n", "src/lib.txt": "lib one\n",
                  "gen/generated.txt": "app one\nlib one\n", "tests/test_base.txt": "ok\n"}
        self.binding = self.default_binding()
        for key, value in (binding_changes or {}).items():
            self.binding[key] = value
        files[".agents/orchestration.json"] = json.dumps(self.binding, indent=2) + "\n"
        seed_base(self.remote, files)
        quiet_clone(self.remote, self.operator)
        state_tool.init(self.state, "Fixture delivery")

    @property
    def lane(self) -> Path:
        """The lane's clone, made when a test first reaches for it: many only ever read the binding or the queue."""
        lane = self.root / "lane"
        if not lane.exists():
            # The lane commits the bytes it means: Git for Windows' system setting would convert them, from the clone's own checkout on.
            quiet_clone(self.remote, lane, settings="[core]\n\tautocrlf = false\n")
        return lane

    def default_binding(self) -> dict:
        return {
            "schemaVersion": "orchestration-binding/1",
            "base": {"remote": "origin", "ref": "main"},
            "workRoot": str(self.work),
            "candidate": [PY, "tools/candidate.py", "{candidate}"],
            "prepare": [],
            "hooks": "bypass",
            "regenerable": {"paths": ["gen/**"], "regenerators": [[PY, "tools/regen.py"]]},
            "postSync": [{"argv": [PY, "tools/fingerprint.py", "{baseHead}"], "paths": ["notes/fingerprint.txt"]}],
            "leadInputs": [
                {"id": "api", "argv": [PY, "tools/leadinput.py"], "paths": ["gen-api/**"], "lease": "backend"},
            ],
            "profiles": {"default": {"environment": {"MQ_PROFILE_HEAD": "{head}"}}},
            "provider": {
                "argv": [PY, "tools/provider.py"], "cwd": ".", "timeoutSeconds": 3,
                "providerInputs": ["tools/provider.py", "tools/provider-mode.txt"],
            },
            "alwaysRun": [check_entry("always", environmentProfile="default")],
            "fallback": [check_entry("fallback")],
            "checkTimeoutSeconds": 20,
            "checkTimeoutCeiling": 60,
            "environment": {"MQ_REMOTE": str(self.remote), "MQ_LOG": str(self.log)},
            "ready": [PY, "tools/forge.py", "ready", "{candidate}"],
            "merge": [PY, "tools/forge.py", "merge", "{branch}", "{expectedHead}"],
            "release": [
                {"id": "backend", "argv": [PY, "tools/release.py", "backend"], "stopsOnFailure": True, "requiresApproval": False},
                {"id": "clients", "argv": [PY, "tools/release.py", "clients"], "stopsOnFailure": True, "requiresApproval": True},
                {"id": "docs", "argv": [PY, "tools/release.py", "docs"], "stopsOnFailure": False, "requiresApproval": False},
            ],
            "fullHealth": [PY, "tools/check.py", "full"],
            "leases": [{
                "id": "backend", "description": "The shared development backend.", "defaultHolder": "lead",
                "materialize": [PY, "tools/lease.py", "on"], "withdraw": [PY, "tools/lease.py", "off"],
            }],
            "state": {"directory": str(self.state), "renderers": [], "publishers": []},
        }

    @staticmethod
    def write(checkout: Path, relative: str, text: str) -> None:
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not text: Windows would turn this fixture's own \r\n into \r\r\n.
        path.write_bytes(text.encode("utf-8"))

    def tip(self, branch: str) -> str:
        return run("ls-remote", "--heads", str(self.remote), f"refs/heads/{branch}", cwd=self.root).split()[0]

    def lane_commit(self, branch: str, files: dict[str, str], *, start: str = "origin/main") -> str:
        run("fetch", "--quiet", "origin", cwd=self.lane)
        if run("rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=self.lane, check=False):
            run("checkout", "--quiet", branch, cwd=self.lane)
            run("reset", "--quiet", "--hard", f"origin/{branch}", cwd=self.lane)
        else:
            run("checkout", "--quiet", "-b", branch, start, cwd=self.lane)
        for relative, text in files.items():
            self.write(self.lane, relative, text)
        run("add", "--all", cwd=self.lane)
        run("commit", "--quiet", "-m", f"change {branch}", cwd=self.lane)
        run("push", "--quiet", "--force", "origin", f"{branch}:{branch}", cwd=self.lane)
        return run("rev-parse", "HEAD", cwd=self.lane)

    def advance_base(self, files: dict[str, str]) -> str:
        run("fetch", "--quiet", "origin", cwd=self.lane)
        run("checkout", "--quiet", "-B", "base-work", "origin/main", cwd=self.lane)
        for relative, text in files.items():
            self.write(self.lane, relative, text)
        run("add", "--all", cwd=self.lane)
        run("commit", "--quiet", "-m", "advance base", cwd=self.lane)
        run("push", "--quiet", "origin", "base-work:main", cwd=self.lane)
        return run("rev-parse", "HEAD", cwd=self.lane)

    def lead_write(self, write: Callable[[dict], dict]) -> None:
        """Write as the lead, as a real lead does while a runner may write too: read again and retry a stale version."""
        for _ in range(20):
            try:
                write(state_tool.read_state(self.state))
                return
            except state_tool.StateError as error:
                if error.code != "version_stale":
                    raise
        raise AssertionError("the record kept moving under the lead's write")

    def enqueue(self, subject: str, head: str, lead_inputs: list[str] | None = None) -> None:
        def write(state: dict) -> dict:
            item = {
                "order": len(state["queue"]) + 1, "subject": subject, "title": f"Candidate {subject}",
                "lane": "lane-1", "stage": "queued", "head": head, "syncedHead": None,
                "leadInputs": lead_inputs or [], "reportedRounds": 2, "runId": None, "checks": [],
            }
            return state_tool.patch(self.state, writer="lead", version=state["version"],
                                    changes={"queue": [*state["queue"], item]})

        self.lead_write(write)

    def lead_set(self, subject: str, **fields: object) -> None:
        self.lead_write(lambda state: state_tool.patch(self.state, writer="lead", version=state["version"],
                                                       selector=f"queue[subject={subject}]", changes=fields))

    def grant(self, lease: str, holder: str = "lead") -> None:
        state = state_tool.read_state(self.state)
        leases = [*state["leases"], {"id": lease, "holder": holder, "since": state_tool.now()}]
        state_tool.patch(self.state, writer="lead", version=state["version"], changes={"leases": leases})

    def runner(self, **options: object) -> "queue.Runner":
        return queue.Runner(self.operator, self.state, remote="origin", base="main", **options)

    def item(self, subject: str) -> dict:
        return next(item for item in state_tool.read_state(self.state)["queue"] if item["subject"] == subject)

    def log_lines(self) -> list[str]:
        return self.log.read_text().splitlines()

    def show(self, revision: str, path: str) -> str:
        run("fetch", "--quiet", "origin", cwd=self.lane)
        return run("show", f"{revision}:{path}", cwd=self.lane)


def remove_tree(path: Path) -> None:
    # Git writes its objects read-only, and Windows refuses to delete a read-only file.
    if os.name == "nt":
        for folder, _, files in os.walk(path):
            for name in files:
                os.chmod(os.path.join(folder, name), stat.S_IWRITE)
    shutil.rmtree(path)


class MergeQueueTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="merge-queue-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.directory, True)

    def adopter(self, binding_changes: dict | None = None, mode: str = "normal") -> Adopter:
        return Adopter(self.directory, binding_changes, mode)

    def fresh(self) -> None:
        remove_tree(self.directory)
        self.directory.mkdir()

    def environment_adopter(self, extra: dict[str, str], binding_changes: dict | None = None) -> Adopter:
        environment = {
            "MQ_REMOTE": str(self.directory / "remote.git"), "MQ_LOG": str(self.directory / "log.txt"), **extra,
        }
        return self.adopter({"environment": environment, **(binding_changes or {})})

    def candidate(self, adopter: Adopter, files: dict[str, str] | None = None, name: str = "feature") -> str:
        head = adopter.lane_commit(name, files or {"src/app.txt": "app two\n"})
        adopter.enqueue(name, head)
        return head

    def run_queue(self, adopter: Adopter, *subjects: str, **options: object) -> list:
        return adopter.runner(**options).run_command("run", list(subjects))

    def assertStopped(self, results: list, reason: str) -> dict:
        last = results[-1].document()
        self.assertIn(last["outcome"], ("stopped", "refused"), last)
        self.assertEqual(last["stop"]["reason"], reason, last["stop"])
        return last


class FixtureMaintenanceTests(MergeQueueTestCase):
    """No git in a fixture starts automatic maintenance, so nothing repacks a repository a test is still reading.

    Git starts a detached `git maintenance run --auto` after a push, a fetch or a
    commit, and from Git 2.52 its default strategy repacks geometrically on nearly
    every push: it writes a temporary `bitmap-ref-tips` file and deletes the packs
    it merged, beside a test's next clone, fetch or push. On a loaded machine the
    detached repack outlives the command that started it, which is how a fixture
    clone failed to copy a vanished `bitmap-ref-tips` file and a batch landing
    stopped on `runner_error` rather than `batch_tree_mismatch`. `quieten` sets
    `maintenance.auto = false` on every repository the fixture makes.
    """

    MAINTENANCE = ("maintenance", "gc", "repack", "multi-pack-index", "pack-refs")

    def started(self, trace: Path) -> list[list[str]]:
        """Every git command the trace saw start, as its argv after the program."""
        commands = []
        for path in trace.iterdir():
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("event") in ("start", "child_start"):
                    commands.append([str(part) for part in event.get("argv", [])[1:]])
        return commands

    def test_a_batch_whose_base_moves_starts_no_maintenance_in_any_fixture_repository(self) -> None:
        trace = self.directory / "trace"
        trace.mkdir()
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": str(trace)}):
            adopter = self.adopter()
            subjects = ["first", "second", "third"]
            for name, path in zip(subjects, ("src/lib.txt", "src/app.txt", "src/extra.txt")):
                self.candidate(adopter, {path: f"{name}\n"}, name=name)

            def push_base(runner, subject) -> None:
                if subject == "first":
                    adopter.advance_base({"src/other.txt": "other\n"})

            results = adopter.runner(seams={"before_batch_landing": push_base}).run_command("batch", subjects)
        documents = {result.document()["candidate"]: result.document() for result in results}
        self.assertEqual(documents["first"]["stop"]["reason"], "batch_tree_mismatch", documents["first"]["stop"])
        commands = self.started(trace)
        # The trace saw the fixture's pushes and the runner's fetches, so an empty match is not an empty trace.
        self.assertTrue(any("push" in command for command in commands), commands[:20])
        self.assertTrue(any("receive-pack" in " ".join(command) for command in commands), commands[:20])
        maintenance = [command for command in commands if any(word in command for word in self.MAINTENANCE)]
        self.assertEqual(maintenance, [])

    def test_every_repository_the_fixture_makes_turns_automatic_maintenance_off(self) -> None:
        adopter = self.adopter()
        lane = adopter.lane
        for repository in (adopter.remote, adopter.operator, lane):
            for name, value in (("maintenance.auto", "false"), ("receive.autoGc", "false"), ("gc.auto", "0")):
                with self.subTest(repository.name, setting=name):
                    self.assertEqual(run("config", "--get", name, cwd=repository), value)


class LeadWriteTests(MergeQueueTestCase):
    """A lead's write while serve runs reads the record again when the runner wrote first, as a real lead does."""

    def test_the_lead_writes_again_when_the_runner_wrote_between_its_read_and_its_write(self) -> None:
        adopter = self.adopter()
        head = adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        real = state_tool.read_state
        raced: list[bool] = []

        def runner_writes_after_the_read(directory: Path) -> dict:
            state = real(directory)
            if not raced:
                raced.append(True)
                # serve records something between the lead's read and its write.
                state_tool.patch(directory, writer="runner", version=state["version"],
                                 assignments={"/meta/baseHead": "c" * 40})
            return state

        with mock.patch.object(state_tool, "read_state", runner_writes_after_the_read):
            adopter.enqueue("feature", head)
        self.assertEqual(raced, [True])
        self.assertEqual(adopter.item("feature")["head"], head)
        self.assertEqual(state_tool.read_state(adopter.state)["meta"]["baseHead"], "c" * 40, "the runner's write stands")


class CheckModeTests(MergeQueueTestCase):
    def hooks(self) -> dict:
        return {
            "ready": [PY, "tools/forge.py", "ready", "{candidate}", "{checkMode}"],
            "merge": [PY, "tools/forge.py", "merge", "{branch}", "{expectedHead}", "{checkMode}"],
        }

    def test_single_merge_hooks_receive_single(self) -> None:
        adopter = self.adopter(self.hooks())
        self.candidate(adopter)
        with mock.patch.dict(os.environ, {"checkMode": "batch", "CHECK_MODE": "batch"}):
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].outcome, "merged")
        synced = adopter.item("feature")["syncedHead"]
        self.assertIn("forge ready feature single", adopter.log_lines())
        self.assertIn(f"forge merge feature {synced} single", adopter.log_lines())

    def test_batch_merge_hooks_receive_batch_for_each_member(self) -> None:
        adopter = self.adopter(self.hooks())
        for subject, changes in [("first", {"src/lib.txt": "changed lib\n"}), ("second", {"src/app.txt": "changed app\n"})]:
            self.candidate(adopter, changes, name=subject)
        results = adopter.runner().run_command("batch", ["first", "second"])
        self.assertEqual([result.outcome for result in results], ["merged", "merged"])
        for subject in ["first", "second"]:
            synced = adopter.item(subject)["syncedHead"]
            self.assertIn(f"forge ready {subject} batch", adopter.log_lines())
            self.assertIn(f"forge merge {subject} {synced} batch", adopter.log_lines())

    def test_check_mode_context_is_an_exact_enum_and_unavailable_before_merge(self) -> None:
        for mode in ["single", "batch"]:
            self.assertEqual(queue.Context({"checkMode": mode}).argv(["{checkMode}", "--mode={checkMode}"]), [mode, "--mode={checkMode}"])
        for values in [{}, *[{"checkMode": invalid} for invalid in ["", "Single", "batch ", "unknown", None, 0, False, [], {}]]]:
            with self.subTest(values=values), self.assertRaises(queue.Stop) as caught:
                queue.Context(values).argv(["{checkMode}"])
            self.assertEqual(caught.exception.reason, "binding_invalid")

    def test_hook_placeholder_preserves_binding_schema_compatibility(self) -> None:
        adopter = self.adopter(self.hooks())
        self.assertEqual(queue.binding_errors(adopter.binding), [])
        self.assertTrue(Draft202012Validator(schema("orchestration-binding")).is_valid(adopter.binding))


# The rule for which bases a binding serves; run-delivery's copy of it is pinned to the same table.
SERVING_BINDING = {"base": {"remote": "origin", "ref": "main"}, "bases": ["integration-*", "hotfix", "release-1.2", "rel?ase"]}
SERVING_RULE = {"main": True, "hotfix": True, "integration-2": True, "integration-": True, "integration-[2]": True,
                "integration-2/x": False, "release/1": False, "hotfix-1": False, "xintegration-2": False,
                "release-1.2": True, "release-1x2": False, "rel?ase": True, "release": False, "relase": False}


class BasesTests(MergeQueueTestCase):
    """A binding that names its bases serves a delivery to any of them; one without serves only base.ref."""

    def cut(self, adopter: Adopter, branch: str) -> None:
        run("push", "--quiet", "origin", f"origin/main:refs/heads/{branch}", cwd=adopter.lane)

    def runner_on(self, adopter: Adopter, base: str) -> "queue.Runner":
        return queue.Runner(adopter.operator, adopter.state, remote="origin", base=base)

    def bases_adopter(self, bases: list[str] | None) -> Adopter:
        merge = [PY, "tools/forge.py", "merge", "{branch}", "{expectedHead}", "--onto", "{base}"]
        return self.adopter({"merge": merge, **({"bases": bases} if bases is not None else {})})

    def test_a_queue_merge_lands_on_a_base_the_binding_serves(self) -> None:
        adopter = self.bases_adopter(["main", "integration-*"])
        self.cut(adopter, "integration-2")
        before_main = adopter.tip("main")
        head = adopter.lane_commit("feature", {"src/app.txt": "app two\n"}, start="origin/integration-2")
        adopter.enqueue("feature", head)
        results = self.runner_on(adopter, "integration-2").run_command("run", ["feature"])
        self.assertEqual(results[-1].outcome, "merged", results[-1].document())
        self.assertEqual(adopter.show("origin/integration-2", "src/app.txt"), "app two")
        self.assertEqual(adopter.tip("main"), before_main, "the merge landed on another base")

    def test_a_base_outside_the_bases_is_refused_with_the_guide_anchor(self) -> None:
        adopter = self.bases_adopter(["main", "integration-*"])
        for branch in ("release/1", "integration-2/sub"):
            self.cut(adopter, branch)
            with self.subTest(branch=branch):
                head = adopter.lane_commit(f"feature-{len(branch)}", {"src/app.txt": "app two\n"},
                                           start=f"origin/{branch}")
                adopter.enqueue(f"feature-{len(branch)}", head)
                results = self.runner_on(adopter, branch).run_command("run", [f"feature-{len(branch)}"])
                document = self.assertStopped(results, "binding_invalid")
                self.assertIn(f"the binding doesn't serve the base {branch}", document["stop"]["detail"])
                self.assertIn("bases", document["stop"]["detail"])
                self.assertIn("orchestration-guide.md#base-mismatch", document["stop"]["detail"])

    def test_a_binding_without_bases_refuses_another_base_as_before(self) -> None:
        adopter = self.bases_adopter(None)
        self.cut(adopter, "integration-2")
        head = adopter.lane_commit("feature", {"src/app.txt": "app two\n"}, start="origin/integration-2")
        adopter.enqueue("feature", head)
        results = self.runner_on(adopter, "integration-2").run_command("run", ["feature"])
        document = self.assertStopped(results, "binding_invalid")
        self.assertIn("the binding doesn't serve the base integration-2", document["stop"]["detail"])
        results = self.runner_on(adopter, "main").run_command("run", ["feature"])
        self.assertEqual(results[-1].outcome, "merged", results[-1].document())

    def test_a_binding_naming_another_remote_is_still_refused(self) -> None:
        adopter = self.bases_adopter(["main"])
        binding = {**adopter.binding, "base": {"remote": "upstream", "ref": "main"}}
        adopter.advance_base({".agents/orchestration.json": json.dumps(binding, indent=2) + "\n"})
        self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), "binding_invalid")
        self.assertIn("remote", document["stop"]["detail"])

    def test_the_serving_rule(self) -> None:
        for name, expected in SERVING_RULE.items():
            with self.subTest(base=name):
                self.assertEqual(queue.serves(SERVING_BINDING, name), expected)
        self.assertFalse(queue.serves({"base": {"remote": "origin", "ref": "main"}}, "integration-2"))


class CandidatePinningTests(MergeQueueTestCase):
    def test_a_clean_candidate_merges_at_its_synced_head(self) -> None:
        adopter = self.adopter()
        base = adopter.tip("main")
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        documents = [result.document() for result in results]
        self.assertEqual([document["outcome"] for document in documents], ["synced", "checked", "merged"])
        synced = adopter.item("feature")["syncedHead"]
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        self.assertIn(f"forge merge feature {synced}", adopter.log_lines())
        merged = adopter.tip("main")
        self.assertEqual(documents[-1]["mergedHead"], merged)
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        self.assertEqual(run("rev-parse", f"{merged}^{{tree}}", cwd=adopter.lane), run("rev-parse", f"{synced}^{{tree}}", cwd=adopter.lane))
        self.assertEqual(run("rev-parse", f"{merged}^1", cwd=adopter.lane), base)
        for document in documents:
            self.assertEqual(Draft202012Validator(schema("merge-queue-result")).is_valid(document), True)

    def test_a_push_between_check_and_merge_stops_with_candidate_moved(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        before_main = adopter.tip("main")

        def lane_pushes(runner, subject):
            adopter.lane_commit("feature", {"src/app.txt": "unreviewed\n"})

        results = self.run_queue(adopter, "feature", seams={"before_merge": lane_pushes})
        self.assertStopped(results, "candidate_moved")
        self.assertEqual(adopter.tip("main"), before_main)
        self.assertNotIn("forge merge", " ".join(adopter.log_lines()))


class SyncSafetyTests(MergeQueueTestCase):
    def test_a_stale_generated_file_is_regenerated_and_merged(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/app.txt": "app two\n"})
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged")
        self.assertEqual(adopter.show("origin/main", "gen/generated.txt"), "app two\nlib one")

    def test_a_generated_only_conflict_is_resolved_after_the_merge_is_committed(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/app.txt": "app two\n", "gen/generated.txt": "app two\nlib one\n"})
        new_base = adopter.advance_base({"src/lib.txt": "lib two\n", "gen/generated.txt": "app one\nlib two\n"})
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertEqual(adopter.show("origin/main", "gen/generated.txt"), "app two\nlib two")
        # The fingerprint step read the merge base after the merge was committed.
        self.assertEqual(adopter.show("origin/main", "notes/fingerprint.txt"), new_base)

    def test_a_source_conflict_stops_and_pushes_nothing(self) -> None:
        adopter = self.adopter()
        head = self.candidate(adopter, {"src/lib.txt": "lane lib\n"})
        adopter.advance_base({"src/lib.txt": "base lib\n"})
        results = self.run_queue(adopter, "feature")
        document = self.assertStopped(results, "source_conflict")
        self.assertEqual(document["stop"]["paths"], ["src/lib.txt"])
        self.assertEqual(adopter.tip("feature"), head)

    def test_an_undeclared_post_sync_change_stops(self) -> None:
        adopter = self.adopter({"postSync": [{"argv": [PY, "tools/fingerprint.py", "{baseHead}", "stray.txt"], "paths": ["notes/fingerprint.txt"]}]})
        head = self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), "undeclared_change")
        self.assertEqual(document["stop"]["paths"], ["stray.txt"])
        self.assertEqual(adopter.tip("feature"), head)

    def test_a_fingerprint_style_post_sync_writing_its_declared_path_passes(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged")
        self.assertTrue(adopter.show("origin/main", "notes/fingerprint.txt"))

    def test_an_unreviewed_change_in_the_synced_tree_stops(self) -> None:
        adopter = self.adopter()
        head = self.candidate(adopter)

        def unreviewed_commit(runner, subject):
            checkout = runner.checkouts[-1]
            Adopter.write(checkout, "src/app.txt", "never reviewed\n")
            runner.git("commit", "--quiet", "--all", "-m", "unreviewed", cwd=checkout)

        document = self.assertStopped(self.run_queue(adopter, "feature", seams={"after_sync": unreviewed_commit}), "unreviewed_change")
        self.assertEqual(document["stop"]["paths"], ["src/app.txt"])
        self.assertEqual(adopter.tip("feature"), head)

    def test_a_non_converging_regenerator_stops(self) -> None:
        adopter = self.environment_adopter({"MQ_REGEN": "unstable"})
        head = self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "unstable_regeneration")
        self.assertEqual(adopter.tip("feature"), head)

    def test_the_push_uses_a_lease_and_never_targets_the_base(self) -> None:
        adopter = self.adopter()
        head = self.candidate(adopter)
        moved: list[str] = []

        def lane_pushes(runner, subject):
            moved.append(adopter.lane_commit("feature", {"src/app.txt": "pushed during sync\n"}))

        self.assertStopped(self.run_queue(adopter, "feature", seams={"after_sync": lane_pushes}), "candidate_moved")
        self.assertEqual(adopter.tip("feature"), moved[0])
        self.assertNotEqual(moved[0], head)
        adopter.enqueue("main", adopter.tip("main"))
        self.assertStopped(self.run_queue(adopter, "main"), "ref_invalid")

    def test_a_re_sync_after_the_base_moves_accepts_the_runners_synced_head(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        advanced: list[str] = []

        def base_moves_once(runner, subject):
            if not advanced:
                advanced.append(adopter.advance_base({"src/other.txt": "other\n"}))

        results = self.run_queue(adopter, "feature", seams={"before_merge": base_moves_once})
        outcomes = [(result.document()["subcommand"], result.document()["outcome"]) for result in results]
        self.assertEqual(outcomes[2], ("merge", "stopped"))
        self.assertEqual(results[2].document()["stop"]["reason"], "base_moved")
        self.assertEqual(outcomes[3], ("sync", "synced"))
        self.assertEqual(outcomes[-1], ("merge", "merged"))
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        self.assertEqual(run("rev-parse", "origin/main^1", cwd=adopter.lane), advanced[0])

    def test_a_second_base_move_stops_the_candidate(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        counter = iter(range(10))

        def base_moves(runner, subject):
            adopter.advance_base({"src/other.txt": f"other {next(counter)}\n"})

        results = self.run_queue(adopter, "feature", seams={"before_merge": base_moves})
        self.assertStopped(results, "base_moved")
        self.assertEqual([result.document()["subcommand"] for result in results], ["sync", "check", "merge", "sync", "check", "merge"])

    def test_a_leased_input_without_the_lease_stops(self) -> None:
        adopter = self.adopter()
        head = adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        adopter.enqueue("feature", head, lead_inputs=["api"])
        self.assertStopped(self.run_queue(adopter, "feature"), "lease_required")
        # A lease held by someone else is not the lead's.
        adopter.grant("backend", holder="peer-lead")
        self.assertStopped(self.run_queue(adopter, "feature"), "lease_required")
        self.assertNotIn("lease on", adopter.log_lines())

    def test_lease_material_exists_only_during_the_input(self) -> None:
        adopter = self.adopter({"alwaysRun": [check_entry("always", "--no-material")]})
        head = adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        adopter.enqueue("feature", head, lead_inputs=["api"])
        adopter.grant("backend")
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        lines = adopter.log_lines()
        self.assertIn("input material=True", lines)
        first = lines.index("lease on")
        self.assertEqual(lines[first:first + 3], ["lease on", "input material=True", "lease off"])
        self.assertIn("check always -", lines)
        self.assertFalse(run("ls-tree", "-r", "--name-only", "origin/main", cwd=adopter.lane).count(".lease-material"))

    def test_a_failing_or_timed_out_leased_input_leaves_no_material(self) -> None:
        for mode, timeout in (("fail", None), ("timeout", 1)):
            with self.subTest(mode=mode):
                self.fresh()
                inputs = [{"id": "api", "argv": [PY, "tools/leadinput.py"], "paths": ["gen-api/**"], "lease": "backend"}]
                if timeout:
                    inputs[0]["timeoutSeconds"] = timeout
                adopter = self.environment_adopter({"MQ_INPUT": mode}, {"leadInputs": inputs})
                head = adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
                adopter.enqueue("feature", head, lead_inputs=["api"])
                adopter.grant("backend")
                self.assertStopped(self.run_queue(adopter, "feature"), "sync_step_failed")
                lines = adopter.log_lines()
                self.assertEqual(lines.count("lease on"), lines.count("lease off"))
                self.assertEqual(adopter.tip("feature"), head)

    def test_a_failing_withdraw_stops_and_pushes_nothing(self) -> None:
        secret = "s3cr3t-withdraw-value-from-the-environment"
        adopter = self.environment_adopter({"MQ_WITHDRAW": "fail", "MQ_STEP_TOKEN": secret})
        head = adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        adopter.enqueue("feature", head, lead_inputs=["api"])
        adopter.grant("backend")
        detail = self.assertStopped(self.run_queue(adopter, "feature"), "lease_withdraw_failed")["stop"]["detail"]
        self.assertEqual(adopter.tip("feature"), head)
        # Why, scrubbed, and the log that keeps the whole output, secret and all.
        self.assertIn("withdrawing lease backend failed: withdraw refused for [redacted]; nothing is pushed; ", detail)
        self.assertNotIn(secret, detail)
        log = re.search(r"its whole output is in (logs/\S+\.log) under the state directory", detail).group(1)
        self.assertIn(f"withdraw refused for {secret}", (adopter.state / log).read_text(encoding="utf-8"))

    def test_every_git_command_runs_with_the_empty_hooks_directory(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        hooks = self.directory / "hooks"
        hooks.mkdir()
        marker = self.directory / "hooks-fired.txt"
        for name in ("pre-commit", "commit-msg", "pre-merge-commit", "post-commit", "post-checkout",
                     "post-merge", "pre-push", "reference-transaction", "post-rewrite"):
            script = hooks / name
            script.write_text(f"#!/bin/sh\necho {name} >> '{marker}'\nexit 1\n")
            script.chmod(0o755)
        run("config", "core.hooksPath", str(hooks), cwd=adopter.operator)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else "")
        self.assertTrue(all(result.document()["hooksBypassed"] for result in results))

    def test_hooks_run_when_the_binding_says_so(self) -> None:
        adopter = self.adopter({"hooks": "run"})
        self.candidate(adopter)
        hooks = self.directory / "hooks"
        hooks.mkdir()
        marker = self.directory / "hooks-fired.txt"
        for name in ("pre-commit", "pre-push"):
            script = hooks / name
            script.write_text(f"#!/bin/sh\necho {name} >> '{marker}'\nexit 0\n")
            script.chmod(0o755)
        run("config", "core.hooksPath", str(hooks), cwd=adopter.operator)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        fired = marker.read_text().split()
        self.assertIn("pre-commit", fired)
        self.assertIn("pre-push", fired)
        self.assertFalse(any(result.document()["hooksBypassed"] for result in results))


REGEN_LOCK = """
import hashlib, json, pathlib, sys
# Refreshes only the digests, as update-provenance.py does: it never adds or removes an output.
lock_path = pathlib.Path("gen/lock.json")
if lock_path.exists():
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for entry in lock["entries"]:
        for output in entry["outputs"]:
            path = pathlib.Path(output["path"])
            if not path.is_file():
                sys.exit(f"listed output {output['path']} is missing")
            output["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    lock_path.write_bytes((json.dumps(lock, indent=2, sort_keys=True) + "\\n").encode("utf-8"))
"""

# A listing that fails as the version it reads says to, so each of the three versions can be made the one that fails.
FAILING_LISTING = """
import json, os, sys, time
text = sys.stdin.read()
lock = json.loads(text)
how = lock.get("listingMode")
if how == "fails":
    # A long refusal whose last line carries a secret from the environment, when one is set.
    sys.stderr.write("y" * 500 + "\\n")
    sys.exit(("cannot list " + os.environ.get("MQ_STEP_TOKEN", "")).strip())
if how == "slow":
    time.sleep(30)
if how == "empty":
    sys.exit(0)
if how == "garbage":
    print("not a listing")
    sys.exit(0)
if how == "deep":
    print("[" * 100000 + "]" * 100000)
    sys.exit(0)
if how == "object":
    print(json.dumps({"outputs": []}))
    sys.exit(0)
if how == "numbers":
    print(json.dumps([1, 2]))
    sys.exit(0)
# A listing given an argument checks it is the head the runner placed there, so a missing placeholder value shows.
if len(sys.argv) > 1 and len(sys.argv[1]) != 40:
    sys.exit(f"expected a head, not {sys.argv[1]!r}")
print(json.dumps(sorted(output["path"] for entry in lock["entries"] for output in entry["outputs"])))
"""

REAL_LISTING = [PY, "-B", str(ROOT / "scripts" / "update-provenance.py"), "--list-outputs"]


def fixture_lock(files: dict[str, str], **extra: object) -> str:
    """A lock in provenance.lock.json's real format, listing ``files`` with their digests."""
    outputs = [{"path": path, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()} for path, text in sorted(files.items())]
    lock = {"entries": [{"classification": "fixture", "id": "fixture-entry", "outputs": outputs, "owner": "fixture"}],
            "schemaVersion": "provenance-lock/1", "sourceBaseline": {"commit": "0" * 40}, **extra}
    return json.dumps(lock, indent=2, sort_keys=True) + "\n"


class RegenerableListingTests(MergeQueueTestCase):
    """A conflicted lock regenerates only when the candidate left its listing alone; otherwise the queue stops."""

    START = {"src/app.txt": "app one\n", "src/lib.txt": "lib one\n", "gen/generated.txt": "app one\nlib one\n"}

    def lock_adopter(self, listing: list[str] | None = None, environment: dict[str, str] | None = None,
                     binding: dict | None = None, **extra: object) -> Adopter:
        regenerable = {"regenerable": {
            "paths": ["gen/**"], "regenerators": [[PY, "tools/regen.py"], [PY, "tools/regen_lock.py"]],
            "listings": [{"path": "gen/lock.json", "argv": listing or REAL_LISTING}]}, **(binding or {})}
        adopter = (self.adopter(regenerable) if environment is None
                   else self.environment_adopter(environment, regenerable))
        adopter.advance_base({"tools/regen_lock.py": REGEN_LOCK.lstrip(), "tools/listing.py": FAILING_LISTING.lstrip(),
                              "gen/lock.json": fixture_lock(self.START, **extra)})
        return adopter

    def side(self, files: dict[str, str], listed: dict[str, str] | None = None, **extra: object) -> dict[str, str]:
        """A side's own change, carrying the generated file and the lock as that side regenerated them."""
        tree = {**self.START, **files}
        generated = "".join(tree[path] for path in sorted(path for path in tree if path.startswith("src/")))
        tree["gen/generated.txt"] = generated
        listed_files = {path: tree[path] for path in (listed if listed is not None else self.START)}
        return {**files, "gen/generated.txt": generated, "gen/lock.json": fixture_lock(listed_files, **extra)}

    def listed(self, adopter: Adopter, revision: str = "origin/main") -> list[str]:
        lock = json.loads(adopter.show(revision, "gen/lock.json"))
        return sorted(output["path"] for entry in lock["entries"] for output in entry["outputs"])

    def stopped_naming_the_lock(self, adopter: Adopter, results: list, base: str, head: str, expect: str) -> None:
        document = self.assertStopped(results, "source_conflict")
        self.assertIn("gen/lock.json", document["stop"]["detail"])
        self.assertIn(expect, document["stop"]["detail"])
        self.assertEqual(document["stop"]["paths"], ["gen/lock.json"])
        # Nothing was regenerated or pushed: the base and the candidate's branch are as they were.
        self.assertEqual(adopter.tip("main"), base)
        self.assertEqual(adopter.tip("feature"), head)

    def test_a_digest_only_conflict_regenerates_and_merges(self) -> None:
        adopter = self.lock_adopter()
        self.candidate(adopter, self.side({"src/app.txt": "app two\n"}))
        adopter.advance_base(self.side({"src/lib.txt": "lib two\n"}))
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document().get("stop"))
        # gen/generated.txt, a regenerable path with no listing, regenerates as before.
        self.assertEqual(adopter.show("origin/main", "gen/generated.txt"), "app two\nlib two")
        lock = json.loads(adopter.show("origin/main", "gen/lock.json"))
        digests = {output["path"]: output["sha256"] for output in lock["entries"][0]["outputs"]}
        self.assertEqual(digests["src/app.txt"], hashlib.sha256(b"app two\n").hexdigest())
        self.assertEqual(digests["src/lib.txt"], hashlib.sha256(b"lib two\n").hexdigest())
        self.assertEqual(sorted(digests), sorted(self.START))

    def test_a_candidate_that_adds_an_output_stops_before_any_check(self) -> None:
        adopter = self.lock_adopter()
        head = self.candidate(adopter, self.side({"src/new.txt": "new\n"},
                                                 listed={**self.START, "src/new.txt": "new\n"}))
        base = adopter.advance_base(self.side({"src/lib.txt": "lib two\n"}))
        results = self.run_queue(adopter, "feature")
        self.stopped_naming_the_lock(adopter, results, base, head, "added src/new.txt")
        self.assertFalse([line for line in adopter.log_lines() if line.startswith("check ")], "no check ran")

    def test_a_candidate_that_removes_an_output_stops(self) -> None:
        adopter = self.lock_adopter()
        listed = {path: text for path, text in self.START.items() if path != "src/lib.txt"}
        head = self.candidate(adopter, self.side({"src/app.txt": "app two\n"}, listed=listed))
        base = adopter.advance_base(self.side({"src/lib.txt": "lib two\n"}))
        self.stopped_naming_the_lock(adopter, self.run_queue(adopter, "feature"), base, head, "removed src/lib.txt")

    def test_both_sides_adding_different_outputs_stops(self) -> None:
        adopter = self.lock_adopter(listing=[PY, "tools/listing.py"])
        head = self.candidate(adopter, self.side({"src/c.txt": "c\n"}, listed={**self.START, "src/c.txt": "c\n"}))
        base = adopter.advance_base(self.side({"src/b.txt": "b\n"}, listed={**self.START, "src/b.txt": "b\n"}))
        self.stopped_naming_the_lock(adopter, self.run_queue(adopter, "feature"), base, head, "added src/c.txt")

    def test_an_output_only_the_base_added_regenerates_and_merges(self) -> None:
        adopter = self.lock_adopter()
        self.candidate(adopter, self.side({"src/app.txt": "app two\n"}))
        adopter.advance_base(self.side({"src/b.txt": "b\n"}, listed={**self.START, "src/b.txt": "b\n"}))
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document().get("stop"))
        self.assertEqual(self.listed(adopter), sorted([*self.START, "src/b.txt"]))
        lock = json.loads(adopter.show("origin/main", "gen/lock.json"))
        digests = {output["path"]: output["sha256"] for output in lock["entries"][0]["outputs"]}
        self.assertEqual(digests["src/app.txt"], hashlib.sha256(b"app two\n").hexdigest())

    def test_a_listing_that_fails_at_any_version_stops_and_never_regenerates(self) -> None:
        for how, says in (("fails", "exited"), ("empty", "printed nothing"), ("garbage", "printed something other than a JSON array"),
                          ("deep", "printed something other than a JSON array"),
                          ("object", "printed something other than a JSON array of strings"),
                          ("numbers", "printed something other than a JSON array of strings")):
            for version in ("merge base", "base", "candidate"):
                with self.subTest(listing=how, version=version):
                    self.fresh()
                    marked = {"listingMode": how}
                    adopter = self.lock_adopter([PY, "tools/listing.py"], **(marked if version == "merge base" else {}))
                    keep = marked if version == "merge base" else {}
                    head = self.candidate(adopter, self.side({"src/app.txt": "app two\n"},
                                                             **(marked if version == "candidate" else keep)))
                    base = adopter.advance_base(self.side({"src/lib.txt": "lib two\n"},
                                                          **(marked if version == "base" else keep)))
                    results = self.run_queue(adopter, "feature")
                    self.stopped_naming_the_lock(adopter, results, base, head, f"at the {version} {says}")

    def test_a_failing_listing_says_why_scrubbed_and_bounded_and_names_its_log(self) -> None:
        secret = "s3cr3t-listing-value-from-the-environment"
        adopter = self.lock_adopter([PY, "tools/listing.py"], {"MQ_STEP_TOKEN": secret})
        head = self.candidate(adopter, self.side({"src/app.txt": "app two\n"}))
        base = adopter.advance_base(self.side({"src/lib.txt": "lib two\n"}, listingMode="fails"))
        results = self.run_queue(adopter, "feature")
        self.stopped_naming_the_lock(adopter, results, base, head,
                                     "at the base exited 1: " + "y" * (queue.CANDIDATE_OUT_LIMIT - len("cannot list [redacted]") - 1)
                                     + "\ncannot list [redacted]; its whole output is in logs/")
        document = results[-1].document()
        detail = document["stop"]["detail"]
        # The tail is the listing's bound, not a stop's: no more than CANDIDATE_OUT_LIMIT characters of the stderr.
        self.assertNotIn("y" * queue.CANDIDATE_OUT_LIMIT, detail)
        self.assertNotIn(secret, json.dumps(document))
        for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE):
            self.assertNotIn(secret, (adopter.state / name).read_text(encoding="utf-8"))
        log = re.search(r"its whole output is in (logs/\S+\.log) under the state directory", detail).group(1)
        self.assertIn("y" * 500 + f"\ncannot list {secret}", (adopter.state / log).read_text(encoding="utf-8"))

    def test_a_listing_that_times_out_says_so_and_names_its_log(self) -> None:
        adopter = self.lock_adopter([PY, "tools/listing.py"], binding={"checkTimeoutSeconds": 2})
        head = self.candidate(adopter, self.side({"src/app.txt": "app two\n"}))
        base = adopter.advance_base(self.side({"src/lib.txt": "lib two\n"}, listingMode="slow"))
        results = self.run_queue(adopter, "feature")
        self.stopped_naming_the_lock(adopter, results, base, head, "at the base timed out; its whole output is in logs/")
        log = re.search(r"its whole output is in (logs/\S+\.log)", results[-1].document()["stop"]["detail"]).group(1)
        self.assertTrue((adopter.state / log).is_file())

    def test_a_version_without_the_path_stops(self) -> None:
        adopter = self.adopter({"regenerable": {
            "paths": ["gen/**"], "regenerators": [[PY, "tools/regen.py"], [PY, "tools/regen_lock.py"]],
            "listings": [{"path": "gen/lock.json", "argv": REAL_LISTING}]}})
        adopter.advance_base({"tools/regen_lock.py": REGEN_LOCK.lstrip()})
        # Both sides add the lock: the merge base has none, so there is nothing to compare against.
        head = self.candidate(adopter, self.side({"src/app.txt": "app two\n"}))
        base = adopter.advance_base(self.side({"src/lib.txt": "lib two\n"}))
        self.stopped_naming_the_lock(adopter, self.run_queue(adopter, "feature"), base, head, "absent at the merge base")

    def test_in_a_batch_a_member_that_adds_an_output_is_dropped_and_a_digest_only_one_merges(self) -> None:
        adopter = self.lock_adopter()
        self.candidate(adopter, self.side({"src/lib.txt": "lib two\n"}), name="first")
        self.candidate(adopter, self.side({"src/new.txt": "new\n"}, listed={**self.START, "src/new.txt": "new\n"}),
                       name="adds")
        self.candidate(adopter, self.side({"src/app.txt": "app two\n"}), name="digests")
        documents = {result.document()["candidate"]: result.document()
                     for result in adopter.runner().run_command("batch", ["first", "adds", "digests"])}
        self.assertEqual(documents["adds"]["stop"]["reason"], "source_conflict")
        self.assertIn("dropped from the batch", documents["adds"]["stop"]["detail"])
        self.assertIn("added src/new.txt", documents["adds"]["stop"]["detail"])
        self.assertEqual((documents["first"]["outcome"], documents["digests"]["outcome"]), ("merged", "merged"))
        self.assertEqual(self.listed(adopter), sorted(self.START))
        self.assertEqual(adopter.show("origin/main", "gen/generated.txt"), "app two\nlib two")

    def batched(self, adopter: Adopter, first: dict[str, str], member: dict[str, str]) -> dict:
        """The member's result when a batch merges it into a tree that already holds ``first``: the batch's base side."""
        self.candidate(adopter, first, name="first")
        head = self.candidate(adopter, member, name="member")
        documents = {result.document()["candidate"]: result.document()
                     for result in adopter.runner().run_command("batch", ["first", "member"])}
        self.assertEqual(documents["first"]["outcome"], "merged", documents["first"].get("stop"))
        return {"document": documents["member"], "head": head}

    def test_in_a_batch_every_row_holds_as_at_sync(self) -> None:
        listed_without_lib = {path: text for path, text in self.START.items() if path != "src/lib.txt"}
        stops = {
            "removes": ({"src/lib.txt": "lib two\n"}, ({"src/app.txt": "app two\n"}, listed_without_lib), "removed src/lib.txt"),
            "both add": ({"src/b.txt": "b\n"}, ({"src/c.txt": "c\n"}, {**self.START, "src/c.txt": "c\n"}), "added src/c.txt"),
        }
        for name, (first, (member, listed), expect) in stops.items():
            with self.subTest(name):
                self.fresh()
                adopter = self.lock_adopter()
                base_listed = {**self.START, "src/b.txt": "b\n"} if name == "both add" else None
                got = self.batched(adopter, self.side(first, listed=base_listed), self.side(member, listed=listed))
                self.assertEqual(got["document"]["stop"]["reason"], "source_conflict")
                self.assertIn("dropped from the batch", got["document"]["stop"]["detail"])
                self.assertIn(expect, got["document"]["stop"]["detail"])
                self.assertEqual(got["document"]["stop"]["paths"], ["gen/lock.json"])
                self.assertEqual(adopter.tip("member"), got["head"])
                # What landed is the first member's lock, untouched by the dropped one.
                self.assertEqual(adopter.show("origin/main", "gen/lock.json").rstrip("\n"),
                                 self.side(first, listed=base_listed)["gen/lock.json"].rstrip("\n"))
        with self.subTest("only the batch's side added an output"):
            self.fresh()
            adopter = self.lock_adopter()
            got = self.batched(adopter, self.side({"src/b.txt": "b\n"}, listed={**self.START, "src/b.txt": "b\n"}),
                               self.side({"src/app.txt": "app two\n"}))
            self.assertEqual(got["document"]["outcome"], "merged", got["document"].get("stop"))
            self.assertEqual(self.listed(adopter), sorted([*self.START, "src/b.txt"]))
        for how in ("fails", "empty", "garbage", "deep", "object", "numbers"):
            for version in ("merge base", "base", "candidate"):
                with self.subTest(listing=how, version=version):
                    self.fresh()
                    marked = {"listingMode": how}
                    keep = marked if version == "merge base" else {}
                    adopter = self.lock_adopter([PY, "tools/listing.py"], **keep)
                    first = self.side({"src/lib.txt": "lib two\n"}, **(marked if version == "base" else keep))
                    member = self.side({"src/app.txt": "app two\n"}, **(marked if version == "candidate" else keep))
                    got = self.batched(adopter, first, member)
                    self.assertEqual(got["document"]["stop"]["reason"], "source_conflict")
                    self.assertIn(f"listing of gen/lock.json at the {version}", got["document"]["stop"]["detail"])
                    self.assertEqual(adopter.tip("member"), got["head"])
        with self.subTest("a version without the path"):
            self.fresh()
            adopter = self.adopter({"regenerable": {
                "paths": ["gen/**"], "regenerators": [[PY, "tools/regen.py"], [PY, "tools/regen_lock.py"]],
                "listings": [{"path": "gen/lock.json", "argv": REAL_LISTING}]}})
            adopter.advance_base({"tools/regen_lock.py": REGEN_LOCK.lstrip()})
            got = self.batched(adopter, self.side({"src/lib.txt": "lib two\n"}), self.side({"src/app.txt": "app two\n"}))
            self.assertEqual(got["document"]["stop"]["reason"], "source_conflict")
            self.assertIn("absent at the merge base", got["document"]["stop"]["detail"])

    def test_a_listing_gets_the_placeholder_values_of_its_step(self) -> None:
        argv = [PY, "tools/listing.py", "{head}"]
        adopter = self.lock_adopter(argv)
        self.candidate(adopter, self.side({"src/app.txt": "app two\n"}))
        adopter.advance_base(self.side({"src/lib.txt": "lib two\n"}))
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document().get("stop"))
        self.fresh()
        adopter = self.lock_adopter(argv)
        got = self.batched(adopter, self.side({"src/lib.txt": "lib two\n"}), self.side({"src/app.txt": "app two\n"}))
        self.assertEqual(got["document"]["outcome"], "merged", got["document"].get("stop"))

    def test_a_listing_must_name_one_regenerable_file(self) -> None:
        binding = Adopter.default_binding(type("Fixture", (), {"work": Path("/work"), "state": Path("/state"),
                                                                "remote": Path("/r"), "log": Path("/l")})())
        for name, listings, error in (
                ("a pattern", [{"path": "gen/*.json", "argv": REAL_LISTING}], "names one file, not a pattern"),
                ("a source file", [{"path": "src/app.txt", "argv": REAL_LISTING}], "is not a regenerable path"),
                ("twice", [{"path": "gen/lock.json", "argv": REAL_LISTING}] * 2, "is listed twice"),
                ("an empty argv", [{"path": "gen/lock.json", "argv": []}], "an argv must not be empty"),
                ("an unknown placeholder", [{"path": "gen/lock.json", "argv": ["{nope}"]}], "unknown placeholder")):
            with self.subTest(name):
                changed = {**binding, "regenerable": {**binding["regenerable"], "listings": listings}}
                self.assertTrue(any(error in problem for problem in queue.binding_errors(changed)),
                                queue.binding_errors(changed))
        self.assertEqual(queue.binding_errors({**binding, "regenerable": {
            **binding["regenerable"], "listings": [{"path": "gen/lock.json", "argv": REAL_LISTING}]}}), [])

    def test_a_publisher_that_runs_nothing_is_refused_naming_the_fix(self) -> None:
        binding = Adopter.default_binding(type("Fixture", (), {"work": Path("/work"), "state": Path("/state"),
                                                                "remote": Path("/r"), "log": Path("/l")})())
        state = binding.get("state") or {"directory": "state", "renderers": []}
        for publishers in ([{"id": "tracker", "kind": "host"}], [{"id": "ok", "argv": ["true"]}, {"id": "live", "kind": "host"}],
                           [{"id": "nothing", "kind": None}]):
            with self.subTest(publishers=publishers):
                errors = queue.binding_errors({**binding, "state": {**state, "publishers": publishers}})
                index = len(publishers) - 1
                self.assertIn(f"/state/publishers/{index}: a publisher with a kind and no argv runs nothing; fix: name "
                              "the tracker publisher instead (run-delivery's lead-setup adds it)", errors)
        self.assertEqual(queue.binding_errors({**binding, "state": {**state, "publishers": [{"id": "ok", "argv": ["true"]}]}}), [])
        # A binding of the wrong shape gets the schema's verdict, never an error of the check's own.
        for shape in ([], {**binding, "state": ["x"]}, {**binding, "state": {**state, "publishers": 5}},
                      {**binding, "state": {**state, "publishers": [7]}}):
            with self.subTest(shape=type(shape).__name__):
                self.assertTrue(queue.binding_errors(shape))


REVIEW_GATE = """
import os, subprocess, sys, time
remote = os.environ["MQ_REMOTE"]
branch, head = sys.argv[1], sys.argv[2]
with open(os.environ["MQ_LOG"], "a") as log:
    log.write("review " + branch + " " + head + "\\n")
listed = subprocess.run(["git", "ls-remote", "--heads", remote, "refs/heads/" + branch],
                        capture_output=True, text=True, check=True).stdout.split()
mode = os.environ.get("MQ_REVIEW", "current")
if mode == "slow":
    # Past the gate's own timeout, within checkTimeoutSeconds: only the gate's timeout stops it.
    time.sleep(5)
if mode == "pause":
    time.sleep(2)
if mode == "missing":
    sys.exit("no review by the pipeline yet; token=ghp_SECRETVALUE123")
if mode == "stale" or not listed or listed[0] != head:
    sys.exit("the latest review is of another head")
"""

GATE = {"argv": [PY, "tools/review_gate.py", "{branch}", "{expectedHead}"], "timeoutSeconds": 10}


class ReviewGateTests(MergeQueueTestCase):
    """The merge step merges only with a review current at the synced head, when the binding declares a gate."""

    def gated(self, review: str = "current", gate: dict | None = None) -> Adopter:
        adopter = self.environment_adopter({"MQ_REVIEW": review}, {"reviewGate": gate or GATE})
        adopter.advance_base({"tools/review_gate.py": REVIEW_GATE.lstrip()})
        return adopter

    def forge_steps(self, adopter: Adopter) -> list[str]:
        return [line.split()[0] + " " + line.split()[1] for line in adopter.log_lines()
                if line.startswith(("forge ready", "forge merge", "review "))]

    def test_a_current_review_at_the_synced_head_merges(self) -> None:
        adopter = self.gated()
        self.candidate(adopter, {"src/app.txt": "app two\n"})
        adopter.advance_base({"src/lib.txt": "lib two\n"})
        results = self.run_queue(adopter, "feature")
        document = results[-1].document()
        self.assertEqual(document["outcome"], "merged", document.get("stop"))
        # The gate ran after ready and before merge, given the synced head the base now carries as a parent.
        self.assertEqual(self.forge_steps(adopter), ["forge ready", "review feature", "forge merge"])
        reviewed = next(line.split()[2] for line in adopter.log_lines() if line.startswith("review "))
        self.assertEqual(reviewed, document["syncedHead"])
        self.assertNotEqual(reviewed, adopter.item("feature")["head"], "the sync moved the head, and the gate saw the new one")

    def test_a_missing_stale_or_slow_review_stops_and_nothing_merges(self) -> None:
        for review, how in (("missing", "exited 1"), ("stale", "exited 1"), ("slow", "timed out")):
            with self.subTest(review=review):
                self.fresh()
                gate = {**GATE, "timeoutSeconds": 1} if review == "slow" else None
                adopter = self.gated(review, gate)
                self.candidate(adopter, {"src/app.txt": "app two\n"})
                before = adopter.tip("main")
                document = self.assertStopped(self.run_queue(adopter, "feature"), "review_not_current")
                Draft202012Validator(schema("merge-queue-result")).validate(document)
                self.assertIn(f"the review gate {how}", document["stop"]["detail"])
                self.assertIn(document["syncedHead"], document["stop"]["detail"])
                self.assertEqual(adopter.tip("main"), before)
                self.assertNotIn("forge merge", self.forge_steps(adopter))
                self.assertEqual(adopter.item("feature")["stage"], "stopped")

    def test_the_gates_output_stays_in_its_log_and_out_of_the_record(self) -> None:
        adopter = self.gated("missing")
        self.candidate(adopter, {"src/app.txt": "app two\n"})
        document = self.assertStopped(self.run_queue(adopter, "feature"), "review_not_current")
        log = re.search(r"logs/\S+\.log", document["stop"]["detail"]).group(0)
        self.assertIn("ghp_SECRETVALUE123", (adopter.state / log).read_text(encoding="utf-8"))
        for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE):
            self.assertNotIn("ghp_SECRETVALUE123", (adopter.state / name).read_text(encoding="utf-8"))
        self.assertNotIn("ghp_SECRETVALUE123", json.dumps(document))

    def test_in_a_batch_each_member_lands_only_with_a_current_review(self) -> None:
        adopter = self.gated()
        self.candidate(adopter, {"src/one.txt": "one\n"}, name="one")
        self.candidate(adopter, {"src/two.txt": "two\n"}, name="two")
        documents = {result.document()["candidate"]: result.document()
                     for result in adopter.runner().run_command("batch", ["one", "two"])}
        self.assertEqual((documents["one"]["outcome"], documents["two"]["outcome"]), ("merged", "merged"))
        reviewed = [line.split()[1:] for line in adopter.log_lines() if line.startswith("review ")]
        self.assertEqual(reviewed, [["one", documents["one"]["syncedHead"]], ["two", documents["two"]["syncedHead"]]])
        self.fresh()
        adopter = self.gated("missing")
        self.candidate(adopter, {"src/one.txt": "one\n"}, name="one")
        self.candidate(adopter, {"src/two.txt": "two\n"}, name="two")
        before = adopter.tip("main")
        documents = {result.document()["candidate"]: result.document()
                     for result in adopter.runner().run_command("batch", ["one", "two"])}
        self.assertEqual(documents["one"]["stop"]["reason"], "review_not_current")
        self.assertEqual(adopter.tip("main"), before)
        self.assertNotIn("forge merge", self.forge_steps(adopter))

    def test_a_gate_without_its_own_timeout_has_the_checks_timeout(self) -> None:
        # Two seconds is within checkTimeoutSeconds (20), so the gate finishes and the candidate merges.
        adopter = self.gated("pause", {"argv": GATE["argv"]})
        self.candidate(adopter, {"src/app.txt": "app two\n"})
        document = self.run_queue(adopter, "feature")[-1].document()
        self.assertEqual(document["outcome"], "merged", document.get("stop"))

    def test_without_a_gate_merging_is_unchanged(self) -> None:
        adopter = self.environment_adopter({"MQ_REVIEW": "missing"})
        self.candidate(adopter, {"src/app.txt": "app two\n"})
        self.assertEqual(self.run_queue(adopter, "feature")[-1].document()["outcome"], "merged")
        self.assertNotIn("review feature", self.forge_steps(adopter))

    def test_the_binding_refuses_a_gate_that_names_no_head(self) -> None:
        binding = Adopter.default_binding(type("Fixture", (), {"work": Path("/work"), "state": Path("/state"),
                                                                "remote": Path("/r"), "log": Path("/l")})())
        for name, gate, error in (
                ("no head", {"argv": [PY, "gate.py", "{branch}"]}, "must name the head"),
                ("a head inside an element", {"argv": [PY, "gate.py", "--head={expectedHead}"]}, "must name the head"),
                ("an empty argv", {"argv": []}, "an argv must not be empty"),
                ("an unknown placeholder", {"argv": [PY, "{expectedHead}", "{nope}"]}, "unknown placeholder"),
                ("over the ceiling", {**GATE, "timeoutSeconds": 61}, "above checkTimeoutCeiling"),
                ("no time at all", {**GATE, "timeoutSeconds": 0}, "timeoutSeconds")):
            with self.subTest(name):
                problems = queue.binding_errors({**binding, "reviewGate": gate})
                self.assertTrue(any(error in problem for problem in problems), problems)
        for head in ("{expectedHead}", "{head}"):
            with self.subTest(head=head):
                self.assertEqual(queue.binding_errors({**binding, "reviewGate": {"argv": [PY, "gate.py", head]}}), [])
        self.assertEqual(queue.binding_errors({**binding, "reviewGate": {**GATE, "timeoutSeconds": 60}}), [],
                         "a gate may take the whole ceiling")


class MergeRefusalDetailTests(MergeQueueTestCase):
    """A refused merge says why in the stop detail, bounded and scrubbed, and keeps its whole output in a log."""

    SECRET = "s3cr3t-forge-value-from-the-environment"

    def refused(self, pad: int = 0) -> tuple[Adopter, dict]:
        adopter = self.environment_adopter({"MQ_MERGE": "loud", "MQ_MERGE_PAD": str(pad), "MQ_FORGE_TOKEN": self.SECRET})
        self.candidate(adopter)
        before = adopter.tip("main")
        document = self.assertStopped(self.run_queue(adopter, "feature"), "merge_refused")
        Draft202012Validator(schema("merge-queue-result")).validate(document)
        self.assertEqual(adopter.tip("main"), before)
        return adopter, document

    def test_the_merge_commands_reason_reaches_the_stop_detail_and_its_secrets_do_not(self) -> None:
        adopter, document = self.refused()
        detail = document["stop"]["detail"]
        self.assertIn("merge refused: Head branch was modified. Review and try the merge again.", detail)
        self.assertIn(document["syncedHead"], detail)
        for secret in (self.SECRET, "ghx_keyedvalue123", "abc.def.ghi", "ghp_" + "A" * 36):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, detail)
                for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE):
                    self.assertNotIn(secret, (adopter.state / name).read_text(encoding="utf-8"))
                self.assertNotIn(secret, json.dumps(document))
        self.assertIn("[redacted]", detail)
        # The whole output, secrets and all, stays in the log the detail names, under the state directory.
        log = re.search(r"logs/\S+\.log", detail).group(0)
        kept = (adopter.state / log).read_text(encoding="utf-8")
        self.assertIn(self.SECRET, kept)
        self.assertIn("Head branch was modified", kept)

    def test_a_long_refusal_keeps_only_its_bounded_tail(self) -> None:
        _, document = self.refused(pad=5000)
        detail = document["stop"]["detail"]
        self.assertIn("Head branch was modified", detail, "the end of the output is what's kept")
        self.assertNotIn("x" * (queue.TAIL_LIMIT + 1), detail)
        self.assertLess(len(detail), queue.TAIL_LIMIT + 400)

    def test_a_silent_refusal_says_so_and_still_names_its_log(self) -> None:
        adopter = self.environment_adopter({"MQ_MERGE": "refuse"})
        self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), "merge_refused")
        self.assertIn("printed nothing", document["stop"]["detail"])
        self.assertTrue((adopter.state / re.search(r"logs/\S+\.log", document["stop"]["detail"]).group(0)).is_file())

    def test_a_merge_that_times_out_says_so_and_names_its_log(self) -> None:
        adopter = self.environment_adopter({"MQ_MERGE": "hang"}, {"checkTimeoutSeconds": 4})
        self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), "merge_refused")
        self.assertIn(": timed out;", document["stop"]["detail"])
        self.assertTrue((adopter.state / re.search(r"logs/\S+\.log", document["stop"]["detail"]).group(0)).is_file())

    def test_the_scrub_covers_each_kind_of_name_shape_and_length(self) -> None:
        names = {"API_KEY": "key-value-1", "DB_PASSWORD": "pass-value-2", "APP_SECRET": "secret-value-3",
                 "CLOUD_CREDENTIALS": "cred-value-4", "SSH_PRIVATE_KEY": "private-value-5", "FORGE_TOKEN": "token-value-6"}
        for name, value in names.items():
            with self.subTest(name=name):
                self.assertEqual(queue.scrubbed_tail(f"saw {value} here", {name: value}), "saw [redacted] here")
        # The length boundary: four characters are redacted, three are left, as too likely to be ordinary text.
        self.assertEqual(queue.scrubbed_tail("x abcd y", {"A_TOKEN": "abcd"}), "x [redacted] y")
        self.assertEqual(queue.scrubbed_tail("x abc y", {"A_TOKEN": "abc"}), "x abc y")
        shapes = ("ghp_" + "a" * 36, "gho_" + "b" * 36, "github_pat_" + "c" * 30, "glpat-" + "d" * 20,
                  "xoxb-" + "1" * 12, "AKIA" + "E" * 16, "sk-" + "f" * 24)
        for shape in shapes:
            with self.subTest(shape=shape[:6]):
                self.assertEqual(queue.scrubbed_tail(f"leaked {shape} there", {}), "leaked [redacted] there")
        self.assertEqual(queue.scrubbed_tail("Proxy-Authorization: Basic Zm9vOmJhcg==\nnext", {}),
                         "Proxy-Authorization: [redacted]\nnext")

    def test_the_scrub_redacts_before_it_cuts(self) -> None:
        environment = {"FORGE_TOKEN": "value-of-the-token", "API_KEY": "abcd1234", "PATH": "/usr/bin", "SHORT_SECRET": "ab"}
        cases = {
            "an environment secret": ("failed with value-of-the-token", "failed with [redacted]"),
            "a keyed value": ("password: hunter22 then", "password: [redacted] then"),
            "an authorization header, to the end of its line": ("Authorization: Bearer xyz.abc\nnext", "Authorization: [redacted]\nnext"),
            "a bearer value": ("sent Bearer xyz.abc", "sent Bearer [redacted]"),
            "a token shape": ("saw github_pat_" + "B" * 30 + " there", "saw [redacted] there"),
            "a variable not named secret": ("in /usr/bin", "in /usr/bin"),
            "a secret too short to match safely": ("ab cd", "ab cd"),
            "JSON-quoted keys": ('{"token": "plainvalue1", "password":"hunter22"}', '{"token": "[redacted]", "password":"[redacted]"}'),
        }
        for name, (text, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(queue.scrubbed_tail(text, environment), expected)
        # A value containing a shorter one is redacted whole.
        self.assertEqual(queue.scrubbed_tail("leak abcdSECRETTAIL99 end", {"A_TOKEN": "abcd", "B_TOKEN": "abcdSECRETTAIL99"}),
                         "leak [redacted] end")
        # The runner's own identity is not a secret, though its variable names hold "AUTH"; an auth segment is.
        self.assertEqual(queue.scrubbed_tail("refused by merge-queue rule", {"GIT_AUTHOR_NAME": "merge-queue"}),
                         "refused by merge-queue rule")
        for name in ("HTTP_AUTH", "AUTH_HEADER", "FORGE_OAUTH_KEY", "FORGE_AUTHORIZATION", "AUTHKEY", "NPM_AUTHKEY"):
            with self.subTest(name=name):
                self.assertEqual(queue.scrubbed_tail("sent hidden-value-1", {name: "hidden-value-1"}), "sent [redacted]")
        # A secret straddling the cut is redacted whole before the tail is taken, so no part of it survives.
        straddling = "value-of-the-token" + "y" * 20
        tail = queue.scrubbed_tail(straddling, environment, limit=25)
        self.assertEqual(tail, ("[redacted]" + "y" * 20)[-25:])
        self.assertFalse(any(part in tail for part in ("value", "the-token", "of-the")))


class StepStderrTests(MergeQueueTestCase):
    """Every runner step that refuses keeps its whole output in a log and says why in the stop, bounded and scrubbed."""

    SECRET = "s3cr3t-step-value-from-the-environment"

    def loud(self, step: str, *extra: str) -> list[str]:
        """A step that writes a secret and a marker to its stderr, then fails."""
        code = ("import os, sys; sys.stderr.write('using ' + os.environ['MQ_STEP_TOKEN'] + '\\n'"
                f" + 'x' * 2000 + '\\nMARKER-{step}: the step refused for ' + os.environ['MQ_STEP_TOKEN'] + '\\n'); sys.exit(1)")
        return [PY, "-c", code, *extra]

    def rows(self) -> dict[str, tuple[dict, str, str]]:
        """Per step: the binding change that makes it the loud one, the stop it gives, and what the detail starts with."""
        return {
            "ready": ({"ready": self.loud("ready", "{candidate}")}, "ready_refused", "ready refused feature: "),
            "prepare": ({"prepare": [self.loud("prepare")]}, "prepare_failed", f"prepare {PY} failed: "),
            "candidate": ({"candidate": self.loud("candidate", "{candidate}")}, "candidate_unresolved",
                          "feature did not resolve to a branch and head: the candidate command exited 1; stderr: "),
            "regenerator": ({"regenerable": {"paths": ["gen/**"], "regenerators": [self.loud("regenerator")]}},
                            "sync_step_failed", f"regenerator {PY} failed: "),
            "postSync": ({"postSync": [{"argv": self.loud("postSync"), "paths": ["notes/fingerprint.txt"]}]},
                         "sync_step_failed", f"postSync step {PY} failed: "),
        }

    def test_each_step_keeps_its_stderr_in_a_log_and_a_scrubbed_tail_in_the_stop(self) -> None:
        for step, (changes, reason, opening) in self.rows().items():
            with self.subTest(step=step):
                self.fresh()
                adopter = self.environment_adopter({"MQ_STEP_TOKEN": self.SECRET}, changes)
                self.candidate(adopter)
                before = adopter.tip("main")
                document = self.assertStopped(self.run_queue(adopter, "feature"), reason)
                Draft202012Validator(schema("merge-queue-result")).validate(document)
                self.assertEqual(adopter.tip("main"), before)
                detail = document["stop"]["detail"]
                self.assertTrue(detail.startswith(opening), detail)
                # The secret on the marker's own line is in the tail, so only the scrub keeps it out.
                self.assertIn(f"MARKER-{step}: the step refused for [redacted]", detail)
                self.assertNotIn("x" * (queue.TAIL_LIMIT + 1), detail, "only a bounded tail is kept")
                # The whole stderr, secret and all, is in the log the detail names, under the state directory.
                log = re.search(r"its whole output is in (logs/\S+\.log) under the state directory", detail).group(1)
                kept = (adopter.state / log).read_text(encoding="utf-8")
                self.assertIn(f"using {self.SECRET}\n" + "x" * 2000 + f"\nMARKER-{step}: the step refused for {self.SECRET}\n", kept)
                self.assertNotIn(self.SECRET, json.dumps(document))
                for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE):
                    self.assertNotIn(self.SECRET, (adopter.state / name).read_text(encoding="utf-8"))

    def test_a_refused_push_says_why_scrubbed_and_names_its_log(self) -> None:
        # Git names the remote it failed to push to; declared as a secret, that path must not reach the stop.
        remote = str(self.directory / "remote.git")
        adopter = self.environment_adopter({"MQ_PUSH_TOKEN": remote})
        head = self.candidate(adopter)
        adopter.advance_base({"src/lib.txt": "lib two\n"})
        # A held lock on the candidate's ref makes the remote refuse the synced head's push.
        lock = adopter.remote / "refs" / "heads" / "feature.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_bytes(b"")
        detail = self.assertStopped(self.run_queue(adopter, "feature"), "push_refused")["stop"]["detail"]
        self.assertEqual(adopter.tip("feature"), head)
        self.assertIn("pushing feature failed: ", detail)
        said = detail.split("failed: ", 1)[1].split("; its whole output", 1)[0]
        self.assertIn("feature", said, "the tail carries git's words")
        self.assertIn("[redacted]", said)
        self.assertNotIn(remote, detail)
        for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE):
            self.assertNotIn(remote, (adopter.state / name).read_text(encoding="utf-8"))
        log = re.search(r"its whole output is in (logs/\S+\.log) under the state directory", detail).group(1)
        kept = (adopter.state / log).read_text(encoding="utf-8")
        self.assertIn("lock", kept)
        self.assertIn(remote, kept)

    def test_the_guide_states_what_the_scrub_removes_and_that_it_removes_nothing_else(self) -> None:
        guide = " ".join((ROOT / "skills/orchestrate-work/references/orchestration-guide.md").read_text(encoding="utf-8").split())
        for phrase in (
                "Every step the runner runs for a candidate keeps its whole output when it refuses",
                "The scrub has a limit.",
                "It removes nothing else: a secret outside the environment that matches none of these reaches the stop, "
                "the record and the journal.",
                "The log is never scrubbed"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, guide)

    def test_a_secret_inside_the_tail_is_redacted_there(self) -> None:
        # The secret on the marker's own line, so the tail would carry it if the scrub did not run.
        code = ("import os, sys; sys.stderr.write('refused for ' + os.environ['MQ_STEP_TOKEN'] + ' and ghp_' + 'A' * 36"
                " + '\\n'); sys.exit(1)")
        adopter = self.environment_adopter({"MQ_STEP_TOKEN": self.SECRET}, {"ready": [PY, "-c", code, "{candidate}"]})
        self.candidate(adopter)
        detail = self.assertStopped(self.run_queue(adopter, "feature"), "ready_refused")["stop"]["detail"]
        self.assertIn("ready refused feature: refused for [redacted] and [redacted]; its whole output is in logs/", detail)

    def test_a_step_that_prints_nothing_or_times_out_says_so_and_names_its_log(self) -> None:
        silent = [PY, "-c", "import sys; sys.exit(1)"]
        slow = [PY, "-c", "import time; time.sleep(30)"]
        for how, argv, says in (("silent", silent, "printed nothing"), ("slow", slow, "timed out")):
            for step, changes, reason in (
                    ("ready", {"ready": [*argv, "{candidate}"]}, "ready_refused"),
                    ("prepare", {"prepare": [argv]}, "prepare_failed"),
                    ("postSync", {"postSync": [{"argv": argv, "paths": ["notes/fingerprint.txt"]}]}, "sync_step_failed")):
                with self.subTest(step=step, how=how):
                    self.fresh()
                    adopter = self.environment_adopter({}, {**changes, "checkTimeoutSeconds": 2})
                    self.candidate(adopter)
                    detail = self.assertStopped(self.run_queue(adopter, "feature"), reason)["stop"]["detail"]
                    self.assertIn(says, detail)
                    log = re.search(r"its whole output is in (logs/\S+\.log) under the state directory", detail).group(1)
                    self.assertTrue((adopter.state / log).is_file())


class SelectionTests(MergeQueueTestCase):
    def check_ids(self, results: list) -> list[str]:
        check = next(result.document() for result in results if result.document()["subcommand"] == "check")
        return check["selection"]["checks"]

    def test_a_candidate_cannot_shrink_its_own_selection(self) -> None:
        adopter = self.adopter()
        always_removed = copy.deepcopy(adopter.binding)
        always_removed["alwaysRun"] = []
        self.candidate(adopter, {
            "src/app.txt": "app two\n",
            "tools/provider-mode.txt": "empty\n",
            ".agents/orchestration.json": json.dumps(always_removed, indent=2) + "\n",
        })
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        check = results[1].document()
        self.assertEqual([provider["source"] for provider in check["selection"]["providers"]], ["base", "candidate"])
        self.assertIn("unit", check["selection"]["checks"])
        self.assertIn("always", check["selection"]["checks"])

    def test_a_missing_failing_malformed_or_timed_out_provider_falls_back(self) -> None:
        for mode, reason in (("fail", "provider_failed"), ("malformed", "provider_malformed"), ("timeout", "provider_timed_out")):
            with self.subTest(mode=mode):
                remove_tree(self.directory)
                self.directory.mkdir()
                # The timeout case waits out the provider's whole timeout: keep it short.
                provider = {"argv": [PY, "tools/provider.py"], "cwd": ".", "timeoutSeconds": 1,
                            "providerInputs": ["tools/provider.py", "tools/provider-mode.txt"]}
                adopter = self.adopter({"provider": provider} if mode == "timeout" else None, mode=mode)
                self.candidate(adopter)
                results = self.run_queue(adopter, "feature")
                check = results[1].document()
                self.assertEqual(check["selection"]["fallbackReasons"], [reason])
                self.assertIn("fallback", check["selection"]["checks"])
                self.assertTrue(check["selection"]["widened"])
                self.assertEqual(results[-1].document()["outcome"], "merged")
        remove_tree(self.directory)
        self.directory.mkdir()
        adopter = self.adopter({"provider": None})
        self.candidate(adopter)
        check = self.run_queue(adopter, "feature")[1].document()
        self.assertEqual(check["selection"]["fallbackReasons"], ["provider_missing"])

    def test_an_empty_selection_falls_back_and_stops_when_still_empty(self) -> None:
        adopter = self.adopter(mode="empty")
        self.candidate(adopter)
        check = self.run_queue(adopter, "feature")[1].document()
        self.assertEqual(check["selection"]["fallbackReasons"], ["empty_selection"])
        self.assertIn("fallback", check["selection"]["checks"])
        remove_tree(self.directory)
        self.directory.mkdir()
        adopter = self.adopter({"alwaysRun": [], "fallback": []}, mode="empty")
        self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "no_checks")

    def test_uncovered_paths_add_the_fallback(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/app.txt": "app two\n", "docs/readme.txt": "new\n"})
        check = self.run_queue(adopter, "feature")[1].document()
        self.assertEqual(check["selection"]["uncovered"], ["docs/readme.txt"])
        self.assertEqual(check["selection"]["fallbackReasons"], ["uncovered"])
        self.assertIn("fallback", check["selection"]["checks"])

    def test_prerequisites_run_first(self) -> None:
        adopter = self.adopter(mode="ordered")
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        checks = [line.split()[1] for line in adopter.log_lines() if line.startswith("check ")]
        self.assertLess(checks.index("first"), checks.index("second"))

    def test_a_cycle_or_an_escaping_cwd_is_refused(self) -> None:
        for mode in ("cycle", "outside"):
            with self.subTest(mode=mode):
                remove_tree(self.directory)
                self.directory.mkdir()
                adopter = self.adopter(mode=mode)
                self.candidate(adopter)
                check = self.run_queue(adopter, "feature")[1].document()
                self.assertEqual(check["selection"]["providers"][0]["outcome"], "malformed")
                self.assertEqual(check["selection"]["fallbackReasons"], ["provider_malformed"])

    @unittest.skipIf(os.name == "nt", "process groups and ps are POSIX")
    def test_a_timed_out_check_is_killed_with_its_children(self) -> None:
        adopter = self.adopter(mode="slow-child")
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertStopped(results, "check_failed")
        checks = {check["id"]: check for check in results[1].document()["checks"]}
        self.assertEqual(checks["slow"]["state"], "timed_out")
        # Killing only the check would leave its child holding the output pipes
        # open, and the runner would wait out the child's whole sleep.
        self.assertLess(checks["slow"]["seconds"], 15)
        child = int(Path(str(adopter.log) + ".child").read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.alive(child):
            time.sleep(0.05)
        self.assertFalse(self.alive(child), "the check's child outlived its timeout")

    @staticmethod
    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(state) and not state.startswith("Z")

    def test_an_unknown_environment_profile_is_refused(self) -> None:
        adopter = self.adopter({"alwaysRun": [check_entry("always", environmentProfile="missing")]})
        self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "binding_invalid")
        remove_tree(self.directory)
        self.directory.mkdir()
        adopter = self.adopter(mode="profile")
        self.candidate(adopter)
        check = self.run_queue(adopter, "feature")[1].document()
        self.assertEqual(check["selection"]["providers"][0]["outcome"], "malformed")


class FailureTests(MergeQueueTestCase):
    def test_a_check_already_failing_on_the_base_stops_the_queue(self) -> None:
        adopter = self.adopter()
        adopter.advance_base({"src/app.txt": "FAIL on base\n", "gen/generated.txt": "FAIL on base\nlib one\n"})
        self.candidate(adopter, {"src/lib.txt": "lib two\n"})
        self.candidate(adopter, {"src/lib.txt": "lib three\n"}, name="second")
        results = self.run_queue(adopter, "feature", "second")
        document = self.assertStopped(results, "base_broken")
        self.assertTrue(document["stop"]["queueStopped"])
        self.assertEqual(adopter.item("second")["stage"], "queued")
        kinds = [entry["kind"] for entry in state_tool.read_journal(adopter.state)]
        self.assertIn("base.broken", kinds)

    def test_a_failing_test_the_candidate_adds_stops_only_the_candidate(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"tests/test_new.txt": "FAIL\n"})
        document = self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertFalse(document["stop"]["queueStopped"])

    def test_a_check_failing_only_on_the_candidate_stops_with_check_failed(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/app.txt": "FAIL here\n"})
        self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")

    def test_a_refusing_ready_or_merge_stops(self) -> None:
        for variable, reason in (("MQ_READY", "ready_refused"), ("MQ_MERGE", "merge_refused")):
            with self.subTest(reason=reason):
                self.fresh()
                adopter = self.environment_adopter({variable: "refuse"})
                before = adopter.tip("main")
                self.candidate(adopter)
                self.assertStopped(self.run_queue(adopter, "feature"), reason)
                self.assertEqual(adopter.tip("main"), before)

    def test_a_failure_the_base_does_not_share_stops_only_the_candidate(self) -> None:
        adopter = self.adopter(mode="implicit")
        self.candidate(adopter, {"src/app.txt": "FAIL here\n"})
        document = self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertFalse(document["stop"]["queueStopped"])
        # The check names no changed path, so it was re-run on the base, where it passed.
        self.assertEqual(adopter.log_lines().count("check implicit -"), 2)

    def test_a_check_in_a_directory_the_candidate_adds_is_never_the_bases_failure(self) -> None:
        adopter = self.adopter(mode="new-cwd")
        self.candidate(adopter, {"src/app.txt": "app two\n", "newdir/readme.txt": "new\n"})
        document = self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertFalse(document["stop"]["queueStopped"])

    def test_a_failing_prepare_stops_the_candidate(self) -> None:
        adopter = self.adopter({"prepare": [[PY, "-c", "import sys; sys.exit(3)"]]})
        self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), "prepare_failed")
        self.assertEqual(document["outcome"], "stopped")
        self.assertEqual(adopter.item("feature")["stage"], "stopped")

    def test_merge_refuses_a_base_its_checked_head_does_not_contain(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="second")
        runner = adopter.runner()
        self.assertEqual(runner.run_command("sync", ["feature"])[0].document()["outcome"], "synced")
        self.assertEqual(runner.run_command("check", ["feature"])[0].document()["outcome"], "checked")
        adopter.advance_base({"src/other.txt": "other\n"})
        # Another candidate's sync records the new base in the shared state.
        self.assertEqual(runner.run_command("sync", ["second"])[0].document()["outcome"], "synced")
        merged = runner.run_command("merge", ["feature"])[0].document()
        self.assertEqual(merged["stop"]["reason"], "base_moved", merged)
        self.assertNotIn("forge merge", " ".join(adopter.log_lines()))

    def test_a_merge_with_a_different_tree_is_unverified(self) -> None:
        adopter = self.environment_adopter({"MQ_MERGE": "unverified"})
        self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), "merged_unverified")
        self.assertTrue(document["stop"]["queueStopped"])


class ReleaseTests(MergeQueueTestCase):
    def release(self, adopter: Adopter, ref: str, approved_by: str | None = None) -> dict:
        return adopter.runner().run_command("release", [ref], approved_by)[0].document()

    def test_a_ref_not_on_the_base_is_refused(self) -> None:
        adopter = self.adopter()
        head = adopter.lane_commit("feature", {"src/app.txt": "elsewhere\n"})
        run("fetch", "--quiet", "origin", cwd=adopter.operator)
        document = self.release(adopter, head, "operator")
        self.assertEqual(document["stop"]["reason"], "ref_not_on_base")
        self.assertEqual(adopter.log_lines(), [])

    def test_an_approval_target_needs_approved_by(self) -> None:
        adopter = self.adopter()
        document = self.release(adopter, adopter.tip("main"))
        self.assertEqual(document["stop"]["reason"], "approval_required")
        self.assertEqual(adopter.log_lines(), [])

    def test_targets_run_in_order_and_stop_at_the_first_stopping_failure(self) -> None:
        adopter = self.environment_adopter({"MQ_RELEASE_FAIL": "clients"})
        document = self.release(adopter, adopter.tip("main"), "operator in chat")
        self.assertEqual(document["stop"]["reason"], "release_failed")
        self.assertEqual(adopter.log_lines(), ["release backend", "release clients"])
        self.assertEqual([target["state"] for target in document["release"]["targets"]], ["passed", "failed", "skipped"])
        self.assertEqual(document["release"]["targets"][1]["approvedBy"], "operator in chat")
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"]["releasedHead"])

    def test_a_release_records_the_released_head(self) -> None:
        adopter = self.adopter()
        tip = adopter.tip("main")
        document = self.release(adopter, tip, "operator in chat")
        self.assertEqual(document["outcome"], "released")
        self.assertEqual(adopter.log_lines(), ["release backend", "release clients", "release docs"])
        self.assertEqual(state_tool.read_state(adopter.state)["meta"]["releasedHead"], tip)


class IsolationTests(MergeQueueTestCase):
    def test_a_fetch_changes_only_the_runners_own_refs(self) -> None:
        adopter = self.adopter()
        tracking = run("rev-parse", "refs/remotes/origin/main", cwd=adopter.operator)
        adopter.advance_base({"src/other.txt": "other\n"})
        tip = adopter.runner().fetch("main", "base")
        self.assertNotEqual(tip, tracking)
        self.assertEqual(run("rev-parse", f"{queue.PRIVATE_REF}/base", cwd=adopter.operator), tip)
        self.assertEqual(run("rev-parse", "refs/remotes/origin/main", cwd=adopter.operator), tracking,
                         "git moves a tracking ref beside an explicit refspec unless told not to")

    def test_a_fetch_is_not_stopped_by_another_process_holding_the_tracking_ref(self) -> None:
        # Worktrees share remote-tracking refs, so another checkout's fetch can hold this lock at any moment.
        adopter = self.adopter()
        adopter.advance_base({"src/other.txt": "other\n"})
        lock = Path(run("rev-parse", "--absolute-git-dir", cwd=adopter.operator)) / "refs/remotes/origin/main.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_bytes(b"")
        self.addCleanup(lock.unlink, True)
        tip = run("ls-remote", "origin", "refs/heads/main", cwd=adopter.operator).split()[0]
        self.assertEqual(adopter.runner().fetch("main", "base"), tip)

    def test_the_private_hooks_folder_goes_with_its_runner(self) -> None:
        runner = self.adopter().runner()
        folder = runner._bootstrap_hooks
        self.assertTrue(folder.is_dir())
        del runner
        gc.collect()
        self.assertFalse(folder.exists())

    def test_the_private_hooks_folder_goes_when_a_command_never_runs_a_subcommand(self) -> None:
        adopter = self.adopter()
        temporary = self.directory / "temporary"
        temporary.mkdir()
        script = ("import sys; sys.path.insert(0, sys.argv[1]); import merge_queue; from pathlib import Path; "
                  "runner = merge_queue.Runner(Path(sys.argv[2]), Path(sys.argv[3]), remote='origin', base='main')")
        subprocess.run([PY, "-B", "-c", script, str(SCRIPTS), str(adopter.operator), str(adopter.state)], check=True,
                       env={**os.environ, "TMPDIR": str(temporary), "TEMP": str(temporary), "TMP": str(temporary)})
        self.assertEqual([path.name for path in temporary.iterdir()], [])

    def test_a_second_runner_is_refused_by_the_lock(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        with state_tool.file_lock(adopter.state / state_tool.QUEUE_LOCK):
            results = self.run_queue(adopter, "feature")
        document = self.assertStopped(results, "queue_locked")
        self.assertEqual(document["outcome"], "refused")
        self.assertEqual(adopter.item("feature")["stage"], "queued")

    def test_a_runner_waits_for_the_lock_when_asked(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        holder = subprocess.Popen([PY, "-B", "-c", (
            "import sys, time; sys.path.insert(0, sys.argv[1]); import delivery_state as d; "
            "from pathlib import Path\nwith d.file_lock(Path(sys.argv[2]) / d.QUEUE_LOCK):\n    print('held', flush=True); time.sleep(1.5)"
        ), str(SCRIPTS), str(adopter.state)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        results = self.run_queue(adopter, "feature", lock_wait=20)
        holder.wait()
        self.assertEqual(results[-1].document()["outcome"], "merged")

    def test_stale_checkouts_are_pruned_at_the_next_start(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        stale = adopter.work / "checkouts" / "99-stale"
        run("worktree", "add", "--detach", "--quiet", str(stale), "origin/main", cwd=adopter.operator)
        # Marked as the runner marks its own checkouts; an unmarked worktree there is refused.
        (Path(run("rev-parse", "--absolute-git-dir", cwd=stale)) / queue.CHECKOUT_MARKER).write_bytes(b"")
        self.assertIn(stale.as_posix(), run("worktree", "list", cwd=adopter.operator))
        self.run_queue(adopter, "feature")
        self.assertFalse(stale.exists())
        self.assertNotIn(stale.as_posix(), run("worktree", "list", cwd=adopter.operator))
        self.assertEqual(list((adopter.work / "checkouts").iterdir()), [])

    def test_the_operators_checkout_and_the_base_branch_are_never_written(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        (adopter.operator / "local.txt").write_text("operator's own work\n")
        branch = run("symbolic-ref", "HEAD", cwd=adopter.operator)
        head = run("rev-parse", "HEAD", cwd=adopter.operator)
        status = run("status", "--porcelain", cwd=adopter.operator)
        base = adopter.tip("main")
        results = adopter.runner().run_command("sync", ["feature"])
        self.assertEqual(results[0].document()["outcome"], "synced")
        self.assertEqual(adopter.tip("main"), base)
        self.assertEqual(run("symbolic-ref", "HEAD", cwd=adopter.operator), branch)
        self.assertEqual(run("rev-parse", "HEAD", cwd=adopter.operator), head)
        self.assertEqual(run("status", "--porcelain", cwd=adopter.operator), status)
        self.assertEqual(run("rev-parse", "refs/heads/main", cwd=adopter.operator), head)


class HardeningTests(MergeQueueTestCase):
    """Holes found in review, each pinned by the race or input that opened it."""

    def test_a_candidate_merged_after_another_still_needs_the_new_base(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/app.txt": "app two\n"})
        self.candidate(adopter, {"src/other.txt": "other\n"}, name="second")
        runner = adopter.runner()
        for subcommand, subject in (("sync", "feature"), ("check", "feature"), ("sync", "second"), ("check", "second"), ("merge", "second")):
            document = runner.run_command(subcommand, [subject])[0].document()
            self.assertNotIn(document["outcome"], ("stopped", "refused"), document)
        merged = runner.run_command("merge", ["feature"])[0].document()
        self.assertEqual(merged["stop"]["reason"], "base_moved")
        self.assertEqual(adopter.show("origin/main", "src/other.txt"), "other")

    def test_a_candidate_provider_cannot_replace_a_binding_check(self) -> None:
        adopter = self.adopter({"alwaysRun": [check_entry("always", "src/app.txt")]})
        self.candidate(adopter, {"src/app.txt": "FAIL here\n", "tools/provider-mode.txt": "shadow\n"})
        results = self.run_queue(adopter, "feature")
        self.assertStopped(results, "check_failed")
        providers = results[1].document()["selection"]["providers"]
        self.assertEqual([provider["outcome"] for provider in providers], ["selected", "malformed"])

    def test_a_candidate_provider_cannot_cancel_the_empty_selection_fallback(self) -> None:
        adopter = self.adopter(mode="empty")
        self.candidate(adopter, {"src/app.txt": "app two\n", "tools/provider-mode.txt": "dummy\n"})
        check = self.run_queue(adopter, "feature")[1].document()
        self.assertIn("empty_selection", check["selection"]["fallbackReasons"])
        self.assertIn("fallback", check["selection"]["checks"])

    def test_an_unexpected_error_still_writes_a_result_and_stops_the_candidate(self) -> None:
        adopter = self.adopter()
        run("checkout", "--quiet", "--orphan", "orphan", cwd=adopter.lane)
        Adopter.write(adopter.lane, "src/app.txt", "unrelated\n")
        run("add", "--all", cwd=adopter.lane)
        run("commit", "--quiet", "-m", "unrelated history", cwd=adopter.lane)
        run("push", "--quiet", "origin", "orphan:orphan", cwd=adopter.lane)
        adopter.enqueue("orphan", run("rev-parse", "HEAD", cwd=adopter.lane))
        results = self.run_queue(adopter, "orphan")
        document = self.assertStopped(results, "runner_error")
        Draft202012Validator(schema("merge-queue-result")).validate(document)
        self.assertEqual(adopter.item("orphan")["stage"], "stopped")

    def test_a_base_re_run_runs_the_checks_prerequisites_first(self) -> None:
        adopter = self.adopter(mode="prereq")
        self.candidate(adopter, {"src/app.txt": "FAIL here\n"})
        document = self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertFalse(document["stop"]["queueStopped"])

    def test_regeneration_that_settles_only_on_a_third_pass_is_unstable(self) -> None:
        adopter = self.environment_adopter({"MQ_REGEN": "twice"})
        head = self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "unstable_regeneration")
        self.assertEqual(adopter.tip("feature"), head)

    def test_a_merge_on_the_wrong_first_parent_is_unverified(self) -> None:
        adopter = self.environment_adopter({"MQ_MERGE": "swapped"})
        self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "merged_unverified")

    def test_check_refuses_a_head_synced_with_an_older_base(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        runner = adopter.runner()
        self.assertEqual(runner.run_command("sync", ["feature"])[0].document()["outcome"], "synced")
        adopter.advance_base({"src/other.txt": "other\n"})
        self.assertEqual(runner.run_command("check", ["feature"])[0].document()["stop"]["reason"], "base_moved")

    def test_a_check_that_dirties_the_checkout_stops(self) -> None:
        adopter = self.adopter(mode="dirty")
        self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), "dirty_checkout")
        self.assertEqual(document["stop"]["paths"], ["stray-output.txt"])

    def test_profiles_substitute_placeholders_and_timeouts_respect_the_ceiling(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        synced = adopter.item("feature")["syncedHead"]
        self.assertIn(f"check always {synced}", adopter.log_lines())
        self.fresh()
        adopter = self.adopter(mode="ceiling")
        self.candidate(adopter)
        check = self.run_queue(adopter, "feature")[1].document()
        self.assertEqual(check["selection"]["providers"][0]["outcome"], "malformed")

    def test_bindings_that_misplace_work_root_or_name_another_base_are_refused(self) -> None:
        cases = {
            "contains the repository": {"workRoot": str(self.directory)},
            "inside the working tree": {"workRoot": "scratch"},
            "another base": {"base": {"remote": "origin", "ref": "develop"}},
        }
        for name, changes in cases.items():
            with self.subTest(name):
                self.fresh()
                adopter = self.adopter(changes)
                self.candidate(adopter)
                document = self.assertStopped(self.run_queue(adopter, "feature"), "binding_invalid")
                self.assertEqual(document["outcome"], "refused")
                self.assertEqual(adopter.item("feature")["stage"], "queued")

    @unittest.skipIf(os.name == "nt", "sessions are POSIX")
    def test_a_grandchild_holding_the_pipes_does_not_hang_the_runner(self) -> None:
        adopter = self.adopter(mode="escape")
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertStopped(results, "check_failed")
        check = {item["id"]: item for item in results[1].document()["checks"]}["escape"]
        self.assertEqual(check["state"], "timed_out")
        # Without the bounded drain the runner waits out the grandchild's 30 seconds.
        self.assertLess(check["seconds"], 15)

    def test_a_refusal_raised_mid_step_leaves_no_stage_in_progress(self) -> None:
        # {ref} has no value while syncing, so the lead input is refused after the stage moved.
        inputs = [{"id": "api", "argv": [PY, "tools/leadinput.py", "{ref}"], "paths": ["gen-api/**"]}]
        adopter = self.adopter({"leadInputs": inputs})
        head = adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        adopter.enqueue("feature", head, lead_inputs=["api"])
        document = self.assertStopped(self.run_queue(adopter, "feature"), "binding_invalid")
        self.assertEqual(document["outcome"], "refused")
        self.assertEqual(adopter.item("feature")["stage"], "stopped")

    def test_a_tracked_or_git_owned_work_root_is_refused(self) -> None:
        for name, work_root, ignore in (
            ("a tracked directory, even when ignored", "tools", "tools/\n"),
            ("the git directory itself", ".git", None),
            ("git's own folder", ".git/worktrees/queue", None),
        ):
            with self.subTest(name):
                self.fresh()
                adopter = self.adopter({"workRoot": work_root})
                if ignore:
                    (adopter.operator / ".gitignore").write_text(ignore)
                precious = adopter.operator / "tools" / "checkouts" / "precious.txt"
                precious.parent.mkdir(parents=True, exist_ok=True)
                precious.write_text("operator's own file\n")
                self.candidate(adopter)
                document = self.assertStopped(self.run_queue(adopter, "feature"), "binding_invalid")
                self.assertEqual(document["outcome"], "refused")
                self.assertTrue(precious.exists())

    def test_invalid_utf8_from_a_tool_is_replaced_not_fatal(self) -> None:
        failing = [PY, "-c", "import sys; sys.stderr.buffer.write(b'bad \\xff\\xfe bytes'); sys.exit(1)"]
        adopter = self.adopter({"regenerable": {"paths": ["gen/**"], "regenerators": [failing]}})
        self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), "sync_step_failed")
        self.assertIn("bad �� bytes", document["stop"]["detail"])

    def test_renaming_a_provider_input_runs_the_candidates_provider(self) -> None:
        adopter = self.adopter()
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        run("checkout", "--quiet", "-b", "feature", "origin/main", cwd=adopter.lane)
        # Only the old path is a provider input, so only rename detection sees it.
        run("mv", "tools/provider-mode.txt", "tools/renamed-mode.txt", cwd=adopter.lane)
        Adopter.write(adopter.lane, "src/app.txt", "app two\n")
        run("add", "--all", cwd=adopter.lane)
        run("commit", "--quiet", "-m", "rename", cwd=adopter.lane)
        run("push", "--quiet", "origin", "feature:feature", cwd=adopter.lane)
        adopter.enqueue("feature", run("rev-parse", "HEAD", cwd=adopter.lane))
        check = self.run_queue(adopter, "feature")[1].document()
        self.assertEqual([provider["source"] for provider in check["selection"]["providers"]], ["base", "candidate"])
        # The provider is told about the rename itself, with its old path.
        runner = adopter.runner()
        changes = runner.changes(check["baseHead"], check["syncedHead"])
        self.assertIn(
            {"path": "tools/renamed-mode.txt", "status": "R", "oldPath": "tools/provider-mode.txt"}, changes,
        )

    def test_a_work_root_inside_the_git_directory_is_accepted(self) -> None:
        adopter = self.adopter({"workRoot": ".git/merge-queue"})
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])

    def test_an_ignored_work_root_inside_the_repository_is_accepted_before_it_exists(self) -> None:
        adopter = self.adopter({"workRoot": ".merge-queue"})
        (adopter.operator / ".gitignore").write_text(".merge-queue/\n")
        self.assertFalse((adopter.operator / ".merge-queue").exists())
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])

    def test_the_command_line_prints_ascii_json_under_a_legacy_code_page(self) -> None:
        # The regenerator fails with UTF-8 bytes no Windows code page holds, so
        # the result's stop detail carries them to stdout.
        failing = [PY, "-c", "import sys; sys.stderr.buffer.write('stuck \\u2028 \\u2603'.encode('utf-8')); sys.exit(1)"]
        adopter = self.adopter({"regenerable": {"paths": ["gen/**"], "regenerators": [failing]}})
        self.candidate(adopter)
        adopter.lead_set("feature", title="Café   line ☃")
        completed = subprocess.run(
            [PY, "-B", str(SCRIPTS / "merge_queue.py"), "--repo", str(adopter.operator), "--state", str(adopter.state),
             "--remote", "origin", "--base", "main", "sync", "feature"],
            capture_output=True, env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        )
        self.assertTrue(completed.stdout, completed.stderr.decode("utf-8", "replace"))
        completed.stdout.decode("ascii")
        document = json.loads(completed.stdout)
        self.assertEqual(document["stop"]["reason"], "sync_step_failed", document)
        self.assertIn("stuck   ☃", document["stop"]["detail"])
        state = json.loads((adopter.state / "state.json").read_bytes().decode("utf-8"))
        self.assertEqual(state["queue"][0]["title"], "Café   line ☃")
        self.assertEqual(state["queue"][0]["stage"], "stopped")
        written = list((adopter.state / "results").glob("*.json"))
        self.assertEqual(len(written), 1)
        raw = written[0].read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertEqual(json.loads(raw.decode("utf-8")), document)

    def test_inherited_git_location_variables_do_not_redirect_the_runner(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        elsewhere = self.directory / "elsewhere"
        run("init", "--quiet", str(elsewhere), cwd=self.directory)
        previous = {key: os.environ.get(key) for key in ("GIT_DIR", "GIT_WORK_TREE")}
        os.environ["GIT_DIR"] = str(elsewhere / ".git")
        os.environ["GIT_WORK_TREE"] = str(elsewhere)
        try:
            results = self.run_queue(adopter, "feature")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])


def case_insensitive_filesystem(directory: Path) -> bool:
    probe = directory / "case-probe"
    probe.write_text("")
    try:
        return (directory / "CASE-PROBE").exists()
    finally:
        probe.unlink()


class WorkRootSafetyTests(MergeQueueTestCase):
    """workRoot is checked against git's own view of the repository, never against where the runner started."""

    def precious(self, checkout: Path) -> Path:
        path = checkout / "tools" / "checkouts" / "precious.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("operator's own file\n")
        return path

    def exclude(self, adopter: Adopter, *patterns: str) -> None:
        exclude = adopter.operator / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("".join(f"{pattern}\n" for pattern in patterns))

    def linked(self, adopter: Adopter, path: Path) -> Path:
        run("worktree", "add", "--quiet", "--detach", str(path), cwd=adopter.operator)
        return path

    def run_from(self, adopter: Adopter, repository: Path, *subjects: str) -> list:
        runner = queue.Runner(repository, adopter.state, remote="origin", base="main")
        return runner.run_command("run", list(subjects))

    def assertRefused(self, results: list, rule: str) -> None:
        document = self.assertStopped(results, "binding_invalid")
        self.assertEqual(document["outcome"], "refused")
        self.assertIn(rule, document["stop"]["detail"])

    def test_a_start_below_the_top_level_is_refused_so_a_relative_work_root_cannot_escape_the_checks(self) -> None:
        # Started in src/, ../tools named the tracked tools/ folder and skipped the tracked-file check.
        adopter = self.adopter({"workRoot": "../tools"})
        precious = self.precious(adopter.operator)
        self.candidate(adopter)
        self.assertRefused(self.run_from(adopter, adopter.operator / "src", "feature"), "top level")
        self.assertTrue(precious.exists())
        self.assertEqual(adopter.item("feature")["stage"], "queued")

    def test_from_a_linked_worktree_the_main_checkout_and_the_common_git_directory_are_protected(self) -> None:
        for name, work_root, rule in (
            ("a tracked folder of the main checkout", "operator/tools", "tracked"),
            ("the main checkout itself", "operator", "contain a checkout"),
            ("git's own folder in the common directory", "operator/.git/objects/q", "git's own folders"),
            # It also holds the linked worktree's private git directory, whichever rule speaks first.
            ("the common git directory itself", "operator/.git", "the git directory"),
        ):
            with self.subTest(name):
                self.fresh()
                adopter = self.adopter({"workRoot": str(self.directory / work_root)})
                linked = self.linked(adopter, self.directory / "linked")
                precious = self.precious(adopter.operator)
                self.candidate(adopter)
                self.assertRefused(self.run_from(adopter, linked, "feature"), rule)
                self.assertTrue(precious.exists())

    def test_a_work_root_holding_another_worktree_is_refused(self) -> None:
        # The runner clears workRoot/checkouts, which here holds a lane's own worktree.
        adopter = self.adopter({"workRoot": ".worktrees"})
        self.exclude(adopter, ".worktrees/")
        lane = self.linked(adopter, adopter.operator / ".worktrees" / "checkouts" / "lane")
        self.candidate(adopter)
        self.assertRefused(self.run_from(adopter, adopter.operator, "feature"), "contain a checkout")
        self.assertTrue((lane / "src" / "app.txt").exists())

    def test_from_a_linked_worktree_an_ignored_work_root_is_accepted(self) -> None:
        adopter = self.adopter({"workRoot": ".merge-queue"})
        self.exclude(adopter, ".worktrees/", ".merge-queue/")
        linked = self.linked(adopter, adopter.operator / ".worktrees" / "lane")
        self.candidate(adopter)
        results = self.run_from(adopter, linked, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertTrue((linked / ".merge-queue" / "checkouts").is_dir())

    def linked_on_branch(self, adopter: Adopter, path: Path) -> Path:
        """A linked worktree on a commit that tracks only-linked/keep.txt, which the main checkout does not."""
        head = adopter.lane_commit("linked-only", {"only-linked/keep.txt": "the linked worktree's own file\n"})
        run("fetch", "--quiet", "origin", cwd=adopter.operator)
        run("worktree", "add", "--quiet", "--detach", str(path), head, cwd=adopter.operator)
        return path

    def test_a_work_root_inside_another_worktree_must_hold_none_of_its_tracked_files(self) -> None:
        # From the main checkout, a folder only the linked worktree tracks is checked against that worktree.
        adopter = self.adopter({"workRoot": str(self.directory / "linked" / "only-linked")})
        linked = self.linked_on_branch(adopter, self.directory / "linked")
        keep = linked / "only-linked" / "keep.txt"
        self.candidate(adopter)
        self.assertRefused(self.run_from(adopter, adopter.operator, "feature"), "tracked")
        self.assertTrue(keep.exists())

    def test_a_work_root_inside_another_worktree_is_accepted_when_that_worktree_ignores_it(self) -> None:
        # Only the linked worktree's own ignore rules name the folder.
        adopter = self.adopter({"workRoot": str(self.directory / "linked" / "queue-work")})
        linked = self.linked(adopter, self.directory / "linked")
        (linked / ".gitignore").write_text("queue-work/\n")
        self.candidate(adopter)
        results = self.run_from(adopter, adopter.operator, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])

    def test_a_work_root_holding_a_separate_git_directory_is_refused(self) -> None:
        adopter = self.adopter({"workRoot": str(self.directory / "gitdirs")})
        separate = self.directory / "separate"
        (self.directory / "gitdirs").mkdir()
        run("clone", "--quiet", "--separate-git-dir", str(self.directory / "gitdirs" / "separate"),
            str(adopter.remote), str(separate), cwd=self.directory)
        self.candidate(adopter)
        # git lists the main worktree of a separate git directory at that directory, so either rule may speak.
        self.assertRefused(self.run_from(adopter, separate, "feature"), "must not contain")
        self.assertTrue((self.directory / "gitdirs" / "separate" / "HEAD").exists())

    def test_a_work_root_spelled_in_another_unicode_normal_form_is_refused(self) -> None:
        composed, decomposed = "café", "café"
        adopter = self.adopter({"workRoot": str(self.directory / decomposed)})
        lane = self.linked(adopter, self.directory / composed / "checkouts" / "lane")
        self.candidate(adopter)
        self.assertRefused(self.run_from(adopter, adopter.operator, "feature"), "contain a checkout")
        self.assertTrue((lane / "src" / "app.txt").exists())

    @unittest.skipIf(os.name == "nt", "a newline cannot be part of a Windows path")
    def test_a_worktree_path_holding_a_newline_is_read_whole(self) -> None:
        adopter = self.adopter()
        lane = self.linked(adopter, adopter.work / "checkouts" / "lane\nname")
        self.candidate(adopter)
        self.assertRefused(self.run_queue(adopter, "feature"), "contain a checkout")
        self.assertTrue((lane / "src" / "app.txt").exists())

    def test_a_work_root_inside_a_submodule_is_refused(self) -> None:
        adopter = self.adopter({"workRoot": "vendor/library/build"})
        library = self.directory / "library"
        Adopter.write(library, "build/checkouts/keep.txt", "the submodule's own file\n")
        run("init", "--quiet", str(library), cwd=self.directory)
        run("add", "--all", cwd=library)
        run("commit", "--quiet", "-m", "library", cwd=library)
        run("-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(library), "vendor/library",
            cwd=adopter.operator)
        # An unanchored superproject rule also matches the folder inside the submodule.
        (adopter.operator / ".gitignore").write_text("build/\n")
        self.candidate(adopter)
        self.assertRefused(self.run_queue(adopter, "feature"), "submodule")
        self.assertTrue((adopter.operator / "vendor" / "library" / "build" / "checkouts" / "keep.txt").exists())

    def test_the_bindings_environment_cannot_move_the_runners_own_git(self) -> None:
        # An empty index would hide the tracked tools/ folder from the tracked-file check.
        empty_index = self.directory / "empty-index"
        adopter = self.environment_adopter({"GIT_INDEX_FILE": str(empty_index)}, {"workRoot": "tools"})
        subprocess.run(["git", "read-tree", "--empty"], cwd=adopter.operator, check=True,
                       env={**os.environ, "GIT_INDEX_FILE": str(empty_index)})
        self.assertTrue(empty_index.exists())
        (adopter.operator / ".gitignore").write_text("tools/\n")
        # Loading alone decides; a full run would also stop later, in a checkout that index does not describe.
        with self.assertRaises(queue.Stop) as stopped:
            adopter.runner().load()
        self.assertEqual(stopped.exception.reason, "binding_invalid")
        self.assertIn("tracked", stopped.exception.detail)
        self.assertTrue((adopter.operator / "tools" / "check.py").exists())

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_symlinked_runner_folder_under_work_root_is_refused(self) -> None:
        for name in ("empty-hooks", "checkouts"):
            with self.subTest(name):
                self.fresh()
                adopter = self.adopter({"workRoot": ".merge-queue"})
                (adopter.operator / ".gitignore").write_text(".merge-queue/\n")
                (adopter.operator / ".merge-queue").mkdir()
                (adopter.operator / ".merge-queue" / name).symlink_to(adopter.operator / "tools")
                self.candidate(adopter)
                self.assertRefused(self.run_queue(adopter, "feature"), "symlink")
                self.assertTrue((adopter.operator / "tools" / "check.py").exists())

    def test_a_worktree_inside_the_git_directory_keeps_its_tracked_files(self) -> None:
        adopter = self.adopter({"workRoot": ".git/lanes/lane/tools"})
        lane = self.linked(adopter, adopter.operator / ".git" / "lanes" / "lane")
        precious = self.precious(lane)
        self.candidate(adopter)
        self.assertRefused(self.run_queue(adopter, "feature"), "tracked")
        self.assertTrue(precious.exists())

    def test_the_runners_own_leftover_checkouts_do_not_block_the_next_run(self) -> None:
        adopter = self.adopter({"workRoot": ".merge-queue"})
        self.exclude(adopter, ".merge-queue/")
        self.candidate(adopter)
        # A killed run leaves its registered checkout behind under workRoot/checkouts.
        runner = adopter.runner()
        base_head = runner.load()
        runner.start()
        leftover = runner.checkout(base_head, "base", prepare=False)
        self.assertTrue(leftover.exists())
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertFalse(leftover.exists())

    def test_git_own_folders_are_protected_without_case_and_include_reftable_and_in_progress_state(self) -> None:
        for work_root in (
            ".git/Objects/q", ".git/REFS/q", ".git/reftable/q", ".git/branches/q", ".git/remotes/q",
            ".git/rebase-merge/q", ".git/rebase-apply/q", ".git/sequencer/q",
        ):
            with self.subTest(work_root):
                self.fresh()
                adopter = self.adopter({"workRoot": work_root})
                self.candidate(adopter)
                self.assertRefused(self.run_queue(adopter, "feature"), "git's own folders")

    def test_a_case_variant_of_a_tracked_folder_is_refused(self) -> None:
        if not case_insensitive_filesystem(self.directory):
            self.skipTest("the filesystem distinguishes case")
        adopter = self.adopter({"workRoot": "TOOLS"})
        (adopter.operator / ".gitignore").write_text("TOOLS/\ntools/\n")
        precious = self.precious(adopter.operator)
        self.candidate(adopter)
        self.assertRefused(self.run_queue(adopter, "feature"), "tracked")
        self.assertTrue(precious.exists())

    def test_pathspec_settings_in_the_bindings_environment_are_refused(self) -> None:
        # They change how git reads every path the workRoot checks name.
        # Windows reads environment names without case, so a case variant is refused too.
        for variable in ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS",
                         "Git_Literal_Pathspecs"):
            with self.subTest(variable):
                self.fresh()
                adopter = self.environment_adopter({variable: "1"}, {"workRoot": ".merge-queue"})
                self.exclude(adopter, ".merge-queue/")
                with self.assertRaises(queue.Stop) as stopped:
                    adopter.runner().load()
                self.assertEqual(stopped.exception.reason, "binding_invalid")
                self.assertIn(variable, stopped.exception.detail)
                self.assertIn("pathspec", stopped.exception.detail)

    def test_the_operators_pathspec_settings_never_reach_the_runners_own_git(self) -> None:
        # Literal pathspecs would turn the ":(icase)" tracked-file check into a
        # search for a file literally named so, and let a tracked folder through.
        adopter = self.adopter({"workRoot": "tools"})
        (adopter.operator / ".gitignore").write_text("tools/\n")
        with mock.patch.dict(os.environ, {"GIT_LITERAL_PATHSPECS": "1", "GIT_ICASE_PATHSPECS": "0"}):
            with self.assertRaises(queue.Stop) as stopped:
                adopter.runner().load()
        self.assertEqual(stopped.exception.reason, "binding_invalid")
        self.assertIn("tracked", stopped.exception.detail)
        self.assertTrue((adopter.operator / "tools" / "check.py").exists())

    def test_the_runners_own_git_keeps_the_operators_home_and_global_config(self) -> None:
        adopter = self.adopter()
        runner = adopter.runner()
        runner.binding = {"environment": {
            "HOME": str(self.directory / "binding-home"), "XDG_CONFIG_HOME": str(self.directory / "binding-xdg"),
            "GIT_CONFIG_GLOBAL": str(self.directory / "binding-global"), "GIT_CONFIG_SYSTEM": str(self.directory / "binding-system"),
            "GIT_CONFIG_NOSYSTEM": "1", "USERPROFILE": str(self.directory / "binding-profile"),
        }}
        seen: dict[str, dict] = {}
        original = queue.run_process

        def capture(argv, cwd, env, **options):
            seen["env"] = env
            return original(["git", "--version"], cwd=cwd, env=env)

        operator = {"HOME": str(self.directory / "operator-home"), "GIT_CONFIG_GLOBAL": str(self.directory / "operator-global")}
        with mock.patch.dict(os.environ, operator), mock.patch.object(queue, "run_process", side_effect=capture):
            runner.git("status")
        for name in ("HOME", "GIT_CONFIG_GLOBAL"):
            self.assertEqual(seen["env"].get(name), operator[name], name)
        for name in ("XDG_CONFIG_HOME", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "USERPROFILE"):
            self.assertEqual(seen["env"].get(name), os.environ.get(name), name)
        # On Windows a case variant names the same variable, and is dropped as well.
        runner.binding = {"environment": {
            "Home": str(self.directory / "binding-home"), "UserProfile": str(self.directory / "binding-profile"),
            "Git_Dir": str(self.directory / "elsewhere"),
        }}
        with mock.patch.object(queue, "WINDOWS", True), mock.patch.dict(os.environ, operator), \
                mock.patch.object(queue, "run_process", side_effect=capture):
            runner.git("status")
        self.assertFalse({"Home", "UserProfile", "Git_Dir"} & set(seen["env"]))
        self.assertEqual(seen["env"]["HOME"], operator["HOME"])
        runner.binding = {"environment": {
            "HOME": str(self.directory / "binding-home"), "GIT_CONFIG_GLOBAL": str(self.directory / "binding-global"),
        }}
        # A declared argv still runs with the binding's own values.
        declared = runner.environment()
        self.assertEqual(declared["HOME"], str(self.directory / "binding-home"))
        self.assertEqual(declared["GIT_CONFIG_GLOBAL"], str(self.directory / "binding-global"))

    def test_a_windows_junction_counts_as_a_link(self) -> None:
        folder = self.directory / "plain"
        folder.mkdir()
        self.assertFalse(queue.is_link(folder))
        # A junction is a mount-point reparse point that is not a symlink.
        def reparse(tag: int) -> object:
            return type("Info", (), {"st_file_attributes": 0x410, "st_mode": 0o040755, "st_reparse_tag": tag})()

        with mock.patch.object(queue.os, "lstat", return_value=reparse(0xA0000003)):
            self.assertTrue(queue.is_link(folder))
        # A cloud placeholder or deduplicated folder is a reparse point that leads nowhere else.
        for tag in (0x9000001A, 0x80000013):
            with mock.patch.object(queue.os, "lstat", return_value=reparse(tag)):
                self.assertFalse(queue.is_link(folder), hex(tag))
        self.assertFalse(queue.is_link(self.directory / "missing"))

    def test_a_junction_at_a_runner_folder_under_work_root_is_refused(self) -> None:
        adopter = self.adopter({"workRoot": ".merge-queue"})
        (adopter.operator / ".gitignore").write_text(".merge-queue/\n")
        (adopter.operator / ".merge-queue" / "empty-hooks").mkdir(parents=True)
        real = queue.is_link
        with mock.patch.object(queue, "is_link", side_effect=lambda path: path.name == "empty-hooks" or real(path)):
            with self.assertRaises(queue.Stop) as stopped:
                adopter.runner().load()
        self.assertEqual(stopped.exception.reason, "binding_invalid")
        self.assertIn("empty-hooks", stopped.exception.detail)
        self.assertIn("junction", stopped.exception.detail)

    def test_a_case_variant_of_a_submodule_path_is_refused(self) -> None:
        if not case_insensitive_filesystem(self.directory):
            self.skipTest("the filesystem distinguishes case")
        adopter = self.adopter({"workRoot": "VENDOR/Library/build"})
        library = self.directory / "library"
        Adopter.write(library, "build/checkouts/keep.txt", "the submodule's own file\n")
        run("init", "--quiet", str(library), cwd=self.directory)
        run("add", "--all", cwd=library)
        run("commit", "--quiet", "-m", "library", cwd=library)
        run("-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(library), "vendor/library",
            cwd=adopter.operator)
        (adopter.operator / ".gitignore").write_text("build/\n")
        self.candidate(adopter)
        self.assertRefused(self.run_queue(adopter, "feature"), "submodule")
        self.assertTrue((adopter.operator / "vendor" / "library" / "build" / "checkouts" / "keep.txt").exists())

    def test_the_submodule_lookup_matches_a_listed_gitlink_without_case(self) -> None:
        runner = self.adopter().runner()
        listed = "160000 0123456789abcdef0123456789abcdef01234567 0\tVendor/Library\0"
        found = queue.Completed(0, listed, "", False, 0.0)
        with mock.patch.object(runner, "git", return_value=found):
            self.assertEqual(runner.submodule_at(runner.repository, ["vendor", "library", "build"]), "Vendor/Library")
            # A listed gitlink that is not one of the asked paths is not reported.
            self.assertIsNone(runner.submodule_at(runner.repository, ["vendor", "other"]))

    def test_a_leftover_checkout_removed_between_runs_does_not_block_the_next(self) -> None:
        adopter = self.adopter({"workRoot": ".merge-queue"})
        self.exclude(adopter, ".merge-queue/")
        self.candidate(adopter)
        runner = adopter.runner()
        base_head = runner.load()
        runner.start()
        leftover = runner.checkout(base_head, "base", prepare=False)
        # Someone removed the folder by hand; git still lists it, now prunable.
        shutil.rmtree(leftover)
        self.assertTrue(any(leftover.as_posix() in line for line in
                            run("worktree", "list", "--porcelain", cwd=adopter.operator).splitlines()))
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertFalse(any(leftover.as_posix() in line for line in
                             run("worktree", "list", "--porcelain", cwd=adopter.operator).splitlines()))

    def test_the_schema_states_every_work_root_rule(self) -> None:
        description = schema("orchestration-binding")["properties"]["workRoot"]["description"]
        for phrase in (
            "top level", "any checkout of the repository", "git's own folders", "tracked", "ignored", "submodule", "junction",
            "marks", "symlink",
        ):
            self.assertIn(phrase, description)


class HostileConfigTests(MergeQueueTestCase):
    """The runner's own git commands keep working under a user's global config."""

    def hostile(self) -> contextlib.AbstractContextManager:
        """A system config that rewrites line endings, and a global one that signs and ignores files."""
        signer = self.directory / "refuse-to-sign.py"
        signer.write_text("import sys\nsys.exit(1)\n")
        excludes = self.directory / "global-excludes"
        excludes.write_text("stray.txt\nnotes/\n")
        # Git for Windows sets autocrlf in its system config by default.
        system = self.directory / "system.gitconfig"
        system.write_text("[core]\n\tautocrlf = true\n")
        attributes = self.directory / "global-attributes"
        attributes.write_text("* text eol=crlf\n")
        config = self.directory / "hostile.gitconfig"
        lines = [
            "[core]", "\tsafecrlf = true", f"\texcludesFile = {excludes.as_posix()}",
            f"\tattributesFile = {attributes.as_posix()}",
            "[diff]", "\trenames = false",
            "[commit]", "\tgpgsign = true",
            "[tag]", "\tgpgsign = true",
            "[push]", "\tgpgSign = true",
            "[merge]", "\tverifySignatures = true", "\trenames = false", "\tdirectoryRenames = true",
            "[gpg]", f"\tprogram = {Path(PY).as_posix()} {signer.as_posix()}",
            "[rerere]", "\tenabled = true", "\tautoUpdate = true",
        ]
        config.write_text("\n".join(lines) + "\n")
        return mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_SYSTEM": str(system)})

    def test_a_run_with_a_regenerate_and_a_merge_commit_completes(self) -> None:
        adopter = self.adopter()
        # A stale generated file forces a regenerate commit; a moved base forces a merge commit.
        self.candidate(adopter, {"src/app.txt": "app two\r\nwith a windows line\r\n"})
        adopter.advance_base({"src/other.txt": "other\n"})
        with self.hostile():
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        adopter.show("origin/main", "src/other.txt")
        generated = subprocess.run(
            ["git", "show", "origin/main:gen/generated.txt"], cwd=adopter.lane, capture_output=True, check=True,
        ).stdout
        # The windows line endings the lane committed reach the generated file untouched.
        self.assertEqual(generated, b"app two\r\nwith a windows line\r\nlib one\nother\n")

    def test_the_excludes_file_is_pinned_to_a_value_git_accepts_on_every_system(self) -> None:
        # Windows' null device makes `git status` die with "cannot use nul as an exclude file".
        self.assertIn("core.excludesFile=", queue.PINNED_GIT_CONFIG)
        self.assertNotIn(f"core.excludesFile={os.devnull}", queue.PINNED_GIT_CONFIG)

    def test_a_globally_ignored_file_is_still_an_undeclared_change(self) -> None:
        adopter = self.adopter({"postSync": [{"argv": [PY, "tools/fingerprint.py", "{baseHead}", "stray.txt"], "paths": ["notes/fingerprint.txt"]}]})
        head = self.candidate(adopter)
        with self.hostile():
            document = self.assertStopped(self.run_queue(adopter, "feature"), "undeclared_change")
        self.assertEqual(document["stop"]["paths"], ["stray.txt"])
        self.assertEqual(adopter.tip("feature"), head)

    def test_the_repositorys_own_attributes_still_apply(self) -> None:
        adopter = self.adopter()
        # The repository marks the generated file as text, so the runner's
        # regenerate commit normalises its CRLF lines to LF. Without the
        # attribute the same bytes are committed as written (the first test).
        self.candidate(adopter, {".gitattributes": "gen/generated.txt text\n", "src/app.txt": "app two\r\n"})
        with self.hostile():
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        adopter.show("origin/main", ".gitattributes")
        generated = subprocess.run(
            ["git", "show", "origin/main:gen/generated.txt"], cwd=adopter.lane, capture_output=True, check=True,
        ).stdout
        self.assertEqual(generated, b"app two\nlib one\n")

    def test_a_rename_against_a_base_edit_still_merges(self) -> None:
        adopter = self.adopter()
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        run("checkout", "--quiet", "-b", "feature", "origin/main", cwd=adopter.lane)
        run("mv", "src/lib.txt", "src/library.txt", cwd=adopter.lane)
        run("commit", "--quiet", "-m", "rename", cwd=adopter.lane)
        run("push", "--quiet", "origin", "feature:feature", cwd=adopter.lane)
        adopter.enqueue("feature", run("rev-parse", "HEAD", cwd=adopter.lane))
        adopter.advance_base({"src/lib.txt": "lib edited on the base\n"})
        with self.hostile():
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertEqual(adopter.show("origin/main", "src/library.txt"), "lib edited on the base")

    def test_a_renamed_directory_never_silently_moves_a_reviewed_file(self) -> None:
        adopter = self.adopter()
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        run("checkout", "--quiet", "-B", "base-work", "origin/main", cwd=adopter.lane)
        Adopter.write(adopter.lane, "lib/a.txt", "".join(f"a line {i}\n" for i in range(30)))
        Adopter.write(adopter.lane, "lib/b.txt", "".join(f"b line {i}\n" for i in range(30)))
        run("add", "--all", cwd=adopter.lane)
        run("commit", "--quiet", "-m", "lib", cwd=adopter.lane)
        run("push", "--quiet", "origin", "base-work:main", cwd=adopter.lane)
        # The candidate adds a file in lib/; the base then renames lib/ to library/.
        run("checkout", "--quiet", "-b", "feature", "base-work", cwd=adopter.lane)
        Adopter.write(adopter.lane, "lib/extra.txt", "extra\n")
        Adopter.write(adopter.lane, "src/app.txt", "app two\n")
        run("add", "--all", cwd=adopter.lane)
        run("commit", "--quiet", "-m", "lane", cwd=adopter.lane)
        run("push", "--quiet", "origin", "feature:feature", cwd=adopter.lane)
        head = run("rev-parse", "HEAD", cwd=adopter.lane)
        adopter.enqueue("feature", head)
        run("checkout", "--quiet", "base-work", cwd=adopter.lane)
        run("mv", "lib", "library", cwd=adopter.lane)
        run("commit", "--quiet", "-m", "rename dir", cwd=adopter.lane)
        run("push", "--quiet", "origin", "base-work:main", cwd=adopter.lane)
        with self.hostile():
            document = self.assertStopped(self.run_queue(adopter, "feature"), "source_conflict")
        # Git names the conflict where the rename would have put the file.
        self.assertEqual(document["stop"]["paths"], ["library/extra.txt"])
        self.assertEqual(adopter.tip("feature"), head)

    def test_a_recorded_resolution_never_resolves_a_conflict_for_the_runner(self) -> None:
        adopter = self.adopter()
        head = adopter.lane_commit("feature", {"src/lib.txt": "lane lib\n"})
        adopter.enqueue("feature", head)
        adopter.advance_base({"src/lib.txt": "base lib\n"})
        # The operator's repository holds a resolution recorded for exactly this conflict.
        operator = adopter.operator
        run("config", "rerere.enabled", "true", cwd=operator)
        run("fetch", "--quiet", "origin", cwd=operator)
        run("checkout", "--quiet", "-B", "record", "origin/feature", cwd=operator)
        run("merge", "--no-edit", "origin/main", cwd=operator, check=False)
        Adopter.write(operator, "src/lib.txt", "resolved by an old recording\n")
        run("add", "src/lib.txt", cwd=operator)
        run("commit", "--quiet", "--no-edit", cwd=operator)
        run("checkout", "--quiet", "main", cwd=operator)
        run("config", "--unset", "rerere.enabled", cwd=operator)
        with self.hostile():
            document = self.assertStopped(self.run_queue(adopter, "feature"), "source_conflict")
        self.assertEqual(document["stop"]["paths"], ["src/lib.txt"])
        self.assertEqual(adopter.tip("feature"), head)

    def test_a_push_and_merge_survive_signature_requirements(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        # A base merge is needed, so the runner merges unsigned base commits under verifySignatures.
        adopter.advance_base({"src/other.txt": "other\n"})
        with self.hostile():
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])


@unittest.skipIf(os.name == "nt", "the stub nodes are shell scripts")
class CheckNodeTests(MergeQueueTestCase):
    """Each check the runner runs records the Node first on the PATH it ran with, after its profile's environment, and
    marks one below the runtime's nodeMinimum without changing the check's own verdict."""

    def stub_node(self, version: str) -> str:
        folder = self.directory / f"node-{version}"
        folder.mkdir()
        (folder / "node").write_text(f"#!/bin/sh\necho v{version}\n")
        (folder / "node").chmod(0o755)
        return str(folder)

    def runtime(self, minimum: str | None) -> Path:
        """A runtime descriptor declaring this nodeMinimum, or none at all."""
        path = self.directory / "runtime" / "runtime.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"nodeMinimum": minimum} if minimum else {"runtimeVersion": "1"}))
        return path

    def run_checks(self, minimum: str | None) -> tuple[dict, dict]:
        """The result's and the queue row's check entries, by id, from one run whose checks put an old Node, a new
        Node or none first on their PATH; one of the old Node's checks fails on its own."""
        old, new = self.stub_node("20.0.0"), self.stub_node("22.6.0")
        empty = self.directory / "no-node"
        empty.mkdir()
        inherited = os.environ.get("PATH", "")
        profiles = {"default": {"environment": {"MQ_PROFILE_HEAD": "{head}"}},
                    "old": {"environment": {"PATH": f"{old}{os.pathsep}{inherited}"}},
                    "new": {"environment": {"PATH": f"{new}{os.pathsep}{inherited}"}},
                    "none": {"environment": {"PATH": str(empty)}}}
        always = [check_entry("old", environmentProfile="old"), check_entry("new", environmentProfile="new"),
                  check_entry("none", environmentProfile="none"),
                  check_entry("old-fails", "--always-fail", environmentProfile="old", prerequisites=["old", "new", "none"])]
        adopter = self.adopter({"profiles": profiles, "alwaysRun": always})
        self.candidate(adopter)
        with mock.patch.object(queue, "RUNTIME_DESCRIPTOR", self.runtime(minimum)):
            results = self.run_queue(adopter, "feature")
        checked = next(result.document() for result in results if result.document()["checks"])
        for document in (result.document() for result in results):
            self.assertEqual(list(Draft202012Validator(schema("merge-queue-result")).iter_errors(document)), [])
        rows = {row["id"]: row for row in adopter.item("feature")["checks"]}
        return {entry["id"]: entry for entry in checked["checks"]}, rows

    def test_each_check_records_the_node_its_profile_puts_first_and_marks_one_below_the_minimum(self) -> None:
        entries, rows = self.run_checks("22.6.0")
        own = shutil.which("node")
        for found in (entries, rows):
            self.assertEqual(found["old"]["node"], "20.0.0", found["old"])
            self.assertIs(found["old"]["nodeBelowMinimum"], True)
            self.assertEqual(found["new"]["node"], "22.6.0")
            self.assertNotIn("nodeBelowMinimum", found["new"])
            self.assertIsNone(found["none"]["node"], found["none"])
            self.assertNotIn("nodeBelowMinimum", found["none"])
            # The mark explains; the verdict is the check's own.
            self.assertEqual(found["old"]["state"], "passed")
            self.assertEqual(found["old-fails"]["state"], "failed")
            self.assertIs(found["old-fails"]["nodeBelowMinimum"], True)
        # The queue's own Node is not what the profile's check recorded.
        if own:
            self.assertNotEqual(subprocess.run([own, "--version"], capture_output=True, text=True).stdout.strip(), "v20.0.0")

    def test_without_a_minimum_no_check_is_marked(self) -> None:
        entries, rows = self.run_checks(None)
        for found in (entries, rows):
            self.assertEqual((found["old"]["node"], found["new"]["node"], found["none"]["node"]), ("20.0.0", "22.6.0", None))
            self.assertEqual([check for check, entry in found.items() if "nodeBelowMinimum" in entry], [])
            self.assertEqual(found["old-fails"]["state"], "failed")

    def test_a_node_is_asked_its_version_in_the_folder_the_check_ran_in(self) -> None:
        """A version manager's shim picks its Node by the folder it runs in: here a pin the candidate adds."""
        shim = self.directory / "shim"
        shim.mkdir()
        (shim / "node").write_text("#!/bin/sh\nif [ -f .node-pin ]; then echo v$(cat .node-pin); else echo v22.6.0; fi\n")
        (shim / "node").chmod(0o755)
        profiles = {"default": {"environment": {"MQ_PROFILE_HEAD": "{head}"}},
                    "shim": {"environment": {"PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '')}"}}}
        adopter = self.adopter({"profiles": profiles, "alwaysRun": [check_entry("pinned", environmentProfile="shim")]})
        self.candidate(adopter, {"src/app.txt": "app two\n", ".node-pin": "20.1.0\n"})
        with mock.patch.object(queue, "RUNTIME_DESCRIPTOR", self.runtime("22.6.0")):
            self.run_queue(adopter, "feature")
        row = {row["id"]: row for row in adopter.item("feature")["checks"]}["pinned"]
        self.assertEqual((row["node"], row.get("nodeBelowMinimum")), ("20.1.0", True), row)

    def test_a_number_compares_number_by_number(self) -> None:
        self.assertEqual(queue.node_fields("100.0.0", (9, 0, 0)), {"node": "100.0.0"})
        self.assertEqual(queue.node_fields("9.0.0", (9, 0, 0)), {"node": "9.0.0"})
        self.assertEqual(queue.node_fields("8.99.1", (9, 0, 0)), {"node": "8.99.1", "nodeBelowMinimum": True})
        self.assertEqual(queue.node_fields(None, (9, 0, 0)), {"node": None})


class LiveCheckStateTests(MergeQueueTestCase):
    """While checks run, the checking panel and the queue row name them, live, and agree."""

    def snapshot_adopter(self, mode: str, binding_changes: dict) -> Adopter:
        environment = {
            "MQ_REMOTE": str(self.directory / "remote.git"), "MQ_LOG": str(self.directory / "log.txt"),
            "MQ_STATE": str(self.directory / "state"),
        }
        return Adopter(self.directory, {"environment": environment, **binding_changes}, mode)

    def snapshots(self, adopter: Adopter) -> list[dict]:
        path = Path(str(adopter.log) + ".snap")
        return [json.loads(line) for line in path.read_text().splitlines()]

    @staticmethod
    def shape(entries: list[dict]) -> list[dict]:
        """Steps or checks without their times, failures or logs: the states these tests pin."""
        return [{key: entry[key] for key in ("label", "id", "state") if key in entry} for entry in entries]

    @staticmethod
    def as_steps(states: dict[str, str]) -> list[dict]:
        return [{"label": name, "state": state} for name, state in states.items()]

    @staticmethod
    def as_checks(states: dict[str, str]) -> list[dict]:
        return [{"id": name, "state": state} for name, state in states.items()]

    def test_a_fallback_check_reads_running_in_both_views_while_it_runs(self) -> None:
        snapshot_fallback = check_entry("full", "--snapshot")
        adopter = self.snapshot_adopter("fail", {"fallback": [snapshot_fallback], "alwaysRun": []})
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertEqual(results[1].document()["selection"]["fallbackReasons"], ["provider_failed"])
        [seen] = self.snapshots(adopter)
        self.assertEqual(self.shape(seen["steps"]), self.as_steps({"full": "running"}))
        self.assertEqual(self.shape(seen["queue"]), self.as_checks({"full": "running"}))

    def test_a_lead_write_to_another_item_during_a_check_is_kept(self) -> None:
        lead_writes = check_entry("lead-writes", "--lead-writes")
        adopter = self.snapshot_adopter("fail", {"fallback": [lead_writes], "alwaysRun": [check_entry("after")]})
        self.candidate(adopter)
        adopter.enqueue("other", adopter.tip("main"))
        with mock.patch.dict(os.environ, {"MQ_SCRIPTS": str(SCRIPTS)}):
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertEqual(adopter.item("other")["title"], "Renamed by the lead")
        self.assertEqual(self.shape(adopter.item("feature")["checks"]), self.as_checks({"after": "passed", "lead-writes": "passed"}))

    def recording(self, subject: str = "feature"):
        """Wrap the state tool: after every runner write, record the panel's steps and the queue row."""
        real = state_tool.patch
        seen: list[tuple] = []

        def wrapper(directory, **kwargs):
            written = real(directory, **kwargs)
            if kwargs.get("writer") == "runner":
                now = written["meta"]["checking"]["now"]
                item = next(element for element in written["queue"] if element["subject"] == subject)
                steps = None if now is None else [(step["label"], step["state"]) for step in now["steps"]]
                seen.append((steps, [(check["id"], check["state"]) for check in item["checks"]], item["stage"]))
            return written

        return mock.patch.object(queue.delivery_state, "patch", wrapper), seen

    def live(self, seen: list[tuple]) -> list[tuple]:
        """The writes made while checks were running, each asserted to show the same states in both views."""
        writes = [
            (steps, checks) for steps, checks, stage in seen
            if stage == "checking" and steps is not None and steps != [("selecting checks", "running")]
        ]
        self.assertTrue(writes, seen)
        for steps, checks in writes:
            self.assertEqual(steps, checks, seen)
        return writes

    def test_both_views_agree_after_every_runner_write(self) -> None:
        adopter = self.snapshot_adopter("trio", {"alwaysRun": []})
        self.candidate(adopter)
        patched, seen = self.recording()
        with patched:
            self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        writes = self.live(seen)
        # Pending, then each check running and then finished, with d skipped, then the history.
        self.assertEqual(len(writes), 9)

    @unittest.skipIf(os.name == "nt", "process groups and ps are POSIX")
    def test_a_timed_out_check_reads_timed_out_in_both_views(self) -> None:
        adopter = self.adopter(mode="slow-child")
        self.candidate(adopter)
        patched, seen = self.recording()
        with patched:
            self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        states = [steps[0] for steps, _ in self.live(seen)]
        self.assertIn(("slow", "running"), states)
        self.assertIn(("slow", "timed_out"), states)
        self.assertEqual(self.shape(adopter.item("feature")["checks"])[0], {"id": "slow", "state": "timed_out"})

    def test_a_retry_rebuilds_from_fresh_state_and_keeps_the_leads_write(self) -> None:
        adopter = self.snapshot_adopter("trio", {"alwaysRun": []})
        self.candidate(adopter)
        adopter.enqueue("other", adopter.tip("main"))
        real = state_tool.patch
        fired: list[int] = []

        def lead_writes_first(directory, **kwargs):
            # The lead writes between the runner's read and its write, so the runner must retry.
            steps = (kwargs.get("assignments") or {}).get("/meta/checking/now/steps")
            if kwargs.get("writer") == "runner" and steps and any(step["state"] == "running" for step in steps) and not fired:
                fired.append(1)
                return real(directory, between_read_and_write=lambda: adopter.lead_set("other", title="Renamed mid-write"), **kwargs)
            return real(directory, **kwargs)

        with mock.patch.object(queue.delivery_state, "patch", lead_writes_first):
            self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertTrue(fired)
        self.assertEqual(adopter.item("other")["title"], "Renamed mid-write")
        self.assertEqual([check["state"] for check in adopter.item("feature")["checks"]], ["passed", "failed", "passed", "skipped"])

    def test_a_stop_mid_check_leaves_no_check_reading_running(self) -> None:
        adopter = self.snapshot_adopter("trio", {"alwaysRun": []})
        self.candidate(adopter)
        real = queue.Runner.run_one

        def crash_on_b(runner, check, checkout, context):
            if check["id"] == "b":
                raise RuntimeError("simulated crash mid-check")
            return real(runner, check, checkout, context)

        with mock.patch.object(queue.Runner, "run_one", crash_on_b):
            self.assertStopped(self.run_queue(adopter, "feature"), "runner_error")
        state = state_tool.read_state(adopter.state)
        self.assertIsNone(state["meta"]["checking"]["now"])
        self.assertEqual(adopter.item("feature")["stage"], "stopped")
        self.assertEqual(
            self.shape(adopter.item("feature")["checks"]), self.as_checks({"a": "passed", "b": "pending", "c": "pending", "d": "pending"}),
        )

    def test_steps_advance_in_order_and_a_failure_is_recorded_in_both_views(self) -> None:
        adopter = self.snapshot_adopter("trio", {"alwaysRun": []})
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertStopped(results, "check_failed")
        seen = {snapshot["check"]: snapshot for snapshot in self.snapshots(adopter)}
        expected = {
            "a": {"a": "running", "b": "pending", "c": "pending", "d": "pending"},
            "b": {"a": "passed", "b": "running", "c": "pending", "d": "pending"},
            "c": {"a": "passed", "b": "failed", "c": "running", "d": "pending"},
        }
        self.assertEqual(sorted(seen), ["a", "b", "c"])
        for name, states in expected.items():
            with self.subTest(check=name):
                self.assertEqual(self.shape(seen[name]["steps"]), self.as_steps(states))
                self.assertEqual(self.shape(seen[name]["queue"]), self.as_checks(states))
        # After the run the queue keeps every check's final state; d never ran because b failed.
        final = {"a": "passed", "b": "failed", "c": "passed", "d": "skipped"}
        self.assertEqual(self.shape(adopter.item("feature")["checks"]), self.as_checks(final))

    def test_a_finished_check_never_reads_running_again_and_one_check_runs_at_a_time(self) -> None:
        adopter = self.snapshot_adopter("trio", {"alwaysRun": []})
        self.candidate(adopter)
        patched, seen = self.recording()
        with patched:
            self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        finished: set[str] = set()
        for steps, checks, _ in seen:
            running = [name for name, state in checks if state == "running"]
            self.assertLessEqual(len(running), 1, seen)
            self.assertFalse(finished & set(running), seen)
            finished |= {name for name, state in checks if state not in ("pending", "running")}
        self.assertEqual(finished, {"a", "b", "c", "d"})


class BatchTests(MergeQueueTestCase):
    """Several candidates are checked once as one combined tree, then land in order, each tree-verified."""

    def trio(self, adopter: Adopter, failing: str | None = None) -> list[str]:
        """Three candidates touching different files; the provider's unit check scans src/app.txt."""
        files = {
            "first": {"src/lib.txt": "lib two\n"},
            "second": {"src/app.txt": "FAIL\n" if failing == "second" else "app two\n"},
            "third": {"src/extra.txt": "FAIL\n" if failing == "third" else "extra\n"},
        }
        if failing == "third":
            # The unit check scans src/app.txt, so the third candidate fails a check of its own.
            files["third"]["tests/test_third.txt"] = "FAIL\n"
        for name, changes in files.items():
            self.candidate(adopter, changes, name=name)
        return list(files)

    def batch(self, adopter: Adopter, *subjects: str, **options: object) -> list:
        return adopter.runner(**options).run_command("batch", list(subjects))

    def landed_trees(self, adopter: Adopter) -> list[str]:
        """The tree of each first-parent commit the base gained, oldest first."""
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        commits = run("rev-list", "--first-parent", "--reverse", "origin/main", cwd=adopter.lane).split()
        return [run("rev-parse", f"{commit}^{{tree}}", cwd=adopter.lane) for commit in commits]

    def check_runs(self, adopter: Adopter, name: str) -> int:
        return sum(1 for line in adopter.log_lines() if line.split()[:2] == ["check", name])

    def outcomes(self, results: list) -> dict[str, str]:
        return {result.document()["candidate"]: result.document()["outcome"] for result in results}

    def test_a_green_batch_runs_its_checks_once_and_lands_every_member_tree_verified(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)
        before = len(self.landed_trees(adopter))
        results = self.batch(adopter, *subjects)
        self.assertEqual(self.outcomes(results), {name: "merged" for name in subjects})
        self.assertEqual(self.check_runs(adopter, "always"), 1)
        self.assertEqual(self.check_runs(adopter, "unit"), 1)
        for name in subjects:
            self.assertEqual(adopter.item(name)["stage"], "merged")
        # Each landing is a merge whose tree is the tree recorded for that step of the build.
        landed = self.landed_trees(adopter)[before:]
        self.assertEqual(len(landed), 3)
        recorded = [result.document()["syncedHead"] for result in results]
        self.assertEqual(landed, [run("rev-parse", f"{head}^{{tree}}", cwd=adopter.lane) for head in recorded])
        final = adopter.tip("main")
        self.assertEqual(adopter.show(final, "src/lib.txt"), "lib two")
        self.assertEqual(adopter.show(final, "src/app.txt"), "app two")
        self.assertEqual(adopter.show(final, "src/extra.txt"), "extra")
        # The regenerated file covers all three members, as the combined tree was checked.
        self.assertEqual(adopter.show(final, "gen/generated.txt"), "app two\nextra\nlib two")
        kinds = [(entry["kind"], entry.get("subject")) for entry in state_tool.read_journal(adopter.state)]
        self.assertEqual([subject for kind, subject in kinds if kind == "candidate.merged"], subjects)

    def test_a_failing_member_is_found_stopped_and_the_others_still_land(self) -> None:
        for search in ("bisect", "serial"):
            with self.subTest(search):
                self.fresh()
                adopter = self.adopter({"batch": {"culpritSearch": search}})
                subjects = self.trio(adopter, failing="second")
                results = self.batch(adopter, *subjects)
                documents = [result.document() for result in results]
                final = {document["candidate"]: document for document in documents}
                self.assertEqual(final["second"]["outcome"], "stopped")
                self.assertEqual(final["second"]["stop"]["reason"], "check_failed")
                self.assertEqual(adopter.item("first")["stage"], "merged")
                self.assertEqual(adopter.item("second")["stage"], "stopped")
                self.assertEqual(adopter.item("third")["stage"], "merged")
                main = adopter.tip("main")
                self.assertEqual(adopter.show(main, "src/app.txt"), "app one")
                self.assertEqual(adopter.show(main, "src/extra.txt"), "extra")
                culprit = [entry for entry in state_tool.read_journal(adopter.state)
                           if entry["kind"] == "candidate.stopped" and entry.get("subject") == "second"]
                self.assertTrue(culprit and "culprit" in culprit[-1]["summary"], culprit)

    def test_bisection_checks_fewer_prefixes_than_the_serial_search(self) -> None:
        runs = {}
        for search in ("bisect", "serial"):
            self.fresh()
            adopter = self.adopter({"batch": {"culpritSearch": search}})
            for name, changes in (
                ("one", {"src/one.txt": "one\n"}), ("two", {"src/two.txt": "two\n"}),
                ("three", {"src/three.txt": "three\n"}), ("four", {"src/app.txt": "FAIL\n"}),
            ):
                self.candidate(adopter, changes, name=name)
            self.batch(adopter, "one", "two", "three", "four")
            self.assertEqual(adopter.item("four")["stage"], "stopped")
            runs[search] = self.check_runs(adopter, "unit")
        # The batch, then prefixes: serial checks 1, 2, 3; bisection checks 2, 3.
        self.assertEqual(runs, {"serial": 4, "bisect": 3})

    def test_a_base_push_between_build_and_landing_stops_the_batch(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)

        def push_base(runner, subject) -> None:
            if subject == "second":
                adopter.advance_base({"src/other.txt": "other\n"})

        results = self.batch(adopter, *subjects, seams={"before_batch_landing": push_base})
        documents = {result.document()["candidate"]: result.document() for result in results}
        self.assertEqual(documents["first"]["outcome"], "merged")
        self.assertEqual(documents["second"]["stop"]["reason"], "batch_tree_mismatch")
        self.assertTrue(documents["second"]["stop"]["queueStopped"])
        # The second candidate did not land: the base holds the push on top of the first.
        self.assertEqual(adopter.show(adopter.tip("main"), "src/app.txt"), "app one")
        self.assertIn("may not have been checked on its own", " ".join(documents["second"]["notes"]))
        self.assertEqual(adopter.item("second")["stage"], "queued")
        self.assertEqual(adopter.item("third")["stage"], "queued")

    def test_a_base_push_before_the_first_landing_stops_the_queue_with_nothing_landed(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)

        def push_base(runner, subject) -> None:
            if subject == "first":
                adopter.advance_base({"src/other.txt": "other\n"})

        documents = {result.document()["candidate"]: result.document()
                     for result in self.batch(adopter, *subjects, seams={"before_batch_landing": push_base})}
        self.assertEqual(documents["first"]["stop"]["reason"], "batch_tree_mismatch")
        self.assertTrue(documents["first"]["stop"]["queueStopped"])
        for name in subjects:
            self.assertEqual(adopter.item(name)["stage"], "queued")

    def test_a_conflict_in_derived_paths_is_regenerated_and_one_in_source_drops_the_member(self) -> None:
        adopter = self.adopter()
        # Both carry their own stale gen/generated.txt, so their trees conflict there only.
        self.candidate(adopter, {"src/app.txt": "app two\n", "gen/generated.txt": "app two\nlib one\n"}, name="first")
        self.candidate(adopter, {"src/lib.txt": "lib two\n", "gen/generated.txt": "app one\nlib two\n"}, name="second")
        # This one conflicts with the first in a source file.
        self.candidate(adopter, {"src/app.txt": "app three\n"}, name="third")
        # The conflicting member sits in the middle: the one after it still joins the batch.
        results = self.batch(adopter, "first", "third", "second")
        documents = {result.document()["candidate"]: result.document() for result in results}
        self.assertEqual(documents["third"]["stop"]["reason"], "source_conflict")
        self.assertIn("dropped from the batch", documents["third"]["stop"]["detail"])
        self.assertEqual(adopter.item("third")["stage"], "stopped")
        self.assertEqual(documents["first"]["outcome"], "merged")
        self.assertEqual(documents["second"]["outcome"], "merged")
        self.assertEqual(adopter.show(adopter.tip("main"), "gen/generated.txt"), "app two\nlib two")

    def test_a_derived_conflict_without_a_declared_regenerator_drops_the_member(self) -> None:
        adopter = self.adopter({"regenerable": {"paths": [], "regenerators": []}})
        self.candidate(adopter, {"src/app.txt": "app two\n", "gen/generated.txt": "app two\nlib one\n"}, name="first")
        self.candidate(adopter, {"src/lib.txt": "lib two\n", "gen/generated.txt": "app one\nlib two\n"}, name="second")
        results = self.batch(adopter, "first", "second")
        documents = {result.document()["candidate"]: result.document() for result in results}
        self.assertEqual(documents["second"]["stop"]["reason"], "source_conflict")
        self.assertEqual(documents["first"]["outcome"], "merged")

    def test_the_views_name_the_batch_and_every_member_mirrors_its_checks(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)
        real = state_tool.patch
        seen: list[dict] = []

        def recording(directory, **kwargs):
            written = real(directory, **kwargs)
            now = written["meta"]["checking"]["now"]
            if now is not None and any(step["state"] == "running" for step in now["steps"]):
                seen.append(copy.deepcopy(written))
            return written

        with mock.patch.object(queue.delivery_state, "patch", recording):
            self.batch(adopter, *subjects)
        live = [state for state in seen if any(step["label"] == "unit" for step in state["meta"]["checking"]["now"]["steps"])]
        self.assertTrue(live)
        for state in live:
            now = state["meta"]["checking"]["now"]
            self.assertIn("first, second, third", now["title"])
            steps = [(step["label"], step["state"]) for step in now["steps"]]
            for item in state["queue"]:
                if item["subject"] in subjects:
                    self.assertEqual(item["stage"], "checking")
                    self.assertEqual([(check["id"], check["state"]) for check in item["checks"]], steps)

    def test_members_landing_after_a_culprit_search_report_the_passing_prefixs_checks(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter, failing="second")
        results = self.batch(adopter, *subjects)
        first = next(result.document() for result in results if result.document()["candidate"] == "first")
        self.assertEqual(first["outcome"], "merged")
        self.assertTrue(first["checks"])
        self.assertEqual({check["state"] for check in first["checks"]}, {"passed"})

    def test_a_broken_base_found_on_the_first_member_reports_every_member(self) -> None:
        adopter = self.adopter()
        # The unit check fails on the base itself, before any member joins.
        adopter.advance_base({"src/app.txt": "FAIL\n"})
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="third")
        results = self.batch(adopter, "first", "third")
        documents = {result.document()["candidate"]: result.document() for result in results}
        self.assertEqual(set(documents), {"first", "third"})
        self.assertEqual(documents["first"]["stop"]["reason"], "base_broken")
        self.assertTrue(documents["first"]["stop"]["queueStopped"])
        self.assertEqual(documents["third"]["outcome"], "stopped")
        self.assertIn("base_broken", documents["third"]["stop"]["detail"])
        self.assertEqual(adopter.item("third")["stage"], "queued")
        self.assertEqual(adopter.item("first")["stage"], "stopped")
        # The culprit's re-run on the base is journaled on the culprit, between its failure and the stop.
        rerun = [(entry["kind"], entry["subject"]) for entry in state_tool.read_journal(adopter.state)
                 if entry["kind"] in ("base.rerun.started", "base.rerun.finished", "base.broken")]
        self.assertEqual(rerun, [("base.rerun.started", "first"), ("base.rerun.finished", "first"),
                                 ("base.broken", "first")])
        [finished] = [entry for entry in state_tool.read_journal(adopter.state) if entry["kind"] == "base.rerun.finished"]
        self.assertIn("also fails", finished["summary"])

    def test_members_a_failed_batch_requeues_are_journalled_so_the_stats_end_their_check(self) -> None:
        adopter = self.adopter()
        # A broken base stops the first member and sends the other two back to queued.
        adopter.advance_base({"src/app.txt": "FAIL\n"})
        for name, changes in (("first", {"src/lib.txt": "lib two\n"}), ("third", {"src/extra.txt": "extra\n"}),
                              ("fourth", {"src/more.txt": "more\n"})):
            self.candidate(adopter, changes, name=name)
            state_tool.append_journal(adopter.state, kind="candidate.handed", summary=f"Handed {name}", subject=name)
        self.batch(adopter, "first", "third", "fourth")
        stats = state_tool.delivery_stats(state_tool.read_state(adopter.state), state_tool.read_journal(adopter.state))
        for name in ("third", "fourth"):
            with self.subTest(name):
                self.assertEqual(adopter.item(name)["stage"], "queued")
                self.assertEqual(stats[name]["outcome"], "stopped", stats[name])
                self.assertIsNotNone(stats[name]["endedAt"])
                requeued = [entry for entry in state_tool.read_journal(adopter.state)
                            if entry["kind"] == "candidate.stopped" and entry.get("subject") == name]
                self.assertTrue(requeued and "queued again" in requeued[-1]["summary"], requeued)
                self.assertIn("base_broken", requeued[-1]["summary"])

    def test_a_stacked_pair_lands_through_a_squash_merging_forge(self) -> None:
        adopter = self.environment_adopter({"MQ_MERGE": "squash"})
        lower = adopter.lane_commit("lower", {"src/lib.txt": "lib two\n"})
        adopter.enqueue("lower", lower)
        # The upper candidate is built on the lower one's branch, so it carries the lower one's commit.
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        upper = adopter.lane_commit("upper", {"src/extra.txt": "extra\n"}, start="origin/lower")
        adopter.enqueue("upper", upper)
        results = self.batch(adopter, "lower", "upper")
        self.assertEqual(self.outcomes(results), {"lower": "merged", "upper": "merged"})
        main = adopter.tip("main")
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        # Squash merges: each landing is one commit on the base, with exactly the recorded tree.
        self.assertEqual(run("rev-list", "--count", f"{results[0].document()['mergedHead']}..{main}", cwd=adopter.lane), "1")
        self.assertEqual(adopter.show(main, "src/lib.txt"), "lib two")
        self.assertEqual(adopter.show(main, "src/extra.txt"), "extra")

    def test_placeholders_a_sync_or_check_provides_have_values_in_a_batch(self) -> None:
        # The same binding works under run: prepare names the branch, a check names the head it checks.
        prepare = [[PY, "-c", "import sys; sys.exit(0 if sys.argv[1] and sys.argv[2] else 1)", "{branch}", "{head}"]]
        named = check_entry("named", "--named", "{branch}", "{head}", "{expectedHead}")
        named["argv"] = [PY, "-c", "import sys; sys.exit(0 if all(sys.argv[1:]) else 1)", "{branch}", "{head}", "{expectedHead}"]
        adopter = self.adopter({"prepare": prepare, "alwaysRun": [named]})
        subjects = self.trio(adopter)
        results = self.batch(adopter, *subjects)
        self.assertEqual(self.outcomes(results), {name: "merged" for name in subjects},
                         [result.document()["stop"] for result in results])

    def test_a_partial_landing_stops_the_queue(self) -> None:
        # The forge refuses the second merge, after the first landed an intermediate tree.
        adopter = self.adopter()
        subjects = self.trio(adopter)

        def refuse_second(runner, subject) -> None:
            if subject == "second":
                os.environ["MQ_MERGE"] = "refuse"

        self.addCleanup(os.environ.pop, "MQ_MERGE", None)
        results = self.batch(adopter, *subjects, seams={"before_batch_landing": refuse_second})
        documents = {result.document()["candidate"]: result.document() for result in results}
        self.assertEqual(documents["first"]["outcome"], "merged")
        self.assertEqual(documents["second"]["stop"]["reason"], "merge_refused")
        self.assertTrue(documents["second"]["stop"]["queueStopped"])
        self.assertTrue(documents["third"]["stop"]["queueStopped"])
        self.assertEqual(adopter.item("third")["stage"], "queued")

    def test_a_runner_error_after_a_partial_landing_stops_the_queue(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)
        real = queue.Runner.merge

        def failing_second(runner, subject, result, **options):
            if subject == "second":
                raise RuntimeError("the state write kept failing")
            return real(runner, subject, result, **options)

        with mock.patch.object(queue.Runner, "merge", failing_second):
            documents = self.batch_documents(adopter, *subjects)
        self.assertEqual(documents["first"]["outcome"], "merged")
        for name in ("second", "third"):
            self.assertEqual(documents[name]["stop"]["reason"], "runner_error")
            self.assertTrue(documents[name]["stop"]["queueStopped"], documents[name]["stop"])
        self.assertIn("holds the tree the batch built up to first", " ".join(documents["second"]["notes"]))

    def test_members_resolve_from_the_base_not_the_tree_built_so_far(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)
        real = queue.Runner.resolve
        seen: list[tuple[str, str]] = []

        def recording(runner, subject, context, cwd):
            seen.append((runner.tree("HEAD", cwd=cwd), context.element("{baseHead}")))
            return real(runner, subject, context, cwd)

        base_head = adopter.tip("main")
        with mock.patch.object(queue.Runner, "resolve", recording):
            self.batch(adopter, *subjects)
        base_tree = run("rev-parse", f"{base_head}^{{tree}}", cwd=adopter.operator)
        build = seen[:3]
        self.assertEqual(build, [(base_tree, base_head)] * 3)

    @staticmethod
    def runner_set(adopter: Adopter, subject: str, **fields: object) -> None:
        """Write fields only the runner owns, as an earlier run would have."""
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="runner", version=current["version"],
                         selector=f"queue[subject={subject}]", assignments={f"/{key}": value for key, value in fields.items()})

    def batch_documents(self, adopter: Adopter, *subjects: str, **options: object) -> dict[str, dict]:
        return {result.document()["candidate"]: result.document() for result in self.batch(adopter, *subjects, **options)}

    def test_a_member_that_moved_after_review_drops_out(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)
        # The second lane pushes again after its head was recorded.
        adopter.lane_commit("second", {"src/app.txt": "app unreviewed\n"})
        documents = self.batch_documents(adopter, *subjects)
        self.assertEqual(documents["second"]["stop"]["reason"], "candidate_moved")
        self.assertEqual(documents["first"]["outcome"], "merged")
        self.assertEqual(documents["third"]["outcome"], "merged")
        self.assertEqual(adopter.show(adopter.tip("main"), "src/app.txt"), "app one")

    def test_a_branch_that_moves_after_resolving_drops_out(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)
        real = queue.Runner.fetch

        def moved(runner, branch, private):
            fetched = real(runner, branch, private)
            return "f" * 40 if branch == "second" and private == "candidate" else fetched

        with mock.patch.object(queue.Runner, "fetch", moved):
            documents = self.batch_documents(adopter, *subjects)
        self.assertEqual(documents["second"]["stop"]["reason"], "candidate_moved")
        self.assertEqual(documents["third"]["outcome"], "merged")

    def test_an_unreviewed_change_drops_the_member_and_leaves_nothing_of_it_behind(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)
        # The second's recorded synced head carries a change its reviewed head does not.
        sneaked = adopter.lane_commit("second", {"src/sneaked.txt": "not reviewed\n"})
        self.runner_set(adopter, "second", syncedHead=sneaked)
        documents = self.batch_documents(adopter, *subjects)
        self.assertEqual(documents["second"]["stop"]["reason"], "unreviewed_change")
        self.assertEqual(documents["third"]["outcome"], "merged")
        # The dropped member's merge was undone before the next member joined.
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        self.assertFalse(run("ls-tree", "--name-only", "origin/main", "src/sneaked.txt", cwd=adopter.lane))
        self.assertEqual(adopter.show(adopter.tip("main"), "src/app.txt"), "app one")

    def test_an_already_merged_member_drops_out(self) -> None:
        adopter = self.adopter()
        subjects = self.trio(adopter)
        self.runner_set(adopter, "second", stage="merged")
        documents = self.batch_documents(adopter, *subjects)
        self.assertEqual(documents["second"]["stop"]["reason"], "already_merged")
        self.assertEqual(documents["first"]["outcome"], "merged")
        self.assertEqual(documents["third"]["outcome"], "merged")

    def test_a_member_is_never_left_mergeable_on_its_own_tree(self) -> None:
        # A run killed after a member's synced head is pushed must not leave it checked: that tree was never checked alone.
        adopter = self.adopter()
        subjects = self.trio(adopter)
        seen: list[str] = []
        real = queue.Runner.merge

        def stage_at_merge(runner, subject, result, **options):
            seen.append(runner.item(subject)["stage"])
            return real(runner, subject, result, **options)

        with mock.patch.object(queue.Runner, "merge", stage_at_merge):
            self.batch(adopter, *subjects)
        self.assertEqual(seen, ["checking"] * 3)

    def test_a_failing_batch_names_the_culprits_tests_and_log_and_its_probes_stay_out_of_the_history(self) -> None:
        adopter = self.adopter({"alwaysRun": [check_entry("suite", "--fail-with", "test_app")],
                                "checkOutput": FAILURE_OUTPUT})
        subjects = self.trio(adopter)
        documents = self.batch_documents(adopter, *subjects)
        detail = documents["second"]["stop"]["detail"]
        self.assertEqual(documents["second"]["stop"]["reason"], "check_failed")
        self.assertIn("culprit", detail)
        self.assertIn("failing test_app", detail)
        self.assertIn("log logs/", detail)
        stopped = [entry for entry in state_tool.read_journal(adopter.state)
                   if entry["kind"] == "candidate.stopped" and entry.get("subject") == "second"]
        self.assertIn("failing test_app", stopped[-1]["summary"])
        # The batch's run and the third's own run count; the prefixes checked to find the culprit do not.
        self.assertEqual(len(state_tool.read_state(adopter.state)["meta"]["checkHistory"]["runs"]), 2)

    def test_a_batch_needs_two_candidates(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, name="only")
        results = self.batch(adopter, "only")
        self.assertEqual(results[-1].document()["stop"]["reason"], "binding_invalid")
        self.assertEqual(results[-1].document()["outcome"], "refused")


class ParallelCheckTests(MergeQueueTestCase):
    """Checks marked parallelSafe run together, up to the binding's limit; the others run alone."""

    def timed(self, cid: str, parallel: bool) -> dict:
        argv = [PY, "-c", (
            "import json, os, sys, time; start = time.time(); time.sleep(1.5); "
            "open(os.environ['MQ_LOG'] + '.times', 'a').write(json.dumps([sys.argv[1], start, time.time()]) + '\\n')"
        ), cid]
        entry = {"id": cid, "argv": argv, "cwd": ".", "reasons": [cid], "prerequisites": []}
        if parallel:
            entry["parallelSafe"] = True
        return entry

    def spans(self, adopter: Adopter) -> dict[str, tuple[float, float]]:
        lines = Path(str(adopter.log) + ".times").read_text().splitlines()
        return {name: (start, end) for name, start, end in map(json.loads, lines)}

    @staticmethod
    def overlap(first: tuple[float, float], second: tuple[float, float]) -> bool:
        return first[0] < second[1] and second[0] < first[1]

    def test_parallel_safe_checks_overlap_and_the_others_run_alone(self) -> None:
        checks = [self.timed("p1", True), self.timed("p2", True), self.timed("solo", False)]
        adopter = self.adopter({"alwaysRun": checks, "parallelChecks": 2})
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        spans = self.spans(adopter)
        self.assertTrue(self.overlap(spans["p1"], spans["p2"]), spans)
        self.assertFalse(self.overlap(spans["solo"], spans["p1"]), spans)
        self.assertFalse(self.overlap(spans["solo"], spans["p2"]), spans)

    def test_a_check_not_marked_parallel_safe_waits_even_with_room_under_the_limit(self) -> None:
        checks = [self.timed("p1", True), self.timed("solo", False)]
        adopter = self.adopter({"alwaysRun": checks, "parallelChecks": 3})
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        spans = self.spans(adopter)
        self.assertFalse(self.overlap(spans["solo"], spans["p1"]), spans)

    def test_at_the_default_limit_checks_run_on_the_runners_own_thread(self) -> None:
        # So an interrupt reaches the check at once, as before parallel checks existed.
        import threading
        adopter = self.adopter()
        self.candidate(adopter)
        real = queue.Runner.run_one
        threads: list[bool] = []

        def recording(runner, check, checkout, context):
            threads.append(threading.current_thread() is threading.main_thread())
            return real(runner, check, checkout, context)

        with mock.patch.object(queue.Runner, "run_one", recording):
            self.run_queue(adopter, "feature")
        self.assertTrue(threads)
        self.assertTrue(all(threads), threads)

    def test_no_more_than_the_limit_run_at_once(self) -> None:
        checks = [self.timed(name, True) for name in ("p1", "p2", "p3")]
        adopter = self.adopter({"alwaysRun": checks, "parallelChecks": 2})
        self.candidate(adopter)
        real = queue.Runner.write_progress
        running: list[int] = []

        def counting(runner, subjects, states):
            running.append(sum(1 for record in states if record["state"] == "running"))
            return real(runner, subjects, states)

        with mock.patch.object(queue.Runner, "write_progress", counting):
            self.run_queue(adopter, "feature")
        spans = self.spans(adopter)
        self.assertFalse(self.overlap(spans["p1"], spans["p3"]) and self.overlap(spans["p2"], spans["p3"])
                         and self.overlap(spans["p1"], spans["p2"]), spans)
        # The live view never shows more checks running than may run.
        self.assertEqual(max(running), 2, running)

    def test_the_limit_defaults_to_one(self) -> None:
        checks = [self.timed("p1", True), self.timed("p2", True)]
        adopter = self.adopter({"alwaysRun": checks})
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        spans = self.spans(adopter)
        self.assertFalse(self.overlap(spans["p1"], spans["p2"]), spans)

    def test_prerequisites_still_order_parallel_checks(self) -> None:
        later = {**self.timed("later", True), "prerequisites": ["first"]}
        adopter = self.adopter({"alwaysRun": [self.timed("first", True), later], "parallelChecks": 2})
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        spans = self.spans(adopter)
        self.assertLessEqual(spans["first"][1], spans["later"][0])


FAILURE_OUTPUT = {"failurePattern": r"^FAIL: (?P<id>\S+)(?: (?P<message>.*))?$"}


class CheckVisibilityTests(MergeQueueTestCase):
    """Timing against history, failure detail with a log, and retries of declared flakes."""

    def visible_adopter(self, checks: list[dict], **more: object) -> Adopter:
        return self.adopter({"alwaysRun": checks, "checkOutput": FAILURE_OUTPUT, **more})

    def seed_history(self, adopter: Adopter, history: dict) -> None:
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="runner", version=current["version"],
                         assignments={"/meta/checkHistory": history})

    def recorded_steps(self) -> tuple:
        real = state_tool.patch
        seen: list[list[dict]] = []

        def recording(directory, **kwargs):
            written = real(directory, **kwargs)
            now = written["meta"]["checking"]["now"]
            if kwargs.get("writer") == "runner" and now is not None:
                seen.append(copy.deepcopy(now["steps"]))
            return written

        return mock.patch.object(queue.delivery_state, "patch", recording), seen

    def check_entry_of(self, results: list, name: str) -> dict:
        for result in results:
            for entry in result.document()["checks"]:
                if entry["id"] == name:
                    found = entry
        return found

    def journal(self, adopter: Adopter, kind: str) -> list[dict]:
        return [entry for entry in state_tool.read_journal(adopter.state) if entry["kind"] == kind]

    def test_steps_carry_their_times_and_the_history_median_as_expected(self) -> None:
        adopter = self.visible_adopter([check_entry("always")])
        # Only the last five passing runs count: their median is 40 seconds.
        self.seed_history(adopter, {"checks": {"always": {"seconds": [10, 20, 30, 40, 50, 60], "flakes": 0}}, "runs": []})
        self.candidate(adopter)
        patched, seen = self.recorded_steps()
        with patched:
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        running = [step for steps in seen for step in steps if step["label"] == "always" and step["state"] == "running"]
        finished = [step for steps in seen for step in steps if step["label"] == "always" and step["state"] == "passed"]
        self.assertTrue(running and finished)
        self.assertEqual(running[0]["expectedSeconds"], 40)
        self.assertIn("startedAt", running[0])
        self.assertNotIn("finishedAt", running[0])
        self.assertEqual(finished[-1]["startedAt"], running[0]["startedAt"])
        self.assertIn("finishedAt", finished[-1])
        # A check with no history has no estimate rather than a guess.
        unit = [step for steps in seen for step in steps if step["label"] == "unit"]
        self.assertTrue(unit)
        self.assertNotIn("expectedSeconds", unit[0])

    def test_a_providers_titles_reach_the_record_and_untitled_checks_keep_their_ids(self) -> None:
        adopter = self.adopter({"alwaysRun": [check_entry("always", title="Always-on smoke")]}, mode="titled")
        self.candidate(adopter)
        patched, seen = self.recorded_steps()
        with patched:
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        rows = {check["id"]: check for check in adopter.item("feature")["checks"]}
        self.assertEqual(rows["unit"]["title"], "Unit tests (webapp) <b>&")
        self.assertEqual(rows["always"]["title"], "Always-on smoke")
        self.assertNotIn("title", rows["plain"])
        # The checking panel carries the same titles from the first write that names the checks.
        self.assertTrue(seen)
        named = next(steps for steps in seen if any(step["label"] == "unit" for step in steps))
        first = {step["label"]: step for step in named}
        self.assertEqual(first["unit"]["title"], "Unit tests (webapp) <b>&")
        self.assertEqual(first["always"]["title"], "Always-on smoke")
        self.assertNotIn("title", first["plain"])
        every = [step for steps in seen for step in steps if step["label"] == "unit"]
        self.assertTrue(all(step["title"] == "Unit tests (webapp) <b>&" for step in every))
        # The id stays the check's name everywhere else.
        [selection] = [result.document()["selection"] for result in results if result.document().get("selection")]
        self.assertIn("unit", selection["checks"])

    def test_a_long_title_is_cut_to_the_records_limit(self) -> None:
        adopter = self.adopter(mode="long-title")
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        title = next(check for check in adopter.item("feature")["checks"] if check["id"] == "unit")["title"]
        self.assertEqual(len(title), 200)
        self.assertTrue(title.startswith("Behaviour scenario: xxx"))
        self.assertTrue(title.endswith("…"))

    def test_a_title_of_exactly_the_records_limit_is_kept_whole(self) -> None:
        adopter = self.adopter(mode="exact-title")
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        title = next(check for check in adopter.item("feature")["checks"] if check["id"] == "unit")["title"]
        self.assertEqual(title, "T" * 200)

    def test_a_titles_change_leaves_a_checks_definition_as_it_was(self) -> None:
        binding = {"checkTimeoutSeconds": 20, "profiles": {}, "environment": {}, "prepare": [], "hooks": "bypass"}
        plain = check_entry("unit", "src/app.txt")
        definitions = {queue.check_definition({**plain, **more}, binding)
                       for more in ({}, {"title": "Unit tests"}, {"title": "Unit tests, renamed"})}
        self.assertEqual(len(definitions), 1)
        self.assertNotEqual(queue.check_definition({**plain, "argv": [PY, "tools/check.py", "other"]}, binding),
                            definitions.pop())

    def test_a_batchs_first_write_names_its_checks_by_title(self) -> None:
        adopter = self.adopter(mode="titled")
        for name, changes in (("first", {"src/lib.txt": "lib two\n"}), ("second", {"src/extra.txt": "extra\n"})):
            self.candidate(adopter, changes, name=name)
        patched, seen = self.recorded_steps()
        with patched:
            adopter.runner().run_command("batch", ["first", "second"])
        named = next(steps for steps in seen if any(step["label"] == "unit" for step in steps))
        self.assertEqual({step["label"]: step.get("title") for step in named}["unit"], "Unit tests (webapp) <b>&")
        self.assertTrue(all(step["state"] == "pending" for step in named))

    def test_a_binding_check_with_an_empty_title_is_refused(self) -> None:
        adopter = self.adopter({"alwaysRun": [check_entry("always", title="")]})
        self.candidate(adopter)
        last = self.run_queue(adopter, "feature")[-1].document()
        self.assertEqual(last["outcome"], "refused", last)
        self.assertIn("title", last["stop"]["detail"])

    def test_an_empty_title_makes_the_providers_answer_malformed(self) -> None:
        adopter = self.adopter(mode="empty-title")
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        [selection] = [result.document()["selection"] for result in results if result.document().get("selection")]
        [provider] = [record for record in selection["providers"] if record["source"] == "base"]
        self.assertEqual(provider["outcome"], "malformed")
        self.assertIn("title", provider["detail"])

    def test_a_passing_run_adds_to_the_history_and_keeps_the_last_five(self) -> None:
        adopter = self.visible_adopter([check_entry("always")])
        self.seed_history(adopter, {"checks": {"always": {"seconds": [1, 2, 3, 4, 5], "flakes": 2}},
                                    "runs": [900, 800, 700, 600, 500]})
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        history = state_tool.read_state(adopter.state)["meta"]["checkHistory"]
        self.assertEqual(history["checks"]["always"]["seconds"][:4], [2, 3, 4, 5])
        self.assertEqual(len(history["checks"]["always"]["seconds"]), 5)
        self.assertEqual(history["checks"]["always"]["flakes"], 2)
        self.assertIn("unit", history["checks"])
        # The oldest run drops out and this one, a few seconds long, joins.
        self.assertEqual(history["runs"][:4], [800, 700, 600, 500])
        self.assertLess(history["runs"][4], 500)

    def test_a_failed_check_records_its_failing_tests_and_log_everywhere(self) -> None:
        adopter = self.visible_adopter([check_entry("suite", "--fail-with", "test_alpha", "test_beta")])
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        document = self.assertStopped(results, "check_failed")
        entry = self.check_entry_of(results, "suite")
        self.assertEqual(entry["exitCode"], 1)
        self.assertEqual(entry["failures"], [{"id": "test_alpha", "message": "boom"}, {"id": "test_beta", "message": "boom"}])
        log = adopter.state / entry["log"]
        self.assertIn("some other output", log.read_text(encoding="utf-8"))
        self.assertIn("test_alpha", document["stop"]["detail"])
        self.assertIn(entry["log"], document["stop"]["detail"])
        row = next(check for check in adopter.item("feature")["checks"] if check["id"] == "suite")
        self.assertEqual(row["failures"], ["test_alpha", "test_beta"])
        self.assertEqual(row["log"], entry["log"])
        [stopped] = self.journal(adopter, "candidate.stopped")
        self.assertIn("test_beta", stopped["summary"])
        self.assertIn(entry["log"], stopped["summary"])
        self.assertEqual(self.journal(adopter, "check.retried"), [])

    def test_a_failing_check_journals_its_failing_tests_and_log_before_the_stop(self) -> None:
        adopter = self.visible_adopter([check_entry("suite", "--fail-with", "test_alpha", "test_beta")])
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        entry = self.check_entry_of(results, "suite")
        [failed] = self.journal(adopter, "check.failed")
        self.assertEqual(failed["subject"], "feature")
        self.assertIn("suite", failed["summary"])
        self.assertIn("test_alpha", failed["summary"])
        self.assertIn("test_beta", failed["summary"])
        self.assertIn(entry["log"], failed["summary"])
        [stopped] = self.journal(adopter, "candidate.stopped")
        self.assertLess(failed["id"], stopped["id"])

    def test_a_timed_out_check_says_so_in_its_entry(self) -> None:
        adopter = self.adopter(mode="slow-child")
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertStopped(results, "check_failed")
        [failed] = self.journal(adopter, "check.failed")
        self.assertIn("timed out", failed["summary"])
        self.assertIn(self.check_entry_of(results, "slow")["log"], failed["summary"])

    def test_a_passing_check_journals_no_failure(self) -> None:
        adopter = self.visible_adopter([check_entry("always")])
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        self.assertEqual(self.journal(adopter, "check.failed"), [])

    def test_a_flake_a_retry_clears_never_wakes_the_lead(self) -> None:
        flakes = [{"check": "suite", "tests": ["test_launcher_.*"], "ref": "FLAKE-7"}]
        adopter = self.visible_adopter([check_entry("suite", "--flaky-once", "test_launcher_cancel")], flakes=flakes)
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertEqual(self.journal(adopter, "check.failed"), [])

    def test_a_declared_flake_failing_again_journals_one_failure_after_its_retry(self) -> None:
        flakes = [{"check": "suite", "ref": "FLAKE-7"}]
        adopter = self.visible_adopter([check_entry("suite", "--fail-with", "test_alpha")], flakes=flakes)
        self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        [failed] = self.journal(adopter, "check.failed")
        [retried] = self.journal(adopter, "check.retried")
        self.assertLess(retried["id"], failed["id"])

    def test_a_batch_journals_a_failure_once_for_each_member_and_not_again_while_it_finds_the_culprit(self) -> None:
        adopter = self.visible_adopter([check_entry("suite", "--fail-with", "test_alpha")])
        for name, changes in (("first", {"src/lib.txt": "lib two\n"}), ("second", {"src/app.txt": "app two\n"}),
                              ("third", {"src/extra.txt": "extra\n"})):
            self.candidate(adopter, changes, name=name)
        adopter.runner().run_command("batch", ["first", "second", "third"])
        self.assertEqual([entry["subject"] for entry in self.journal(adopter, "check.failed")],
                         ["first", "second", "third"])

    def wait_through_a_held_base_rerun(self, ended: int) -> tuple[list, Adopter]:
        """Hold a queue turn on the base re-run, wait as the lead does, and only then let the re-run end."""
        adopter = self.visible_adopter([{"id": "held", "argv": [PY, "tools/hold.py"], "cwd": ".", "reasons": ["held"],
                                         "prerequisites": []}])
        self.candidate(adopter, {"src/candidate.txt": "candidate\n"})
        through = max((entry["id"] for entry in state_tool.read_journal(adopter.state)), default=0)
        marker = Path(str(adopter.log) + ".ended")
        results: list = []
        turn = threading.Thread(target=lambda: results.extend(self.run_queue(adopter, "feature")))
        turn.start()
        try:
            waited = subprocess.run(
                [PY, "-B", str(SCRIPTS / "delivery_state.py"), "--dir", str(adopter.state), "wait", "--after", str(through),
                 "--timeout", "60", "--poll", "0.05", "--load-multiple", "1000000", "--min-free-bytes", "0",
                 "--swap-growth-bytes", str(2**60)],
                capture_output=True, text=True, check=False)
            row = next(check for check in adopter.item("feature")["checks"] if check["id"] == "held")
            kinds = [entry["kind"] for entry in state_tool.read_journal(adopter.state)]
            self.assertEqual(waited.returncode, 0, waited.stdout + waited.stderr)
            woke = json.loads(waited.stdout)
            self.assertEqual([event["kind"] for event in woke["events"]], ["check.failed"])
            self.assertIn("test_held", woke["events"][0]["summary"])
            self.assertIn(row["log"], woke["events"][0]["summary"])
            self.assertEqual(row["failures"], ["test_held"])
            self.assertNotIn("candidate.stopped", kinds)
            self.assertNotIn("base.broken", kinds)
            self.assertEqual(adopter.item("feature")["stage"], "checking")
            # Before the re-run starts, the record says the candidate failed and waits on it.
            deadline = time.monotonic() + 30
            while not self.journal(adopter, "base.rerun.started") and time.monotonic() < deadline:
                time.sleep(0.02)
            [started] = self.journal(adopter, "base.rerun.started")
            self.assertEqual(started["subject"], "feature")
            self.assertIn("held", started["summary"])
            self.assertEqual(self.journal(adopter, "base.rerun.finished"), [])
            now = state_tool.read_state(adopter.state)["meta"]["checking"]["now"]
            self.assertEqual(now["subject"], "feature")
            self.assertEqual(now["baseRerun"]["checks"], ["held"])
            self.assertLessEqual(now["baseRerun"]["startedAt"], started["at"])
            self.assertEqual(adopter.item("feature")["stage"], "checking")
        finally:
            marker.write_text(str(ended))
            turn.join(60)
        self.assertFalse(turn.is_alive())
        return results, adopter

    def rerun_order(self, adopter: Adopter, stop: str) -> dict:
        """The re-run's entries fall between the failure and the stop; returns its verdict entry."""
        order = [entry["kind"] for entry in state_tool.read_journal(adopter.state)
                 if entry["kind"] in ("check.failed", "base.rerun.started", "base.rerun.finished", stop)]
        self.assertEqual(order, ["check.failed", "base.rerun.started", "base.rerun.finished", stop])
        [started] = self.journal(adopter, "base.rerun.started")
        [finished] = self.journal(adopter, "base.rerun.finished")
        self.assertEqual(finished["subject"], "feature")
        self.assertLessEqual(started["at"], finished["at"])
        self.assertRegex(finished["summary"], r"after [0-9]+s")
        # The waiting state ends with the turn.
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"]["checking"]["now"])
        return finished

    def test_the_wait_returns_on_the_failure_while_the_base_rerun_holds_and_the_base_passes(self) -> None:
        results, adopter = self.wait_through_a_held_base_rerun(0)
        document = self.assertStopped(results, "check_failed")
        self.assertFalse(document["stop"]["queueStopped"])
        self.assertIn("test_held", document["stop"]["detail"])
        self.assertEqual(self.journal(adopter, "base.broken"), [])
        self.assertEqual(len(self.journal(adopter, "check.failed")), 1)
        finished = self.rerun_order(adopter, "candidate.stopped")
        self.assertIn("held passed", finished["summary"])
        self.assertIn("the failure is the candidate's", finished["summary"])

    def test_the_wait_returns_on_the_failure_while_the_base_rerun_holds_and_the_base_fails(self) -> None:
        results, adopter = self.wait_through_a_held_base_rerun(1)
        document = self.assertStopped(results, "base_broken")
        self.assertTrue(document["stop"]["queueStopped"])
        self.assertEqual(len(self.journal(adopter, "base.broken")), 1)
        self.assertEqual(len(self.journal(adopter, "check.failed")), 1)
        finished = self.rerun_order(adopter, "base.broken")
        self.assertIn("held also fails", finished["summary"])
        self.assertNotIn("the failure is the candidate's", finished["summary"])

    def rerun_with(self, adopter: Adopter, *, judged: bool = True, starts: bool = True, raises: bool = False) -> tuple[list, list]:
        """Run the queue with the base re-run bent as asked; returns the results and the panel at each re-run entry."""
        real_journal, real_run_one, real_checkout = queue.Runner.journal, queue.Runner.run_one, queue.Runner.checkout
        real_prerequisites = queue.Runner.base_prerequisites_pass
        seen: list = []
        rerunning = []

        def journal(runner, kind, summary, subject=None):
            if kind in ("base.rerun.started", "base.rerun.finished"):
                seen.append((kind, copy.deepcopy(state_tool.read_state(adopter.state)["meta"]["checking"]["now"])))
            if kind == "base.rerun.started":
                rerunning.append(True)
            return real_journal(runner, kind, summary, subject)

        def run_one(runner, check, checkout, context):
            return None if rerunning and not starts else real_run_one(runner, check, checkout, context)

        def prerequisites(runner, *args):
            return real_prerequisites(runner, *args) if judged else False

        def checkout(runner, commit, label, *args, **kwargs):
            if raises and label == "base-rerun":
                raise OSError("no room for the base checkout")
            return real_checkout(runner, commit, label, *args, **kwargs)

        with mock.patch.object(queue.Runner, "journal", journal), mock.patch.object(queue.Runner, "run_one", run_one), \
                mock.patch.object(queue.Runner, "base_prerequisites_pass", prerequisites), \
                mock.patch.object(queue.Runner, "checkout", checkout):
            results = self.run_queue(adopter, "feature")
        return results, seen

    def test_the_panel_says_the_candidate_waits_on_the_rerun_until_its_verdict_and_no_longer(self) -> None:
        adopter = self.adopter(mode="implicit")
        self.candidate(adopter, {"src/app.txt": "FAIL here\n"})
        results, seen = self.rerun_with(adopter)
        self.assertStopped(results, "check_failed")
        (started, at_start), (finished, at_verdict) = seen
        self.assertEqual((started, finished), ("base.rerun.started", "base.rerun.finished"))
        self.assertEqual(at_start["baseRerun"]["checks"], ["implicit"])
        # The verdict is in: the panel still names the candidate until the stop, but no longer says it waits.
        self.assertEqual(at_verdict["subject"], "feature")
        self.assertNotIn("baseRerun", at_verdict)

    def test_a_base_rerun_that_cannot_judge_or_start_the_check_says_so_and_leaves_the_failure_the_candidates(self) -> None:
        for name, bent, words in (("unjudged", {"judged": False}, "implicit could not be judged there"),
                                  ("unstarted", {"starts": False}, "implicit could not start there")):
            with self.subTest(name):
                self.fresh()
                adopter = self.adopter(mode="implicit")
                self.candidate(adopter, {"src/app.txt": "FAIL here\n"})
                results, _ = self.rerun_with(adopter, **bent)
                self.assertStopped(results, "check_failed")
                [finished] = self.journal(adopter, "base.rerun.finished")
                self.assertIn(words + ": the failure is the candidate's", finished["summary"])
                self.assertNotIn("passed", finished["summary"])

    def test_a_base_rerun_that_raises_partway_still_journals_its_end_and_the_error(self) -> None:
        adopter = self.adopter(mode="implicit")
        self.candidate(adopter, {"src/app.txt": "FAIL here\n"})
        results, seen = self.rerun_with(adopter, raises=True)
        last = results[-1].document()
        self.assertEqual(last["outcome"], "stopped", last)
        self.assertIn("no room for the base checkout", last["stop"]["detail"])
        self.assertEqual([kind for kind, _ in seen], ["base.rerun.started", "base.rerun.finished"])
        [finished] = self.journal(adopter, "base.rerun.finished")
        self.assertIn("ended without a verdict (OSError)", finished["summary"])
        self.assertNotIn("baseRerun", seen[1][1])

    def test_a_failure_with_no_check_to_rerun_on_the_base_journals_no_rerun(self) -> None:
        adopter = self.adopter(mode="new-cwd")
        self.candidate(adopter, {"src/app.txt": "app two\n", "newdir/readme.txt": "new\n"})
        self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertEqual(len(self.journal(adopter, "check.failed")), 1)
        self.assertEqual(self.journal(adopter, "base.rerun.started"), [])
        self.assertEqual(self.journal(adopter, "base.rerun.finished"), [])

    BASE_FLAKES = [{"check": "suite", "tests": ["test_launcher_.*"], "ref": "FLAKE-9"}]

    def base_flake_adopter(self, how: str) -> Adopter:
        adopter = self.visible_adopter([check_entry("suite", "--base-flake", how)], flakes=self.BASE_FLAKES)
        self.candidate(adopter, {"src/candidate.txt": "candidate\n"})
        return adopter

    def test_a_declared_flake_failing_on_the_base_rerun_is_retried_once_there(self) -> None:
        adopter = self.base_flake_adopter("once")
        results = self.run_queue(adopter, "feature")
        document = self.assertStopped(results, "check_failed")
        self.assertFalse(document["stop"]["queueStopped"])
        self.assertEqual(self.journal(adopter, "base.broken"), [])
        [retried] = self.journal(adopter, "check.retried")
        self.assertEqual(retried["subject"], "feature")
        self.assertIn("base re-run", retried["summary"])
        self.assertIn("test_launcher_cancel", retried["summary"])
        self.assertIn("FLAKE-9", retried["summary"])
        [started] = self.journal(adopter, "base.rerun.started")
        [finished] = self.journal(adopter, "base.rerun.finished")
        self.assertLess(started["id"], retried["id"])
        self.assertLess(retried["id"], finished["id"])
        self.assertIn("suite passed after a flake retry", finished["summary"])
        self.assertEqual(state_tool.read_state(adopter.state)["meta"]["checkHistory"]["checks"]["suite"]["flakes"], 1)
        # The record keeps the retry's run, the one the verdict stands on, not the flaky first one.
        rerun = next(check for check in adopter.item("feature")["checks"] if check["id"] == "suite")["baseRerun"]
        self.assertNotIn("failures", rerun)
        self.assertIn(f"(log {rerun['log']})", finished["summary"])
        self.assertNotIn("flaked on the base", (adopter.state / rerun["log"]).read_text(encoding="utf-8"))

    def test_a_declared_flake_failing_its_base_retry_too_breaks_the_base_naming_its_tests_and_log(self) -> None:
        adopter = self.base_flake_adopter("always")
        results = self.run_queue(adopter, "feature")
        document = self.assertStopped(results, "base_broken")
        self.assertEqual(len(self.journal(adopter, "check.retried")), 1)
        row = next(check for check in adopter.item("feature")["checks"] if check["id"] == "suite")
        rerun = row["baseRerun"]
        self.assertEqual(rerun["failures"], ["test_launcher_cancel"])
        self.assertIn("flaked on the base", (adopter.state / rerun["log"]).read_text(encoding="utf-8"))
        # The candidate's own run keeps its tests and log beside the re-run's.
        self.assertEqual(row["failures"], ["test_real"])
        self.assertNotEqual(row["log"], rerun["log"])
        words = f"suite (failing test_launcher_cancel; log {rerun['log']})"
        self.assertIn(words, document["stop"]["detail"])
        [broken] = self.journal(adopter, "base.broken")
        self.assertIn(words, broken["summary"])

    def test_a_base_rerun_failure_keeps_its_log_and_names_its_tests_like_a_check_failure(self) -> None:
        adopter = self.visible_adopter([check_entry("suite", "--fail-with", "test_alpha", "test_beta")])
        adopter.advance_base({"src/app.txt": "app broken\n"})
        self.candidate(adopter, {"src/lib.txt": "lib two\n"})
        results = self.run_queue(adopter, "feature")
        document = self.assertStopped(results, "base_broken")
        self.assertTrue(document["stop"]["queueStopped"])
        row = next(check for check in adopter.item("feature")["checks"] if check["id"] == "suite")
        rerun = row["baseRerun"]
        self.assertEqual(rerun["failures"], ["test_alpha", "test_beta"])
        self.assertTrue(rerun["log"].startswith("logs/"))
        self.assertIn("some other output", (adopter.state / rerun["log"]).read_text(encoding="utf-8"))
        [failed] = self.journal(adopter, "check.failed")
        [broken] = self.journal(adopter, "base.broken")
        # Named as check.failed names the candidate's run: the check, then its failing tests and log.
        self.assertIn(f"suite (failing test_alpha, test_beta; log {row['log']})", failed["summary"])
        self.assertIn(f"suite (failing test_alpha, test_beta; log {rerun['log']})", broken["summary"])
        self.assertIn(rerun["log"], document["stop"]["detail"])
        [finished] = self.journal(adopter, "base.rerun.finished")
        self.assertIn(f"suite also fails (failing test_alpha, test_beta; log {rerun['log']})", finished["summary"])
        # No flake is declared, so nothing is retried.
        self.assertEqual(self.journal(adopter, "check.retried"), [])

    def test_a_base_rerun_that_times_out_is_never_retried_even_under_a_whole_check_flake(self) -> None:
        adopter = self.visible_adopter([check_entry("hang", "--sleep-child", timeoutSeconds=2)],
                                       flakes=[{"check": "hang", "ref": "FLAKE-3"}])
        self.candidate(adopter, {"src/candidate.txt": "candidate\n"})
        document = self.assertStopped(self.run_queue(adopter, "feature"), "base_broken")
        self.assertIn("hang", document["stop"]["detail"])
        self.assertEqual(self.journal(adopter, "check.retried"), [])
        history = state_tool.read_state(adopter.state)["meta"]["checkHistory"]["checks"]
        self.assertEqual(history.get("hang", {}).get("flakes", 0), 0)

    def test_a_base_flake_retry_that_cannot_start_leaves_the_failure_the_candidates(self) -> None:
        adopter = self.base_flake_adopter("always")
        real = queue.Runner.run_one
        base_runs = []

        def run_one(runner, check, checkout, context):
            if "base-rerun" in checkout.name:
                base_runs.append(check["id"])
                if len(base_runs) == 2:
                    return None
            return real(runner, check, checkout, context)

        with mock.patch.object(queue.Runner, "run_one", run_one):
            document = self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertFalse(document["stop"]["queueStopped"])
        self.assertEqual(self.journal(adopter, "base.broken"), [])
        self.assertEqual(len(self.journal(adopter, "check.retried")), 1)
        [finished] = self.journal(adopter, "base.rerun.finished")
        self.assertIn("suite could not start there on its flake retry: the failure is the candidate's", finished["summary"])
        # The first run, the only one there is, stays on the record.
        rerun = next(check for check in adopter.item("feature")["checks"] if check["id"] == "suite")["baseRerun"]
        self.assertEqual(rerun["failures"], ["test_launcher_cancel"])
        self.assertIn("flaked on the base", (adopter.state / rerun["log"]).read_text(encoding="utf-8"))

    def test_a_base_rerun_that_passes_keeps_its_log_on_the_record(self) -> None:
        adopter = self.base_flake_adopter("once")
        (Path(str(adopter.log) + ".base-flaked")).write_text("")
        results = self.run_queue(adopter, "feature")
        self.assertStopped(results, "check_failed")
        row = next(check for check in adopter.item("feature")["checks"] if check["id"] == "suite")
        self.assertNotIn("failures", row["baseRerun"])
        self.assertTrue((adopter.state / row["baseRerun"]["log"]).is_file())
        self.assertEqual(self.journal(adopter, "check.retried"), [])
        [finished] = self.journal(adopter, "base.rerun.finished")
        self.assertIn(row["baseRerun"]["log"], finished["summary"])

    def test_a_failure_matching_only_a_declared_flake_is_retried_once_and_journaled(self) -> None:
        flakes = [{"check": "suite", "tests": ["test_launcher_.*"], "ref": "FLAKE-7"}]
        adopter = self.visible_adopter([check_entry("suite", "--flaky-once", "test_launcher_cancel")], flakes=flakes)
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        [retried] = self.journal(adopter, "check.retried")
        self.assertIn("FLAKE-7", retried["summary"])
        self.assertIn("test_launcher_cancel", retried["summary"])
        entry = self.check_entry_of(results, "suite")
        self.assertEqual(entry["state"], "passed")
        self.assertEqual(entry["retried"]["flake"], "FLAKE-7")
        self.assertEqual(entry["retried"]["failures"], [{"id": "test_launcher_cancel", "message": "flaked"}])
        history = state_tool.read_state(adopter.state)["meta"]["checkHistory"]
        self.assertEqual(history["checks"]["suite"]["flakes"], 1)

    def test_a_declared_flake_that_fails_again_stops_with_both_failures(self) -> None:
        flakes = [{"check": "suite", "tests": ["test_launcher_.*"], "ref": "FLAKE-7"}]
        adopter = self.visible_adopter([check_entry("suite", "--fail-with", "test_launcher_cancel")], flakes=flakes)
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertStopped(results, "check_failed")
        self.assertEqual(len(self.journal(adopter, "check.retried")), 1)
        entry = self.check_entry_of(results, "suite")
        self.assertEqual(entry["state"], "failed")
        self.assertEqual(entry["failures"], [{"id": "test_launcher_cancel", "message": "boom"}])
        self.assertEqual(entry["retried"]["failures"], [{"id": "test_launcher_cancel", "message": "boom"}])
        self.assertNotEqual(entry["retried"]["log"], entry["log"])

    def test_a_mixed_or_unknown_failure_is_never_retried(self) -> None:
        flakes = [{"check": "suite", "tests": ["test_launcher_.*"], "ref": "FLAKE-7"}]
        for name, tests in (("mixed", ["test_launcher_cancel", "test_real_bug"]), ("unknown", ["test_real_bug"])):
            with self.subTest(name):
                self.fresh()
                adopter = self.visible_adopter([check_entry("suite", "--fail-with", *tests)], flakes=flakes)
                self.candidate(adopter)
                results = self.run_queue(adopter, "feature")
                document = self.assertStopped(results, "check_failed")
                self.assertEqual(self.journal(adopter, "check.retried"), [])
                for test in tests:
                    self.assertIn(test, document["stop"]["detail"])

    def test_a_whole_check_flake_is_retried_even_without_parsed_failures(self) -> None:
        flakes = [{"check": "plain", "ref": "FLAKE-1"}]
        adopter = self.adopter({"alwaysRun": [check_entry("plain", "--flaky-once")], "flakes": flakes})
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertEqual(len(self.journal(adopter, "check.retried")), 1)

    def test_output_the_pattern_cannot_read_never_matches_a_test_pattern_flake(self) -> None:
        # No failure pattern, so no failing ids: a flake that names tests cannot vouch for this failure.
        flakes = [{"check": "suite", "tests": [".*"], "ref": "FLAKE-1"}]
        adopter = self.adopter({"alwaysRun": [check_entry("suite", "--flaky-once", "test_x")], "flakes": flakes})
        self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertEqual(self.journal(adopter, "check.retried"), [])

    def test_a_flake_declaration_for_another_check_does_not_retry_this_one(self) -> None:
        flakes = [{"check": "other", "ref": "FLAKE-1"}]
        adopter = self.visible_adopter([check_entry("suite", "--flaky-once", "test_x")], flakes=flakes)
        self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertEqual(self.journal(adopter, "check.retried"), [])

    @unittest.skipIf(os.name == "nt", "process groups and ps are POSIX")
    def test_a_timed_out_check_is_never_retried(self) -> None:
        adopter = self.adopter({"flakes": [{"check": "slow", "ref": "FLAKE-1"}]}, mode="slow-child")
        self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertEqual(self.journal(adopter, "check.retried"), [])

    def failing_output(self, adopter_changes: dict, script: str) -> list:
        """Run a check whose failing output a short script prints, and return the run's results."""
        argv = [PY, "-c", "import pathlib, sys\n"
                "if pathlib.Path('src/app.txt').read_text() == 'app one\\n': sys.exit(0)\n" + script]
        adopter = self.adopter({"alwaysRun": [{"id": "suite", "argv": argv, "cwd": ".", "reasons": ["r"],
                                               "prerequisites": []}], **adopter_changes})
        self.candidate(adopter)
        self.adopter_in_use = adopter
        return self.run_queue(adopter, "feature")

    def test_matches_with_no_id_or_an_empty_id_are_skipped_not_fatal(self) -> None:
        for pattern in (r"^FAIL:(?: (?P<id>[a-z_]+))?", r"^FAIL: ?(?P<id>\S*)"):
            with self.subTest(pattern=pattern):
                self.fresh()
                results = self.failing_output({"checkOutput": {"failurePattern": pattern}},
                                              "print('FAIL:')\nprint('FAIL: test_named')\nsys.exit(1)\n")
                self.assertStopped(results, "check_failed")
                entry = self.check_entry_of(results, "suite")
                self.assertEqual([failure["id"] for failure in entry["failures"]], ["test_named"])
                self.assertTrue(entry["log"])

    def test_the_pattern_is_matched_one_line_at_a_time_in_each_stream(self) -> None:
        # A negated class cannot run past a line's end, and stdout's last line never joins stderr's first.
        script = ("sys.stdout.write('FAIL: t_a boom\\nsome other output\\nFAIL: t_flaky')\n"
                  "sys.stderr.write('FAIL: t_real\\n')\nsys.exit(1)\n")
        results = self.failing_output({"checkOutput": {"failurePattern": r"^FAIL: (?P<id>[^ ]+)"}}, script)
        entry = self.check_entry_of(results, "suite")
        self.assertEqual([failure["id"] for failure in entry["failures"]], ["t_a", "t_flaky", "t_real"])

    def test_a_repeated_id_keeps_its_first_message(self) -> None:
        results = self.failing_output({"checkOutput": FAILURE_OUTPUT},
                                      "print('FAIL: t_a first')\nprint('FAIL: t_a second')\nsys.exit(1)\n")
        self.assertEqual(self.check_entry_of(results, "suite")["failures"], [{"id": "t_a", "message": "first"}])

    def test_a_flake_pattern_must_match_the_whole_failure_id(self) -> None:
        flakes = [{"check": "suite", "tests": ["test_launcher"], "ref": "FLAKE-7"}]
        adopter = self.visible_adopter([check_entry("suite", "--flaky-once", "test_launcher_cancel_real_bug")],
                                       flakes=flakes)
        self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        self.assertEqual(self.journal(adopter, "check.retried"), [])

    def test_a_long_list_of_failures_is_kept_whole_in_the_result_and_shortened_in_the_stop(self) -> None:
        ids = [f"test_{number:02d}" for number in range(30)]
        adopter = self.visible_adopter([check_entry("suite", "--fail-with", *ids)])
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        document = self.assertStopped(results, "check_failed")
        self.assertEqual(len(self.check_entry_of(results, "suite")["failures"]), 30)
        self.assertIn("test_09 and 20 more", document["stop"]["detail"])
        self.assertNotIn("test_10", document["stop"]["detail"])

    def test_a_run_that_retried_a_flake_still_joins_the_history(self) -> None:
        flakes = [{"check": "suite", "tests": ["test_launcher_.*"], "ref": "FLAKE-7"}]
        adopter = self.visible_adopter([check_entry("suite", "--flaky-once", "test_launcher_cancel")], flakes=flakes)
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        history = state_tool.read_state(adopter.state)["meta"]["checkHistory"]
        self.assertEqual(len(history["runs"]), 1)
        self.assertEqual(len(history["checks"]["suite"]["seconds"]), 1)

    def test_a_retried_check_shows_no_stale_failure_while_it_runs_again(self) -> None:
        flakes = [{"check": "suite", "tests": ["test_launcher_.*"], "ref": "FLAKE-7"}]
        adopter = self.visible_adopter([check_entry("suite", "--flaky-once", "test_launcher_cancel")], flakes=flakes)
        self.candidate(adopter)
        real = state_tool.patch
        rows: list[dict] = []

        def recording(directory, **kwargs):
            written = real(directory, **kwargs)
            item = next(element for element in written["queue"] if element["subject"] == "feature")
            rows.extend(check for check in item["checks"] if check["id"] == "suite" and check["state"] == "running")
            return written

        with mock.patch.object(queue.delivery_state, "patch", recording):
            self.run_queue(adopter, "feature")
        self.assertEqual(len(rows), 2)
        self.assertFalse(any("failures" in row or "log" in row for row in rows), rows)

    def test_log_names_never_collide(self) -> None:
        runner = self.adopter().runner()
        completed = queue.Completed(1, "out\n", "", False, 0.1)
        names = {runner.save_log("same", completed) for _ in range(20)}
        names |= {self.adopter_runner_again().save_log("same", completed) for _ in range(5)}
        self.assertEqual(len(names), 25)

    def adopter_runner_again(self) -> "queue.Runner":
        return queue.Runner(self.directory / "operator", self.directory / "state", remote="origin", base="main")

    def test_a_binding_with_a_bad_failure_pattern_or_flake_is_refused(self) -> None:
        for name, changes in (
            ("a pattern with no id group", {"checkOutput": {"failurePattern": "^FAIL: (\\S+)$"}}),
            ("a pattern that does not compile", {"checkOutput": {"failurePattern": "^FAIL: (?P<id>"}}),
            ("a flake test pattern that does not compile", {"flakes": [{"check": "a", "tests": ["("], "ref": "X"}]}),
        ):
            with self.subTest(name):
                self.fresh()
                adopter = self.adopter(changes)
                self.candidate(adopter)
                self.assertStopped(self.run_queue(adopter, "feature"), "binding_invalid")


class ServeTests(MergeQueueTestCase):
    """serve takes what is queued, in order, as a batch or alone, until the queue is idle or the lead holds it."""

    def serve(self, adopter: Adopter, **options: object) -> list:
        return queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True, **options)

    def outcomes(self, results: list) -> list[tuple]:
        return [(result.document()["subcommand"], result.document()["candidate"], result.document()["outcome"])
                for result in results]

    def test_an_empty_queue_does_nothing(self) -> None:
        adopter = self.adopter()
        self.assertEqual(self.serve(adopter), [])

    def test_one_queued_candidate_merges_alone(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        results = self.serve(adopter)
        self.assertEqual(self.outcomes(results)[-1], ("merge", "feature", "merged"))
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        # Every result is written down, as the command line's are.
        self.assertEqual(len(list((adopter.state / "results").glob("*.json"))), len(results))

    def test_two_queued_candidates_run_as_one_batch(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")
        results = self.serve(adopter)
        self.assertEqual(self.outcomes(results), [("batch", "first", "merged"), ("batch", "second", "merged")])

    def test_a_known_flake_is_retried_once_under_serve(self) -> None:
        flakes = [{"check": "suite", "tests": ["test_launcher_.*"], "ref": "FLAKE-7"}]
        adopter = self.adopter({"alwaysRun": [check_entry("suite", "--flaky-once", "test_launcher_cancel")],
                                "checkOutput": {"failurePattern": r"^FAIL: (?P<id>\S+)"}, "flakes": flakes})
        self.candidate(adopter)
        self.serve(adopter)
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        retried = [entry for entry in state_tool.read_journal(adopter.state) if entry["kind"] == "check.retried"]
        self.assertEqual(len(retried), 1)

    def test_a_hold_pauses_serve(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="lead", version=current["version"],
                         changes={"meta": {"hold": {"reason": "release in progress", "since": "2026-10-21T10:00:00Z"}}})
        self.assertEqual(self.serve(adopter), [])
        self.assertEqual(adopter.item("feature")["stage"], "queued")

    def test_a_hand_off_waiting_for_the_lead_is_not_taken(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="lead", version=current["version"], selector="queue[subject=feature]",
                         changes={"pending": True})
        self.assertEqual(self.serve(adopter), [])

    def test_a_merge_the_queue_records_ends_a_wait_set_while_it_ran(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)

        def lead_sets_the_wait(runner, subject) -> None:
            current = state_tool.read_state(adopter.state)
            state_tool.patch(adopter.state, writer="lead", version=current["version"],
                             selector=f"queue[subject={subject}]", changes={"pending": True})

        results = self.serve(adopter, seams={"before_merge": lead_sets_the_wait})
        self.assertEqual([entry for entry in self.outcomes(results) if entry[2] == "merged"],
                         [("merge", "feature", "merged")])
        item = adopter.item("feature")
        self.assertEqual(item["stage"], "merged")
        self.assertNotIn("pending", item)

    def test_serve_takes_work_queued_while_it_runs(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")

        def queue_another(runner, subject) -> None:
            if subject == "first" and not any(item["subject"] == "second" for item in runner.state()["queue"]):
                self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")

        results = self.serve(adopter, seams={"before_merge": queue_another})
        self.assertEqual([entry for entry in self.outcomes(results) if entry[2] == "merged"],
                         [("merge", "first", "merged"), ("merge", "second", "merged")])

    def test_a_queue_stopping_result_ends_serve(self) -> None:
        adopter = self.adopter()
        # The unit check fails on the base itself: the first candidate stops the queue.
        adopter.advance_base({"src/app.txt": "FAIL\n"})
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        results = self.serve(adopter)
        self.assertTrue(results[-1].document()["stop"]["queueStopped"])
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="later")
        results = self.serve(adopter)
        # Until the lead confirms the base, serve takes nothing more.
        self.assertEqual(results, [])
        self.assertEqual(adopter.item("later")["stage"], "queued")
        adopter.advance_base({"src/app.txt": "app one\n"})
        state_tool.append_journal(adopter.state, kind="base.repaired", summary="The base passes again")
        self.serve(adopter)
        self.assertEqual(adopter.item("later")["stage"], "merged")

    def test_a_partial_landing_pauses_serve_until_the_base_is_confirmed(self) -> None:
        adopter = self.adopter()
        for name, changes in (("first", {"src/lib.txt": "lib two\n"}), ("second", {"src/app.txt": "app two\n"}),
                              ("third", {"src/extra.txt": "extra\n"})):
            self.candidate(adopter, changes, name=name)

        def refuse_second(runner, subject) -> None:
            if subject == "second":
                os.environ["MQ_MERGE"] = "refuse"

        self.addCleanup(os.environ.pop, "MQ_MERGE", None)
        self.serve(adopter, seams={"before_batch_landing": refuse_second})
        os.environ.pop("MQ_MERGE", None)
        self.assertEqual(adopter.item("first")["stage"], "merged")
        self.assertEqual(adopter.item("third")["stage"], "queued")
        self.assertIn("base.broken", [entry["kind"] for entry in state_tool.read_journal(adopter.state)])
        # The base holds a prefix checked only as part of the batch: nothing more is taken until it is confirmed.
        self.assertEqual(self.serve(adopter), [])
        self.assertEqual(adopter.item("third")["stage"], "queued")

    def test_serve_records_nothing_while_another_runner_holds_the_queue(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        with state_tool.file_lock(adopter.state / state_tool.QUEUE_LOCK):
            self.assertEqual(self.serve(adopter), [])
        self.assertEqual(list((adopter.state / "results").glob("*.json")), [])
        self.assertEqual(adopter.item("feature")["stage"], "queued")

    def break_checkout(self, adopter: Adopter) -> Callable[[], None]:
        """Take the operator's checkout away, as a removed clone would; the returned call puts it back."""
        git, away = adopter.operator / ".git", adopter.operator / ".git-away"
        git.rename(away)
        return lambda: away.rename(git)

    def blockers(self, adopter: Adopter) -> list[dict]:
        return [entry for entry in state_tool.read_journal(adopter.state) if entry["kind"] == "blocker.recorded"]

    def test_a_checkout_removed_between_turns_holds_serve_and_stops_nothing(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")
        real = queue.Runner.recover
        restore: list[Callable[[], None]] = []

        def break_after_startup(runner) -> None:
            # The startup check has passed; the checkout goes before the first turn takes anything.
            if not restore:
                restore.append(self.break_checkout(adopter))
            real(runner)

        with mock.patch.object(queue.Runner, "recover", break_after_startup):
            results = self.serve(adopter)
        self.assertEqual(results, [])
        hold = state_tool.read_state(adopter.state)["meta"]["hold"]
        self.assertIn("top level of a checkout", hold["reason"])
        self.assertEqual(len(self.blockers(adopter)), 1)
        self.assertEqual([adopter.item(name)["stage"] for name in ("first", "second")], ["queued", "queued"])
        self.assertNotIn("candidate.stopped", [entry["kind"] for entry in state_tool.read_journal(adopter.state)])
        self.assertTrue(state_tool.show(adopter.state)["status"]["text"].startswith("Serving is paused: "))
        # Repaired and released by the lead: the queue lands in order, with no lane handing anything off again.
        restore[0]()
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="lead", version=current["version"], assignments={"/meta/hold": None})
        self.assertEqual(self.outcomes(self.serve(adopter)), [("batch", "first", "merged"), ("batch", "second", "merged")])

    def test_a_binding_made_invalid_mid_run_holds_serve_with_its_reason(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        real = queue.Runner.recover
        broken: list[bool] = []

        def break_after_startup(runner) -> None:
            if not broken:
                broken.append(True)
                adopter.advance_base({".agents/orchestration.json": "{ not json\n"})
            real(runner)

        with mock.patch.object(queue.Runner, "recover", break_after_startup):
            self.serve(adopter)
        hold = state_tool.read_state(adopter.state)["meta"]["hold"]
        self.assertIn(".agents/orchestration.json", hold["reason"])
        self.assertEqual(adopter.item("feature")["stage"], "queued")
        self.assertEqual(len(self.blockers(adopter)), 1)

    def test_a_setup_that_breaks_during_a_turn_holds_serve_without_stopping_its_candidates(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        real = queue.Runner.run_command
        restore: list[Callable[[], None]] = []

        def break_then_run(runner, *args, **kwargs):
            if not restore:
                restore.append(self.break_checkout(adopter))
            return real(runner, *args, **kwargs)

        with mock.patch.object(queue.Runner, "run_command", break_then_run):
            results = self.serve(adopter)
        self.assertEqual(results[-1].document()["stop"]["reason"], "binding_invalid")
        # The refusal was the setup's, not the candidate's: the candidate waits, and serving is held.
        self.assertEqual(adopter.item("feature")["stage"], "queued")
        self.assertIn("top level of a checkout", state_tool.read_state(adopter.state)["meta"]["hold"]["reason"])
        self.assertEqual(len(self.blockers(adopter)), 1)
        restore[0]()

    def release_hold(self, adopter: Adopter) -> None:
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="lead", version=current["version"], assignments={"/meta/hold": None})

    def test_a_setup_that_breaks_between_sync_and_check_puts_the_candidate_back(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        restore: list[Callable[[], None]] = []

        def break_once(runner, subject) -> None:
            if not restore:
                restore.append(self.break_checkout(adopter))

        self.serve(adopter, seams={"after_sync": break_once})
        item = adopter.item("feature")
        # Synced, then the setup went: the candidate is queued again, not left synced or stopped.
        self.assertEqual((item["stage"], item["checks"]), ("queued", []))
        self.assertIn("top level of a checkout", state_tool.read_state(adopter.state)["meta"]["hold"]["reason"])
        self.assertEqual(len(self.blockers(adopter)), 1)
        restore[0]()
        self.release_hold(adopter)
        self.serve(adopter)
        self.assertEqual(adopter.item("feature")["stage"], "merged")

    def test_a_candidate_put_back_after_its_sync_was_pushed_merges_once_the_hold_is_cleared(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        real = queue.Runner.check
        restore: list[Callable[[], None]] = []

        def break_then_check(runner, subject, result):
            # Sync has pushed its synced head to the candidate's branch; the setup goes before the check.
            if not restore:
                restore.append(self.break_checkout(adopter))
            return real(runner, subject, result)

        with mock.patch.object(queue.Runner, "check", break_then_check):
            self.serve(adopter)
        self.assertEqual(adopter.item("feature")["stage"], "queued")
        restore[0]()
        self.release_hold(adopter)
        self.serve(adopter)
        self.assertEqual(adopter.item("feature")["stage"], "merged")

    def test_a_batch_put_back_after_a_member_was_pushed_merges_once_the_hold_is_cleared(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")
        real = queue.Runner.merge
        restore: list[Callable[[], None]] = []

        def break_then_merge(runner, subject, result, **kwargs):
            # The batch has pushed its first member's synced head; the setup goes before that member's merge begins.
            if not restore:
                restore.append(self.break_checkout(adopter))
            return real(runner, subject, result, **kwargs)

        with mock.patch.object(queue.Runner, "merge", break_then_merge):
            self.serve(adopter)
        self.assertEqual([adopter.item(name)["stage"] for name in ("first", "second")], ["queued", "queued"])
        restore[0]()
        self.release_hold(adopter)
        self.serve(adopter)
        self.assertEqual([adopter.item(name)["stage"] for name in ("first", "second")], ["merged", "merged"])

    def test_a_candidate_put_back_after_its_checks_ran_has_them_cleared(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        restore: list[Callable[[], None]] = []

        def break_once(runner, subject) -> None:
            if not restore:
                restore.append(self.break_checkout(adopter))

        self.serve(adopter, seams={"before_merge": break_once})
        restore[0]()
        item = adopter.item("feature")
        # Checked, then the setup went before the merge began: back to queued, its checks to run again.
        self.assertEqual((item["stage"], item["checks"]), ("queued", []))

    def test_a_candidate_whose_check_failed_as_the_setup_broke_is_checked_again_and_named(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/app.txt": "FAIL\n"})
        real = queue.Runner.check
        restore: list[Callable[[], None]] = []

        def check_then_break(runner, subject, result):
            try:
                return real(runner, subject, result)
            finally:
                if not restore:
                    restore.append(self.break_checkout(adopter))

        with mock.patch.object(queue.Runner, "check", check_then_break):
            self.serve(adopter)
        restore[0]()
        # The failure may have been the setup's: it is put back, to be checked again, and the blocker says so.
        self.assertEqual(adopter.item("feature")["stage"], "queued")
        self.assertIsNotNone(state_tool.read_state(adopter.state)["meta"]["hold"])
        [blocker] = self.blockers(adopter)
        self.assertIn("Put back to queued, to be taken again: feature.", blocker["summary"])

    def test_a_candidate_the_lead_moved_during_the_turn_is_not_put_back(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        real = queue.Runner.check
        restore: list[Callable[[], None]] = []
        newer: list[str] = []

        def move_then_break(runner, subject, result):
            if not restore:
                newer.append(adopter.lane_commit("feature", {"src/app.txt": "app three\n"}))
                adopter.lead_set("feature", head=newer[0])
                restore.append(self.break_checkout(adopter))
            return real(runner, subject, result)

        with mock.patch.object(queue.Runner, "check", move_then_break):
            self.serve(adopter)
        restore[0]()
        item = adopter.item("feature")
        self.assertEqual(item["head"], newer[0])
        self.assertNotEqual(item["stage"], "queued", "only the head serve took is put back")

    def test_a_setup_that_breaks_during_a_batch_puts_its_members_back(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")
        restore: list[Callable[[], None]] = []

        def break_once(runner, subject) -> None:
            if not restore:
                restore.append(self.break_checkout(adopter))

        # Checked as a batch, then the setup goes before the first member lands.
        self.serve(adopter, seams={"before_batch_landing": break_once})
        self.assertEqual([adopter.item(name)["stage"] for name in ("first", "second")], ["queued", "queued"])
        self.assertIsNotNone(state_tool.read_state(adopter.state)["meta"]["hold"])
        restore[0]()
        self.release_hold(adopter)
        self.assertEqual(self.outcomes(self.serve(adopter)), [("batch", "first", "merged"), ("batch", "second", "merged")])

    def test_a_candidate_whose_merge_had_begun_is_never_put_back(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        real = queue.Runner.set_item
        restore: list[Callable[[], None]] = []

        def break_once_merging(runner, subject, **fields) -> None:
            real(runner, subject, **fields)
            if fields.get("stage") == "merging" and not restore:
                # The forge may already have the merge: putting it back could merge it twice.
                restore.append(self.break_checkout(adopter))

        with mock.patch.object(queue.Runner, "set_item", break_once_merging):
            self.serve(adopter)
        restore[0]()
        self.assertEqual(adopter.item("feature")["stage"], "stopped")
        self.assertIsNotNone(state_tool.read_state(adopter.state)["meta"]["hold"])
        [blocker] = self.blockers(adopter)
        self.assertIn("feature", blocker["summary"])

    def test_a_check_that_meets_another_runner_holds_nothing_and_stops_nothing(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        held = contextlib.ExitStack()
        self.addCleanup(held.close)
        real = queue.Runner.recover

        def lock_after_startup(runner) -> None:
            real(runner)
            if not held._exit_callbacks:
                held.enter_context(state_tool.file_lock(adopter.state / state_tool.QUEUE_LOCK))

        with mock.patch.object(queue.Runner, "recover", lock_after_startup):
            self.assertEqual(self.serve(adopter), [])
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"].get("hold"))
        self.assertEqual(adopter.item("feature")["stage"], "queued")

    def test_a_refusal_whose_cause_cannot_be_checked_stops_nothing(self) -> None:
        adopter = self.adopter()
        adopter.enqueue("first", adopter.lane_commit("first", {"src/lib.txt": "lib two\n"}), lead_inputs=["nope"])
        held = contextlib.ExitStack()
        self.addCleanup(held.close)
        real = queue.Runner.run_command

        def lock_after_the_turn(runner, *args, **kwargs):
            taken = real(runner, *args, **kwargs)
            held.enter_context(state_tool.file_lock(adopter.state / state_tool.QUEUE_LOCK))
            return taken

        with mock.patch.object(queue.Runner, "run_command", lock_after_the_turn):
            self.serve(adopter)
        held.close()
        # Another runner held the queue, so the cause is unknown: the candidate waits for the next turn.
        self.assertEqual(adopter.item("first")["stage"], "queued")
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"].get("hold"))

    def test_the_hold_s_reason_is_one_short_line(self) -> None:
        adopter = self.adopter()
        detail = "\n  fatal: unable to access 'remote': " + "word " * 100 + "\n  Could not resolve host\n"
        queue.hold_for_setup(adopter.state, detail)
        reason = state_tool.read_state(adopter.state)["meta"]["hold"]["reason"]
        self.assertNotIn("\n", reason)
        self.assertNotIn("resolve host", reason, "only the first line")
        self.assertLessEqual(len(reason), 240)
        self.assertTrue(reason.endswith("word..."), reason[-20:])
        self.assertTrue(reason.startswith("serve cannot use its setup: fatal: unable to access 'remote':"))

    def test_a_candidate_whose_check_fails_stops_alone_and_holds_nothing(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/app.txt": "FAIL\n"}, name="first")
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")
        self.serve(adopter)
        self.assertEqual((adopter.item("first")["stage"], adopter.item("second")["stage"]), ("stopped", "merged"))
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"].get("hold"))
        self.assertEqual(self.blockers(adopter), [])

    def test_a_hold_already_set_is_never_replaced_by_serve(self) -> None:
        adopter = self.adopter()
        lead_hold = {"reason": "release in progress", "since": "2026-10-21T10:00:00Z"}
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="lead", version=current["version"], changes={"meta": {"hold": lead_hold}})
        # The lead held serving while a turn ran; that turn's setup refusal leaves the lead's hold as it is.
        queue.hold_for_setup(adopter.state, "--repo must be the top level of a checkout")
        self.assertEqual(state_tool.read_state(adopter.state)["meta"]["hold"], lead_hold)
        self.assertEqual(self.blockers(adopter), [])

    def test_a_refused_candidate_stops_once_and_is_not_taken_again(self) -> None:
        adopter = self.adopter()
        # A lead input the binding does not declare: the runner refuses the candidate before doing anything.
        adopter.enqueue("first", adopter.lane_commit("first", {"src/lib.txt": "lib two\n"}), lead_inputs=["nope"])
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")
        polls: list[float] = []

        def sleep(seconds: float) -> None:
            polls.append(seconds)
            if len(polls) == 3:
                raise InterruptedError

        with self.assertRaises(InterruptedError):
            queue.serve(adopter.operator, adopter.state, remote="origin", base="main", poll=0, sleep=sleep)
        self.assertEqual(adopter.item("second")["stage"], "merged")
        # It stops, as any stop does, and waits for its lane to hand it off again: idle polls retake nothing.
        self.assertEqual(adopter.item("first")["stage"], "stopped")
        documents = [json.loads(path.read_text()) for path in (adopter.state / "results").glob("*.json")]
        self.assertEqual([document["stop"]["reason"] for document in documents if document["candidate"] == "first"],
                         ["binding_invalid"])
        stops = [entry for entry in state_tool.read_journal(adopter.state)
                 if entry["kind"] == "candidate.stopped" and entry["subject"] == "first"]
        self.assertEqual(len(stops), 1)

    def test_a_candidate_handed_off_again_after_its_refusal_keeps_its_new_head(self) -> None:
        adopter = self.adopter()
        adopter.enqueue("first", adopter.lane_commit("first", {"src/lib.txt": "lib two\n"}), lead_inputs=["nope"])
        real = queue.Runner.run_command
        newer: list[str] = []

        def refused_then_handed_again(runner, *args, **kwargs):
            taken = real(runner, *args, **kwargs)
            if not newer:
                # The lane hands it off again, at a new head, between the refusal and serve's stop.
                newer.append(adopter.lane_commit("first", {"src/lib.txt": "lib three\n"}))
                adopter.lead_set("first", head=newer[0], leadInputs=[])
            return taken

        with mock.patch.object(queue.Runner, "run_command", refused_then_handed_again):
            results = queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True,
                                  sleep=lambda seconds: None)
        self.assertEqual(results[0].document()["stop"]["reason"], "binding_invalid")
        item = adopter.item("first")
        # The new head waits to be taken on its own merits; serve's stop was for the head it refused.
        self.assertEqual((item["head"], item["stage"]), (newer[0], "queued"))

    def test_a_closed_output_on_any_system_leaves_serve_serving(self) -> None:
        # POSIX says broken pipe; Windows reports the same closed pipe as a plain invalid argument.
        for error in (BrokenPipeError(), OSError(errno.EINVAL, "Invalid argument")):
            with self.subTest(error=type(error).__name__):
                output = mock.Mock()
                output.buffer.write.side_effect = error
                with mock.patch.object(queue.sys, "stdout", output), mock.patch.object(queue.os, "open", return_value=99), \
                        mock.patch.object(queue.os, "dup2") as replaced:
                    queue.write_document_line({"outcome": "merged"})
                replaced.assert_called_once_with(99, output.fileno.return_value)

    def test_a_reader_that_goes_away_leaves_serve_serving(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        serving = subprocess.Popen(
            [PY, "-B", str(SCRIPTS / "merge_queue.py"), "--repo", str(adopter.operator), "--state", str(adopter.state),
             "--remote", "origin", "--base", "main", "serve", "--poll", "0.2"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: serving.poll() is None and serving.kill())
        serving.stdout.close()  # type: ignore[union-attr]
        deadline = time.monotonic() + 120
        while adopter.item("first")["stage"] != "merged" and serving.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(adopter.item("first")["stage"], "merged")
        # Serving goes on with nobody reading: it takes work queued after the reader left.
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")
        while adopter.item("second")["stage"] != "merged" and serving.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(adopter.item("second")["stage"], "merged")
        serving.terminate()
        _, errors = serving.communicate(timeout=60)
        # Windows' terminate gives the process no chance to exit cleanly, so its code says nothing there.
        if os.name != "nt":
            self.assertEqual(serving.returncode, 0, errors.decode("utf-8", "replace"))

    def test_serve_refuses_to_start_on_a_wrong_checkout_and_stops_nothing(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        made: list = []
        real = queue.Runner.__init__

        def recorded(runner, *args, **kwargs) -> None:
            real(runner, *args, **kwargs)
            made.append(runner)

        with mock.patch.object(queue.Runner, "__init__", recorded):
            results = queue.serve(adopter.operator / "src", adopter.state, remote="origin", base="main",
                                  until_idle=True)
        # The check leaves no hooks folder behind: serve starts often, and every start makes one.
        self.assertTrue(made)
        self.assertEqual([runner._bootstrap_hooks for runner in made if runner._bootstrap_hooks.exists()], [])
        self.assertEqual([(document["subcommand"], document["candidate"], document["outcome"], document["stop"]["reason"])
                          for document in (result.document() for result in results)],
                         [("serve", None, "refused", "binding_invalid")])
        self.assertIn("top level of a checkout", results[0].document()["stop"]["detail"])
        [written] = list((adopter.state / "results").glob("*.json"))
        self.assertTrue(written.name.endswith("-serve-setup.json"), written.name)
        # Nothing was taken: the queued candidate stays queued, with nothing journaled against it.
        self.assertEqual(adopter.item("feature")["stage"], "queued")
        self.assertNotIn("candidate.stopped", [entry["kind"] for entry in state_tool.read_journal(adopter.state)])

    def test_the_startup_check_waits_for_the_queue_lock(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        # Under the lock another runner holds, the check does not run: nothing is refused or written.
        with state_tool.file_lock(adopter.state / state_tool.QUEUE_LOCK):
            self.assertEqual(queue.serve(adopter.operator / "src", adopter.state, remote="origin", base="main",
                                         until_idle=True), [])
        self.assertFalse((adopter.state / "results").exists() and any((adopter.state / "results").iterdir()))

    def test_the_serve_command_ends_at_startup_when_its_base_cannot_be_read(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        # A base the remote does not have: serving would fail every turn, so it never starts.
        completed = subprocess.run(
            [PY, "-B", str(SCRIPTS / "merge_queue.py"), "--repo", str(adopter.operator), "--state", str(adopter.state),
             "--remote", "origin", "--base", "elsewhere", "serve", "--poll", "0.2"],
            capture_output=True, timeout=120, check=False,
        )
        self.assertEqual(completed.returncode, 1, completed.stderr.decode("utf-8", "replace"))
        [document] = [json.loads(line) for line in completed.stdout.decode("ascii").splitlines()]
        self.assertEqual((document["subcommand"], document["candidate"]), ("serve", None))
        self.assertIn(document["outcome"], ("refused", "stopped"))
        self.assertEqual(adopter.item("feature")["stage"], "queued")

    def test_a_long_serve_reports_each_result_as_it_is_written(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        reported: list = []

        def sleep(seconds: float) -> None:
            raise InterruptedError

        with self.assertRaises(InterruptedError):
            queue.serve(adopter.operator, adopter.state, remote="origin", base="main", poll=0, sleep=sleep,
                        on_result=reported.append)
        self.assertEqual(self.outcomes(reported)[-1], ("merge", "feature", "merged"))

    def test_the_serve_command_prints_each_result_and_exits_cleanly_when_stopped(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        serving = subprocess.Popen(
            [PY, "-B", str(SCRIPTS / "merge_queue.py"), "--repo", str(adopter.operator), "--state", str(adopter.state),
             "--remote", "origin", "--base", "main", "serve", "--poll", "0.2"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: serving.poll() is None and serving.kill())
        # A serve that never prints would hold the read open forever: kill it, so the test fails instead.
        deadline = threading.Timer(120, serving.kill)
        deadline.start()
        self.addCleanup(deadline.cancel)
        printed = []
        # One document a line, as each is written; a serve that ends early ends the read.
        for line in serving.stdout:  # type: ignore[union-attr]
            printed.append(json.loads(line))
            if printed[-1]["outcome"] == "merged":
                break
        serving.terminate()
        _, errors = serving.communicate(timeout=60)
        # Windows' terminate gives the process no chance to exit cleanly, so its code says nothing there.
        if os.name != "nt":
            self.assertEqual(serving.returncode, 0, errors.decode("utf-8", "replace"))
        self.assertEqual(printed[-1]["candidate"], "feature")
        self.assertEqual(printed[-1]["outcome"], "merged")

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_a_stop_that_lands_in_a_finalizer_still_stops_serve(self) -> None:
        # Python only prints what a finalizer raises, and serve builds a runner each turn, whose hooks folder goes in one.
        code = textwrap.dedent("""
            import os, shutil, signal, sys
            sys.path.insert(0, sys.argv[1])
            import merge_queue
            real, sent, stop = shutil.rmtree, [], getattr(signal, sys.argv[2])
            # However the suite was started: serve keeps a SIGINT it inherits as ignored.
            signal.signal(signal.SIGINT, signal.default_int_handler)
            def rmtree(path, *args, **kwargs):
                # Only from the finalizer: the same folder also goes by a plain call, where a stop arrives as usual.
                if "weakref" in sys._getframe(1).f_code.co_filename and not sent:
                    sent.append(True)
                    os.kill(os.getpid(), stop)
                return real(path, *args, **kwargs)
            shutil.rmtree = rmtree
            sys.argv = ["merge_queue.py", *sys.argv[3:]]
            sys.exit(merge_queue.main())
        """)
        adopter = self.adopter()
        for name in ("SIGTERM", "SIGINT"):
            with self.subTest(signal=name):
                done = subprocess.run(
                    [PY, "-B", "-c", code, str(SCRIPTS), name, "--repo", str(adopter.operator), "--state", str(adopter.state),
                     "--remote", "origin", "--base", "main", "serve", "--poll", "0.2"],
                    capture_output=True, timeout=60,
                )
                self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_a_stop_that_lands_in_a_finalizer_takes_no_queued_candidate(self) -> None:
        code = textwrap.dedent("""
            import os, shutil, signal, sys
            sys.path.insert(0, sys.argv[1])
            import merge_queue
            real, sent = shutil.rmtree, []
            def rmtree(path, *args, **kwargs):
                if "weakref" in sys._getframe(1).f_code.co_filename and not sent:
                    sent.append(True)
                    os.kill(os.getpid(), signal.SIGTERM)
                return real(path, *args, **kwargs)
            shutil.rmtree = rmtree
            sys.argv = ["merge_queue.py", *sys.argv[2:]]
            sys.exit(merge_queue.main())
        """)
        adopter = self.adopter()
        self.candidate(adopter)
        done = subprocess.run(
            [PY, "-B", "-c", code, str(SCRIPTS), "--repo", str(adopter.operator), "--state", str(adopter.state),
             "--remote", "origin", "--base", "main", "serve", "--poll", "0.2"],
            capture_output=True, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(done.stdout, b"")
        self.assertEqual([item["stage"] for item in state_tool.read_state(adopter.state)["queue"]], ["queued"])

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_serve_started_with_sigint_ignored_keeps_ignoring_it_and_stops_on_sigterm(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        # As a shell starts a background job: the ignore is inherited through exec.
        serving = subprocess.Popen(
            ["sh", "-c", 'trap "" INT; exec "$@"', "sh", PY, "-B", str(SCRIPTS / "merge_queue.py"),
             "--repo", str(adopter.operator), "--state", str(adopter.state), "--remote", "origin", "--base", "main",
             "serve", "--poll", "0.2"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: serving.poll() is None and serving.kill())
        deadline = threading.Timer(120, serving.kill)
        deadline.start()
        self.addCleanup(deadline.cancel)
        # A document means its handlers are set, and the merge means serve is about to wait.
        for line in serving.stdout:  # type: ignore[union-attr]
            if json.loads(line)["outcome"] == "merged":
                break
        serving.send_signal(signal.SIGINT)
        time.sleep(1.5)
        self.assertIsNone(serving.poll(), "serve stopped on a SIGINT it inherited as ignored")
        serving.terminate()
        _, errors = serving.communicate(timeout=60)
        self.assertEqual(serving.returncode, 0, errors.decode("utf-8", "replace"))

    def test_every_queue_stopping_stop_is_journaled_as_a_broken_base(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        os.environ["MQ_MERGE"] = "unverified"
        self.addCleanup(os.environ.pop, "MQ_MERGE", None)
        results = self.serve(adopter)
        self.assertEqual(results[-1].document()["stop"]["reason"], "merged_unverified")
        kinds = [entry["kind"] for entry in state_tool.read_journal(adopter.state)]
        self.assertIn("base.broken", kinds)

    def test_a_killed_serve_restarts_clean_and_merges_nothing_twice(self) -> None:
        adopter = self.adopter({"alwaysRun": [check_entry("hang", "--hang-once")]})
        self.candidate(adopter)
        hang = Path(str(adopter.log) + ".hang")
        started = Path(str(adopter.log) + ".started")
        hang.write_text("")
        self.addCleanup(hang.unlink, True)
        serving = subprocess.Popen(
            [PY, "-B", str(SCRIPTS / "merge_queue.py"), "--repo", str(adopter.operator), "--state", str(adopter.state),
             "--remote", "origin", "--base", "main", "serve", "--poll", "0.2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # Killed below, so it cannot remove its temp folder: this test's own folder takes that with it.
            env={**os.environ, "TMPDIR": str(self.directory)},
        )
        deadline = time.monotonic() + 120
        while not started.exists() and serving.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(started.exists(), "the check never started")
        serving.kill()
        serving.wait()
        hang.unlink()
        results = self.serve(adopter)
        item = adopter.item("feature")
        self.assertNotIn("running", [check["state"] for check in item["checks"]])
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"]["checking"]["now"])
        # The interrupted candidate stops; it waits for its lane to hand it off again, never merging twice.
        self.assertEqual(item["stage"], "stopped")
        self.assertEqual(results, [])
        merges = [entry for entry in state_tool.read_journal(adopter.state) if entry["kind"] == "candidate.merged"]
        self.assertEqual(merges, [])


SOURCE_TOOL = """
import datetime, json, sys
source, world = sys.argv[1], sys.argv[2]
with open(world + ".runs", "a") as runs:
    runs.write("run\\n")
seen = json.load(open(world))
if seen["mode"] == "fail":
    print("refused", file=sys.stderr); sys.exit(3)
taken = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps({"takenAt": taken, "source": source, "items": seen["items"]}))
"""


class ServeRefreshTests(MergeQueueTestCase):
    """serve reads the binding's declared forge and tracker snapshots on its interval and reconciles them."""

    def setUp(self) -> None:
        super().setUp()
        self.tools = Path(tempfile.mkdtemp(prefix="merge-queue-sources-")).resolve()
        self.addCleanup(shutil.rmtree, self.tools, True)
        (self.tools / "source.py").write_text(SOURCE_TOOL)
        self.worlds = {source: self.tools / f"{source}.json" for source in ("tracker", "forge")}
        for source in self.worlds:
            self.world(source, [])

    def world(self, source: str, items: list[dict], mode: str = "ok") -> None:
        self.worlds[source].write_text(json.dumps({"mode": mode, "items": items}))

    def runs(self, source: str) -> int:
        path = Path(str(self.worlds[source]) + ".runs")
        return len(path.read_text().splitlines()) if path.exists() else 0

    def refreshing(self, *sources: str, **extra: object) -> Adopter:
        member = {"directory": str(self.directory / "state"), "renderers": [], "publishers": [], **extra}
        for source in sources:
            member[f"{source}Snapshot"] = {"argv": [PY, "-B", str(self.tools / "source.py"), source,
                                                    str(self.worlds[source])], "timeoutSeconds": 10}
        return self.adopter({"state": member})

    def serve_for(self, adopter: Adopter, ticks: int, *, poll: float = 30.0,
                  between: Callable[[int, float], None] | None = None) -> list[float]:
        """Serve on a fake clock until the ``ticks``-th sleep; ``between`` runs at each sleep, with its count and time."""
        moment = [0.0]
        slept: list[float] = []

        def sleep(seconds: float) -> None:
            moment[0] += seconds
            slept.append(seconds)
            if between is not None:
                between(len(slept), moment[0])
            if len(slept) >= ticks:
                raise InterruptedError

        with self.assertRaises(InterruptedError):
            queue.serve(adopter.operator, adopter.state, remote="origin", base="main", poll=poll, sleep=sleep,
                        clock=lambda: moment[0])
        return slept

    def hold(self, adopter: Adopter) -> None:
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="lead", version=current["version"],
                         changes={"meta": {"hold": {"reason": "watching", "since": "2026-10-21T10:00:00Z"}}})

    def sources(self, adopter: Adopter) -> dict | None:
        return state_tool.read_state(adopter.state)["meta"].get("sources")

    def footprint(self, adopter: Adopter) -> tuple:
        return state_tool.read_state(adopter.state)["version"], len(state_tool.read_journal(adopter.state))

    def test_a_forge_merge_and_a_tracker_change_reach_the_record_within_one_interval(self) -> None:
        adopter = self.refreshing("tracker", "forge", refreshSeconds=100)
        head = self.candidate(adopter)
        # Held, so the only way the record learns of the merge is the forge.
        self.hold(adopter)
        refreshed_at: list[float] = []

        def change(count: int, moment: float) -> None:
            if count == 1:
                self.world("forge", [{"subject": "feature", "state": "merged", "head": head}])
                self.world("tracker", [{"subject": "work-t", "title": "Ticket", "state": "started"}])
            if self.runs("forge") > len(refreshed_at):
                refreshed_at.append(moment)

        slept = self.serve_for(adopter, 6, between=change)
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        self.assertEqual([entry["subject"] for entry in state_tool.read_state(adopter.state)["planned"]], ["work-t"])
        # Idle polls stop short of the interval, so the next read is on time.
        self.assertEqual(slept[:4], [30.0, 30.0, 30.0, 10.0])
        self.assertEqual((self.runs("tracker"), self.runs("forge")), (2, 2))
        self.assertEqual({source: entry["status"] for source, entry in self.sources(adopter).items()},
                         {"tracker": "observed", "forge": "observed"})

    def test_an_unchanged_refresh_writes_nothing(self) -> None:
        adopter = self.refreshing("tracker", "forge", refreshSeconds=30)
        self.world("tracker", [{"subject": "work-t", "title": "Ticket", "state": "started"}])
        after_first: list[tuple] = []
        self.serve_for(adopter, 5, between=lambda count, moment: after_first.append(self.footprint(adopter))
                       if count == 1 else None)
        self.assertEqual(self.runs("tracker"), 5)
        self.assertEqual(self.footprint(adopter), after_first[0])

    def test_the_default_interval_is_three_minutes(self) -> None:
        adopter = self.refreshing("tracker")
        self.serve_for(adopter, 8)
        self.assertEqual(self.runs("tracker"), 2, "read at the start and again at 180s, not at 240s")

    def test_an_interval_of_zero_turns_the_refresh_off(self) -> None:
        adopter = self.refreshing("tracker", "forge", refreshSeconds=0)
        self.candidate(adopter)
        queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        self.assertEqual((self.runs("tracker"), self.runs("forge")), (0, 0))
        self.assertIsNone(self.sources(adopter))

    def test_a_failing_source_never_stops_the_queue(self) -> None:
        adopter = self.refreshing("tracker", refreshSeconds=100)
        self.world("tracker", [], mode="fail")
        self.candidate(adopter)
        queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        tracker = self.sources(adopter)["tracker"]
        self.assertEqual((tracker["status"], tracker["lastReadAt"]), ("stale", None))
        self.assertEqual(self.sources(adopter)["forge"]["status"], "not_observed")

    def test_a_refresh_that_raises_is_reported_and_serving_goes_on(self) -> None:
        adopter = self.refreshing("tracker", refreshSeconds=100)
        self.candidate(adopter)
        broken = state_tool.StateError("state_invalid", "the record cannot be read")
        with mock.patch.object(queue.delivery_state, "refresh_sources", side_effect=broken), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as errors:
            queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        self.assertIn("the record cannot be read", errors.getvalue())

    def test_a_merge_the_forge_reports_is_recorded_before_the_queue_is_read(self) -> None:
        adopter = self.refreshing("forge", refreshSeconds=100)
        head = self.candidate(adopter)
        self.world("forge", [{"subject": "feature", "state": "merged", "head": head}])
        results = queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        # Recorded from the forge first, so serve never takes work that already landed.
        self.assertEqual(results, [])
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        self.assertNotIn("forge merge", " ".join(adopter.log_lines()))

    def test_serve_does_not_start_on_a_snapshot_block_or_interval_it_cannot_use(self) -> None:
        for name, extra, word in (("timeout", {"forgeSnapshot": {"argv": ["tool"], "timeoutSeconds": 900}}, "forgeSnapshot"),
                                  ("interval", {"refreshSeconds": 86401}, "refreshSeconds")):
            with self.subTest(name):
                self.fresh()
                adopter = self.refreshing("tracker", **extra)
                self.candidate(adopter)
                [refused] = queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
                document = refused.document()
                self.assertEqual((document["subcommand"], document["stop"]["reason"]), ("serve", "binding_invalid"))
                self.assertIn(word, document["stop"]["detail"])
                self.assertEqual(adopter.item("feature")["stage"], "queued")

    def test_a_binding_without_sources_reports_both_as_not_observed(self) -> None:
        adopter = self.refreshing()
        queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertEqual({source: entry["status"] for source, entry in self.sources(adopter).items()},
                         {"tracker": "not_observed", "forge": "not_observed"})


class InterruptedRunTests(MergeQueueTestCase):
    """A run killed outright leaves live state behind; the next run to take the queue lock clears it first."""

    def seed_running(self, adopter: Adopter, subject: str = "feature") -> None:
        """What a run killed mid-check leaves: the panel and the row reading running, the stage checking."""
        current = state_tool.read_state(adopter.state)
        state_tool.patch(
            adopter.state, writer="runner", version=current["version"], selector=f"queue[subject={subject}]",
            assignments={"/stage": "checking", "/checks": [{"id": "a", "state": "passed"}, {"id": "b", "state": "running"}]},
        )
        current = state_tool.read_state(adopter.state)
        state_tool.patch(
            adopter.state, writer="runner", version=current["version"],
            assignments={"/meta/checking/now": {
                "subject": subject, "title": subject, "startedAt": "2026-10-20T00:00:00Z",
                "steps": [{"label": "a", "state": "passed"}, {"label": "b", "state": "running"}],
            }},
        )

    def interruptions(self, adopter: Adopter) -> list[dict]:
        return [
            entry for entry in state_tool.read_journal(adopter.state)
            if entry["kind"] == "candidate.stopped" and "interrupted" in entry["summary"]
        ]

    def test_the_next_run_clears_what_a_killed_run_left_before_anything_else(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        self.seed_running(adopter)
        patched_writes: list[dict] = []
        real = state_tool.patch

        def first_write(directory, **kwargs):
            written = real(directory, **kwargs)
            patched_writes.append(written)
            return written

        with mock.patch.object(queue.delivery_state, "patch", first_write):
            # The release subcommand never touches the candidate; the reset happens anyway.
            adopter.runner().run_command("release", ["v1"])
        cleared = patched_writes[0]
        self.assertIsNone(cleared["meta"]["checking"]["now"])
        item = next(element for element in cleared["queue"] if element["subject"] == "feature")
        self.assertEqual(item["checks"], [{"id": "a", "state": "passed"}, {"id": "b", "state": "pending"}])
        self.assertEqual(item["stage"], "stopped")
        [entry] = self.interruptions(adopter)
        self.assertEqual(entry["subject"], "feature")

    def seed(self, adopter: Adopter, stage: str, checks: list[dict], panel: str | None) -> None:
        current = state_tool.read_state(adopter.state)
        state_tool.patch(
            adopter.state, writer="runner", version=current["version"], selector="queue[subject=feature]",
            assignments={"/stage": stage, "/checks": checks},
        )
        if panel is not None:
            current = state_tool.read_state(adopter.state)
            state_tool.patch(
                adopter.state, writer="runner", version=current["version"],
                assignments={"/meta/checking/now": {
                    "subject": panel, "title": panel, "startedAt": "2026-10-20T00:00:00Z", "steps": [],
                }},
            )

    def test_an_item_killed_mid_step_stops_whatever_the_step(self) -> None:
        passed = [{"id": "a", "state": "passed"}]
        for name, stage, panel, summary in (
            # merge writes no running check and no panel; only the stage shows it.
            ("killed while merging", "merging", None, "check the forge"),
            ("killed while syncing", "syncing", None, "interrupted"),
            # The panel alone names it: the run died between steps while working on it.
            ("named only by the panel", "checked", "feature", "interrupted"),
        ):
            with self.subTest(name):
                self.fresh()
                adopter = self.adopter()
                self.candidate(adopter)
                self.seed(adopter, stage, passed, panel)
                adopter.runner().run_command("release", ["v1"])
                self.assertEqual(adopter.item("feature")["stage"], "stopped")
                self.assertEqual(adopter.item("feature")["checks"], passed)
                self.assertIsNone(state_tool.read_state(adopter.state)["meta"]["checking"]["now"])
                [entry] = self.interruptions(adopter)
                self.assertIn(summary, entry["summary"])

    def test_the_interruption_is_journalled_before_the_state_is_cleared(self) -> None:
        # A second failure between the two repeats the entry at the next run; it never loses it.
        adopter = self.adopter()
        self.candidate(adopter)
        self.seed_running(adopter)
        real = queue.Runner.write
        failed: list[int] = []

        def write_fails_once(runner, **kwargs):
            if not failed:
                failed.append(1)
                raise OSError("disk went away")
            return real(runner, **kwargs)

        with mock.patch.object(queue.Runner, "write", write_fails_once):
            adopter.runner().run_command("release", ["v1"])
        self.assertEqual(len(self.interruptions(adopter)), 1)
        self.assertEqual(adopter.item("feature")["stage"], "checking")
        adopter.runner().run_command("release", ["v1"])
        self.assertEqual(len(self.interruptions(adopter)), 2)
        self.assertEqual(adopter.item("feature")["stage"], "stopped")

    def test_a_panel_naming_no_queued_item_is_cleared_without_a_journal_entry(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        self.seed(adopter, "queued", [], "gone")
        adopter.runner().run_command("release", ["v1"])
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"]["checking"]["now"])
        self.assertEqual(adopter.item("feature")["stage"], "queued")
        self.assertEqual(self.interruptions(adopter), [])

    def test_a_row_left_running_on_its_own_is_cleared(self) -> None:
        # The stage already reads stopped and the panel is clear; only the row still says running.
        adopter = self.adopter()
        self.candidate(adopter)
        current = state_tool.read_state(adopter.state)
        state_tool.patch(
            adopter.state, writer="runner", version=current["version"], selector="queue[subject=feature]",
            assignments={"/stage": "stopped", "/checks": [{"id": "b", "state": "running"}]},
        )
        adopter.runner().run_command("release", ["v1"])
        self.assertEqual(adopter.item("feature")["checks"], [{"id": "b", "state": "pending"}])
        self.assertEqual(len(self.interruptions(adopter)), 1)

    def test_a_clean_state_is_left_alone(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        before = state_tool.read_state(adopter.state)
        adopter.runner().run_command("release", ["v1"])
        self.assertEqual(self.interruptions(adopter), [])
        self.assertEqual(adopter.item("feature"), next(item for item in before["queue"] if item["subject"] == "feature"))

    def test_a_run_that_still_holds_the_lock_is_never_reset(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        self.seed_running(adopter)
        before = state_tool.read_state(adopter.state)
        with state_tool.file_lock(adopter.state / state_tool.QUEUE_LOCK):
            results = adopter.runner().run_command("run", ["feature"])
        self.assertEqual(results[-1].document()["stop"]["reason"], "queue_locked")
        self.assertEqual(state_tool.read_state(adopter.state), before)
        self.assertEqual(self.interruptions(adopter), [])

    def test_a_runner_killed_mid_check_is_cleared_by_the_next_run(self) -> None:
        adopter = self.adopter({"alwaysRun": [check_entry("hang", "--hang-once")]})
        self.candidate(adopter)
        hang = Path(str(adopter.log) + ".hang")
        started = Path(str(adopter.log) + ".started")
        hang.write_text("")
        self.addCleanup(hang.unlink, True)
        runner = subprocess.Popen(
            [PY, "-B", str(SCRIPTS / "merge_queue.py"), "--repo", str(adopter.operator), "--state", str(adopter.state),
             "--remote", "origin", "--base", "main", "run", "feature"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # Killed below, so it cannot remove its temp folder: this test's own folder takes that with it.
            env={**os.environ, "TMPDIR": str(self.directory)},
        )
        deadline = time.monotonic() + 120
        while not started.exists() and runner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(started.exists(), "the check never started")
        runner.kill()  # SIGKILL on POSIX, TerminateProcess on Windows: no cleanup runs.
        runner.wait()
        item = adopter.item("feature")
        self.assertIn({"id": "hang", "state": "running"}, item["checks"])
        self.assertEqual(item["stage"], "checking")
        self.assertIsNotNone(state_tool.read_state(adopter.state)["meta"]["checking"]["now"])
        # The orphaned check finishes once the flag is gone.
        hang.unlink()
        results = self.run_queue(adopter, "feature")
        [entry] = self.interruptions(adopter)
        kinds = [(item["kind"], item["summary"]) for item in state_tool.read_journal(adopter.state)]
        self.assertLess(kinds.index((entry["kind"], entry["summary"])),
                        max(index for index, (kind, _) in enumerate(kinds) if kind == "check.started"))
        final = adopter.item("feature")
        self.assertNotIn("running", [check["state"] for check in final["checks"]])
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"]["checking"]["now"])
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])


class RenderingTests(MergeQueueTestCase):
    def wrapper(self, exit_code: int) -> Path:
        script = self.directory.parent / f"{self.directory.name}-render_state.py"
        calls = self.directory.parent / f"{self.directory.name}-render-calls.jsonl"
        script.write_text(
            "import json, sys\n"
            f"with open({str(calls)!r}, 'a') as log:\n"
            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            f"sys.exit({exit_code})\n"
        )
        self.addCleanup(script.unlink, missing_ok=True)
        self.addCleanup(calls.unlink, missing_ok=True)
        original = queue.RENDER_WRAPPER
        queue.RENDER_WRAPPER = script
        self.addCleanup(setattr, queue, "RENDER_WRAPPER", original)
        return calls

    def test_the_wrapper_runs_after_state_writes_with_the_bindings_state(self) -> None:
        adopter = self.adopter({"state": {
            "directory": "state", "renderers": [{"id": "page", "builtin": "static-page", "output": "view/index.html"}],
            "publishers": [],
        }})
        calls = self.wrapper(0)
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged")
        recorded = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertGreater(len(recorded), 3)
        for argv in recorded:
            self.assertEqual(argv[:2], ["--dir", str(adopter.state)])
            self.assertEqual(argv[2], "--state-config")
            self.assertEqual(json.loads(argv[3]), adopter.binding["state"])
        self.assertEqual(results[-1].document()["notes"], [])

    def test_the_skills_own_wrapper_renders_the_bindings_views(self) -> None:
        state = {
            "directory": "state", "renderers": [{"id": "summary", "builtin": "markdown", "output": "views/summary.md"}],
            "publishers": [],
        }
        adopter = self.adopter({"state": state})
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged")
        self.assertEqual(results[-1].document()["notes"], [])
        self.assertIn("Candidate feature", (adopter.state / "views" / "summary.md").read_text(encoding="utf-8"))

    def test_a_failing_wrapper_is_noted_and_never_fails_the_runner(self) -> None:
        adopter = self.adopter({"state": {
            "directory": "state", "renderers": [{"id": "page", "builtin": "static-page", "output": "view/index.html"}],
            "publishers": [],
        }})
        self.wrapper(1)
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged")
        # Noted once, however many writes it failed after.
        self.assertEqual(results[-1].document()["notes"].count("rendering the state exited 1"), 1)

    def test_a_missing_wrapper_renders_nothing_and_notes_nothing(self) -> None:
        adopter = self.adopter({"state": {
            "directory": "state", "renderers": [{"id": "page", "builtin": "static-page", "output": "view/index.html"}],
            "publishers": [],
        }})
        original = queue.RENDER_WRAPPER
        queue.RENDER_WRAPPER = self.directory.parent / f"{self.directory.name}-no-render_state.py"
        self.addCleanup(setattr, queue, "RENDER_WRAPPER", original)
        self.candidate(adopter)
        counting, started = self.wrapper_runs()
        with counting:
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged")
        self.assertEqual((started, results[-1].document()["notes"]), ([], []))

    def wrapper_runs(self) -> tuple[object, list[list[str]]]:
        """Count the render wrapper processes the runner starts, and still start them."""
        original = queue.run_process
        started: list[list[str]] = []

        def counting(argv, **options):
            if str(queue.RENDER_WRAPPER) in argv:
                started.append(list(argv))
            return original(argv, **options)

        return mock.patch.object(queue, "run_process", side_effect=counting), started

    def record_bytes(self, adopter: Adopter) -> dict[str, bytes]:
        return {name: (adopter.state / name).read_bytes()
                for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE, state_tool.CHANGES_FILE)
                if (adopter.state / name).exists()}

    def test_with_no_views_a_whole_run_starts_no_wrapper_and_leaves_the_same_record(self) -> None:
        adopter = self.adopter()
        self.assertEqual((adopter.binding["state"]["renderers"], adopter.binding["state"]["publishers"]), ([], []))
        self.candidate(adopter)
        counting, started = self.wrapper_runs()
        with counting:
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document())
        self.assertEqual(started, [])
        self.assertEqual(results[-1].document()["notes"], [])
        # The wrapper itself, given the same configuration, writes nothing: no view,
        # no outbox, and not a byte of the record, the journal or the change log.
        self.assertTrue(self.record_bytes(adopter))
        before = self.record_bytes(adopter)
        listed = sorted(path.name for path in adopter.state.iterdir())
        done = subprocess.run([PY, "-B", str(queue.RENDER_WRAPPER), "--dir", str(adopter.state),
                               "--state-config", json.dumps(adopter.binding["state"])],
                              capture_output=True, check=False)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(json.loads(done.stdout), {"ok": True, "publishers": [], "renderers": []})
        self.assertEqual(self.record_bytes(adopter), before)
        self.assertEqual(sorted(path.name for path in adopter.state.iterdir()), listed)

    def test_a_declared_renderer_or_publisher_still_starts_the_wrapper(self) -> None:
        for name, state in (
            ("a renderer", {"directory": "state", "publishers": [],
                            "renderers": [{"id": "summary", "builtin": "markdown", "output": "views/summary.md"}]}),
            ("a publisher only", {"directory": "state", "renderers": [],
                                  "publishers": [{"id": "store", "argv": [PY, "-c", "pass"]}]}),
        ):
            with self.subTest(name):
                self.fresh()
                adopter = self.adopter({"state": state})
                self.candidate(adopter)
                counting, started = self.wrapper_runs()
                with counting:
                    results = self.run_queue(adopter, "feature")
                self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document())
                self.assertGreater(len(started), 3)
                self.assertTrue(all(json.loads(argv[argv.index("--state-config") + 1]) == state for argv in started))
                self.assertEqual(results[-1].document()["notes"], [])

    def test_with_no_views_what_the_wrapper_checks_is_still_checked_in_process(self) -> None:
        adopter = self.adopter()
        runner = adopter.runner()
        runner.binding = copy.deepcopy(adopter.binding)
        counting, started = self.wrapper_runs()
        with counting:
            # A record that cannot be read is noted as the wrapper's refusal was.
            with mock.patch.object(queue.delivery_state, "show",
                                   side_effect=state_tool.StateError("state_invalid", "unreadable")):
                runner.render()
                runner.render()
            self.assertEqual(runner.notes, ["rendering the state exited 2"])
            # A record the wrapper could not serialize is noted as its crash was.
            runner.notes = []
            with mock.patch.object(queue.delivery_state, "dumps", side_effect=UnicodeEncodeError("utf-8", "\ud800", 0, 1, "x")):
                runner.render()
            self.assertEqual(runner.notes, ["rendering the state exited 1"])
            # A readable record and a sound configuration leave nothing to note.
            runner.notes = []
            runner.render()
            self.assertEqual(runner.notes, [])
        self.assertEqual(started, [])
        # A configuration the wrapper would refuse, or one that cannot be read, still goes to the wrapper.
        for name, change in (("an unknown field", lambda state: state.update(colour="blue")),
                             ("an unreadable schema", None)):
            with self.subTest(name):
                runner.notes = []
                runner.binding = copy.deepcopy(adopter.binding)
                counting, started = self.wrapper_runs()
                if change is not None:
                    change(runner.binding["state"])
                    with counting:
                        runner.render()
                    self.assertEqual(runner.notes, ["rendering the state exited 2"])
                else:
                    with counting, mock.patch.object(queue.render_state, "parse_config", side_effect=OSError("gone")):
                        runner.render()
                    self.assertEqual(runner.notes, [])
                self.assertEqual(len(started), 1)

    def test_the_state_member_takes_the_wrappers_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binding = Adopter(Path(temporary)).binding
        binding_schema = schema("orchestration-binding")
        schema_module = state_tool
        reference = Draft202012Validator(binding_schema)
        cases = {
            "builtin": ({"id": "md", "builtin": "markdown", "output": "view.md"}, True),
            "argv": ({"id": "own", "argv": [PY, "render.py"], "output": "views/own.txt", "timeoutSeconds": 5}, True),
            "both": ({"id": "x", "builtin": "markdown", "argv": [PY], "output": "x.md"}, False),
            "neither": ({"id": "x", "output": "x.md"}, False),
            "parent": ({"id": "x", "builtin": "markdown", "output": "../x.md"}, False),
            "nested parent": ({"id": "x", "builtin": "markdown", "output": "a/../../x.md"}, False),
            "absolute": ({"id": "x", "builtin": "markdown", "output": "/tmp/x.md"}, False),
            "unknown builtin": ({"id": "x", "builtin": "pdf", "output": "x.pdf"}, False),
        }
        for name, (renderer, valid) in cases.items():
            with self.subTest(name):
                document = {**binding, "state": {"directory": "state", "renderers": [renderer], "publishers": []}}
                self.assertEqual(reference.is_valid(document), valid)
                self.assertEqual(not schema_module.validate(document, binding_schema), valid)
        publisher = {**binding, "state": {"directory": "state", "renderers": [], "publishers": [{"id": "store", "argv": [PY, "p.py"]}]}}
        self.assertTrue(reference.is_valid(publisher))


class ContractTests(unittest.TestCase):
    def test_schemas_are_valid_and_pass_the_leakage_scan(self) -> None:
        for name in ("orchestration-binding", "affected-checks", "merge-queue-result"):
            document = schema(name)
            Draft202012Validator.check_schema(document)
            self.assertNotIn("$schema", document)
            self.assertTrue(document["$id"].startswith("urn:"))
            self.assertEqual(document["title"], f"{name}/1")
        for path in (SCHEMA_FILE, SCRIPTS / "merge_queue.py"):
            text = path.read_text(encoding="utf-8")
            for class_id, pattern in LEAKAGE_PATTERNS:
                self.assertIsNone(pattern.search(text), f"{path.name}: {class_id}")

    def test_the_bundled_validator_agrees_with_the_reference_on_bindings_and_answers(self) -> None:
        schema_module = state_tool
        with tempfile.TemporaryDirectory() as temporary:
            binding = Adopter(Path(temporary)).binding
        samples = {
            "orchestration-binding": [binding, {**binding, "hooks": "maybe"}, {**binding, "merge": "not argv"}],
            "affected-checks": [
                {"checks": [check_entry("a")], "widened": False, "uncovered": []},
                {"checks": [{"id": "a"}], "widened": False, "uncovered": []},
                {"checks": [], "widened": "no", "uncovered": []},
            ],
        }
        for name, documents in samples.items():
            named = schema(name)
            reference = Draft202012Validator(named)
            for document in documents:
                self.assertEqual(not schema_module.validate(document, named), reference.is_valid(document), (name, document))

    def test_a_merge_argv_without_expected_head_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binding = Adopter(Path(temporary)).binding
        binding["merge"] = [PY, "tools/forge.py", "merge", "{branch}", "{head}"]
        self.assertTrue(any("{expectedHead}" in error for error in queue.binding_errors(binding)))
        binding["merge"] = [PY, "tools/forge.py", "merge", "--expected={expectedHead}"]
        self.assertTrue(any("{expectedHead}" in error for error in queue.binding_errors(binding)))

    def test_an_unknown_placeholder_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binding = Adopter(Path(temporary)).binding
        binding["ready"] = [PY, "tools/forge.py", "ready", "{nope}"]
        self.assertTrue(any("{nope}" in error for error in queue.binding_errors(binding)))

    def test_placeholders_replace_whole_elements_only(self) -> None:
        context = queue.Context({"head": "h" * 40})
        self.assertEqual(context.argv(["x", "{head}", "--at={head}", "{head}x"]), ["x", "h" * 40, "--at={head}", "{head}x"])
        with self.assertRaises(queue.Stop):
            context.argv(["{baseHead}"])

    def test_no_bytecode_is_written_beside_the_scripts(self) -> None:
        self.assertEqual(list(SCRIPTS.rglob("__pycache__")), [])
        self.assertEqual(list(SCRIPTS.rglob("*.pyc")), [])



class ReusedCheckTests(MergeQueueTestCase):
    """A check the binding marks reusable is not run again on a tree the lane already checked and passed."""

    REUSING = {"postSync": [], "alwaysRun": [check_entry("always"), check_entry("full")], "reusableChecks": ["full"]}

    def reusing(self, **changes: object) -> Adopter:
        return self.adopter({**self.REUSING, **changes})

    def hand_off(self, adopter: Adopter, subject: str, head: str, *, evidence: list[dict] | None = None,
                 checked: str | None = None) -> None:
        """The lane hands its candidate off through the tool, with the evidence of its own full run."""
        state = state_tool.read_state(adopter.state)
        lanes = state["lanes"] or [{"id": "lane-1", "title": "Lane 1", "focus": "", "state": "working",
                                    "waitingOn": None, "since": state_tool.now()}]
        known = [entry["subject"] for entry in [*state.get("planned", []), *state["queue"]]]
        planned = [*state.get("planned", []), *([] if subject in known else [
            {"subject": subject, "title": f"Candidate {subject}", "lane": "lane-1", "stage": "review"}])]
        state_tool.patch(adopter.state, writer="lead", version=state["version"],
                         changes={"lanes": lanes, "planned": planned})
        if evidence is None:
            evidence = [queue.evidence(adopter.lane, remote="origin", base="main", check="full", head=checked or head)]
        state_tool.handoff(adopter.state, lane="lane-1", subject=subject, head=head, change_request="cr",
                           evidence=evidence)

    def runs(self, adopter: Adopter, name: str) -> int:
        return sum(1 for line in adopter.log_lines() if line.split()[:2] == ["check", name])

    def check_document(self, results: list) -> dict:
        return next(result.document() for result in results if result.document()["subcommand"] == "check")

    def test_a_lane_head_that_already_contains_the_tip_reuses_the_lanes_full_run(self) -> None:
        adopter = self.reusing()
        head = adopter.lane_commit("feature", {"tests/test_new.txt": "ok\n"})
        self.hand_off(adopter, "feature", head)
        # The full check's usual duration is known, but a reused run has none to show.
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="runner", version=current["version"], assignments={
            "/meta/checkHistory": {"checks": {"full": {"seconds": [50, 60], "flakes": 0}}, "runs": []}})
        real = state_tool.patch
        panels: list[list[dict]] = []

        def recording(directory, **kwargs):
            written = real(directory, **kwargs)
            now = written["meta"]["checking"]["now"]
            if kwargs.get("writer") == "runner" and now is not None:
                panels.append(copy.deepcopy(now["steps"]))
            return written

        with mock.patch.object(queue.delivery_state, "patch", recording):
            results = self.run_queue(adopter, "feature")
        for document in (result.document() for result in results):
            Draft202012Validator(schema("merge-queue-result")).validate(document)
        shown = [step for steps in panels for step in steps if step["label"] == "full" and step["state"] == "reused"]
        self.assertTrue(shown)
        self.assertTrue(all(step["reusedFrom"] == head and "expectedSeconds" not in step for step in shown), shown)
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document())
        # The quick checks still run; the full one does not.
        self.assertEqual((self.runs(adopter, "full"), self.runs(adopter, "always")), (0, 1))
        self.assertEqual(self.runs(adopter, "test:tests/test_new.txt"), 1)
        [reused] = [check for check in self.check_document(results)["checks"] if check["id"] == "full"]
        self.assertEqual(reused["state"], "reused")
        self.assertEqual(reused["reusedFrom"], {"subject": "feature", "head": head,
                                                "tree": run("rev-parse", f"{head}^{{tree}}", cwd=adopter.lane)})
        [row] = [check for check in adopter.item("feature")["checks"] if check["id"] == "full"]
        self.assertEqual(row, {"id": "full", "state": "reused", "reusedFrom": head})
        [journaled] = [entry for entry in state_tool.read_journal(adopter.state) if entry["kind"] == "check.reused"]
        self.assertIn(f"reused from the lane's run at {head}", journaled["summary"])
        # The merge is still pinned to the synced head, whose tree is the one the lane checked.
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        self.assertEqual(run("rev-parse", f"{adopter.tip('main')}^{{tree}}", cwd=adopter.lane),
                         reused["reusedFrom"]["tree"])
        # A reused check adds nothing to the check history, and a run that reused one is not a whole run.
        history = state_tool.read_state(adopter.state)["meta"]["checkHistory"]
        self.assertEqual(history["checks"]["full"]["seconds"], [50, 60])
        self.assertEqual(history["runs"], [])

    def test_any_difference_in_tree_definition_or_result_runs_the_check(self) -> None:
        def tip_moved(adopter: Adopter, head: str) -> None:
            self.hand_off(adopter, "feature", head)
            adopter.advance_base({"notes/base.txt": "moved\n"})

        def pushed_after(adopter: Adopter, head: str) -> None:
            later = adopter.lane_commit("feature", {"tests/test_more.txt": "ok\n"})
            self.hand_off(adopter, "feature", later, checked=head)

        def other_definition(adopter: Adopter, head: str) -> None:
            found = queue.evidence(adopter.lane, remote="origin", base="main", check="full", head=head)
            self.hand_off(adopter, "feature", head, evidence=[{**found, "definition": "sha256:" + "0" * 64}])

        def failing(adopter: Adopter, head: str) -> None:
            found = queue.evidence(adopter.lane, remote="origin", base="main", check="full", head=head)
            self.hand_off(adopter, "feature", head, evidence=[{**found, "result": "failed"}])

        def missing(adopter: Adopter, head: str) -> None:
            self.hand_off(adopter, "feature", head, evidence=[])

        def another_check(adopter: Adopter, head: str) -> None:
            found = queue.evidence(adopter.lane, remote="origin", base="main", check="full", head=head)
            self.hand_off(adopter, "feature", head, evidence=[{**found, "check": "always"}])

        def forged_tree(adopter: Adopter, head: str) -> None:
            # The recorded tree is the synced one, but the head it names has another tree.
            base = run("rev-parse", "origin/main", cwd=adopter.lane)
            found = queue.evidence(adopter.lane, remote="origin", base="main", check="full", head=head)
            self.hand_off(adopter, "feature", head, evidence=[{**found, "head": base}])

        def changed_in_head(adopter: Adopter, head: str) -> None:
            # The lane ran the check under its own head's binding, which runs it differently from the base's.
            binding = json.loads(adopter.show(head, ".agents/orchestration.json"))
            [full] = [check for check in binding["alwaysRun"] if check["id"] == "full"]
            full["argv"].append("--changed")
            changed = adopter.lane_commit("feature", {".agents/orchestration.json": json.dumps(binding, indent=2) + "\n"})
            self.hand_off(adopter, "feature", changed)

        for name, setup, why in (("the tip moved after the hand-off", tip_moved, "the tree is not the one"),
                                 ("a push after the hand-off changed the tree", pushed_after, "the tree is not the one"),
                                 ("evidence for another check definition", other_definition, "its definition changed"),
                                 ("the lane's own binding runs it differently", changed_in_head, "its definition changed"),
                                 ("a failing result", failing, "the lane's run failed"), ("no evidence", missing, None),
                                 ("evidence for another check", another_check, None),
                                 ("a head whose tree is not the recorded one", forged_tree, "has another tree")):
            with self.subTest(name):
                self.fresh()
                adopter = self.reusing()
                head = adopter.lane_commit("feature", {"tests/test_new.txt": "ok\n"})
                setup(adopter, head)
                results = self.run_queue(adopter, "feature")
                self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document())
                self.assertEqual(self.runs(adopter, "full"), 1)
                document = self.check_document(results)
                states = {check["id"]: check["state"] for check in document["checks"]}
                self.assertEqual(states["full"], "passed")
                # Evidence that was there but not reused says why; none, nothing.
                notes = [note for note in document["notes"] if note.startswith("full was run, not reused")]
                if why is None:
                    self.assertEqual(notes, [])
                else:
                    self.assertEqual(len(notes), 1, document["notes"])
                    self.assertIn(why, notes[0])

    def written_evidence(self, adopter: Adopter, head: str) -> list[dict]:
        """Evidence a lane could write by hand for a check the binding does not mark reusable."""
        [full] = [check for check in adopter.binding["alwaysRun"] if check["id"] == "full"]
        return [{"check": "full", "definition": queue.check_definition(full, adopter.binding), "head": head,
                 "tree": run("rev-parse", f"{head}^{{tree}}", cwd=adopter.lane), "result": "passed"}]

    def test_with_no_reusable_checks_declared_nothing_is_reused(self) -> None:
        for name, changes in (("an empty list", {**self.REUSING, "reusableChecks": []}),
                              ("no list", {key: value for key, value in self.REUSING.items() if key != "reusableChecks"})):
            with self.subTest(name):
                self.fresh()
                adopter = self.adopter(changes)
                head = adopter.lane_commit("feature", {"tests/test_new.txt": "ok\n"})
                with self.assertRaises(queue.Stop):
                    queue.evidence(adopter.lane, remote="origin", base="main", check="full", head=head)
                self.hand_off(adopter, "feature", head, evidence=self.written_evidence(adopter, head))
                self.run_queue(adopter, "feature")
                self.assertEqual(self.runs(adopter, "full"), 1)

    def test_evidence_is_never_reused_across_candidates(self) -> None:
        adopter = self.reusing()
        head = adopter.lane_commit("feature", {"tests/test_new.txt": "ok\n"})
        self.hand_off(adopter, "feature", head)
        run("fetch", "--quiet", "origin", cwd=adopter.operator)
        runner = adopter.runner()
        runner.load()
        [full] = [check for check in runner.binding["alwaysRun"] if check["id"] == "full"]
        tree = run("rev-parse", f"{head}^{{tree}}", cwd=adopter.lane)
        own = adopter.item("feature")
        self.assertEqual(set(runner.reusable([("feature", own)], [full], tree)), {"full"})
        # Another candidate at the very same tree has no evidence of its own.
        other = {**own, "subject": "other"}
        other.pop("evidence")
        self.assertEqual(runner.reusable([("other", other)], [full], tree), {})
        # Evidence for a head the runner's repository does not have is never taken on trust.
        unknown = {**own, "evidence": [{**own["evidence"][0], "head": "0" * 40}]}
        self.assertEqual(runner.reusable([("feature", unknown)], [full], tree), {})
        # A selected check, a provider's say, that builds on it makes it run.
        report = {"id": "report", "argv": [PY, "tools/check.py", "report"], "cwd": ".", "reasons": ["x"],
                  "prerequisites": ["full"]}
        runner.notes = []
        self.assertEqual(runner.reusable([("feature", own)], [full, report], tree), {})
        self.assertIn("another check needs its output", runner.notes[0])
        # Asked again, as a batch's culprit search does, it says so once.
        runner.reusable([("feature", own)], [full, report], tree)
        self.assertEqual(len(runner.notes), 1)
        # A changed definition is named before a changed tree.
        runner.notes = []
        both = {**own, "evidence": [{**own["evidence"][0], "tree": "0" * 40, "definition": "sha256:" + "0" * 64}]}
        self.assertEqual(runner.reusable([("feature", both)], [full], tree), {})
        self.assertIn("its definition changed", runner.notes[0])

    def test_a_batch_whose_combined_tree_is_the_last_members_checked_tree_reuses_its_run(self) -> None:
        adopter = self.reusing()
        first = adopter.lane_commit("first", {"tests/test_first.txt": "ok\n"})
        # The second member is built on the first, so the batch's combined tree is the second's own.
        second = adopter.lane_commit("second", {"tests/test_second.txt": "ok\n"}, start="origin/first")
        self.hand_off(adopter, "first", first)
        self.hand_off(adopter, "second", second)
        results = adopter.runner().run_command("batch", ["first", "second"])
        self.assertEqual({result.document()["outcome"] for result in results}, {"merged"},
                         [result.document() for result in results])
        self.assertEqual(self.runs(adopter, "full"), 0)
        entries = [check for result in results for check in result.document()["checks"] if check["id"] == "full"]
        self.assertTrue(entries)
        self.assertTrue(all(entry["state"] == "reused" and entry["reusedFrom"]["subject"] == "second" for entry in entries))
        journaled = [entry["subject"] for entry in state_tool.read_journal(adopter.state) if entry["kind"] == "check.reused"]
        self.assertEqual(sorted(journaled), ["first", "second"])

    def test_a_reusable_check_whose_own_prerequisite_fails_is_skipped_not_reused(self) -> None:
        adopter = self.reusing(alwaysRun=[check_entry("always", "--always-fail"),
                                          check_entry("full", prerequisites=["always"])])
        head = adopter.lane_commit("feature", {"tests/test_new.txt": "ok\n"})
        self.hand_off(adopter, "feature", head)
        results = self.run_queue(adopter, "feature")
        # The prerequisite fails on the base too, so the queue stops as broken; the reusable check never stood in.
        self.assertStopped(results, "base_broken")
        states = {check["id"]: check["state"] for check in self.check_document(results)["checks"]}
        self.assertEqual((states["always"], states["full"]), ("failed", "skipped"))
        self.assertEqual([entry for entry in state_tool.read_journal(adopter.state) if entry["kind"] == "check.reused"], [])

    def test_a_batch_reuses_only_when_the_combined_tree_is_a_members_checked_tree(self) -> None:
        adopter = self.reusing()
        first = adopter.lane_commit("first", {"tests/test_first.txt": "ok\n"})
        second = adopter.lane_commit("second", {"tests/test_second.txt": "ok\n"})
        self.hand_off(adopter, "first", first)
        self.hand_off(adopter, "second", second)
        results = adopter.runner().run_command("batch", ["first", "second"])
        self.assertEqual({result.document()["outcome"] for result in results}, {"merged"},
                         [result.document() for result in results])
        # The combined tree is neither member's checked tree, so the full check runs, once.
        self.assertEqual(self.runs(adopter, "full"), 1)
        runner = adopter.runner()
        runner.load()
        [full] = [check for check in runner.binding["alwaysRun"] if check["id"] == "full"]
        members = [("first", adopter.item("first")), ("second", adopter.item("second"))]
        second_tree = adopter.item("second")["evidence"][0]["tree"]
        found = runner.reusable(members, [full], second_tree)
        self.assertEqual(found["full"]["subject"], "second")

    def test_the_evidence_command_prints_evidence_or_says_why_not(self) -> None:
        adopter = self.reusing()
        head = adopter.lane_commit("feature", {"tests/test_new.txt": "ok\n"})
        script = SCRIPTS / "merge_queue.py"

        def command(*args: str) -> tuple[int, dict]:
            done = subprocess.run([sys.executable, "-B", str(script), "--repo", str(adopter.lane), "--remote", "origin",
                                   "--base", "main", "evidence", *args], capture_output=True, check=False)
            return done.returncode, json.loads(done.stdout)

        code, found = command("--check", "full", "--head", head)
        self.assertEqual(code, 0, found)
        self.assertEqual(found, queue.evidence(adopter.lane, remote="origin", base="main", check="full", head=head))
        self.assertEqual(found["tree"], run("rev-parse", f"{head}^{{tree}}", cwd=adopter.lane))
        # A git failure is reported, not raised.
        with mock.patch.object(queue.Runner, "evidence", side_effect=RuntimeError("boom")), \
             mock.patch.object(sys, "stdout", new=io.TextIOWrapper(io.BytesIO())) as captured:
            code = queue.main(["--repo", str(adopter.lane), "--remote", "origin", "--base", "main",
                               "evidence", "--check", "full", "--head", head])
            captured.flush()
            printed = json.loads(captured.buffer.getvalue())
        self.assertEqual((code, printed), (1, {"reason": "git_failed", "detail": "boom"}))
        # Every other command still needs --state.
        with self.assertRaises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()):
            queue.main(["--remote", "origin", "--base", "main", "run", "x"])
        self.assertEqual(stopped.exception.code, 2)
        code, failed = command("--check", "full", "--head", head, "--result", "failed")
        self.assertEqual((code, failed["result"]), (0, "failed"))
        for args, reason in ((("--check", "always", "--head", head), "binding_invalid"),
                             (("--check", "full", "--head", "0" * 40), "ref_invalid")):
            with self.subTest(args):
                code, refused = command(*args)
                self.assertEqual((code, refused["reason"]), (1, reason))
                # Pasted into a hand-off, the refusal is reported as the evidence command's.
                with self.assertRaises(state_tool.StateError) as caught:
                    self.hand_off(adopter, "feature", head, evidence=[refused])
                self.assertIn("the evidence command refused", str(caught.exception))

    def test_the_binding_reuses_only_its_own_checks(self) -> None:
        adopter = self.reusing()
        binding = copy.deepcopy(adopter.binding)
        for listed, problem in ((["unit"], "unit"), (["full", "full"], "unique")):
            binding["reusableChecks"] = listed
            with self.subTest(listed):
                self.assertTrue([error for error in queue.binding_errors(binding)
                                 if error.startswith("/reusableChecks") and problem in error], queue.binding_errors(binding))
        binding["reusableChecks"] = ["full", "fallback"]
        self.assertEqual(queue.binding_errors(binding), [])
        # Only the tree may vary: a placeholder it does not determine, in the argv or the profile, is refused.
        for where, change in (
            ("argv", lambda check: check["argv"].append("{baseHead}")),
            ("profile", lambda check: check.update(environmentProfile="default")),
        ):
            with self.subTest(where):
                varying = copy.deepcopy(binding)
                change(next(check for check in varying["alwaysRun"] if check["id"] == "full"))
                self.assertTrue([error for error in queue.binding_errors(varying) if "which the tree does not" in error],
                                queue.binding_errors(varying))
        preparing = copy.deepcopy(binding)
        preparing["prepare"] = [[PY, "tools/prepare.py", "{baseHead}"]]
        self.assertTrue([error for error in queue.binding_errors(preparing) if "the prepare steps use {baseHead}" in error],
                        queue.binding_errors(preparing))
        preparing["reusableChecks"] = []
        self.assertEqual(queue.binding_errors(preparing), [])
        tree_only = copy.deepcopy(binding)
        next(check for check in tree_only["alwaysRun"] if check["id"] == "full")["argv"].append("{treeSha}")
        self.assertEqual(queue.binding_errors(tree_only), [])
        # A check another check builds on is never reused.
        for where, name in (("alwaysRun", "always"), ("fallback", "fallback")):
            with self.subTest(dependant=where):
                feeding = copy.deepcopy(binding)
                feeding["reusableChecks"] = ["full"]
                next(check for check in feeding[where] if check["id"] == name)["prerequisites"] = ["full"]
                self.assertTrue([error for error in queue.binding_errors(feeding) if f"prerequisite of {name}" in error],
                                queue.binding_errors(feeding))

    def test_a_check_definition_changes_with_anything_that_changes_the_run(self) -> None:
        adopter = self.reusing()
        binding = copy.deepcopy(adopter.binding)
        [full] = [check for check in binding["alwaysRun"] if check["id"] == "full"]
        digest = queue.check_definition(full, binding)
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(queue.check_definition(copy.deepcopy(full), copy.deepcopy(binding)), digest)
        changes = {
            "argv": lambda check, b: check["argv"].append("--more"),
            "cwd": lambda check, b: check.update(cwd="src"),
            "timeout": lambda check, b: check.update(timeoutSeconds=7),
            "the default timeout": lambda check, b: b.update(checkTimeoutSeconds=19),
            "prerequisites": lambda check, b: check.update(prerequisites=["always"]),
            "the profile": lambda check, b: check.update(environmentProfile="default"),
            "the profile's environment": lambda check, b: (check.update(environmentProfile="default"),
                                                           b["profiles"]["default"]["environment"].update(X="1")),
            "the binding's environment": lambda check, b: b["environment"].update(MQ_EXTRA="1"),
            "the prepare steps": lambda check, b: b["prepare"].append([PY, "-c", "pass"]),
            "the hooks": lambda check, b: b.update(hooks="run"),
        }
        seen = {digest}
        for name, change in changes.items():
            with self.subTest(name):
                check, changed = copy.deepcopy(full), copy.deepcopy(binding)
                change(check, changed)
                found = queue.check_definition(check, changed)
                self.assertNotIn(found, seen)
                seen.add(found)
        # Its reasons say why it was selected, not how it runs.
        self.assertEqual(queue.check_definition({**full, "reasons": ["other"]}, binding), digest)


if __name__ == "__main__":
    unittest.main()


class LeadSelectionTests(MergeQueueTestCase):
    """Under a binding that enables it, the lead's checks for a candidate's reviewed head replace the provider's answer."""

    LEAD = {"selection": {"lead": True}}

    def select(self, adopter: Adopter, subject: str, checks: list[dict], reason: str = "only the parser changed",
               *, head: str | None = None, binding: dict | None = None) -> dict:
        return state_tool.select_checks(adopter.state, subject, binding=binding or adopter.binding,
                                        head=head or adopter.item(subject)["head"], checks=checks, reason=reason)

    def checked(self, results: list) -> dict:
        return next(result.document() for result in results if result.document()["subcommand"] in ("check", "batch")
                    and result.document()["selection"] is not None)

    def ran(self, adopter: Adopter) -> list[str]:
        return [line.split()[1] for line in adopter.log_lines() if line.startswith("check ")]

    def test_the_lead_selection_and_always_run_are_the_checks_and_no_provider_runs(self) -> None:
        adopter = self.adopter(self.LEAD)
        head = self.candidate(adopter)
        self.select(adopter, "feature", [check_entry("focused")])
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        selection = self.checked(results)["selection"]
        self.assertEqual(selection["checks"], ["focused", "always"])
        self.assertEqual((selection["providers"], selection["fallbackReasons"], selection["widened"]), ([], [], False))
        self.assertEqual(selection["lead"]["members"][0]["subject"], "feature")
        self.assertEqual(selection["lead"]["members"][0]["head"], head)
        self.assertEqual(selection["lead"]["members"][0]["reason"], "only the parser changed")
        self.assertEqual(selection["lead"]["members"][0]["checks"], ["focused"])
        self.assertTrue(any("selected by the lead" in note for note in self.checked(results)["notes"]))
        self.assertEqual(sorted(self.ran(adopter)), ["always", "focused"])
        self.assertEqual(adopter.item("feature")["checkSelection"],
                         {"by": "lead", "head": head, "reason": "only the parser changed"})
        stats = state_tool.delivery_stats(state_tool.read_state(adopter.state), state_tool.read_journal(adopter.state))
        self.assertEqual(stats["feature"]["checksSelectedBy"], "lead")
        kinds = [entry["kind"] for entry in state_tool.read_journal(adopter.state)]
        self.assertIn("check.selected", kinds)

    def test_without_a_selection_the_provider_selects_and_the_row_says_so(self) -> None:
        adopter = self.adopter(self.LEAD)
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        selection = self.checked(results)["selection"]
        self.assertNotIn("lead", selection)
        self.assertIn("unit", selection["checks"])
        self.assertEqual(selection["providers"][0]["outcome"], "selected")
        self.assertEqual(adopter.item("feature")["checkSelection"], {"by": "provider"})

    def test_without_a_selection_a_candidate_widened_to_the_fallback_names_it_on_its_row(self) -> None:
        adopter = self.adopter({**self.LEAD, "provider": None})
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        selection = self.checked(results)["selection"]
        fallback = [check["id"] for check in adopter.binding["fallback"]]
        self.assertTrue(selection["widened"])
        self.assertEqual(adopter.item("feature")["checkSelection"], {"by": "provider", "fallback": fallback})
        # A provider that widened its own answer added no fallback, so the row doesn't say it runs one.
        self.fresh()
        adopter = self.adopter(self.LEAD, mode="widened")
        self.candidate(adopter)
        selection = self.checked(self.run_queue(adopter, "feature"))["selection"]
        self.assertEqual((selection["widened"], selection["fallbackReasons"]), (True, []))
        self.assertNotIn("fallback", selection["checks"])
        self.assertEqual(adopter.item("feature")["checkSelection"], {"by": "provider"})
        # Without lead selection the row says nothing, as before.
        self.fresh()
        adopter = self.adopter({"provider": None})
        self.candidate(adopter)
        self.run_queue(adopter, "feature")
        self.assertNotIn("checkSelection", adopter.item("feature"))

    def test_a_selection_made_at_another_head_is_void(self) -> None:
        adopter = self.adopter(self.LEAD)
        self.candidate(adopter)
        self.select(adopter, "feature", [check_entry("focused")])
        newer = adopter.lane_commit("feature", {"src/app.txt": "app three\n"})
        adopter.lead_set("feature", head=newer)
        results = self.run_queue(adopter, "feature")
        document = self.checked(results)
        self.assertNotIn("lead", document["selection"])
        self.assertIn("unit", document["selection"]["checks"])
        self.assertNotIn("focused", self.ran(adopter))
        self.assertTrue(any("is void" in note for note in document["notes"]), document["notes"])
        self.assertEqual(adopter.item("feature")["checkSelection"], {"by": "provider"})

    def test_a_binding_that_does_not_enable_it_ignores_the_selection(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        self.select(adopter, "feature", [check_entry("focused")], binding={**adopter.binding, **self.LEAD})
        results = self.run_queue(adopter, "feature")
        document = self.checked(results)
        self.assertNotIn("lead", document["selection"])
        self.assertNotIn("focused", self.ran(adopter))
        self.assertTrue(any("does not set selection.lead" in note for note in document["notes"]), document["notes"])
        # An adopter without the option gains nothing new on its rows.
        self.assertNotIn("checkSelection", adopter.item("feature"))

    def test_a_lead_selected_check_failing_on_the_base_too_stops_the_queue_as_base_broken(self) -> None:
        adopter = self.adopter(self.LEAD)
        adopter.advance_base({"src/app.txt": "FAIL on base\n", "gen/generated.txt": "FAIL on base\nlib one\n"})
        self.candidate(adopter, {"src/lib.txt": "lib two\n"})
        self.select(adopter, "feature", [check_entry("focused", "src/app.txt")])
        document = self.assertStopped(self.run_queue(adopter, "feature"), "base_broken")
        self.assertTrue(document["stop"]["queueStopped"])
        self.assertIn("focused", document["stop"]["detail"])

    def test_a_selection_the_binding_no_longer_admits_stops_the_candidate(self) -> None:
        adopter = self.adopter(self.LEAD)
        self.candidate(adopter)
        # Recorded under a binding without that alwaysRun check; the repository's binding has one of that id.
        self.select(adopter, "feature", [check_entry("always")], binding={**adopter.binding, "alwaysRun": []})
        document = self.assertStopped(self.run_queue(adopter, "feature"), "selection_invalid")
        self.assertIn("always", document["stop"]["detail"])
        self.assertFalse(document["stop"]["queueStopped"])

    def batch(self, adopter: Adopter, *subjects: str) -> list:
        return adopter.runner().run_command("batch", list(subjects))

    def test_a_batch_runs_the_union_of_its_members_selections(self) -> None:
        adopter = self.adopter(self.LEAD)
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")
        self.select(adopter, "first", [check_entry("shared"), check_entry("lib")], "lib only")
        self.select(adopter, "second", [check_entry("shared"), check_entry("extra")], "extra only")
        results = self.batch(adopter, "first", "second")
        self.assertEqual({result.document()["outcome"] for result in results}, {"merged"})
        selection = self.checked(results)["selection"]
        self.assertEqual(selection["checks"], ["shared", "lib", "extra", "always"])
        self.assertEqual([member["subject"] for member in selection["lead"]["members"]], ["first", "second"])
        self.assertEqual(self.ran(adopter).count("shared"), 1)
        self.assertNotIn("unit", self.ran(adopter))
        self.assertEqual(adopter.item("second")["checkSelection"]["reason"], "extra only")

    def test_a_batch_member_without_a_selection_sends_the_whole_batch_to_the_provider(self) -> None:
        adopter = self.adopter(self.LEAD)
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        self.candidate(adopter, {"src/app.txt": "app two\n"}, name="second")
        self.select(adopter, "first", [check_entry("lib")], "lib only")
        results = self.batch(adopter, "first", "second")
        self.assertEqual({result.document()["outcome"] for result in results}, {"merged"})
        document = self.checked(results)
        self.assertNotIn("lead", document["selection"])
        self.assertIn("unit", document["selection"]["checks"])
        self.assertNotIn("lib", self.ran(adopter))
        self.assertTrue(any("second has no lead selection" in note for note in document["notes"]), document["notes"])
        self.assertEqual(adopter.item("first")["checkSelection"], {"by": "provider"})

    def test_batch_members_that_define_one_check_differently_are_refused(self) -> None:
        adopter = self.adopter(self.LEAD)
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        self.candidate(adopter, {"src/extra.txt": "extra\n"}, name="second")
        self.select(adopter, "first", [check_entry("shared")])
        self.select(adopter, "second", [check_entry("shared", "src/extra.txt")])
        results = self.batch(adopter, "first", "second")
        reasons = {result.document()["stop"]["reason"] for result in results if result.document()["stop"]}
        self.assertIn("selection_invalid", reasons)
        self.assertNotEqual(adopter.item("first")["stage"], "merged")


class CheckCatalogueQueueTests(MergeQueueTestCase):
    """A selection by catalogue id runs as one by full entries, and a base re-run is recorded with the head it checked."""

    def catalogue_adopter(self) -> Adopter:
        return self.adopter({"selection": {
            "lead": True,
            "catalogue": [check_entry("focused", "src/app.txt"), check_entry("guard", prerequisites=["guard-setup"]),
                          check_entry("guard-setup"), check_entry("lib", "src/lib.txt"),
                          check_entry("suite", "--fail-with", "test_app")],
            "guards": ["guard"],
        }})

    def ran(self, adopter: Adopter) -> list[str]:
        return [line.split()[1] for line in adopter.log_lines() if line.startswith("check ")]

    def select_tool(self, adopter: Adopter, subject: str, checks: str) -> dict:
        """select on the command line, from the operator's checkout, after it fetched the base."""
        run("fetch", "--quiet", "origin", cwd=adopter.operator)
        binding = adopter.root / "binding.json"
        binding.write_text(json.dumps(adopter.binding), encoding="utf-8")
        completed = subprocess.run(
            [PY, "-B", str(SCRIPTS / "delivery_state.py"), "--dir", str(adopter.state), "select", "--subject", subject,
             "--binding", str(binding), "--head", adopter.item(subject)["head"], "--checks", checks,
             "--reason", "the lead's reason", "--repo", str(adopter.operator)],
            capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    def test_the_queue_runs_a_selection_by_ids_the_same_as_one_by_full_entries(self) -> None:
        adopter = self.catalogue_adopter()
        self.candidate(adopter, {"src/app.txt": "app two\n"})
        result = self.select_tool(adopter, "feature", "focused")
        self.assertEqual(result["item"]["leadSelection"]["checks"],
                         [check_entry("focused", "src/app.txt"), check_entry("guard-setup"),
                          check_entry("guard", prerequisites=["guard-setup"])])
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        selection = next(r.document() for r in results if r.document()["selection"] is not None)["selection"]
        self.assertEqual(selection["checks"], ["focused", "guard-setup", "guard", "always"])
        self.assertEqual(selection["lead"]["members"][0]["checks"], ["focused", "guard-setup", "guard"])
        self.assertEqual(sorted(self.ran(adopter)), ["always", "focused", "guard", "guard-setup"])

    def test_a_selection_recorded_before_the_binding_named_a_guard_still_runs_the_guard(self) -> None:
        adopter = self.catalogue_adopter()
        self.candidate(adopter, {"src/app.txt": "app two\n"})
        unguarded = {**adopter.binding, "selection": {**adopter.binding["selection"], "guards": []}}
        state_tool.select_checks(adopter.state, "feature", binding=unguarded, head=adopter.item("feature")["head"],
                                 checks=["focused"], reason="before the guard", repo=adopter.operator)
        self.assertEqual([check["id"] for check in adopter.item("feature")["leadSelection"]["checks"]], ["focused"])
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        document = next(r.document() for r in results if r.document()["selection"] is not None)
        # The guard brings its catalogue prerequisite with it.
        self.assertEqual(document["selection"]["checks"], ["focused", "guard-setup", "guard", "always"])
        self.assertTrue(any("added the binding's guards guard" in note for note in document["notes"]), document["notes"])
        self.assertEqual(sorted(self.ran(adopter)), ["always", "focused", "guard", "guard-setup"])

    def test_a_failing_base_rerun_is_recorded_with_its_head_and_select_warns_at_that_head(self) -> None:
        adopter = self.catalogue_adopter()
        base = adopter.advance_base({"src/app.txt": "FAIL on base\n", "gen/generated.txt": "FAIL on base\nlib one\n"})
        self.candidate(adopter, {"src/lib.txt": "lib two\n"})
        self.select_tool(adopter, "feature", "focused")
        document = self.assertStopped(self.run_queue(adopter, "feature"), "base_broken")
        self.assertIn("focused", document["stop"]["detail"])
        [entry] = state_tool.read_state(adopter.state)["meta"]["baseReruns"]
        self.assertEqual((entry["check"], entry["result"], entry["subject"]), ("focused", "failed", "feature"))
        self.assertEqual(entry["baseHead"], base)
        self.assertEqual(entry["baseHead"], adopter.tip("main"))
        self.assertNotEqual(entry["baseHead"], adopter.item("feature")["syncedHead"])
        warned = self.select_tool(adopter, "feature", "focused")
        self.assertEqual(warned["baseHead"], base)
        [warning] = warned["warnings"]
        self.assertIn("focused", warning)
        self.assertIn(base, warning)
        # A check not re-run there gives no warning, and the head is known.
        quiet = self.select_tool(adopter, "feature", "lib")
        self.assertEqual((quiet["warnings"], quiet["notes"]), ([], []))

    def test_a_passing_base_rerun_is_recorded_as_passed_and_the_list_keeps_its_bound(self) -> None:
        adopter = self.catalogue_adopter()
        filler = [{"check": "old", "baseHead": "c" * 40, "result": "failed", "subject": "old",
                   "recordedAt": "2026-11-01T00:00:00Z"}] * state_tool.BASE_RERUNS_KEPT
        current = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="runner", version=current["version"], assignments={"/meta/baseReruns": filler})
        # The suite fails with the candidate's change and passes on the base, whose re-run clears it.
        self.candidate(adopter, {"src/app.txt": "app two\n"})
        self.select_tool(adopter, "feature", "suite")
        self.assertStopped(self.run_queue(adopter, "feature"), "check_failed")
        reruns = state_tool.read_state(adopter.state)["meta"]["baseReruns"]
        self.assertEqual(len(reruns), state_tool.BASE_RERUNS_KEPT)
        self.assertEqual((reruns[-1]["check"], reruns[-1]["result"], reruns[-1]["baseHead"]),
                         ("suite", "passed", adopter.tip("main")))
        self.assertEqual(reruns[-2]["check"], "old")
        quiet = self.select_tool(adopter, "feature", "suite")
        self.assertEqual((quiet["warnings"], quiet["notes"]), ([], []))

    def test_the_binding_checks_its_catalogue_guards_and_readers(self) -> None:
        binding = self.catalogue_adopter().binding
        self.assertEqual(queue.binding_errors(binding), [])
        selection = binding["selection"]
        reading = {**binding, "selection": {**selection, "readers": [{"paths": ["src/**"], "checks": ["lib"]}]}}
        self.assertEqual(queue.binding_errors(reading), [])
        self.assertTrue(Draft202012Validator(schema("orchestration-binding")).is_valid(reading))
        cases = {
            "unknown guard": ({"guards": ["missing"]}, "/selection/guards"),
            "unknown reader": ({"readers": [{"paths": ["src/**"], "checks": ["missing"]}]}, "/selection/readers/0"),
            "binding id": ({"catalogue": [*selection["catalogue"], check_entry("always")]}, "/selection/catalogue"),
            "unknown profile": ({"catalogue": [check_entry("p", environmentProfile="missing")], "guards": []},
                                "/selection/catalogue"),
            "over the ceiling": ({"catalogue": [check_entry("p", timeoutSeconds=61)], "guards": []}, "/selection/catalogue"),
            "cycle": ({"catalogue": [check_entry("a", prerequisites=["b"]), check_entry("b", prerequisites=["a"])],
                       "guards": []}, "/selection/catalogue"),
            "outside prerequisite": ({"catalogue": [check_entry("a", prerequisites=["always"])], "guards": []},
                                     "/selection/catalogue"),
        }
        # A check exactly at the ceiling is within it.
        at_ceiling = {**binding, "selection": {**selection, "catalogue": [*selection["catalogue"],
                                                                         check_entry("p", timeoutSeconds=60)]}}
        self.assertEqual(queue.binding_errors(at_ceiling), [])
        for name, (changes, where) in cases.items():
            with self.subTest(name):
                errors = queue.binding_errors({**binding, "selection": {**selection, **changes}})
                self.assertTrue(errors)
                self.assertTrue(all(error.startswith(where) for error in errors), errors)


# What a refused step's stop ends with: its reason, then the log that keeps its whole output.
KEPT_AFTER = r"{}; its whole output is in logs/\S+\.log under the state directory\Z"


class CandidateStepTests(MergeQueueTestCase):
    """The candidate step says why it gave no branch and head, a binding must pass {candidate}, and serve holds,
    taking nothing, when the step's program or script is missing."""

    SECRET = "s3cr3t-candidate-value-from-the-environment"

    def script(self, code: str) -> list[str]:
        return [PY, "-c", code, "{candidate}"]

    def stop(self, adopter: Adopter, reason: str = "candidate_unresolved") -> tuple[str, str]:
        """The stop's detail and the journal's candidate.stopped summary."""
        self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), reason)
        Draft202012Validator(schema("merge-queue-result")).validate(document)
        summary = next(entry["summary"] for entry in reversed(state_tool.read_journal(adopter.state))
                       if entry["kind"] == "candidate.stopped")
        self.assertIn(document["stop"]["detail"], summary)
        return document["stop"]["detail"], summary

    def test_a_missing_script_stops_with_the_interpreters_own_words(self) -> None:
        detail, _ = self.stop(self.adopter({"candidate": [PY, "tools/missing.py", "{candidate}"]}))
        self.assertTrue(detail.startswith("feature did not resolve to a branch and head: the candidate command exited 2; "
                                          "stderr: "), detail)
        self.assertIn("can't open file", detail)
        self.assertIn("missing.py", detail)

    def test_a_secret_in_stderr_never_reaches_the_record(self) -> None:
        code = ("import os, sys; sys.stderr.write('auth failed with ' + os.environ['MQ_API_TOKEN'] + ' and ghp_' + 'A' * 36)"
                "; sys.exit(1)")
        adopter = self.environment_adopter({"MQ_API_TOKEN": self.SECRET}, {"candidate": self.script(code)})
        detail, _ = self.stop(adopter)
        self.assertIn("the candidate command exited 1; stderr: auth failed with [redacted] and [redacted]", detail)
        for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE):
            text = (adopter.state / name).read_text(encoding="utf-8")
            self.assertNotIn(self.SECRET, text)
            self.assertNotIn("ghp_" + "A" * 36, text)

    def test_text_where_json_belongs_is_shown_bounded(self) -> None:
        detail, _ = self.stop(self.adopter({"candidate": self.script("print('x' * 1000 + ' END')")}))
        self.assertIn("the candidate command printed no JSON; stdout: ", detail)
        self.assertRegex(detail, KEPT_AFTER.format("x END"))
        self.assertNotIn("x" * queue.CANDIDATE_OUT_LIMIT, detail)

    def test_json_without_a_head_shows_the_answer(self) -> None:
        detail, _ = self.stop(self.adopter({"candidate": self.script("import json; print(json.dumps({'branch': 'feature'}))")}))
        self.assertIn('its JSON has no branch and full head; stdout: {"branch": "feature"}', detail)

    def test_a_silent_failure_says_so(self) -> None:
        detail, _ = self.stop(self.adopter({"candidate": self.script("import sys; sys.exit(4)")}))
        self.assertRegex(detail, KEPT_AFTER.format(re.escape("the candidate command exited 4; it printed nothing")))

    def test_a_candidate_command_that_runs_too_long_says_it_timed_out(self) -> None:
        adopter = self.adopter({"candidate": self.script("import time; time.sleep(30)"), "checkTimeoutSeconds": 1})
        detail, _ = self.stop(adopter)
        self.assertRegex(detail, KEPT_AFTER.format(re.escape("the candidate command timed out after 1s")))

    def test_a_failing_command_is_never_resolved_even_with_a_valid_answer(self) -> None:
        code = ("import subprocess, sys; subprocess.run([sys.executable, 'tools/candidate.py', sys.argv[1]], check=True)"
                "; sys.exit(3)")
        detail, _ = self.stop(self.adopter({"candidate": self.script(code)}))
        self.assertIn("the candidate command exited 3; stdout: {", detail)

    def test_a_failing_command_that_explains_on_stdout_shows_it(self) -> None:
        detail, _ = self.stop(self.adopter({"candidate": self.script("print('no such PR'); import sys; sys.exit(1)")}))
        self.assertRegex(detail, KEPT_AFTER.format(re.escape("the candidate command exited 1; stdout: no such PR")))

    def test_an_invalid_branch_is_named(self) -> None:
        code = "import json; print(json.dumps({'branch': 'bad..name', 'head': '0' * 40}))"
        detail, _ = self.stop(self.adopter({"candidate": self.script(code)}), "ref_invalid")
        self.assertEqual(detail, 'feature resolved to an invalid branch name: "bad..name"')

    def test_a_candidate_argv_without_the_placeholder_is_invalid(self) -> None:
        adopter = self.adopter()
        rule = "/candidate: the candidate argv must use {candidate} as a whole element"
        self.assertEqual(queue.binding_errors({**adopter.binding, "candidate": [PY, "tools/candidate.py", "feature"]}), [rule])
        self.assertEqual(queue.binding_errors(adopter.binding), [])
        self.fresh()
        adopter = self.adopter({"candidate": [PY, "tools/candidate.py", "feature"]})
        self.candidate(adopter)
        self.assertIn(rule, self.assertStopped(self.run_queue(adopter, "feature"), "binding_invalid")["stop"]["detail"])
        served = queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertIn(rule, self.assertStopped(served, "binding_invalid")["stop"]["detail"])
        self.assertEqual(adopter.item("feature")["stage"], "queued")

    def held(self, adopter: Adopter) -> str:
        hold = state_tool.read_state(adopter.state)["meta"]["hold"]
        blockers = [entry for entry in state_tool.read_journal(adopter.state) if entry["kind"] == "blocker.recorded"]
        self.assertEqual(len(blockers), 1)
        self.assertNotIn("candidate.stopped", [entry["kind"] for entry in state_tool.read_journal(adopter.state)])
        return hold["reason"]

    def test_serve_started_with_a_missing_script_holds_and_takes_nothing(self) -> None:
        adopter = self.adopter({"candidate": [PY, "tools/missing.py", "{candidate}"]})
        self.candidate(adopter)
        results = queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertEqual(results, [])
        self.assertEqual(adopter.item("feature")["stage"], "queued")
        self.assertIn("serve cannot use its setup: the binding's candidate step can't run: its script tools/missing.py "
                      "doesn't exist in the repository.", self.held(adopter))

    def test_a_script_reached_through_a_committed_symlink_holds_nothing(self) -> None:
        # As an installed release's .agent-skills/current is: the candidate step runs in a checkout, where it resolves.
        adopter = self.adopter()
        run("fetch", "--quiet", "origin", cwd=adopter.lane)
        run("checkout", "--quiet", "-B", "base-work", "origin/main", cwd=adopter.lane)
        os.symlink("tools", adopter.lane / "current-tools")
        adopter.write(adopter.lane, ".agents/orchestration.json", json.dumps(
            {**adopter.binding, "candidate": [PY, "-B", "current-tools/candidate.py", "{candidate}"]}, indent=2) + "\n")
        run("add", "--all", cwd=adopter.lane)
        run("commit", "--quiet", "-m", "a linked tools folder", cwd=adopter.lane)
        run("push", "--quiet", "origin", "base-work:main", cwd=adopter.lane)
        self.candidate(adopter)
        results = queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"].get("hold"), [r.document()["stop"] for r in results])
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        runner = adopter.runner()
        base_head = runner.load()
        self.assertEqual(runner.candidate_step_problems(base_head), [])
        runner.binding = {**runner.binding, "candidate": [PY, "current-tools/missing.py", "{candidate}"]}
        self.assertEqual(runner.candidate_step_problems(base_head),
                         ["its script current-tools/missing.py doesn't exist in the repository"])
        runner.binding = {**runner.binding, "candidate": [PY, "./current-tools", "{candidate}"]}
        self.assertEqual(runner.candidate_step_problems(base_head),
                         ["its script ./current-tools doesn't exist in the repository"], "a folder isn't a script")

    def test_serve_started_on_an_empty_queue_with_a_missing_script_holds_at_once(self) -> None:
        adopter = self.adopter({"candidate": [PY, "./tools/missing.py", "{candidate}"]})
        self.assertEqual(queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True), [])
        self.assertIn("its script ./tools/missing.py doesn't exist in the repository", self.held(adopter))

    def test_a_present_script_named_from_the_top_level_holds_nothing(self) -> None:
        adopter = self.adopter({"candidate": [PY, "./tools/candidate.py", "{candidate}"]})
        self.candidate(adopter)
        results = queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertIsNone(state_tool.read_state(adopter.state)["meta"].get("hold"))
        self.assertEqual(adopter.item("feature")["stage"], "merged", [r.document()["stop"] for r in results])

    def test_a_candidate_step_broken_after_serve_starts_holds_before_its_first_turn(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        real = queue.Runner.recover
        broken: list[bool] = []

        def break_after_startup(runner) -> None:
            # The startup check has passed; the candidate step breaks before the first turn takes anything.
            if not broken:
                broken.append(True)
                adopter.advance_base({".agents/orchestration.json": json.dumps(
                    {**adopter.binding, "candidate": [PY, "tools/missing.py", "{candidate}"]}, indent=2) + "\n"})
            real(runner)

        with mock.patch.object(queue.Runner, "recover", break_after_startup):
            results = queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertEqual(results, [])
        self.assertEqual(adopter.item("feature")["stage"], "queued")
        self.assertIn("its script tools/missing.py doesn't exist in the repository", self.held(adopter))

    def test_a_candidate_step_broken_during_a_turn_puts_its_candidate_back_and_holds(self) -> None:
        adopter = self.adopter()
        self.candidate(adopter)
        real = queue.Runner.run_command
        broken: list[bool] = []

        def break_then_run(runner, *args, **kwargs):
            if not broken:
                broken.append(True)
                adopter.advance_base({".agents/orchestration.json": json.dumps(
                    {**adopter.binding, "candidate": [PY, "tools/missing.py", "{candidate}"]}, indent=2) + "\n"})
            return real(runner, *args, **kwargs)

        with mock.patch.object(queue.Runner, "run_command", break_then_run):
            results = queue.serve(adopter.operator, adopter.state, remote="origin", base="main", until_idle=True)
        self.assertEqual(results[-1].document()["stop"]["reason"], "candidate_unresolved")
        self.assertEqual(adopter.item("feature")["stage"], "queued")
        hold = state_tool.read_state(adopter.state)["meta"]["hold"]
        self.assertIn("its script tools/missing.py doesn't exist in the repository", hold["reason"])

    def test_the_program_check_finds_each_missing_part(self) -> None:
        present = {"tools/c.py", "run.py", "tools/c.ts", "tools/c.sh", "tools/c.pl", "tools/c.js", "tools/c.rb"}.__contains__
        cases = [
            ([PY, "tools/c.py", "{candidate}"], []),
            ([PY, "-B", "./tools/c.py", "{candidate}"], []),
            ([PY, "tools/missing.py", "{candidate}"], ["its script tools/missing.py doesn't exist in the repository"]),
            ([PY, "/no/such/script.py", "{candidate}"], ["its script /no/such/script.py doesn't exist"]),
            (["no-such-program-anywhere", "{candidate}"], ["its program no-such-program-anywhere isn't on PATH"]),
            (["/no/such/program", "{candidate}"], ["its program /no/such/program doesn't exist"]),
            (["tools/run.sh", "{candidate}"], ["its program tools/run.sh isn't in the repository"]),
            ([PY, "-c", "print(1)", "--out=x/y.py", "https://example.test/a.py", "run.py", "{candidate}"], []),
            ([PY, "lost.py", "{candidate}"], ["its script lost.py doesn't exist in the repository"]),
            # Values with a slash that aren't the script, inline code and expressions aren't paths.
            ([PY, "tools/c.py", "candidate", "--key", "{candidate}", "--prefix", "story/", "--repo", "owner/repo"], []),
            (["sh", "-c", 'git rev-parse refs/heads/"$1"', "sh", "{candidate}"], []),
            ([PY, "-c", 'import sys; print("a/b")', "{candidate}"], []),
            ([PY, "-m", "tools.candidate", "data/in", "{candidate}"], []),
            # A code flag the check doesn't know: its value reads as the operand, but it's an expression, not a path.
            (["perl", "-E", 'say "a/b.pl"', "{candidate}"], []),
            # Subcommands and flag values never take the script's place.
            (["deno", "run", "-A", "tools/c.ts", "{candidate}"], []),
            (["bun", "run", "tools/c.ts", "{candidate}"], []),
            (["bash", "-o", "pipefail", "tools/c.sh", "{candidate}"], []),
            (["perl", "-I", "lib/perl5", "tools/c.pl", "{candidate}"], []),
            # Python's -I is isolated mode and takes no value, so the script after it is still checked.
            ([PY, "-I", "tools/gone.py", "{candidate}"], ["its script tools/gone.py doesn't exist in the repository"]),
            ([PY, "-I", "tools/c.py", "{candidate}"], []),
            (["pypy3", "-I", "tools/gone.py", "{candidate}"], ["its script tools/gone.py doesn't exist in the repository"]),
            (["py", "-I", "tools/gone.py", "{candidate}"], ["its script tools/gone.py doesn't exist in the repository"]),
            (["PYTHON.EXE", "-I", "tools/gone.py", "{candidate}"], ["its script tools/gone.py doesn't exist in the repository"]),
            # Ruby's -I, like perl's, names a folder to load from: its value isn't the script.
            (["ruby", "-I", "lib/ruby", "tools/c.rb", "{candidate}"], []),
            (["node", "--import", "tsx/esm", "tools/c.js", "{candidate}"], []),
            (["node", "--require", "ts-node/register", "tools/c.js", "{candidate}"], []),
            (["node", "--loader", "ts-node/esm", "tools/c.js", "{candidate}"], []),
            (["node", "--print", "process.argv/2", "{candidate}"], []),
            (["node", "-r", "ts-node/register", "tools/c.js", "{candidate}"], []),
            (["node", "-p", "process.argv/2", "{candidate}"], []),
            (["deno", "run", "-A", "tools/gone.ts", "{candidate}"], ["its script tools/gone.ts doesn't exist in the repository"]),
            (["bash", "-o", "pipefail", "tools/gone/run", "{candidate}"], ["its script tools/gone/run doesn't exist in the repository"]),
            (["git", "log", "--format=%H", "--jq", '.a + "/" + .b', "{candidate}"], []),
            ([PY, "-X", "importtime", "-X/importtime", "-Wignore", "tools/c.py", "{candidate}"], []),
            ([PY, "-X", "importtime", "tools/gone.py", "{candidate}"], ["its script tools/gone.py doesn't exist in the repository"]),
            (["git", "x/y", "lib/helper.rb", "{candidate}"], ["its script lib/helper.rb doesn't exist in the repository"]),
            ([PY, "tools/c.py", "other/thing.py", "{candidate}"], ["its script other/thing.py doesn't exist in the repository"]),
        ]
        # Stand-ins for interpreters this machine may not have, first on the PATH the check reads.
        stand_ins = self.directory / "bin"
        stand_ins.mkdir()
        for name in ("deno", "bun", "node", "perl", "ruby", "pypy3", "py", "PYTHON.EXE"):
            (stand_ins / name).write_text("#!/bin/sh\n")
            (stand_ins / name).chmod(0o755)
        search = os.pathsep.join([str(stand_ins), os.environ.get("PATH", "")])
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(queue.candidate_problems(argv, lambda path: present(path.removeprefix("./")), search),
                                 expected)

class ProviderRefusalTests(MergeQueueTestCase):
    """A provider that exits non-zero widens to fallback, unless the binding's selection.refusalStops makes it a stop."""

    STOPS = {"selection": {"lead": True, "refusalStops": True}}

    def kinds(self, adopter: Adopter) -> list[dict]:
        return state_tool.read_journal(adopter.state)

    def test_without_the_flag_a_refusing_provider_widens_to_fallback_as_before(self) -> None:
        for name, changes in (("unset", None), ("lead only", {"selection": {"lead": True}}),
                              ("false", {"selection": {"lead": True, "refusalStops": False}})):
            with self.subTest(binding=name):
                self.fresh()
                adopter = self.adopter(changes, mode="refuse")
                self.candidate(adopter)
                results = self.run_queue(adopter, "feature")
                check = results[1].document()
                self.assertEqual(check["selection"]["fallbackReasons"], ["provider_failed"])
                self.assertEqual(check["selection"]["providers"][0]["outcome"], "failed")
                self.assertIn("over the budget: unit (400s)", check["selection"]["providers"][0]["detail"])
                self.assertIn("fallback", check["selection"]["checks"])
                self.assertTrue(check["selection"]["widened"])
                self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
                self.assertNotIn("selection.refused", [entry["kind"] for entry in self.kinds(adopter)])

    def ran(self, adopter: Adopter) -> list[str]:
        return [line.split()[1] for line in adopter.log_lines() if line.startswith("check ")]

    @staticmethod
    def requeue(adopter: Adopter, subject: str) -> None:
        """What run-delivery's lead-accept --requeue writes: the stage is the runner's."""
        state = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="runner", version=state["version"], selector=f"queue[subject={subject}]",
                         assignments={"/stage": "queued", "/checks": []})

    def refused(self, adopter: Adopter, subject: str = "feature") -> None:
        """What a refusal leaves: no check ran, the candidate stopped and unmerged, and the row and journal say why."""
        self.assertEqual(self.ran(adopter), [], "a check ran for a refused candidate")
        item = adopter.item(subject)
        self.assertEqual(item["stage"], "stopped", item)
        selection = item["checkSelection"]
        self.assertEqual((selection["by"], selection["outcome"]), ("provider", "provider_refused"), selection)
        self.assertIn("over the budget: unit (400s), e2e (700s)", selection["detail"])
        mine = [entry for entry in self.kinds(adopter) if entry.get("subject") == subject]
        refusals = [entry for entry in mine if entry["kind"] == "selection.refused"]
        self.assertEqual(len(refusals), 1, mine)
        self.assertIn(subject, refusals[0]["summary"])
        self.assertIn("over the budget: unit (400s)", refusals[0]["summary"])
        self.assertNotIn("candidate.merged", [entry["kind"] for entry in mine])
        stops = [entry for entry in mine if entry["kind"] == "candidate.stopped"]
        self.assertTrue(stops and stops[-1]["summary"].startswith("provider_refused: "), mine)
        self.assertLess(refusals[0]["id"], stops[-1]["id"])

    def test_with_the_flag_a_refusing_provider_stops_the_candidate_and_runs_no_check(self) -> None:
        adopter = self.adopter(self.STOPS, mode="refuse")
        base = adopter.tip("main")
        self.candidate(adopter)
        document = self.assertStopped(self.run_queue(adopter, "feature"), "provider_refused")
        self.assertFalse(document["stop"]["queueStopped"])
        self.assertIn("over the budget: unit (400s), e2e (700s)", document["stop"]["detail"])
        self.refused(adopter)
        self.assertEqual(adopter.tip("main"), base, "a refused candidate landed")
        self.assertEqual(state_tool.read_state(adopter.state)["meta"].get("hold"), None)
        # Put back without a selection, it is refused again, never widened.
        self.requeue(adopter, "feature")
        self.assertStopped(self.run_queue(adopter, "feature"), "provider_refused")
        self.assertEqual(self.ran(adopter), [])
        self.assertEqual(adopter.tip("main"), base)

    def test_a_refusing_providers_words_are_scrubbed_before_they_reach_the_record(self) -> None:
        secret = "s3cr3t-provider-value-from-the-environment"
        adopter = self.environment_adopter({"MQ_STEP_TOKEN": secret}, self.STOPS)
        self.candidate(adopter, {"src/app.txt": "app two\n", "tools/provider-mode.txt": "refuse\n"})
        document = self.assertStopped(self.run_queue(adopter, "feature"), "provider_refused")
        self.assertIn("asked with [redacted]", document["stop"]["detail"])
        self.assertNotIn(secret, json.dumps(document))
        for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE):
            self.assertNotIn(secret, (adopter.state / name).read_text(encoding="utf-8"))

    def test_a_provider_that_cannot_start_has_its_words_scrubbed_in_the_record(self) -> None:
        # The runner's own words for a program it can't start name the program's path; declared secret, it is redacted.
        secret = "s3cr3t-provider-path-from-the-environment"
        provider = {"argv": [str(self.directory / secret / "provider")], "cwd": ".", "timeoutSeconds": 3,
                    "providerInputs": ["tools/provider.py", "tools/provider-mode.txt"]}
        adopter = self.environment_adopter({"MQ_STEP_TOKEN": secret}, {"provider": provider})
        self.candidate(adopter)
        documents = [result.document() for result in self.run_queue(adopter, "feature")]
        missing = [record for document in documents if document.get("selection")
                   for record in document["selection"]["providers"] if record["outcome"] == "missing"]
        self.assertTrue(missing, documents)
        self.assertTrue(all("[redacted]" in record["detail"] for record in missing), missing)
        self.assertNotIn(secret, json.dumps(documents))
        for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE):
            self.assertNotIn(secret, (adopter.state / name).read_text(encoding="utf-8"))

    def test_a_refusal_from_the_candidate_s_own_provider_stops_it_too(self) -> None:
        adopter = self.adopter(self.STOPS)
        base = adopter.tip("main")
        # The base's provider selects; the candidate changes a provider input, so its own provider runs, and refuses.
        self.candidate(adopter, {"src/app.txt": "app two\n", "tools/provider-mode.txt": "refuse\n"})
        document = self.assertStopped(self.run_queue(adopter, "feature"), "provider_refused")
        self.assertIn("the candidate provider exited 3", document["stop"]["detail"])
        self.assertNotIn("the base provider", document["stop"]["detail"])
        self.refused(adopter)
        self.assertEqual(adopter.tip("main"), base)

    def test_only_a_non_zero_exit_is_a_refusal(self) -> None:
        for mode, reason in (("malformed", "provider_malformed"), ("timeout", "provider_timed_out")):
            with self.subTest(mode=mode):
                self.fresh()
                provider = {"argv": [PY, "tools/provider.py"], "cwd": ".", "timeoutSeconds": 1,
                            "providerInputs": ["tools/provider.py", "tools/provider-mode.txt"]}
                adopter = self.adopter({**self.STOPS, **({"provider": provider} if mode == "timeout" else {})}, mode=mode)
                self.candidate(adopter)
                results = self.run_queue(adopter, "feature")
                self.assertEqual(results[1].document()["selection"]["fallbackReasons"], [reason])
                self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])

    def test_the_lead_s_selection_releases_a_refused_candidate_and_is_what_runs(self) -> None:
        adopter = self.adopter(self.STOPS, mode="refuse")
        head = self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "provider_refused")
        # Wider than any budget the provider keeps: the lead's selection is bounded by checkTimeoutCeiling alone.
        ceiling = adopter.binding["checkTimeoutCeiling"]
        wide = [check_entry("focused", timeoutSeconds=ceiling), check_entry("wide", timeoutSeconds=ceiling)]
        with self.assertRaises(state_tool.StateError):
            state_tool.select_checks(adopter.state, "feature", binding=adopter.binding, head=head,
                                     checks=[check_entry("focused", timeoutSeconds=ceiling + 1)], reason="too long")
        state_tool.select_checks(adopter.state, "feature", binding=adopter.binding, head=head, checks=wide,
                                 reason="the lead judged it worth the wide run")
        self.requeue(adopter, "feature")
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertEqual(sorted(self.ran(adopter)), ["always", "focused", "wide"])
        self.assertEqual(adopter.item("feature")["checkSelection"],
                         {"by": "lead", "head": head, "reason": "the lead judged it worth the wide run"})

    def test_under_refusal_stops_a_providers_titles_and_the_lead_s_titled_selection_reach_the_record(self) -> None:
        # A provider that answers keeps its titles under refusalStops, as it does without the flag.
        adopter = self.adopter({**self.STOPS, "alwaysRun": [check_entry("always", title="Always-on smoke")]}, mode="titled")
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        rows = {check["id"]: check for check in adopter.item("feature")["checks"]}
        self.assertEqual(rows["unit"]["title"], "Unit tests (webapp) <b>&")
        self.assertEqual(rows["always"]["title"], "Always-on smoke")
        self.assertNotIn("title", rows["plain"])
        # A refused candidate the lead releases with titled checks shows the lead's titles, and an untitled one its id.
        self.fresh()
        adopter = self.adopter(self.STOPS, mode="refuse")
        head = self.candidate(adopter)
        self.assertStopped(self.run_queue(adopter, "feature"), "provider_refused")
        chosen = [check_entry("focused", title="Focused unit tests"), check_entry("wide")]
        state_tool.select_checks(adopter.state, "feature", binding=adopter.binding, head=head, checks=chosen,
                                 reason="the lead chose titled checks")
        self.requeue(adopter, "feature")
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        rows = {check["id"]: check for check in adopter.item("feature")["checks"]}
        self.assertEqual(rows["focused"]["title"], "Focused unit tests")
        self.assertNotIn("title", rows["wide"])

    def test_a_refused_batch_stops_every_member_and_names_each(self) -> None:
        adopter = self.adopter(self.STOPS, mode="refuse")
        base = adopter.tip("main")
        self.candidate(adopter, {"src/lib.txt": "lib two\n"}, name="first")
        self.candidate(adopter, {"src/app.txt": "app two\n"}, name="second")
        results = adopter.runner().run_command("batch", ["first", "second"])
        self.assertEqual({result.document()["stop"]["reason"] for result in results}, {"provider_refused"})
        for subject in ("first", "second"):
            self.refused(adopter, subject)
        self.assertEqual(adopter.tip("main"), base)

    def test_the_binding_rows(self) -> None:
        adopter = self.adopter()
        binding = adopter.binding
        # A binding without either key loads, as every binding before them did.
        self.assertNotIn("fullHealthTimeoutSeconds", binding)
        self.assertEqual(queue.binding_errors(binding), [])
        carrying = {**binding, "fullHealthTimeoutSeconds": binding["checkTimeoutCeiling"] * 100,
                    "selection": {"lead": True, "refusalStops": True}}
        self.assertEqual(queue.binding_errors(carrying), [])
        self.assertTrue(Draft202012Validator(schema("orchestration-binding")).is_valid(carrying))
        # The most a process wait takes is accepted; one more would overflow it, so the binding refuses it.
        self.assertEqual(queue.binding_errors({**binding, "fullHealthTimeoutSeconds": 2147483}), [])
        for value in (0, -1, 1.5, "600", True, None, 2147484, 1e300):
            with self.subTest(fullHealthTimeoutSeconds=value):
                errors = queue.binding_errors({**binding, "fullHealthTimeoutSeconds": value})
                self.assertTrue(errors)
                self.assertTrue(all("/fullHealthTimeoutSeconds" in error for error in errors), errors)
                if value not in (2147484, 1e300):  # the schema says integer; the runner checks the bound
                    self.assertFalse(Draft202012Validator(schema("orchestration-binding")).is_valid(
                        {**binding, "fullHealthTimeoutSeconds": value}))
        for value in (1, "true", None):
            with self.subTest(refusalStops=value):
                errors = queue.binding_errors({**binding, "selection": {"lead": True, "refusalStops": value}})
                self.assertTrue(errors)
                self.assertTrue(all(error.startswith("/selection/refusalStops") for error in errors), errors)
        self.assertEqual(queue.binding_errors({**binding, "selection": {"lead": False, "refusalStops": True}}),
                         ["/selection/refusalStops: needs selection.lead, since only the lead's selection releases a "
                          "candidate the provider refused"])
