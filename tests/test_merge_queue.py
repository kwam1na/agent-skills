from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

    def enqueue(self, subject: str, head: str, lead_inputs: list[str] | None = None) -> None:
        state = state_tool.read_state(self.state)
        item = {
            "order": len(state["queue"]) + 1, "subject": subject, "title": f"Candidate {subject}",
            "lane": "lane-1", "stage": "queued", "head": head, "syncedHead": None,
            "leadInputs": lead_inputs or [], "reportedRounds": 2, "runId": None, "checks": [],
        }
        state_tool.patch(self.state, writer="lead", version=state["version"], changes={"queue": [*state["queue"], item]})

    def lead_set(self, subject: str, **fields: object) -> None:
        state = state_tool.read_state(self.state)
        state_tool.patch(self.state, writer="lead", version=state["version"], selector=f"queue[subject={subject}]", changes=fields)

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
                adopter = self.adopter(mode=mode)
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

    def test_the_schema_states_every_work_root_rule(self) -> None:
        description = schema("orchestration-binding")["properties"]["workRoot"]["description"]
        for phrase in (
            "top level", "any checkout of the repository", "git's own folders", "tracked", "ignored", "submodule",
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
        self.assertEqual(seen["steps"], self.as_steps({"full": "running"}))
        self.assertEqual(seen["queue"], self.as_checks({"full": "running"}))

    def test_a_lead_write_to_another_item_during_a_check_is_kept(self) -> None:
        lead_writes = check_entry("lead-writes", "--lead-writes")
        adopter = self.snapshot_adopter("fail", {"fallback": [lead_writes], "alwaysRun": [check_entry("after")]})
        self.candidate(adopter)
        adopter.enqueue("other", adopter.tip("main"))
        with mock.patch.dict(os.environ, {"MQ_SCRIPTS": str(SCRIPTS)}):
            results = self.run_queue(adopter, "feature")
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document()["stop"])
        self.assertEqual(adopter.item("other")["title"], "Renamed by the lead")
        self.assertEqual(adopter.item("feature")["checks"], self.as_checks({"after": "passed", "lead-writes": "passed"}))

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
        # Pending, then each check running and then finished, with d skipped.
        self.assertEqual(len(writes), 8)

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
        self.assertEqual(adopter.item("feature")["checks"][0], {"id": "slow", "state": "timed_out"})

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
            adopter.item("feature")["checks"], self.as_checks({"a": "passed", "b": "pending", "c": "pending", "d": "pending"}),
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
                self.assertEqual(seen[name]["steps"], self.as_steps(states))
                self.assertEqual(seen[name]["queue"], self.as_checks(states))
        # After the run the queue keeps every check's final state; d never ran because b failed.
        final = {"a": "passed", "b": "failed", "c": "passed", "d": "skipped"}
        self.assertEqual(adopter.item("feature")["checks"], self.as_checks(final))

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
        # Nothing that was not checked landed: the base holds the push, not the second candidate.
        self.assertEqual(adopter.show(adopter.tip("main"), "src/app.txt"), "app one")
        self.assertEqual(adopter.item("second")["stage"], "queued")
        self.assertEqual(adopter.item("third")["stage"], "queued")

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


if __name__ == "__main__":
    unittest.main()
