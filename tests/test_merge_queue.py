from __future__ import annotations

import contextlib
import copy
import io
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from agent_skills.validate import LEAKAGE_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/orchestrate-work"
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
import json, pathlib, sys, time
request = json.load(sys.stdin)
mode = pathlib.Path("tools/provider-mode.txt").read_text().strip()
py = sys.executable
def check(cid, *args, **more):
    item = {"id": cid, "argv": [py, "tools/check.py", cid, *args], "cwd": ".", "reasons": ["changed"], "prerequisites": []}
    item.update(more)
    return item
if mode == "fail":
    sys.exit(2)
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
print(json.dumps({"checks": checks, "widened": False, "uncovered": uncovered}))
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
if os.environ.get("MQ_MERGE") == "refuse" or tip(branch) != expected:
    sys.exit(1)
base = tip("main")
git("fetch", "--quiet", remote, "+refs/heads/main:refs/forge/main", "+refs/heads/" + branch + ":refs/forge/candidate")
tree = git("rev-parse", expected + "^{tree}")
if os.environ.get("MQ_MERGE") == "unverified":
    tree = git("rev-parse", base + "^{tree}")
parents = ["-p", expected, "-p", base] if os.environ.get("MQ_MERGE") == "swapped" else ["-p", base, "-p", expected]
if os.environ.get("MQ_MERGE") == "squash":
    # One new commit on the base with the candidate's tree, as a squash merge makes.
    parents = ["-p", base]
commit = git("commit-tree", tree, *parents, "-m", "Merge " + branch)
# The forge is the adopter's own tool: it, not the runner, owns its push settings.
git("-c", "push.gpgSign=false", "push", "--quiet", "--force-with-lease=refs/heads/main:" + base, remote, commit + ":refs/heads/main")
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
        self.lane = root / "lane"
        self.state = root / "state"
        self.work = root / "work"
        self.log = root / "log.txt"
        self.log.write_text("")
        run("init", "--quiet", "--bare", "--initial-branch=main", str(self.remote), cwd=root)
        seed = root / "seed"
        run("clone", "--quiet", str(self.remote), str(seed), cwd=root)
        run("checkout", "--quiet", "-b", "main", cwd=seed)
        for name, text in TOOLS.items():
            self.write(seed, f"tools/{name}", text.lstrip())
        self.write(seed, "tools/provider-mode.txt", mode + "\n")
        self.write(seed, "src/app.txt", "app one\n")
        self.write(seed, "src/lib.txt", "lib one\n")
        self.write(seed, "gen/generated.txt", "app one\nlib one\n")
        self.write(seed, "tests/test_base.txt", "ok\n")
        self.binding = self.default_binding()
        for key, value in (binding_changes or {}).items():
            self.binding[key] = value
        self.write(seed, ".agents/orchestration.json", json.dumps(self.binding, indent=2) + "\n")
        run("add", "--all", cwd=seed)
        run("commit", "--quiet", "-m", "base", cwd=seed)
        run("push", "--quiet", "origin", "main", cwd=seed)
        run("clone", "--quiet", str(self.remote), str(self.operator), cwd=root)
        run("clone", "--quiet", str(self.remote), str(self.lane), cwd=root)
        state_tool.init(self.state, "Fixture delivery")

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
        path.write_text(text)

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


class MergeQueueTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="merge-queue-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.directory, True)

    def adopter(self, binding_changes: dict | None = None, mode: str = "normal") -> Adopter:
        return Adopter(self.directory, binding_changes, mode)

    def fresh(self) -> None:
        shutil.rmtree(self.directory)
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
        adopter = self.environment_adopter({"MQ_WITHDRAW": "fail"})
        head = adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        adopter.enqueue("feature", head, lead_inputs=["api"])
        adopter.grant("backend")
        self.assertStopped(self.run_queue(adopter, "feature"), "lease_withdraw_failed")
        self.assertEqual(adopter.tip("feature"), head)

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
                shutil.rmtree(self.directory)
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
        shutil.rmtree(self.directory)
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
        shutil.rmtree(self.directory)
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
                shutil.rmtree(self.directory)
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
        shutil.rmtree(self.directory)
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
        self.assertIn(str(stale), run("worktree", "list", cwd=adopter.operator))
        self.run_queue(adopter, "feature")
        self.assertFalse(stale.exists())
        self.assertNotIn(str(stale), run("worktree", "list", cwd=adopter.operator))
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
        self.assertTrue(any(str(leftover) in line for line in
                            run("worktree", "list", "--porcelain", cwd=adopter.operator).splitlines()))
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertFalse(any(str(leftover) in line for line in
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
        # Synced, then the setup went: the candidate is queued again as it was, not left synced or stopped.
        self.assertEqual((item["stage"], item["syncedHead"], item["checks"]), ("queued", None, []))
        self.assertIn("top level of a checkout", state_tool.read_state(adopter.state)["meta"]["hold"]["reason"])
        self.assertEqual(len(self.blockers(adopter)), 1)
        restore[0]()
        self.release_hold(adopter)
        self.serve(adopter)
        self.assertEqual(adopter.item("feature")["stage"], "merged")

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
        detail = "fatal: unable to access 'remote':\n  Could not resolve host\n" + "x " * 400
        queue.hold_for_setup(adopter.state, detail)
        reason = state_tool.read_state(adopter.state)["meta"]["hold"]["reason"]
        self.assertNotIn("\n", reason)
        self.assertLessEqual(len(reason), 240)
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
        self.assertEqual(serving.returncode, 0, errors.decode("utf-8", "replace"))
        self.assertEqual(printed[-1]["candidate"], "feature")
        self.assertEqual(printed[-1]["outcome"], "merged")

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
        adopter = self.adopter()
        self.wrapper(1)
        self.candidate(adopter)
        results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged")
        self.assertIn("rendering the state exited 1", results[-1].document()["notes"])

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
