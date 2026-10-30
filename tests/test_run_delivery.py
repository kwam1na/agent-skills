"""The run-delivery tool, characterized as it was moved in.

Each case below is one of the tool's own self-test cases, ported unchanged in
what it asserts. The tool runs from an installed `run-delivery` release built
from this source tree, on a throwaway fixture repository with a local bare
origin, a fake forge adapter, a fake in-app build, serve and check, and a
two-story plan. Nothing outside a temporary folder is touched: no network, no
real forge, no real application.

The tool always runs as a subprocess with `-B`: its commands through its own
command line, and the functions the cases inspect through a small probe that
imports it in a fresh interpreter. The test runner's workers therefore keep
their own signal handlers, working directory and module state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills import release
from agent_skills.lifecycle import Lifecycle
from agent_skills.release import build_release, verify_release
from agent_skills.validate import LEAKAGE_PATTERNS


# The cases share one fixture repository's in-app lease lock and the
# environment variables its fake adapters read, and the timeout case looks for
# leftover processes machine-wide.
RUN_SERIALLY = True

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/run-delivery"
PROFILE = "run-delivery"
TOOL_RELATIVE = "skills/run-delivery/scripts/run_delivery.py"
# The current qualification set's core product carries the runtime this profile's product is rebound from.
RUNTIME_SOURCE = ROOT / "qualifications/product/core-plan-review-v1.zip"
RUNTIME_VERSION = "0.7.4"
# The fixture page adapter-test serves, byte for byte as the tool's own fixture page was before it moved in.
ADAPTER_FIXTURE_SHA256 = "31257fd55d83ebc6aa602b7a508248d6f44776991f54e1127ce1629b0bf59a81"
POSIX_ONLY = "the run-delivery tool is POSIX-only: it uses fcntl, process groups and sh"
# First-adopter terms, pinned as sha256 digests of lowercase words so that this
# file does not itself carry them. A shipped file fails when any word in it
# hashes to one of these. Add a term by appending the digest of its lowercase
# form.
FIRST_ADOPTER_TERM_DIGESTS = frozenset({
    "09885e8a9efdbe55a497df803d1228cbe0b43e873a1d6b401f0850e96124ccf1",
    "7b441ddffe939f90a2c8bc751aee62ddee9d6d1eeb157e8be13680e19e55e825",
})


class Stopped(Exception):
    """The tool refused, raising its own Stop; `elapsed` is how long the call itself ran before it did."""

    def __init__(self, message: str, elapsed: float) -> None:
        super().__init__(message)
        self.elapsed = elapsed


def sh(*argv, cwd=None, env=None, check=True, input=None):
    done = subprocess.run([str(a) for a in argv], cwd=cwd, env=env, text=True, capture_output=True, input=input)
    if check and done.returncode != 0:
        raise AssertionError(f"{argv} failed: {done.stdout[-400:]}{done.stderr[-400:]}")
    return done


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def commit(cwd: Path, message: str):
    sh("git", "add", "-A", cwd=cwd)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message, cwd=cwd)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def clean_environment() -> dict[str, str]:
    """The test runner's environment, without anything the tool or the fake adapters read."""
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("DELIVERY_FLOW_", "RUN_DELIVERY_", "FAKE_FORGE_"))}


# -- running the tool ---------------------------------------------------------------------------------

PROBE = r'''
import json, os, subprocess, sys, time
from pathlib import Path
request = json.load(sys.stdin)
sys.path.insert(0, request["tool"])
import run_delivery as flow
def decode(value):
    if isinstance(value, dict):
        return Path(value["__path__"]) if set(value) == {"__path__"} else {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value
def plain(value):
    if isinstance(value, subprocess.CompletedProcess):
        return {"returncode": value.returncode, "stdout": value.stdout}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    return value
os.chdir(request["cwd"])
try:
    if request.get("delivery"):
        flow.CFG = flow.Config(Path(request["cwd"]), request["delivery"])
    kwargs = decode(request["kwargs"])
    for name in request["sets"]:
        kwargs[name] = set(kwargs[name])
    function, args = getattr(flow, request["fn"]), decode(request["args"])
    started = time.monotonic()
    value = function if request.get("attr") else function(*args, **kwargs)
    if request.get("then"):
        getattr(value, request["then"])()
        value = None
    if request.get("attrs"):
        value = {name: getattr(value, name) for name in request["attrs"]}
    print(json.dumps({"value": plain(value)}))
except flow.Stop as error:
    print(json.dumps({"stop": str(error), "elapsed": time.monotonic() - started}))
'''


def encode(value):
    if isinstance(value, Path):
        return {"__path__": str(value)}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def call(tool: Path, fn: str, *args, cwd: Path, env: dict[str, str] | None = None, delivery: str | None = None,
         then: str | None = None, attrs: list[str] | None = None, sets: tuple[str, ...] = (), attr: bool = False,
         **kwargs):
    """Call one of the tool's functions in a fresh `-B` interpreter inside `cwd`; its Stop becomes Stopped."""
    request = {"tool": str(tool.parent), "cwd": str(cwd), "fn": fn, "args": encode(list(args)),
               "kwargs": encode(kwargs), "sets": list(sets), "delivery": delivery, "then": then, "attrs": attrs,
               "attr": attr}
    done = subprocess.run([sys.executable, "-B", "-c", PROBE], input=json.dumps(request), cwd=cwd,
                          env=env if env is not None else clean_environment(), text=True, capture_output=True,
                          timeout=300)
    if done.returncode != 0:
        raise AssertionError(f"{fn} failed: {done.stdout[-1500:]}{done.stderr[-1500:]}")
    answer = json.loads(done.stdout.strip().splitlines()[-1])
    if "stop" in answer:
        raise Stopped(answer["stop"], answer["elapsed"])
    return answer["value"]


# -- the fixture ----------------------------------------------------------------------------------

FAKE_FORGE = r'''
import argparse, json, os, subprocess, sys
state = os.environ["FAKE_FORGE_STATE"]
prs = json.load(open(state)) if os.path.exists(state) else {}
p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="c")
a = sub.add_parser("find"); a.add_argument("--source")
a = sub.add_parser("open"); a.add_argument("--source"); a.add_argument("--destination"); a.add_argument("--title"); a.add_argument("--body-file")
a = sub.add_parser("get"); a.add_argument("--url")
a = sub.add_parser("reviews"); a.add_argument("--url"); a.add_argument("--head")
args = p.parse_args()
if args.c == "reviews":
    path = os.environ.get("FAKE_FORGE_REVIEWS", "")
    print(open(path).read() if path and os.path.exists(path) else json.dumps({"checks": [], "reviews": []}))
    sys.exit(0)
def head(branch):
    line = subprocess.run(["git", "ls-remote", "origin", "refs/heads/" + branch], capture_output=True, text=True).stdout
    return line.split("\t")[0][:12]
if args.c == "find":
    url = next((u for u, pr in prs.items() if pr["source"] == args.source and pr["state"] == "open"), None)
    print(json.dumps({"url": url} if url else {}))
elif args.c == "open":
    url = "https://forge.test/pr/%d" % (len(prs) + 1)
    prs[url] = {"source": args.source, "destination": args.destination, "state": "open", "title": args.title}
    json.dump(prs, open(state, "w")); print(json.dumps({"url": url}))
else:
    pr = prs.get(args.url)
    if not pr:
        sys.exit("no such PR " + args.url)
    print(json.dumps({**pr, "url": args.url, "head": head(pr["source"])}))
'''

FAKE_CHECK = r'''
import datetime, json, sys, time, urllib.request
spec = json.load(open(sys.argv[1])); port = sys.argv[2]
stamp = lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
try:
    build = json.load(urllib.request.urlopen("http://127.0.0.1:%s/.flow-build.json" % port))
except Exception:
    build = None
held = [{"regex": h, "url": "http://api.test" + h.strip("^$"), "heldAt": stamp(), "releasedAt": None} for h in spec["holds"]]
failed = [{"regex": f, "url": "http://api.test" + f.strip("^$"), "status": 500, "at": stamp()} for f in spec["fails"]]
if spec["path"] == "/slow":
    time.sleep(30)
time.sleep(0.01)
evals = [{"expr": e, "result": "false" not in e, "evalAt": stamp()} for e in spec["evals"]]
time.sleep(0.01)
for h in held:
    h["releasedAt"] = stamp()
after = [{"expr": e, "result": "false" not in e, "firstResult": True, "polls": 1, "afterAt": stamp()} for e in spec["afterEvals"]]
video = None
if spec.get("video"):
    import os, subprocess
    os.makedirs(spec["video"], exist_ok=True)
    video = os.path.join(spec["video"], "run.webm")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", ("testsrc" if spec["holds"] else "testsrc2") + "=size=320x200:rate=10:duration=1",
                    "-f", "lavfi", "-i", "color=c=0x808080:size=320x40:rate=10:duration=1",
                    "-filter_complex", "[0:v][1:v]vstack", video], check=True)
print(json.dumps({"ready": True, "build": build, "held": held, "failed": failed, "evals": evals, "afterEvals": after,
                  "afterForFound": None, "alertsSeen": [], "pageErrors": [], "consoleErrors": [x for x in sys.argv[3:]],
                  "video": video}))
'''

PLAN = """# Fixture delivery

## Decisions

| Decision | Choice |
| --- | --- |
| Copy | "Couldn't be loaded." |

## Stories

### S1 (FX-2). Thing one (AC1). Regex `/api/one$`

- Scenarios: it loads; it fails.

### S2 (FX-3). Thing two (AC2)

- Scenarios: it renders.

| AC | Story | Criterion |
| --- | --- | --- |
| AC1 | S1 | one works |
| AC2 | S2 | two works |
"""

CONTEXT_TODO = "TODO: replace this line with your evidence so far"


# Host terms no shipped file or command output may carry outside an example block marked as one: the session,
# messaging and publishing tool names the tool's earlier text used, host tool prefixes, and host projection paths.
HOST_TERMS = (
    ("session tool", re.compile(r"start_session")),
    ("messaging tool", re.compile(r"SendMessage")),
    ("publishing tool", re.compile(r"\bArtifact\b")),
    ("host name", re.compile(r"Claude Code")),
    ("host meta tag", re.compile(r"claude:")),
    ("host tool prefix", re.compile(r"mcp__")),
    ("host projection path", re.compile(r"\.claude/|~/\.claude|\.agents/skills")),
)
# A fenced block whose info string carries the word `example` is a marked example.
EXAMPLE_BLOCK = re.compile(r"^```[^\n]*\bexample\b[^\n]*\n.*?^```[ \t]*$", re.MULTILINE | re.DOTALL)


def host_terms(text: str) -> list[str]:
    outside = EXAMPLE_BLOCK.sub("", text)
    return [name for name, pattern in HOST_TERMS if pattern.search(outside)]


class Fixture:
    def __init__(self, generation: Path, receipt: Path):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-delivery-characterization-"))
        self.origin = self.tmp / "origin.git"
        self.main = self.tmp / "main"
        self.port = free_port()
        self.release = generation
        self.tool = generation / TOOL_RELATIVE
        self.env = {**clean_environment(), "FAKE_FORGE_STATE": str(self.tmp / "forge.json")}
        sh("git", "init", "-q", "--bare", "-b", "main", self.origin)
        sh("git", "clone", "-q", self.origin, self.main)
        m = self.main
        (m / "src").mkdir()
        (m / "src/app.txt").write_text("app\n")
        (m / "docs").mkdir()
        (m / "docs/plan.md").write_text(PLAN)
        (m / ".gitignore").write_text(".orchestration/\nout/\n")
        (m / ".agent-skills").mkdir()
        (m / ".agent-skills/current").symlink_to(generation)
        shutil.copy2(receipt, m / ".agent-skills/active.json")
        (m / "tools").mkdir()
        (m / "tools/fake_forge.py").write_text(FAKE_FORGE)
        (m / "tools/fake_check.py").write_text(FAKE_CHECK)
        commit(m, "product")
        sh("git", "push", "-q", "origin", "main", cwd=m)
        sh("git", "checkout", "-q", "-b", "feature/FX-1", cwd=m)
        repo_config = {
            "schemaVersion": "delivery-flow/1", "branchPattern": "feature/{key}", "laneCheckout": "lanes/{key}",
            "tracker": {"issueUrl": "https://tracker.test/{key}"},
            "forge": {"argv": [sys.executable, "-B", "tools/fake_forge.py"]},
            "review": {"bound": 3, "prepare": []},
            "gates": [{"id": "lint", "argv": ["sh", "-c", "test -f src/app.txt"]}, {"id": "test", "argv": ["true"]},
                      {"id": "build", "argv": ["sh", "-c", "mkdir -p out && cp src/*.txt out/"]}],
            "neverTouch": [".agents", "tools/"],
            "inApp": {"lease": "app", "lock": str(self.tmp / "app.lock"), "port": self.port,
                      "build": {"argv": ["sh", "-c", "mkdir -p out && cp src/*.txt out/"], "artifact": "out"},
                      "serve": {"argv": [sys.executable, "-m", "http.server", "{port}", "--bind", "127.0.0.1", "--directory", "{dir}"]},
                      "check": {"argv": [sys.executable, "-B", "tools/fake_check.py", "{spec}", "{port}", "{args}"]},
                      "baseline": str(self.tmp / "baseline"),
                      "consoleErrorPattern": "^ERROR", "lockWaitSeconds": 5, "checkTimeoutSeconds": 5},
        }
        (m / ".agents").mkdir()
        (m / ".agents/run-delivery.json").write_text(json.dumps(repo_config, indent=2))
        commit(m, "FX-1: tooling")
        self.tooling = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        sh("git", "push", "-q", "-u", "origin", "feature/FX-1", cwd=m)
        (self.tmp / "baseline").mkdir()
        (self.tmp / "baseline/app.txt").write_text("app\n")
        self.call("write_stamp", self.tmp / "baseline", "b" * 40, "c" * 40, cwd=self.tmp)

    def flow(self, *argv, cwd: Path, tool: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
        """Run the tool's command line in `cwd`, returning (exit code, combined output). Every output is scanned for
        host terms."""
        done = subprocess.run([sys.executable, "-B", str(tool or self.tool), *[str(a) for a in argv]], cwd=cwd,
                              env=env or self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=300)
        expect("Traceback (most recent call last)" not in done.stdout, f"{argv[:2]} crashed: {done.stdout[-2000:]}")
        expect(not host_terms(done.stdout), f"{argv[:2]} printed host terms {host_terms(done.stdout)}: {done.stdout[-2000:]}")
        return done.returncode, done.stdout

    def call(self, fn: str, *args, cwd: Path, **kwargs):
        return call(self.tool, fn, *args, cwd=cwd, env=self.env, **kwargs)

    def lenses(self, path: Path) -> list:
        """The resolved lenses, as the cases use them: [(lens id, (short name, charter path))]."""
        lenses = self.call("Config", path, "FX-1", cwd=path, attrs=["lenses"])["lenses"]
        return [(lens, tuple(value)) for lens, value in lenses.items()]

    def lane(self, key: str) -> Path:
        path = self.main / "lanes" / key
        sh("git", "fetch", "-q", "origin", cwd=self.main)
        sh("git", "worktree", "add", "-q", "--no-track", "-b", f"feature/{key}", path, "origin/feature/FX-1", cwd=self.main)
        return path

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def report(**over):
    base = {"ready": True, "build": {"head": "h"}, "pageErrors": [], "consoleErrors": [], "alertsSeen": [],
            "evals": [{"expr": "x", "result": True, "evalAt": "2026-01-01T00:00:02Z"}],
            "afterEvals": [{"expr": "y", "result": True, "afterAt": "2026-01-01T00:00:04Z"}],
            "held": [{"regex": "/a$", "url": "u", "heldAt": "2026-01-01T00:00:01Z", "releasedAt": "2026-01-01T00:00:03Z"}],
            "failed": [], "afterForFound": None}
    base.update(over)
    return base


META = {"candidate": "c" * 40, "tree": "t" * 40}


def lens_result(**over):
    base = {"schemaVersion": "review-lens-result/1", "lensId": "lens.l", "subjectRef": "K", "candidateRef": META["candidate"],
            "round": 1, "outcome": "aligned", "findings": [], "evidence": [f"tree {META['tree']} checked"]}
    base.update(over)
    return base


PATTERN = r"^### (?P<label>S\d+) \((?P<key>FX-\d+)\)\. (?P<title>[^(\n]+?)(?: \(AC\d+\))?(?:\. Regex (?P<pinned>.*))?$"


def setup_delivery(fx: Fixture) -> Path:
    """The lead's setup: draft the delivery file, complete it from the plan, commit and push it."""
    m = fx.main
    code, out = fx.flow("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                        "--ship-from", "origin/main", "--context-section", "Decisions", cwd=m)
    expect(code == 0 and "2 stories" in out, out)
    path = m / ".agents/deliveries/FX-1.json"
    d = json.loads(path.read_text())
    expect(d["stories"]["FX-2"]["pinned"] == ["/api/one$"] and d["ship"]["toolingCommit"] == fx.tooling[:12], d)
    expect(d["stories"]["FX-2"].get("evidence") == "video" and "evidence" not in d["stories"]["FX-3"]
           and [f["id"] for f in d["finish"]] == ["evidence-page"], f"lead-setup didn't mark video evidence by rule: {d}")
    del d["stories"]["FX-2"]["evidence"]  # the other cases don't exercise video; its own case turns it back on
    code, out = fx.flow("lead-init", cwd=m)
    expect(code == 2 and "unassigned" in out, f"lead-init ran with unassigned stories: {out}")
    d["stories"]["FX-2"]["lane"] = "lane-1"
    d["stories"]["FX-3"].update(lane="lane-2", inApp="loaded")
    d["lanes"] = {"lane-1": {"owns": ["src/one*"]}, "lane-2": {"owns": ["src/two*"]}}
    d["finish"] = [{"id": "acs", "text": "AC1-AC2 met"}]
    d["stateDirectory"] = str(fx.tmp / "record")
    d["laneBrief"] = "docs/lane-brief.md"
    d["authority"] = {"push": True, "draftPullRequests": True, "grantedBy": "the operator", "grantedOn": "2026-01-01"}
    path.write_text(json.dumps(d, indent=2))
    commit(m, "FX-1: delivery")
    sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
    return path


def write_results(review: Path, n: int, key: str, lenses, findings=None, resolved=None, candidate=None, only=None):
    meta = json.loads((review / f"round-{n}.meta.json").read_text())
    for i, (lens, (short, _)) in enumerate(lenses):
        if only is not None and i != only:
            continue
        result = {"schemaVersion": "review-lens-result/1", "lensId": lens, "subjectRef": key,
                  "candidateRef": candidate or meta["candidate"], "round": n, "outcome": "aligned",
                  "findings": [], "evidence": [f"tree {meta['tree']}"]}
        if i == 0 and findings:
            result.update(outcome="changes-requested", findings=findings)
        if i == 0 and resolved:
            result["resolvedFindings"] = resolved
        (review / f"result-r{n}-{short}.json").write_text(json.dumps(result))


def remote_has(m: Path, branch: str) -> bool:
    return bool(sh("git", "ls-remote", "origin", f"refs/heads/{branch}", cwd=m).stdout.strip())


def bytecode_under(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.name == "__pycache__" or p.suffix == ".pyc")


def find_node22(tool: Path) -> str | None:
    return call(tool, "find_node22", cwd=Path(tempfile.gettempdir()))


RELEASE_CACHE: dict[str, types.SimpleNamespace] = {}


def installed_release() -> types.SimpleNamespace:
    """The run-delivery release built from this tree and installed once, shared by every class in the module."""
    if "release" in RELEASE_CACHE:
        return RELEASE_CACHE["release"]
    built = types.SimpleNamespace()
    built.build = Path(tempfile.mkdtemp(prefix="run-delivery-release-"))
    unittest.addModuleCleanup(shutil.rmtree, built.build, True)
    # The runtime the current core product carries, with only its descriptor's workflow digest rebound to this
    # profile's workflow projection.
    runtime = built.build / "runtime"
    runtime.mkdir()
    with zipfile.ZipFile(RUNTIME_SOURCE) as source:
        for name in release.RUNTIME_FILES:
            (runtime / name).write_bytes(source.read(f"runtime/{name}"))
    descriptor_text = (runtime / "runtime.json").read_text(encoding="utf-8")
    built.descriptor = json.loads(descriptor_text)
    view = release._release_view(ROOT, PROFILE)
    built.workflow_digest = release._path_digest(sorted(view), view)
    rebound = descriptor_text.replace(built.descriptor["workflowContentSha256"], built.workflow_digest)
    (runtime / "runtime.json").write_text(rebound, encoding="utf-8")
    built.rebound = json.loads(rebound)
    built.archive = built.build / f"{PROFILE}.zip"
    built.metadata = built.build / f"{PROFILE}.release.json"
    build_release(ROOT, built.archive, built.metadata, release_id="run-delivery-characterization",
                  profile_id=PROFILE, runtime_directory=runtime)
    built.verified = verify_release(built.archive, built.metadata)
    # One installed generation every fixture repository links to, as `.agent-skills/current`.
    built.install = built.build / "install"
    built.install.mkdir()
    sh("git", "init", "-q", cwd=built.install)
    (built.install / "README.md").write_text("install\n")
    commit(built.install, "Initial")
    with mock.patch.dict(os.environ, {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""}):
        Lifecycle(built.install).install(built.archive, built.metadata, maintainer=True)
    built.generation = (built.install / ".agent-skills/current").resolve()
    built.receipt = built.install / ".agent-skills/active.json"
    built.tool = built.generation / TOOL_RELATIVE
    RELEASE_CACHE["release"] = built
    return built


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryCharacterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def tearDown(self) -> None:
        self.assertEqual(bytecode_under(self.generation), [], "the case left bytecode in the installed generation")

    def fixture(self) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        return fx

    def call(self, fn: str, *args, cwd: Path | None = None, **kwargs):
        return call(self.tool, fn, *args, cwd=cwd or Path(tempfile.gettempdir()), **kwargs)

    # -- the release the cases run from ----------------------------------------------------------------

    def test_the_built_release_carries_the_rebound_runtime_and_verifies(self) -> None:
        self.assertEqual(self.descriptor["runtimeVersion"], RUNTIME_VERSION)
        self.assertEqual(self.descriptor["nodeMinimum"], "22.6.0")
        self.assertEqual({k: v for k, v in self.rebound.items() if k != "workflowContentSha256"},
                         {k: v for k, v in self.descriptor.items() if k != "workflowContentSha256"})
        self.assertEqual(self.rebound["workflowContentSha256"], self.workflow_digest)
        self.assertEqual(self.verified.profile_id, PROFILE)
        with zipfile.ZipFile(self.archive) as opened:
            names = set(opened.namelist())
        self.assertEqual({name for name in names if name.startswith("runtime/")},
                         {f"runtime/{name}" for name in release.RUNTIME_FILES})
        self.assertTrue((self.generation / "runtime/loader.mjs").is_file())
        self.assertTrue(self.tool.is_file())

    def test_the_installed_generation_has_the_skill_byte_for_byte(self) -> None:
        shipped = sorted(str(p.relative_to(SKILL)) for p in SKILL.rglob("*") if p.is_file())
        installed = sorted(str(p.relative_to(self.generation / "skills/run-delivery"))
                           for p in (self.generation / "skills/run-delivery").rglob("*") if p.is_file())
        self.assertEqual(installed, shipped)
        for relative in shipped:
            self.assertEqual((self.generation / "skills/run-delivery" / relative).read_bytes(),
                             (SKILL / relative).read_bytes(), relative)

    # -- what moving the tool changed ------------------------------------------------------------------

    def test_importing_the_tool_installs_no_signal_handler(self) -> None:
        script = ("import json, signal, sys; sys.path.insert(0, sys.argv[1]); "
                  "before = [signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)]; import run_delivery as flow; "
                  "print(json.dumps(before == [signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)]))")
        done = sh(sys.executable, "-B", "-c", script, self.tool.parent)
        self.assertEqual(done.stdout.strip(), "true")

    def test_loading_the_release_validator_writes_no_bytecode(self) -> None:
        # Bytecode writing is on when the release's validator loads: the tool itself keeps the release's modules
        # from writing bytecode into the generation.
        fx = self.fixture()
        script = ("import sys, os; sys.path.insert(0, sys.argv[1]); os.chdir(sys.argv[2]); "
                  "sys.dont_write_bytecode = True; import run_delivery as flow; sys.dont_write_bytecode = False; "
                  "from pathlib import Path; flow.CFG = flow.Config(Path.cwd(), need_delivery=False); flow._validator(); assert 'delivery_state' in sys.modules")
        environment = {k: v for k, v in fx.env.items() if k != "PYTHONDONTWRITEBYTECODE"}
        sh(sys.executable, "-c", script, self.tool.parent, fx.main, env=environment)
        self.assertEqual(bytecode_under(self.generation), [])

    def test_adapter_test_uses_the_shipped_fixture_page(self) -> None:
        page = self.generation / "skills/run-delivery/assets/adapter-fixture/index.html"
        self.assertEqual(hashlib.sha256(page.read_bytes()).hexdigest(), ADAPTER_FIXTURE_SHA256)
        self.assertEqual(sorted(p.name for p in page.parent.iterdir()), ["index.html"])
        source = self.tool.read_text(encoding="utf-8")
        self.assertIn('TOOL / "assets/adapter-fixture"', source)
        self.assertNotIn("fixtures/app", source)

    def test_no_bytecode_is_left_under_the_source_skills(self) -> None:
        self.assertEqual(bytecode_under(ROOT / "skills"), [])

    # -- pure parts -------------------------------------------------------------------------------------

    def verdict(self, rep, mode="hold", spec=None, pinned=("/a$",), head="h", allowed=()):
        spec = spec if spec is not None else {"holds": ["/a$"], "fails": []}
        return self.call("rules", rep, mode=mode, spec=spec, pinned=list(pinned), build_head=head,
                         allowed_errors=list(allowed), error_pattern="^ERROR", sets=("allowed_errors",))

    def test_rules_accept_a_real_check_and_refuse_loose_ones(self) -> None:
        verdict = self.verdict
        expect(all(verdict(report()).values()), verdict(report()))
        bad = {
            "no evals": report(evals=[]), "no after-evals": report(afterEvals=[]),
            "string result": report(evals=[{"expr": "x", "result": "false", "evalAt": "2026-01-01T00:00:02Z"}]),
            "truthy 1": report(evals=[{"expr": "x", "result": 1, "evalAt": "2026-01-01T00:00:02Z"}]),
            "eval before hold": report(evals=[{"expr": "x", "result": True, "evalAt": "2026-01-01T00:00:00Z"}]),
            "eval after release": report(evals=[{"expr": "x", "result": True, "evalAt": "2026-01-01T00:00:05Z"}]),
            "nothing held": report(held=[]), "not ready": report(ready=False), "page error": report(pageErrors=["x"]),
            "console error": report(consoleErrors=["ERROR x"]), "another build": report(build={"head": "z"}),
            "no build stamp": report(build=None), "after-for missing": report(afterForFound=False),
        }
        for name, rep in bad.items():
            expect(not all(verdict(rep).values()), f"{name} passed")
        expect(not all(verdict(report(), spec={"holds": ["/b$"], "fails": []}).values()), "an unpinned pattern passed")
        expect(not all(verdict(report(), pinned=("/a$", "/c$"), spec={"holds": ["/a$|/c$"], "fails": []}).values()),
               "a combined pattern passed")
        expect(not all(verdict(report(), mode="loaded").values()), "loaded mode with holds passed")
        expect(all(verdict(report(consoleErrors=["ERROR old"]), allowed=("ERROR old",)).values()), "a baseline error failed")
        late = report(held=report()["held"] + [{"regex": "/a$", "url": "u", "heldAt": "2026-01-01T00:00:05Z",
                                                "releasedAt": "2026-01-01T00:00:05Z", "afterRelease": True}])
        expect(all(verdict(late).values()), f"a re-fetch after the release failed a good hold: {verdict(late)}")
        expect(not all(verdict(report(alertsSeen=["failed"])).values()), "a transient alert during a hold passed")
        no_alerts = report()
        no_alerts.pop("alertsSeen", None)
        expect(not all(verdict(no_alerts).values()), "a hold report without alertsSeen passed")
        no_at = report(held=[], afterEvals=[], failed=[{"regex": "/a$", "url": "u", "status": 500}])
        expect(not all(verdict(no_at, mode="fail", spec={"holds": [], "fails": ["/a$"]}).values()), "a failure with no time passed")
        late_fail = report(held=[], afterEvals=[], failed=[{"regex": "/a$", "url": "u", "status": 500, "at": "2026-01-01T00:00:09Z"}])
        expect(not all(verdict(late_fail, mode="fail", spec={"holds": [], "fails": ["/a$"]}).values()),
               "a failure that happened after the evals passed")
        expect(not all(verdict(report(held=[], afterEvals=[], failed=[{"regex": "/a$", "url": "u", "status": 200, "at": "2026-01-01T00:00:01Z"}]),
                               mode="fail", spec={"holds": [], "fails": ["/a$"]}).values()), "a 200 'failure' passed")
        fail_rep = report(held=[], afterEvals=[], failed=[{"regex": "/a$", "url": "u", "status": 500, "at": "2026-01-01T00:00:01Z"}])
        expect(all(verdict(fail_rep, mode="fail", spec={"holds": [], "fails": ["/a$"]}).values()), "a real fail check failed")

    def test_lens_results_must_match_their_brief(self) -> None:
        check = lambda r, carried=(), n=1: self.call("check_result", r, key="K", lens="lens.l", short="l", n=n, meta=META,
                                                     carried=list(carried), sets=("carried",))
        expect(check(lens_result()) == [], check(lens_result()))
        for name, r in {
            "wrong candidate": lens_result(candidateRef="d" * 40), "wrong lens": lens_result(lensId="lens.x"),
            "tree not named": lens_result(evidence=["ok"]),
            "aligned with open P1": lens_result(findings=[{"id": "l-r1-1", "severity": "P1", "summary": "s"}]),
            "deferred P1": lens_result(outcome="changes-requested", findings=[{"id": "l-r1-1", "severity": "P1", "summary": "s",
                                                                               "state": "deferred", "deferredIssue": "x"}]),
            "undeferred P3": lens_result(findings=[{"id": "l-r1-1", "severity": "P3", "summary": "s"}]),
            "no severity": lens_result(outcome="changes-requested", findings=[{"id": "l-r1-1", "summary": "s"}]),
            "bad id": lens_result(outcome="changes-requested", findings=[{"id": "x-1", "severity": "P0", "summary": "s"}]),
        }.items():
            expect(check(r), f"{name} was accepted")
        expect(check(lens_result(round=2), carried={"l-r1-1"}, n=2), "a dropped carried finding was accepted")
        expect(check(lens_result(lateFindings=["l-r1-9"])), "a lateFindings id that isn't one of the result's own was accepted")

    def test_patches_apply_unstripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            sh("git", "init", "-q", cwd=r)
            (r / "a.txt").write_text("one\ntwo\n\nthree\n\n")
            commit(r, "a")
            (r / "a.txt").write_text("one\nTWO\n\nthree\n\n")
            diff = sh("git", "diff", cwd=r).stdout
            sh("git", "checkout", "-q", "--", "a.txt", cwd=r)
            expect(self.call("apply_patch", diff.strip(), r, cwd=r)["returncode"] != 0,
                   "the fixture doesn't exercise the stripped case")
            sh("git", "reset", "-q", "--hard", cwd=r)
            expect(self.call("apply_patch", diff, r, cwd=r)["returncode"] == 0, "the unstripped diff did not apply")

    def test_the_lease_is_exclusive_and_released_on_death(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lock"
            holder = subprocess.Popen([sys.executable, "-c", "import fcntl,sys,time; f=open(sys.argv[1],'a+'); "
                                       "fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(60)", str(lock)],
                                      stdout=subprocess.PIPE, text=True)
            holder.stdout.readline()
            try:
                try:
                    self.call("Lease", lock, 1, "selftest", 1)
                    raise AssertionError("took a held lease")
                except Stopped as error:
                    # Timed inside the probe, so interpreter start-up can't stand in for the lease's own wait.
                    expect(error.elapsed >= 1, f"gave up too early: {error.elapsed:.2f}s")
                holder.kill()
                holder.wait()
                self.call("Lease", lock, 1, "selftest", 5, then="close")
            finally:
                if holder.poll() is None:
                    holder.kill()
                    holder.wait()
                holder.stdout.close()

    def test_plan_context_is_found_by_key(self) -> None:
        sections = self.call("plan_sections", PLAN)
        expect(any("FX-2" in title for _, title, _ in sections), "no FX-2 section")
        own = next(body for _, title, body in sections if "FX-2" in title)
        expect("FX-3" not in own and "it loads" in own, own)

    # -- end to end ---------------------------------------------------------------------------------------

    def test_lane_and_lead_flow_end_to_end(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        plan_delivery(fx)  # with the plan chapter on, so lead-accept's acceptance record reaches the plan
        code, out = flow_run("lead-init", cwd=m); expect(code == 0, out)
        code, out = flow_run("lead-brief", cwd=m); expect(code == 0, out)
        brief_text = (m / "docs/lane-brief.md").read_text()
        expect("In-app checks" in brief_text and "{{" not in brief_text and "src/one*" in brief_text
               and "needs-hold" in brief_text, "the lane brief is incomplete")
        code, out = flow_run("lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane 1", cwd=m); expect(code == 0, out)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        code, out = flow_run("handoff", "FX-2", "--pr", "https://forge.test/pr/1", cwd=wt)
        expect(code == 2 and "push first" in out, f"handed off unpushed: {out}")
        # gates (the last one is the in-app build, which gets stamped) and in-app checks
        code, out = flow_run("gate", "FX-2", cwd=wt); expect(code == 0 and "in-app build stamped" in out, out)
        hold = ["app-check", "FX-2", "hold", "--mode", "hold", "--path", "/one", "--hold", "/api/one$", "--eval", "true",
                "--after-eval", "true"]
        code, out = flow_run(*hold, cwd=wt); expect(code == 0 and "PASS" in out, out)
        code, out = flow_run("app-check", "FX-2", "smuggle", "--mode", "loaded", "--path", "/one", "--eval", "true",
                             "--", "--fail", "/api/one$", cwd=wt)
        expect(code == 2 and "belong to the check spec" in out, f"a smuggled spec flag ran: {out}")
        code, out = flow_run("app-check", "FX-2", "ids", "--probe", "--mode", "loaded", "--path", "/one",
                             "--eval", "'some-id'", cwd=wt)
        expect(code == 0 and "not evidence" in out and not (wt / ".orchestration/review/FX-2/app-checks/ids.json").exists(),
               f"a probe became evidence: {out}")
        code, out = flow_run("app-check", "FX-2", "tune", "--probe", "--mode", "fail", "--path", "/one", "--fail",
                             "/api/one$", "--eval", "true", cwd=wt)
        expect(code == 2 and "plain load" in out, f"a probe ran a fail check: {out}")
        code, out = flow_run("app-check", "FX-2", "vacuous", "--mode", "fail", "--path", "/one", "--fail", "/api/one$", cwd=wt)
        expect(code == 1 and "FAIL" in out, f"a fail check with no eval passed: {out}")
        (wt / ".orchestration/review/FX-2/app-checks/vacuous.json").unlink()
        # a baseline excuses an error only from a plain load of the same page, and never in a fail check
        code, out = flow_run("app-check", "FX-2", "err", "--baseline", "--mode", "loaded", "--path", "/one",
                             "--eval", "true", "--", "ERROR old", cwd=wt)
        code, out = flow_run("app-check", "FX-2", "err", "--mode", "loaded", "--path", "/one", "--eval", "true", "--", "ERROR old", cwd=wt)
        expect(code == 0, f"a baseline error wasn't excused in a plain load: {out}")
        code, out = flow_run("app-check", "FX-2", "err", "--mode", "fail", "--path", "/one", "--fail", "/api/one$",
                             "--eval", "true", "--", "ERROR old", cwd=wt)
        expect(code == 1 and "NO  no new console errors" in out, f"a baseline excused an error in a fail check: {out}")
        for name in ("err", "err-baseline"):
            (wt / f".orchestration/review/FX-2/app-checks/{name}.json").unlink()
        flow_run("app-check", "FX-2", "err3", "--baseline", "--mode", "loaded", "--path", "/elsewhere", "--eval", "true",
                 "--", "ERROR old", cwd=wt)
        code, out = flow_run("app-check", "FX-2", "err3", "--mode", "loaded", "--path", "/one", "--eval", "true",
                             "--", "ERROR old", cwd=wt)
        expect(code == 1 and "NO  no new console errors" in out, f"a baseline of another page excused an error: {out}")
        for name in ("err3", "err3-baseline"):
            (wt / f".orchestration/review/FX-2/app-checks/{name}.json").unlink()
        flow_run("app-check", "FX-2", "err2", "--baseline", "--mode", "hold", "--path", "/one", "--hold", "/api/one$",
                 "--eval", "true", "--after-eval", "true", "--", "ERROR old", cwd=wt)
        code, out = flow_run("app-check", "FX-2", "err2", "--mode", "loaded", "--path", "/one", "--eval", "true",
                             "--", "ERROR old", cwd=wt)
        expect(code == 1 and "NO  no new console errors" in out, f"a hold-mode baseline excused an error: {out}")
        for name in ("err2", "err2-baseline"):
            (wt / f".orchestration/review/FX-2/app-checks/{name}.json").unlink()
        code, out = flow_run("app-check", "FX-2", "fail", "--mode", "fail", "--path", "/one", "--fail", "/api/one$",
                             "--eval", "true", cwd=wt)
        expect(code == 0, out)
        # review: a mismatched result, a finding, a refused re-prepare, a restart, then alignment
        code, out = flow_run("review-prepare", "FX-2", "--round", "1", cwd=wt)
        expect(code == 2 and "Evidence so far" in out, out)
        context = wt / ".orchestration/review/FX-2/context.md"
        text = context.read_text()
        expect("Couldn't be loaded" in text and "| AC1 | S1 |" in text and "AC2" not in text, "context is wrong")
        context.write_text(text.replace(CONTEXT_TODO, "- fixture evidence"))
        code, out = flow_run("review-prepare", "FX-2", "--round", "1", cwd=wt); expect(code == 0, out)
        review = wt / ".orchestration/review/FX-2"
        expect("`hold` (hold, PASS" in (review / "brief-r1-adversarial-testing.md").read_text(), "the brief lacks the in-app records")
        lenses = fx.lenses(wt)
        write_results(review, 1, "FX-2", lenses, candidate="0" * 40, only=0)
        write_results(review, 1, "FX-2", lenses, only=1)
        good_second = (review / f"result-r1-{lenses[1][1][0]}.json").read_text()
        code, out = flow_run("review-reduce", "FX-2", "--round", "1", cwd=wt)
        expect(code == 2 and "attempt 1" in out, f"a wrong candidate was reduced: {out}")
        expect((review / f"result-r1-{lenses[1][1][0]}.json").read_text() == good_second, "a good result was discarded too")
        code, out = flow_run("review-prepare", "FX-2", "--round", "1", cwd=wt)
        expect(code == 2 and "already prepared" in out, f"a prepared round was re-prepared silently: {out}")
        code, out = flow_run("review-prepare", "FX-2", "--round", "1", "--restart", cwd=wt)
        expect(code == 0 and "restart 1" in out, out)
        short0 = lenses[0][1][0]
        write_results(review, 1, "FX-2", lenses, findings=[
            {"id": f"{short0}-r1-1", "severity": "P1", "summary": "fix"},
            {"id": f"{short0}-r1-2", "severity": "P3", "summary": "later", "state": "deferred", "deferredIssue": "x"}])
        code, out = flow_run("review-reduce", "FX-2", "--round", "1", cwd=wt)
        expect(code == 0 and "unresolved" in out and "deferred P3" in out and "1 restart" in out, out)
        code, out = flow_run("review-reduce", "FX-2", "--round", "1", cwd=wt); expect(code == 0, out)
        journal = [json.loads(line) for line in (fx.tmp / "record/journal.jsonl").read_text().splitlines()]
        expect(sum(e["kind"] == "review.round" for e in journal) == 1, "a second reduce journaled again")
        round_entry = next(e for e in journal if e["kind"] == "review.round")
        expect("refused attempt" in round_entry["summary"] and "restart" in round_entry["summary"],
               f"the journal doesn't carry the round's history: {round_entry['summary']}")
        (wt / "src/one.txt").write_text("one fixed\n")
        commit(wt, "FX-2: fix")
        code, out = flow_run("review-prepare", "FX-2", "--round", "2", cwd=wt)
        expect(code == 0 and "Resume the same reviewer" in out, out)
        write_results(review, 2, "FX-2", lenses)
        code, out = flow_run("review-reduce", "FX-2", "--round", "2", cwd=wt)
        expect(code == 2 and "carried finding" in out, f"a dropped finding was reduced: {out}")
        write_results(review, 2, "FX-2", lenses, resolved=[{"id": f"{short0}-r1-1", "state": "fixed", "resolution": "fixed"}], only=0)
        code, out = flow_run("review-reduce", "FX-2", "--round", "2", cwd=wt)
        expect(code == 0 and "aligned" in out, out)
        # hand-off: stale gates and in-app evidence are refused until re-run at the new head
        sh("git", "push", "-q", "-u", "origin", "HEAD:feature/FX-2", cwd=wt)
        body = fx.tmp / "body.md"
        body.write_text("Summary\n")
        code, out = flow_run("open-pr", "FX-2", "--title", "FX-2: one", "--body-file", body, cwd=wt)
        expect(code == 0 and "https://forge.test/pr/1" in out, out)
        code, out = flow_run("handoff", "FX-2", "--pr", "https://forge.test/pr/1", cwd=wt)
        expect(code == 2 and "gates ran at" in out and "STALE" in out, f"stale evidence was accepted: {out}")
        flow_run("gate", "FX-2", cwd=wt)
        for args in (hold, ["app-check", "FX-2", "fail", "--mode", "fail", "--path", "/one", "--fail", "/api/one$", "--eval", "true"]):
            code, out = flow_run(*args, cwd=wt); expect(code == 0, out)
        (wt / "stray.txt").write_text("x")
        code, out = flow_run("handoff", "FX-2", "--pr", "https://forge.test/pr/1", cwd=wt)
        expect(code == 2 and "untracked" in out, f"handed off with an untracked file: {out}")
        (wt / "stray.txt").unlink()
        # the pipeline's PR review: required at the handed head, triaged, and current when the lead accepts
        with_review = fx.tmp / "flow-pr-review.json"
        conf = json.loads((wt / ".agents/run-delivery.json").read_text())
        conf["review"]["pr"] = {"author": "Pipeline Bot", "waitSeconds": 1}
        with_review.write_text(json.dumps(conf))
        fx.env["RUN_DELIVERY_CONFIG"] = str(with_review)
        reviews = fx.tmp / "reviews.json"
        fx.env["FAKE_FORGE_REVIEWS"] = str(reviews)
        code, out = flow_run("handoff", "FX-2", "--pr", "https://forge.test/pr/1", cwd=wt)
        expect(code == 2 and "pr-review FX-2" in out, f"handed off without the PR review: {out}")
        reviews.write_text(json.dumps({"checks": [{"name": "pipe", "state": "pending", "at": "2026-01-01T00:00:10"}],
                                       "reviews": [{"author": "Pipeline Bot", "at": "2026-01-01T00:00:05", "body": "old"}]}))
        code, out = flow_run("pr-review", "FX-2", cwd=wt)
        expect(code == 2 and "no Pipeline Bot review" in out, f"a pending pipeline counted as reviewed: {out}")
        reviews.write_text(json.dumps({"checks": [{"name": "pipe", "state": "failed", "at": "2026-01-01T00:00:10"}], "reviews": []}))
        code, out = flow_run("pr-review", "FX-2", "--wait", cwd=wt)
        expect(code == 2 and "pipeline failed" in out, f"a failed pipeline passed: {out}")
        reviews.write_text(json.dumps({"checks": [{"name": "pipe", "state": "passed", "at": "2026-01-01T00:00:10"}],
                                       "reviews": [{"author": "Pipeline Bot", "at": "2026-01-01T00:00:05", "body": "stale"},
                                                   {"author": "Someone", "at": "2026-01-01T00:00:20", "body": "not the bot"}]}))
        code, out = flow_run("pr-review", "FX-2", cwd=wt)
        expect(code == 2 and "no Pipeline Bot review" in out, f"a review from before the pipeline counted: {out}")
        reviews.write_text(json.dumps({"checks": [{"name": "pipe", "state": "passed", "at": "2026-01-01T00:00:10"}],
                                       "reviews": [{"author": "Pipeline Bot", "at": "2026-01-01T00:00:30", "body": "Should fix: x"}]}))
        code, out = flow_run("pr-review", "FX-2", cwd=wt)
        expect(code == 0 and "Should fix: x" in out, out)
        code, out = flow_run("handoff", "FX-2", "--pr", "https://forge.test/pr/1", cwd=wt)
        expect(code == 2 and "--pr-review-triage" in out, f"handed off without triage: {out}")
        code, out = flow_run("handoff", "FX-2", "--pr", "https://forge.test/pr/1", "--pr-review-triage", "fixed x", cwd=wt)
        expect(code == 0 and "**Hand-off: FX-2**" in out and "gates at" in out and "restart" in out
               and "`vacuous`: its record was removed" in out and "triage: fixed x" in out, out)
        reviews.write_text(json.dumps({"checks": [{"name": "pipe", "state": "passed", "at": "2026-01-01T00:00:10"}],
                                       "reviews": [{"author": "Pipeline Bot", "at": "2026-01-01T00:00:40", "body": "new point"}]}))
        code, out = flow_run("lead-accept", "FX-2", "--accept-restarts", cwd=m)
        expect(code == 1 and "changed after the lane read it" in out, f"accepted a review the lane never read: {out}")
        reviews.write_text(json.dumps({"checks": [{"name": "pipe", "state": "passed", "at": "2026-01-01T00:00:10"}],
                                       "reviews": [{"author": "Pipeline Bot", "at": "2026-01-01T00:00:30", "body": "Should fix: x"}]}))
        # the lead decides on restarts; a declined PR is refused
        code, out = flow_run("lead-accept", "FX-2", cwd=m)
        expect(code == 1 and "--accept-restarts" in out, f"a restarted review was accepted without a decision: {out}")
        forge_state = json.loads((fx.tmp / "forge.json").read_text())
        forge_state["https://forge.test/pr/1"]["state"] = "closed"
        (fx.tmp / "forge.json").write_text(json.dumps(forge_state))
        code, out = flow_run("lead-accept", "FX-2", "--accept-restarts", cwd=m)
        expect(code == 1 and "the PR is closed" in out, f"a closed PR was accepted: {out}")
        forge_state["https://forge.test/pr/1"]["state"] = "open"
        (fx.tmp / "forge.json").write_text(json.dumps(forge_state))
        code, out = flow_run("lead-accept", "FX-2", "--accept-restarts", cwd=m)
        expect(code == 0 and "accepted" in out, out)
        del fx.env["RUN_DELIVERY_CONFIG"], fx.env["FAKE_FORGE_REVIEWS"]
        # FX-3: blocked evidence needs the lead's decision; straying outside the lane is refused; the bound holds
        flow_run("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        wt3 = fx.lane("FX-3")
        (wt3 / "src/two.txt").write_text("two\n")
        commit(wt3, "FX-3: two")
        (wt3 / ".orchestration/review/FX-3").mkdir(parents=True)
        (wt3 / ".orchestration/review/FX-3/context.md").write_text("- evidence")
        review3 = wt3 / ".orchestration/review/FX-3"
        code, out = flow_run("review-prepare", "FX-3", "--round", "4", cwd=wt3)
        expect(code == 2 and "past the bound" in out, f"a round past the bound was prepared: {out}")
        for n in (1, 2, 3):
            code, out = flow_run("review-prepare", "FX-3", "--round", n, cwd=wt3); expect(code == 0, out)
            found = [{"id": f"{short0}-r{n}-1", "severity": "P1", "summary": "still wrong"}]
            resolved = [{"id": f"{short0}-r{k}-1", "state": "open", "resolution": "not yet"} for k in range(1, n)]
            write_results(review3, n, "FX-3", lenses, findings=found, resolved=resolved or None)
            code, out = flow_run("review-reduce", "FX-3", "--round", n, cwd=wt3); expect(code == 0, out)
            (wt3 / "src/two.txt").write_text(f"two {n}\n")
            commit(wt3, f"FX-3: try {n}")
        expect("blocked" in out and "loop-bound-reached" in out, f"round 3 of 3 wasn't blocked: {out}")
        code, out = flow_run("review-prepare", "FX-3", "--round", 4, cwd=wt3)
        expect(code == 2 and "--grace" in out, out)
        code, out = flow_run("review-prepare", "FX-3", "--round", 4, "--grace", cwd=wt3); expect(code == 0, out)
        write_results(review3, 4, "FX-3", lenses, resolved=[{"id": f"{short0}-r{k}-1", "state": "fixed", "resolution": "done"} for k in (1, 2, 3)])
        code, out = flow_run("review-reduce", "FX-3", "--round", 4, cwd=wt3); expect(code == 0 and "aligned" in out, out)
        (wt3 / "src/one.txt").write_text("trespass\n")
        commit(wt3, "FX-3: trespass")
        flow_run("gate", "FX-3", cwd=wt3)
        code, out = flow_run("review-prepare", "FX-3", "--round", 5, cwd=wt3)
        expect(code == 2, "a sixth round was prepared")
        sh("git", "reset", "-q", "--hard", "HEAD~1", cwd=wt3)  # the grace round approved HEAD~1
        (wt3 / "src/one.txt").unlink(missing_ok=True)
        flow_run("gate", "FX-3", cwd=wt3)
        sh("git", "push", "-q", "-u", "origin", "HEAD:feature/FX-3", cwd=wt3)
        code, out = flow_run("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", body, cwd=wt3); expect(code == 0, out)
        code, out = flow_run("handoff", "FX-3", "--pr", "https://forge.test/pr/2", "--blocked",
                             "needs-loaded=no data in the fixture", cwd=wt3)
        expect(code == 0, out)
        code, out = flow_run("lead-accept", "FX-3", cwd=m)
        expect(code == 1 and "accept with --accept-blocked 'needs-loaded'" in out, f"a blocked check was accepted: {out}")
        code, out = flow_run("lead-accept", "FX-3", "--accept-blocked", "needs-loaded", cwd=m)
        expect(code == 0, out)
        # lead-accept rebuilds the plan itself: before any sweep, the blocked item it accepted reads as waived.
        accepted_head = sh("git", "rev-parse", "HEAD", cwd=wt3).stdout.strip()
        waived = json.loads((fx.tmp / "record/plan.json").read_text())["evidence"]["FX-3"]["in-app-loaded"]
        expect({k: waived.get(k) for k in ("state", "head", "items", "reason")} ==
               {"state": "waived", "head": accepted_head, "items": ["needs-loaded"], "reason": "needs-loaded: no data in the fixture"},
               f"lead-accept's waiver didn't reach the plan: {waived}")
        journal = (fx.tmp / "record/journal.jsonl").read_text()
        expect("accepted blocked 'needs-loaded'" in journal, "the lead's decision on the blocked check wasn't journaled")
        # ship: merge FX-2 into the base by hand (the merge queue's job), then build the product-only branch
        sh("git", "fetch", "-q", "origin", cwd=m)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "--no-ff", "-m", "merge FX-2", "origin/feature/FX-2", cwd=m)
        # a gate script only the delivery base has, with a helper beside it: ship lends both for its gate run
        (m / "tools/gate.sh").write_text("sh tools/gate-helper.sh && test -f src/app.txt\n")
        (m / "tools/gate-helper.sh").write_text("true\n")
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        conf["gates"].append({"id": "script", "argv": ["sh", "tools/gate.sh"]})
        (m / ".agents/run-delivery.json").write_text(json.dumps(conf, indent=2))
        commit(m, "FX-1: a gate script")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = flow_run("ship", cwd=m)
        expect(code == 0 and "1 file(s) staged" in out and "outside ['src/']: none" in out, out)
        expect("lent the base's gate tooling for the run: tools/gate-helper.sh, tools/gate.sh" in out
               and "gate script" in out and "gates passed" in out, f"ship didn't gate with the base's tooling: {out}")
        untracked = sh("git", "status", "--porcelain", "--untracked-files=all", cwd=m / "lanes/ship-FX-1").stdout
        expect("tools/" not in untracked, f"the lent gate tooling was left in the ship checkout: {untracked}")
        expect('"passed": true' in (fx.tmp / "flow-log/FX-1.ship-gates.jsonl").read_text(), "the ship gate run wasn't logged")
        code, out = flow_run("ship", cwd=m)
        expect(code == 2 and "already exists" in out, f"ship overwrote an existing branch: {out}")

    def test_cleanup_removes_only_merged_clean_work_and_close_archives(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        setup_delivery(fx)
        code, out = flow_run("lead-init", cwd=m); expect(code == 0, out)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        sh("git", "push", "-q", "origin", "feature/FX-2", cwd=wt)
        code, out = flow_run("open-pr", "FX-2", "--title", "FX-2: one", "--body-file", "/dev/null", cwd=wt); expect(code == 0, out)
        (wt / ".orchestration/review/FX-2").mkdir(parents=True, exist_ok=True)
        (wt / ".orchestration/review/FX-2/gates.json").write_text("{}")
        code, out = flow_run("lead-cleanup", "FX-2", cwd=m)
        expect(code == 1 and "unmerged" in out and wt.exists(), f"cleaned an unmerged story: {out}")
        code, out = flow_run("lead-cleanup", cwd=m)
        expect(code == 0 and "FX-2: unmerged" in out and "FX-3: nothing" in out, f"a sweep failed on unmerged work: {out}")
        sh("git", "fetch", "-q", "origin", cwd=m)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "--no-ff", "-m", "merge FX-2", "origin/feature/FX-2", cwd=m)
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        (wt / "stray.txt").write_text("x\n")
        code, out = flow_run("lead-cleanup", "FX-2", cwd=m)
        expect(code == 1 and "refused" in out and "stray.txt" in out and wt.exists(), f"removed a dirty checkout: {out}")
        (wt / "stray.txt").unlink()
        (wt / "src/one.txt").write_text("one, unpushed\n")
        commit(wt, "FX-2: unpushed")
        code, out = flow_run("lead-cleanup", "FX-2", cwd=m)
        expect(code == 1 and "the checkout's HEAD" in out and wt.exists(), f"lost an unmerged commit: {out}")
        sh("git", "reset", "-q", "--hard", "HEAD~1", cwd=wt)
        code, out = flow_run("lead-cleanup", "FX-2", cwd=m)
        expect(code == 0 and "cleaned" in out and not wt.exists(), out)
        expect(not sh("git", "branch", "--list", "feature/FX-2", cwd=m).stdout.strip(), "the local branch survived")
        expect((fx.tmp / "evidence/FX-2/gates.json").exists(), "the lane's evidence wasn't kept")
        expect(remote_has(m, "feature/FX-2"), "the remote branch was deleted without the repository allowing it")
        journal_size = len((fx.tmp / "record/journal.jsonl").read_text().splitlines())
        code, out = flow_run("lead-cleanup", "FX-2", cwd=m)
        expect(code == 0 and "nothing to clean" in out
               and len((fx.tmp / "record/journal.jsonl").read_text().splitlines()) == journal_size,
               f"a second sweep journaled again: {out}")
        flow_json = m / ".agents/run-delivery.json"
        conf = json.loads(flow_json.read_text())
        conf["cleanup"] = {"deleteRemoteBranches": True, "archive": str(fx.tmp / "archive/{delivery}")}
        flow_json.write_text(json.dumps(conf))
        code, out = flow_run("lead-cleanup", "FX-2", cwd=m)
        expect(code == 0 and "still open" in out and remote_has(m, "feature/FX-2"), f"deleted a branch whose PR is open: {out}")
        prs = json.loads((fx.tmp / "forge.json").read_text())
        for pr in prs.values():
            pr["state"] = "merged"
        (fx.tmp / "forge.json").write_text(json.dumps(prs))
        code, out = flow_run("lead-cleanup", "FX-2", cwd=m)
        expect(code == 0 and "deleted origin/feature/FX-2" in out and not remote_has(m, "feature/FX-2"), out)
        code, out = flow_run("lead-close", cwd=m)
        expect(code == 2 and "delivery.closed" in out, f"closed before delivery.closed: {out}")
        state_tool = fx.release / "skills/orchestrate-work/scripts/delivery_state.py"
        sh(sys.executable, "-B", state_tool, "--dir", fx.tmp / "record", "journal", "--kind", "delivery.closed",
           "--summary", "closed", cwd=m)
        # reconcile sweeps: this delivery's is stopped, one naming another delivery is left alone
        (fx.tmp / "bin").mkdir()
        (fx.tmp / "bin/flow.py").write_text("import time\ntime.sleep(600)\n")
        sweep = [sys.executable, "-B", str(fx.tmp / "bin/flow.py")]
        ours = subprocess.Popen(sweep + ["lead-reconcile", "--every", "600"], cwd=m)
        theirs = subprocess.Popen(sweep + ["--delivery", "OTHER-9", "lead-reconcile", "--every", "600"], cwd=m)
        try:
            time.sleep(0.5)
            code, out = flow_run("lead-close", cwd=m)
            expect(f"stopped {ours.pid}" in out and f"stopped {theirs.pid}" not in out, f"the sweeps weren't sorted: {out}")
            ours.wait(timeout=5)
            expect(theirs.poll() is None, "lead-close stopped another delivery's sweep")
        finally:
            for proc in (ours, theirs):
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        archive = fx.tmp / "archive/FX-1"
        expect(code == 0 and "closed out" in out and list(archive.glob("*/record/journal.jsonl")), f"lead-close didn't archive: {out}")
        expect(list(archive.glob("*/evidence/FX-2/gates.json")), "the archive lacks the lane evidence")

    def test_lanes_push_and_open_prs_only_as_granted(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        path = setup_delivery(fx)
        code, out = flow_run("lead-init", cwd=m); expect(code == 0, out)
        code, out = flow_run("lead-brief", cwd=m); expect(code == 0, out)
        brief_text = (m / "docs/lane-brief.md").read_text()
        expect("the operator gave lanes authority on 2026-01-01" in brief_text and "never force-push" in brief_text,
               "the brief doesn't state the authority")
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        code, out = flow_run("push", "FX-3", cwd=wt)
        expect(code == 2 and "not feature/FX-3" in out and not remote_has(m, "feature/FX-3"), f"pushed another branch: {out}")
        code, out = flow_run("push", "FX-2", cwd=wt)
        expect(code == 0 and remote_has(m, "feature/FX-2"), out)
        sh("git", "commit", "-q", "--amend", "-m", "FX-2: rewritten", cwd=wt)
        code, out = flow_run("push", "FX-2", cwd=wt)
        expect(code == 2 and "never forced" in out, f"a rewritten branch was pushed: {out}")
        lane_file = wt / path.relative_to(m)  # the lane reads its own checkout's copy
        d = json.loads(lane_file.read_text())
        d["authority"] = {"push": True, "grantedBy": "the operator", "grantedOn": "2026-01-01"}
        lane_file.write_text(json.dumps(d, indent=2))
        code, out = flow_run("open-pr", "FX-2", "--title", "FX-2: one", "--body-file", "/dev/null", cwd=wt)
        expect(code == 2 and "draftPullRequests" in out, f"opened a PR without the authority: {out}")
        del d["authority"]
        lane_file.write_text(json.dumps(d, indent=2))
        code, out = flow_run("push", "FX-2", cwd=wt)
        expect(code == 2 and "no `push` authority" in out, f"pushed without authority: {out}")
        path.write_text(json.dumps(d, indent=2))
        code, out = flow_run("lead-brief", cwd=m); expect(code == 0, out)
        expect("no authority to push" in (m / "docs/lane-brief.md").read_text(), "the brief claims authority nobody gave")
        d["authority"] = {"push": True}
        lane_file.write_text(json.dumps(d, indent=2))
        code, out = flow_run("push", "FX-2", cwd=wt)
        expect(code == 2 and "grantedBy" in out, f"accepted authority without saying who granted it: {out}")

    def test_stories_added_later_join_the_record_as_planned_work(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        path = setup_delivery(fx)
        code, out = flow_run("lead-init", cwd=m); expect(code == 0, out)
        record = fx.tmp / "record"
        state_script = fx.release / "skills/orchestrate-work/scripts/delivery_state.py"
        show = lambda: json.loads(sh(sys.executable, "-B", state_script, "--dir", record, "show").stdout)
        # The tracker reconcile files a ticket it finds under the parent, but not in the record, as a follow-up.
        doc = show()
        sh(sys.executable, "-B", state_script, "--dir", record, "patch", "--writer", "lead", "--version",
           doc["state"]["version"], "--patch", json.dumps({"followups": [{"id": "FX-4", "title": "four", "reference": "FX-4"}]}))
        d = json.loads(path.read_text())
        d["stories"]["FX-4"] = {"title": "Thing four", "lane": "lane-1", "label": "S1b"}
        path.write_text(json.dumps(d, indent=2))
        code, out = flow_run("lead-brief", cwd=m)
        expect(code == 0 and "registered FX-4" in out, f"lead-brief didn't register the new story: {out}")
        state = show()["state"]
        entry = next((p for p in state["planned"] if p["subject"] == "FX-4"), None)
        expect(entry == {"subject": "FX-4", "title": "S1b: Thing four", "lane": "lane-1", "stage": "planned"},
               f"the new story isn't planned work for its lane: {state['planned']}")
        expect(not any(f["id"] == "FX-4" for f in state["followups"]), f"it's still a follow-up: {state['followups']}")
        added = [e for e in show()["journal"] if e.get("kind") == "decision.recorded" and e.get("subject") == "FX-4"]
        expect(len(added) == 1, f"the addition isn't journaled once: {added}")
        code, out = flow_run("lead-brief", cwd=m)
        expect(code == 0 and "registered" not in out and len(show()["state"]["planned"]) == 3,
               f"a second lead-brief registered it again: {out}")
        # The reconcile sweep registers one too, before it reads the tracker.
        d["stories"]["FX-5"] = {"title": "Thing five", "lane": "lane-2"}
        path.write_text(json.dumps(d, indent=2))
        code, out = flow_run("lead-reconcile", cwd=m)
        expect(code == 0 and "registered FX-5 as planned work" in out
               and any(p["subject"] == "FX-5" for p in show()["state"]["planned"]), f"the sweep didn't register it: {out}")
        # And lead-assign, for a story assigned before any sweep.
        d["stories"]["FX-6"] = {"title": "Thing six", "lane": "lane-2"}
        path.write_text(json.dumps(d, indent=2))
        code, out = flow_run("lead-assign", "FX-6", "--lane", "lane-2", "--title", "six", cwd=m)
        expect(code == 0 and any(p["subject"] == "FX-6" for p in show()["state"]["planned"]),
               f"lead-assign didn't register it: {out}")

    def test_lanes_read_the_delivery_from_the_base_not_their_checkout(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        path = setup_delivery(fx)
        (m / ".agents/orchestration.json").write_text(json.dumps({"base": {"remote": "origin", "ref": "feature/FX-1"}}))
        commit(m, "FX-1: binding")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = flow_run("lead-init", cwd=m); expect(code == 0, out)
        wt = fx.lane("FX-2")
        lane_head = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        # the lead changes the plan and withdraws push authority on the base; the lane doesn't merge
        plan = m / "docs/plan.md"
        plan.write_text(plan.read_text().replace("- Scenarios: it loads; it fails.", "- Scenarios: it loads; it fails; NEW-SCENARIO."))
        d = json.loads(path.read_text())
        del d["authority"]
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: plan and authority")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        expect("NEW-SCENARIO" in fx.call("plan_context", "FX-2", cwd=wt), "the lane read its own stale plan, not the base's")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        code, out = flow_run("push", "FX-2", cwd=wt)
        expect(code == 2 and "no `push` authority" in out and "origin/feature/FX-1:" in out,
               f"the lane used its checkout's stale authority: {out}")
        expect(sh("git", "rev-parse", "HEAD~1", cwd=wt).stdout.strip() == lane_head, "the lane's branch was moved")
        # the lead's own checkout, on the base branch, reads its working tree (uncommitted edits included)
        d["authority"] = {"push": True, "grantedBy": "the operator", "grantedOn": "2026-01-01"}
        path.write_text(json.dumps(d, indent=2))
        code, out = flow_run("lead-brief", cwd=m); expect(code == 0, out)
        expect("the operator gave lanes authority" in (m / "docs/lane-brief.md").read_text(),
               "the lead's checkout didn't read its own working tree")
        expect("Never merge or rebase" in (m / "docs/lane-brief.md").read_text(), "the brief doesn't forbid merging the base")

    def test_video_evidence_is_recorded_required_and_published(self) -> None:
        missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
        if missing:
            self.skipTest(f"video evidence needs {' and '.join(missing)}, which this host lacks")
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        path = setup_delivery(fx)
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["evidence"] = "video"
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: video evidence")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = flow_run("lead-init", cwd=m); expect(code == 0, out)
        code, out = flow_run("lead-brief", cwd=m); expect(code == 0, out)
        expect("needs-hold; needs-fail" in (m / "docs/lane-brief.md").read_text() and "video |" in (m / "docs/lane-brief.md").read_text(),
               "the brief doesn't list the video requirement")
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        code, out = flow_run("app-video", "FX-2", "loading", "--mode", "hold", "--path", "/one", cwd=wt)
        expect(code == 2 and "at least one --hold" in out, f"a hold video without a held request ran: {out}")
        code, out = flow_run("app-video", "FX-2", "loading", "--mode", "hold", "--path", "/one", "--hold", "/api/one$", cwd=wt)
        expect(code == 2 and "build" in out and "recorded" not in out, f"recorded without a build of HEAD: {out}")
        flow_run("build", "FX-2", cwd=wt)
        code, out = flow_run("app-video", "FX-2", "loading", "--mode", "hold", "--path", "/one", "--hold", "/api/one$", cwd=wt)
        expect(code == 0 and "recorded FX-2 loading" in out, out)
        clip = wt / ".orchestration/review/FX-2/videos/loading.mp4"
        dims = sh("ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", clip).stdout.strip()
        expect(dims.split(",")[1] != "720", f"the grey padding wasn't cropped: {dims}")
        head = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        lines, problems = fx.call("video_evidence", "FX-2", wt, head, cwd=wt)
        expect(lines and not problems, f"a current clip wasn't counted: {problems}")
        clip.write_bytes(clip.read_bytes() + b"x")
        lines, problems = fx.call("video_evidence", "FX-2", wt, head, cwd=wt)
        expect(problems and not lines, "an altered clip still counted")
        lines, problems = fx.call("video_evidence", "FX-3", wt, head, cwd=wt)
        expect(not problems, "a story not marked for video was asked for one")
        code, out = flow_run("app-video", "FX-2", "loading", "--mode", "hold", "--path", "/one", "--hold", "/api/one$", cwd=wt)
        expect(code == 0, out)
        page = fx.tmp / "page"
        code, out = flow_run("lead-evidence", "page", "FX-2", "--out", page, cwd=wt)
        html = (page / "index.html").read_text()
        expect(code == 0 and "videos/FX-2-loading.mp4" in html and (page / "videos/FX-2-loading.mp4").exists()
               and "{{" not in html and "<title>" in html, f"the evidence page is incomplete: {out}")
        # currency: a merged story marked for video must be on the published page
        record = json.loads((fx.tmp / "record/state.json").read_text())
        record["queue"].append({"subject": "FX-2", "title": "one", "lane": "lane-1", "stage": "merged", "head": head,
                                "syncedHead": head, "changeRequest": "https://forge.test/pr/1", "checks": [], "runId": None,
                                "order": 1, "leadInputs": [], "reportedRounds": 1})
        record["planned"] = [i for i in record.get("planned", []) if i["subject"] != "FX-2"]
        (fx.tmp / "record/state.json").write_text(json.dumps(record))  # a fixture shortcut for "it merged"
        code, out = flow_run("lead-evidence", "status", cwd=wt)
        expect(code == 1 and "no evidence page is published" in out, f"an unpublished page counted as current: {out}")
        code, out = flow_run("lead-evidence", "published", "--url", "https://pages.test/e", "--out", page, cwd=wt)
        expect(code == 0, out)
        code, out = flow_run("lead-evidence", "status", cwd=wt)
        expect(code == 0 and "current" in out, f"the published page wasn't current: {out}")
        flow_run("app-video", "FX-2", "failing", "--mode", "fail", "--path", "/one", "--fail", "/api/one$", cwd=wt)
        code, out = flow_run("lead-evidence", "status", cwd=wt)
        expect(code == 1 and "aren't on the published page" in out, f"a new clip didn't make the page stale: {out}")

    def test_one_run_cant_meet_two_requirements_and_stale_heads_fail_closed(self) -> None:
        fx = self.fixture()
        m = fx.main
        path = setup_delivery(fx)
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["inApp"] = [{"mode": "fail", "pattern": "/api/one$", "args": []},
                                         {"mode": "fail", "pattern": "/api/one$", "args": ["--fail-body", ""]}]
        path.write_text(json.dumps(d))
        commit(m, "FX-1: S6-style requirements")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        wt = fx.lane("FX-2")
        head = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        checks = wt / ".orchestration/review/FX-2/app-checks"
        checks.mkdir(parents=True)
        record = {"name": "empty", "mode": "fail", "baseline": False, "head": head, "pass": True,
                  "spec": {"holds": [], "fails": ["/api/one$"]}, "passthrough": ["--fail-body", ""]}
        (checks / "empty.json").write_text(json.dumps(record))
        in_app_evidence = lambda: fx.call("in_app_evidence", "FX-2", wt, head, {}, cwd=wt)
        _, problems = in_app_evidence()
        expect(any("args []" in p for p in problems), f"one empty-body run met both fail requirements: {problems}")
        (checks / "empty2.json").write_text(json.dumps({**record, "name": "empty2"}))
        _, problems = in_app_evidence()
        expect(any("args []" in p for p in problems), f"two empty-body runs met an args [] requirement: {problems}")
        (checks / "empty2.json").unlink()
        (checks / "plain.json").write_text(json.dumps({**record, "name": "plain", "passthrough": []}))
        _, problems = in_app_evidence()
        expect(not problems, f"two runs didn't meet two requirements: {problems}")
        # A requirement with no pattern mustn't take the only run that used a pattern another one needs.
        d3 = json.loads((wt / ".agents/deliveries/FX-1.json").read_text())
        d3["stories"]["FX-2"]["inApp"] = [{"mode": "hold"}, {"mode": "hold", "pattern": "/api/one$"}]
        (wt / ".agents/deliveries/FX-1.json").write_text(json.dumps(d3))
        for name in ("empty", "plain"):
            (checks / f"{name}.json").unlink()
        hold_rec = {**record, "mode": "hold", "passthrough": []}
        (checks / "a.json").write_text(json.dumps({**hold_rec, "name": "a", "spec": {"holds": ["/api/one$"], "fails": []}}))
        (checks / "b.json").write_text(json.dumps({**hold_rec, "name": "b", "spec": {"holds": ["/other$"], "fails": []}}))
        _, problems = in_app_evidence()
        expect(not problems, f"the matching gave the patterned run to the unpatterned requirement: {problems}")
        for name in ("a", "b"):
            (checks / f"{name}.json").unlink()
        sh("git", "checkout", "-q", "--", ".agents/deliveries/FX-1.json", cwd=wt)
        (checks / "empty.json").write_text(json.dumps(record))
        (checks / "plain.json").write_text(json.dumps({**record, "name": "plain", "passthrough": []}))
        # Without "args": [], a single empty-body run could stand in for both; each requirement needs its own run.
        d2 = json.loads((wt / ".agents/deliveries/FX-1.json").read_text())
        d2["stories"]["FX-2"]["inApp"] = [{"mode": "fail", "pattern": "/api/one$"},
                                          {"mode": "fail", "pattern": "/api/one$", "args": ["--fail-body", ""]}]
        (wt / ".agents/deliveries/FX-1.json").write_text(json.dumps(d2))
        (checks / "plain.json").unlink()
        _, problems = in_app_evidence()
        expect(problems, "one empty-body run met two fail requirements")
        sh("git", "checkout", "-q", "--", ".agents/deliveries/FX-1.json", cwd=wt)
        stale = {**record, "head": "0" * 40}
        fresh, why = fx.call("record_is_current", stale, head, wt, cwd=wt)
        expect(not fresh and "can't be compared" in why, f"an uncomparable head counted as current: {why}")

    def test_changed_evals_and_discarded_rounds_are_visible(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        setup_delivery(fx)
        flow_run("lead-init", cwd=m)
        flow_run("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        wt = fx.lane("FX-3")
        (wt / "src/two.txt").write_text("two\n")
        commit(wt, "FX-3: two")
        (wt / ".orchestration/review/FX-3").mkdir(parents=True)
        (wt / ".orchestration/review/FX-3/context.md").write_text("- evidence")
        code, out = flow_run("gate", "FX-3", cwd=wt); expect(code == 0, out)
        code, out = flow_run("app-check", "FX-3", "page", "--mode", "loaded", "--path", "/two", "--eval", "false", cwd=wt)
        expect(code == 1, f"a false eval passed: {out}")
        code, out = flow_run("app-check", "FX-3", "page", "--mode", "loaded", "--path", "/two", "--eval", "true", cwd=wt)
        expect(code == 0, out)
        review = wt / ".orchestration/review/FX-3"
        code, out = flow_run("review-prepare", "FX-3", "--round", 1, cwd=wt); expect(code == 0, out)
        brief_text = (review / "brief-r1-adversarial-testing.md").read_text()
        expect("failed earlier with a different spec" in brief_text, "the brief hides an eval changed after failing")
        # a lane that deletes the round's meta to dodge --restart is caught at acceptance
        (review / "round-1.meta.json").unlink()
        code, out = flow_run("review-prepare", "FX-3", "--round", 1, cwd=wt); expect(code == 0, out)
        write_results(review, 1, "FX-3", fx.lenses(wt))
        code, out = flow_run("review-reduce", "FX-3", "--round", 1, cwd=wt); expect(code == 0, out)
        sh("git", "push", "-q", "-u", "origin", "HEAD:feature/FX-3", cwd=wt)
        body = fx.tmp / "body.md"
        body.write_text("x\n")
        flow_run("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", body, cwd=wt)
        code, out = flow_run("handoff", "FX-3", "--pr", "https://forge.test/pr/1", cwd=wt)
        expect(code == 0 and "failed earlier with a different spec" in out, f"the hand-off hides a changed eval: {out}")
        code, out = flow_run("lead-accept", "FX-3", cwd=m)
        expect(code == 1 and "prepared 2 time(s)" in out and "--accept-discarded 1" in out, f"a discarded round went unnoticed: {out}")
        code, out = flow_run("lead-accept", "FX-3", "--accept-discarded", 1, cwd=m)
        expect(code == 0, f"the lead couldn't accept a discarded round: {out}")
        expect("accepted round 1 was prepared 2 time(s)" in (fx.tmp / "record/journal.jsonl").read_text(),
               "the lead's decision on the discarded round wasn't journaled")

    def test_settings_come_from_the_repository_configs(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        config_in = lambda path, *attrs: fx.call("Config", path, "FX-1", cwd=path, attrs=list(attrs))
        setup_delivery(fx)
        binding = {"base": {"remote": "origin", "ref": "feature/FX-1"}, "workRoot": ".orchestration/work", "prepare": [],
                   "fallback": [{"id": "lint", "argv": ["sh", "-c", "test -f src/app.txt"]}],
                   "leases": [{"id": "app", "description": "fixture", "defaultHolder": "lead"}],
                   "state": {"directory": ".orchestration/state"}}
        (m / ".agents/orchestration.json").write_text(json.dumps(binding))
        (m / ".agents/delivery-views.json").write_text(json.dumps({"tracker": {"issueUrl": "https://views.test/{key}"},
                                                                    "forges": [{"name": "GitHub", "repository": "o/n"}]}))
        flow_cfg = json.loads((m / ".agents/run-delivery.json").read_text())
        for k in ("tracker", "forge", "gates"):
            flow_cfg.pop(k)
        (m / ".agents/run-delivery.json").write_text(json.dumps(flow_cfg))
        bad_base = json.loads((m / ".agents/deliveries/FX-1.json").read_text())
        bad_base["baseBranch"] = "feature/other"
        (m / ".agents/deliveries/FX-1.json").write_text(json.dumps(bad_base))
        code, out = flow_run("config", cwd=m)
        expect(code == 2 and "binding here serves" in out, f"a delivery naming another base was accepted: {out}")
        bad_base.pop("baseBranch")
        (m / ".agents/deliveries/FX-1.json").write_text(json.dumps(bad_base))
        c = config_in(m, "issue_url", "sources", "forge", "gates", "base_branch")
        expect(c["issue_url"] == "https://views.test/{key}" and c["sources"]["tracker.issueUrl"] == "delivery-views.json", c["sources"])
        expect(c["forge"] == {"adapter": "github", "repository": "o/n"}, c["forge"])
        expect([g["id"] for g in c["gates"]] == ["lint"] and "binding" in c["sources"]["gates"], c["sources"])
        expect(c["base_branch"] == "feature/FX-1" and "binding" in c["sources"]["baseBranch"], c["sources"])
        code, out = flow_run("config", cwd=m)
        expect(code == 0 and "<- delivery-views.json" in out and "<- orchestration binding" in out, out)
        node22 = fx.call("find_node22", cwd=m)
        if node22:
            (m / "harness.config.ts").write_text(
                "export default { baseRef: 'origin/feature/FX-1', preparationCommands: [{ id: 'lint', command: ['npm', 'run', 'lint'] }] };\n")
            code, out = flow_run("config", cwd=m)
            expect(code == 2 and "disagree" in out, f"a harness/binding gate mismatch wasn't refused: {out}")
            (m / "harness.config.ts").write_text(
                "export default { baseRef: 'origin/feature/FX-1', preparationCommands: [{ id: 'lint', command: ['sh', '-c', 'test -f src/app.txt'] }] };\n")
            code, out = flow_run("config", cwd=m)
            expect(code == 0 and "harness config: harness.config.ts" in out, f"an agreeing harness config was refused: {out}")
            # an extra lens from the harness config has its charter in the repository
            (m / "docs/extra-charter.md").write_text("# Extra charter\n")
            (m / "harness.config.ts").write_text(
                "export default { baseRef: 'origin/feature/FX-1', preparationCommands: [{ id: 'lint', command: ['sh', '-c', 'test -f src/app.txt'] }],"
                " additionalReviewLenses: [{ lensId: 'lens.extra', reviewerId: 'x', charterPath: 'docs/extra-charter.md' }] };\n")
            lenses = config_in(m, "lenses")["lenses"]
            expect(Path(lenses["lens.extra"][1]) == m / "docs/extra-charter.md", f"an extra lens's charter isn't the repository's: {lenses}")
            # an unreadable harness config stops every command but config, unless run-delivery.json ignores it
            (m / "harness.config.ts").write_text("export default {{{ broken\n")
            code, out = flow_run("config", cwd=m)
            expect(code == 0 and "not read" in out, f"config didn't report an unreadable harness config: {out}")
            code, out = flow_run("lead-brief", cwd=m)
            expect(code == 2 and '"harness": "ignore"' in out, f"an unreadable harness config was skipped silently: {out}")
            fc = json.loads((m / ".agents/run-delivery.json").read_text())
            fc["harness"] = "ignore"
            (m / ".agents/run-delivery.json").write_text(json.dumps(fc))
            code, out = flow_run("config", cwd=m)
            expect(code == 0 and "ignored" in out, out)
            (m / "harness.config.ts").unlink()
        # a run-delivery.json value contradicting a higher source is refused, not silently dropped
        fc = json.loads((m / ".agents/run-delivery.json").read_text())
        for field, value, said in (("gates", [{"id": "lint", "argv": ["true"]}], "run-delivery.json sets gates"),
                                   ("forge", {"repository": "someone/else"}, "forge.repository")):
            (m / ".agents/run-delivery.json").write_text(json.dumps({**fc, field: value}))
            code, out = flow_run("config", cwd=m)
            expect(code == 2 and said in out and "disagree" in out, f"a conflicting run-delivery.json {field} passed: {out}")
        (m / ".agents/run-delivery.json").write_text(json.dumps({**fc, "gates": [{"id": "lint", "argv": ["sh", "-c", "test -f src/app.txt"]}]}))
        code, out = flow_run("config", cwd=m)
        expect(code == 0, f"a run-delivery.json gate list matching the binding's was called a conflict: {out}")
        (m / ".agents/run-delivery.json").write_text(json.dumps(fc))
        # a binding that selects checks beyond its fallback list needs the lane gates spelled out; gate cwd is kept
        (m / ".agents/orchestration.json").write_text(json.dumps({**binding, "alwaysRun": ["x"]}))
        code, out = flow_run("config", cwd=m)
        expect(code == 2 and "list the lane gates" in out, f"a binding with alwaysRun didn't need explicit gates: {out}")
        (m / ".agents/orchestration.json").write_text(json.dumps({**binding, "checkTimeoutSeconds": 77, "fallback": [
            {"id": "lint", "argv": ["sh", "-c", "test -f app.txt"], "cwd": "src"}]}))
        gates = config_in(m, "gates")["gates"]
        expect(gates[0]["cwd"] == "src" and gates[0]["timeoutSeconds"] == 77, f"binding gate fields lost: {gates}")
        commit(m, "binding with a gate that only passes in src/")
        code, out = flow_run("gate", "FX-2", cwd=m)
        expect(code == 0 and "gates passed" in out, f"a gate didn't run in its cwd: {out}")
        # "gates": "none" can't switch off checks the binding declares
        (m / ".agents/run-delivery.json").write_text(json.dumps({**fc, "gates": "none"}))
        code, out = flow_run("config", cwd=m)
        expect(code == 2 and "gates" in out and "none" in out, f"'gates: none' switched off the binding's checks: {out}")
        (m / ".agents/run-delivery.json").write_text(json.dumps(fc))
        # no gates anywhere is a problem for the hand-off, unless run-delivery.json says "none"
        (m / ".agents/orchestration.json").write_text(json.dumps({**binding, "fallback": []}))
        _, problems = fx.call("gates_summary", "FX-2", m, "0" * 40, cwd=m, delivery="FX-1")
        expect(problems and "no gates" in problems[0], f"a hand-off with no gates passed: {problems}")
        fc["gates"] = "none"
        (m / ".agents/run-delivery.json").write_text(json.dumps(fc))
        summary, problems = fx.call("gates_summary", "FX-2", m, "0" * 40, cwd=m, delivery="FX-1")
        expect(not problems and "none" in summary, f"an explicit 'gates: none' was refused: {problems}")
        (m / ".agents/orchestration.json").write_text(json.dumps(binding))
        fc.pop("gates")
        (m / ".agents/run-delivery.json").write_text(json.dumps(fc))
        # a requirement's pattern must be one the story pins
        unpinned = json.loads((m / ".agents/deliveries/FX-1.json").read_text())
        unpinned["stories"]["FX-2"]["inApp"] = [{"mode": "fail", "pattern": "/not-pinned$"}]
        (m / ".agents/deliveries/FX-1.json").write_text(json.dumps(unpinned))
        code, out = flow_run("config", cwd=m)
        expect(code == 2 and "isn't one the story pins" in out, f"an unpinned requirement pattern loaded: {out}")
        bad = json.loads((m / ".agents/deliveries/FX-1.json").read_text())
        bad["stories"]["FX-2"]["inApp"] = [{"mode": "fial"}]
        (m / ".agents/deliveries/FX-1.json").write_text(json.dumps(bad))
        code, out = flow_run("config", cwd=m)
        expect(code == 2 and "fial" in out, f"a typo'd requirement mode loaded: {out}")
        if not node22:
            self.skipTest("the harness configuration part needs Node 22.6 or newer, which this host lacks; the rest passed")

    def test_trespass_outside_the_lane_is_refused(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        setup_delivery(fx)
        flow_run("lead-init", cwd=m)
        flow_run("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        wt = fx.lane("FX-3")
        (wt / "src/two.txt").write_text("two\n")
        (wt / "src/one.txt").write_text("trespass\n")
        commit(wt, "FX-3: two and trespass")
        (wt / ".orchestration/review/FX-3").mkdir(parents=True)
        (wt / ".orchestration/review/FX-3/context.md").write_text("- evidence")
        flow_run("gate", "FX-3", cwd=wt)
        code, out = flow_run("app-check", "FX-3", "loaded", "--mode", "loaded", "--path", "/two", "--eval", "true", cwd=wt)
        expect(code == 0, out)
        flow_run("review-prepare", "FX-3", "--round", 1, cwd=wt)
        write_results(wt / ".orchestration/review/FX-3", 1, "FX-3", fx.lenses(wt))
        code, out = flow_run("review-reduce", "FX-3", "--round", 1, cwd=wt); expect(code == 0, out)
        sh("git", "push", "-q", "-u", "origin", "HEAD:feature/FX-3", cwd=wt)
        body = fx.tmp / "body.md"
        body.write_text("x\n")
        flow_run("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", body, cwd=wt)
        code, out = flow_run("handoff", "FX-3", "--pr", "https://forge.test/pr/1", cwd=wt); expect(code == 0, out)
        code, out = flow_run("lead-accept", "FX-3", cwd=m)
        expect(code == 1 and "outside lane-2" in out, f"a trespassing branch was accepted: {out}")
        # lead-init resumes after a half-done init, and a missing ship commit is refused
        shutil.rmtree(fx.tmp / "record")
        fx.call("state_tool", "init", "--headline", "half", cwd=m)
        code, out = flow_run("lead-init", cwd=m); expect(code == 0, f"lead-init didn't resume: {out}")

    def test_timeouts_and_interruptions_stop_their_processes(self) -> None:
        started = time.time()
        try:
            self.call("run_group", ["sh", "-c", "sleep 30 & sleep 30"], Path(tempfile.gettempdir()), 1)
            raise AssertionError("a timed-out command didn't stop")
        except Stopped as error:
            expect("took longer" in str(error) and time.time() - started < 15, str(error))
        leftover = sh("pgrep", "-f", "sleep 30", check=False).stdout.split()
        expect(not leftover, f"the timed-out group left processes behind: {leftover}")
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lock"
            orphan = subprocess.Popen(["sleep", "60"], start_new_session=True)
            try:
                lock.write_text(f"dead holder pid 1 pgid {orphan.pid}\n")
                self.call("Lease", lock, 1, "selftest", 2, then="close")
                time.sleep(0.5)
                expect(orphan.poll() is not None, "a server left by a dead holder wasn't stopped")
            finally:
                if orphan.poll() is None:
                    orphan.kill()
                orphan.wait()

    def test_an_interrupted_check_releases_the_lease_and_its_server(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        setup_delivery(fx)
        flow_run("lead-init", cwd=m)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        flow_run("build", "FX-2", cwd=wt)
        port_open = lambda: fx.call("port_open", fx.port, cwd=fx.tmp)
        code, out = flow_run("app-check", "FX-2", "slow", "--mode", "loaded", "--path", "/slow", "--eval", "true", cwd=wt)
        expect(code == 2 and "longer than 5s" in out, f"a slow check didn't time out: {out}")
        expect(not port_open(), "the server outlived a timed-out check")
        # Both signals main() handles: a kill from a tool timeout, and a closed terminal.
        for sent in (signal.SIGTERM, signal.SIGHUP):
            proc = subprocess.Popen([sys.executable, "-B", str(fx.tool), "app-check", "FX-2", "slow", "--mode", "loaded",
                                     "--path", "/slow", "--eval", "true"], cwd=wt, env=fx.env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                for _ in range(100):
                    if port_open():
                        break
                    time.sleep(0.1)
                expect(port_open(), f"the check's server never came up before {sent.name}")
                proc.send_signal(sent)
                proc.wait(timeout=20)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                proc.stdout.close()
            time.sleep(0.5)
            expect(not port_open(), f"the server outlived a check interrupted by {sent.name}")
            fx.call("Lease", fx.tmp / "app.lock", fx.port, "selftest", 2, then="close", cwd=fx.tmp)

# The fallbacks to the tool's earlier names, pinned: removing one takes a later approved change to this list.
LEGACY_NAMES = (
    (".agents/flow.json", ".agents/run-delivery.json"),
    ("DELIVERY_FLOW_CONFIG", "RUN_DELIVERY_CONFIG"),
    ("DELIVERY_FLOW_DELIVERY", "RUN_DELIVERY_DELIVERY"),
    ("DELIVERY_FLOW_LOCAL", "RUN_DELIVERY_LOCAL"),
)
# Names kept as they were: frozen contract names existing files and adapters depend on.
KEPT_NAMES = ("delivery-flow/1", "delivery-flow-delivery/1", ".flow-build.json", "FLOW_FORGE_REPOSITORY",
              "FLOW_FORGE_TOKEN_ENV")
STABLE_TOOL_PATH = ".agent-skills/current/skills/run-delivery/scripts/run_delivery.py"
# The sections the tool's round brief adds after obtain-review's own, in order.
ROUND_BRIEF_EXTRAS = ["In-app evidence so far", "Context refs (caller-retained material: ticket contract and plan unit)"]

FAKE_HTTP = r'''
import io, json, os, urllib.error, urllib.request
LOG, MODE = os.environ["FAKE_HTTP_LOG"], os.environ.get("FAKE_HTTP_MODE", "ok")
PR = {"links": {"html": {"href": "https://bitbucket.org/ws/repo/pull-requests/7"}}, "state": "OPEN",
      "source": {"commit": {"hash": "abc"}, "branch": {"name": "feature/X-1"}}, "destination": {"branch": {"name": "main"}}}
def answer(request, timeout=None):
    with open(LOG, "a") as log:
        log.write(json.dumps({"method": request.get_method(), "url": request.full_url,
                              "body": json.loads(request.data) if request.data else None}) + "\n")
    auth = request.get_header("Authorization")
    if MODE == "echo":
        raise urllib.error.HTTPError(request.full_url, 401, "denied", {}, io.BytesIO(("rejected " + auth).encode()))
    if MODE == "echo-long":
        # The token straddles the 300-character cut the adapter makes in an error body.
        body = "p" * (300 - len("Bearer ") - 10) + auth + " and more"
        raise urllib.error.HTTPError(request.full_url, 401, "denied", {}, io.BytesIO(body.encode()))
    if MODE == "unreachable":
        raise urllib.error.URLError("no route while sending " + auth)
    if MODE == "unreachable-long":
        # The token straddles the 300-character cut the adapter makes in a connection failure's text.
        raise urllib.error.URLError("q" * (300 - len("<urlopen error ") - len("Bearer ") - 10) + auth + " and more")
    url = request.full_url
    if request.get_method() == "POST" or "/pullrequests/7" in url and "comments" not in url:
        return io.BytesIO(json.dumps(PR).encode())
    if "/pullrequests?" in url:
        return io.BytesIO(json.dumps({"values": [PR]}).encode())
    return io.BytesIO(json.dumps({"values": []}).encode())
urllib.request.urlopen = answer
'''

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
if os.environ.get("FAKE_GH_MODE") == "fail":
    sys.exit("gh: authentication failed for token " + os.environ.get("GH_TOKEN", ""))
if os.environ.get("FAKE_GH_MODE") == "fail-long":
    # The token straddles the start of the last 300 characters the adapter keeps of gh's error.
    sys.exit("gh: authentication failed for token " + os.environ.get("GH_TOKEN", "") + "z" * 290)
command = sys.argv[1:3]
if command == ["pr", "list"]:
    print(json.dumps([{"url": "https://github.com/o/n/pull/3"}]))
elif command == ["pr", "create"]:
    print("https://github.com/o/n/pull/4")
elif command == ["pr", "view"]:
    print(json.dumps({"url": "https://github.com/o/n/pull/3", "state": "OPEN", "headRefOid": "abc",
                      "headRefName": "feature/X-1", "baseRefName": "main"}))
'''


def ready_lane(fx: Fixture) -> Path:
    """A lane with one committed change and its review context filled in, ready for review-prepare."""
    setup_delivery(fx)
    code, out = fx.flow("lead-init", cwd=fx.main)
    expect(code == 0, out)
    wt = fx.lane("FX-3")
    (wt / "src/two.txt").write_text("two\n")
    commit(wt, "FX-3: two")
    (wt / ".orchestration/review/FX-3").mkdir(parents=True)
    (wt / ".orchestration/review/FX-3/context.md").write_text("- evidence")
    return wt


def token_fragments(token: str, text: str) -> list[str]:
    """Every six-character piece of the token that appears in the text."""
    return [token[i:i + 6] for i in range(len(token) - 5) if token[i:i + 6] in text]


def receipt_tuple(entry: dict) -> dict:
    return {k: entry["release"][k] for k in ("releaseId", "profile", "archiveSha256", "metadataSha256")}


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryReleaseAndNamesTests(unittest.TestCase):
    """The tool runs from any installed generation under its new names, and says nothing host-specific."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def tearDown(self) -> None:
        self.assertEqual(bytecode_under(self.generation), [], "the case left bytecode in the installed generation")

    def fixture(self, generation: Path | None = None, receipt: Path | None = None) -> Fixture:
        fx = Fixture(generation or self.generation, receipt or self.receipt)
        self.addCleanup(fx.close)
        return fx

    def updated_install(self) -> tuple[Path, Path, Path]:
        """An installation updated from this module's release to a second one: (old generation, new generation,
        receipt), the old one still on disk as an update leaves it."""
        root = Path(tempfile.mkdtemp(prefix="run-delivery-update-"))
        self.addCleanup(shutil.rmtree, root, True)
        archive, metadata = root / "update.zip", root / "update.release.json"
        build_release(ROOT, archive, metadata, release_id="run-delivery-update", profile_id=PROFILE)
        install = root / "install"
        install.mkdir()
        sh("git", "init", "-q", cwd=install)
        (install / "README.md").write_text("install\n")
        commit(install, "Initial")
        with mock.patch.dict(os.environ, {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""}):
            Lifecycle(install).install(self.archive, self.metadata, maintainer=True)
            old = (install / ".agent-skills/current").resolve()
            Lifecycle(install).update(archive, metadata, maintainer=True)
        new = (install / ".agent-skills/current").resolve()
        self.assertNotEqual(old, new)
        self.assertTrue((old / TOOL_RELATIVE).is_file(), "the update removed the old generation")
        return old, new, install / ".agent-skills/active.json"

    # -- locating the release ----------------------------------------------------------------------------

    def test_every_installed_path_runs_the_generation_it_resolves_to(self) -> None:
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["generation"], self.generation.name)
        expected = receipt_tuple(receipt)
        fx = self.fixture()
        wt = ready_lane(fx)
        paths = {"current": fx.main / STABLE_TOOL_PATH,
                 "claude-code projection": self.install / ".claude/skills/run-delivery/scripts/run_delivery.py",
                 "codex projection": self.install / ".agents/skills/run-delivery/scripts/run_delivery.py"}
        for name, tool in paths.items():
            with self.subTest(path=name):
                self.assertTrue(tool.is_file(), tool)
                self.assertEqual(tool.resolve(), self.tool.resolve())
                self.assertEqual(call(tool, "release_tuple", cwd=wt, env=fx.env, delivery="FX-1"), expected)
        # A brief written through a projection link carries the tuple and names the tool by its stable path.
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt, tool=paths["claude-code projection"])
        expect(code == 0, out)
        brief_text = (wt / ".orchestration/review/FX-3/brief-r1-outcome-correctness.md").read_text()
        for value in expected.values():
            self.assertIn(f"`{value}`", brief_text)
        self.assertIn(f"Then: python3 -B {STABLE_TOOL_PATH} review-reduce FX-3 --round 1", out)
        for text in (out, brief_text):
            self.assertNotIn("generations/", text)
            self.assertEqual(host_terms(text), [])
        code, out = fx.flow("lead-brief", cwd=fx.main, tool=paths["codex projection"])
        expect(code == 0, out)
        lane_brief = (fx.main / "docs/lane-brief.md").read_text()
        self.assertIn(f"`python3 -B {STABLE_TOOL_PATH} <command>`", lane_brief)
        self.assertNotIn("generations/", lane_brief)
        self.assertEqual(host_terms(lane_brief), [])

    def test_an_older_generation_still_running_carries_the_previous_tuple(self) -> None:
        old, new, receipt_file = self.updated_install()
        receipt = json.loads(receipt_file.read_text())
        self.assertEqual((receipt["generation"], receipt["previous"]["generation"]), (new.name, old.name))
        fx = self.fixture(new, receipt_file)
        wt = ready_lane(fx)
        review = wt / ".orchestration/review/FX-3"
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt, tool=old / TOOL_RELATIVE)
        expect(code == 0, out)
        brief_text = (review / "brief-r1-adversarial-testing.md").read_text()
        previous, current = receipt_tuple(receipt["previous"]), receipt_tuple(receipt)
        self.assertIn(f"`{previous['releaseId']}`", brief_text)
        self.assertIn(f"`{previous['archiveSha256']}`", brief_text)
        self.assertNotIn(current["archiveSha256"], brief_text)
        # The current generation, run the same way, carries the current entry's tuple and never the previous one's.
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, "--restart", cwd=wt, tool=new / TOOL_RELATIVE)
        expect(code == 0, out)
        brief_text = (review / "brief-r1-adversarial-testing.md").read_text()
        self.assertIn(f"`{current['releaseId']}`", brief_text)
        self.assertIn(f"`{current['archiveSha256']}`", brief_text)
        self.assertNotIn(previous["archiveSha256"], brief_text)

    def test_a_generation_in_neither_receipt_entry_writes_no_brief(self) -> None:
        fx = self.fixture()
        wt = ready_lane(fx)
        review = wt / ".orchestration/review/FX-3"
        receipt = json.loads(self.receipt.read_text())
        # A round prepared while the receipt still named this generation, with one result saved.
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt)
        expect(code == 0, out)
        (review / "result-r1-outcome-correctness.json").write_text("{}")
        before = {p.name: p.read_bytes() for p in review.iterdir() if p.is_file()}
        stranger = {**receipt, "generation": "0" * 64,
                    "previous": {"files": [], "generation": "1" * 64, "release": receipt["release"]}}
        (wt / ".agent-skills/active.json").write_text(json.dumps(stranger))
        commit(wt, "FX-3: a receipt naming other generations")
        worktrees = sh("git", "worktree", "list", "--porcelain", cwd=wt).stdout
        for restart in ((), ("--restart",)):
            with self.subTest(arguments=restart):
                code, out = fx.flow("review-prepare", "FX-3", "--round", 1, *restart, cwd=wt)
                expect(code == 2, out)
                # Refused before anything was discarded, checked out or written.
                self.assertEqual({p.name: p.read_bytes() for p in review.iterdir() if p.is_file()}, before)
                self.assertEqual(sh("git", "worktree", "list", "--porcelain", cwd=wt).stdout, worktrees)
        self.assertIn(f"wrong: the running generation {self.generation.name} is neither the current nor the previous "
                      "generation", out)
        self.assertIn(f"fix: run the tool as {STABLE_TOOL_PATH}", out)
        # With no receipt at all, the same refusal names the missing receipt.
        (wt / ".agent-skills/active.json").unlink()
        commit(wt, "FX-3: no receipt")
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, "--restart", cwd=wt)
        expect(code == 2 and "wrong: no receipt at" in out, out)
        self.assertEqual({p.name: p.read_bytes() for p in review.iterdir() if p.is_file()}, before)

    def test_the_release_key_names_another_installation(self) -> None:
        fx = self.fixture()
        wt = ready_lane(fx)
        # The repository has no receipt of its own; the release it names is another installation's.
        sh("git", "rm", "-q", ".agent-skills/active.json", cwd=fx.main)
        home = fx.tmp / "home"
        home.mkdir()
        (home / "tooling").symlink_to(self.install)
        conf = json.loads((fx.main / ".agents/run-delivery.json").read_text())
        conf["release"] = "~/tooling/.agent-skills/current"
        (fx.main / ".agents/run-delivery.json").write_text(json.dumps(conf))
        commit(fx.main, "FX-1: use the tooling installation")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "origin/feature/FX-1", cwd=wt)
        env = {**fx.env, "HOME": str(home)}
        settings = call(self.tool, "Config", wt, "FX-1", cwd=wt, env=env, attrs=["release"])
        self.assertEqual(settings["release"], str(home / "tooling/.agent-skills/current"))
        expected = receipt_tuple(json.loads(self.receipt.read_text()))
        self.assertEqual(call(self.tool, "release_tuple", cwd=wt, env=env, delivery="FX-1"), expected)
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt, env=env)
        expect(code == 0, out)
        self.assertIn("Then: python3 -B ~/tooling/.agent-skills/current/skills/run-delivery/scripts/run_delivery.py "
                      "review-reduce FX-3 --round 1", out)
        brief_text = (wt / ".orchestration/review/FX-3/brief-r1-outcome-correctness.md").read_text()
        self.assertIn(f"`{expected['archiveSha256']}`", brief_text)

    # -- names ------------------------------------------------------------------------------------------------

    def test_the_earlier_names_are_pinned_and_the_kept_names_unchanged(self) -> None:
        self.assertEqual(tuple(map(tuple, self.call("LEGACY_NAMES", attr=True))), LEGACY_NAMES)
        self.assertEqual(self.call("TOOL_NAMES", attr=True), ["run_delivery.py", "flow.py"])
        self.assertEqual(self.call("DEFAULT_ARCHIVE", attr=True), "~/.local/share/run-delivery/archive/{repo}/{delivery}")
        self.assertEqual(self.call("TOOL_PATH", attr=True), STABLE_TOOL_PATH)
        shipped = "\n".join(p.read_text(encoding="utf-8") for p in (self.generation / "skills/run-delivery").rglob("*")
                            if p.suffix in (".py", ".md"))
        for name in KEPT_NAMES:
            self.assertIn(name, shipped)
        self.assertFalse((self.generation / "skills/run-delivery/scripts/flow.py").exists())

    def call(self, fn: str, *args, cwd: Path | None = None, **kwargs):
        return call(self.tool, fn, *args, cwd=cwd or Path(tempfile.gettempdir()), **kwargs)

    def test_the_earlier_config_file_is_read_only_without_the_new_one(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        sh("git", "mv", ".agents/run-delivery.json", ".agents/flow.json", cwd=m)
        commit(m, "FX-1: the earlier config name")
        code, out = fx.flow("config", cwd=m)
        expect(code == 0 and "https://tracker.test/{key}" in out and "<- flow.json" in out, out)
        self.assertEqual(call(self.tool, "LEGACY_USED", cwd=m, env=fx.env, delivery="FX-1", attr=True),
                         [[".agents/flow.json", ".agents/run-delivery.json"]])
        code, out = fx.flow("lead-brief", cwd=m)
        expect(code == 0, out)
        lane_brief = (m / "docs/lane-brief.md").read_text()
        self.assertIn("in `.agents/flow.json` (`inApp`)", lane_brief)
        self.assertNotIn("run-delivery.json", lane_brief)
        conf = json.loads((m / ".agents/flow.json").read_text())
        (m / ".agents/run-delivery.json").write_text(json.dumps({**conf, "tracker": {"issueUrl": "https://new.test/{key}"}}))
        code, out = fx.flow("config", cwd=m)
        expect(code == 0 and "https://new.test/{key}" in out and "<- run-delivery.json" in out
               and "https://tracker.test/{key}" not in out, out)
        self.assertEqual(call(self.tool, "LEGACY_USED", cwd=m, env=fx.env, delivery="FX-1", attr=True), [])
        expect(fx.call("Config", m, "FX-1", cwd=m, attrs=["lane_pattern"])["lane_pattern"] == "lanes/{key}",
               "an explicit laneCheckout was not kept")
        del conf["laneCheckout"]
        (m / ".agents/run-delivery.json").write_text(json.dumps(conf))
        expect(fx.call("Config", m, "FX-1", cwd=m, attrs=["lane_pattern"])["lane_pattern"] == ".orchestration/lanes/{key}",
               "the default lane checkout isn't beside the binding's state")

    def test_the_earlier_environment_names_are_read_only_without_the_new_ones(self) -> None:
        fx = self.fixture()
        m = fx.main
        path = setup_delivery(fx)
        # Two deliveries, so only a named one is loaded.
        other = json.loads(path.read_text())
        (m / ".agents/deliveries/FX-9.json").write_text(json.dumps({**other, "delivery": "FX-9"}))
        named = lambda out: re.findall(r"^delivery\s+(\S+)$", out, re.MULTILINE)
        legacy_file, new_file = fx.tmp / "legacy.json", fx.tmp / "new.json"
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        legacy_file.write_text(json.dumps({**conf, "tracker": {"issueUrl": "https://legacy.test/{key}"}}))
        new_file.write_text(json.dumps({**conf, "tracker": {"issueUrl": "https://new.test/{key}"}}))
        cases = [
            ({"DELIVERY_FLOW_CONFIG": str(legacy_file)}, lambda out: "https://legacy.test/{key}" in out),
            ({"DELIVERY_FLOW_CONFIG": str(legacy_file), "RUN_DELIVERY_CONFIG": str(new_file)},
             lambda out: "https://new.test/{key}" in out and "legacy.test" not in out),
            ({"DELIVERY_FLOW_DELIVERY": "FX-1"}, lambda out: named(out) == ["FX-1"]),
            ({"DELIVERY_FLOW_DELIVERY": "FX-1", "RUN_DELIVERY_DELIVERY": "FX-9"}, lambda out: named(out) == ["FX-9"]),
            ({}, lambda out: named(out) == []),
        ]
        for extra, holds in cases:
            with self.subTest(environment=extra):
                code, out = fx.flow("config", cwd=m, env={**fx.env, **extra})
                expect(code == 0 and holds(out), out)
        used = lambda extra: call(self.tool, "LEGACY_USED", cwd=m, env={**fx.env, **extra}, delivery="FX-1", attr=True)
        self.assertEqual(used({"DELIVERY_FLOW_CONFIG": str(legacy_file)}), [["DELIVERY_FLOW_CONFIG", "RUN_DELIVERY_CONFIG"]])
        self.assertEqual(used({"DELIVERY_FLOW_CONFIG": str(legacy_file), "RUN_DELIVERY_CONFIG": str(new_file)}), [])
        # A brief names the configuration the tool actually read.
        code, out = fx.flow("--delivery", "FX-1", "lead-brief", cwd=m, env={**fx.env, "RUN_DELIVERY_CONFIG": str(new_file)})
        expect(code == 0, out)
        self.assertIn(f"in `{new_file}` (`inApp`)", (m / "docs/lane-brief.md").read_text())
        # The lane reads its own working tree only when told to, under either name.
        (m / ".agents/deliveries/FX-9.json").unlink()
        (m / ".agents/orchestration.json").write_text(json.dumps({"base": {"remote": "origin", "ref": "feature/FX-1"}}))
        commit(m, "FX-1: binding")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        wt = fx.lane("FX-2")
        plan = wt / "docs/plan.md"
        plan.write_text(plan.read_text().replace("it loads; it fails.", "it loads; it fails; LANE-ONLY."))
        for extra, local in (({}, False), ({"DELIVERY_FLOW_LOCAL": "1"}, True), ({"RUN_DELIVERY_LOCAL": "1"}, True),
                             ({"DELIVERY_FLOW_LOCAL": "1", "RUN_DELIVERY_LOCAL": "0"}, False)):
            with self.subTest(environment=extra):
                context = call(self.tool, "plan_context", "FX-2", cwd=wt, env={**fx.env, **extra})
                self.assertEqual("LANE-ONLY" in context, local, context)

    # -- processes -----------------------------------------------------------------------------------------

    def test_lead_close_stops_this_deliverys_processes_under_either_name(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=m)
        expect(code == 0, out)
        home = fx.tmp / "home"
        home.mkdir()
        state = fx.tmp / "record"
        sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir", state,
           "journal", "--kind", "delivery.closed", "--summary", "closed", cwd=m)
        bin_dir = fx.tmp / "bin"
        bin_dir.mkdir()
        for name in ("flow.py", "run_delivery.py", "merge_queue.py", "render_state.py", "notflow.py"):
            (bin_dir / name).write_text("import time\ntime.sleep(600)\n")
        start = lambda name, *args: subprocess.Popen([sys.executable, "-B", str(bin_dir / name), *args], cwd=m)
        ours = {"the earlier tool's sweep": start("flow.py", "lead-reconcile", "--every", "600"),
                "the tool's sweep": start("run_delivery.py", "lead-reconcile", "--every", "600"),
                "the merge queue": start("merge_queue.py", "--dir", str(state)),
                "the renderer": start("render_state.py", "--dir", str(state))}
        theirs = start("run_delivery.py", "--delivery", "OTHER-9", "lead-reconcile", "--every", "600")
        # Not this tool, though its name ends like the earlier one's.
        stranger = start("notflow.py", "lead-reconcile", "--every", "600")
        try:
            time.sleep(0.5)
            code, out = fx.flow("lead-close", cwd=m, env={**fx.env, "HOME": str(home)})
            expect(code == 0 and "closed out" in out, out)
            # With no cleanup.archive, the record is archived under the run-delivery default.
            self.assertTrue(list((home / ".local/share/run-delivery/archive/main/FX-1").glob("*/record/journal.jsonl")),
                            out)
            self.assertFalse((home / ".local/share/delivery-flow").exists())
            for name, proc in ours.items():
                with self.subTest(process=name):
                    self.assertIn(f"stopped {proc.pid}", out)
                    proc.wait(timeout=10)
            for proc in (theirs, stranger):
                self.assertNotIn(f"stopped {proc.pid}", out)
                self.assertIsNone(proc.poll(), "lead-close stopped a process that isn't this delivery's")
            listing = sh("ps", "-axo", "pid=,command=").stdout
            left = [line for line in listing.splitlines() if str(bin_dir) in line
                    and int(line.split()[0]) not in (theirs.pid, stranger.pid)]
            self.assertEqual(left, [], "a process lead-close should have stopped is still running")
        finally:
            for proc in (*ours.values(), theirs, stranger):
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

    # -- sign-in values -------------------------------------------------------------------------------------

    def test_adapter_arguments_that_look_like_sign_in_values_are_refused(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        fx.flow("lead-init", cwd=fx.main)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        code, out = fx.flow("build", "FX-2", cwd=wt)
        expect(code == 0, out)
        check = ["app-check", "FX-2", "page", "--mode", "loaded", "--path", "/one", "--eval", "true", "--"]
        for refused in (["--password=hunter2secret"], ["--api-key", "hunter2secret"], ["--API_KEY=hunter2secret"], ["--apikey=hunter2secret"],
                        ["token=hunter2secret"], ["--passwd", "hunter2secret"], ["--client-secret", "hunter2secret"],
                        ["--credentials=hunter2secret"], ["--auth-token", "hunter2secret"]):
            with self.subTest(arguments=refused):
                code, out = fx.flow(*check, *refused, cwd=wt)
                expect(code == 2 and "looks like a sign-in value" in out and "hunter2secret" not in out, out)
                self.assertFalse((wt / ".orchestration/review/FX-2/app-checks/page.json").exists())
        code, out = fx.flow("app-video", "FX-2", "clip", "--mode", "loaded", "--path", "/one", "--",
                            "--password=hunter2secret", cwd=wt)
        expect(code == 2 and "looks like a sign-in value" in out and "hunter2secret" not in out, out)
        for accepted in (["--sort-key", "x"], ["--keyboard"], ["--monkey=1"]):
            with self.subTest(arguments=accepted):
                code, out = fx.flow(*check, *accepted, cwd=wt)
                expect(code == 0 and "PASS" in out, out)

    # -- forge adapters -------------------------------------------------------------------------------------

    def adapter(self, name: str) -> Path:
        return self.generation / f"skills/run-delivery/adapters/forge_{name}.py"

    def test_the_bitbucket_adapter_writes_only_a_draft_and_never_prints_its_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site, log = Path(tmp) / "site", Path(tmp) / "requests.jsonl"
            site.mkdir()
            (site / "sitecustomize.py").write_text(FAKE_HTTP)
            body = Path(tmp) / "body.md"
            body.write_text("Summary\n")
            token = "bbtoken-4c1d9e2a7f3b"
            env = {**clean_environment(), "PYTHONPATH": str(site), "FAKE_HTTP_LOG": str(log),
                   "FLOW_FORGE_REPOSITORY": "ws/repo", "FLOW_FORGE_TOKEN_ENV": "FORGE_TOKEN", "FORGE_TOKEN": token}
            run = lambda *args, **extra: subprocess.run([sys.executable, "-B", str(self.adapter("bitbucket")), *args],
                                                        env={**env, **extra}, text=True, capture_output=True)
            url = "https://bitbucket.org/ws/repo/pull-requests/7"
            commands = [("find", "--source", "feature/X-1"), ("get", "--url", url),
                        ("reviews", "--url", url, "--head", "abc"),
                        ("open", "--source", "feature/X-1", "--destination", "main", "--title", "X-1", "--body-file", str(body))]
            for args in commands:
                with self.subTest(command=args[0]):
                    done = run(*args)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    json.loads(done.stdout)
                    self.assertNotIn(token, done.stdout + done.stderr)
            requests = [json.loads(line) for line in log.read_text().splitlines()]
            writes = [r for r in requests if r["method"] != "GET"]
            self.assertEqual([(r["method"], r["url"].split("/repositories/ws/repo")[1]) for r in writes],
                             [("POST", "/pullrequests")])
            self.assertIs(writes[0]["body"]["draft"], True)
            for mode in ("echo", "echo-long", "unreachable", "unreachable-long"):
                for args in commands:
                    with self.subTest(mode=mode, command=args[0]):
                        done = run(*args, FAKE_HTTP_MODE=mode)
                        self.assertNotEqual(done.returncode, 0)
                        self.assertEqual(token_fragments(token, done.stdout + done.stderr), [])
                        if not mode.endswith("-long"):  # there the cut falls inside the marker
                            self.assertIn("[redacted]", done.stderr)
            done = run("find", "--source", "x", FORGE_TOKEN="")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("FLOW_FORGE_TOKEN_ENV", done.stderr)

    def test_the_github_adapter_writes_only_a_draft_and_has_no_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir, log = Path(tmp) / "bin", Path(tmp) / "gh.jsonl"
            bin_dir.mkdir()
            (bin_dir / "gh").write_text(FAKE_GH.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
            (bin_dir / "gh").chmod(0o755)
            body = Path(tmp) / "body.md"
            body.write_text("Summary\n")
            token = "ghtoken-9e2a4c1d5a8e"
            env = {**clean_environment(), "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                   "FAKE_GH_LOG": str(log), "FLOW_FORGE_REPOSITORY": "o/n", "GH_TOKEN": token}
            run = lambda *args, **extra: subprocess.run([sys.executable, "-B", str(self.adapter("github")), *args],
                                                        env={**env, **extra}, text=True, capture_output=True)
            commands = [("find", "--source", "feature/X-1"), ("get", "--url", "https://github.com/o/n/pull/3"),
                        ("open", "--source", "feature/X-1", "--destination", "main", "--title", "X-1", "--body-file", str(body))]
            for args in commands:
                with self.subTest(command=args[0]):
                    done = run(*args)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    json.loads(done.stdout)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([c[:2] for c in calls], [["pr", "list"], ["pr", "view"], ["pr", "create"]])
            self.assertIn("--draft", calls[2])
            for mode in ("fail", "fail-long"):
                for args in commands:
                    with self.subTest(mode=mode, failing=args[0]):
                        done = run(*args, FAKE_GH_MODE=mode)
                        self.assertNotEqual(done.returncode, 0)
                        self.assertEqual(token_fragments(token, done.stdout + done.stderr), [])
                        if mode == "fail":  # in fail-long the cut falls inside the marker
                            self.assertIn("[redacted]", done.stderr)
            done = run("reviews", "--url", "https://github.com/o/n/pull/3", "--head", "abc")
            self.assertNotEqual(done.returncode, 0, "the GitHub adapter answered reviews")

    def test_pr_review_through_an_adapter_without_reviews_reports_it_unsupported(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        conf["forge"] = {"adapter": "github", "repository": "o/n"}
        conf["review"]["pr"] = {"author": "Pipeline Bot"}
        (m / ".agents/run-delivery.json").write_text(json.dumps(conf))
        commit(m, "FX-1: GitHub with a pipeline review")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        wt = fx.lane("FX-2")
        code, out = fx.flow("pr-review", "FX-2", cwd=wt)
        expect(code == 2 and "unsupported by this forge adapter" in out and "github has no `reviews`" in out, out)
        # The reference Bitbucket adapter, and an adopter's own argv adapter, answer reviews.
        for forge, answers in (({"adapter": "github", "repository": "o/n"}, False),
                               ({"adapter": "bitbucket", "repository": "ws/repo"}, True),
                               ({"argv": ["true"]}, True)):
            with self.subTest(forge=forge):
                (m / ".agents/run-delivery.json").write_text(json.dumps({**conf, "forge": forge}))
                self.assertEqual(call(self.tool, "forge_answers", "reviews", cwd=m, env=fx.env, delivery="FX-1"), answers)

    # -- shipped text ---------------------------------------------------------------------------------------

    def test_the_round_brief_keeps_obtain_reviews_sections_as_its_prefix(self) -> None:
        headings = lambda path: re.findall(r"^## (.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
        theirs = headings(self.generation / "skills/obtain-review/references/round-brief-template.md")
        ours = headings(self.generation / "skills/run-delivery/templates/round-brief.md")
        self.assertTrue(theirs)
        self.assertEqual(ours[:len(theirs)], theirs)
        self.assertEqual(ours[len(theirs):], ROUND_BRIEF_EXTRAS)

    def test_no_shipped_file_carries_a_host_term_outside_a_marked_example(self) -> None:
        lock = json.loads((ROOT / "provenance.lock.json").read_text(encoding="utf-8"))
        outputs = next(e for e in lock["entries"] if e["id"] == "run-delivery")["outputs"]
        self.assertTrue(outputs)
        for output in outputs:
            with self.subTest(path=output["path"]):
                self.assertEqual(host_terms((ROOT / output["path"]).read_text(encoding="utf-8")), [])

    def test_the_host_term_scan_is_not_vacuous(self) -> None:
        for sample in ("start_session", "SendMessage to its id", "a private Artifact", "in Claude Code",
                       '<meta name="claude:viewport-fit-sides">', "mcp__tool", "~/.claude/skills/x",
                       ".claude/worktrees/{key}", ".agents/skills/run-delivery"):
            with self.subTest(sample=sample):
                self.assertTrue(host_terms(f"text {sample} text"))
                self.assertEqual(host_terms(f"text\n```text example\n{sample}\n```\ntext"), [])
                self.assertTrue(host_terms(f"text\n```text\n{sample}\n```\ntext"))
        self.assertEqual(host_terms("the build artifact and reviewArtifact"), [])


# A check adapter that keeps the in-app contract on the shipped fixture page: what a real browser adapter reports for
# each of adapter-test's cases.
FAKE_CONTRACT_CHECK = r'''
import datetime, json, sys, time, urllib.request
spec = json.load(open(sys.argv[1])); port = sys.argv[2]
stamp = lambda: (time.sleep(0.005), datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))[1]
build = json.load(urllib.request.urlopen("http://127.0.0.1:%s/.flow-build.json" % port))
path = spec["path"]
report = {"ready": True, "build": build, "held": [], "failed": [], "evals": [], "afterEvals": [],
          "afterForFound": True if spec.get("afterFor") else None, "pageErrors": [], "consoleErrors": [],
          "alertsSeen": {"/?flash": ["transient"], "/?flash-attr": ["became an alert"],
                         "/?flash-text": ["live text"]}.get(path, [])}
if spec["holds"]:
    held = {"regex": spec["holds"][0], "url": "http://127.0.0.1/api/one", "heldAt": stamp()}
    report["evals"] = [{"expr": e, "result": True, "evalAt": stamp()} for e in spec["evals"]]
    held["releasedAt"] = stamp()
    report["held"] = [held]
    if path == "/?refetch":
        report["held"].append({**held, "heldAt": stamp(), "releasedAt": stamp(), "afterRelease": True})
    report["afterEvals"] = [{"expr": e, "result": True, "afterAt": stamp()} for e in spec["afterEvals"]]
elif spec["fails"]:
    report["failed"] = [{"regex": spec["fails"][0], "url": "http://127.0.0.1/api/one", "status": 500, "at": stamp()}]
    report["evals"] = [{"expr": e, "result": True, "evalAt": stamp()} for e in spec["evals"]]
else:
    report["evals"] = [{"expr": e, "result": True, "evalAt": stamp()} for e in spec["evals"]]
print(json.dumps(report))
'''

FAKE_GH_SIGNED_IN = r'''#!/usr/bin/env python3
import json, sys
command = sys.argv[1:3]
if command == ["auth", "status"]:
    sys.exit(0)
if command == ["pr", "list"]:
    print(json.dumps([]))
'''


def plan_delivery(fx: Fixture) -> Path:
    """The fixture delivery with the plan chapter on: criteria, an outcome, and finish lines with covers and labels."""
    path = setup_delivery(fx)
    d = json.loads(path.read_text())
    d["stories"]["FX-2"]["criteria"] = [{"id": "one-loads", "text": "Thing one loads"},
                                        {"id": "one-fails", "text": "Thing one says when it fails"}]
    d["stories"]["FX-3"]["criteria"] = [{"id": "two-renders", "text": "Thing two renders"}]
    d["finish"] = [{"id": "acs", "label": "Every criterion", "text": "AC1-AC2 met",
                    "covers": {"criteria": ["one-loads", "one-fails", "two-renders"], "proofs": ["merged"]}}]
    d["outcome"] = {"summary": "Things one and two work.", "text": "Things one and two work, and say when they fail."}
    path.write_text(json.dumps(d, indent=2))
    commit(fx.main, "FX-1: the plan's criteria")
    sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
    return path


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryPlanTests(unittest.TestCase):
    """The plan chapter's producer: plan.json from the delivery file, the changes log and the lead-side flow logs,
    written by lead-side commands alone, and only when it changed."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        return fx

    def record(self, fx: Fixture) -> dict:
        return json.loads(sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py",
                             "--dir", fx.tmp / "record", "show").stdout)

    def plan(self, fx: Fixture) -> dict:
        """plan.json, after checking it with the release's own delivery-views check-plan."""
        path = fx.tmp / "record/plan.json"
        checked = sh(sys.executable, "-B", fx.release / "skills/delivery-views/scripts/views_common.py", "check-plan", path,
                     check=False)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        return json.loads(path.read_text())

    def changes_log(self, fx: Fixture) -> list[dict]:
        path = fx.tmp / "flow-log/plan-changes.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def written(self, fx: Fixture) -> tuple:
        """Everything a rebuild may write: plan.json, the record's journal and the changes log, byte for byte."""
        read = lambda path: path.read_bytes() if path.exists() else None
        return (read(fx.tmp / "record/plan.json"), read(fx.tmp / "record/journal.jsonl"),
                read(fx.tmp / "flow-log/plan-changes.jsonl"))

    def test_lead_init_keeps_only_id_text_and_met_in_the_record_and_writes_the_approved_plan(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record(fx)["state"]["finish"], [{"id": "acs", "text": "AC1-AC2 met", "met": False}])
        plan = self.plan(fx)
        self.assertEqual([(u["subject"], [c["id"] for c in u["criteria"]], u["proofs"]) for u in plan["units"]], [
            ("FX-2", ["one-loads", "one-fails"], ["review", "handed", "merged", "in-app-hold", "in-app-fail", "in-app-any"]),
            ("FX-3", ["two-renders"], ["review", "handed", "merged", "in-app-loaded"])])
        self.assertEqual(plan["finish"][0]["label"], "Every criterion")
        self.assertEqual(plan["outcome"]["summary"], "Things one and two work.")
        self.assertEqual(plan["approved"]["snapshot"]["units"], plan["units"])
        self.assertNotIn("review", plan["approved"])
        self.assertEqual(plan["changes"], [])
        log = self.changes_log(fx)
        self.assertEqual([entry["type"] for entry in log], ["snapshot"])
        self.assertFalse(log[0]["late"])
        updates = [e for e in self.record(fx)["journal"] if e["summary"].startswith("Plan updated:")]
        self.assertEqual([e["summary"] for e in updates], [f"Plan updated: {plan['updated']}; first build"])

    def test_two_sweeps_with_no_change_write_nothing(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        fx.flow("lead-init", cwd=fx.main)
        before = self.written(fx)
        self.assertIsNotNone(before[0])
        for _ in range(2):
            code, out = fx.flow("lead-reconcile", cwd=fx.main)
            self.assertEqual(code, 0, out)
            self.assertIn("plan: unchanged", out)
            self.assertEqual(self.written(fx), before)
        code, out = fx.flow("lead-brief", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.written(fx), before, "lead-brief rewrote an unchanged plan")

    def test_a_delivery_without_criteria_writes_no_plan(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        fx.flow("lead-reconcile", cwd=fx.main)
        fx.flow("lead-brief", cwd=fx.main)
        self.assertEqual(self.written(fx)[0::2], (None, None))
        self.assertFalse(any(e["summary"].startswith("Plan updated:") for e in self.record(fx)["journal"]))
        code, out = fx.flow("lead-plan", "why", "c1", "--why", "x", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("the plan chapter is off", out)

    def test_a_delivery_initialized_before_criteria_gets_a_late_snapshot(self) -> None:
        fx = self.fixture()
        path = setup_delivery(fx)
        fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(self.written(fx)[0::2], (None, None))
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"] = [{"id": "one-loads", "text": "Thing one loads"}]
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: criteria, after the start")
        code, out = fx.flow("lead-reconcile", cwd=fx.main)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        self.assertEqual(plan["approved"]["review"], "taken after the delivery started")
        log = self.changes_log(fx)
        self.assertEqual([(entry["type"], entry.get("late")) for entry in log], [("snapshot", True)])
        self.assertEqual(plan["approved"]["at"], log[0]["at"])
        self.assertEqual(plan["changes"], [], "the late snapshot's own criteria read as changes")

    def test_lead_plan_change_records_before_and_after_and_commits_the_delivery_file(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--text", "Thing one says it failed", "--why", "The operator eased it.", "--ref", "#7", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn("recorded c1: relaxed FX-2", out)
        self.assertIn("git push origin HEAD:feature/FX-1", out)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=m).stdout, "", "the change wasn't committed")
        self.assertIn("plan change c1", sh("git", "log", "-1", "--format=%s", cwd=m).stdout)
        committed = json.loads(sh("git", "show", "HEAD:.agents/deliveries/FX-1.json", cwd=m).stdout)
        self.assertEqual(committed["stories"]["FX-2"]["criteria"][1], {"id": "one-fails", "text": "Thing one says it failed"})
        self.assertNotEqual(sh("git", "rev-parse", "HEAD", cwd=m).stdout, sh("git", "rev-parse", "origin/feature/FX-1", cwd=m).stdout,
                            "lead-plan pushed")
        change = self.plan(fx)["changes"][0]
        self.assertEqual(change, {"id": "c1", "at": change["at"], "kind": "relaxed", "subjects": ["FX-2"],
                                  "criteria": ["one-fails"], "by": "lead", "why": "The operator eased it.",
                                  "source": "lead-plan", "ref": "#7", "before": "Thing one says when it fails",
                                  "after": "Thing one says it failed"})
        # The rebuild after it found the edit explained, so it recorded no unrecorded change.
        code, out = fx.flow("lead-reconcile", cwd=m)
        self.assertEqual([c["id"] for c in self.plan(fx)["changes"]], ["c1"], out)
        # Refused on a dirty checkout, and on one behind the base, with nothing written.
        before = (path.read_text(), self.written(fx))
        (m / "scratch.txt").write_text("x\n")
        code, out = fx.flow("lead-plan", "change", "--kind", "changed", "--subjects", "FX-3", "--criterion", "two-renders",
                            "--text", "Two", "--why", "w", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("uncommitted changes", out)
        (m / "scratch.txt").unlink()
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-1", cwd=m)
        other = fx.tmp / "other"
        sh("git", "clone", "-q", "-b", "feature/FX-1", fx.origin, other)
        (other / "src/app.txt").write_text("moved\n")
        commit(other, "FX-1: the base moves")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=other)
        code, out = fx.flow("lead-plan", "change", "--kind", "changed", "--subjects", "FX-3", "--criterion", "two-renders",
                            "--text", "Two", "--why", "w", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("diverged from origin/feature/FX-1", out)
        self.assertEqual((path.read_text(), self.written(fx)), before)

    def test_a_finish_change_adds_the_line_to_the_record_and_the_plan(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        covers = {"criteria": "all", "proofs": ["review"]}
        code, out = fx.flow("lead-plan", "change", "--kind", "finish", "--finish", "reviewed", "--text", "Every story is reviewed",
                            "--label", "Reviewed", "--covers", json.dumps(covers), "--why", "Asked for at the review.", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record(fx)["state"]["finish"][-1], {"id": "reviewed", "text": "Every story is reviewed", "met": False})
        plan = self.plan(fx)
        self.assertEqual([(line["id"], line["label"], line["covers"]) for line in plan["finish"]][-1], ("reviewed", "Reviewed", covers))
        self.assertEqual([(c["kind"], c.get("before"), c["after"]) for c in plan["changes"]], [("finish", None, covers)])
        # Changing an existing line records what it covered before.
        narrower = {"criteria": ["one-loads"], "proofs": ["merged"]}
        code, out = fx.flow("lead-plan", "change", "--kind", "finish", "--finish", "acs", "--covers", json.dumps(narrower),
                            "--why", "Two moved out.", cwd=m)
        self.assertEqual(code, 0, out)
        change = self.plan(fx)["changes"][-1]
        self.assertEqual((change["before"], change["after"]), ({"criteria": ["one-loads", "one-fails", "two-renders"],
                                                                "proofs": ["merged"]}, narrower))

    def test_a_removed_story_leaves_the_record_and_stays_in_the_plan_marked_removed(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        code, out = fx.flow("lead-plan", "change", "--kind", "removed", "--subjects", "FX-3", "--why", "Folded into FX-2.", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertNotIn("FX-3", json.loads(path.read_text())["stories"])
        self.assertEqual([p["subject"] for p in self.record(fx)["state"]["planned"]], ["FX-2"])
        plan = self.plan(fx)
        self.assertEqual([(u["subject"], u.get("removed", False)) for u in plan["units"]], [("FX-2", False), ("FX-3", True)])
        self.assertEqual([(c["kind"], c["subjects"], c["by"]) for c in plan["changes"]], [("removed", ["FX-3"], "lead")])
        fx.flow("lead-reconcile", cwd=m)
        self.assertEqual(len(self.plan(fx)["changes"]), 1, "the recorded removal read as an unrecorded edit too")
        self.assertIn("FX-3", [u["subject"] for u in plan["approved"]["snapshot"]["units"]])

    def test_a_story_registered_mid_delivery_records_an_added_change_until_lead_plan_why_gives_its_reason(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        d = json.loads(path.read_text())
        d["stories"]["FX-4"] = {"title": "Thing four", "lane": "lane-1", "criteria": [{"id": "four-works", "text": "Four works"}]}
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a story added")
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        self.assertEqual([(c["id"], c["kind"], c["subjects"], "why" in c) for c in plan["changes"]], [("c1", "added", ["FX-4"], False)])
        self.assertEqual(plan["units"][-1]["subject"], "FX-4")
        self.assertNotIn("FX-4", [u["subject"] for u in plan["approved"]["snapshot"]["units"]])
        code, out = fx.flow("lead-plan", "why", "c1", "--why", "A lane found the gap.", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.plan(fx)["changes"][0]["why"], "A lane found the gap.")
        code, out = fx.flow("lead-plan", "why", "c9", "--why", "x", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("no recorded change c9", out)

    def test_a_hand_edit_to_a_criterion_appears_as_unrecorded(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        d = json.loads(path.read_text())
        d["stories"]["FX-3"]["criteria"][0]["text"] = "Thing two renders fast"
        d["stories"]["FX-3"]["criteria"].append({"id": "two-scrolls", "text": "Thing two scrolls"})
        d["finish"][0]["covers"]["proofs"] = ["review"]
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: edited by hand")
        code, out = fx.flow("lead-reconcile", cwd=m)
        self.assertEqual(code, 0, out)
        changes = [{k: c.get(k) for k in ("kind", "subjects", "criteria", "by", "before", "after")} for c in self.plan(fx)["changes"]]
        self.assertEqual(changes, [
            {"kind": "changed", "subjects": ["FX-3"], "criteria": ["two-renders"], "by": "unrecorded",
             "before": "Thing two renders", "after": "Thing two renders fast"},
            {"kind": "added", "subjects": ["FX-3"], "criteria": ["two-scrolls"], "by": "unrecorded", "before": None,
             "after": "Thing two scrolls"},
            {"kind": "finish", "subjects": [], "criteria": None, "by": "unrecorded",
             "before": {"criteria": ["one-loads", "one-fails", "two-renders"], "proofs": ["merged"]},
             "after": {"criteria": ["one-loads", "one-fails", "two-renders"], "proofs": ["review"]}}])
        self.assertTrue(all("why" not in c for c in self.plan(fx)["changes"]))
        # Compared with the last baseline, never with plan.json: the next sweep finds nothing new.
        before = self.written(fx)
        fx.flow("lead-reconcile", cwd=m)
        self.assertEqual(self.written(fx), before)
        (fx.tmp / "record/plan.json").unlink()
        fx.flow("lead-reconcile", cwd=m)
        self.assertEqual(len(self.plan(fx)["changes"]), 3, "a lost plan.json was read back as a source")
        # A criterion deleted by hand is a change too, with only what it said before.
        d["stories"]["FX-3"]["criteria"].pop()
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a criterion deleted by hand")
        fx.flow("lead-reconcile", cwd=m)
        last = self.plan(fx)["changes"][-1]
        self.assertEqual({k: last.get(k) for k in ("kind", "criteria", "by", "before", "after")},
                         {"kind": "changed", "criteria": ["two-scrolls"], "by": "unrecorded", "before": "Thing two scrolls", "after": None})

    def test_a_lanes_check_changes_the_plan_only_through_the_next_lead_rebuild(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        fx.flow("lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane 1", cwd=m)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        head = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        code, out = fx.flow("gate", "FX-2", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.plan(fx)["evidence"]["FX-2"]["in-app-hold"], {"state": "todo", "items": ["needs-hold"]})
        before = self.written(fx)
        code, out = fx.flow("app-check", "FX-2", "hold", "--mode", "hold", "--path", "/one", "--hold", "/api/one$",
                            "--eval", "true", "--after-eval", "true", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.written(fx), before, "the lane's check rewrote the plan")
        code, out = fx.flow("lead-reconcile", cwd=m)
        self.assertEqual(code, 0, out)
        evidence = self.plan(fx)["evidence"]["FX-2"]
        self.assertEqual({k: v["state"] for k, v in evidence.items()}, {"in-app-hold": "done", "in-app-fail": "todo", "in-app-any": "done"})
        self.assertEqual(evidence["in-app-hold"]["head"], head)
        update = [e["summary"] for e in self.record(fx)["journal"] if e["summary"].startswith("Plan updated:")][-1]
        self.assertIn("changed evidence", update)

    def test_evidence_is_waived_only_for_the_items_the_lead_accepted_as_blocked(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        head = "a" * 40
        log = fx.tmp / "flow-log"
        log.mkdir(exist_ok=True)
        run = lambda mode, holds=(), fails=(): json.dumps({"name": mode, "mode": mode, "pass": True, "head": head,
                                                           "specValue": {"holds": list(holds), "fails": list(fails)},
                                                           "passthrough": [], "at": "2026-01-01T00:00:00Z"})
        (log / "FX-2.app-checks.jsonl").write_text(run("hold", holds=["/api/one$"]) + "\n")
        (log / "FX-2.acceptance.jsonl").write_text(json.dumps({"head": head, "blocked": {"needs-fail": "the error page is down"},
                                                               "at": "2026-01-01T00:01:00Z"}) + "\n")
        fx.flow("lead-reconcile", cwd=m)
        evidence = self.plan(fx)["evidence"]["FX-2"]
        self.assertEqual(evidence["in-app-fail"], {"state": "waived", "head": head, "at": "2026-01-01T00:01:00Z",
                                                   "items": ["needs-fail"], "reason": "needs-fail: the error page is down"})
        self.assertEqual(evidence["in-app-hold"]["state"], "done")
        # Accepted at another head than the checks ran at, and nothing comparable: nothing is done or waived.
        (log / "FX-2.acceptance.jsonl").write_text(json.dumps({"head": "b" * 40, "blocked": {}, "at": "2026-01-01T00:02:00Z"}) + "\n")
        fx.flow("lead-reconcile", cwd=m)
        self.assertEqual({k: v["state"] for k, v in self.plan(fx)["evidence"]["FX-2"].items()},
                         {"in-app-hold": "todo", "in-app-fail": "todo", "in-app-any": "todo"})

    def test_a_running_sweep_builds_from_the_delivery_file_as_it_stands(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        sweep = subprocess.Popen([sys.executable, "-B", fx.tool, "lead-reconcile", "--every", "1"], cwd=m, env=fx.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: sweep.poll() is None and sweep.kill())
        self.assertIn("plan: unchanged", sweep.stdout.readline())
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--text", "Eased", "--why", "Eased.", cwd=m)
        self.assertEqual(code, 0, out)
        # Two passes of the sweep that started before the change, then a stop.
        seen = [sweep.stdout.readline() for _ in range(2)]
        sweep.terminate()
        sweep.wait(timeout=60)
        plan = self.plan(fx)
        self.assertEqual([(c["id"], c["by"]) for c in plan["changes"]], [("c1", "lead")], seen)
        self.assertEqual(plan["units"][0]["criteria"][1]["text"], "Eased")

    def test_a_running_sweep_outlives_a_delivery_file_saved_mid_edit(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        sweep = subprocess.Popen([sys.executable, "-B", fx.tool, "lead-reconcile", "--every", "1"], cwd=m, env=fx.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: sweep.poll() is None and sweep.kill())
        self.assertIn("plan: unchanged", sweep.stdout.readline())
        whole = path.read_text()
        path.write_text(whole[: len(whole) // 2])
        broken = sweep.stdout.readline()
        path.write_text(whole)
        recovered = sweep.stdout.readline()
        running = sweep.poll() is None
        sweep.terminate()
        sweep.wait(timeout=60)
        self.assertIn("plan: not rebuilt:", broken)
        self.assertIn("plan: unchanged", recovered)
        self.assertTrue(running, "the sweep ended on a delivery file that isn't JSON")

    def test_a_plan_the_view_cant_read_is_refused_before_anything_is_kept(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        d = json.loads(path.read_text())
        d["stories"]["FX-3"]["criteria"][0]["id"] = "two renders"
        d["finish"][0]["covers"]["criteria"] = ["one-loads"]
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a bad criterion id")
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("units[1].criteria[0].id", out)
        self.assertEqual(self.written(fx), (None, None, None))
        d["stories"]["FX-3"]["criteria"][0]["id"] = "two-renders"
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: corrected")
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.plan(fx)["units"][1]["criteria"][0]["id"], "two-renders")
        # The late snapshot too: a sweep that first finds criteria the view can't read keeps no snapshot of them.
        fx2 = self.fixture()
        path2 = setup_delivery(fx2)
        fx2.flow("lead-init", cwd=fx2.main)
        d2 = json.loads(path2.read_text())
        d2["stories"]["FX-2"]["criteria"] = [{"id": "bad id", "text": "x"}]
        path2.write_text(json.dumps(d2, indent=2))
        commit(fx2.main, "FX-1: late criteria, badly named")
        code, out = fx2.flow("lead-reconcile", cwd=fx2.main)
        self.assertEqual(code, 0, out)
        self.assertIn("plan: not rebuilt", out)
        self.assertEqual(self.changes_log(fx2), [])
        d2["stories"]["FX-2"]["criteria"] = [{"id": "good-id", "text": "x"}]
        path2.write_text(json.dumps(d2, indent=2))
        commit(fx2.main, "FX-1: renamed")
        fx2.flow("lead-reconcile", cwd=fx2.main)
        self.assertEqual(self.plan(fx2)["approved"]["snapshot"]["units"][0]["criteria"], [{"id": "good-id", "text": "x"}])

    def test_a_story_added_before_any_registering_command_is_never_an_unrecorded_edit(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        d = json.loads(path.read_text())
        d["stories"]["FX-4"] = {"title": "Thing four", "lane": "lane-1", "criteria": [{"id": "four-works", "text": "Four works"}]}
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a story added")
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--text", "Eased", "--why", "Eased.", cwd=m)
        self.assertEqual(code, 0, out)
        fx.flow("lead-brief", cwd=m)
        self.assertEqual([(c["kind"], c["subjects"], c["by"]) for c in self.plan(fx)["changes"]],
                         [("relaxed", ["FX-2"], "lead"), ("added", ["FX-4"], "lead")])

    def test_a_clip_counts_only_through_the_next_lead_rebuild_and_only_at_a_current_head(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["evidence"] = "video"
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: FX-2 shows its change on video")
        fx.flow("lead-init", cwd=m)
        plan = self.plan(fx)
        self.assertIn({"id": "clip", "label": "Before/after clip", "source": "producer"}, plan["proofKinds"])
        self.assertEqual(plan["units"][0]["proofs"][-1], "clip")
        self.assertEqual(plan["evidence"]["FX-2"]["clip"], {"state": "todo"})
        head, log = "a" * 40, fx.tmp / "flow-log"
        entry = lambda name, at_head: json.dumps({"name": name, "head": at_head, "digest": "d", "at": "2026-01-01T00:00:00Z"})
        (log / "FX-2.app-checks.jsonl").write_text(json.dumps({"name": "hold", "mode": "hold", "pass": True, "head": head,
            "specValue": {"holds": ["/api/one$"], "fails": []}, "passthrough": [], "at": "2026-01-01T00:00:00Z"}) + "\n")
        # A clip of another head than the one judged, as app-video appends it: not done.
        (log / "FX-2.videos.jsonl").write_text(entry("stale", "b" * 40) + "\n")
        fx.flow("lead-reconcile", cwd=m)
        self.assertEqual(self.plan(fx)["evidence"]["FX-2"]["clip"], {"state": "todo"})
        before = self.written(fx)
        with open(log / "FX-2.videos.jsonl", "a") as videos:
            videos.write(entry("loading", head) + "\n")
        self.assertEqual(self.written(fx), before, "a clip changed the plan before a lead-side rebuild")
        fx.flow("lead-reconcile", cwd=m)
        clip = self.plan(fx)["evidence"]["FX-2"]["clip"]
        self.assertEqual((clip["state"], clip["head"], clip["items"]), ("done", head, ["loading"]))

    def test_a_requirement_naming_adapter_arguments_matches_the_logged_ones(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["inApp"] = [{"mode": "loaded", "args": ["--fixture-user", "reader"]}]
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: FX-2 loads as a reader")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        fx.flow("lead-init", cwd=m)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        fx.flow("gate", "FX-2", cwd=wt)
        code, out = fx.flow("app-check", "FX-2", "reader", "--mode", "loaded", "--path", "/one", "--eval", "true",
                            "--", "--fixture-user", "reader", cwd=wt)
        self.assertEqual(code, 0, out)
        fx.flow("lead-reconcile", cwd=m)
        self.assertEqual(self.plan(fx)["evidence"]["FX-2"]["in-app-loaded"]["state"], "done")
        # A run logged without its adapter arguments never meets a requirement that names them, even none.
        d["stories"]["FX-2"]["inApp"] = [{"mode": "loaded", "args": []}]
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: FX-2 loads with no arguments")
        (fx.tmp / "flow-log/FX-2.app-checks.jsonl").write_text(json.dumps({"name": "old", "mode": "loaded", "pass": True,
            "head": "a" * 40, "specValue": {"holds": [], "fails": []}, "at": "2026-01-01T00:00:00Z"}) + "\n")
        fx.flow("lead-reconcile", cwd=m)
        self.assertEqual(self.plan(fx)["evidence"]["FX-2"]["in-app-loaded"]["state"], "todo")

    def test_a_check_stays_current_through_commits_that_change_no_shipped_file(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        checked = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        (wt / "tests").mkdir()
        (wt / "tests/one.txt").write_text("a test\n")
        commit(wt, "FX-2: a test")
        tested = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        (wt / "src/one.txt").write_text("one, changed\n")
        commit(wt, "FX-2: shipped code changed")
        shipped = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        log = fx.tmp / "flow-log"
        (log / "FX-2.app-checks.jsonl").write_text(json.dumps({"name": "hold", "mode": "hold", "pass": True, "head": checked,
            "specValue": {"holds": ["/api/one$"], "fails": []}, "passthrough": [], "at": "2026-01-01T00:00:00Z"}) + "\n")
        for accepted, state in ((tested, "done"), (shipped, "todo")):
            (log / "FX-2.acceptance.jsonl").write_text(json.dumps({"head": accepted, "blocked": {}, "at": "2026-01-01T00:01:00Z"}) + "\n")
            fx.flow("lead-reconcile", cwd=m)
            self.assertEqual((state, self.plan(fx)["evidence"]["FX-2"]["in-app-hold"]["state"]), (state, state))

    def test_lead_plan_change_adds_criteria_and_refuses_what_it_cant_keep(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        code, out = fx.flow("lead-plan", "change", "--kind", "added", "--subjects", "FX-3", "--criterion", "two-scrolls",
                            "--text", "Thing two scrolls", "--why", "Found in review.", cwd=m)
        self.assertEqual(code, 0, out)
        committed = json.loads(sh("git", "show", "HEAD:.agents/deliveries/FX-1.json", cwd=m).stdout)
        self.assertEqual(committed["stories"]["FX-3"]["criteria"][-1], {"id": "two-scrolls", "text": "Thing two scrolls"})
        fx.flow("lead-reconcile", cwd=m)
        self.assertEqual([(c["kind"], c["criteria"], c["by"], c["after"]) for c in self.plan(fx)["changes"]],
                         [("added", ["two-scrolls"], "lead", "Thing two scrolls")])
        before = (path.read_bytes(), self.written(fx), sh("git", "rev-parse", "HEAD", cwd=m).stdout)
        refusals = [
            (["--kind", "added", "--subjects", "FX-2", "--criterion", "two-scrolls", "--text", "again"], "already exists"),
            (["--kind", "finish", "--finish", "acs", "--covers", json.dumps({"criteria": ["no-such"], "proofs": []})],
             "names no criterion of the plan's units"),
        ]
        # A story already in the queue can't be removed from the plan.
        doc = self.record(fx)
        queued = {"order": 1, "subject": "FX-3", "title": "Thing two", "lane": "lane-2", "stage": "queued", "head": "c" * 40,
                  "syncedHead": None, "leadInputs": [], "reportedRounds": 1, "runId": None, "checks": []}
        sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir", fx.tmp / "record",
           "patch", "--writer", "lead", "--version", doc["state"]["version"], "--patch", json.dumps({"queue": [queued], "planned": [e for e in doc["state"]["planned"] if e["subject"] != "FX-3"]}))
        before = (path.read_bytes(), self.written(fx), sh("git", "rev-parse", "HEAD", cwd=m).stdout)
        refusals.append((["--kind", "removed", "--subjects", "FX-3"], "already in the queue"))
        for argv, message in refusals:
            code, out = fx.flow("lead-plan", "change", *argv, "--why", "w", cwd=m)
            self.assertEqual((code, message in out), (2, True), out)
            self.assertEqual((path.read_bytes(), self.written(fx), sh("git", "rev-parse", "HEAD", cwd=m).stdout), before, argv)
            self.assertEqual(sh("git", "status", "--porcelain", cwd=m).stdout, "", argv)

    def test_lead_assign_rebuilds_for_a_story_it_registers(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        d = json.loads(path.read_text())
        d["stories"]["FX-4"] = {"title": "Thing four", "lane": "lane-1", "criteria": [{"id": "four-works", "text": "Four works"}]}
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a story added")
        code, out = fx.flow("lead-assign", "FX-4", "--lane", "lane-1", "--title", "Lane 1", cwd=m)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        self.assertEqual([(c["kind"], c["subjects"]) for c in plan["changes"]], [("added", ["FX-4"])])
        self.assertEqual(plan["units"][-1]["subject"], "FX-4")

    def test_concurrent_rebuilds_lose_no_update(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        d = json.loads(path.read_text())
        for n in range(4, 10):
            d["stories"][f"FX-{n}"] = {"title": f"Thing {n}", "lane": "lane-1", "criteria": [{"id": f"c-{n}", "text": f"{n} works"}]}
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: six stories added")
        fx.flow("lead-brief", cwd=m)
        ids = [c["id"] for c in self.plan(fx)["changes"]]
        self.assertEqual(len(ids), 6)
        processes = [subprocess.Popen([sys.executable, "-B", fx.tool, "lead-plan", "why", ident, "--why", f"reason {ident}"],
                                      cwd=m, env=fx.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                     for ident in ids]
        for process in processes:
            out, _ = process.communicate(timeout=300)
            self.assertEqual(process.returncode, 0, out)
        self.assertEqual({c["id"]: c.get("why") for c in self.plan(fx)["changes"]}, {ident: f"reason {ident}" for ident in ids})

def doctor_binding() -> dict:
    """A complete orchestrate-work binding for the fixture: its gates are the fallback checks."""
    gate = lambda name, argv: {"id": name, "argv": argv, "cwd": ".", "reasons": ["every candidate"], "prerequisites": []}
    return {
        "schemaVersion": "orchestration-binding/1", "base": {"remote": "origin", "ref": "feature/FX-1"},
        "workRoot": ".orchestration/work", "candidate": ["true", "{candidate}"], "prepare": [], "hooks": "bypass",
        "regenerable": {"paths": [], "regenerators": []}, "postSync": [], "leadInputs": [], "profiles": {},
        "provider": None, "alwaysRun": [],
        "fallback": [gate("lint", ["sh", "-c", "test -f src/app.txt"]), gate("test", ["true"])],
        "checkTimeoutSeconds": 600, "checkTimeoutCeiling": 1200, "environment": {},
        "ready": ["true", "{candidate}", "{expectedHead}"], "merge": ["true", "{branch}", "{expectedHead}"],
        "release": [], "fullHealth": ["true"], "leases": [],
        "state": {"directory": ".orchestration/state", "publishers": [], "renderers": [],
                  "trackerSnapshot": {"argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
                                               "--kind", "jira", "--site", "https://tracker.test", "--parent", "{parent}",
                                               "--user-env", "FX_TRACKER_USER", "--token-env", "FX_TRACKER_TOKEN"],
                                      "timeoutSeconds": 30}},
    }


def doctor_lines(out: str) -> dict[str, tuple[str, str]]:
    """Each check's line, by its label: (status, the rest)."""
    found = {}
    for line in out.splitlines():
        match = re.match(r"^(ok|missing|wrong|skipped) +([^:]+): (.*)$", line)
        if match:
            found[match.group(2)] = (match.group(1), match.group(3))
    return found


def failing(out: str) -> dict[str, str]:
    return {label: rest for label, (status, rest) in doctor_lines(out).items() if status not in ("ok", "skipped")}


# The words each failing doctor line starts its detail with, as the adoption guide's troubleshooting section keys them.
TROUBLESHOOTING_KEYS = [
    "this host lacks", "no .agents/run-delivery.json", "the repository's configs disagree", "earlier names in use",
    "no receipt at", "is neither the current nor the previous generation", "is the receipt's previous one",
    "no .agents/orchestration.json", "isn't JSON", "which RUN_DELIVERY_CONFIG names", "doesn't validate", "no delivery base", "the delivery base",
    "has no .agent-skills/current", "isn't a link inside the repository", "doesn't carry", "carries generation",
    "no gates are declared", "these don't resolve", "isn't ignored by git", "no tracker issue URL",
    "review.pr is configured, but", "gh isn't installed", "gh isn't signed in",
    "no token variable for the Bitbucket adapter", "the Bitbucket adapter reads its token from it", "forge find failed",
    "the tracker snapshot reads", "these in-app adapters don't resolve", "isn't writable",
    "the in-app adapters break the contract", "lease held by", "video evidence needs both", "which video evidence uses",
    "needs Node 22.6 or newer", "no runtime loader", "harness.config.ts not read", "is missing (lead-setup writes it)",
    "stories without a lane", "lanes own the same files", "fails on", "isn't a valid delivery-plan/1",
]


def troubleshooting_section() -> str:
    text = (ROOT / "skills/run-delivery/references/adopting.md").read_text(encoding="utf-8")
    return text[text.index("## Troubleshooting"):]


DOCTOR_LABELS = ["host and tools", "orchestration binding", "configuration", "release generation",
                 "release on the delivery base", "gates", "lane checkouts ignored", "tracker issue URL",
                 "pipeline PR review", "gh", "forge find", "forge write access", "tracker snapshot token",
                 "in-app adapters", "in-app adapter contract", "video evidence tools", "harness configuration",
                 "delivery file", "plan chapter", "gates on the base"]


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryDoctorTests(unittest.TestCase):
    """The setup check: one line per check, a fix on every line that fails, and one failing line per missing
    prerequisite."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)
        cls.node22 = bool(find_node22(cls.tool))
        cls.ffmpeg = all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe"))
        cls.bins = Path(tempfile.mkdtemp(prefix="run-delivery-bins-"))
        cls.addClassCleanup(shutil.rmtree, cls.bins, True)

    def without(self, *names: str) -> str:
        """A PATH with every program the current one has, except these."""
        folder = self.bins / "-".join(names)
        if not folder.exists():
            folder.mkdir()
            for directory in os.environ.get("PATH", "").split(os.pathsep):
                for program in (Path(directory).iterdir() if Path(directory).is_dir() else []):
                    target = folder / program.name
                    if program.name in names or target.exists() or not os.access(program, os.X_OK):
                        continue
                    target.symlink_to(program)
        return str(folder)

    def fixture(self) -> Fixture:
        """A repository set up the way the setup check wants: the release installed and committed on the delivery
        base, a binding, a configuration and a delivery."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        m = fx.main
        sh("git", "rm", "-r", "-q", ".agent-skills", cwd=m)
        commit(m, "FX-1: drop the linked release")
        with mock.patch.dict(os.environ, {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""}):
            Lifecycle(m).install(self.archive, self.metadata, maintainer=True)
        fx.tool = m / STABLE_TOOL_PATH
        fx.release = fx.tool.resolve().parents[3]
        (m / "tools/fake_contract_check.py").write_text(FAKE_CONTRACT_CHECK)
        (m / ".gitignore").write_text(".orchestration/\nout/\nlanes/\n")
        (m / ".agents/orchestration.json").write_text(json.dumps(doctor_binding(), indent=2))
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        del conf["gates"]
        conf["inApp"]["check"]["argv"] = [sys.executable, "-B", "tools/fake_contract_check.py", "{spec}", "{port}", "{args}"]
        (m / ".agents/run-delivery.json").write_text(json.dumps(conf, indent=2))
        if self.node22:
            (m / "harness.config.ts").write_text(
                "export default { baseRef: 'origin/feature/FX-1', preparationCommands: ["
                "{ id: 'lint', command: ['sh', '-c', 'test -f src/app.txt'] }, { id: 'test', command: ['true'] }] };\n")
        commit(m, "FX-1: install the release and bind the repository")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        path = setup_delivery(fx)
        if self.ffmpeg:
            d = json.loads(path.read_text())
            d["stories"]["FX-2"]["evidence"] = "video"
            path.write_text(json.dumps(d, indent=2))
            commit(m, "FX-1: video evidence")
            sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        fx.env.update({"FX_TRACKER_USER": "lead@example.test", "FX_TRACKER_TOKEN": "tracker-sentinel"})
        return fx

    def doctor(self, fx: Fixture, *args, env: dict | None = None, expect_code: int | None = None) -> str:
        code, out = fx.flow("doctor", "--delivery", "FX-1", *args, cwd=fx.main, env=env)
        if expect_code is not None:
            expect(code == expect_code, f"doctor exited {code}, not {expect_code}:\n{out}")
        return out

    def test_the_plan_row_checks_the_plan_a_delivery_with_criteria_makes(self) -> None:
        fx = self.fixture()
        path = fx.main / ".agents/deliveries/FX-1.json"
        lines = doctor_lines(self.doctor(fx, "--offline"))
        self.assertEqual(lines["plan chapter"], ("skipped", "(no criteria declared; the plan chapter is off)"))
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"] = [{"id": "one-loads", "text": "Thing one loads"},
                                            {"id": "one-fails", "text": "Thing one says when it fails"}]
        d["stories"]["FX-3"]["criteria"] = [{"id": "two-renders", "text": "Thing two renders"}]
        d["finish"] = [{"id": "acs", "text": "AC1-AC2 met", "covers": {"criteria": ["one-loads"], "proofs": ["merged"]}}]
        path.write_text(json.dumps(d, indent=2))
        out = self.doctor(fx, "--offline")
        self.assertEqual(doctor_lines(out)["plan chapter"],
                         ("ok", "3 criteria across 2 stories; the plan they make passes delivery-views check-plan"), out)
        self.assertEqual(failing(out), {}, out)
        d["finish"][0]["covers"]["criteria"].append("no-such-criterion")
        path.write_text(json.dumps(d, indent=2))
        out = self.doctor(fx, "--offline")
        self.assertEqual(list(failing(out)), ["plan chapter"], out)
        self.assertIn("finish[0].covers.criteria[1]: names no criterion of the plan's units", failing(out)["plan chapter"])

    def test_a_clean_repository_passes_every_check(self) -> None:
        fx = self.fixture()
        out = self.doctor(fx, "--run-adapters", "--run-gates", expect_code=0)
        lines = doctor_lines(out)
        self.assertEqual(list(lines), DOCTOR_LABELS, out)
        expected_skips = {"pipeline PR review", "gh", "forge write access", "plan chapter"}
        expected_skips |= set() if self.ffmpeg else {"video evidence tools"}
        expected_skips |= set() if self.node22 else {"harness configuration"}
        self.assertEqual({label for label, (status, _) in lines.items() if status == "skipped"}, expected_skips, out)
        self.assertEqual(failing(out), {}, out)
        self.assertIn("not verified", lines["forge write access"][1])
        self.assertEqual(len(out.splitlines()), len(DOCTOR_LABELS), "doctor prints one line per check")

    def test_each_missing_prerequisite_yields_one_failing_line_with_its_fix(self) -> None:
        fx = self.fixture()
        m = fx.main
        conf_path, binding_path = m / ".agents/run-delivery.json", m / ".agents/orchestration.json"
        delivery_path = m / ".agents/deliveries/FX-1.json"
        base = sh("git", "rev-parse", "origin/feature/FX-1", cwd=m).stdout.strip()

        def edit_json(path: Path, change):
            original = path.read_text()
            document = json.loads(original)
            change(document)
            path.write_text(json.dumps(document))
            return lambda: path.write_text(original)

        def both(*undos):
            return lambda: [undo() for undo in reversed(undos)]

        def rewrite(path: Path, text: str):
            original = path.read_text()
            path.write_text(text)
            return lambda: path.write_text(original)

        def move(path: Path, to: Path | None = None):
            target = to or path.with_name(path.name + ".away")
            path.rename(target)
            return lambda: target.rename(path)

        def base_change(change):
            """Push a base commit the change makes; undoing puts the base back."""
            scratch = Path(tempfile.mkdtemp(prefix="base-", dir=fx.tmp)) / "checkout"
            sh("git", "worktree", "add", "-q", "--detach", scratch, base, cwd=m)
            try:
                change(scratch)
                sh("git", "add", "-A", cwd=scratch)
                sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change the base", cwd=scratch)
                sh("git", "push", "-q", "origin", "HEAD:feature/FX-1", cwd=scratch)
            finally:
                sh("git", "worktree", "remove", "--force", scratch, cwd=m)
            return lambda: sh("git", "push", "-q", "-f", "origin", f"{base}:feature/FX-1", cwd=m)

        def base_without_release():
            return base_change(lambda scratch: sh("git", "rm", "-r", "-q", ".agent-skills", cwd=scratch))

        def base_with_another_generation(scratch: Path):
            current = scratch / ".agent-skills/current"
            other = current.parent / "generations" / ("f" * 64)
            shutil.copytree(current.resolve(), other, symlinks=True)
            current.unlink()
            current.symlink_to(Path("generations") / other.name)

        def base_with_a_plain_current(scratch: Path):
            current = scratch / ".agent-skills/current"
            current.unlink()
            current.write_text("not a link\n")

        def fake_ffmpeg():
            folder = fx.tmp / "ffmpeg-bin"
            folder.mkdir(exist_ok=True)
            listed = "\n".join(f" ... {name:<12} V->V       fixture" for name in
                               ("lutyuv", "cropdetect", "crop", "scale", "tpad", "pad", "drawtext"))
            (folder / "ffmpeg").write_text(f"#!{sys.executable}\nimport sys\n"
                                           f"print({listed!r} if '-filters' in sys.argv else ' V..... libx264  H.264')\n")
            (folder / "ffmpeg").chmod(0o755)
            return f"{folder}{os.pathsep}{os.environ.get('PATH', '')}"

        def receipt(change):
            path = m / ".agent-skills/active.json"
            original = path.read_bytes()
            document = json.loads(original)
            change(document)
            path.write_text(json.dumps(document))
            return lambda: path.write_bytes(original)

        def env(**changes):
            return lambda: None, {k: v for k, v in {**fx.env, **changes}.items() if v is not None}

        def chmod_locked():
            locked = fx.tmp / "locked"
            locked.mkdir(exist_ok=True)
            locked.chmod(0o555)
            undo = edit_json(conf_path, lambda c: c["inApp"].update(lock=str(locked / "deep" / "app.lock")))
            return lambda: (undo(), locked.chmod(0o755))

        (m / "tools/broken_contract_check.py").write_text(FAKE_CONTRACT_CHECK.replace(
            "print(json.dumps(report))", "report.pop('alertsSeen')\nprint(json.dumps(report))"))
        github = lambda c: c.update(forge={"adapter": "github", "repository": "o/n"})
        fake_gh = fx.tmp / "gh-bin"
        fake_gh.mkdir()
        (fake_gh / "gh").write_text(FAKE_GH_SIGNED_IN.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
        (fake_gh / "gh").chmod(0o755)
        with_gh = f"{fake_gh}{os.pathsep}{os.environ.get('PATH', '')}"
        cases = [
            # (label that fails, its status, a phrase its fix names, how to remove the prerequisite)
            ("host and tools", "missing", "install ps", lambda: env(PATH=self.without("ps"))),
            ("host and tools", "missing", "install lsof", lambda: env(PATH=self.without("lsof"))),
            ("configuration", "missing", "write it", lambda: (move(conf_path), None)),
            ("configuration", "wrong", "rename them",
             lambda: (move(conf_path, m / ".agents/flow.json"), None)),
            ("configuration", "wrong", "rename them", lambda: env(DELIVERY_FLOW_LOCAL="0")),
            ("configuration", "wrong", "correct", lambda: (edit_json(conf_path, lambda c: c.update(
                gates=[{"id": "lint", "argv": ["false"]}])), None)),
            ("release generation", "wrong", "run the tool as", lambda: (receipt(lambda r: r.update(
                generation="0" * 64, previous={"files": [], "generation": r["generation"], "release": r["release"]})), None)),
            ("release generation", "wrong", "install the release", lambda: (move(m / ".agent-skills/active.json"), None)),
            ("orchestration binding", "missing", "write the orchestrate-work binding", lambda: (move(binding_path), None)),
            ("orchestration binding", "wrong", "correct it", lambda: (edit_json(binding_path, lambda b: b.pop("merge")), None)),
            ("orchestration binding", "wrong", "correct it", lambda: (rewrite(binding_path, "{not json"), None)),
            ("configuration", "wrong", "correct it", lambda: (rewrite(conf_path, "{not json"), None)),
            ("configuration", "missing", "point RUN_DELIVERY_CONFIG", lambda: env(RUN_DELIVERY_CONFIG=str(fx.tmp / "nope.json"))),
            ("release generation", "wrong", "reinstall the release", lambda: (rewrite(m / ".agent-skills/active.json", "{not json"), None)),
            ("release generation", "wrong", "reinstall the release", lambda: (rewrite(m / ".agent-skills/active.json", "[1]"), None)),
            ("release generation", "wrong", "run the tool as", lambda: (receipt(lambda r: r.update(
                generation="0" * 64, previous="not an entry")), None)),
            ("delivery file", "wrong", "correct it", lambda: (rewrite(delivery_path, "{not json"), None)),
            ("release on the delivery base", "wrong", "commit the installed release", lambda: (base_without_release(), None)),
            ("release on the delivery base", "wrong", "commit the installed release",
             lambda: (base_change(base_with_another_generation), None), (), "carries generation"),
            ("release on the delivery base", "wrong", "commit the installed release",
             lambda: (base_change(base_with_a_plain_current), None), (), "isn't a link inside the repository"),
            ("gates", "wrong", "declare the binding's fallback checks", lambda: (edit_json(binding_path, lambda b: b.update(
                fallback=[])), None)),
            ("gates on the base", "wrong", "repair the base", lambda: (base_change(
                lambda scratch: (scratch / "src/app.txt").unlink()), None), ("--run-gates",)),
            ("in-app adapter contract", "wrong", "see the adoption guide", lambda: (edit_json(conf_path, lambda c: c["inApp"][
                "check"].update(argv=[sys.executable, "-B", "tools/broken_contract_check.py", "{spec}", "{port}", "{args}"])),
                None), ("--run-adapters",)),
            ("gates", "wrong", "install them", lambda: (both(
                edit_json(binding_path, lambda b: b["fallback"][1].update(argv=["no-such-gate-program"])),
                rewrite(m / "harness.config.ts", (m / "harness.config.ts").read_text().replace(
                    "command: ['true']", "command: ['no-such-gate-program']")) if self.node22 else (lambda: None)), None)),
            ("gates", "wrong", "install them", lambda: (edit_json(binding_path, lambda b: b.update(
                prepare=[["no-such-prepare-program"]])), None)),
            ("lane checkouts ignored", "wrong", "to .gitignore", lambda: (edit_json(conf_path, lambda c: c.update(
                laneCheckout="unignored/{key}")), None)),
            ("tracker issue URL", "missing", "tracker.issueUrl", lambda: (edit_json(conf_path, lambda c: c.pop("tracker")), None)),
            ("pipeline PR review", "wrong", "remove review.pr", lambda: (edit_json(conf_path, lambda c: (
                github(c), c["review"].update(pr={"author": "Pipeline Bot"}))), {**fx.env, "PATH": with_gh})),
            ("gh", "missing", "install gh", lambda: (edit_json(conf_path, github), {**fx.env, "PATH": self.without("gh")})),
            ("forge find", "wrong", "", lambda: (edit_json(conf_path, lambda c: c.update(forge={"argv": [
                sys.executable, "-c", "import sys; sys.exit('the forge is unreachable')"]})), None)),
            ("forge find", "missing", "export FX_FORGE_TOKEN", lambda: (edit_json(conf_path, lambda c: c.update(
                forge={"adapter": "bitbucket", "repository": "ws/repo", "tokenEnv": "FX_FORGE_TOKEN"})), None)),
            ("tracker snapshot token", "missing", "export FX_TRACKER_TOKEN", lambda: env(FX_TRACKER_TOKEN=None)),
            ("in-app adapters", "wrong", "install them", lambda: (edit_json(conf_path, lambda c: c["inApp"]["check"].update(
                argv=["no-such-check-program"])), None)),
            ("in-app adapters", "wrong", "make it writable", lambda: (chmod_locked(), None)),
            ("delivery file", "wrong", "give each a lane", lambda: (edit_json(delivery_path, lambda d: d["stories"]["FX-3"].update(
                lane="lane-9")), None)),
            ("delivery file", "wrong", "disjoint", lambda: (edit_json(delivery_path, lambda d: d["lanes"]["lane-2"].update(
                owns=["src/*"])), None)),
            ("delivery file", "missing", "", lambda: (move(delivery_path), None)),
        ]
        if self.ffmpeg:
            cases.append(("video evidence tools", "missing", "install ffmpeg", lambda: env(PATH=self.without("ffmpeg", "ffprobe"))))
            cases.append(("video evidence tools", "wrong", "install an ffmpeg build", lambda: env(PATH=fake_ffmpeg())))
        if self.node22:
            cases.append(("harness configuration", "missing", "install Node 22.6",
                          lambda: env(PATH=self.without("node"), HOME=str(fx.tmp))))
            cases.append(("harness configuration", "wrong", "correct harness.config.ts",
                          lambda: (rewrite(m / "harness.config.ts", "export default {{{ broken\n"), None)))
        for label, status, fix, remove, *flags in cases:
            with self.subTest(failing=label, status=status, fix=fix):
                undo, environment = remove()
                try:
                    out = self.doctor(fx, *(flags[0] if flags else ()), env=environment, expect_code=1)
                finally:
                    undo()
                self.assertEqual(list(failing(out)), [label], out)
                self.assertEqual(doctor_lines(out)[label][0], status, out)
                self.assertIn("fix: ", doctor_lines(out)[label][1], out)
                self.assertIn(fix, doctor_lines(out)[label][1].split("fix: ", 1)[1], out)
                if len(flags) > 1:
                    self.assertIn(flags[1], doctor_lines(out)[label][1].split("; fix: ")[0], out)
                keys = [key for key in TROUBLESHOOTING_KEYS if key in doctor_lines(out)[label][1].split("; fix: ")[0]]
                self.assertTrue(keys, f"no troubleshooting entry is keyed by: {doctor_lines(out)[label][1]}")
        # Every prerequisite was put back.
        self.assertEqual(failing(self.doctor(fx, "--run-gates", "--run-adapters", expect_code=0)), {})

    def test_the_rows_with_two_good_answers_accept_both(self) -> None:
        fx = self.fixture()
        m = fx.main
        conf_path, binding_path = m / ".agents/run-delivery.json", m / ".agents/orchestration.json"
        conf = json.loads(conf_path.read_text())
        # review.pr with an adapter that answers `reviews` (the fixture's own argv forge).
        conf_path.write_text(json.dumps({**conf, "review": {**conf["review"], "pr": {"author": "Pipeline Bot"}}}))
        lines = doctor_lines(self.doctor(fx, expect_code=0))
        self.assertEqual(lines["pipeline PR review"], ("ok", "the forge adapter answers `reviews`"))
        # A repository with no gates says so, and that is fine.
        binding = json.loads(binding_path.read_text())
        binding_path.write_text(json.dumps({**binding, "fallback": []}))
        conf_path.write_text(json.dumps({**conf, "gates": "none"}))
        lines = doctor_lines(self.doctor(fx, "--run-gates", expect_code=0))
        self.assertEqual(lines["gates"][0], "ok")
        self.assertIn("none", lines["gates"][1])

    def test_the_github_adapter_needs_gh_signed_in(self) -> None:
        fx = self.fixture()
        conf = json.loads((fx.main / ".agents/run-delivery.json").read_text())
        (fx.main / ".agents/run-delivery.json").write_text(json.dumps({**conf, "forge": {"adapter": "github",
                                                                                         "repository": "o/n"}}))
        bin_dir = fx.tmp / "gh-bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH_SIGNED_IN.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
        (bin_dir / "gh").chmod(0o755)
        signed_in = {**fx.env, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        lines = doctor_lines(self.doctor(fx, env=signed_in, expect_code=0))
        self.assertEqual(lines["gh"][0], "ok")
        self.assertEqual(lines["forge find"][0], "ok")
        (bin_dir / "gh").write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")
        out = self.doctor(fx, env=signed_in, expect_code=1)
        self.assertEqual(failing(out), {"gh": "gh isn't signed in; fix: gh auth login"})

    def test_bitbucket_forge_rows_name_the_token_and_never_claim_write_access(self) -> None:
        fx = self.fixture()
        conf_path = fx.main / ".agents/run-delivery.json"
        conf = json.loads(conf_path.read_text())
        conf_path.write_text(json.dumps({**conf, "forge": {"adapter": "bitbucket", "repository": "ws/repo",
                                                           "tokenEnv": "FX_FORGE_TOKEN"}}))
        site = fx.tmp / "site"
        site.mkdir()
        (site / "sitecustomize.py").write_text(FAKE_HTTP)
        fake = {**fx.env, "PYTHONPATH": str(site), "FAKE_HTTP_LOG": str(fx.tmp / "requests.jsonl")}
        out = self.doctor(fx, env=fake, expect_code=1)
        self.assertEqual(failing(out), {"forge find": "FX_FORGE_TOKEN is unset, and the Bitbucket adapter reads its "
                                                      "token from it; fix: export FX_FORGE_TOKEN"})
        lines = doctor_lines(self.doctor(fx, "--offline", env=fake, expect_code=0))
        self.assertEqual(lines["forge find"], ("skipped", "(--offline)"))
        lines = doctor_lines(self.doctor(fx, env={**fake, "FX_FORGE_TOKEN": "bb-sentinel"}, expect_code=0))
        self.assertEqual(lines["forge find"][0], "ok")
        self.assertEqual(lines["forge write access"][0], "skipped")
        self.assertIn("not verified", lines["forge write access"][1])
        requests = [json.loads(line) for line in (fx.tmp / "requests.jsonl").read_text().splitlines()]
        self.assertEqual({r["method"] for r in requests}, {"GET"}, "doctor wrote to the forge")

    def test_offline_reads_local_refs_without_fetching(self) -> None:
        fx = self.fixture()
        tracking = lambda: sh("git", "rev-parse", "refs/remotes/origin/feature/FX-1", cwd=fx.main).stdout.strip()
        lane = fx.lane("FX-2")  # a checkout on another branch reads the base from the remote
        before = tracking()
        other = fx.tmp / "other"
        sh("git", "clone", "-q", "-b", "feature/FX-1", fx.origin, other)
        (other / "src/newer.txt").write_text("newer\n")
        commit(other, "FX-1: a newer base")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=other)
        self.doctor(fx, "--offline", expect_code=0)
        self.assertEqual(tracking(), before, "doctor --offline fetched")
        fx.flow("doctor", "--offline", cwd=lane)
        self.assertEqual(tracking(), before, "doctor --offline fetched from a lane's checkout")
        self.doctor(fx, expect_code=0)
        self.assertNotEqual(tracking(), before, "doctor without --offline didn't fetch the base")
        sh("git", "remote", "set-url", "origin", str(fx.tmp / "gone.git"), cwd=fx.main)
        out = self.doctor(fx, "--offline", expect_code=0)
        self.assertEqual(doctor_lines(out)["release on the delivery base"][0], "ok", out)
        self.assertEqual(doctor_lines(out)["forge find"], ("skipped", "(--offline)"))

    def test_a_busy_lease_skips_the_adapter_run_at_once(self) -> None:
        fx = self.fixture()
        lock = fx.tmp / "app.lock"
        holder = subprocess.Popen([sys.executable, "-c", "import fcntl,sys,time; f=open(sys.argv[1],'a+'); "
                                   "fcntl.flock(f, fcntl.LOCK_EX); f.write('lane-1 FX-2 hold pid 1\\n'); f.flush(); "
                                   "print('held', flush=True); time.sleep(60)", str(lock)],
                                  stdout=subprocess.PIPE, text=True)
        try:
            holder.stdout.readline()
            started = time.monotonic()
            out = self.doctor(fx, "--run-adapters", expect_code=0)
            self.assertLess(time.monotonic() - started, 30, "doctor waited for the lease")
            status, detail = doctor_lines(out)["in-app adapter contract"]
            self.assertEqual(status, "skipped", out)
            self.assertIn("lease held by lane-1 FX-2 hold pid 1", detail)
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()

    def test_by_default_doctor_leaves_nothing_behind(self) -> None:
        fx = self.fixture()
        m = fx.main
        status = lambda: sh("git", "status", "--porcelain", "--ignored", "--untracked-files=all", cwd=m).stdout
        processes = lambda: [line for line in sh("ps", "-axo", "command=").stdout.splitlines() if str(fx.tmp) in line]
        before = (status(), sorted(p.name for p in fx.tmp.iterdir()), processes())
        refs = sh("git", "for-each-ref", cwd=m).stdout
        self.doctor(fx, expect_code=0)
        self.assertEqual((status(), sorted(p.name for p in fx.tmp.iterdir()), processes()), before)
        self.assertFalse((fx.tmp / "app.lock").exists(), "doctor took the in-app lease without --run-adapters")
        self.assertEqual(sh("git", "for-each-ref", cwd=m).stdout, refs)
        # The gate run makes its own checkout and removes it.
        worktrees = sh("git", "worktree", "list", "--porcelain", cwd=m).stdout
        self.doctor(fx, "--run-gates", expect_code=0)
        self.assertEqual(sh("git", "worktree", "list", "--porcelain", cwd=m).stdout, worktrees)

    def test_a_fresh_repository_gets_one_missing_line_per_required_file(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="run-delivery-fresh-"))
        self.addCleanup(shutil.rmtree, root, True)
        sh("git", "init", "-q", "-b", "main", cwd=root)
        (root / "README.md").write_text("fresh\n")
        commit(root, "Initial")
        with mock.patch.dict(os.environ, {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""}):
            Lifecycle(root).install(self.archive, self.metadata, maintainer=True)
        done = subprocess.run([sys.executable, "-B", str(root / STABLE_TOOL_PATH), "doctor", "--offline"], cwd=root,
                              env=clean_environment(), text=True, capture_output=True)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(failing(done.stdout), {
            "configuration": f"no .agents/run-delivery.json; fix: write it (see the adoption guide, "
                             "references/adopting.md)",
            "orchestration binding": "no .agents/orchestration.json; fix: write the orchestrate-work binding (see the "
                                     "adoption guide, references/adopting.md)"}, done.stdout)
        self.assertEqual(doctor_lines(done.stdout)["release generation"][0], "ok", done.stdout)

    def test_every_message_has_its_troubleshooting_entry(self) -> None:
        section = troubleshooting_section()
        for key in TROUBLESHOOTING_KEYS:
            with self.subTest(key=key):
                self.assertIn(f"**`{key}`**", section)
        self.assertEqual(host_terms(section), [])

    def test_usage_errors_and_windows_exit_2(self) -> None:
        done = subprocess.run([sys.executable, "-B", str(self.tool), "doctor", "--no-such-flag"], text=True,
                              capture_output=True, env=clean_environment())
        self.assertEqual(done.returncode, 2, done.stderr)
        script = ("import os, runpy, sys; os.name = 'nt'; sys.argv = [sys.argv[1], 'doctor']; "
                  "runpy.run_path(sys.argv[0], run_name='__main__')")
        done = subprocess.run([sys.executable, "-B", "-c", script, str(self.tool)], text=True, capture_output=True,
                              env=clean_environment())
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("runs on POSIX hosts only", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_app_video_refuses_with_doctors_message_without_ffmpeg(self) -> None:
        fx = self.fixture()
        wt = fx.lane("FX-2")
        environment = {**fx.env, "PATH": self.without("ffmpeg", "ffprobe")}
        code, out = fx.flow("app-video", "FX-2", "clip", "--mode", "loaded", "--path", "/one", cwd=wt, env=environment)
        expect(code == 2, out)
        message = "ffmpeg and ffprobe are not installed, and video evidence needs both; fix: install ffmpeg (it brings ffprobe)"
        self.assertIn(message, out)
        if self.ffmpeg:
            out = self.doctor(fx, env=environment, expect_code=1)
            self.assertEqual(failing(out), {"video evidence tools": message})


GUIDE = ROOT / "skills/run-delivery/references/adopting.md"
# A marked example block: ```<language> example <kind> [<path>]
GUIDE_BLOCK = re.compile(r"^```(?P<lang>\w*) example (?P<kind>file|repo|lane|outline)(?: (?P<path>\S+))?\n(?P<body>.*?)^```$",
                         re.MULTILINE | re.DOTALL)
# The only changes the fixture adopter makes to the guide's blocks. The guide targets a real adopter; the suite
# reaches no forge, application or tracker. Everything else is used verbatim.
GUIDE_SUBSTITUTIONS = (
    "forge.argv -> the fake forge adapter, with the <forge-repository> placeholder filled",
    "inApp build, serve and check argv -> the fixture fakes",
    "the remote -> a local bare repository",
    "the issue URL host -> tracker.test",
    "<delivery-key> and <story-key> placeholders -> fixture keys",
    "doctor -> doctor --offline",
)
FIXTURE_KEYS = {"<delivery-key>": "FX-1", "<story-key>": "FX-2"}
# Section 8 of the guide: the operating rules, by their opening words.
OPERATING_RULES = [
    "Lanes are sessions of their own.", "Lanes never merge the base.", "Review evidence is captured at hand-off.",
    "Restart long-running commands after an update.", "Start the merge queue with default signals.",
    "The evidence page follows each marked story's merge.", "Sign-in values stay out of commands.",
]
GUIDE_SECTIONS = ["1. What the workflow does", "2. Prerequisites", "3. Install a release", "4. Configure the repository",
                  "5. Run the setup check", "6. A first delivery", "7. Host notes", "8. Operating rules",
                  "Troubleshooting"]


def guide_blocks() -> list[dict]:
    return [match.groupdict() for match in GUIDE_BLOCK.finditer(GUIDE.read_text(encoding="utf-8"))]


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryAdoptionGuideTests(unittest.TestCase):
    """The adoption guide's own blocks take an empty repository to a clean setup check and a closed delivery."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def substitute(self, block: dict, fakes: Path, used: set[str]) -> dict:
        """One block as the fixture adopter runs it: only the pinned substitutions."""
        body, path = block["body"], block["path"]
        for placeholder, key in FIXTURE_KEYS.items():
            if placeholder in body or (path and placeholder in path):
                used.add(GUIDE_SUBSTITUTIONS[4])
            body, path = body.replace(placeholder, key), path and path.replace(placeholder, key)
        if "https://tracker.example.com/" in body:
            used.add(GUIDE_SUBSTITUTIONS[3])
            body = body.replace("https://tracker.example.com/", "https://tracker.test/")
        if path == ".agents/run-delivery.json":
            conf = json.loads(body)
            # The guide names a shipped reference adapter; a set argv stands in for it here.
            self.assertTrue((self.generation / f"skills/run-delivery/adapters/forge_{conf['forge']['adapter']}.py").is_file(),
                            conf["forge"])
            self.assertEqual(conf["forge"]["repository"], "<forge-repository>")
            conf["forge"] = {**conf["forge"], "repository": "fixture/repository",
                             "argv": [sys.executable, "-B", str(fakes / "fake_forge.py")]}
            used.add(GUIDE_SUBSTITUTIONS[0])
            conf["inApp"]["build"]["argv"] = ["sh", "-c", "mkdir -p out && cp src/*.txt out/"]
            conf["inApp"]["serve"]["argv"] = [sys.executable, "-m", "http.server", "{port}", "--bind", "127.0.0.1",
                                              "--directory", "{dir}"]
            conf["inApp"]["check"]["argv"] = [sys.executable, "-B", str(fakes / "contract_check.py"), "{spec}", "{port}",
                                              "{args}"]
            used.add(GUIDE_SUBSTITUTIONS[1])
            body = json.dumps(conf, indent=2) + "\n"
        if block["kind"] in ("repo", "lane") and "run_delivery.py doctor" in body:
            body = re.sub(r"(run_delivery\.py doctor[^\n]*)", r"\1 --offline", body)
            used.add(GUIDE_SUBSTITUTIONS[5])
        if block["kind"] != "outline":
            leftover = re.findall(r"(?<!\?P)<[a-z][a-z-]*>|your-org|your-repo|example\.com", body + (path or ""))
            self.assertEqual(leftover, [], f"a block carries an adopter value outside a named placeholder: {block}")
        return {**block, "body": body, "path": path}

    def test_the_guide_takes_an_empty_repository_to_a_closed_delivery(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="run-delivery-adopter-"))
        self.addCleanup(shutil.rmtree, root, True)
        fakes, home, bin_dir = root / "fakes", root / "home", root / "bin"
        for folder in (fakes, home, bin_dir):
            folder.mkdir()
        (fakes / "fake_forge.py").write_text(FAKE_FORGE)
        (fakes / "contract_check.py").write_text(FAKE_CONTRACT_CHECK)
        (bin_dir / "python3").symlink_to(sys.executable)
        env = {**clean_environment(), "HOME": str(home), "FAKE_FORGE_STATE": str(root / "forge.json"),
               "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
               "GIT_AUTHOR_NAME": "Adopter", "GIT_AUTHOR_EMAIL": "adopter@example.invalid",
               "GIT_COMMITTER_NAME": "Adopter", "GIT_COMMITTER_EMAIL": "adopter@example.invalid"}
        # The remote is a local bare repository; the repository starts with a product and nothing else.
        origin, repo = root / "origin.git", root / "repository"
        sh("git", "init", "-q", "--bare", "-b", "main", origin)
        sh("git", "clone", "-q", origin, repo)
        (repo / "src").mkdir()
        (repo / "src/app.txt").write_text("app\n")
        (repo / "README.md").write_text("A product.\n")
        commit(repo, "A product")
        sh("git", "push", "-q", "origin", "main", cwd=repo)
        used = {GUIDE_SUBSTITUTIONS[2]}
        # Section 3's installer is an outline; the release under test is installed the same way it does.
        with mock.patch.dict(os.environ, {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""}):
            Lifecycle(repo).install(self.archive, self.metadata, maintainer=True)
        lane = repo / ".orchestration/lanes/FX-2"
        ran = []
        for raw in guide_blocks():
            block = self.substitute(raw, fakes, used)
            if block["kind"] == "outline":
                continue
            if block["kind"] == "file":
                target = repo / block["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(block["body"])
                ran.append(f"file {block['path']}")
                continue
            # What a person or a reviewer does between the blocks, never a command the guide gives.
            if "run_delivery.py gate FX-2" in block["body"]:
                (lane / "src/greeting.txt").write_text("Hello\n")
                commit(lane, "FX-2: Show the greeting")
            if "review-prepare FX-2" in block["body"]:
                # The first run seeds the review context from the plan and stops for the evidence.
                first = subprocess.run(["sh", "-ec", block["body"]], cwd=lane, env=env, text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
                self.assertNotEqual(first.returncode, 0, first.stdout)
                self.assertIn("fill in 'Evidence so far'", first.stdout)
                context = lane / ".orchestration/review/FX-2/context.md"
                seeded = context.read_text()
                self.assertIn("## Ticket contract and plan unit", seeded)
                self.assertIn("Show the greeting", seeded)
                todo = next(line for line in seeded.splitlines() if line.startswith(CONTEXT_TODO))
                context.write_text(seeded.replace(todo, "- the gates and the in-app check passed"))
            if "review-reduce FX-2" in block["body"]:
                review = lane / ".orchestration/review/FX-2"
                meta = json.loads((review / "round-1.meta.json").read_text())
                for lens in meta["lenses"]:
                    (review / f"result-r1-{lens.split('.', 1)[1]}.json").write_text(json.dumps({
                        "schemaVersion": "review-lens-result/1", "lensId": lens, "subjectRef": "FX-2",
                        "candidateRef": meta["candidate"], "round": 1, "outcome": "aligned", "findings": [],
                        "evidence": [f"tree {meta['tree']} reviewed"]}))
            cwd = lane if block["kind"] == "lane" else repo
            done = subprocess.run(["sh", "-ec", block["body"]], cwd=cwd, env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            self.assertEqual(done.returncode, 0, f"the guide's block failed:\n{block['body']}\n---\n{done.stdout[-3000:]}")
            self.assertNotIn("Traceback (most recent call last)", done.stdout)
            self.assertEqual(host_terms(done.stdout), [], done.stdout)
            ran.append(block["body"].strip().splitlines()[0])
            if "run_delivery.py doctor" in block["body"]:
                self.assertEqual(failing(done.stdout), {}, done.stdout)
        self.assertEqual(used, set(GUIDE_SUBSTITUTIONS), "a pinned substitution is no longer needed; unpin it")
        # The delivery the guide declares has the plan chapter on: its plan passes check-plan and carries the change.
        plan_path = repo / ".orchestration/state/plan.json"
        checked = sh(sys.executable, "-B", repo / ".agent-skills/current/skills/delivery-views/scripts/views_common.py",
                     "check-plan", plan_path, check=False)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        plan = json.loads(plan_path.read_text())
        self.assertEqual([(c["kind"], c["subjects"], c["before"], c["after"]) for c in plan["changes"]],
                         [("relaxed", ["FX-2"], "The front page shows the greeting", "The front page shows a greeting")])
        self.assertEqual(plan["units"][0]["criteria"], [{"id": "greeting-shown", "text": "The front page shows a greeting"}])
        self.assertTrue(any(ran_block.startswith("python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-plan change")
                            for ran_block in ran))
        # The story merged and the delivery closed, with nothing left running and nothing left behind.
        state = json.loads(sh(sys.executable, "-B", repo / ".agent-skills/current/skills/orchestrate-work/scripts/"
                              "delivery_state.py", "--dir", repo / ".orchestration/state", "show").stdout)
        self.assertEqual([(q["subject"], q["stage"]) for q in state["state"]["queue"]], [("FX-2", "merged")])
        kinds = [e["kind"] for e in state["journal"]]
        self.assertIn("review.evidence", kinds)
        accepted = [e for e in state["journal"] if e["kind"] == "decision.recorded" and e.get("subject") == "FX-2"
                    and e["summary"].startswith("Hand-off checked as a claim")]
        self.assertEqual(len(accepted), 1, "the hand-off was never accepted")
        self.assertEqual(kinds[-1], "delivery.closed")
        self.assertFalse(lane.exists(), "the lane's checkout outlived its merge")
        sh("git", "fetch", "-q", "origin", cwd=repo)
        self.assertEqual(sh("git", "show", "origin/main:src/greeting.txt", cwd=repo).stdout, "Hello\n")
        self.assertTrue(list((home / ".local/share/run-delivery/archive/repository/FX-1").glob("*/state/journal.jsonl")))
        left = [line for line in sh("ps", "-axo", "command=").stdout.splitlines() if str(root) in line]
        self.assertEqual(left, [], "the delivery left processes running")
        # The installed generation is still exactly what its receipt says.
        lifecycle = Lifecycle(repo).status().as_dict()
        self.assertEqual((lifecycle["lifecycle"], lifecycle["blockers"]), ("current", []), lifecycle)
        # And its own renderer draws the plan chapter from the delivery's plan.json.
        views = root / "views.json"
        views.write_text(json.dumps({"spec": "delivery-views-config/1"}))
        document = sh(sys.executable, "-B", repo / ".agent-skills/current/skills/orchestrate-work/scripts/delivery_state.py",
                      "--dir", repo / ".orchestration/state", "show").stdout
        page = subprocess.run([sys.executable, "-B", str(repo / ".agent-skills/current/skills/delivery-views/scripts/render.py"),
                               "line-map", "--config", str(views), "--binding", ".agents/orchestration.json",
                               "--state-dir", ".orchestration/state"], cwd=repo, input=document, env=env, text=True,
                              capture_output=True, check=False)
        self.assertEqual(page.returncode, 0, page.stderr)
        embedded = re.search(r'<script type="application/json" id="delivery-plan">([^<]*)</script>', page.stdout)
        self.assertIsNotNone(embedded, "the installed renderer drew no plan chapter")
        self.assertEqual([c["kind"] for c in json.loads(embedded.group(1))["plan"]["changes"]], ["relaxed"])

    def test_every_block_is_marked_and_every_section_is_there(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        fences = re.findall(r"^```(.*)$", text, re.MULTILINE)
        openings = fences[0::2]
        self.assertEqual(len(fences) % 2, 0)
        self.assertEqual([o for o in openings if " example " not in f" {o} "], [], "an unmarked block")
        self.assertEqual(len(guide_blocks()), len(openings))
        prose = re.sub(r"^```.*?^```$", "", text, flags=re.MULTILINE | re.DOTALL)
        headings = re.findall(r"^## (.+)$", prose, re.MULTILINE)
        self.assertEqual(headings, GUIDE_SECTIONS)
        self.assertEqual(host_terms(text), [])

    def test_every_command_the_guide_names_exists(self) -> None:
        text = GUIDE.read_text(encoding="utf-8") + (SKILL / "SKILL.md").read_text(encoding="utf-8")
        named = set(re.findall(r"run_delivery\.py (?:--delivery \S+ )?([a-z][a-z-]+)", text))
        named |= set(re.findall(r"^\| (?:Lane|Lead|Setup) \| `([a-z][a-z-]+)", text, re.MULTILINE))
        # Commands named in prose, in backticks.
        skills = {s["id"] for s in json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))["skills"]}
        named |= set(re.findall(r"`((?:lead|app|review|pr)-[a-z-]+|adapter-test|handoff|doctor|ship)(?: [^`]*)?`", text)) - skills
        help_text = sh(sys.executable, "-B", self.tool, "--help").stdout
        commands = set(re.search(r"\{([a-z,-]+)\}", help_text).group(1).split(","))
        self.assertTrue(named)
        self.assertLessEqual(named, commands, f"the guide or skill text names commands the tool lacks: {named - commands}")
        # Its queue and state commands are the release's own.
        for script, command in re.findall(r"orchestrate-work/scripts/(\w+\.py) [^\n]*? (run|journal) ", text):
            with self.subTest(script=script, command=command):
                usage = sh(sys.executable, "-B", self.generation / "skills/orchestrate-work/scripts" / script, "--help").stdout
                self.assertIn(command, usage)

    def test_the_operating_rules_are_pinned(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        section = text[text.index("## 8. Operating rules"):text.index("## Troubleshooting")]
        rules = re.findall(r"^- \*\*(.+?)\*\*", section, re.MULTILINE)
        self.assertEqual(rules, OPERATING_RULES)
        normalized = " ".join(section.split())
        for phrase in ("SIGINT, SIGHUP and SIGQUIT at their defaults", "never with `nohup` or a background `&`",
                       "publish it privately", "never from a command argument"):
            self.assertIn(phrase, normalized)

    def test_the_skill_text_points_new_adopters_at_the_guide(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("[the adoption guide](references/adopting.md)", skill)
        # The guide names the skill's own files by their installed path, and each one exists in the generation.
        text = GUIDE.read_text(encoding="utf-8")
        for relative in set(re.findall(r"\.agent-skills/current/(skills/[\w./-]+\.\w+)", text)):
            with self.subTest(path=relative):
                self.assertTrue((self.generation / relative).is_file(), relative)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryLeakageTests(unittest.TestCase):
    """Every file the skill ships is free of the corpus's leakage patterns and of first-adopter terms."""

    PATTERNS = tuple((class_id, pattern) for class_id, pattern in LEAKAGE_PATTERNS if class_id != "provider-boundary")

    def outputs(self) -> list[str]:
        lock = json.loads((ROOT / "provenance.lock.json").read_text(encoding="utf-8"))
        entry = next(entry for entry in lock["entries"] if entry["id"] == "run-delivery")
        return [output["path"] for output in entry["outputs"]]

    def findings(self, text: str) -> list[str]:
        found = [class_id for class_id, pattern in self.PATTERNS if pattern.search(text)]
        found += ["first-adopter term" for word in set(re.findall(r"[a-z0-9]+", text.lower()))
                  if hashlib.sha256(word.encode()).hexdigest() in FIRST_ADOPTER_TERM_DIGESTS]
        return found

    def test_every_shipped_file_is_listed_and_clean(self) -> None:
        outputs = self.outputs()
        self.assertEqual(sorted(outputs),
                         sorted(str(p.relative_to(ROOT)) for p in SKILL.rglob("*") if p.is_file()))
        for relative in outputs:
            with self.subTest(path=relative):
                self.assertEqual(self.findings((ROOT / relative).read_text(encoding="utf-8")), [])

    def test_the_scan_is_not_vacuous(self) -> None:
        self.assertEqual({class_id for class_id, _ in self.PATTERNS},
                         {class_id for class_id, _ in LEAKAGE_PATTERNS} - {"provider-boundary"})
        self.assertTrue(FIRST_ADOPTER_TERM_DIGESTS)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in FIRST_ADOPTER_TERM_DIGESTS))
        self.assertIn("machine-path", self.findings("see /Users/someone/checkout"))
        # Assembled here, so the corpus's own ticket-id scan does not read this file as naming a ticket.
        self.assertIn("v26-ticket", self.findings("fixes " + "V26" + "-1"))
        planted = "plantedterm"
        with mock.patch(f"{__name__}.FIRST_ADOPTER_TERM_DIGESTS",
                        frozenset({hashlib.sha256(planted.encode()).hexdigest()})):
            self.assertEqual(self.findings(f"a {planted.upper()}-thing"), ["first-adopter term"])


class RunDeliveryContinuousIntegrationTests(unittest.TestCase):
    def test_posix_runners_install_what_the_video_and_harness_cases_need(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^      - uses: actions/setup-node@v4\n        if: runner\.os != 'Windows'\n"
                                   r"        with:\n          node-version: \"22\.6\.0\"$")
        self.assertRegex(workflow, r"(?m)^        if: runner\.os == 'Linux'\n        run: .*apt-get install -y ffmpeg$")
        self.assertRegex(workflow, r"(?m)^        if: runner\.os == 'macOS'\n        run: brew install ffmpeg$")
        # Both are in place before the suite runs.
        self.assertLess(workflow.index("install -y ffmpeg"), workflow.index("scripts/run-tests.py"))
        self.assertLess(workflow.index("brew install ffmpeg"), workflow.index("scripts/run-tests.py"))


if __name__ == "__main__":
    unittest.main()
