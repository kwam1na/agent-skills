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

import argparse
import ast
import atexit
import base64
import contextlib
import datetime
import fcntl
import gc
import hashlib
import io
import importlib.util
import inspect
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills import release
from agent_skills.lifecycle import Lifecycle
from agent_skills.release import build_release, verify_release
from agent_skills.validate import LEAKAGE_PATTERNS
from quiet_git import quieten
from tests.test_merge_queue import SERVING_BINDING, SERVING_RULE  # the rule both tools apply


# The cases share one fixture repository's in-app lease lock and the
# environment variables its fake adapters read, and the timeout case looks for
# leftover processes machine-wide.
RUN_SERIALLY = True

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/run-delivery"
PROFILE = "run-delivery"
TOOL_RELATIVE = "skills/run-delivery/scripts/run_delivery.py"
# The stored runtime this profile's product is rebound from.
RUNTIME_SOURCE = ROOT / "product-runtime"
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


def group_members(group: int) -> list[int]:
    """The live processes in one process group; a zombie has already stopped."""
    rows = [line.split() for line in sh("ps", "-A", "-o", "pid=,pgid=,stat=").stdout.splitlines()]
    return [int(pid) for pid, pgid, state in rows if int(pgid) == group and not state.startswith("Z")]


def commit(cwd: Path, message: str):
    sh("git", "add", "-A", cwd=cwd)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message, cwd=cwd)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def clean_environment() -> dict[str, str]:
    """The test runner's environment, without anything the tool or the fake adapters read: a setting left in the
    runner's shell, DELIVERY_STATE above all, would point a fixture's commands at a live delivery instead."""
    return {key: value for key, value in os.environ.items()
            if key != "DELIVERY_STATE" and not key.startswith(("DELIVERY_FLOW_", "RUN_DELIVERY_", "FAKE_FORGE_"))}


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
a = sub.add_parser("ready"); a.add_argument("--url")
args = p.parse_args()
if os.environ.get("FAKE_FORGE_CALLS"):  # what was asked of the forge, in order, for a case that reads it
    open(os.environ["FAKE_FORGE_CALLS"], "a").write(args.c + "\n")
if args.c == "ready":
    prs[args.url]["draft"] = False; json.dump(prs, open(state, "w")); print(json.dumps({"url": args.url, "draft": False}))
    sys.exit(0)
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
    print(json.dumps({**pr, "url": args.url, "head": pr.get("head") or head(pr["source"])}))
'''

# The fixtures' in-app server. `python -m http.server` binds through HTTPServer.server_bind, which looks up the
# address's host name before it listens; where reverse lookups go unanswered (macOS runners) that takes about 34
# seconds, and every in-app check waited it out. This one names its address without a lookup.
STATIC_SERVE = r'''
import functools, http.server, socketserver, sys
class Server(http.server.ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=sys.argv[2])
Server(("127.0.0.1", int(sys.argv[1])), handler).serve_forever()
'''

# A Python that loads this on startup answers a reverse lookup of a loopback address only after `seconds`, as a
# macOS runner does.
STALLED_LOOKUP = r'''
import socket, time
_getfqdn = socket.getfqdn
def getfqdn(name=""):
    if name in ("", "localhost", "127.0.0.1", "::1", "0.0.0.0", "::"):
        time.sleep(%d)
    return _getfqdn(name)
socket.getfqdn = getfqdn
'''


def serve_argv(script: Path) -> list[str]:
    """The in-app serve every fixture declares: STATIC_SERVE, written at `script`."""
    script.write_text(STATIC_SERVE)
    return [sys.executable, "-B", str(script), "{port}", "{dir}"]


def listens_under_stalled_lookup(argv: list[str], seconds: float) -> bool:
    """Whether a serve `argv` ({port}, {dir}) listens within `seconds` while every reverse lookup of a loopback address
    stalls for a minute. The server is started here and stopped by the pid recorded here."""
    with tempfile.TemporaryDirectory() as tmp:
        stalled = Path(tmp) / "stalled"
        stalled.mkdir()
        (stalled / "sitecustomize.py").write_text(STALLED_LOOKUP % 60)
        port = free_port()
        argv = [part.replace("{port}", str(port)).replace("{dir}", tmp) for part in argv]
        server = subprocess.Popen(argv, env={**clean_environment(), "PYTHONPATH": str(stalled)},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        return True
                time.sleep(0.05)
            return False
        finally:
            server.kill()
            server.wait()


# A real listener: argv is the port, a mark that names it in its command line, and "reuse" to share the port.
LISTENER = r'''
import socket, sys, time
s = socket.socket()
if sys.argv[3] == "reuse":
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen()
print("listening", flush=True)
time.sleep(60)
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
    # Where this checkout lives is the machine's, not the tool's: its path may hold a host's folder name.
    outside = EXAMPLE_BLOCK.sub("", text).replace(str(ROOT), "")
    return [name for name, pattern in HOST_TERMS if pattern.search(outside)]


class Fixture:
    def __init__(self, generation: Path, receipt: Path):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-delivery-characterization-"))
        watch_case(self.tmp)
        self.origin = self.tmp / "origin.git"
        self.main = self.tmp / "main"
        self.port = free_port()
        self.release = generation
        self.tool = generation / TOOL_RELATIVE
        # The commands' own temp folder, inside the case's folder but outside its checkouts: what a process the case
        # stops with SIGKILL leaves there (a merge queue's hooks folder, a full-health checkout) goes with the case.
        self.temp = self.tmp / "temp"
        self.temp.mkdir()
        # The supervisor's own cases start it; every other case runs the lead's commands by hand.
        self.env = {**clean_environment(), "FAKE_FORGE_STATE": str(self.tmp / "forge.json"), "RUN_DELIVERY_SUPERVISOR": "off",
                    "TMPDIR": str(self.temp)}
        sh("git", "init", "-q", "--bare", "-b", "main", self.origin)
        quieten(self.origin)
        sh("git", "clone", "-q", self.origin, self.main)
        quieten(self.main)
        m = self.main
        (m / "src").mkdir()
        (m / "src/app.txt").write_text("app\n")
        (m / "docs").mkdir()
        (m / "docs/plan.md").write_text(PLAN)
        (m / ".gitignore").write_text(".orchestration/\nout/\n")
        (m / ".agent-skills").mkdir()
        (m / ".agent-skills/current").symlink_to(generation)
        shutil.copy2(receipt, m / ".agent-skills/active.json")
        # The settings file the receipt's hooks member names, as the generation's install wrote it beside the receipt,
        # so the fixture's generation and its receipt agree on the hook entries it holds.
        hooks = json.loads(receipt.read_text()).get("hooks")
        if isinstance(hooks, dict) and isinstance(hooks.get("file"), str):
            installed = receipt.parent.parent / hooks["file"]
            (m / hooks["file"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(installed, m / hooks["file"])
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
            # Inside the case's folder: the default is under the runner's own HOME, which every supervisor pass and
            # every close-out would write to.
            "cleanup": {"archive": str(self.tmp / "archive/{delivery}")},
            "inApp": {"lease": "app", "lock": str(self.tmp / "app.lock"), "port": self.port,
                      "build": {"argv": ["sh", "-c", "mkdir -p out && cp src/*.txt out/"], "artifact": "out"},
                      "serve": {"argv": serve_argv(self.tmp / "static_serve.py")},
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
        stop_supervisors(self.tmp)
        self.stop_in_app()
        shutil.rmtree(self.tmp, ignore_errors=True)
        forget_case(self.tmp)

    def stop_in_app(self) -> None:
        """Stop what a killed in-app holder left running. Its lease still names the groups it started, and taking the
        lease, as lead-close does, has the tool stop them. A lease released cleanly names none, so this costs a run only
        after a kill. The wait is defensive: the tool only signals the groups, and the folder they serve is removed next.
        The fixture's own serve and check exit on SIGTERM well inside the tool's one-second port wait, so no case pins it."""
        lock = self.tmp / "app.lock"
        groups = [int(g) for g in re.findall(r"pgid (\d+)", lock.read_text())] if lock.exists() else []
        if not groups:
            return
        with contextlib.suppress(Stopped):  # still held: its live holder stops its own groups
            self.call("Lease", lock, self.port, "fixture-close", 0, then="close", cwd=Path(tempfile.gettempdir()))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(group_alive(g) for g in groups):
            time.sleep(0.1)


def locks_under(root: Path, suffix: str) -> list[Path]:
    """The lock files under a case's folder. The walk goes on past a folder removed while it walks, as the merge
    queue removes its work checkouts whenever it likes."""
    found = []
    for folder, _, files in os.walk(root, onerror=lambda error: None):
        found += [Path(folder) / name for name in files if name.endswith(suffix)]
    return found


def lock_record(lock: Path) -> tuple[bool, dict] | None:
    """(whether a process holds the lock, what it records), or None once the lock is gone. Opened to read, so a lock
    removed meanwhile is never made again."""
    try:
        with open(lock, "rb") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe, fcntl.LOCK_UN)
                held = False
            except BlockingIOError:
                held = True
            text = probe.read().decode(errors="replace")
    except (FileNotFoundError, NotADirectoryError):
        return None
    try:
        recorded = json.loads(text or "{}")
    except ValueError:  # a lock being written
        recorded = {}
    return held, recorded if isinstance(recorded, dict) else {}


def stop_supervisors(root: Path) -> None:
    """Stop any supervisor a case started under this folder, and what a killed one left: a held lock's recorded pid
    leads its group, and a lock nobody holds still names the group its children run in. Only a child still in that
    group is the case's own."""
    for lock in locks_under(root, ".supervisor.lock"):
        read = lock_record(lock)
        if read is None:
            continue
        held, recorded = read
        pid = recorded.get("pid")
        if not isinstance(pid, int) or pid <= 1:
            continue
        def in_group(child: dict) -> bool:
            try:
                return os.getpgid(child.get("pid")) == pid
            except (ProcessLookupError, PermissionError, TypeError):
                return False
        if held or any(in_group(child) for child in recorded.get("children") or []):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
    # A post-merge fullHealth run leads a session of its own, so it outlives its supervisor: stop it while it holds its
    # lock, and only then.
    for lock in locks_under(root, ".full-health.lock"):
        read = lock_record(lock)
        if read is None or not read[0]:
            continue
        pid = read[1].get("pid")
        if isinstance(pid, int) and pid > 1:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)


# The case folders whose supervisors are still this process's to stop. A supervisor runs in a session of its own,
# outside the test runner's process group, so when the runner stops a worker (SIGTERM, a grace, then SIGKILL) only
# the worker itself can stop it: on SIGTERM or SIGHUP, and at exit, it stops every live case's supervisors first.
LIVE_CASES: list[Path] = []
_INTERRUPT_HANDLERS: dict[int, object] = {}
_WATCHING: list[bool] = []  # set once the handlers and the exit hook are in place


def stop_live_cases() -> None:
    for folder in list(LIVE_CASES):
        with contextlib.suppress(Exception):
            stop_supervisors(folder)


def _interrupted(signum, frame) -> None:
    stop_live_cases()
    earlier = _INTERRUPT_HANDLERS.get(signum, signal.SIG_DFL)
    if callable(earlier):
        earlier(signum, frame)
        return
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)  # then end as the signal would have


def watch_case(folder: Path) -> None:
    """Stop this case's supervisors if this process is interrupted or exits before the case's own cleanup does. The
    first call, from the main thread, puts the handlers and the exit hook in place, once; a signal this process
    ignores stays ignored."""
    if not _WATCHING and threading.current_thread() is threading.main_thread():
        _WATCHING.append(True)
        for signum in (signal.SIGTERM, signal.SIGHUP):
            earlier = signal.getsignal(signum)
            if earlier is signal.SIG_IGN:
                continue
            _INTERRUPT_HANDLERS[signum] = earlier
            signal.signal(signum, _interrupted)
        atexit.register(stop_live_cases)
    LIVE_CASES.append(folder)


def forget_case(folder: Path) -> None:
    with contextlib.suppress(ValueError):
        LIVE_CASES.remove(folder)


def group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:  # the group exists, but isn't ours to signal
        return True


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
                        "--ship-from", "origin/main", "--context-section", "Decisions", "--no-authority", cwd=m)
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


POSTURE_VECTORS = ROOT / "tests/fixtures/delivery-posture-vectors.json"
POSTURE_PATH = ".agents/delivery-posture.json"
# Every git command fails, or never returns and leaves a child holding its output, as a fetch's transport can.
# The machine-wide pattern is `pgrep -f "sleep 30"`, which the timeout case once used to find its leftovers: it matched
# any process whose command line held that text, `sleep 300` among them. That case now checks its own process group,
# but a hanging stub's sleep still stays out of the pattern, so no search for it, then or now, can find one.
MACHINE_WIDE_SLEEP = re.compile("sleep 30")
POSTURE_HANG = "sleep 240"
POSTURE_GIT_STUBS = {"fails": "#!/bin/sh\nexit 1\n", "hangs": f"#!/bin/sh\n{POSTURE_HANG} &\nwait\n"}


def expanded(base: dict) -> str:
    """A blob vector's text with each `expand` marker replaced by its nesting, as the vector file's `setup` says."""
    text = base["text"]
    for entry in base.get("expand", []):
        marker, opening, closing, inner = entry["marker"], entry["open"], entry["close"], entry.get("inner", "")
        assert text.count(marker) == 1, f"{marker} isn't in the text exactly once"
        if "fillTo" in entry:
            spare = entry["fillTo"] - len((text.replace(marker, inner)).encode("utf-8"))
            count = spare // len((opening + closing).encode("utf-8"))
        else:
            count = entry["count"]
        text = text.replace(marker, opening * count + inner + closing * count)
    return text


def posture_tree(repository: Path, base: dict) -> None:
    """Put what a vector's `base` holds at the declaration's path into a checkout."""
    path = repository / POSTURE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    kind = base["kind"]
    if kind == "blob":
        data = base64.b64decode(base["base64"]) if "base64" in base else expanded(base).encode("utf-8")
        path.write_bytes(data + b" " * (base.get("padTo", len(data)) - len(data)))
    elif kind == "symlink":
        path.symlink_to(base["target"])
    elif kind == "tree":
        path.mkdir()
        (path / "inner.json").write_text("{}\n")
    elif kind == "submodule":
        tip = sh("git", "rev-parse", "HEAD", cwd=repository).stdout.strip()
        sh("git", "update-index", "--add", "--cacheinfo", f"160000,{tip},{POSTURE_PATH}", cwd=repository)
    elif kind != "none":
        raise AssertionError(f"no such base kind: {kind}")


def posture_vector_repo(vector: dict, folder: Path) -> tuple[Path, dict[str, str]]:
    """A clone and a local bare remote set up as a vector says: (the clone to read in, the environment to read with).
    The vector file's `setup` member says what each field means; both readers' cases build their repository here."""
    remote, author, clone = folder / "remote.git", folder / "author", folder / "clone"
    sh("git", "init", "-q", "--bare", "--initial-branch=main", remote)
    quieten(remote)
    sh("git", "init", "-q", "--initial-branch=main", author)
    quieten(author)
    (author / "README.md").write_text("fixture\n")
    commit(author, "first")
    sh("git", "remote", "add", "origin", remote, cwd=author)
    if vector.get("ref") != "missing":
        sh("git", "push", "-q", "origin", "main", cwd=author)
    sh("git", "init", "-q", "--initial-branch=main", clone)
    quieten(clone)
    sh("git", "remote", "add", "origin", remote, cwd=clone)
    if vector.get("ref") != "missing":
        sh("git", "fetch", "-q", "--no-tags", "origin", cwd=clone)
        sh("git", "remote", "set-head", "origin", "main", cwd=clone)
    posture_tree(author, vector["base"])
    if vector["base"]["kind"] != "submodule":  # its gitlink is in the index, and adding everything would drop it
        sh("git", "add", "-A", cwd=author)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "the declaration", "--allow-empty", cwd=author)
    if vector.get("ref") != "missing":
        sh("git", "push", "-q", "origin", "main", cwd=author)
    if vector.get("fetch") == "fails":
        sh("git", "fetch", "-q", "--no-tags", "origin", cwd=clone)
        sh("git", "remote", "set-url", "origin", folder / "gone.git", cwd=clone)
    if "shadow" in vector:
        sh("git", "checkout", "-q", "-b", "shadow", cwd=author)
        posture_tree(author, vector["shadow"])
        commit(author, "a local name that isn't the base")
        sh("git", "fetch", "-q", "--no-tags", author, "shadow:refs/heads/origin/main", cwd=clone)
        sh("git", "update-ref", "refs/tags/origin/main", "refs/heads/origin/main", cwd=clone)
    env = clean_environment()
    if "git" in vector:
        stubs = folder / "stubs"
        stubs.mkdir()
        (stubs / "git").write_text(POSTURE_GIT_STUBS[vector["git"]])
        (stubs / "git").chmod(0o755)
        env["PATH"] = f"{stubs}{os.pathsep}{env['PATH']}"
    return clone, env


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
    if "release" in RELEASE_CACHE and RELEASE_CACHE["release"].receipt.exists():
        return RELEASE_CACHE["release"]
    # Not built yet, or built for another module whose cleanup has since removed it: build one for this module.
    built = types.SimpleNamespace()
    built.build = Path(tempfile.mkdtemp(prefix="run-delivery-release-"))
    unittest.addModuleCleanup(shutil.rmtree, built.build, True)
    # The stored runtime, with only its descriptor's workflow digest rebound to this profile's workflow projection.
    runtime = built.build / "runtime"
    runtime.mkdir()
    for name in release.RUNTIME_FILES:
        (runtime / name).write_bytes((RUNTIME_SOURCE / name).read_bytes())
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

    # -- the lead's review carry ------------------------------------------------------------------------

    def test_lead_carry_review_shows_the_diff_it_carries_across_and_passes_the_state_tools_refusal_through(self) -> None:
        """`lead-carry-review` prints from..to's diffstat, naming each file, before the state tool's carry writes; a
        refusal is the state tool's own text, and writes nothing."""
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane 1", cwd=m)
        self.assertEqual(code, 0, out)
        sh("git", "checkout", "-q", "-b", "feature/FX-2", cwd=m)
        (m / "src/one.txt").write_text("one\n")
        commit(m, "FX-2: one")
        reviewed = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        (m / "src/one-fixture.txt").write_text("fixture\n")
        commit(m, "FX-2: a fixture after the last round")
        queued = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        (m / "src/one-later.txt").write_text("later\n")
        commit(m, "FX-2: a later commit nobody handed off")
        later = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        sh("git", "checkout", "-q", "feature/FX-1", cwd=m)
        state = Path(json.loads((m / ".agents/deliveries/FX-1.json").read_text())["stateDirectory"])
        tool = fx.release / "skills/orchestrate-work/scripts/delivery_state.py"

        def state_tool(*args: str) -> dict:
            done = subprocess.run([sys.executable, "-B", str(tool), "--dir", str(state), *args], cwd=m, env=fx.env,
                                  text=True, capture_output=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            return json.loads(done.stdout)

        rounds = []
        for number in (1, 2):
            entries = []
            for lens in ("lens.outcome-correctness", "lens.adversarial-testing"):
                binding = {"subjectRef": "FX-2", "candidateRef": reviewed, "round": number}
                entries.append({"lens": lens, "state": "obtained", "binding": binding, "result": {
                    "schemaVersion": "review-lens-result/1", "lensId": lens, **binding, "outcome": "aligned",
                    "findings": [], "resolvedFindings": [], "evidence": ["observed"]}})
            rounds.append(fx.tmp / f"round-{number}.json")
            rounds[-1].write_text(json.dumps({"round": number, "outcome": "aligned", "entries": entries}))
        state_tool("capture-review", "--subject", "FX-2", "--candidate", reviewed,
                   *[part for path in rounds for part in ("--outcome", str(path))])
        state_tool("handoff", "--lane", "lane-1", "--subject", "FX-2", "--head", queued, "--change-request",
                   "https://forge.test/pr/1", "--rounds", "2")
        journal = lambda: [e for e in state_tool("show")["journal"] if e.get("reviewCarry")]
        sh("git", "push", "-q", "origin", "feature/FX-2", cwd=m)
        other = fx.tmp / "elsewhere"
        sh("git", "clone", "-q", "-b", "feature/FX-2", fx.origin, other)
        quieten(other)
        (other / "src/one-remote.txt").write_text("remote\n")
        sh("git", "-c", "user.name=Lane", "-c", "user.email=lane@example.test", "add", "src/one-remote.txt", cwd=other)
        sh("git", "-c", "user.name=Lane", "-c", "user.email=lane@example.test", "commit", "-q", "-m", "FX-2: elsewhere",
           cwd=other)
        sh("git", "push", "-q", "origin", "feature/FX-2", cwd=other)
        remote_only = sh("git", "rev-parse", "HEAD", cwd=other).stdout.strip()
        self.assertNotEqual(sh("git", "cat-file", "-t", remote_only, cwd=m, check=False).returncode, 0)

        with self.subTest("a --to that isn't the queued head: the state tool's own refusal, and no write"):
            code, out = fx.flow("lead-carry-review", "FX-2", "--from", reviewed, "--to", later, "--reason", "fixture", cwd=m)
            self.assertEqual(code, 2, out)
            refusal = out.index(f"carry_invalid: FX-2 is queued at {queued}, not {later}")
            shown = out.index(f"FX-2's review carries from {reviewed[:10]} to {later[:10]} across:")
            self.assertLess(shown, refusal, "the diff is shown before the state tool is asked")
            self.assertIn("src/one-later.txt", out[shown:refusal])
            self.assertEqual(journal(), [])
        with self.subTest("a head only on the story's remote branch is fetched, then judged by the state tool"):
            code, out = fx.flow("lead-carry-review", "FX-2", "--from", reviewed, "--to", remote_only, "--reason", "x",
                                cwd=m)
            self.assertEqual(code, 2, out)
            self.assertNotIn("no commit", out)
            self.assertIn(f"carry_invalid: FX-2 is queued at {queued}, not {remote_only}", out)
            self.assertIn("src/one-remote.txt", out)
            self.assertEqual(journal(), [])
        with self.subTest("a head that names no commit is refused before the state tool"):
            code, out = fx.flow("lead-carry-review", "FX-2", "--from", reviewed, "--to", "f" * 40, "--reason", "x", cwd=m)
            self.assertEqual(code, 2, out)
            self.assertIn(f"no commit {'f' * 40}", out)
            self.assertEqual(journal(), [])
        code, out = fx.flow("lead-carry-review", "FX-2", "--from", reviewed, "--to", queued, "--reason",
                            "only a fixture changed", cwd=m)
        self.assertEqual(code, 0, out)
        diff = out.index(f"FX-2's review carries from {reviewed[:10]} to {queued[:10]} across:")
        done = out.index("carried: ")
        listed = {line.split()[-1] for line in out[diff:done].splitlines()[1:] if line.strip()}
        # Exactly the commits between the heads: not the reviewed head's own change, nor a later one.
        self.assertEqual(listed, {"src/one-fixture.txt"}, out)
        carried = journal()
        self.assertEqual(len(carried), 1, carried)
        self.assertIn(f"carried: {carried[0]['summary']}\n", out)
        self.assertEqual((carried[0]["reviewCarry"]["from"], carried[0]["reviewCarry"]["to"]), (reviewed, queued))
        self.assertIn("only a fixture changed", carried[0]["summary"])

    # -- the merge queue's review gate -----------------------------------------------------------------

    def test_pr_review_current_passes_only_a_current_passing_review_at_the_named_head(self) -> None:
        """`pr-review-current` is a ready-made reviewGate: exit 0 only for the configured review, current and passing
        at that head; every other answer exits non-zero with one line on stderr."""
        fx = self.fixture()
        m = fx.main
        sh("git", "checkout", "-q", "-b", "feature/FX-2", cwd=m)
        (m / "src/one.txt").write_text("one\n")
        commit(m, "FX-2: one")
        sh("git", "push", "-q", "-u", "origin", "feature/FX-2", cwd=m)
        head = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        reviews = fx.tmp / "reviews.json"
        env = {**fx.env, "FAKE_FORGE_REVIEWS": str(reviews)}

        def gate(*extra: str, at: str = head, conf: dict | None = None) -> tuple[int, str, str]:
            if conf is not None:
                path = fx.tmp / "flow-gate.json"
                path.write_text(json.dumps(conf))
                env["RUN_DELIVERY_CONFIG"] = str(path)
            done = subprocess.run([sys.executable, "-B", str(fx.tool), "pr-review-current", "--branch", "feature/FX-2",
                                   "--head", at, *extra], cwd=m, env=env, text=True, capture_output=True, timeout=300)
            self.assertNotIn("Traceback", done.stderr)
            return done.returncode, done.stdout, done.stderr

        def refused(answer: tuple[int, str, str], reason: str) -> None:
            code, _, err = answer
            self.assertNotEqual(code, 0, f"passed: {answer}")
            self.assertEqual(len(err.strip().splitlines()), 1, f"the reason isn't one line: {err!r}")
            self.assertIn(reason, err)

        base = json.loads((m / ".agents/run-delivery.json").read_text())
        refused(gate(), "review.pr")
        conf = {**base, "review": {**base["review"], "pr": {"author": "Pipeline Bot", "waitSeconds": 1}}}
        refused(gate(conf=conf), "no open PR for feature/FX-2")
        (fx.tmp / "forge.json").write_text(json.dumps({"https://forge.test/pr/1": {
            "source": "feature/FX-2", "destination": "main", "state": "open", "title": "FX-2: one"}}))
        passed = {"name": "pipe", "state": "passed", "at": "2026-01-01T00:00:10"}
        current = {"author": "Pipeline Bot", "at": "2026-01-01T00:00:30", "body": "fine"}
        cases = [
            ("no checks and no review", {"checks": [], "reviews": []}, "no current Pipeline Bot review"),
            ("a pending pipeline", {"checks": [{**passed, "state": "pending"}], "reviews": [current]}, "pipe pending"),
            ("a failed pipeline", {"checks": [{**passed, "state": "failed"}], "reviews": [current]}, "pipe failed"),
            ("a review from before the pipeline", {"checks": [passed], "reviews": [{**current, "at": "2026-01-01T00:00:05"}]},
             "no current Pipeline Bot review"),
            ("someone else's review", {"checks": [passed], "reviews": [{**current, "author": "Someone"}]},
             "no current Pipeline Bot review"),
        ]
        for what, answer, reason in cases:
            with self.subTest(what):
                reviews.write_text(json.dumps(answer))
                refused(gate(), reason)
        reviews.write_text(json.dumps({"checks": [passed], "reviews": [current]}))
        code, out, err = gate()
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("Pipeline Bot", out)
        with self.subTest("the PR at another head"):
            refused(gate(at=fx.tooling), f"the PR is at {head[:10]}, not {fx.tooling[:10]}")
        with self.subTest("an adapter that can't fetch reviews"):
            refused(gate(conf={**conf, "forge": {"adapter": "none"}}), "no `reviews` command")
        with self.subTest("an adapter's own error, over several lines, is one line"):
            loud = {"argv": ["sh", "-c", "printf 'forge said no\\nand said why\\n' >&2; exit 3"]}
            refused(gate(conf={**conf, "forge": loud}), "and said why")
        gate(conf=conf)
        with self.subTest("without --wait it answers at once, however long waitSeconds is"):
            gate(conf={**conf, "review": {**conf["review"], "pr": {"author": "Pipeline Bot", "waitSeconds": 30}}})
            reviews.write_text(json.dumps({"checks": [{**passed, "state": "pending"}], "reviews": []}))
            started = time.monotonic()
            answer = gate()
            refused(answer, "pipe pending")
            self.assertNotIn("waited", answer[2])
            self.assertLess(time.monotonic() - started, 10, "waited without --wait")
            gate(conf=conf)
        with self.subTest("--wait gives up after review.pr's waitSeconds"):
            reviews.write_text(json.dumps({"checks": [{**passed, "state": "pending"}], "reviews": []}))
            started = time.monotonic()
            answer = gate("--wait")
            refused(answer, "waited 1s")
            self.assertLess(time.monotonic() - started, 20, "waited past waitSeconds")
        with self.subTest("--wait waits out a forge still showing the PR at its old head"):
            gate(conf={**conf, "review": {**conf["review"], "pr": {"author": "Pipeline Bot", "waitSeconds": 30}}})
            reviews.write_text(json.dumps({"checks": [passed], "reviews": [current]}))
            prs = json.loads((fx.tmp / "forge.json").read_text())
            lagging = {url: {**pr, "head": fx.tooling[:12]} for url, pr in prs.items()}
            (fx.tmp / "forge.json").write_text(json.dumps(lagging))
            refused(gate(), f"the PR is at {fx.tooling[:10]}, not {head[:10]}")
            catch_up = threading.Timer(1, lambda: (fx.tmp / "forge.json").write_text(json.dumps(prs)))
            catch_up.start()
            self.addCleanup(catch_up.cancel)
            started = time.monotonic()
            code, out, err = gate("--wait")
            self.assertEqual((code, err), (0, ""), out)
            self.assertLess(time.monotonic() - started, 25, "kept waiting after the forge caught up")
            gate(conf=conf)
        with self.subTest("--wait passes once the review lands within the wait"):
            gate(conf={**conf, "review": {**conf["review"], "pr": {"author": "Pipeline Bot", "waitSeconds": 30}}})
            land = threading.Timer(1, lambda: reviews.write_text(json.dumps({"checks": [passed], "reviews": [current]})))
            land.start()
            self.addCleanup(land.cancel)
            started = time.monotonic()
            code, out, err = gate("--wait")
            self.assertEqual((code, err), (0, ""), out)
            self.assertLess(time.monotonic() - started, 25, "kept waiting after the review landed")

    # -- the release the cases run from ----------------------------------------------------------------

    def test_the_built_release_carries_the_rebound_runtime_and_verifies(self) -> None:
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
        # Only the still-open findings must appear; one closed in an earlier round may be listed again.
        relisted = lens_result(round=2, resolvedFindings=[{"id": "l-r1-1", "state": "fixed", "resolution": "fixed"},
                                                          {"id": "l-r1-2", "state": "fixed", "resolution": "fixed in round 1"}])
        expect(check(relisted, carried={"l-r1-1"}, n=2) == [], check(relisted, carried={"l-r1-1"}, n=2))
        expect(check(lens_result(lateFindings=["l-r1-9"])), "a lateFindings id that isn't one of the result's own was accepted")

    def test_a_story_review_defers_a_p1_only_when_it_is_late_in_a_verification_round_with_its_follow_up(self) -> None:
        """Decision 1: review-reduce accepts a deferred P1 only in round > 1, listed in lateFindings, with a non-empty
        deferredIssue. Every other deferred P1, and every deferred P0, is refused as before."""
        def check(r, n=2):
            return self.call("check_result", r, key="K", lens="lens.l", short="l", n=n, meta=META, carried=[],
                             sets=("carried",))

        def result(n=2, severity="P1", late=True, issue="a follow-up for the retry path"):
            finding = {"id": f"l-r{n}-1", "severity": severity, "summary": "s", "state": "deferred"}
            if issue is not None:
                finding["deferredIssue"] = issue
            return lens_result(round=n, resolvedFindings=[], findings=[finding],
                               **({"lateFindings": [f"l-r{n}-1"]} if late else {}))

        self.assertEqual(check(result()), [], "a late deferred P1 with its follow-up was refused")
        for name, r, n, said in (
                ("not listed in lateFindings", result(late=False), 2, "not listed in lateFindings"),
                ("no deferredIssue", result(issue=None), 2, "with no deferredIssue"),
                ("a blank deferredIssue", result(issue="  "), 2, "with no deferredIssue"),
                ("a deferred P0", result(severity="P0"), 2, "is P0 and can't be deferred"),
                ("in round 1, even listed late with its follow-up", result(n=1), 1, "can't be deferred in an assessment round")):
            with self.subTest(name):
                problems = check(r, n=n)
                self.assertTrue(any(said in problem for problem in problems), problems)
        # Filed open instead, the same late P1 blocks, as before.
        filed = lens_result(round=2, outcome="changes-requested", resolvedFindings=[], lateFindings=["l-r2-1"],
                            findings=[{"id": "l-r2-1", "severity": "P1", "summary": "s"}])
        self.assertEqual(check(filed), [])

    def test_a_late_deferred_p1_reduces_to_aligned_and_is_listed_for_its_follow_up(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        wt = fx.lane("FX-2")
        review = wt / ".orchestration/review/FX-2"
        lenses = fx.lenses(wt)
        short0 = lenses[0][1][0]
        for n, change in ((1, "one\n"), (2, "two\n")):
            (wt / "src/one.txt").write_text(change)
            commit(wt, f"FX-2: {change.strip()}")
            code, out = fx.flow("review-prepare", "FX-2", "--round", str(n), cwd=wt)
            if code == 2 and "Evidence so far" in out:
                context = review / "context.md"
                context.write_text(context.read_text().replace(next(line for line in context.read_text().splitlines()
                                                                    if line.startswith(CONTEXT_TODO)), "- evidence"))
                code, out = fx.flow("review-prepare", "FX-2", "--round", str(n), cwd=wt)
            self.assertEqual(code, 0, out)
            if n == 1:
                write_results(review, 1, "FX-2", lenses)
            else:
                write_results(review, 2, "FX-2", lenses, resolved=[])
                path = review / f"result-r2-{short0}.json"
                first = json.loads(path.read_text())
                first.update(outcome="aligned", lateFindings=[f"{short0}-r2-1"], findings=[
                    {"id": f"{short0}-r2-1", "severity": "P1", "summary": "the retry path swallows the error",
                     "state": "deferred", "deferredIssue": "file a follow-up for the retry path"}])
                path.write_text(json.dumps(first))
            code, out = fx.flow("review-reduce", "FX-2", "--round", str(n), cwd=wt)
            self.assertEqual(code, 0, out)
        self.assertIn("FX-2 round 2: aligned", out)
        self.assertIn(f"deferred P1 {short0}-r2-1", out)
        self.assertIn("-> follow-up: file a follow-up for the retry path", out)

    def test_lead_assign_warns_of_files_outside_the_lane_before_it_starts(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        plan = m / "docs/plan.md"
        plan.write_text(plan.read_text()
                        .replace("- Scenarios: it loads; it fails.\n",
                                 "- Scenarios: it loads; it fails.\n- Files: `src/one.txt`, `src/two.txt`, `one.txt`, `tools/`, "
                                 "`docs/refs/*.md`, `x/notes2.md`, `src/one-log.txt`, `x/a.md`, `./tools/fake_check.py`, "
                                 "`Makefile`.\n")
                        .replace("- Scenarios: it renders.\n", "- Scenarios: it renders.\n- Files: `src/two.txt`.\n")
                        .replace("| Copy | \"Couldn't be loaded.\" |\n",
                                 "| Copy | \"Couldn't be loaded.\" |\n| S10's files | `tools/s10.py` |\n"))
        # A bare name or a path's end that several files share is the lane's own match; a directory and a glob resolve;
        # a path's end matches whole folder names; a bare word with no extension, and a row naming S10, aren't S1's.
        for path in ("src/one.txt", "docs/one.txt", "docs/refs/notes.md", "docs/deep/x/notes2.md", "docs/deep/yx/notes2.md",
                     "src/one-dir/x/a.md", "docs/x/a.md", "docs/Makefile"):
            (m / path).parent.mkdir(parents=True, exist_ok=True)
            (m / path).write_text(f"{path}\n")
        commit(m, "FX-1: the plan names FX-2's files")
        (m / "docs/guide.md").write_text("guide\n")
        (m / "docs/rare.md").write_text("rare\n")
        commit(m, "FX-1: the docs")
        # History, without the sweeping change below: src/one.txt changes 5 times, docs/refs/notes.md (named) 4.
        # docs/pair.md changes with src/one.txt in 4 of 5 and with notes.md in 3 of 4, so its best match is the later
        # name. docs/guide.md, z1 and z2 change with notes.md in 3 of 4, docs/rare.md twice (too few), docs/gone.md 3
        # times but is gone from the base, and src/one-extra.txt, the lane's own, as often as docs/pair.md. Four are
        # shown, so a fifth that shouldn't be (the lane's own, or docs/log.md below) would show.
        named, own, zs = "docs/refs/notes.md", "src/one-extra.txt", ["docs/z1.md", "docs/z2.md"]
        common = ["docs/guide.md", named, own, "docs/pair.md", "docs/gone.md", *zs]
        for k, also in enumerate(([*common, "docs/rare.md"], [*common, "docs/rare.md"], common, ["docs/pair.md", own])):
            (m / "src/one.txt").write_text(f"{k + 1}\n")
            for path in also:
                (m / path).write_text(f"{path} {k}\n")
            commit(m, f"FX-1: change {k}")
        sh("git", "rm", "-q", "docs/gone.md", cwd=m)
        commit(m, "FX-1: docs/gone.md goes")
        (m / "sweep").mkdir()
        for k in range(45):
            (m / f"sweep/{k}.txt").write_text("x\n")
        for path in ("src/one.txt", "docs/guide.md", named):
            (m / path).write_text("sweep\n")
        commit(m, "FX-1: a sweeping change, which would make guide.md 4 of 5 with notes.md if it counted")
        # src/one-log.txt changes 13 times, 3 of them with docs/log.md: often enough, but too small a share.
        (m / "src/one-log.txt").write_text("0\n")
        commit(m, "FX-1: a log")
        for k in range(12):
            (m / "src/one-log.txt").write_text(f"{k + 1}\n")
            if k < 3:
                (m / "docs/log.md").write_text(f"{k}\n")
            commit(m, f"FX-1: log {k}")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        # The lead's checkout holds a commit the base doesn't: the warning reads the base's files.
        (m / "tools/local-only.py").write_text("x\n")
        commit(m, "FX-1: not on the base")
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane 1", cwd=m)
        self.assertEqual(code, 0, out)
        shared = "(no lane's; with docs/refs/notes.md in 3 of 4 changes)"
        self.assertIn("Files outside lane-1's owns, before it starts (lead-accept refuses a change to any of them):\n"
                      "  FX-2 names docs/deep/x/notes2.md (no lane's), docs/refs/notes.md (no lane's), src/two.txt (lane-2's), "
                      "tools/fake_check.py (no lane's), tools/fake_forge.py (no lane's)\n"
                      "  FX-2's files often change with docs/pair.md (no lane's; with src/one.txt in 4 of 5 changes), "
                      f"docs/guide.md {shared}, docs/z1.md {shared}, docs/z2.md {shared}\n"
                      "Move each to lane-1's owns now, or plan the story on the lane that owns it.\n", out)
        for quiet in ("docs/rare.md", "docs/log.md", "src/one-extra.txt", "docs/gone.md", "docs/x/a.md",
                      "yx/notes2.md", "tools/s10.py", "Makefile", "./tools", "tools/local-only.py"):
            self.assertNotIn(quiet, out)
        self.assertLess(out.index("Files outside"), out.index("Assignment message for lane-1"))
        # A story that names only its lane's files gets no warning.
        code, out = fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertNotIn("Files outside", out)

    def test_a_lens_keeps_one_checkout_across_rounds_and_is_reprepared_only_when_a_dependency_changes(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        prepares = fx.tmp / "prepares.log"
        config = m / ".agents/run-delivery.json"
        settings = json.loads(config.read_text())
        settings["review"]["prepare"] = [["sh", "-c", f"echo run >> {prepares} && mkdir -p out && echo env > out/env"]]
        config.write_text(json.dumps(settings, indent=2))
        commit(m, "FX-1: a prepare step")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        wt = fx.lane("FX-2")
        review = wt / ".orchestration/review/FX-2"
        lenses = fx.lenses(wt)
        short0 = lenses[0][1][0]
        runs = lambda: prepares.read_text().count("run") if prepares.exists() else 0

        def prepare(n: int, change: str) -> dict[str, Path]:
            (wt / "src/one.txt").write_text(change)
            commit(wt, f"FX-2: {change.strip()}")
            code, out = fx.flow("review-prepare", "FX-2", "--round", str(n), cwd=wt)
            if code == 2 and "Evidence so far" in out:
                context = review / "context.md"
                context.write_text(context.read_text().replace(next(line for line in context.read_text().splitlines()
                                                                    if line.startswith(CONTEXT_TODO)), "- evidence"))
                code, out = fx.flow("review-prepare", "FX-2", "--round", str(n), cwd=wt)
            self.assertEqual(code, 0, out)
            found = {}
            for _, (short, _) in lenses:
                brief = (review / f"brief-r{n}-{short}.md").read_text()
                found[short] = Path(re.search(r"Your private checkout: `([^`]+)`", brief).group(1))
            return found

        def plant(checkouts: dict[str, Path]) -> None:
            for checkout in checkouts.values():
                (checkout / "src/probe.txt").write_text("a lens's untracked probe\n")
                (checkout / "out/cache").write_text("kept with the environment\n")
                (checkout / "src/app.txt").write_text("a lens's unreverted edit\n")

        def reduce(n: int, open_p1: bool) -> None:
            if open_p1:
                write_results(review, n, "FX-2", lenses, findings=[{"id": f"{short0}-r{n}-1", "severity": "P1", "summary": "fix"}],
                              resolved=[{"id": f"{short0}-r{n - 1}-1", "state": "fixed", "resolution": "x"}] if n > 1 else None)
            else:
                write_results(review, n, "FX-2", lenses, resolved=[{"id": f"{short0}-r{n - 1}-1", "state": "fixed", "resolution": "x"}])
            code, out = fx.flow("review-reduce", "FX-2", "--round", str(n), cwd=wt)
            self.assertEqual(code, 0, out)

        first = prepare(1, "one\n")
        self.assertEqual(runs(), len(lenses), "each lens's checkout wasn't prepared once")
        self.assertEqual(len(set(first.values())), len(lenses), first)
        plant(first)
        reduce(1, open_p1=True)
        for checkout in first.values():
            self.assertTrue(checkout.is_dir(), f"{checkout} was removed while the review goes on")

        # Round 2 changes no dependency: the same checkouts, at the new candidate, clean, with the environment kept.
        second = prepare(2, "two\n")
        self.assertEqual(second, first, "a lens got a new checkout for round 2")
        head = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        for checkout in second.values():
            self.assertEqual(sh("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip(), head)
            self.assertEqual(sh("git", "status", "--porcelain", cwd=checkout).stdout, "", f"{checkout} isn't clean")
            self.assertFalse((checkout / "src/probe.txt").exists(), "the lens's untracked probe was kept")
            self.assertEqual((checkout / "src/app.txt").read_text(), "app\n", "the lens's edit to a tracked file was kept")
            self.assertEqual((checkout / "src/one.txt").read_text(), "two\n")
            self.assertTrue((checkout / "out/cache").exists(), "the prepared environment was thrown away with no dependency change")
        self.assertEqual(runs(), len(lenses), "a round with no dependency change prepared again")
        plant(second)
        reduce(2, open_p1=True)

        # Round 3 adds a dependency manifest: the environment is cleared and prepared again, in the same checkouts.
        (wt / "requirements.txt").write_text("example==1.0\n")
        third = prepare(3, "three\n")
        self.assertEqual(third, first, "a lens got a new checkout for round 3")
        self.assertEqual(runs(), 2 * len(lenses), "a dependency change didn't prepare each checkout again")
        for checkout in third.values():
            self.assertFalse((checkout / "out/cache").exists(), "an ignored file outlived a dependency change")
            self.assertTrue((checkout / "out/env").exists(), "the environment wasn't prepared again")
            self.assertTrue((checkout / "requirements.txt").exists())
            self.assertEqual(sh("git", "status", "--porcelain", cwd=checkout).stdout, "", f"{checkout} isn't clean")
        reduce(3, open_p1=False)
        for checkout in third.values():
            self.assertFalse(checkout.exists(), f"{checkout} outlived the aligned round")
            self.assertFalse(checkout.with_name(checkout.name + ".prepared.json").exists(), "a removed checkout's stamp was kept")
        self.assertNotIn(str(third[short0]), sh("git", "worktree", "list", cwd=wt).stdout)

    def lens_round_one(self, bound: int = 3) -> tuple:
        """FX-2 with a counting prepare step, through round 1's prepare: (fixture, lane, review folder, lenses,
        prepares log, each lens's checkout by short name)."""
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        prepares = fx.tmp / "prepares.log"
        config = m / ".agents/run-delivery.json"
        settings = json.loads(config.read_text())
        settings["review"].update(bound=bound, prepare=[["sh", "-c", f"echo run >> {prepares}"]])
        config.write_text(json.dumps(settings, indent=2))
        commit(m, "FX-1: a prepare step")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        wt = fx.lane("FX-2")
        review = wt / ".orchestration/review/FX-2"
        lenses = fx.lenses(wt)
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        context = review / "context.md"
        context.write_text(context.read_text().replace(next(line for line in context.read_text().splitlines()
                                                            if line.startswith(CONTEXT_TODO)), "- evidence"))
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        checkouts = {short: Path(re.search(r"Your private checkout: `([^`]+)`",
                                           (review / f"brief-r1-{short}.md").read_text()).group(1)) for _, (short, _) in lenses}
        return fx, wt, review, lenses, prepares, checkouts

    def test_a_review_that_reaches_its_bound_removes_the_lens_checkouts(self) -> None:
        fx, wt, review, lenses, _, checkouts = self.lens_round_one(bound=1)
        short0 = lenses[0][1][0]
        write_results(review, 1, "FX-2", lenses, findings=[{"id": f"{short0}-r1-1", "severity": "P1", "summary": "fix"}])
        code, out = fx.flow("review-reduce", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertIn("Bound reached", out)
        for checkout in checkouts.values():
            self.assertFalse(checkout.exists(), f"{checkout} outlived a review at its bound")
            self.assertFalse(checkout.with_name(checkout.name + ".prepared.json").exists(), "a removed checkout's stamp was kept")
            self.assertNotIn(str(checkout), sh("git", "worktree", "list", cwd=wt).stdout)

    def test_a_path_at_a_lens_checkout_that_isnt_its_own_worktree_is_made_afresh_and_the_lane_is_untouched(self) -> None:
        fx, wt, review, lenses, prepares, checkouts = self.lens_round_one()
        short0 = lenses[0][1][0]
        write_results(review, 1, "FX-2", lenses, findings=[{"id": f"{short0}-r1-1", "severity": "P1", "summary": "fix"}])
        code, out = fx.flow("review-reduce", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        # What a partial removal leaves: a plain folder, no .git, with its prepared stamp still beside it.
        checkout = checkouts[short0]
        sh("git", "worktree", "remove", "--force", checkout, cwd=wt)
        checkout.mkdir()
        (checkout / "leftover.txt").write_text("left\n")
        (wt / "out").mkdir(exist_ok=True)
        (wt / "out/lane-ignored").write_text("the lane's own\n")
        (wt / "src/one.txt").write_text("two\n")
        commit(wt, "FX-2: two")
        code, out = fx.flow("review-prepare", "FX-2", "--round", "2", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(Path(sh("git", "rev-parse", "--show-toplevel", cwd=checkout).stdout.strip()).resolve(), checkout.resolve(),
                         "the plain folder wasn't made its own checkout")
        self.assertFalse((checkout / "leftover.txt").exists())
        self.assertEqual(sh("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip(), sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip())
        self.assertEqual(sh("git", "symbolic-ref", "--short", "HEAD", cwd=wt).stdout.strip(), "feature/FX-2", "the lane was detached")
        self.assertTrue((wt / "out/lane-ignored").exists(), "the lane's own ignored file was cleaned")
        self.assertEqual(prepares.read_text().count("run"), len(lenses) + 1, "the made-afresh checkout wasn't prepared")

    def test_a_lens_checkout_gone_between_rounds_is_made_and_prepared_afresh(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        prepares = fx.tmp / "prepares.log"
        config = m / ".agents/run-delivery.json"
        settings = json.loads(config.read_text())
        fail = fx.tmp / "fail-once"
        settings["review"]["prepare"] = [["sh", "-c", f"echo run >> {prepares}; test ! -f {fail} || {{ rm {fail}; exit 1; }}"]]
        config.write_text(json.dumps(settings, indent=2))
        commit(m, "FX-1: a prepare step")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        wt = fx.lane("FX-2")
        review = wt / ".orchestration/review/FX-2"
        lenses = fx.lenses(wt)
        short0 = lenses[0][1][0]
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        context = review / "context.md"
        context.write_text(context.read_text().replace(next(line for line in context.read_text().splitlines()
                                                            if line.startswith(CONTEXT_TODO)), "- evidence"))
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        checkout = Path(re.search(r"Your private checkout: `([^`]+)`", (review / f"brief-r1-{short0}.md").read_text()).group(1))
        write_results(review, 1, "FX-2", lenses, findings=[{"id": f"{short0}-r1-1", "severity": "P1", "summary": "fix"}])
        code, out = fx.flow("review-reduce", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        sh("git", "worktree", "remove", "--force", checkout, cwd=wt)
        (wt / "src/one.txt").write_text("two\n")
        commit(wt, "FX-2: two")
        # The first prepare of the new checkout fails part-way; the retry must not take it as prepared.
        fail.write_text("")
        code, out = fx.flow("review-prepare", "FX-2", "--round", "2", cwd=wt)
        self.assertNotEqual(code, 0, out)
        code, out = fx.flow("review-prepare", "FX-2", "--round", "2", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(sh("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip(),
                         sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip())
        self.assertEqual(prepares.read_text().count("run"), len(lenses) + 2,
                         "the missing checkout wasn't prepared again after a failed prepare, or a kept one was")

    def test_what_makes_a_lens_checkout_prepare_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            sh("git", "init", "-q", cwd=r)
            (r / "src").mkdir()
            (r / "src/app.txt").write_text("app\n")
            (r / "tools").mkdir()
            (r / "tools/setup.sh").write_text("true\n")
            commit(r, "base")
            commands = [["sh", "./tools/setup.sh"]]
            fingerprint = lambda cmds=commands: self.call("dependency_fingerprint", "HEAD", cmds, cwd=r)
            before = fingerprint()
            (r / "src/app.txt").write_text("changed\n")
            commit(r, "a source change")
            self.assertEqual(fingerprint(), before, "a source change re-prepares")
            self.assertNotEqual(fingerprint([["sh", "./tools/setup.sh", "--again"]]), fingerprint(),
                                "changed prepare commands don't re-prepare")
            for path in ("tools/setup.sh", "requirements-dev.txt", "web/package.json", "pyproject.toml"):
                last = fingerprint()
                (r / path).parent.mkdir(parents=True, exist_ok=True)
                (r / path).write_text(f"{path} changed\n")
                commit(r, f"change {path}")
                self.assertNotEqual(fingerprint(), last, f"a change to {path} doesn't re-prepare")

    def test_each_round_reads_the_plan_from_the_base_and_a_ruled_round_needs_its_own_commit(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        # The story's plan part mentions the lane's own heading, which a re-seed mustn't take for the lane's.
        plan = m / "docs/plan.md"
        plan.write_text(plan.read_text().replace("- Scenarios: it loads; it fails.\n",
                                                 "- Scenarios: it loads; it fails.\n- Note: `## Evidence so far` heads the lane's part.\n"))
        commit(m, "FX-1: a note in the plan")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        review = wt / ".orchestration/review/FX-2"
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 2, out)
        context = review / "context.md"
        context.write_text(context.read_text().replace(next(line for line in context.read_text().splitlines()
                                                            if line.startswith(CONTEXT_TODO)), "- the lane's own evidence"))
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        base = lambda: sh("git", "rev-parse", "origin/feature/FX-1", cwd=wt).stdout.strip()
        first = base()
        lenses = fx.lenses(wt)
        self.assertIn(f"git show {first}:docs/plan.md", (review / f"brief-r1-{lenses[0][1][0]}.md").read_text())
        # The lead rules on the round's finding without a lane fix.
        short0 = lenses[0][1][0]
        write_results(review, 1, "FX-2", lenses, findings=[{"id": f"{short0}-r1-1", "severity": "P1", "summary": "fix"}], only=0)
        write_results(review, 1, "FX-2", lenses, only=1)
        code, out = fx.flow("review-reduce", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("review-prepare", "FX-2", "--round", "2", cwd=wt)
        self.assertEqual(code, 2, out)
        self.assertIn("HEAD is the candidate of the previous round", out)
        self.assertIn('git commit --allow-empty -m "FX-2: <the ruling>"', out)
        self.assertIn("note the ruling in context.md's Evidence so far", out)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m",
           "FX-2: the lead ruled the finding out of scope", cwd=wt)
        # Then the lead changes the plan on the base, from a clone the lane shares no refs with, after every command that
        # fetches it here: only round 2's own review-prepare can bring it before seeding.
        clone = fx.tmp / "lead-elsewhere"
        sh("git", "clone", "-q", "--branch", "feature/FX-1", fx.origin, clone)
        elsewhere = clone / "docs/plan.md"
        elsewhere.write_text(elsewhere.read_text().replace("- Scenarios: it loads; it fails.\n",
                                                           "- Scenarios: it loads; it fails; NEW-SCENARIO.\n"))
        commit(clone, "FX-1: the plan changes")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=clone)
        code, out = fx.flow("review-prepare", "FX-2", "--round", "2", cwd=wt)
        self.assertEqual(code, 0, out)
        seeded = context.read_text()
        self.assertIn("NEW-SCENARIO", seeded, "round 2's context kept round 1's plan")
        self.assertIn("- the lane's own evidence", seeded, "re-seeding the plan lost the lane's evidence")
        self.assertNotEqual(base(), first, "review-prepare didn't fetch the base before seeding")
        brief = (review / f"brief-r2-{short0}.md").read_text()
        self.assertIn(f"git show {base()}:docs/plan.md", brief)
        self.assertIn("NEW-SCENARIO", brief)
        # Round 1's plan part and header are gone, not kept beside round 2's.
        for text in (seeded, brief):
            self.assertNotIn(first, text, "round 1's base commit is still named")
            self.assertNotIn("- Scenarios: it loads; it fails.\n", text, "round 1's plan line is still there")
            self.assertEqual(text.count("## Ticket contract and plan unit"), 1, text)
            self.assertEqual(text.count("heads the lane's part"), 1, text)
        self.assertIn("Every finding this lens raised in an earlier round that is still open must appear", brief)

    def test_a_plan_line_that_is_the_evidence_heading_isnt_taken_for_the_lanes(self) -> None:
        # Only a top-level story heading lets the plan's part carry a whole `## Evidence so far` line; the lane's own
        # heading comes after it, so a re-seed must cut at the last one.
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        plan = m / "docs/plan.md"
        plan.write_text(plan.read_text().replace("### S1 (FX-2). Thing one (AC1). Regex `/api/one$`\n\n- Scenarios: it loads; it fails.\n",
                                                 "# S1 (FX-2). Thing one (AC1)\n\n- Scenarios: it loads; it fails.\n\n"
                                                 "## Evidence so far\n\n- PLAN-OWN-EVIDENCE\n\n# Rest\n"))
        commit(m, "FX-1: a top-level story heading")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        context = wt / ".orchestration/review/FX-2/context.md"
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 2, out)
        context.write_text(context.read_text().replace(next(line for line in context.read_text().splitlines()
                                                            if line.startswith(CONTEXT_TODO)), "- LANE-EVIDENCE"))
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        seeded = context.read_text()
        self.assertEqual(seeded.count("PLAN-OWN-EVIDENCE"), 1, seeded)
        self.assertEqual(seeded.count("- LANE-EVIDENCE"), 1, seeded)
        self.assertEqual(seeded.count("## Evidence so far\n"), 2, seeded)
        self.assertLess(seeded.index("PLAN-OWN-EVIDENCE"), seeded.rindex("## Evidence so far"), seeded)

    def test_a_context_the_lane_wrote_its_own_way_is_left_as_it_is(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        context = wt / ".orchestration/review/FX-2/context.md"
        context.parent.mkdir(parents=True, exist_ok=True)
        context.write_text("- evidence")
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(context.read_text(), "- evidence", "a context without the evidence heading was re-seeded")

    def test_a_plan_the_base_doesnt_hold_falls_back_to_the_lanes_copy_and_says_no_commit(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        sh("git", "rm", "-q", "docs/plan.md", cwd=m)
        commit(m, "FX-1: the plan moves off the base")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 2, out)
        seeded = (wt / ".orchestration/review/FX-2/context.md").read_text()
        self.assertIn("Delivery base `origin/feature/FX-1`; the plan is `docs/plan.md` (approved, binding).", seeded)
        self.assertNotIn("git show", seeded, "the context names a commit that doesn't hold the plan")
        self.assertIn("- Scenarios: it loads; it fails.", seeded, "the lane's own copy of the plan wasn't read")

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

    def raced_lead(self, fx: Fixture, argv: list[str], writes: int) -> tuple[int, str, list[str]]:
        """Run a lead command in-process, with lane-1 writing its focus just before each of the command's first
        `writes` patches: the record moves between the command's read and its patch, as a supervisor's or a lane's
        write does. Returns (exit code, output, every patch call's first argument)."""
        spec = importlib.util.spec_from_file_location("run_delivery_raced", ROOT / "skills/run-delivery/scripts/run_delivery.py")
        tool = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(tool)
        finally:
            sys.dont_write_bytecode = previous
        real, patches = tool.state_tool, []

        def raced(*args, check=True):
            if args and args[0] == "patch":
                if len(patches) < writes:
                    state = json.loads(real("show").stdout)["state"]
                    if any(lane["id"] == "lane-1" for lane in state["lanes"]):
                        real("focus", "--lane", "lane-1", "--text", f"lane-1 at work ({len(patches) + 1})")
                    else:  # no lane yet: another writer moves the record's headline
                        real("patch", "--writer", "lead", "--version", str(state["version"]), "--patch",
                             json.dumps({"meta": {"headline": f"moved in between ({len(patches) + 1})"}}))
                patches.append(args[0])
            return real(*args, check=check)
        out, code = io.StringIO(), 0
        here = os.getcwd()
        try:
            os.chdir(fx.main)
            with mock.patch.dict(os.environ, fx.env, clear=True), mock.patch.object(tool, "state_tool", raced), \
                    mock.patch.object(tool.signal, "signal"), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                tool.main(argv)
        except SystemExit as stopped:
            code = stopped.code
        finally:
            os.chdir(here)
        return code, out.getvalue(), patches

    def assigned_fixture(self) -> Fixture:
        fx = self.fixture()
        setup_delivery(fx)
        for argv in (["lead-init"], ["lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane 1"]):
            code, out = fx.flow(*argv, cwd=fx.main)
            self.assertEqual(code, 0, out)
        return fx

    def test_a_lead_write_raced_by_another_writer_reads_again_and_keeps_both(self) -> None:
        fx = self.assigned_fixture()
        code, out, patches = self.raced_lead(fx, ["lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2"], writes=1)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(patches), 2, f"the raced patch wasn't tried again from a fresh read: {patches}")
        lanes = {lane["id"]: lane for lane in json.loads((fx.tmp / "record/state.json").read_text())["lanes"]}
        self.assertEqual((lanes["lane-2"]["state"], lanes["lane-2"]["focus"]), ("assigned", "assigned FX-3"))
        self.assertEqual(lanes["lane-1"]["focus"], "lane-1 at work (1)", "the write in between was overwritten")

    def test_lead_init_raced_by_another_writer_reads_again_and_keeps_both(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        code, out, patches = self.raced_lead(fx, ["lead-init"], writes=1)
        self.assertEqual(code, 0, out)
        state = json.loads((fx.tmp / "record/state.json").read_text())
        self.assertEqual(state["meta"]["headline"], "moved in between (1)", "the write in between was overwritten")
        self.assertEqual(state["meta"]["trackerParent"], "FX-1")
        self.assertEqual({p["subject"] for p in state["planned"]}, {"FX-2", "FX-3"})
        self.assertEqual(len(patches), 2, patches)

    def test_a_story_registered_under_a_race_is_planned_and_journalled_once(self) -> None:
        fx = self.assigned_fixture()
        path = fx.main / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["stories"]["FX-4"] = {**{k: v for k, v in d["stories"]["FX-3"].items() if k != "inApp"}, "title": "Thing four",
                                "lane": "lane-1"}
        path.write_text(json.dumps(d, indent=2))
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text() + "\n### S4 (FX-4). Thing four\n\n- Scenarios: it four.\n")
        commit(fx.main, "FX-1: a story added")
        # The race lands on the first patch, register_stories' own, so its retry runs from a fresh read.
        code, out, patches = self.raced_lead(fx, ["lead-assign", "FX-4", "--lane", "lane-1", "--title", "Lane 1"], writes=1)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(patches), 3, f"register_stories' raced patch wasn't tried again: {patches}")
        record = json.loads((fx.tmp / "record/state.json").read_text())
        self.assertEqual([p["subject"] for p in record["planned"]].count("FX-4"), 1, record["planned"])
        journal = [json.loads(line) for line in (fx.tmp / "record/journal.jsonl").read_text().splitlines()]
        added = [e for e in journal if e.get("subject") == "FX-4" and "added to the delivery as planned work" in e["summary"]]
        self.assertEqual(len(added), 1, added)

    def test_a_lead_write_that_never_lands_says_so_after_its_bound(self) -> None:
        fx = self.assigned_fixture()
        code, out, patches = self.raced_lead(fx, ["lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2"], writes=99)
        self.assertEqual(code, 2, out)
        self.assertIn("version_stale: the record kept changing while lane-2's assignment was written; tried 5 times", out)
        self.assertEqual(len(patches), 5, patches)
        lanes = {lane["id"] for lane in json.loads((fx.tmp / "record/state.json").read_text())["lanes"]}
        self.assertNotIn("lane-2", lanes, "a patch from a stale read was written")

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
        span = f"{sh('git', 'rev-parse', 'HEAD~1', cwd=wt).stdout.strip()}..{sh('git', 'rev-parse', 'HEAD', cwd=wt).stdout.strip()}"
        expect(f"the files `git diff {span}` changes, and the tests that cover the rest: `src/one.txt`."
               in (review / "brief-r2-adversarial-testing.md").read_text(), "round 2's scoped files aren't its own delta")
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

    # -- ship: the product-only branch ---------------------------------------------------------------------

    def ship_fixture(self, **ship) -> tuple[Fixture, Path]:
        """A delivery whose base is ready to ship, with `ship` overridden by `ship`, and a checkout of `main` beside."""
        fx = self.fixture()
        path = setup_delivery(fx)
        if ship:
            d = json.loads(path.read_text())
            d["ship"].update(ship)
            path.write_text(json.dumps(d, indent=2))
            commit(fx.main, "FX-1: ship settings")
            sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        main = fx.tmp / "default"
        sh("git", "worktree", "add", "-q", main, "main", cwd=fx.main)
        return fx, main

    def on(self, checkout: Path, branch: str, files: dict[str, str], message: str) -> str:
        for rel, text in files.items():
            (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
            (checkout / rel).write_text(text)
        commit(checkout, message)
        sh("git", "push", "-q", "origin", branch, cwd=checkout)
        return sh("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()

    def shipped(self, fx: Fixture) -> tuple[list[str], dict[str, str]]:
        """The ship checkout's staged files, and their staged contents."""
        path = fx.main / "lanes/ship-FX-1"
        names = sh("git", "diff", "--cached", "--name-only", cwd=path).stdout.split()
        return names, {n: sh("git", "show", f":{n}", cwd=path).stdout for n in names}

    def test_ship_names_the_integration_review_before_the_default_branch(self) -> None:
        fx, _ = self.ship_fixture()
        m = fx.main
        self.on(m, "feature/FX-1", {"src/one.txt": "one\n"}, "FX-2: one")
        step = "review the integration with `$review-work` before it reaches the default branch"
        for options in ((), ("--no-gates",)):
            with self.subTest(options=options):
                code, out = fx.flow("ship", *options, cwd=m)
                # The next run builds the branch afresh.
                sh("git", "worktree", "remove", "--force", m / "lanes/ship-FX-1", cwd=m, check=False)
                sh("git", "branch", "-D", "ship/FX-1", cwd=m, check=False)
                self.assertEqual(code, 0, out)
                said = out.splitlines()[-1]
                self.assertIn(step, said)
                # Named between committing the change and its PR, the order the guide's close-out gives.
                self.assertLess(said.index("commit"), said.index(step))
                self.assertLess(said.index(step), said.index("Its PR waits for the user"))

    def test_ship_leaves_out_tooling_changed_after_the_tooling_commit(self) -> None:
        for whole in (".", "./"):
            with self.subTest(paths=whole):
                fx, main = self.ship_fixture(paths=[whole], gateTooling=["tools/gate-extra.sh"])
                m = fx.main
                # The lead's tooling spans three commits, and the last is the one ship names.
                self.on(m, "feature/FX-1", {"tools/first.sh": "echo first\n"}, "FX-1: first tooling")
                tooling = self.on(m, "feature/FX-1", {"tools/lead.sh": "echo lead\n"}, "FX-1: more tooling")
                d = json.loads((m / ".agents/deliveries/FX-1.json").read_text())
                d["ship"]["toolingCommit"] = tooling[:12]
                (m / ".agents/deliveries/FX-1.json").write_text(json.dumps(d, indent=2))
                # Later, a product change, and tooling changed again: the run-delivery config, a settings file no
                # tooling commit touched, both tooling commits' own files, a declared gate tool, and a generation.
                conf = json.loads((m / ".agents/run-delivery.json").read_text())
                conf["gates"].append({"id": "later", "argv": ["true"]})
                self.on(m, "feature/FX-1", {"src/one.txt": "one\n", "tools/lead.sh": "echo lead, changed\n",
                                            "tools/first.sh": "echo first, changed\n", "tools/gate-extra.sh": "true\n",
                                            ".agents/run-delivery.json": json.dumps(conf, indent=2),
                                            ".agents/later.json": "{}\n", ".agent-skills/active.json": "{}\n"},
                        "FX-1: product and tooling")
                code, out = fx.flow("ship", "--no-gates", cwd=m)
                self.assertEqual(code, 0, out)
                names, _ = self.shipped(fx)
                self.assertEqual(names, ["src/one.txt"], out)
                self.assertIn("left out the delivery's tooling: ", out)
                self.assertIn("tools/lead.sh", out.split("left out the delivery's tooling: ", 1)[1].splitlines()[0])
                self.assertIn("changed after the tooling commit and left out: tools/first.sh, tools/lead.sh; "
                              "a product change there doesn't ship", out)

    def test_ship_reports_a_failed_apply_that_staged_nothing(self) -> None:
        fx, main = self.ship_fixture()
        sh("git", "rm", "-q", "src/app.txt", cwd=main)
        self.on(main, "main", {}, "the default branch drops the file the delivery changes")
        self.on(fx.main, "feature/FX-1", {"src/app.txt": "app, changed by the delivery\n"}, "FX-2: app")
        code, out = fx.flow("ship", "--no-gates", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("git apply failed", out)
        self.assertNotIn("nothing to ship", out)
        self.assertTrue((fx.main / "lanes/ship-FX-1").exists(), "a failed apply's checkout was removed before it was seen")

    def test_ship_refuses_a_branch_with_nothing_staged(self) -> None:
        fx, main = self.ship_fixture()
        self.on(fx.main, "feature/FX-1", {"src/one.txt": "one\n"}, "FX-2: one")
        self.on(main, "main", {"src/one.txt": "one\n"}, "the same change, already on the default branch")
        code, out = fx.flow("ship", "--no-gates", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("nothing to ship", out)
        self.assertNotIn("next:", out)
        # Nothing is left to remove by hand before it runs again.
        self.assertFalse((fx.main / "lanes/ship-FX-1").exists(), "the empty ship checkout was left behind")
        self.assertEqual(sh("git", "branch", "--list", "ship/FX-1", cwd=fx.main).stdout.strip(), "")

    def test_ship_builds_from_a_base_that_merged_the_default_branch(self) -> None:
        fx, main = self.ship_fixture()
        m = fx.main
        # Tooling outside the ship paths (src/), named by ship and changed later: nothing under the paths is lost.
        tooling = self.on(m, "feature/FX-1", {"tools/lead.sh": "echo lead\n"}, "FX-1: more tooling")
        d = json.loads((m / ".agents/deliveries/FX-1.json").read_text())
        d["ship"]["toolingCommit"] = tooling[:12]
        (m / ".agents/deliveries/FX-1.json").write_text(json.dumps(d, indent=2))
        self.on(m, "feature/FX-1", {"src/one.txt": "one\n", "tools/lead.sh": "echo lead, changed\n"}, "FX-2: one")
        self.on(main, "main", {"src/main-only.txt": "main\n", "src/app.txt": "app, on main\n"}, "the default branch moves")
        sh("git", "fetch", "-q", "origin", cwd=m)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "--no-ff", "-m", "merge main", "origin/main", cwd=m)
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        self.on(m, "feature/FX-1", {"src/two.txt": "two\n", "src/app.txt": "app, on main\nand the delivery\n"}, "FX-3: two")
        code, out = fx.flow("ship", "--no-gates", cwd=m)
        self.assertEqual(code, 0, out)
        names, staged = self.shipped(fx)
        self.assertEqual(names, ["src/app.txt", "src/one.txt", "src/two.txt"], out)
        self.assertEqual(staged["src/app.txt"], "app, on main\nand the delivery\n")
        self.assertNotIn("changed after the tooling commit", out, "a later tooling change outside the ship paths was named")

    def test_cleanup_takes_a_story_its_forge_merged_by_squash_only_at_or_below_the_merged_head(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=m); expect(code == 0, out)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        sh("git", "push", "-q", "origin", "feature/FX-2", cwd=wt)
        code, out = fx.flow("open-pr", "FX-2", "--title", "FX-2: one", "--body-file", "/dev/null", cwd=wt); expect(code == 0, out)
        head = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        (wt / ".orchestration/review/FX-2").mkdir(parents=True, exist_ok=True)
        (wt / ".orchestration/review/FX-2/gates.json").write_text("{}")
        record = json.loads((fx.tmp / "record/state.json").read_text())
        record["queue"].append({"subject": "FX-2", "title": "one", "lane": "lane-1", "stage": "merged", "head": head,
                                "syncedHead": head, "changeRequest": "https://forge.test/pr/1", "checks": [], "runId": None,
                                "order": 1, "leadInputs": [], "reportedRounds": 1})
        record["planned"] = [i for i in record.get("planned", []) if i["subject"] != "FX-2"]
        (fx.tmp / "record/state.json").write_text(json.dumps(record))  # a fixture shortcut for "it merged"
        # The squash: the story's change lands on the base as a commit of its own, so no tip of the story is on it.
        sh("git", "fetch", "-q", "origin", cwd=m)
        (m / "src/one.txt").write_text("one\n")
        commit(m, "FX-2: one (#1)")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        calls = fx.tmp / "forge-calls.txt"
        fx.env["FAKE_FORGE_CALLS"] = str(calls)

        def forge_says(**pr) -> None:
            prs = json.loads((fx.tmp / "forge.json").read_text())
            prs["https://forge.test/pr/1"].update(pr)
            (fx.tmp / "forge.json").write_text(json.dumps(prs))

        def untouched(out: str) -> None:
            expect(wt.exists(), f"the checkout was removed: {out}")
            expect(sh("git", "branch", "--list", "feature/FX-2", cwd=m).stdout.strip(), f"the local branch was deleted: {out}")
            expect(not (fx.tmp / "evidence/FX-2").exists(), f"evidence was moved for a story left in place: {out}")

        for state in ("open", "closed"):
            forge_says(state=state)
            code, out = fx.flow("lead-cleanup", "FX-2", cwd=m)
            expect(code == 1 and "FX-2: unmerged" in out and f"https://forge.test/pr/1 is {state}, not merged" in out,
                   f"the story of a request {state} was taken as merged: {out}")
            untouched(out)
        # Merged, with the checkout one commit past the head the forge merged: that commit is on no base.
        forge_says(state="merged", head=head)
        (wt / "src/one.txt").write_text("one, after the merge\n")
        commit(wt, "FX-2: after the merge")
        later = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()[:10]
        code, out = fx.flow("lead-cleanup", "FX-2", cwd=m)
        expect(code == 1 and "FX-2: unmerged" in out
               and f"the checkout's HEAD ({later}), local feature/FX-2 ({later}) are past "
                   f"https://forge.test/pr/1's merged head {head[:10]}" in out, f"a commit past the merged head was lost: {out}")
        untouched(out)
        # Back at the merged head, but the remote branch carries a commit past it.
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-2", cwd=wt)
        sh("git", "reset", "-q", "--hard", head, cwd=wt)
        code, out = fx.flow("lead-cleanup", "FX-2", cwd=m)
        expect(code == 1 and f"left: origin/feature/FX-2 ({later}) is past https://forge.test/pr/1's merged head "
               f"{head[:10]}" in out, f"the remote's later commit was missed: {out}")
        untouched(out)
        sh("git", "push", "-q", "-f", "origin", f"{head}:refs/heads/feature/FX-2", cwd=wt)
        # A dirty checkout is still refused, however its story merged.
        (wt / "stray.txt").write_text("x\n")
        code, out = fx.flow("lead-cleanup", "FX-2", cwd=m)
        expect(code == 1 and "FX-2: refused" in out and "stray.txt" in out, f"removed a dirty checkout: {out}")
        untouched(out)
        (wt / "stray.txt").unlink()
        # A forge that can't say leaves the story where it is, and a sweep goes on past it.
        def record_request(url: str) -> None:
            doc = json.loads((fx.tmp / "record/state.json").read_text())
            next(q for q in doc["queue"] if q["subject"] == "FX-2")["changeRequest"] = url
            (fx.tmp / "record/state.json").write_text(json.dumps(doc))
        record_request("https://forge.test/pr/9")
        code, out = fx.flow("lead-cleanup", "FX-2", cwd=m)
        expect(code == 1 and "FX-2: unmerged" in out and "couldn't read https://forge.test/pr/9" in out, out)
        untouched(out)
        code, out = fx.flow("lead-cleanup", cwd=m)
        expect(code == 0 and "FX-2: unmerged" in out and "FX-3: nothing to clean" in out, f"the sweep stopped: {out}")
        untouched(out)
        record_request("https://forge.test/pr/1")
        # A forge adapter with no `get` is never asked, and the story stays unmerged as before.
        flow_json = m / ".agents/run-delivery.json"
        kept = flow_json.read_bytes()
        flow_json.write_text(json.dumps({**json.loads(kept), "forge": {"adapter": "none"}}))
        calls.write_text("")
        code, out = fx.flow("lead-cleanup", "FX-2", cwd=m)
        flow_json.write_bytes(kept)
        expect(code == 1 and "FX-2: unmerged" in out and "https://forge.test/pr/1" not in out and not calls.read_text(),
               f"a forge with no get was asked, or named: {out}")
        untouched(out)
        # The forge's head may be abbreviated, as the fake's and Bitbucket's are.
        forge_says(state="merged", head=head[:12])
        code, out = fx.flow("lead-cleanup", "FX-2", cwd=m)
        expect(code == 0 and "FX-2: cleaned" in out and not wt.exists(), out)
        expect(f"https://forge.test/pr/1 merged at {head[:10]}" in out, f"the cleanup didn't say why it counted as merged: {out}")
        expect(not sh("git", "branch", "--list", "feature/FX-2", cwd=m).stdout.strip(), "the local branch survived")
        expect((fx.tmp / "evidence/FX-2/gates.json").exists(), "the lane's evidence wasn't kept")
        expect(remote_has(m, "feature/FX-2"), "the remote branch was deleted without the repository allowing it")
        cleaned = [e for e in map(json.loads, (fx.tmp / "record/journal.jsonl").read_text().splitlines())
                   if e.get("kind") == "decision.recorded" and e.get("subject") == "FX-2"]
        expect(cleaned and "https://forge.test/pr/1 merged at" in cleaned[-1]["summary"], cleaned)
        # Run again, with the remote branch still there: cleaned already, so nothing is said or journalled twice.
        code, out = fx.flow("lead-cleanup", "FX-2", cwd=m)
        again = [e for e in map(json.loads, (fx.tmp / "record/journal.jsonl").read_text().splitlines())
                 if e.get("kind") == "decision.recorded" and e.get("subject") == "FX-2"]
        expect(code == 0 and "FX-2: cleaned" not in out and len(again) == len(cleaned), f"cleaned twice: {out}")
        # A story whose tips are on the base is cleaned by ancestry, with no forge call, as before.
        wt3 = fx.lane("FX-3")
        (wt3 / "src/two.txt").write_text("two\n")
        commit(wt3, "FX-3: two")
        sh("git", "push", "-q", "origin", "feature/FX-3", cwd=wt3)
        code, out = fx.flow("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", "/dev/null", cwd=wt3); expect(code == 0, out)
        # Its request is on record, so only the ancestry path keeps the forge out of it.
        record = json.loads((fx.tmp / "record/state.json").read_text())
        record["queue"].append({**record["queue"][-1], "subject": "FX-3", "title": "two", "lane": "lane-2",
                                "changeRequest": "https://forge.test/pr/2", "order": 2})
        record["planned"] = [i for i in record.get("planned", []) if i["subject"] != "FX-3"]
        (fx.tmp / "record/state.json").write_text(json.dumps(record))
        # Before it merges, FX-3 is judged by its own open request, not by FX-2's merged one ahead of it in the queue.
        code, out = fx.flow("lead-cleanup", "FX-3", cwd=m)
        expect(code == 1 and "https://forge.test/pr/2 is open, not merged" in out and "pr/1" not in out and wt3.exists(), out)
        sh("git", "fetch", "-q", "origin", cwd=m)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "--no-ff", "-m", "merge FX-3", "origin/feature/FX-3", cwd=m)
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        calls.write_text("")
        code, out = fx.flow("lead-cleanup", "FX-3", cwd=m)
        expect(code == 0 and "FX-3: cleaned" in out and not wt3.exists(), out)
        expect("get" not in calls.read_text().split(), f"a story on the base asked the forge: {calls.read_text()}")

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
        # lead-close refuses while the lead's settings have uncommitted changes.
        code, out = flow_run("lead-close", cwd=m)
        expect(code == 2 and "the lead's settings have uncommitted changes" in out and ".agents/run-delivery.json" in out,
               f"closed over uncommitted settings: {out}")
        commit(m, "FX-1: clean up remote branches, archive in the fixture")
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
        ours = subprocess.Popen(sweep + ["lead-run"], cwd=m)
        theirs = subprocess.Popen(sweep + ["--delivery", "OTHER-9", "lead-run"], cwd=m)
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

    def test_the_leads_ready_grant_is_left_out_of_the_brief_and_open_pr_still_opens_a_draft(self) -> None:
        fx = self.fixture()
        m = fx.main
        path = setup_delivery(fx)
        d = json.loads(path.read_text())
        d["authority"]["readyPullRequests"] = True
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: the lead may mark drafts ready")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        calls = fx.tmp / "forge-calls.txt"
        fx.env["FAKE_FORGE_CALLS"] = str(calls)
        code, out = fx.flow("lead-init", cwd=m); expect(code == 0, out)
        code, out = fx.flow("lead-brief", cwd=m); expect(code == 0, out)
        brief = " ".join((m / "docs/lane-brief.md").read_text().split())
        authority = brief[brief.index("**Authority.**"):][:900]
        self.assertNotIn("readyPullRequests", brief)
        self.assertIn("never mark a pull request ready or approve one", authority)
        self.assertIn("open its draft pull request into `feature/FX-1`", authority)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        code, out = fx.flow("push", "FX-2", cwd=wt); expect(code == 0, out)
        body = fx.tmp / "body.md"
        body.write_text("x\n")
        code, out = fx.flow("open-pr", "FX-2", "--title", "FX-2: one", "--body-file", body, cwd=wt)
        self.assertEqual(code, 0, out)
        asked = calls.read_text().split()
        self.assertIn("open", asked)
        self.assertNotIn("ready", asked, "open-pr marked the pull request ready under the lead's grant")
        # The lead's grant alone gives lanes nothing, and their brief says marking ready is not theirs either.
        d = json.loads(path.read_text())
        d["authority"] = {"readyPullRequests": True, "grantedBy": "the operator", "grantedOn": "2026-11-09"}
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: only the lead's grant")
        code, out = fx.flow("lead-brief", cwd=m); expect(code == 0, out)
        brief = " ".join((m / "docs/lane-brief.md").read_text().split())
        self.assertIn("Lanes have no authority to push, open pull requests or mark one ready in this delivery", brief)

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
        # And lead-assign, for a story assigned before any sweep, once the plan has its section.
        d["stories"]["FX-6"] = {"title": "Thing six", "lane": "lane-2"}
        path.write_text(json.dumps(d, indent=2))
        plan = m / "docs/plan.md"
        plan.write_text(plan.read_text() + "\n### S6 (FX-6). Thing six\n\n- Scenarios: it six.\n")
        code, out = flow_run("lead-assign", "FX-6", "--lane", "lane-2", "--title", "six", cwd=m)
        expect(code == 0 and any(p["subject"] == "FX-6" for p in show()["state"]["planned"]),
               f"lead-assign didn't register it: {out}")

    def test_lanes_read_the_delivery_from_the_base_not_their_checkout(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        path = setup_delivery(fx)
        (m / ".agents/orchestration.json").write_text(json.dumps({"base": {"remote": "origin", "ref": "feature/FX-1"},
                                                                  "state": {"forgeSnapshot": {"argv": ["true"]}}}))
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

    def test_the_evidence_pages_notes_are_recorded_so_a_rebuild_keeps_them(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        record = fx.tmp / "record"
        page, again = fx.tmp / "page", fx.tmp / "page-again"
        code, out = fx.flow("lead-evidence", "page", "FX-2", "FX-3", "--out", page, "--title", "FX-1 proof",
                            "--intro", "What each story shows.", "--note", "FX-2=Covered by its hold check",
                            "--note", "FX-3=Nothing visible changes: the copy is server-side", cwd=m)
        self.assertEqual(code, 0, out)
        first = (page / "index.html").read_text()
        for text in ("FX-1 proof", "What each story shows.", "Covered by its hold check", "Nothing visible changes: the copy is server-side"):
            self.assertIn(text, first)
        kept = record.parent / "evidence-notes.json"
        before = kept.read_bytes()
        # After a compaction the lead has none of it in mind: the same page, rebuilt from the record alone.
        code, out = fx.flow("lead-evidence", "page", "FX-2", "FX-3", "--out", again, cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual((again / "index.html").read_text(), first, "a rebuild without the notes lost some")
        self.assertIn("reused the recorded note(s) for FX-2, FX-3", out)
        self.assertNotIn("recorded the page's words", out)
        self.assertEqual(kept.read_bytes(), before, "a rebuild that changed nothing rewrote the kept words")
        # A new note replaces its story's, and the other story's kept note stands.
        code, out = fx.flow("lead-evidence", "page", "FX-2", "FX-3", "--out", again, "--note", "FX-3=Server-side only", cwd=m)
        self.assertEqual(code, 0, out)
        html = (again / "index.html").read_text()
        self.assertIn("Server-side only", html)
        self.assertNotIn("the copy is server-side", html)
        self.assertIn("Covered by its hold check", html, "a note for one story dropped another story's kept note")
        # An empty note removes its story's, and the rest stands.
        code, out = fx.flow("lead-evidence", "page", "FX-2", "FX-3", "--out", again, "--note", "FX-2=", cwd=m)
        self.assertEqual(code, 0, out)
        html = (again / "index.html").read_text()
        self.assertNotIn("Covered by its hold check", html)
        self.assertIn("No recording: this story has no visible loading or failure state to show.", html)
        self.assertIn("Server-side only", html, "removing one note dropped another")
        self.assertIn("FX-1 proof", html, "a note change dropped the recorded title")
        # What's published carries the words the published folder was built with, even after a later draft changed
        # the kept ones.
        draft = fx.tmp / "page-draft"
        code, out = fx.flow("lead-evidence", "page", "FX-3", "--out", draft, "--note", "FX-3=Trying other wording", cwd=m)
        self.assertEqual(code, 0, out)
        (again / "videos/FX-9-clip.mp4").write_bytes(b"a clip, as far as published can tell")
        code, out = fx.flow("lead-evidence", "published", "--url", "https://pages.test/e", "--out", again, cwd=m)
        self.assertEqual(code, 0, out)
        published = json.loads((record.parent / "evidence-page.json").read_text())
        self.assertEqual({k: published.get(k) for k in ("notes", "title", "intro")},
                         {"notes": {"FX-3": "Server-side only"}, "title": "FX-1 proof", "intro": "What each story shows."})
        # A note that isn't KEY=TEXT, or names no story of the delivery, is refused and records nothing.
        before = (record.parent / "evidence-notes.json").read_text()
        for bad in ("FX-3", "FX-3 has no equals sign", "FX-99=not a story"):
            with self.subTest(note=bad):
                code, out = fx.flow("lead-evidence", "page", "FX-3", "--out", again, "--note", bad, cwd=m)
                self.assertEqual(code, 2, out)
                self.assertIn("--note", out)
                self.assertEqual((record.parent / "evidence-notes.json").read_text(), before)

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

    def reduced_round_one(self, fx: Fixture, run_id: str | None = None) -> tuple[Path, Path, list]:
        """FX-3 assigned and reviewed once, with one open P1, and not handed off: (lane checkout, review folder, lenses).
        With a run id, FX-3's planned work names that product run before the round is reduced."""
        m = fx.main
        setup_delivery(fx)
        fx.flow("lead-init", cwd=m)
        fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        wt = fx.lane("FX-3")
        (wt / "src/two.txt").write_text("two\n")
        commit(wt, "FX-3: two")
        review = wt / ".orchestration/review/FX-3"
        review.mkdir(parents=True)
        (review / "context.md").write_text("- evidence")
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt); expect(code == 0, out)
        lenses = fx.lenses(wt)
        short = lenses[0][1][0]
        write_results(review, 1, "FX-3", lenses, findings=[{"id": f"{short}-r1-1", "severity": "P1",
                                                            "summary": "the empty cart is not handled"}])
        if run_id is not None:
            record = fx.tmp / "record"
            version = str(self.shown(record)["state"]["version"])
            state_script = self.generation / "skills/orchestrate-work/scripts/delivery_state.py"
            sh(sys.executable, "-B", state_script, "--dir", record, "patch", "--writer", "lead", "--version", version,
               "--element", "planned[subject=FX-3]", "--patch", json.dumps({"runId": run_id}))
        code, out = fx.flow("review-reduce", "FX-3", "--round", 1, cwd=wt)
        expect(code == 0 and "unresolved" in out, out)
        return wt, review, lenses

    def test_a_story_with_a_product_run_has_its_round_journaled_and_nothing_captured(self) -> None:
        fx = self.fixture()
        self.reduced_round_one(fx, run_id="run-1")
        shown = self.shown(fx.tmp / "record")
        kinds = [e["kind"] for e in shown["journal"] if e.get("subject") == "FX-3"
                 and e["kind"] in ("review.round", "review.evidence")]
        expect(kinds == ["review.round"], f"a run-backed round was recorded as {kinds}")
        expect("FX-3" not in shown.get("reviewEvidence", {}), "a run-backed story got native evidence")

    def shown(self, record: Path) -> dict:
        state_script = self.generation / "skills/orchestrate-work/scripts/delivery_state.py"
        return json.loads(sh(sys.executable, "-B", state_script, "--dir", record, "show").stdout)

    def test_each_reduced_round_reaches_the_live_view_before_hand_off(self) -> None:
        from tests.delivery_views_fixtures import render
        fx = self.fixture()
        wt, review, lenses = self.reduced_round_one(fx)
        record = fx.tmp / "record"
        first = json.loads((review / "round-1.json").read_text())["entries"][0]["binding"]["candidateRef"]
        shown = self.shown(record)
        expect(any(i["subject"] == "FX-3" for i in shown["state"]["planned"]), "FX-3 was handed off already")
        observation = shown["reviewEvidence"]["FX-3"]
        expect("unavailable" not in observation and observation["candidateRef"] == first, f"round 1 isn't shown: {observation}")
        card = render.native_review_summaries(shown)["FX-3"]["roundDetail"]
        expect([r["round"] for r in card] == [1] and card[0]["raised"][0]["summary"] == "the empty cart is not handled",
               f"the round's card lacks its note: {card}")
        kinds = lambda doc: [(e["kind"], e.get("lane")) for e in doc["journal"] if e.get("subject") == "FX-3"
                             and e["kind"] in ("review.round", "review.evidence")]
        expect(kinds(shown) == [("review.round", "lane-2"), ("review.evidence", "lane-2")], f"round 1 recorded as {kinds(shown)}")
        # A re-run writes nothing more; a round whose capture failed is finished by one.
        code, out = fx.flow("review-reduce", "FX-3", "--round", 1, cwd=wt); expect(code == 0, out)
        expect(kinds(self.shown(record)) == kinds(shown), "a second reduce wrote to the record again")
        (wt / "src/two.txt").write_text("two, with an empty cart\n")
        commit(wt, "FX-3: empty cart")
        code, out = fx.flow("review-prepare", "FX-3", "--round", 2, cwd=wt); expect(code == 0, out)
        short = lenses[0][1][0]
        write_results(review, 2, "FX-3", lenses, resolved=[{"id": f"{short}-r1-1", "state": "fixed", "resolution": "handled"}])
        # The capture fails after the round is journaled: the live view shows it stale, and a re-run finishes it.
        artifacts, aside = record / "review-artifacts", fx.tmp / "artifacts-aside"
        artifacts.rename(aside)
        artifacts.symlink_to(aside)
        code, out = fx.flow("review-reduce", "FX-3", "--round", 2, cwd=wt)
        expect(code == 2 and (review / "round-2.json").exists(), f"a failed capture went unreported: {out}")
        expect(kinds(self.shown(record)) == [("review.round", "lane-2"), ("review.evidence", "lane-2"), ("review.round", "lane-2")],
               f"the round wasn't journaled before its capture: {kinds(self.shown(record))}")
        expect(self.shown(record)["reviewEvidence"]["FX-3"].get("unavailable"), "an uncaptured round left round 1 current")
        artifacts.unlink()
        aside.rename(artifacts)
        code, out = fx.flow("review-reduce", "FX-3", "--round", 2, cwd=wt); expect(code == 0 and "aligned" in out, out)
        shown = self.shown(record)
        second = json.loads((review / "round-2.json").read_text())["entries"][0]["binding"]["candidateRef"]
        expect(second != first and shown["reviewEvidence"]["FX-3"]["candidateRef"] == second,
               f"round 2 isn't bound to its own candidate: {shown['reviewEvidence']['FX-3']}")
        expect([r["outcome"] for r in render.native_review_summaries(shown)["FX-3"]["roundDetail"]] == ["changes", "approve"],
               "the live view doesn't show both rounds")
        expect(kinds(shown) == [("review.round", "lane-2"), ("review.evidence", "lane-2")] * 2, f"rounds recorded as {kinds(shown)}")

    def served_document(self, argv: list, cwd: Path, env: dict, expect_record: Path) -> dict:
        """Start the tool with these arguments, a `view`, check its first two lines, and return /document; it is stopped by pid."""
        import urllib.request
        served = subprocess.Popen([sys.executable, "-B", str(self.tool), *map(str, argv)], cwd=cwd, env=env,
                                  text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            address = served.stdout.readline().strip()
            expect(re.fullmatch(r"http://127\.0\.0\.1:\d+/", address), f"the first line isn't the address: {address}")
            expect(str(expect_record) in served.stdout.readline(), "it doesn't say which record it serves")
            with urllib.request.urlopen(address, timeout=10) as answer:
                expect(answer.status == 200 and b"FX-3" in answer.read(), "the page isn't served")
            with urllib.request.urlopen(address + "document", timeout=10) as answer:
                return json.loads(answer.read())
        finally:
            served.send_signal(signal.SIGTERM)
            served.communicate(timeout=10)

    def test_view_serves_the_live_record_from_any_checkout_of_the_repository(self) -> None:
        fx = self.fixture()
        wt, _, _ = self.reduced_round_one(fx)
        record = fx.tmp / "record"
        for where in (wt, fx.main):
            with self.subTest(where=where.name):
                argv = ["--delivery", "FX-1", "view"] if where == fx.main else ["view"]
                document = self.served_document(argv, where, fx.env, record)
                rounds = [e["review"]["round"] for e in document["journal"]
                          if e["kind"] == "review.round" and e["subject"] == "FX-3"]
                expect(rounds == [1] and "unavailable" not in document["reviewEvidence"]["FX-3"],
                       f"the live record's round isn't served: {rounds}, {document.get('reviewEvidence')}")
        code, out = fx.flow("view", cwd=wt, env={**fx.env, "DELIVERY_STATE": str(fx.tmp / "gone")})
        expect(code == 2 and "no record at" in out and "--archive" in out, f"a missing record was served: {out}")
        # The binding's state member picks the views; its publishers never run from a view.
        published = fx.tmp / "published"
        (wt / ".agents/orchestration.json").write_text(json.dumps({"state": {
            "renderers": [{"id": "notes", "builtin": "markdown", "output": "lead-notes.md"}],
            "publishers": [{"id": "post", "argv": ["sh", "-c", f"touch {published}"]}]}}))
        self.served_document(["view"], wt, fx.env, record)
        expect((record / "lead-notes.md").is_file(), "the binding's own view wasn't rendered")
        expect(not published.exists(), "a view ran the binding's publisher")

    def test_view_serves_the_latest_copy_once_the_leads_checkout_is_gone(self) -> None:
        fx = self.fixture()
        self.reduced_round_one(fx)
        record = fx.tmp / "record"
        latest = fx.tmp / "archive" / "latest"
        latest.mkdir(parents=True)
        shutil.copytree(record, latest / "record")
        (latest / "flow-log").mkdir()
        before = sorted(p.name for p in (latest / "record").iterdir())
        shutil.rmtree(fx.main)
        shutil.rmtree(record)
        outside = fx.tmp / "elsewhere"
        outside.mkdir()
        for path in (latest, latest / "record"):
            with self.subTest(path=path.name):
                document = self.served_document(["view", "--archive", path], outside, fx.env, latest / "record")
                rounds = [e["review"]["round"] for e in document["journal"]
                          if e["kind"] == "review.round" and e["subject"] == "FX-3"]
                expect(rounds == [1], f"the copy's latest round isn't served: {rounds}")
                expect("unavailable" not in document["reviewEvidence"]["FX-3"], "the copy's round has no evidence")
        expect(sorted(p.name for p in (latest / "record").iterdir()) == sorted({*before, "state.lock"}),
               "serving the copy rendered views into it")
        code, out = fx.flow("view", "--archive", latest / "flow-log", cwd=outside)
        expect(code == 2 and "holds no record" in out, f"a folder without a record was served: {out}")
        shutil.copytree(latest / "record", latest / "another")
        code, out = fx.flow("view", "--archive", latest, cwd=outside)
        expect(code == 2 and "several records (another, record)" in out, f"one of several records was served: {out}")

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
        # A half-done init is still a record: lead-init names it, and --replace starts again, archiving inside the
        # fixture.
        conf_path = m / ".agents/run-delivery.json"
        conf_path.write_text(json.dumps({**json.loads(conf_path.read_text()),
                                         "cleanup": {"archive": str(fx.tmp / "archive/{delivery}")}}, indent=2))
        shutil.rmtree(fx.tmp / "record")
        fx.call("state_tool", "init", "--headline", "half", cwd=m)
        code, out = flow_run("lead-init", cwd=m)
        expect(code == 2 and "delivery half" in out and "lead-init --replace" in out, f"a half-done init wasn't named: {out}")
        code, out = flow_run("lead-init", "--replace", cwd=m); expect(code == 0, f"lead-init --replace didn't start again: {out}")

    def test_a_merge_outside_the_queue_can_still_take_the_leads_decisions_once(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        path = plan_delivery(fx)
        delivery = json.loads(path.read_text())
        delivery["authority"]["readyPullRequests"] = True
        path.write_text(json.dumps(delivery, indent=2))
        commit(m, "FX-1: the lead may mark drafts ready")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        flow_run("lead-init", cwd=m)
        flow_run("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        wt = fx.lane("FX-3")
        (wt / "src/two.txt").write_text("two\n")
        (wt / "src/one.txt").write_text("outside the lane\n")
        commit(wt, "FX-3: two, and a file outside the lane")
        (wt / ".orchestration/review/FX-3").mkdir(parents=True)
        (wt / ".orchestration/review/FX-3/context.md").write_text("- evidence")
        flow_run("gate", "FX-3", cwd=wt)
        flow_run("review-prepare", "FX-3", "--round", 1, cwd=wt)
        write_results(wt / ".orchestration/review/FX-3", 1, "FX-3", fx.lenses(wt))
        code, out = flow_run("review-reduce", "FX-3", "--round", 1, cwd=wt); expect(code == 0, out)
        sh("git", "push", "-q", "-u", "origin", "HEAD:feature/FX-3", cwd=wt)
        body = fx.tmp / "body.md"
        body.write_text("x\n")
        flow_run("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", body, cwd=wt)
        code, out = flow_run("handoff", "FX-3", "--pr", "https://forge.test/pr/1", "--blocked",
                             "needs-loaded=no data in the fixture", cwd=wt)
        expect(code == 0, out)
        handed = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()

        def forge_says(**fields) -> None:
            forge = json.loads((fx.tmp / "forge.json").read_text())
            forge["https://forge.test/pr/1"].update(fields)
            (fx.tmp / "forge.json").write_text(json.dumps(forge))

        def reports_rounds(rounds) -> None:
            version = json.loads((fx.tmp / "record/state.json").read_text())["version"]
            fx.call("state_tool", "patch", "--writer", "lead", "--version", str(version), "--element", "queue[subject=FX-3]",
                    "--set", json.dumps({"/reportedRounds": rounds}), cwd=m)

        # Still unmerged in the record: the branch and an open PR are still what it checks.
        reports_rounds(None)
        code, out = flow_run("lead-accept", "FX-3", "--accept-blocked", "needs-loaded", cwd=m)
        expect(code == 1 and "the hand-off reports None" in out, f"unmerged work skipped its rounds: {out}")
        reports_rounds(1)
        sh("git", "push", "-q", "-f", "origin", "HEAD~1:refs/heads/feature/FX-3", cwd=wt)
        code, out = flow_run("lead-accept", "FX-3", "--accept-blocked", "needs-loaded", cwd=m)
        expect(code == 1 and "not at the handed head" in out, f"accepted a branch that moved: {out}")
        sh("git", "push", "-q", "-f", "origin", "HEAD:refs/heads/feature/FX-3", cwd=wt)
        forge_says(state="merged", head=handed)
        code, out = flow_run("lead-accept", "FX-3", "--accept-blocked", "needs-loaded", cwd=m)
        expect(code == 1 and "the PR is merged" in out, f"accepted as unmerged work with a merged PR: {out}")
        # Merged by hand: the branch goes, and the record learns of it from the forge.
        sh("git", "push", "-q", "origin", "--delete", "feature/FX-3", cwd=wt)
        snapshot = fx.tmp / "forge-snapshot.json"
        snapshot.write_text(json.dumps({"takenAt": sh("date", "-u", "+%Y-%m-%dT%H:%M:%SZ").stdout.strip(), "source": "forge",
                                        "items": [{"subject": "FX-3", "state": "merged", "head": handed,
                                                   "changeRequest": "https://forge.test/pr/1"}]}))
        fx.call("state_tool", "reconcile", "--snapshot", str(snapshot), cwd=m)
        for fields, refusal in (({"state": "open"}, "the PR is open"), ({"state": "closed"}, "the PR is closed"),
                                ({"state": "merged", "head": "0" * 40}, "not the handed head")):
            forge_says(**fields)
            code, out = flow_run("lead-accept", "FX-3", "--accept-blocked", "needs-loaded", cwd=m)
            expect(code == 1 and refusal in out, f"merged work was accepted against the forge ({refusal}): {out}")
        forge_says(state="merged", head=handed)
        reports_rounds(9)
        code, out = flow_run("lead-accept", "FX-3", "--accept-blocked", "needs-loaded", cwd=m)
        expect(code == 1 and "the hand-off reports 9" in out, f"merged work skipped its rounds: {out}")
        reports_rounds(1)
        # An acceptance of an earlier head doesn't stand for the one that merged.
        with open(fx.tmp / "flow-log/FX-3.acceptance.jsonl", "a") as log:
            log.write(json.dumps({"head": "1" * 40, "blocked": {}, "at": "2026-01-01T00:00:00Z"}) + "\n")
        code, out = flow_run("lead-accept", "FX-3", cwd=m)
        expect(code == 1 and "accept with --accept-blocked 'needs-loaded'" in out and "already accepted" not in out,
               f"a blocked check was accepted, or an earlier head's acceptance stood in: {out}")
        code, out = flow_run("lead-accept", "FX-3", "--accept-blocked", "needs-loaded", cwd=m)
        expect(code == 0 and "after its merge" in out, out)
        waived = json.loads((fx.tmp / "record/plan.json").read_text())["evidence"]["FX-3"]["in-app-loaded"]
        expect(waived.get("state") == "waived", f"the lead's waiver didn't reach the plan: {waived}")
        journal = (fx.tmp / "record/journal.jsonl").read_text()
        expect("could not be checked" in journal, "the journal claims an owned-files check that never ran")
        expect("marked https://forge.test/pr/1 ready" not in journal, "tried to mark a merged PR ready")
        expect("accepted blocked 'needs-loaded'" in journal, "the lead's decision on the blocked check wasn't journaled")
        code, out = flow_run("lead-accept", "FX-3", "--accept-blocked", "needs-loaded", cwd=m)
        expect(code == 1 and "already accepted" in out, f"accepted twice: {out}")

    def test_work_merged_with_no_hand_off_has_no_claims_for_the_lead_to_check(self) -> None:
        fx = self.fixture()
        flow_run = fx.flow
        m = fx.main
        plan_delivery(fx)
        flow_run("lead-init", cwd=m)
        flow_run("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        wt = fx.lane("FX-3")
        (wt / "src/two.txt").write_text("two\n")
        commit(wt, "FX-3: two")
        sh("git", "push", "-q", "-u", "origin", "HEAD:feature/FX-3", cwd=wt)
        body = fx.tmp / "body.md"
        body.write_text("x\n")
        flow_run("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", body, cwd=wt)
        handed = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        forge = json.loads((fx.tmp / "forge.json").read_text())
        forge["https://forge.test/pr/1"].update(state="merged", head=handed)
        (fx.tmp / "forge.json").write_text(json.dumps(forge))
        snapshot = fx.tmp / "forge-snapshot.json"
        snapshot.write_text(json.dumps({"takenAt": sh("date", "-u", "+%Y-%m-%dT%H:%M:%SZ").stdout.strip(), "source": "forge",
                                        "items": [{"subject": "FX-3", "state": "merged", "head": handed,
                                                   "changeRequest": "https://forge.test/pr/1"}]}))
        fx.call("state_tool", "reconcile", "--snapshot", str(snapshot), cwd=m)
        code, out = flow_run("lead-accept", "FX-3", cwd=m)
        expect(code == 2 and "no hand-off" in out and "journal your decision" in out, f"no claims, yet checked: {out}")

    def test_timeouts_and_interruptions_stop_their_processes(self) -> None:
        started = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            # The shell leads its own group, so its pid names what this command started and nothing else on the machine.
            group = Path(tmp) / "group"
            try:
                self.call("run_group", ["sh", "-c", 'echo $$ > "$1"; sleep 30 & sleep 30', "sh", str(group)], Path(tmp), 2)
                raise AssertionError("a timed-out command didn't stop")
            except Stopped as error:
                expect("took longer" in str(error) and time.time() - started < 15, str(error))
            leader = int(group.read_text())
            deadline = time.monotonic() + 5
            leftover = group_members(leader)
            while leftover and time.monotonic() < deadline:
                time.sleep(0.05)
                leftover = group_members(leader)
            if leftover:
                os.killpg(leader, signal.SIGKILL)
            expect(not leftover, f"the timed-out group left processes behind: {leftover}")
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

    def test_the_in_app_server_listens_at_once_when_name_lookups_stall(self) -> None:
        """Where a reverse lookup of the loopback address goes unanswered, as on a macOS runner, a server that looks up
        its own name before it listens delays every in-app check by the lookup. The fixtures' declared serve doesn't."""
        fx = self.fixture()
        declared = json.loads((fx.main / ".agents/run-delivery.json").read_text())["inApp"]["serve"]["argv"]
        stock = [sys.executable, "-B", "-m", "http.server", "{port}", "--bind", "127.0.0.1", "--directory", "{dir}"]
        self.assertFalse(listens_under_stalled_lookup(stock, 3), "the stalled lookup didn't hold up the stock server")
        self.assertTrue(listens_under_stalled_lookup(declared, 5), f"the fixtures' serve waited for the stalled lookup: {declared}")

    def listener(self, port: int, mark: str, reuse: bool = False) -> subprocess.Popen:
        """A real listener on `port`, started here and stopped at cleanup by the pid recorded here. `mark` rides in its
        argv, so its command line can be told from any other's."""
        proc = subprocess.Popen([sys.executable, "-c", LISTENER, str(port), mark, "reuse" if reuse else "plain"],
                                stdout=subprocess.PIPE, text=True)

        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stdout.close()
        self.addCleanup(stop)
        self.assertEqual(proc.stdout.readline().strip(), "listening", f"the {mark} listener didn't start")
        return proc

    def closable(self) -> Fixture:
        """A delivery of its own, initialized and journalled closed, so lead-close runs to its in-app step."""
        fx = self.fixture()
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir", fx.tmp / "record",
           "journal", "--kind", "delivery.closed", "--summary", "closed", cwd=fx.main)
        home = fx.tmp / "home"
        home.mkdir()
        fx.env = {**fx.env, "HOME": str(home)}
        return fx

    def test_lead_close_with_the_lease_held_reports_only_the_held_lease(self) -> None:
        fx = self.closable()
        listener = self.listener(fx.port, "held-lease-mark")  # the check's own server, inside the lease
        lock = fx.tmp / "app.lock"
        holder = subprocess.Popen([sys.executable, "-c", "import fcntl, sys, time; f = open(sys.argv[1], 'a+'); "
                                   "fcntl.flock(f, fcntl.LOCK_EX); f.truncate(0); f.write('app-check pid 1 since then\\n'); "
                                   "f.flush(); print('holding', flush=True); time.sleep(60)", str(lock)],
                                  stdout=subprocess.PIPE, text=True)

        def stop():  # started here, stopped by the pid recorded here
            if holder.poll() is None:
                holder.kill()
            holder.wait()
            holder.stdout.close()
        self.addCleanup(stop)
        self.assertEqual(holder.stdout.readline().strip(), "holding")
        code, out = fx.flow("lead-close", cwd=fx.main)
        left = [line for line in out.splitlines() if line.startswith("  left: ")]
        self.assertEqual(code, 1, out)
        self.assertEqual(left, ["  left: the in-app lease is held (app-check pid 1 since then); gave up after 0s"], out)
        # The hint's own form, `kill <pid>`: the word alone also sits in paths, such as a checkout of agent-skills.
        self.assertNotRegex(out, r"\bkill \d", "a held lease's server got a kill hint")
        self.assertNotIn("is free", out, "a held lease was reported free")
        self.assertIsNone(listener.poll(), "lead-close stopped the server a live check holds")

    def test_lead_close_with_the_lease_free_names_the_ports_listener_and_the_kill_hint(self) -> None:
        fx = self.closable()
        listener = self.listener(fx.port, "free-lease-mark")  # a build served by hand, outside the lease
        code, out = fx.flow("lead-close", cwd=fx.main)
        left = [line for line in out.splitlines() if line.startswith("  left: ")]
        self.assertEqual(code, 1, out)
        self.assertIn(f"  done: the in-app lease {fx.tmp / 'app.lock'} is free", out)
        self.assertEqual(len(left), 1, out)
        self.assertIn(f"outside the in-app lease: pid {listener.pid} (", left[0])
        self.assertIn("free-lease-mark", left[0], "the named command line isn't the listener's")
        self.assertIn(f"stop it (kill {listener.pid}) and run again", left[0])
        self.assertIsNone(listener.poll(), "lead-close stopped a server outside the lease")

    def test_the_port_holder_is_named_by_its_listening_pid(self) -> None:
        port = free_port()  # an ephemeral port this case owns
        listener = self.listener(port, "holder-mark-one")
        # A client connected to the port (a browser looking at the build, say) isn't a holder, and isn't to be killed.
        client = subprocess.Popen([sys.executable, "-c", "import socket,sys,time; c=socket.create_connection(('127.0.0.1', "
                                   "int(sys.argv[1]))); print('connected', flush=True); time.sleep(60)", str(port)],
                                  stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(client.stdout.readline().strip(), "connected")
            holders = self.call("port_holders", port)
            self.assertEqual([pid for pid, _ in holders], [listener.pid], holders)
            self.assertIn("holder-mark-one", holders[0][1], "the named command line isn't the listener's")
            busy = self.call("port_busy", port)
            self.assertIn(f"outside the in-app lease: pid {listener.pid} (", busy)
            self.assertIn(f"stop it (kill {listener.pid}) and run again", busy)
            self.assertNotIn(str(client.pid), busy, "the kill hint named a connected client")
        finally:
            if client.poll() is None:  # started here, stopped by the pid recorded here
                client.kill()
            client.wait()
            client.stdout.close()
        with tempfile.TemporaryDirectory() as empty:  # no lsof or ps on PATH: nothing named, nothing raised
            unseen = dict(cwd=Path(empty), env={**clean_environment(), "PATH": empty})
            self.assertEqual(call(self.tool, "port_holders", port, **unseen), [])
            busy = call(self.tool, "port_busy", port, **unseen)
            self.assertIn(f"something else is listening on {port}, outside the in-app lease (its process isn't visible here)", busy)
            self.assertNotIn("kill", busy, "an unseen holder got a kill hint")
        listener.kill()
        listener.wait()
        self.assertEqual(self.call("port_holders", port), [], "a closed port still named a holder")

    def test_every_holder_of_a_shared_port_is_named_once(self) -> None:
        if not hasattr(socket, "SO_REUSEPORT"):
            self.skipTest("no SO_REUSEPORT here, so two listeners can't share a port")
        port = free_port()
        first, second = self.listener(port, "holder-mark-a", reuse=True), self.listener(port, "holder-mark-b", reuse=True)
        holders = self.call("port_holders", port)
        self.assertEqual([pid for pid, _ in holders], sorted([first.pid, second.pid]), holders)
        self.assertEqual({pid: "holder-mark-a" in command or "holder-mark-b" in command for pid, command in holders},
                         {first.pid: True, second.pid: True})
        busy = self.call("port_busy", port)
        hint = re.search(r"stop it \(kill ([\d ]+)\)", busy)
        self.assertIsNotNone(hint, busy)
        self.assertEqual(hint.group(1).split(), [str(pid) for pid in sorted([first.pid, second.pid])], busy)

    def test_a_check_refused_by_a_busy_port_names_the_listener_and_how_to_release_it(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        fx.flow("lead-init", cwd=fx.main)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        fx.flow("build", "FX-2", cwd=wt)
        listener = self.listener(fx.port, "served-by-hand")  # a build served on the in-app port, outside the lease
        code, out = fx.flow("app-check", "FX-2", "page", "--mode", "loaded", "--path", "/one", "--eval", "true", cwd=wt)
        self.assertEqual(code, 2, out)
        self.assertIn(f"something else is listening on {fx.port}, outside the in-app lease: pid {listener.pid} (", out)
        self.assertIn("served-by-hand", out)
        self.assertIn(f"stop it (kill {listener.pid}) and run again", out)
        self.assertIsNone(listener.poll(), "app-check stopped a process it didn't start")
        self.assertFalse((wt / ".orchestration/review/FX-2/app-checks/page.json").exists(), "a refused check became evidence")

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

    def test_a_check_removes_its_spec_before_it_releases_the_lease(self) -> None:
        """The spec file's one path is the lease holder's: a check removes it while it still holds the lease, so it can
        never remove the spec the next holder writes as soon as the lease is free."""
        fx = self.fixture()
        script = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import run_delivery as flow
flow.CFG = flow.Config(Path.cwd(), need_delivery=False)
lock = Path(flow.expand(flow.need_in_app()["lock"]))
spec_file = lock.with_name(lock.name + ".spec.json")
seen = {}
released = flow.Lease.close
def close(self):
    seen["at release"] = spec_file.exists()
    released(self)
    spec_file.write_text("the next holder's spec")  # what the next holder writes the moment the lease is free
flow.Lease.close = close
report = flow.run_in_app(Path(sys.argv[2]), {"path": "/", "holds": [], "fails": [], "evals": [], "afterEvals": []},
                         [], "order probe", 5)
seen["after"] = spec_file.read_text() if spec_file.exists() else None
seen["ready"] = report.get("ready")
print(json.dumps(seen))
'''
        done = sh(sys.executable, "-B", "-c", script, fx.tool.parent, fx.tmp / "baseline", cwd=fx.main, env=fx.env)
        seen = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertTrue(seen["ready"], done.stdout)
        self.assertFalse(seen["at release"], "the check still had its spec when it released the lease")
        self.assertEqual(seen["after"], "the next holder's spec", "the check removed the next holder's spec")

    def test_a_killed_check_leaves_nothing_running_once_its_fixture_closes(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        fx.flow("lead-init", cwd=fx.main)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        fx.flow("build", "FX-2", cwd=wt)
        lock = fx.tmp / "app.lock"
        recorded = lambda: [int(g) for g in re.findall(r"pgid (\d+)", lock.read_text())] if lock.exists() else []
        groups: list[int] = []
        # The fixture gives its commands a temp folder of their own, inside the case and outside its checkouts, so what
        # a killed check leaves there is this case's to see, and goes with it.
        temp = Path(fx.env["TMPDIR"])
        self.assertEqual(temp, fx.temp)
        self.assertTrue(temp.is_relative_to(fx.tmp) and not temp.is_relative_to(fx.main), temp)
        self.assertEqual(sorted(p.name for p in temp.iterdir()), [], "the fixture's temp folder wasn't empty before the check")
        proc = subprocess.Popen([sys.executable, "-B", str(fx.tool), "app-check", "FX-2", "slow", "--mode", "loaded",
                                 "--path", "/slow", "--eval", "true"], cwd=wt, env=fx.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and len(recorded()) < 2:  # the server's group, then the check's
                time.sleep(0.1)
            groups = recorded()
            self.assertEqual(len(groups), 2, f"the lease didn't record the server and the check: {lock.read_text()}")
            self.assertTrue(fx.call("port_open", fx.port, cwd=fx.tmp), "the check's server never came up")
            # A SIGKILL can't be caught, so app-check never stops what it started: the lease's record is what's left.
            proc.kill()
            proc.wait(timeout=20)
            time.sleep(0.5)
            self.assertTrue(all(group_alive(g) for g in groups), "the kill didn't leave the check's groups running")
            # Its spec sits beside the lease's lock, inside the fixture, not in the temp folder.
            self.assertEqual(sorted(p.name for p in temp.iterdir()), [], "a killed check left a file in the temp folder")
            self.assertTrue((fx.tmp / "app.lock.spec.json").is_file(), "the check's spec wasn't beside the lease's lock")
            fx.close()
            self.assertEqual([g for g in groups if group_alive(g)], [], "the fixture closed with the check's groups running")
            self.assertFalse(fx.call("port_open", fx.port, cwd=Path(tempfile.gettempdir())), "the server outlived its fixture")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for group in groups:  # the groups this case's own check started, by the pids its lease recorded
                if group_alive(group):
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(group, signal.SIGKILL)

    def test_closing_a_fixture_leaves_a_live_holders_lease_and_groups_alone(self) -> None:
        fx = self.fixture()
        lock = fx.tmp / "app.lock"
        served = subprocess.Popen(["sleep", "60"], start_new_session=True)
        holder = subprocess.Popen([sys.executable, "-c", "import fcntl,sys,time; f=open(sys.argv[1],'a+'); "
                                   "fcntl.flock(f, fcntl.LOCK_EX); f.write('lane-1 FX-2 hold pid 1 pgid %s\\n' % sys.argv[2]); "
                                   "f.flush(); print('held', flush=True); time.sleep(60)", str(lock), str(served.pid)],
                                  stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            fx.stop_in_app()  # raises if a held lease isn't left to its holder
            self.assertIsNone(served.poll(), "closing the fixture stopped a live holder's group")
            self.assertIn(f"pgid {served.pid}", lock.read_text(), "closing the fixture rewrote a live holder's lease")
        finally:
            for proc in (holder, served):  # both started here, stopped by the pids recorded here
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
            holder.stdout.close()

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
    count = int(os.environ.get("FAKE_GH_PRS", "1"))
    print(json.dumps([{"url": "https://github.com/o/n/pull/%d" % (3 + i)} for i in range(count)]))
elif command == ["pr", "create"]:
    print("https://github.com/o/n/pull/4")
elif command == ["pr", "view"]:
    print(json.dumps({"url": "https://github.com/o/n/pull/3", "state": "OPEN", "isDraft": True, "headRefOid": "abc",
                      "headRefName": "feature/X-1", "baseRefName": "main",
                      "statusCheckRollup": [{"name": "build", "conclusion": "SUCCESS", "completedAt": "2026-01-01T00:00:01Z"},
                                            {"context": "lint", "state": "PENDING"},
                                            {"name": "test", "conclusion": "FAILURE", "completedAt": "2026-01-01T00:00:03Z"}],
                      "reviews": [{"author": {"login": "pipeline-bot"}, "submittedAt": "2026-01-01T00:00:02Z",
                                   "body": "Looks fine"}]}))
elif command == ["pr", "merge"] and "--match-head-commit" in sys.argv:
    # As gh does: the merge is refused when the pull request's head isn't the one named.
    if sys.argv[sys.argv.index("--match-head-commit") + 1] != "abc":
        sys.exit("gh: head branch was modified; not merged")
    print("done")
elif command in (["pr", "ready"], ["pr", "merge"]):
    print("done")
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

    def test_a_prepared_brief_bounds_each_lens_to_the_scoped_checks(self) -> None:
        fx = self.fixture()
        wt = ready_lane(fx)
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt)
        expect(code == 0, out)
        head = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        base = sh("git", "merge-base", "origin/feature/FX-1", "HEAD", cwd=wt).stdout.strip()
        for lens in ("outcome-correctness", "adversarial-testing"):
            with self.subTest(lens=lens):
                text = " ".join((wt / f".orchestration/review/FX-3/brief-r1-{lens}.md").read_text().split())
                self.assertIn("## Checks this lens runs Run the scoped check set below in your checkout, never the "
                              "repository's full suite", text)
                self.assertIn("Mutation and fault injection stay on the candidate's changed lines", text)
                self.assertIn("- scoped sensor commands: the sensors the plan unit names", text)
                self.assertIn(f"- test files in scope for this round: the tests among the files `git diff {base}..{head}` "
                              "changes, and the tests that cover the rest: `src/two.txt`.", text)
                self.assertNotIn("{{", text)
        # A delta that changes no file puts no file in scope, rather than every file.
        self.assertTrue(call(self.tool, "scoped_checks", f"{head}..{head}", cwd=wt, env=fx.env, delivery="FX-1")
                        .endswith("the tests that cover the rest: none."))

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
        conf_path = m / ".agents/run-delivery.json"
        conf = json.loads(conf_path.read_text())
        del conf["cleanup"]  # the default archive, under the HOME this case gives lead-close
        conf_path.write_text(json.dumps(conf, indent=2))
        commit(m, "FX-1: the default archive")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
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
        ours = {"the earlier tool's sweep": start("flow.py", "lead-reconcile"),
                "the tool's supervisor": start("run_delivery.py", "lead-run"),
                "the merge queue": start("merge_queue.py", "--dir", str(state)),
                "the renderer": start("render_state.py", "--dir", str(state))}
        theirs = start("run_delivery.py", "--delivery", "OTHER-9", "lead-run")
        # Not this tool, though its name ends like the earlier one's.
        stranger = start("notflow.py", "lead-run")
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

    # -- the integration build ------------------------------------------------------------------------------

    def test_build_without_a_story_key_builds_the_delivery_under_its_own_key(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        m = fx.main
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        conf["inApp"]["build"]["argv"] = ["sh", "-c", 'mkdir -p out && cp src/*.txt out/ && printf %s "$0" > out/key.txt', "{key}"]
        (m / ".agents/run-delivery.json").write_text(json.dumps(conf, indent=2))
        commit(m, "FX-1: the build names its key")
        fx.flow("lead-init", cwd=m)
        head = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        code, out = fx.flow("build", cwd=m)
        expect(code == 0 and f"built {head[:10]}" in out, out)
        self.assertEqual((m / "out/key.txt").read_text(), "FX-1")
        self.assertEqual(json.loads((m / "out/.flow-build.json").read_text())["head"], head)
        # A story's build still names the story, and a key that is not one of the delivery's is still refused.
        code, out = fx.flow("build", "FX-2", cwd=m)
        expect(code == 0, out)
        self.assertEqual((m / "out/key.txt").read_text(), "FX-2")
        code, out = fx.flow("build", "FX-9", cwd=m)
        expect(code == 2 and "FX-9 is not a story of FX-1" in out, out)
        # An empty key is a key, refused like any other that is not a story, never the integration build.
        code, out = fx.flow("build", "", cwd=m)
        expect(code == 2 and " is not a story of FX-1" in out, out)

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

    def test_the_github_adapter_opens_drafts_marks_ready_merges_and_reads_reviews(self) -> None:
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
            url = "https://github.com/o/n/pull/3"
            commands = [("find", "--source", "feature/X-1"), ("get", "--url", url),
                        ("open", "--source", "feature/X-1", "--destination", "main", "--title", "X-1", "--body-file", str(body)),
                        ("reviews", "--url", url, "--head", "abc"), ("ready", "--url", url),
                        ("merge", "--url", url, "--strategy", "squash", "--head", "abc")]
            said = {}
            for args in commands:
                with self.subTest(command=args[0]):
                    done = run(*args)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    said[args[0]] = json.loads(done.stdout)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([c[:2] for c in calls], [["pr", "list"], ["pr", "view"], ["pr", "create"], ["pr", "view"],
                                                      ["pr", "ready"], ["pr", "merge"]])
            self.assertIn("--draft", calls[2])
            self.assertEqual(calls[5][2:], [url, "--squash", "--match-head-commit", "abc", "--repo", "o/n"])
            self.assertTrue(said["get"]["draft"])
            self.assertEqual(said["reviews"], {"checks": [{"name": "build", "state": "passed", "at": "2026-01-01T00:00:01Z"},
                                                          {"name": "lint", "state": "pending", "at": ""},
                                                          {"name": "test", "state": "failed", "at": "2026-01-01T00:00:03Z"}],
                                               "reviews": [{"author": "pipeline-bot", "at": "2026-01-01T00:00:02Z",
                                                            "body": "Looks fine"}]})
            self.assertEqual((said["ready"], said["merge"]), ({"url": url, "draft": False}, {"url": url, "state": "merged"}))
            # A binding's merge step knows the branch, not the pull request: --source finds its one open pull request
            # and merges it at exactly the checked head.
            done = run("merge", "--source", "feature/X-1", "--strategy", "merge", "--head", "abc")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout), {"url": url, "state": "merged"})
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(calls[-2][:6], ["pr", "list", "--head", "feature/X-1", "--state", "open"])
            self.assertEqual(calls[-1][2:], [url, "--merge", "--match-head-commit", "abc", "--repo", "o/n"])
            for refused, extra, words in ((["--head", "def"], {}, "head branch was modified"),
                                          (["--head", "abc"], {"FAKE_GH_PRS": "0"}, "has no open pull requests"),
                                          (["--head", "abc"], {"FAKE_GH_PRS": "2"}, "has 2 open pull requests"),
                                          ([], {}, "needs --head")):
                with self.subTest(refused=refused, extra=extra):
                    before = len(log.read_text().splitlines())
                    done = run("merge", "--source", "feature/X-1", "--strategy", "merge", *refused, **extra)
                    self.assertNotEqual(done.returncode, 0, done.stdout)
                    self.assertIn(words, done.stderr)
                    merges = [json.loads(line) for line in log.read_text().splitlines()[before:]
                              if json.loads(line)[:2] == ["pr", "merge"]]
                    if "modified" in words:  # gh itself refuses the moved head the merge was pinned to
                        self.assertEqual([m[m.index("--match-head-commit") + 1] for m in merges], ["def"])
                    else:
                        self.assertEqual(merges, [], "a merge was attempted on a refusal")
            # The rollup is the pull request's current head's: at another head nothing has run yet.
            self.assertEqual(json.loads(run("reviews", "--url", url, "--head", "def").stdout)["checks"], [])
            for refused in (("ready", "--url", "https://github.com/x/y/pull/3"),
                            ("merge", "--url", url, "--strategy", "rebase")):
                with self.subTest(refused=refused):
                    self.assertNotEqual(run(*refused).returncode, 0)
            for mode in ("fail", "fail-long"):
                for args in commands:
                    with self.subTest(mode=mode, failing=args[0]):
                        done = run(*args, FAKE_GH_MODE=mode)
                        self.assertNotEqual(done.returncode, 0)
                        self.assertEqual(token_fragments(token, done.stdout + done.stderr), [])
                        if mode == "fail":  # in fail-long the cut falls inside the marker
                            self.assertIn("[redacted]", done.stderr)

    def test_the_bitbucket_adapter_marks_ready_and_merges_only_the_head_it_was_given(self) -> None:
        spec = importlib.util.spec_from_file_location("forge_bitbucket_for_tests", self.adapter("bitbucket"))
        calls = []
        open_prs = [1]

        def answer(request, timeout=None):
            calls.append((request.get_method(), request.full_url, json.loads(request.data) if request.data else None))
            if "?q=" in request.full_url:
                body = {"values": [{"links": {"html": {"href": f"https://bitbucket.org/ws/repo/pull-requests/{7 + i}"}}}
                                   for i in range(open_prs[0])]}
            elif request.get_method() == "GET":
                body = {"source": {"commit": {"hash": "abc123def456"}}}
            else:
                body = {}
            return contextlib.closing(io.BytesIO(json.dumps(body).encode()))

        def adapter(*argv):
            module = importlib.util.module_from_spec(spec)
            with mock.patch.dict(os.environ, {"FLOW_FORGE_REPOSITORY": "ws/repo", "FLOW_FORGE_TOKEN_ENV": "FX_TOKEN",
                                              "FX_TOKEN": "bb-secret"}), \
                    mock.patch.object(sys, "argv", ["forge_bitbucket.py", *argv]), \
                    mock.patch("urllib.request.urlopen", answer), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                previous = sys.dont_write_bytecode
                sys.dont_write_bytecode = True
                try:
                    spec.loader.exec_module(module)
                    module.main()
                finally:
                    sys.dont_write_bytecode = previous
            return json.loads(out.getvalue())

        url = "https://bitbucket.org/ws/repo/pull-requests/7"
        api = "https://api.bitbucket.org/2.0/repositories/ws/repo/pullrequests/7"
        self.assertEqual(adapter("ready", "--url", url), {"url": url, "draft": False})
        self.assertEqual(calls[-1], ("PUT", api, {"draft": False}))
        self.assertEqual(adapter("merge", "--url", url, "--strategy", "merge", "--head", "abc123def456"),
                         {"url": url, "state": "merged"})
        self.assertEqual(calls[-2:], [("GET", api, None), ("POST", api + "/merge", {"merge_strategy": "merge_commit",
                                                                                     "close_source_branch": False})])
        calls.clear()
        # --source finds the branch's open pull request first.
        self.assertEqual(adapter("merge", "--source", "feature/X-1", "--strategy", "squash", "--head", "abc123def456"),
                         {"url": url, "state": "merged"})
        self.assertIn('source.branch.name%3D%22feature/X-1%22', calls[0][1])
        self.assertEqual(calls[-1], ("POST", api + "/merge", {"merge_strategy": "squash", "close_source_branch": False}))
        # It refuses a moved head, a branch with no open pull request or two, and a merge not pinned to a head.
        for count, argv, words in ((1, ["--head", "fff000"], "not merged"), (0, ["--head", "abc123def456"], "has no open"),
                                   (2, ["--head", "abc123def456"], "has 2 open"), (1, [], "needs --head")):
            with self.subTest(count=count, argv=argv):
                open_prs[0] = count
                calls.clear()
                with self.assertRaises(SystemExit) as refused:
                    adapter("merge", "--source", "feature/X-1", "--strategy", "merge", *argv)
                self.assertIn(words, str(refused.exception.code))
                self.assertFalse([c for c in calls if c[0] == "POST"], "merged on a refusal")
        open_prs[0] = 1
        calls.clear()
        with self.assertRaises(SystemExit) as refused:
            adapter("merge", "--url", url, "--strategy", "squash", "--head", "fff000")
        self.assertIn("not merged", str(refused.exception.code))
        self.assertEqual([method for method, *_ in calls], ["GET"], "a moved head was merged")

    def test_every_shipped_forge_adapter_answers_reviews(self) -> None:
        fx = self.fixture()
        m = fx.main
        setup_delivery(fx)
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        for forge in ({"adapter": "github", "repository": "o/n"}, {"adapter": "bitbucket", "repository": "ws/repo"},
                      {"argv": ["true"]}):
            with self.subTest(forge=forge):
                (m / ".agents/run-delivery.json").write_text(json.dumps({**conf, "forge": forge}))
                for command in ("reviews", "ready", "merge"):
                    self.assertTrue(call(self.tool, "forge_answers", command, cwd=m, env=fx.env, delivery="FX-1"))

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


def add_story(fx: Fixture, path: Path, key: str, **story) -> None:
    """A story added to the delivery file and the plan by hand, as SKILL.md's step 3 says, and committed."""
    d = json.loads(path.read_text())
    d["stories"][key] = {"title": f"Thing {key}", "lane": "lane-1",
                         "criteria": [{"id": f"{key.lower()}-works", "text": f"{key} works"}], **story}
    path.write_text(json.dumps(d, indent=2))
    plan = fx.main / "docs/plan.md"
    plan.write_text(plan.read_text() + f"\n### S9 ({key}). Thing {key}\n\n- Scenarios: it works.\n")
    commit(fx.main, f"FX-1: {key} added")


def plan_delivery(fx: Fixture, path: Path | None = None) -> Path:
    """The fixture delivery with the plan chapter on: criteria, an outcome, and finish lines with covers and labels.
    Given the delivery file a fixture already set up, it adds them to that one."""
    path = path or setup_delivery(fx)
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


THIS_PLAN = "docs/plans/2026-11-09-lead-hand-work.md"
# The delivery this repository runs from that plan, as its stories' `after` keys and its lanes' lowest-tier rows say,
# written out here so that nothing the lead's ownership moves rewrite is read. Stories are numbered under the
# delivery's own key prefix, so the corpus names no tracker key.
THIS_PLAN_AFTER = {3276: [], 3259: [], 3256: [3276, 3259], 3263: [3256], 3257: [3263], 3310: [3257], 3309: [3257, 3259]}
THIS_PLAN_OWNS = {
    "lane-1": ["skills/run-delivery/*", "tests/test_run_delivery.py", "tests/test_run_delivery_supervisor.py",
               "tests/test_run_delivery_plan_review.py", "tests/test_run_delivery_tracker.py", "skills/plan-work/*",
               "docs/solutions/*", "catalog.json", "profiles/*"],
    "lane-2": ["skills/orchestrate-work/*", "tests/test_orchestration_state.py", "tests/test_orchestration_render.py",
               "tests/test_merge_queue.py", "tests/test_lead_wait.py", "tests/test_registry.py", "skills/delivery-views/*",
               "tests/test_delivery_views_page.py", "tests/test_delivery_views_snapshots.py", "tests/delivery_views_page/*",
               "tests/fixtures/delivery-views/*"],
}


def plan_format_example() -> str:
    """The plan-format example plan-work's SKILL.md documents."""
    text = (ROOT / "skills/plan-work/SKILL.md").read_text(encoding="utf-8")
    found = re.findall(r"^```markdown example plan-format\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)
    assert len(found) == 1, "plan-work's SKILL.md documents one plan-format example"
    return found[0]


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryPlanFormatTests(unittest.TestCase):
    """lead-setup drafts each story's lane, criteria and order and the lanes' owns from a plan in plan-work's
    format, and lead-hints passes the order on to orchestrate-work's next."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        return fx

    def setup(self, fx: Fixture, plan: str, *extra: str, key: str = "EX-1") -> tuple[int, str, dict | None]:
        (fx.main / "docs/format-plan.md").write_text(plan)
        commit(fx.main, f"{key}: the plan")
        code, out = fx.flow("lead-setup", key, "--plan", "docs/format-plan.md", "--base", "feature/FX-1",
                            "--no-authority", *extra, cwd=fx.main)
        path = fx.main / f".agents/deliveries/{key}.json"
        return code, out, json.loads(path.read_text()) if path.exists() else None

    def test_this_deliverys_plan_drafts_its_committed_lanes_criteria_order_and_owns(self) -> None:
        listed = sh("git", "ls-tree", "--name-only", "HEAD", ".agents/deliveries/", cwd=ROOT).stdout.split()
        files = [json.loads(sh("git", "show", f"HEAD:{name}", cwd=ROOT).stdout) for name in listed if name.endswith(".json")]
        delivery = next(d for d in files if d.get("plan") == THIS_PLAN)
        prefix = delivery["delivery"].rsplit("-", 1)[0]
        named = lambda n: f"{prefix}-{n}"
        after = {named(n): [named(k) for k in keys] for n, keys in THIS_PLAN_AFTER.items()}
        committed = delivery["stories"]
        # The tracker snapshot carries each item's criteria, as the snapshot producer reads them from the item.
        items = [{"subject": key, "title": story["title"], "state": "backlog",
                  "criteria": [criterion["text"] for criterion in story.get("criteria", [])]}
                 for key, story in committed.items()]
        tracker = self.fixture()
        snapshot = tracker.tmp / "tracker.json"
        snapshot.write_text(json.dumps({"takenAt": "2026-11-09T00:00:00Z", "source": "tracker", "items": items}))
        code, out, drafted = self.setup(tracker, (ROOT / THIS_PLAN).read_text(encoding="utf-8"),
                                        "--tracker-snapshot", str(snapshot), key=delivery["delivery"])
        self.assertEqual(code, 0, out)
        stories = drafted["stories"]
        self.assertLessEqual(set(committed), set(stories), out)
        for key, story in committed.items():
            with self.subTest(key):
                self.assertEqual(stories[key]["lane"], story["lane"])
                self.assertEqual([c["text"] for c in stories[key].get("criteria", [])],
                                 [c["text"] for c in story.get("criteria", [])])
                # A story added since the row was written isn't in its table: its delivery-file story holds its keys.
                expected = after.get(key, story.get("after", []))
                self.assertEqual(stories[key].get("after", []), story.get("after", expected))
                self.assertEqual(stories[key].get("after", []), expected)
        self.assertEqual({lane: entry["owns"] for lane, entry in drafted["lanes"].items()}, THIS_PLAN_OWNS)
        self.assertNotIn("unassigned", out)
        self.assertNotIn("drafted no", out)

    def test_plan_works_example_drafts_every_story_with_no_hand_edit(self) -> None:
        fx = self.fixture()
        code, out, drafted = self.setup(fx, plan_format_example())
        self.assertEqual(code, 0, out)
        self.assertEqual({key: (story["lane"], story.get("after", [])) for key, story in drafted["stories"].items()},
                         {"EX-2": ("lane-1", []), "EX-3": ("lane-2", ["EX-2"]), "EX-4": ("lane-2", ["EX-2", "EX-3"])})
        self.assertEqual({key: story["title"] for key, story in drafted["stories"].items()},
                         {"EX-2": "Serve the greeting from the API", "EX-3": "Show the greeting on the front page",
                          "EX-4": "Greet a returning visitor by name"})
        # Each lane's lowest-tier row: lane-2's tier-2 row is what it gains later, not its owns.
        self.assertEqual(drafted["lanes"], {"lane-1": {"owns": ["src/api/*", "tests/test_api.py"]},
                                            "lane-2": {"owns": ["src/web/*", "tests/test_web.py"]}})
        # With no tracker snapshot, no story has criteria, and lead-setup says so.
        self.assertTrue(all("criteria" not in story for story in drafted["stories"].values()), drafted)
        self.assertIn("note: no tracker snapshot (pass --tracker-snapshot, or declare the binding's "
                      "state.trackerSnapshot): drafted no story's criteria", out)

    def test_a_section_with_no_lane_sentence_leaves_its_story_unassigned_and_says_so(self) -> None:
        fx = self.fixture()
        plan = plan_format_example().replace("Lane 2, tier 1, after EX-2. Test-first.", "Test-first.")
        code, out, drafted = self.setup(fx, plan)
        self.assertEqual(code, 0, out)
        self.assertEqual(drafted["stories"]["EX-3"]["lane"], "unassigned")
        self.assertNotIn("after", drafted["stories"]["EX-3"])
        self.assertIn("note: plan story EX-3's section doesn't open with its lane sentence", out)
        self.assertIn("left it unassigned", out)
        self.assertEqual(drafted["stories"]["EX-2"]["lane"], "lane-1")

    def test_only_headings_under_stories_are_stories_and_a_lanes_lowest_tier_row_wins_in_any_order(self) -> None:
        fx = self.fixture()
        example = plan_format_example()
        # A story heading outside `## Stories` drafts nothing, and a table listing lane-2's tier-2 row first still
        # drafts its tier-1 owns.
        plan = (example.replace("| 1 | 2 | EX-3 | `src/web/`, `tests/test_web.py` |\n| 2 | 2 | EX-4 | `src/api/` |",
                                "| 2 | 2 | EX-4 | `src/api/` |\n| 1 | 2 | EX-3 | `src/web/`, `tests/test_web.py` |")
                .replace("## Stories", "### EX-9. Not a story\n\nLane 1, tier 1, not blocked.\n\n## Stories"))
        self.assertNotEqual(plan, example)
        self.assertIn("| 2 | 2 | EX-4 | `src/api/` |\n| 1 | 2 | EX-3", plan)
        code, out, drafted = self.setup(fx, plan)
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(drafted["stories"]), ["EX-2", "EX-3", "EX-4"])
        self.assertEqual(drafted["lanes"]["lane-2"], {"owns": ["src/web/*", "tests/test_web.py"]})

    def test_the_lane_sentence_counts_only_as_its_sections_opening_sentence(self) -> None:
        fx = self.fixture()
        plan = plan_format_example().replace("Lane 2, tier 1, after EX-2. Test-first.", "Test-first. Lane 2, tier 1, after EX-2.")
        code, out, drafted = self.setup(fx, plan)
        self.assertEqual(code, 0, out)
        self.assertEqual(drafted["stories"]["EX-3"]["lane"], "unassigned")
        self.assertIn("note: plan story EX-3's section doesn't open with its lane sentence", out)

    def test_lead_setup_notes_a_lane_with_no_units_row_and_an_after_key_with_no_story(self) -> None:
        fx = self.fixture()
        plan = plan_format_example().replace("Lane 2, tier 2, after EX-2 and EX-3.", "Lane 3, tier 1, after EX-9.")
        code, out, drafted = self.setup(fx, plan)
        self.assertEqual(code, 0, out)
        self.assertEqual((drafted["stories"]["EX-4"]["lane"], drafted["stories"]["EX-4"]["after"]), ("lane-3", ["EX-9"]))
        self.assertEqual(drafted["lanes"]["lane-3"], {"owns": []})
        self.assertIn("note: plan story EX-4 comes after EX-9, which the plan has no story for", out)
        self.assertIn("note: lane-3 has stories but no row in the plan's 'Units and order' table: drafted it owning "
                      "nothing", out)

    def test_criteria_come_from_the_tracker_snapshot_and_a_story_without_any_is_named(self) -> None:
        fx = self.fixture()
        snapshot = fx.tmp / "tracker.json"
        snapshot.write_text(json.dumps({"takenAt": "2026-11-09T00:00:00Z", "source": "tracker", "items": [
            {"subject": "EX-2", "state": "backlog", "criteria": ["The API serves it", "It says when it fails"]},
            {"subject": "EX-3", "state": "backlog"},
            {"subject": "EX-4", "state": "backlog", "criteria": []}]}))
        code, out, drafted = self.setup(fx, plan_format_example(), "--tracker-snapshot", str(snapshot))
        self.assertEqual(code, 0, out)
        self.assertEqual(drafted["stories"]["EX-2"]["criteria"], [{"id": "ex-2-1", "text": "The API serves it"},
                                                                 {"id": "ex-2-2", "text": "It says when it fails"}])
        for key in ("EX-3", "EX-4"):
            self.assertNotIn("criteria", drafted["stories"][key])
            self.assertIn(f"note: plan story {key}'s tracker item has no Acceptance Criteria section", out)
        # The binding's own tracker snapshot, run for the delivery, when no file is given.
        binding = fx.main / ".agents/orchestration.json"
        document = json.loads(binding.read_text()) if binding.exists() else {}
        # The command runs for the delivery's own key: it prints the snapshot only when {parent} is EX-1.
        argv = ["sh", "-c", 'test "$1" = EX-1 && cat "$2"', "sh", "{parent}", str(snapshot)]
        rerun = lambda: fx.flow("lead-setup", "EX-1", "--plan", "docs/format-plan.md", "--base", "feature/FX-1",
                                "--no-authority", "--force", cwd=fx.main)
        document.setdefault("state", {})["trackerSnapshot"] = {"argv": argv}
        binding.write_text(json.dumps(document, indent=2))
        commit(fx.main, "EX-1: the binding's tracker snapshot")
        code, out = rerun()
        self.assertEqual(code, 0, out)
        drafted = json.loads((fx.main / ".agents/deliveries/EX-1.json").read_text())
        self.assertEqual([c["id"] for c in drafted["stories"]["EX-2"]["criteria"]], ["ex-2-1", "ex-2-2"])
        self.assertNotIn("no tracker snapshot", out)
        # When the command fails, prints no snapshot or outlives its timeout, lead-setup says so and drafts no criteria.
        for failing, words in (({"argv": ["sh", "-c", "echo boom >&2; exit 3"]},
                                "the binding's tracker snapshot failed (3: boom)"),
                               ({"argv": ["echo", "not json"]}, "the binding's tracker snapshot printed no snapshot"),
                               ({"argv": ["sleep", "30"], "timeoutSeconds": 1},
                                "the binding's tracker snapshot timed out")):
            with self.subTest(failing=failing):
                document["state"]["trackerSnapshot"] = failing
                binding.write_text(json.dumps(document, indent=2))
                commit(fx.main, "EX-1: a failing tracker snapshot")
                code, out = rerun()
                self.assertEqual(code, 0, out)
                self.assertIn(f"note: {words}: drafted no story's criteria", out)
                drafted = json.loads((fx.main / ".agents/deliveries/EX-1.json").read_text())
                self.assertTrue(all("criteria" not in story for story in drafted["stories"].values()), drafted)

    def test_lead_hints_passes_each_storys_after_keys_and_plan_files_as_work_hints(self) -> None:
        fx = self.fixture()
        code, out, drafted = self.setup(fx, plan_format_example())
        self.assertEqual(code, 0, out)
        path = fx.main / ".agents/deliveries/EX-1.json"
        drafted["stateDirectory"] = str(fx.tmp / "record-ex")
        path.write_text(json.dumps(drafted, indent=2))
        commit(fx.main, "EX-1: delivery")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("--delivery", "EX-1", "lead-hints", cwd=fx.main)
        self.assertEqual(code, 0, out)
        hints = json.loads(out)
        self.assertEqual(hints, {"items": {
            "EX-2": {"files": ["src/api/greeting.py", "tests/test_api.py"]},
            "EX-3": {"files": ["src/web/front.py", "src/web/templates/*", "tests/test_web.py"], "blockers": ["EX-2"]},
            "EX-4": {"files": ["src/api/visitor.py"], "blockers": ["EX-2", "EX-3"]}}})
        # It is a work-hints/1 the release's next reads.
        checked = subprocess.run(
            [sys.executable, "-B", "-c", "import json, sys; sys.path.insert(0, sys.argv[1]); import delivery_state; "
             "delivery_state.check_hints(json.loads(sys.argv[2]), 'hints'); print('ok')",
             str(fx.release / "skills/orchestrate-work/scripts"), out], capture_output=True, text=True)
        self.assertEqual(checked.stdout.strip(), "ok", checked.stderr)


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
        self.assertIn(f"{m} is behind origin/feature/FX-1; bring it up to date first: git merge --ff-only "
                      "origin/feature/FX-1", out)
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

    def test_a_finish_change_with_text_rewords_the_line_in_the_delivery_file_the_plan_and_the_record(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        covers = json.loads(path.read_text())["finish"][0]["covers"]
        # The wording alone: --covers may be left out, and what the line covers stays.
        code, out = fx.flow("lead-plan", "change", "--kind", "finish", "--finish", "acs", "--text", "Every criterion is met",
                            "--why", "Said plainly.", cwd=m)
        self.assertEqual(code, 0, out)
        line = json.loads(path.read_text())["finish"][0]
        self.assertEqual((line["text"], line["covers"]), ("Every criterion is met", covers))
        self.assertEqual(self.record(fx)["state"]["finish"][0]["text"], "Every criterion is met")
        plan = self.plan(fx)
        self.assertEqual([(entry["id"], entry["text"]) for entry in plan["finish"]], [("acs", "Every criterion is met")])
        change = plan["changes"][-1]
        self.assertEqual((change["kind"], change["before"], change["after"]), ("finish", "AC1-AC2 met", "Every criterion is met"))
        # With new covers too, the line is reworded and two changes record it: what it covers, then its wording.
        narrower = {"criteria": ["one-loads"], "proofs": ["merged"]}
        count = len(self.plan(fx)["changes"])
        code, out = fx.flow("lead-plan", "change", "--kind", "finish", "--finish", "acs", "--text", "One loads",
                            "--covers", json.dumps(narrower), "--why", "Narrowed.", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(path.read_text())["finish"][0]["text"], "One loads")
        self.assertEqual(self.record(fx)["state"]["finish"][0]["text"], "One loads")
        changes = self.plan(fx)["changes"]
        self.assertEqual(len(changes), count + 2, changes)
        self.assertEqual([(change["kind"], change["before"], change["after"], change["why"]) for change in changes[-2:]],
                         [("finish", covers, narrower, "Narrowed."),
                          ("finish", "Every criterion is met", "One loads", "Narrowed.")])
        self.assertIn(f"recorded {changes[-2]['id']} and {changes[-1]['id']}", out)
        self.assertIn(f"plan change {changes[-2]['id']} and {changes[-1]['id']} (finish acs)",
                      sh("git", "log", "-1", "--format=%s", cwd=m).stdout)
        # The same wording and covers again changes nothing, and is refused with nothing written.
        before = (path.read_text(), len(self.plan(fx)["changes"]))
        code, out = fx.flow("lead-plan", "change", "--kind", "finish", "--finish", "acs", "--text", "One loads",
                            "--why", "Again.", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("already reads so and covers that: nothing to change", out)
        self.assertEqual((path.read_text(), len(self.plan(fx)["changes"])), before)
        # A new line still needs its covers.
        code, out = fx.flow("lead-plan", "change", "--kind", "finish", "--finish", "new-line", "--text", "New",
                            "--why", "w", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("--kind finish takes --finish <id> and --covers <JSON>", out)

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

    def test_a_story_registered_mid_delivery_records_an_added_change_with_its_why_and_lead_plan_why_can_restate_it(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        d = json.loads(path.read_text())
        d["stories"]["FX-4"] = {"title": "Thing four", "lane": "lane-1", "why": "A lane found the gap.",
                                "criteria": [{"id": "four-works", "text": "Four works"}]}
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a story added")
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        self.assertEqual([(c["id"], c["kind"], c["subjects"], c.get("why")) for c in plan["changes"]],
                         [("c1", "added", ["FX-4"], "A lane found the gap.")])
        self.assertEqual(plan["units"][-1]["subject"], "FX-4")
        self.assertNotIn("FX-4", [u["subject"] for u in plan["approved"]["snapshot"]["units"]])
        code, out = fx.flow("lead-plan", "why", "c1", "--why", "A lane found the gap in thing two.", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.plan(fx)["changes"][0]["why"], "A lane found the gap in thing two.")
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

    def supervise(self, fx: Fixture) -> subprocess.Popen:
        """The supervisor in the foreground, a pass a second, stopped and reaped at cleanup."""
        conf_path = fx.main / ".agents/run-delivery.json"
        conf_path.write_text(json.dumps({**json.loads(conf_path.read_text()), "supervisor": {"passSeconds": 1}}))
        commit(fx.main, "FX-1: a pass a second")
        proc = subprocess.Popen([sys.executable, "-B", fx.tool, "lead-run"], cwd=fx.main, env=fx.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)

        def stop():
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=60)
            proc.stdout.close()
        self.addCleanup(stop)
        self.assertTrue(proc.stdout.readline().startswith("http://127.0.0.1:"))
        return proc

    def next_pass(self, fx: Fixture, after: int) -> dict:
        """The supervisor's first pass after the first `after`, as its pass log records it."""
        log = fx.tmp / "flow-log/FX-1.supervisor.jsonl"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            passes = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            if len(passes) > after:
                return passes[after]
            time.sleep(0.1)
        raise AssertionError(f"no pass {after + 1} within 60s")

    def test_a_running_supervisor_builds_from_the_delivery_file_as_it_stands(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        self.supervise(fx)
        self.assertIn("plan: unchanged", self.next_pass(fx, 0)["reconcile"])
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--text", "Eased", "--why", "Eased.", cwd=m)
        self.assertEqual(code, 0, out)
        seen = [self.next_pass(fx, n) for n in range(1, 3)]
        plan = self.plan(fx)
        self.assertEqual([(c["id"], c["by"]) for c in plan["changes"]], [("c1", "lead")], seen)
        self.assertEqual(plan["units"][0]["criteria"][1]["text"], "Eased")

    def test_a_running_supervisor_outlives_a_delivery_file_saved_mid_edit(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        proc = self.supervise(fx)
        self.assertIn("plan: unchanged", self.next_pass(fx, 0)["reconcile"])
        whole = path.read_text()
        path.write_text(whole[: len(whole) // 2])
        broken = self.next_pass(fx, 1)
        path.write_text(whole)
        recovered = self.next_pass(fx, 3)
        self.assertIn("not reread", broken.get("delivery", "") + " ".join(broken["reconcile"]))
        self.assertIn("plan: unchanged", recovered["reconcile"])
        self.assertIsNone(proc.poll(), "the supervisor ended on a delivery file that isn't JSON")

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
        d["stories"]["FX-4"] = {"title": "Thing four", "lane": "lane-1", "why": "Split from FX-2.",
                                "criteria": [{"id": "four-works", "text": "Four works"}]}
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

    def test_a_second_separator_reaches_the_adapter_as_given(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        fx.flow("lead-init", cwd=fx.main)
        wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        fx.flow("gate", "FX-2", cwd=wt)
        code, out = fx.flow("app-check", "FX-2", "reader", "--mode", "loaded", "--path", "/one", "--eval", "true",
                            "--", "--", "--fixture-user", "reader", cwd=wt)
        record = json.loads((wt / ".orchestration/review/FX-2/app-checks/reader.json").read_text())
        self.assertEqual(record["passthrough"], ["--", "--fixture-user", "reader"], out)

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

    def no_change_lane(self) -> tuple[Fixture, Path, Path]:
        """FX-2, whose two criteria already hold, assigned and started in its lane's checkout from the base."""
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        code, out = fx.flow("lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane 1", cwd=m)
        self.assertEqual(code, 0, out)
        return fx, path, fx.lane("FX-2")

    def base(self, fx: Fixture) -> str:
        return sh("git", "rev-parse", "origin/feature/FX-1", cwd=fx.main).stdout.strip()

    def test_a_story_that_needs_no_change_is_handed_off_and_settled_with_no_branch_pr_or_base_commit(self) -> None:
        fx, path, wt = self.no_change_lane()
        m = fx.main
        before = self.base(fx)
        code, out = fx.flow("handoff", "FX-2", "--no-change", "--evidence",
                            "one-loads: test_one_loads passes at the base; one-fails: test_one_fails passes", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertIn("candidate: none, no change needed", out)
        self.assertIn(f"base head the evidence ran at: `{before}`", out)
        record = self.record(fx)
        self.assertEqual(record["state"]["queue"], [], "a no-change hand-off entered the merge queue")
        entry = next(p for p in record["state"]["planned"] if p["subject"] == "FX-2")
        self.assertEqual((entry["stage"], entry["noChange"]["baseHead"]), ("review", before))
        # No branch was pushed, no change request opened.
        self.assertEqual(sh("git", "ls-remote", "--heads", "origin", "feature/FX-2", cwd=m).stdout, "")
        code, out = fx.flow("lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn("accepted FX-2: settled with no change needed", out)
        record = self.record(fx)
        entry = next(p for p in record["state"]["planned"] if p["subject"] == "FX-2")
        self.assertEqual(entry["stage"], "settled")
        self.assertEqual(record["state"]["queue"], [])
        self.assertEqual(self.base(fx), before, "a base commit was made")
        self.assertEqual(sh("git", "ls-remote", "origin", "feature/FX-1", cwd=m).stdout.split()[0], before)
        # The plan keeps it, not removed, and counts every proof it owes done, as the line map and finish lines do.
        plan = self.plan(fx)
        unit = next(u for u in plan["units"] if u["subject"] == "FX-2")
        self.assertFalse(unit.get("removed", False))
        states = {kind: fx.call("proof_state", None, plan, self.record(fx), unit, kind, cwd=m, delivery="FX-1")
                  for kind in unit["proofs"]}
        self.assertEqual(set(states.values()), {"done"}, states)

    def test_a_base_that_moved_after_the_hand_off_still_settles_it(self) -> None:
        fx, path, wt = self.no_change_lane()
        m = fx.main
        code, out = fx.flow("handoff", "FX-2", "--no-change", "--evidence", "one-loads and one-fails hold", cwd=wt)
        self.assertEqual(code, 0, out)
        handed = self.base(fx)
        # Another clone moves the base, so the lead's checkout knows of it only once lead-accept fetches.
        other = fx.tmp / "other-clone"
        origin = sh("git", "remote", "get-url", "origin", cwd=m).stdout.strip()
        sh("git", "clone", "-q", "--branch", "feature/FX-1", origin, other)
        (other / "docs/later.md").write_text("later\n")
        commit(other, "FX-1: the base moves on")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=other)
        self.assertEqual(self.base(fx), handed, "the lead's checkout already knew the base had moved")
        code, out = fx.flow("lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertNotEqual(self.base(fx), handed, "lead-accept judged the hand-off against a base it never fetched")
        self.assertEqual(next(p for p in self.record(fx)["state"]["planned"] if p["subject"] == "FX-2")["stage"], "settled")

    def test_a_no_change_hand_off_from_a_head_the_base_doesnt_hold_is_refused(self) -> None:
        fx, path, wt = self.no_change_lane()
        m = fx.main
        # The lane's checkout holds a change: that is a hand-off with a PR.
        (wt / "src/one.txt").write_text("changed\n")
        commit(wt, "FX-2: a change")
        code, out = fx.flow("handoff", "FX-2", "--no-change", "--evidence", "one-loads and one-fails hold", cwd=wt)
        self.assertEqual(code, 2, out)
        self.assertIn("this checkout holds a change, so hand it off with --pr", out)
        self.assertEqual(next(p for p in self.record(fx)["state"]["planned"] if p["subject"] == "FX-2")["stage"], "planned")
        # The lead refuses evidence run at a head the base doesn't hold.
        stray = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir", fx.tmp / "record",
           "handoff", "--no-change", "--lane", "lane-1", "--subject", "FX-2", "--base-head", stray, "--evidence-text",
           "one-loads and one-fails hold")
        code, out = fx.flow("lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 1, out)
        self.assertIn(f"NOT accepted: FX-2", out)
        self.assertIn(f"the evidence ran at {stray[:10]}, which origin/feature/FX-1 doesn't hold", out)
        self.assertNotEqual(next(p for p in self.record(fx)["state"]["planned"] if p["subject"] == "FX-2")["stage"], "settled")

    def test_the_lead_settles_no_change_evidence_only_when_it_names_every_criterion(self) -> None:
        fx, path, wt = self.no_change_lane()
        m = fx.main
        code, out = fx.flow("handoff", "FX-2", "--no-change", "--evidence", "one-loads: test_one_loads passes", cwd=wt)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 1, out)
        self.assertIn("the evidence names no proof of criterion one-fails", out)
        # Nor does a longer token that only holds it.
        for longer in ("one-fails.extra", "one-failsafe", "not-one-fails-yet", "xone-fails"):
            with self.subTest(longer=longer):
                code, out = fx.flow("handoff", "FX-2", "--no-change", "--evidence", f"one-loads holds; {longer} holds", cwd=wt)
                self.assertEqual(code, 0, out)
                code, out = fx.flow("lead-accept", "FX-2", cwd=m)
                self.assertEqual(code, 1, out)
                self.assertIn("the evidence names no proof of criterion one-fails", out)
        self.assertNotIn("one-loads,", out)
        self.assertEqual(next(p for p in self.record(fx)["state"]["planned"] if p["subject"] == "FX-2")["stage"], "review")
        # Handed off again, from a file, naming both: accepted.
        evidence = fx.tmp / "evidence.md"
        # A sentence may end on a criterion id: its full stop isn't part of the id.
        evidence.write_text("one-loads: test_one_loads passes\ntest_one_fails proves one-fails.\n")
        code, out = fx.flow("handoff", "FX-2", "--no-change", "--evidence", f"@{evidence}", cwd=wt)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 0, out)
        code, again = fx.flow("lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 2, again)
        self.assertIn("FX-2 is already settled with no change", again)
        self.assertEqual(next(p for p in self.record(fx)["state"]["planned"] if p["subject"] == "FX-2")["noChange"]["evidence"],
                         "one-loads: test_one_loads passes\ntest_one_fails proves one-fails.")

    def test_a_no_change_story_with_no_criteria_is_refused_naming_it_and_the_fix(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        d = json.loads(path.read_text())
        del d["stories"]["FX-3"]["criteria"]
        d["finish"][0]["covers"]["criteria"] = ["one-loads", "one-fails"]
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: FX-3 has no criteria")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        fx.flow("lead-init", cwd=m)
        fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        wt = fx.lane("FX-3")
        code, out = fx.flow("handoff", "FX-3", "--no-change", "--evidence", "it holds", cwd=wt)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-accept", "FX-3", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-3 has no criteria", out)
        self.assertIn("fix: record its criteria first", out)

    def test_a_no_change_hand_off_takes_its_evidence_and_nothing_a_change_carries(self) -> None:
        fx, path, wt = self.no_change_lane()
        for argv, words in ((["--no-change"], "--no-change needs --evidence"),
                            (["--no-change", "--evidence", "x", "--pr", "https://forge.test/pr/1"], "no --pr"),
                            ([], "a hand-off needs --pr")):
            with self.subTest(argv=argv):
                code, out = fx.flow("handoff", "FX-2", *argv, cwd=wt)
                self.assertEqual(code, 2, out)
                self.assertIn(words, out)
        self.assertEqual(next(p for p in self.record(fx)["state"]["planned"] if p["subject"] == "FX-2")["stage"], "planned")

    def test_lead_assign_rebuilds_for_a_story_it_registers(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        d = json.loads(path.read_text())
        d["stories"]["FX-4"] = {"title": "Thing four", "lane": "lane-1", "why": "Split from FX-2.",
                                "criteria": [{"id": "four-works", "text": "Four works"}]}
        path.write_text(json.dumps(d, indent=2))
        plan = m / "docs/plan.md"
        plan.write_text(plan.read_text() + "\n### S4 (FX-4). Thing four\n\n- Scenarios: it four.\n")
        commit(m, "FX-1: a story added")
        code, out = fx.flow("lead-assign", "FX-4", "--lane", "lane-1", "--title", "Lane 1", cwd=m)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        self.assertEqual([(c["kind"], c["subjects"], c.get("why")) for c in plan["changes"]],
                         [("added", ["FX-4"], "Split from FX-2.")])
        self.assertEqual(plan["units"][-1]["subject"], "FX-4")

    def held(self, fx: Fixture) -> tuple:
        """What registration writes: the record's state and journal, plan.json and the changes log, byte for byte."""
        return (*self.written(fx), (fx.tmp / "record/state.json").read_bytes())

    def test_a_story_added_after_lead_init_without_a_why_is_refused_by_every_registering_command(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        add_story(fx, path, "FX-4")
        before = self.held(fx)
        for argv in (["lead-brief"], ["lead-reconcile"], ["lead-assign", "FX-4", "--lane", "lane-1", "--title", "Lane 1"],
                     ["lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                      "--text", "Eased", "--why", "Eased."]):
            head = sh("git", "rev-parse", "HEAD", cwd=m).stdout
            code, out = fx.flow(*argv, cwd=m)
            self.assertEqual(code, 2, f"{argv[0]} registered a story with no reason: {out}")
            self.assertIn("FX-4 has no `why`", out, argv)
            self.assertIn(f"fix: give it a `why` there", out, argv)
            self.assertIn("Nothing was registered", out, argv)
            self.assertEqual(self.held(fx), before, f"{argv[0]} wrote something before refusing")
            self.assertEqual((sh("git", "rev-parse", "HEAD", cwd=m).stdout, sh("git", "status", "--porcelain", cwd=m).stdout),
                             (head, ""), argv)
        self.assertNotIn("FX-4", [p["subject"] for p in self.record(fx)["state"]["planned"]])
        # A `why` the plan can't carry is refused the same way, naming what is wrong with it.
        for why, problem in (("", "must be non-empty text"), (7, "must be non-empty text"), ("x" * 5000, "over the cap"),
                             ("two\nlines", "control character")):
            d = json.loads(path.read_text())
            d["stories"]["FX-4"]["why"] = why
            path.write_text(json.dumps(d, indent=2))
            commit(m, "FX-1: a reason the plan can't carry")
            code, out = fx.flow("lead-brief", cwd=m)
            self.assertEqual(code, 2, out)
            self.assertIn("FX-4 has a `why` the plan can't carry", out)
            self.assertIn(problem, out)
            self.assertEqual(self.held(fx), before, repr(why))
        # Given its reason, the same story registers, and its `added` change carries it.
        d = json.loads(path.read_text())
        d["stories"]["FX-4"]["why"] = "A lane found the gap."
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: FX-4's reason")
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn("registered FX-4", out)
        self.assertEqual([(c["kind"], c["subjects"], c.get("why"), c.get("source")) for c in self.plan(fx)["changes"]],
                         [("added", ["FX-4"], "A lane found the gap.", "registration")])

    def test_lead_plan_adds_a_whole_story_in_one_command_which_lead_assign_then_accepts(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        # The tracker reconcile already filed the ticket as a follow-up: the add moves it out.
        state_script = fx.release / "skills/orchestrate-work/scripts/delivery_state.py"
        version = self.record(fx)["state"]["version"]
        sh(sys.executable, "-B", state_script, "--dir", fx.tmp / "record", "patch", "--writer", "lead", "--version",
           version, "--patch", json.dumps({"followups": [{"id": "FX-4", "title": "four", "reference": "FX-4"},
                                                         {"id": "FX-8", "title": "eight", "reference": "FX-8"}]}))
        head = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        code, out = fx.flow("lead-plan", "change", "--kind", "added", "--subjects", "FX-4", "--title", "Thing four",
                            "--text", "Thing four works", "--why", "A lane found the gap.", "--lane", "lane-1",
                            "--after", "FX-2", cwd=m)
        self.assertEqual(code, 0, out)
        # One commit holds the delivery-file story, with its reason, and the plan's heading.
        self.assertEqual(sh("git", "rev-list", "--count", f"{head}..HEAD", cwd=m).stdout.strip(), "1")
        self.assertEqual(sorted(sh("git", "show", "--name-only", "--format=", "HEAD", cwd=m).stdout.split()),
                         sorted([".agents/deliveries/FX-1.json", "docs/plan.md"]))
        self.assertEqual(sh("git", "status", "--porcelain", cwd=m).stdout, "")
        self.assertEqual(json.loads(path.read_text())["stories"]["FX-4"], {
            "title": "Thing four", "lane": "lane-1", "why": "A lane found the gap.",
            "criteria": [{"id": "fx-4-1", "text": "Thing four works"}], "after": ["FX-2"]})
        plan = (m / "docs/plan.md").read_text()
        self.assertIn("### FX-4. Thing four\n\nLane 1, tier 1, after FX-2. Added mid-delivery: A lane found the gap.\n",
                      plan)
        # The planned entry, and one `added` change with its reason: registration finds the story held.
        self.assertIn({"subject": "FX-4", "title": "Thing four", "lane": "lane-1", "stage": "planned"},
                      self.record(fx)["state"]["planned"])
        self.assertEqual([f["id"] for f in self.record(fx)["state"]["followups"]], ["FX-8"],
                         "the added story is still a follow-up, or another follow-up went with it")
        changes = lambda: [(c["kind"], c["subjects"], c.get("why"), c["source"]) for c in self.plan(fx)["changes"]]
        self.assertEqual(changes(), [("added", ["FX-4"], "A lane found the gap.", "lead-plan")])
        self.assertEqual(self.plan(fx)["units"][-1]["criteria"], [{"id": "fx-4-1", "text": "Thing four works"}])
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        for argv in (["lead-assign", "FX-4", "--lane", "lane-1", "--title", "Lane 1"], ["lead-brief"], ["lead-reconcile"]):
            code, out = fx.flow(*argv, cwd=m)
            self.assertEqual(code, 0, f"{argv[0]}: {out}")
            self.assertNotIn("has no `why`", out)
        self.assertEqual(changes(), [("added", ["FX-4"], "A lane found the gap.", "lead-plan")],
                         "registration recorded the story a second time")
        added = [e for e in self.record(fx)["journal"]
                 if e.get("subject") == "FX-4" and "added to the delivery as planned work" in e["summary"]]
        self.assertEqual(len(added), 1, added)

    def test_registration_waits_for_the_plans_lock_so_a_whole_story_add_is_never_registered_twice(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        # As lead-plan's add leaves it mid-way: the story in the delivery file, not yet planned, the lock held.
        add_story(fx, path, "FX-4", why="A lane found the gap.")
        lock = fx.tmp / "flow-log/plan.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            sweep = subprocess.Popen([sys.executable, "-B", str(fx.tool), "lead-brief"], cwd=m, env=fx.env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.addCleanup(lambda: sweep.poll() is None and (sweep.kill(), sweep.wait()))
            # Not a fixed wait, which a slow machine could outrun: the sweep is seen holding the lock file open, waiting
            # on it, before anything is asserted. Without the lock it opens the file only to record its change, after
            # it has already planned the story.
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and sweep.poll() is None:
                holders = subprocess.run(["lsof", "-t", str(lock)], capture_output=True, text=True).stdout.split()
                if str(sweep.pid) in holders:
                    break
                time.sleep(0.2)
            else:
                self.fail("the sweep never reached the plan's lock")
            self.assertIsNone(sweep.poll(), "registration ran without the plan's lock")
            self.assertNotIn("FX-4", [p["subject"] for p in self.record(fx)["state"]["planned"]],
                             "registration planned the story while the lock was held")
            fcntl.flock(held, fcntl.LOCK_UN)
        out, _ = sweep.communicate(timeout=300)
        self.assertEqual(sweep.returncode, 0, out)
        self.assertIn("registered FX-4", out)
        self.assertEqual([(c["kind"], c["subjects"]) for c in self.plan(fx)["changes"]], [("added", ["FX-4"])])

    def test_lead_plans_added_heading_takes_the_lanes_latest_tier_and_never_repeats_one(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        plan = m / "docs/plan.md"
        plan.write_text(plan.read_text() + "\n## Units and order\n\n| Tier | Lane | Items, in order | Owns |\n"
                        "| --- | --- | --- | --- |\n| 1 | 1 | FX-2 | `src/one.txt` |\n| 2 | 1 | FX-4 | `src/four.txt` |\n"
                        "\n### FX-5. Thing five\n\nWritten by the lead before the add.\n")
        commit(m, "FX-1: units and a heading for FX-5")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        fx.flow("lead-init", cwd=m)
        add = lambda key, title: fx.flow("lead-plan", "change", "--kind", "added", "--subjects", key, "--title", title,
                                         "--text", f"{title} works", "--why", "Found mid-way.", "--lane", "lane-1", cwd=m)
        code, out = add("FX-4", "Thing four")
        self.assertEqual(code, 0, out)
        self.assertIn("### FX-4. Thing four\n\nLane 1, tier 2, not blocked. Added mid-delivery: Found mid-way.\n",
                      plan.read_text())
        code, out = add("FX-5", "Thing five")
        self.assertEqual(code, 0, out)
        self.assertEqual(plan.read_text().count("### FX-5."), 1, "a heading the plan already held was written again")
        self.assertEqual(sh("git", "show", "--name-only", "--format=", "HEAD", cwd=m).stdout.split(),
                         [".agents/deliveries/FX-1.json"])
        self.assertEqual(json.loads(path.read_text())["stories"]["FX-5"]["why"], "Found mid-way.")

    def test_lead_plan_refuses_a_whole_story_it_cant_add_and_writes_nothing(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        story = ["lead-plan", "change", "--kind", "added", "--subjects", "FX-4", "--title", "Thing four", "--text", "It works"]
        before = lambda: (sh("git", "rev-parse", "HEAD", cwd=m).stdout, path.read_bytes(), (m / "docs/plan.md").read_bytes(),
                          self.held(fx))
        held = before()
        for argv, words in (
                ([*story, "--lane", "lane-1"], "the following arguments are required: --why"),
                ([*story, "--why", "W"], "--kind added adds it whole with --title, --text (its first criterion) and --lane"),
                ([*story, "--why", "W", "--lane", "lane-9"], "--lane lane-9 isn't a lane of FX-1"),
                ([*story, "--why", "W", "--lane", "lane-1", "--after", "FX-8"], "--after FX-8 isn't a story of FX-1"),
                ([*story, "--why", "W", "--lane", "lane-1", "--criterion", "one-loads"], "criterion one-loads already exists"),
                ([*story, "--why", "", "--lane", "lane-1"], "FX-4 has a `why` the plan can't carry"),
                (["lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-4", "--criterion", "x", "--text", "y",
                  "--why", "W"], "FX-4 isn't a story of FX-1")):
            with self.subTest(argv=argv):
                code, out = fx.flow(*argv, cwd=m)
                self.assertEqual(code, 2, out)
                self.assertIn(words, out)
                self.assertEqual(before(), held)
                self.assertEqual(sh("git", "status", "--porcelain", cwd=m).stdout, "")
        # Built on registration's refusal: while a story added by hand waits without its reason, the add refuses too,
        # naming that story, and writes nothing.
        add_story(fx, path, "FX-5")
        held = before()
        code, out = fx.flow(*story, "--why", "W", "--lane", "lane-1", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-5 has no `why`", out)
        self.assertEqual(before(), held)

    def test_lead_plan_removes_a_story_added_without_a_why_and_records_no_added_change_for_it(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        add_story(fx, path, "FX-4")
        code, out = fx.flow("lead-plan", "change", "--kind", "removed", "--subjects", "FX-4", "--why", "Dropped.", cwd=m)
        self.assertEqual(code, 0, f"the story registration refuses couldn't be taken out: {out}")
        self.assertNotIn("FX-4", json.loads(path.read_text())["stories"])
        self.assertEqual([(c["kind"], c["subjects"], c.get("why")) for c in self.plan(fx)["changes"]],
                         [("removed", ["FX-4"], "Dropped.")])
        logged = [json.loads(line) for line in (fx.tmp / "flow-log/plan-changes.jsonl").read_text().splitlines()]
        self.assertFalse([e for e in logged if e["type"] == "change" and e["kind"] == "added"], logged)
        self.assertNotIn("FX-4", [p["subject"] for p in self.record(fx)["state"]["planned"]])
        # The exemption is the removed story's alone: another added without its reason still refuses the removal.
        add_story(fx, path, "FX-5")
        add_story(fx, path, "FX-6")
        before = (sh("git", "rev-parse", "HEAD", cwd=m).stdout, path.read_bytes(), self.held(fx))
        code, out = fx.flow("lead-plan", "change", "--kind", "removed", "--subjects", "FX-5", "--why", "Dropped.", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-6 has no `why`", out)
        self.assertNotIn("FX-5 has no `why`", out)
        # Refused before anything is written: no commit, no edit to the delivery file, nothing in the record or the log.
        self.assertEqual((sh("git", "rev-parse", "HEAD", cwd=m).stdout, path.read_bytes(), self.held(fx)), before)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=m).stdout, "")

    def test_every_registering_command_copies_the_stories_why_into_its_added_change(self) -> None:
        for argv in (["lead-brief"], ["lead-reconcile"], ["lead-assign", "FX-4", "--lane", "lane-1", "--title", "Lane 1"]):
            with self.subTest(argv[0]):
                fx = self.fixture()
                path = plan_delivery(fx)
                fx.flow("lead-init", cwd=fx.main)
                add_story(fx, path, "FX-4", why=f"Added for {argv[0]}.")
                code, out = fx.flow(*argv, cwd=fx.main)
                self.assertEqual(code, 0, out)
                self.assertIn("FX-4", [p["subject"] for p in self.record(fx)["state"]["planned"]])
                changes = self.plan(fx)["changes"]
                self.assertEqual([(c["kind"], c["subjects"], c.get("why")) for c in changes],
                                 [("added", ["FX-4"], f"Added for {argv[0]}.")])
                logged = [json.loads(line) for line in (fx.tmp / "flow-log/plan-changes.jsonl").read_text().splitlines()]
                self.assertEqual([e.get("why") for e in logged if e["type"] == "change"], [f"Added for {argv[0]}."],
                                 "the changes log itself lacks the reason")

    def test_the_supervisors_sweep_leaves_a_story_with_no_why_unregistered_and_goes_on(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        add_story(fx, path, "FX-4")
        before = self.held(fx)
        lines = call(fx.tool, "reconcile_once", cwd=m, env=fx.env, delivery="FX-1")
        self.assertIn("registration refused: FX-4 has no `why`", "; ".join(lines))
        self.assertTrue(any(line.startswith("forgeSnapshot:") for line in lines), lines)
        self.assertFalse(any(line.startswith("plan:") for line in lines), f"the sweep rebuilt the plan around it: {lines}")
        self.assertEqual(self.held(fx)[2], before[2], "the sweep recorded a change for it")
        self.assertNotIn("FX-4", [p["subject"] for p in self.record(fx)["state"]["planned"]])

    def test_a_story_added_between_lead_setup_and_lead_init_is_frozen_with_the_plan_and_needs_no_why(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        add_story(fx, path, "FX-4")
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        for argv in (["lead-brief"], ["lead-reconcile"], ["lead-assign", "FX-4", "--lane", "lane-1", "--title", "Lane 1"]):
            code, out = fx.flow(*argv, cwd=m)
            self.assertEqual(code, 0, f"{argv[0]}: {out}")
        plan = self.plan(fx)
        self.assertIn("FX-4", [u["subject"] for u in plan["approved"]["snapshot"]["units"]])
        self.assertEqual(plan["changes"], [], "a story frozen with the plan read as an added change")
        self.assertIn("FX-4", [p["subject"] for p in self.record(fx)["state"]["planned"]])

    def test_concurrent_rebuilds_lose_no_update(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        fx.flow("lead-init", cwd=m)
        d = json.loads(path.read_text())
        for n in range(4, 10):
            d["stories"][f"FX-{n}"] = {"title": f"Thing {n}", "lane": "lane-1", "why": f"Thing {n} was missed.",
                                       "criteria": [{"id": f"c-{n}", "text": f"{n} works"}]}
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
        "state": {"directory": ".orchestration/state", "renderers": [],
                  "publishers": [{"id": "tracker", "argv": [
                      "python3", "-B", ".agent-skills/current/skills/run-delivery/scripts/tracker_publisher.py",
                      "--state-dir", "{stateDirectory}"], "timeoutSeconds": 600}],
                  "forgeSnapshot": {"argv": ["true"]},
                  "trackerSnapshot": {"argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
                                               "--kind", "jira", "--site", "https://tracker.test", "--parent", "{parent}",
                                               "--user-env", "FX_TRACKER_USER", "--token-env", "FX_TRACKER_TOKEN"],
                                      "timeoutSeconds": 30}},
    }


def doctor_lines(out: str) -> dict[str, tuple[str, str]]:
    """Each check's line, by its label: (status, the rest)."""
    found = {}
    for line in out.splitlines():
        match = re.match(r"^(ok|missing|wrong|skipped|warn) +([^:]+): (.*)$", line)
        if match:
            found[match.group(2)] = (match.group(1), match.group(3))
    return found


def failing(out: str) -> dict[str, str]:
    return {label: rest for label, (status, rest) in doctor_lines(out).items() if status not in ("ok", "skipped", "warn")}


# The words each failing doctor line starts its detail with, as the adoption guide's troubleshooting section keys them.
TROUBLESHOOTING_KEYS = [
    "this host lacks", "no .agents/run-delivery.json", "the repository's configs disagree", "earlier names in use",
    "no receipt at", "is neither the current nor the previous generation", "is the receipt's previous one",
    "no .agents/orchestration.json", "isn't JSON", "which RUN_DELIVERY_CONFIG names", "doesn't validate", "no delivery base", "the delivery base",
    "has no .agent-skills/current", "isn't a link inside the repository", "doesn't carry", "carries generation",
    "no gates are declared", "these don't resolve", "isn't ignored by git", "no tracker issue URL",
    "review.pr is configured, but", "gh isn't installed", "gh isn't signed in",
    "no token variable for the Bitbucket adapter", "the Bitbucket adapter reads its token from it", "forge find failed",
    "the tracker snapshot or adapter reads", "the binding names a publisher that runs nothing", "these in-app adapters don't resolve", "isn't writable",
    "the in-app adapters break the contract", "lease held by", "video evidence needs both", "which video evidence uses",
    "needs Node 22.6 or newer", "no runtime loader", "harness.config.ts not read", "is missing (lead-setup writes it)",
    "stories without a lane", "lanes own the same files", "fails on", "isn't a valid delivery-plan/1",
    "the binding has no state.forgeSnapshot", "the binding has no tracker publisher", "and the runtime needs Node",
    "is below the runtime's nodeMinimum", "stories with no plan section", "context.sections the plan lacks",
    "no plan at", "has no configuration for lanes", "forge.mergeNeedsReady says the merge step refuses a draft",
    "the supervisor is down", "don't have the default signal dispositions",
    "binding doesn't serve the base", "isn't offered; refine it first",
]


def troubleshooting_section() -> str:
    text = (ROOT / "skills/run-delivery/references/adopting.md").read_text(encoding="utf-8")
    return text[text.index("## Troubleshooting"):]


DOCTOR_LABELS = ["host and tools", "orchestration binding", "merge queue candidate step", "configuration", "release generation",
                 "host hooks", "release on the delivery base", "gates", "lane checkouts ignored", "tracker issue URL",
                 "pipeline PR review", "gh", "forge find", "forge write access", "tracker snapshot token",
                 "snapshots and publishers", "in-app adapters", "in-app adapter contract", "video evidence tools",
                 "Node", "harness configuration", "delivery file", "plan chapter", "added stories' reasons",
                 "plan sections", "story files", "story readiness", "context sections",
                 "configuration on the remote base", "pull requests marked ready", "delivery supervisor",
                 "gates on the base"]


SUPERVISOR_STAND_IN = r'''
import datetime, fcntl, json, os, signal, sys, time
lock = open(sys.argv[1], "a+")
fcntl.flock(lock, fcntl.LOCK_EX)
lock.truncate(0)
pid = json.loads(sys.argv[3]) if len(sys.argv) > 3 else os.getpid()
lock.write(json.dumps({"pid": pid, "startedAt": "2026-01-02T03:04:05Z", "children": []}))
lock.flush()
if sys.argv[2] == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("holding", flush=True)
time.sleep(120)
'''


# A tracker command that is the snapshot producer itself, answered from a file rather than the network: the
# file maps each parent to its state type, or to "fail" for a command that fails.
FAKE_TRACKER = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, ".agent-skills/current/skills/delivery-views/scripts")
import snapshot

parent = sys.argv[1]
kind = json.loads(Path("tools/tracker-states.json").read_text()).get(parent, "started")
if kind.startswith("as:"):  # a command that ignores {parent} and answers for another issue
    _, parent, kind = kind.split(":")
if kind == "fail":
    print("the tracker is down", file=sys.stderr)
    raise SystemExit(3)
failing = kind.startswith("printfail:")  # a command that prints a whole snapshot, then exits non-zero
if failing:
    kind = kind.split(":", 1)[1]
answer = {"data": {"issue": {"createdAt": "2026-10-20T08:00:00.000Z", "state": {"type": kind}, "children": {
    "nodes": [{"identifier": parent + "-child", "createdAt": "2026-10-20T09:00:00.000Z", "title": "A child",
               "description": None, "state": {"type": "started"}, "inverseRelations": {"nodes": []}}],
    "pageInfo": {"hasNextPage": False}}}}}

def transport(method, url, headers, timeout, body=None):
    return snapshot.Response(200, {}, json.dumps(answer).encode())

code, out, err = snapshot.run(["--kind", "linear", "--parent", parent, "--token-env", "T"], environ={"T": "x"},
                              transport=transport)
sys.stdout.write(out)
sys.stderr.write(err)
raise SystemExit(3 if failing else code)
'''


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryDefaultDeliveryTests(unittest.TestCase):
    """A lead command without --delivery: the one delivery whose record is open here, else the one whose story branch
    this checkout is on, else the only one not known to be closed, else a refusal naming each one not known to be closed. The tracker is read through the snapshot producer."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self, states: dict[str, str]) -> Fixture:
        """FX-1 and a second delivery, FX-5, both on the fixture's base, with a binding whose tracker snapshot runs
        the producer for each parent, its state taken from `states`."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        path = setup_delivery(fx)
        m = fx.main
        second = json.loads(path.read_text())
        second.update(delivery="FX-5", headline="FX-5: the second delivery", stateDirectory=str(fx.tmp / "record-5"),
                      stories={"FX-6": {"title": "Thing six", "lane": "lane-1"}})
        (m / ".agents/deliveries/FX-5.json").write_text(json.dumps(second, indent=2))
        (m / "tools/fake_tracker.py").write_text(FAKE_TRACKER)
        (m / "tools/tracker-states.json").write_text(json.dumps(states))
        (m / ".agents/orchestration.json").write_text(json.dumps({"state": {"trackerSnapshot": {
            "argv": [sys.executable, "-B", "tools/fake_tracker.py", "{parent}"], "timeoutSeconds": 60}}}, indent=2))
        commit(m, "FX-5: a second delivery, and the binding's tracker snapshot")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        return fx

    def picked(self, fx: Fixture) -> tuple[int, str, list[str]]:
        """What a lead command without --delivery chose: its exit code, output, and the stories it listed."""
        code, out = fx.flow("lead-hints", cwd=fx.main)
        try:  # the hints, then any note on the delivery's supervisor
            stories = list(json.JSONDecoder().raw_decode(out)[0]["items"])
        except ValueError:
            stories = []
        return code, out, stories

    def closed_here(self, fx: Fixture, key: str, beside: Path | None = None) -> None:
        """What lead-close leaves in this machine's flow log, beside the record (the fixture's, by default)."""
        folder = (beside or fx.tmp) / "flow-log"
        folder.mkdir(exist_ok=True)
        (folder / f"{key}.closed.json").write_text(json.dumps({"by": "lead-close"}))

    def test_one_closed_and_one_not_closed_delivery_file_selects_the_not_closed_one(self) -> None:
        fx = self.fixture({})
        self.closed_here(fx, "FX-1")
        code, out, stories = self.picked(fx)
        self.assertEqual((code, stories), (0, ["FX-6"]), out)
        self.closed_here(fx, "FX-5")
        (fx.tmp / "flow-log/FX-1.closed.json").unlink()
        code, out, stories = self.picked(fx)
        self.assertEqual((code, stories), (0, ["FX-2", "FX-3"]), out)

    def test_two_not_closed_with_no_record_open_here_and_their_parents_started_is_refused_naming_both(self) -> None:
        fx = self.fixture({"FX-1": "started", "FX-5": "started"})
        code, out, _ = self.picked(fx)
        self.assertEqual(code, 2, out)
        self.assertIn("can't tell which delivery this is", out)
        self.assertIn("FX-1, FX-5 aren't known to be closed, and no record of theirs is open here", out)
        self.assertIn("pass --delivery KEY", out)

    def test_two_not_closed_with_one_record_open_here_selects_that_one(self) -> None:
        fx = self.fixture({"FX-1": "started", "FX-5": "started"})
        m = fx.main
        path = m / ".agents/deliveries/FX-5.json"
        d = json.loads(path.read_text())
        d["stateDirectory"] = ".orchestration/state-5"  # inside this checkout
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-5: its record in the lead's checkout")
        tool = fx.release / "skills/orchestrate-work/scripts/delivery_state.py"
        sh(sys.executable, "-B", tool, "--dir", m / ".orchestration/state-5", "init", "--headline", "FX-5")
        # FX-1's record is open too, but outside this checkout, so it isn't this checkout's.
        sh(sys.executable, "-B", tool, "--dir", fx.tmp / "record", "init", "--headline", "FX-1")
        code, out, stories = self.picked(fx)
        self.assertEqual((code, stories), (0, ["FX-6"]), out)
        # A record that names another tracker parent isn't this delivery's.
        shown = json.loads(sh(sys.executable, "-B", tool, "--dir", m / ".orchestration/state-5", "show").stdout)
        sh(sys.executable, "-B", tool, "--dir", m / ".orchestration/state-5", "patch", "--writer", "lead", "--version",
           str(shown["state"]["version"]), "--patch", json.dumps({"meta": {"trackerParent": "FX-9"}}))
        code, out, _ = self.picked(fx)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-1, FX-5 aren't known to be closed", out)
        shown = json.loads(sh(sys.executable, "-B", tool, "--dir", m / ".orchestration/state-5", "show").stdout)
        sh(sys.executable, "-B", tool, "--dir", m / ".orchestration/state-5", "patch", "--writer", "lead", "--version",
           str(shown["state"]["version"]), "--patch", json.dumps({"meta": {"trackerParent": "FX-5"}}))
        code, out, stories = self.picked(fx)
        self.assertEqual((code, stories), (0, ["FX-6"]), out)
        # Once it is closed here, it is no longer the open record, and FX-1 is the only one not known to be closed.
        self.closed_here(fx, "FX-5", beside=m / ".orchestration")
        code, out, stories = self.picked(fx)
        self.assertEqual((code, stories), (0, ["FX-2", "FX-3"]), out)

    def test_a_delivery_closed_from_another_checkout_counts_as_closed_by_its_parent_completed_or_canceled(self) -> None:
        for state in ("completed", "canceled"):
            with self.subTest(state=state):
                # FX-1's file is on the base and its parent is closed in the tracker, with no closed.json here.
                fx = self.fixture({"FX-1": state, "FX-5": "started"})
                self.assertFalse((fx.tmp / "flow-log/FX-1.closed.json").exists())
                code, out, stories = self.picked(fx)
                self.assertEqual((code, stories), (0, ["FX-6"]), out)
        # A snapshot that answers for another issue says nothing of this delivery's parent.
        (fx.main / "tools/tracker-states.json").write_text(json.dumps({"FX-1": "as:FX-9:completed", "FX-5": "started"}))
        commit(fx.main, "a tracker command that ignores its parent")
        code, out, _ = self.picked(fx)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-1, FX-5 aren't known to be closed", out)

    def test_a_lane_on_a_story_branch_takes_that_story_s_delivery(self) -> None:
        fx = self.fixture({"FX-1": "started", "FX-5": "started"})
        wt = fx.main / "lanes/FX-6"
        sh("git", "worktree", "add", "-q", "--no-track", "-b", "feature/FX-6", wt, "origin/feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-hints", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(list(json.JSONDecoder().raw_decode(out)[0]["items"]), ["FX-6"])

    def test_a_lone_delivery_file_is_taken_even_once_closed_so_the_commands_after_its_close_still_find_it(self) -> None:
        # With one file there is nothing to choose between: a repository with one delivery never names it, before or
        # after its close, so the lone delivery is taken whatever the tracker or the flow log says.
        fx = self.fixture({"FX-1": "completed", "FX-5": "completed"})
        (fx.main / ".agents/deliveries/FX-5.json").unlink()
        commit(fx.main, "FX-5 gone: one delivery file")
        self.closed_here(fx, "FX-1")
        code, out, stories = self.picked(fx)
        self.assertEqual((code, stories), (0, ["FX-2", "FX-3"]), out)

    def test_the_story_branch_rule_comes_before_the_only_delivery_not_known_to_be_closed(self) -> None:
        # A deliberate addition to the default's order: FX-5's parent is completed, so FX-1 is the only delivery not
        # known to be closed, yet a lane on FX-5's story branch still takes FX-5, as lanes did before the order.
        fx = self.fixture({"FX-1": "started", "FX-5": "completed"})
        code, out, stories = self.picked(fx)
        self.assertEqual((code, stories), (0, ["FX-2", "FX-3"]), out)
        wt = fx.main / "lanes/FX-6"
        sh("git", "worktree", "add", "-q", "--no-track", "-b", "feature/FX-6", wt, "origin/feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-hints", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(list(json.JSONDecoder().raw_decode(out)[0]["items"]), ["FX-6"])

    def test_the_record_open_here_comes_before_the_only_delivery_not_known_to_be_closed(self) -> None:
        # FX-5's parent is completed but its record is still open here, with no lead-close yet: the record decides,
        # though FX-1 is the only delivery not known to be closed.
        fx = self.fixture({"FX-1": "started", "FX-5": "completed"})
        code, out, stories = self.picked(fx)
        self.assertEqual((code, stories), (0, ["FX-2", "FX-3"]), out)
        m = fx.main
        path = m / ".agents/deliveries/FX-5.json"
        d = json.loads(path.read_text())
        d["stateDirectory"] = ".orchestration/state-5"  # inside this checkout
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-5: its record in the lead's checkout")
        tool = fx.release / "skills/orchestrate-work/scripts/delivery_state.py"
        sh(sys.executable, "-B", tool, "--dir", m / ".orchestration/state-5", "init", "--headline", "FX-5")
        code, out, stories = self.picked(fx)
        self.assertEqual((code, stories), (0, ["FX-6"]), out)

    def test_a_record_open_in_a_checkout_nested_inside_this_one_is_not_open_here(self) -> None:
        fx = self.fixture({"FX-1": "started", "FX-5": "started"})
        m = fx.main
        lead = m / "leads/FX-5"  # FX-5's lead checkout, nested in this one as a lane's is under the lanes folder
        path = m / ".agents/deliveries/FX-5.json"
        d = json.loads(path.read_text())
        d["stateDirectory"] = str(lead / ".orchestration/state")
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-5: its record in its own lead checkout")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        sh("git", "worktree", "add", "-q", "--no-track", "-b", "lead-5", lead, "origin/feature/FX-1", cwd=m)
        tool = fx.release / "skills/orchestrate-work/scripts/delivery_state.py"
        sh(sys.executable, "-B", tool, "--dir", lead / ".orchestration/state", "init", "--headline", "FX-5")
        # From the enclosing checkout, the record is the nested checkout's, not this one's.
        code, out, _ = self.picked(fx)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-1, FX-5 aren't known to be closed, and no record of theirs is open here", out)
        # From the lead checkout itself, it is open here.
        code, out = fx.flow("lead-hints", cwd=lead)
        self.assertEqual(code, 0, out)
        self.assertEqual(list(json.JSONDecoder().raw_decode(out)[0]["items"]), ["FX-6"])

    def test_the_only_delivery_left_after_those_closed_here_is_refused_when_its_parent_is_closed(self) -> None:
        fx = self.fixture({"FX-1": "started", "FX-5": "completed"})
        self.closed_here(fx, "FX-1")
        code, out, _ = self.picked(fx)
        self.assertEqual(code, 2, out)
        self.assertIn("deliveries FX-1, FX-5, none of them open", out)

    def test_a_snapshot_command_that_fails_leaves_its_delivery_not_known_to_be_closed(self) -> None:
        fx = self.fixture({"FX-1": "fail", "FX-5": "started"})
        code, out, _ = self.picked(fx)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-1, FX-5 aren't known to be closed", out)
        # A command that prints a whole snapshot, parent completed, but exits non-zero says nothing either.
        (fx.main / "tools/tracker-states.json").write_text(json.dumps({"FX-1": "printfail:completed", "FX-5": "started"}))
        commit(fx.main, "a tracker command that prints, then fails")
        code, out, _ = self.picked(fx)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-1, FX-5 aren't known to be closed", out)
        # The same with no tracker snapshot at all.
        (fx.main / ".agents/orchestration.json").write_text("{}\n")
        commit(fx.main, "no tracker snapshot")
        code, out, _ = self.picked(fx)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-1, FX-5 aren't known to be closed", out)

    def test_a_missing_delivery_file_names_the_open_delivery_and_says_when_the_key_is_a_story(self) -> None:
        fx = self.fixture({"FX-1": "completed", "FX-5": "started"})
        code, out = fx.flow("--delivery", "FX-6", "lead-accept", "FX-6", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-6.json is missing (lead-setup writes it)", out)
        self.assertIn("FX-6 looks like a story key: it is a story of FX-5", out)
        self.assertIn("the open delivery is FX-5: pass --delivery FX-5", out)
        code, out = fx.flow("--delivery", "FX-9", "lead-accept", "FX-6", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-9 is no story of any delivery here either", out)
        # With none to choose, it names those not known to be closed.
        (fx.main / "tools/tracker-states.json").write_text(json.dumps({"FX-1": "started", "FX-5": "started"}))
        commit(fx.main, "both open")
        code, out = fx.flow("--delivery", "FX-6", "lead-accept", "FX-6", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("the deliveries not known to be closed are FX-1, FX-5", out)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryLeadWorktreeTests(unittest.TestCase):
    """lead-plan from a lead's own worktree on its own branch, as when several deliveries share one machine and the
    operator's checkout already holds the base: its HEAD at the base's tip, or ahead with this delivery's own plan
    commits only. It commits there and prints the push to the base; it never pushes."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self) -> tuple[Fixture, Path, Path]:
        """The plan fixture, initialised, with one approval waiting, and the lead's worktree on lead/FX-1 at the base."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        m = fx.main
        path = plan_delivery(fx)
        d = json.loads(path.read_text())
        d["approvals"] = [{"id": "approval-1", "text": "Copy sign-off", "state": "waiting", "blocking": True}]
        path.write_text(json.dumps(d, indent=2))
        # The binding names the base, so a checkout on another branch reads the base on the remote, as a lane does.
        binding = m / ".agents/orchestration.json"
        bound = json.loads(binding.read_text()) if binding.exists() else {}
        bound.setdefault("base", {}).update(remote="origin", ref="feature/FX-1")
        bound.setdefault("state", {}).setdefault("forgeSnapshot", {"argv": ["true"]})
        binding.write_text(json.dumps(bound, indent=2) + "\n")
        commit(m, "FX-1: an approval, and the binding's base")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        lead = fx.tmp / "lead-FX-1"
        sh("git", "worktree", "add", "-q", "--no-track", "-b", "lead/FX-1", lead, "origin/feature/FX-1", cwd=m)
        return fx, lead, lead / ".agents/deliveries/FX-1.json"

    def pushed(self, fx: Fixture, out: str, lead: Path) -> dict:
        """Run the push the command printed, from the worktree, and read the delivery file the base then holds."""
        line = next(line for line in out.splitlines() if "git push" in line)
        command = line[line.index("git push"):].split()
        self.assertEqual(command, ["git", "push", "origin", "HEAD:feature/FX-1"], out)
        sh(*command, "-q", cwd=lead)
        return json.loads(sh("git", "show", "origin/feature/FX-1:.agents/deliveries/FX-1.json", cwd=lead).stdout)

    def test_an_approval_from_a_lead_worktree_at_the_base_tip_commits_there_and_reaches_the_base(self) -> None:
        fx, lead, path = self.fixture()
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", "--who", "the operator", cwd=lead)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(path.read_text())["approvals"][0]["state"], "done")
        self.assertEqual(sh("git", "log", "-1", "--format=%s", cwd=lead).stdout.strip(), "FX-1: approval approval-1 done")
        self.assertEqual(sh("git", "status", "--porcelain", cwd=lead).stdout, "")
        # It never pushes: the base still has it waiting until the lead runs the push it printed.
        on_base = json.loads(sh("git", "show", "origin/feature/FX-1:.agents/deliveries/FX-1.json", cwd=lead).stdout)
        self.assertEqual(on_base["approvals"][0]["state"], "waiting")
        self.assertEqual(self.pushed(fx, out, lead)["approvals"][0]["state"], "done")

    def test_with_two_deliveries_open_a_lead_worktree_names_its_delivery_and_is_never_handed_another(self) -> None:
        fx, lead, path = self.fixture()
        m = fx.main
        # A second delivery on the same base, open too: its file reaches the lead's worktree with the base.
        other = json.loads((m / ".agents/deliveries/FX-1.json").read_text())
        stories = {f"FX-9{key.split('-')[1]}": story for key, story in other["stories"].items()}
        (m / ".agents/deliveries/FX-9.json").write_text(json.dumps({**other, "delivery": "FX-9", "stories": stories},
                                                                   indent=2))
        commit(m, "FX-9: a second delivery")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        sh("git", "fetch", "-q", "origin", cwd=lead)
        sh("git", "merge", "-q", "--ff-only", "origin/feature/FX-1", cwd=lead)
        # Neither record is open in the worktree and lead/FX-1 is no story branch, so without --delivery it refuses,
        # naming both, rather than taking one, and writes nothing.
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", "--who", "the operator", cwd=lead)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-1, FX-9 aren't known to be closed", out)
        self.assertIn("pass --delivery KEY", out)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=lead).stdout, "")
        self.assertEqual(json.loads(path.read_text())["approvals"][0]["state"], "waiting")
        # Named, it records FX-1's approval in FX-1's file only.
        code, out = fx.flow("--delivery", "FX-1", "lead-plan", "approval", "approval-1", "--state", "done", "--who",
                            "the operator", cwd=lead)
        self.assertEqual(code, 0, out)
        self.assertEqual(sh("git", "show", "--name-only", "--format=", "HEAD", cwd=lead).stdout.split(),
                         [".agents/deliveries/FX-1.json"])
        self.assertEqual(self.pushed(fx, out, lead)["approvals"][0]["state"], "done")
        on_base = json.loads(sh("git", "show", "origin/feature/FX-1:.agents/deliveries/FX-9.json", cwd=lead).stdout)
        self.assertEqual(on_base["approvals"][0]["state"], "waiting")
        # The other delivery's story branch is a lane's too, though none of FX-1's stories is on it.
        lane = fx.tmp / "lane-FX-92"
        sh("git", "worktree", "add", "-q", "--no-track", "-b", "feature/FX-92", lane, "origin/feature/FX-1", cwd=m)
        code, out = fx.flow("--delivery", "FX-1", "lead-plan", "approval", "approval-1", "--state", "waiting",
                            cwd=lane)
        self.assertEqual(code, 2, out)
        self.assertIn("feature/FX-92 is a story branch", out)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=lane).stdout, "")

    def test_a_change_from_a_lead_worktree_at_the_base_tip_commits_there_and_reaches_the_base(self) -> None:
        fx, lead, path = self.fixture()
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--text", "Thing one says something when it fails", "--why", "The copy is not settled.",
                            cwd=lead)
        self.assertEqual(code, 0, out)
        criteria = {c["id"]: c["text"] for c in json.loads(path.read_text())["stories"]["FX-2"]["criteria"]}
        self.assertEqual(criteria["one-fails"], "Thing one says something when it fails")
        self.assertEqual(sh("git", "status", "--porcelain", cwd=lead).stdout, "")
        # The plan it rebuilds reads the worktree's delivery file, not the base's older one.
        plan = json.loads((fx.tmp / "record/plan.json").read_text())
        self.assertEqual([(c["kind"], c["after"]) for c in plan["changes"]],
                         [("relaxed", "Thing one says something when it fails")])
        # More changes before that push are ahead of the base with this delivery's own plan commits only: allowed.
        # An added story commits the plan beside the delivery file.
        code, out = fx.flow("lead-plan", "change", "--kind", "added", "--subjects", "FX-7", "--title", "Thing seven",
                            "--text", "Thing seven loads", "--why", "The operator asked for it.", "--lane", "lane-1",
                            cwd=lead)
        self.assertEqual(code, 0, out)
        self.assertIn("### FX-7. Thing seven", (lead / "docs/plan.md").read_text())
        # A story only the worktree's unpushed commit holds is this delivery's own: one added after it is taken.
        code, out = fx.flow("lead-plan", "change", "--kind", "added", "--subjects", "FX-8", "--title", "Thing eight",
                            "--text", "Thing eight loads", "--why", "It follows seven.", "--lane", "lane-1",
                            "--after", "FX-7", cwd=lead)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(path.read_text())["stories"]["FX-8"].get("after"), ["FX-7"])
        # A story only the worktree holds can be removed again before any push.
        code, out = fx.flow("lead-plan", "change", "--kind", "removed", "--subjects", "FX-8", "--why", "Not needed.",
                            cwd=lead)
        self.assertEqual(code, 0, out)
        self.assertNotIn("FX-8", json.loads(path.read_text())["stories"])
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", cwd=lead)
        self.assertEqual(code, 0, out)
        # Before any push, the rebuilt plan is the worktree's: the changes and the approval, none the base's older file.
        plan = json.loads((fx.tmp / "record/plan.json").read_text())
        self.assertEqual(plan["approvals"][0]["state"], "done")
        self.assertIn("FX-7", [unit["subject"] for unit in plan["units"]])
        self.assertEqual([c["kind"] for c in plan["changes"]], ["relaxed", "added", "added", "removed"])
        on_base = self.pushed(fx, out, lead)
        on_base_criteria = {c["id"]: c["text"] for c in on_base["stories"]["FX-2"]["criteria"]}
        self.assertEqual((on_base_criteria["one-fails"], on_base["approvals"][0]["state"], "FX-7" in on_base["stories"]),
                         ("Thing one says something when it fails", "done", True))

    def test_a_removal_after_an_unpushed_change_records_the_worktree_s_story(self) -> None:
        fx, lead, path = self.fixture()
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-3", "--criterion",
                            "two-renders", "--text", "Thing two renders, in time", "--why", "w", cwd=lead)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-plan", "change", "--kind", "removed", "--subjects", "FX-3", "--why", "Dropped.",
                            cwd=lead)
        self.assertEqual(code, 0, out)
        logged = [json.loads(line) for line in (fx.tmp / "flow-log/plan-changes.jsonl").read_text().splitlines()]
        removed = [entry for entry in logged if entry.get("type") == "change" and entry.get("kind") == "removed"]
        self.assertEqual(len(removed), 1, logged)
        # What was removed is the story as the worktree holds it, relaxed, not the base's older text.
        self.assertIn("Thing two renders, in time", json.dumps(removed[0]["units"]))
        self.assertNotIn('"Thing two renders"', json.dumps(removed[0]["units"]))

    def test_a_lead_worktree_behind_the_base_is_refused_naming_the_fix(self) -> None:
        fx, lead, path = self.fixture()
        (fx.main / "src/app.txt").write_text("moved on\n")
        commit(fx.main, "the base moves on")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        before = path.read_text()
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", cwd=lead)
        self.assertEqual(code, 2, out)
        self.assertIn(f"{lead} is behind origin/feature/FX-1", out)
        self.assertIn("git merge --ff-only origin/feature/FX-1", out)
        self.assertEqual(path.read_text(), before)
        self.assertEqual(sh("git", "rev-list", "--count", "origin/feature/FX-1..HEAD", cwd=lead).stdout.strip(), "0")

    def test_a_lead_worktree_ahead_with_another_lead_s_commit_is_refused_naming_it(self) -> None:
        fx, lead, path = self.fixture()
        # Another delivery's lead committed here, unpushed; a path with a space is named whole.
        (lead / "docs/other plan.md").write_text("# FX-9\n")
        commit(lead, "FX-9: its plan")
        other = sh("git", "rev-parse", "--short=12", "HEAD", cwd=lead).stdout.strip()
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", cwd=lead)
        self.assertEqual(code, 2, out)
        self.assertIn(f"{lead} is ahead of origin/feature/FX-1 with {other} (FX-9: its plan), which changes "
                      "docs/other plan.md: a lead worktree", out)
        self.assertIn("only FX-1's own delivery file and plan", out)
        self.assertEqual(json.loads(path.read_text())["approvals"][0]["state"], "waiting")
        # Every commit ahead is read, not only the last or the first, and another delivery's own file is not this
        # one's: another lead's commit to FX-9's delivery file, above this lead's own approval, then below one.
        sh("git", "reset", "-q", "--hard", "origin/feature/FX-1", cwd=lead)
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", "--who", "the operator", cwd=lead)
        self.assertEqual(code, 0, out)
        other_file = lead / ".agents/deliveries/FX-9.json"
        other_file.write_text(json.dumps({"delivery": "FX-9"}, indent=2) + "\n")
        commit(lead, "FX-9: its delivery file")
        above = sh("git", "rev-parse", "--short=12", "HEAD", cwd=lead).stdout.strip()
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "waiting", cwd=lead)
        self.assertEqual(code, 2, out)
        self.assertIn(f"with {above} (FX-9: its delivery file), which changes .agents/deliveries/FX-9.json:", out)
        sh("git", "reset", "-q", "--hard", "origin/feature/FX-1", cwd=lead)
        other_file.write_text(json.dumps({"delivery": "FX-9"}, indent=2) + "\n")
        commit(lead, "FX-9: its delivery file")
        below = sh("git", "rev-parse", "--short=12", "HEAD", cwd=lead).stdout.strip()
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "waiting", cwd=lead)
        self.assertEqual(code, 2, out)  # refused already, before any own commit is on top
        self.assertIn(f"with {below} (FX-9: its delivery file)", out)
        d = json.loads(path.read_text())
        d["approvals"][0]["state"] = "done"
        path.write_text(json.dumps(d, indent=2))
        commit(lead, "FX-1: approval approval-1 done")  # an own commit on top of the foreign one
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "waiting", cwd=lead)
        self.assertEqual(code, 2, out)
        self.assertIn(f"with {below} (FX-9: its delivery file), which changes .agents/deliveries/FX-9.json:", out)
        # A worktree that has diverged from the base is refused as today.
        sh("git", "reset", "-q", "--hard", "origin/feature/FX-1", cwd=lead)
        (lead / "src/app.txt").write_text("the lead's\n")
        commit(lead, "a lead commit")
        (fx.main / "src/app.txt").write_text("the base's\n")
        commit(fx.main, "the base moves on")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", cwd=lead)
        self.assertEqual(code, 2, out)
        self.assertIn(f"{lead} has diverged from origin/feature/FX-1", out)

    def test_a_clean_merge_of_the_base_passes_and_a_merge_adding_its_own_file_is_refused(self) -> None:
        fx, lead, path = self.fixture()
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", "--who", "the operator", cwd=lead)
        self.assertEqual(code, 0, out)
        # The base moves on before that commit is pushed; the lead merges it in, cleanly.
        (fx.main / "src/app.txt").write_text("the base's\n")
        commit(fx.main, "the base moves on")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        sh("git", "fetch", "-q", "origin", cwd=lead)
        sh("git", "merge", "-q", "--no-edit", "origin/feature/FX-1", cwd=lead)
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--text", "Thing one says so when it fails", "--why", "w", cwd=lead)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.pushed(fx, out, lead)["approvals"][0]["state"], "done")
        # A merge that adds a file neither parent has is refused, naming it, though each parent alone is the lead's.
        sh("git", "checkout", "-q", "-b", "side", cwd=lead)
        sh("git", "commit", "-q", "--allow-empty", "-m", "side", cwd=lead)
        sh("git", "checkout", "-q", "lead/FX-1", cwd=lead)
        sh("git", "commit", "-q", "--allow-empty", "-m", "main side", cwd=lead)
        sh("git", "merge", "-q", "--no-ff", "--no-commit", "side", cwd=lead)
        (lead / "docs/evil.md").write_text("added by the merge\n")
        sh("git", "add", "docs/evil.md", cwd=lead)
        sh("git", "commit", "-q", "-m", "merge side", cwd=lead)
        merge = sh("git", "rev-parse", "--short=12", "HEAD", cwd=lead).stdout.strip()
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "waiting", cwd=lead)
        self.assertEqual(code, 2, out)
        self.assertIn(f"with {merge} (merge side), which changes docs/evil.md:", out)

    def test_a_dirty_lead_worktree_is_refused(self) -> None:
        fx, lead, path = self.fixture()
        (lead / "notes.txt").write_text("untracked\n")
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--text", "Anything", "--why", "w", cwd=lead)
        self.assertEqual(code, 2, out)
        self.assertIn(f"{lead} has uncommitted changes; commit or remove them first", out)
        self.assertEqual(json.loads(path.read_text())["approvals"][0]["state"], "waiting")

    def test_a_checkout_on_the_base_branch_behaves_as_it_does_today(self) -> None:
        fx, lead, _ = self.fixture()
        m = fx.main
        # Ahead of the base with a commit of any kind is allowed there, as before.
        (m / "src/app.txt").write_text("the lead's own\n")
        commit(m, "a commit on the base branch")
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn("push it so lanes see it: git push origin HEAD:feature/FX-1", out)
        self.assertEqual(sh("git", "log", "-1", "--format=%s", cwd=m).stdout.strip(), "FX-1: approval approval-1 done")
        # And a story branch is no lead checkout: a lane can't change the plan from its own.
        lane = fx.tmp / "lane-FX-2"
        sh("git", "worktree", "add", "-q", "--no-track", "-b", "feature/FX-2", lane, "origin/feature/FX-1", cwd=m)
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "waiting", cwd=lane)
        self.assertEqual(code, 2, out)
        self.assertIn("run lead-plan in the lead's checkout", out)
        self.assertIn("feature/FX-2 is a story branch", out)

    def test_doctor_reads_a_lead_worktree_on_its_own_branch_as_it_reads_the_base_checkout(self) -> None:
        fx, lead, _ = self.fixture()
        failing = lambda out: sorted(line for line in out.splitlines() if "FAIL" in line or "fix:" in line)
        base_code, base_out = fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=fx.main)
        code, out = fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=lead)
        self.assertEqual((code, failing(out)), (base_code, failing(base_out)), out)
        self.assertNotIn("lead/FX-1", out)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliverySetupTests(unittest.TestCase):
    """What lead-init, lead-assign and lead-setup refuse before a lane starts, and how each refusal says to recover."""

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

    def started(self, argv: list[str]) -> subprocess.Popen:
        """A process this test starts in its own session, and stops (by its pid) and reaps at cleanup."""
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, start_new_session=True)

        def stop():
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            proc.stdout.close()
        self.addCleanup(stop)
        return proc

    def supervisor(self, fx: Fixture, mode: str = "default", pid: object = None) -> subprocess.Popen:
        """A stand-in for the delivery's supervisor: it holds the flow log's supervisor lock, recording its pid (or
        the one given), and in mode ignore-term ignores SIGTERM."""
        lock = fx.tmp / "flow-log/FX-1.supervisor.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        proc = self.started([sys.executable, "-B", "-c", SUPERVISOR_STAND_IN, str(lock), mode,
                             *([json.dumps(pid)] if pid is not None else [])])
        self.assertEqual(proc.stdout.readline().strip(), "holding")
        # Reaped as soon as it exits, as the supervisor's own parent would, so a stopped one leaves no zombie.
        threading.Thread(target=proc.wait, daemon=True).start()
        return proc

    def test_lead_init_over_a_record_refuses_and_names_it(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        before = (fx.tmp / "record/state.json").read_bytes()
        started = self.record(fx)["journal"][0]["at"]
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("already holds a record: delivery FX-1 on origin/feature/FX-1", out)
        self.assertIn(f"started {started}", out)
        self.assertIn("fix: run lead-init --replace", out)
        self.assertEqual((fx.tmp / "record/state.json").read_bytes(), before, "the refusal changed the record")
        # The base named is the one the record started on, from its own journal.
        shutil.rmtree(fx.tmp / "record")
        fx.call("state_tool", "init", "--headline", "earlier", cwd=fx.main)
        fx.call("state_tool", "journal", "--kind", "decision.recorded", "--summary",
                "Record starts at base 0123456 on origin/release-1. Lanes merge through the runner.", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("already holds a record: delivery earlier on origin/release-1", out)
        # A record the state tool can't read is still named, and --replace archives it without reading it.
        (fx.tmp / "record/state.json").write_text("{truncated")
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("already holds a record the state tool can't read", out)
        code, out = fx.flow("lead-init", "--replace", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record(fx)["journal"][0]["id"], 1)

    def test_a_snapshot_or_a_supervisor_lock_in_the_flow_log_counts_and_a_pre_init_change_does_not(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        m = fx.main
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--text", "Thing one says something when it fails", "--why", "the copy is not settled", cwd=m)
        self.assertEqual(code, 0, out)
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        self.assertTrue((fx.tmp / "flow-log/plan.lock").exists())
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, f"a pre-init lead-plan change was read as a record: {out}")
        # A snapshot left without its record is still the earlier delivery's.
        shutil.rmtree(fx.tmp / "record")
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("already holds the plan snapshot of delivery FX-1 on origin/feature/FX-1", out)
        self.assertIn("lead-init --replace", out)
        # A supervisor's lock alone counts too, and names who holds it.
        shutil.rmtree(fx.tmp / "flow-log")
        proc = self.supervisor(fx)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn(f"holds the supervisor lock of delivery FX-1 on origin/feature/FX-1, held by pid {proc.pid}, "
                      "started 2026-01-02T03:04:05Z", out)
        self.assertFalse((fx.tmp / "record").exists())

    def test_replace_stops_the_supervisor_archives_both_and_takes_the_snapshot_afresh(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        m = fx.main
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane one", cwd=m)
        self.assertEqual(code, 0, out)
        old_journal = (fx.tmp / "record/journal.jsonl").read_text()
        # A supervisor that ignores SIGTERM is still stopped, and confirmed gone, before anything is archived.
        proc = self.supervisor(fx, "ignore-term")
        # Edited between the two inits: not a change of the approved plan, which the new init approves as it stands.
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"][0]["text"] = "Thing one loads within a second"
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: the plan as approved again")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", "--replace", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn(f"stopped the supervisor (pid {proc.pid})", out)
        self.assertLess(out.index("stopped the supervisor"), out.index("archived the record"))
        self.assertEqual(proc.wait(timeout=5), -signal.SIGKILL, "it ignored SIGTERM, so only SIGKILL could stop it")
        archives = sorted((fx.tmp / "archive/FX-1").iterdir())
        self.assertEqual(len(archives), 1, archives)
        self.assertIn(f"archived the record and the flow log in {archives[0]}", out)
        self.assertEqual((archives[0] / "record/journal.jsonl").read_text(), old_journal)
        self.assertTrue((archives[0] / "flow-log/plan-changes.jsonl").exists())
        self.assertTrue((archives[0] / "flow-log/FX-1.supervisor.lock").exists())
        # Nothing carried over: a fresh journal from id 1, a fresh plan log, and no drift.
        journal = self.record(fx)["journal"]
        self.assertEqual(journal[0]["id"], 1)
        self.assertFalse(any(e.get("kind") == "lane.assigned" for e in journal), journal)
        self.assertFalse((fx.tmp / "flow-log/FX-1.supervisor.lock").exists())
        log = [json.loads(line) for line in (fx.tmp / "flow-log/plan-changes.jsonl").read_text().splitlines()]
        self.assertEqual([entry["type"] for entry in log], ["snapshot"])
        plan = json.loads((fx.tmp / "record/plan.json").read_text())
        self.assertEqual(plan["changes"], [])
        self.assertEqual(plan["units"][0]["criteria"][0]["text"], "Thing one loads within a second")
        # With nothing to replace, --replace is a plain init.
        shutil.rmtree(fx.tmp / "record")
        shutil.rmtree(fx.tmp / "flow-log")
        code, out = fx.flow("lead-init", "--replace", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertNotIn("archived", out)

    def test_replace_signals_only_a_held_locks_pid_and_never_a_group_it_cant_name(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        m = fx.main
        # A lock nobody holds, naming a live process this test started: a stale pid, maybe reused. Left alone.
        bystander = self.started(["sleep", "60"])
        lock = fx.tmp / "flow-log/FX-1.supervisor.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps({"pid": bystander.pid, "startedAt": "2026-01-02T03:04:05Z", "children": []}))
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("holds the supervisor lock of delivery FX-1 on origin/feature/FX-1, which no process holds", out)
        code, out = fx.flow("lead-init", "--replace", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertNotIn("stopped the supervisor", out)
        self.assertIsNone(bystander.poll(), "--replace signalled a pid no lock holder vouched for")
        # A held lock that records pid 0 would signal lead-init's own group: refused, and nothing is archived.
        shutil.rmtree(fx.tmp / "record")
        holder = self.supervisor(fx, pid=0)
        # In a session of its own, so a regression that signals group 0 stops only lead-init, never this test run.
        done = subprocess.run([sys.executable, "-B", str(fx.tool), "lead-init", "--replace"], cwd=m, env=fx.env,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300,
                              start_new_session=True)
        code, out = done.returncode, done.stdout
        self.assertEqual(code, 2, out)
        self.assertIn("is held, but records no pid to stop", out)
        self.assertTrue(lock.exists())
        self.assertIsNone(holder.poll())

    def test_lead_assign_refuses_a_story_with_no_plan_section(self) -> None:
        fx = self.fixture()
        path = setup_delivery(fx)
        m = fx.main
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        d = json.loads(path.read_text())
        d["stories"]["FX-9"] = {"title": "Nine", "lane": "lane-1"}
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a story the plan doesn't have")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-assign", "FX-2", "FX-9", "--lane", "lane-1", "--title", "Lane one", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("stories with no plan section: FX-9", out)
        self.assertIn("fix: add a heading naming each to docs/plan.md", out)
        self.assertFalse(any(e.get("kind") == "lane.assigned" for e in self.record(fx)["journal"]))
        code, out = fx.flow("lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane one", cwd=m)
        self.assertEqual(code, 0, out)

    def test_lead_setup_asks_for_the_lanes_authority_grant(self) -> None:
        fx = self.fixture()
        m = fx.main
        path = m / ".agents/deliveries/FX-1.json"
        setup = ["lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN]
        code, out = fx.flow(*setup, cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("lead-setup needs the lanes' authority grant", out)
        self.assertIn("--authority push,draftPullRequests --granted-by", out)
        self.assertIn("--no-authority", out)
        self.assertFalse(path.exists(), "a delivery file was written without the grant")
        for refused, words in ((["--authority", "draftPullRequests", "--granted-by", "the operator"],
                                "draftPullRequests needs push"),
                               (["--authority", "push"], "--authority needs --granted-by"),
                               (["--authority", "bogus", "--granted-by", "x"],
                                "--authority takes push, draftPullRequests (the lanes') and readyPullRequests (the lead's"),
                               (["--authority", "push", "--granted-by", "x", "--no-authority"], "not both")):
            with self.subTest(refused=refused):
                code, out = fx.flow(*setup, *refused, cwd=m)
                self.assertEqual(code, 2, out)
                self.assertIn(words, out)
                self.assertFalse(path.exists())
        code, out = fx.flow(*setup, "--authority", "push,draftPullRequests", "--granted-by", "the operator",
                            "--granted-on", "2026-11-03", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(path.read_text())["authority"], {
            "push": True, "draftPullRequests": True, "grantedBy": "the operator", "grantedOn": "2026-11-03"})
        # The lead's own grant, readyPullRequests, is written beside the lanes'.
        code, out = fx.flow(*setup, "--force", "--authority", "push,draftPullRequests,readyPullRequests", "--granted-by",
                            "the operator", "--granted-on", "2026-11-04", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(path.read_text())["authority"], {
            "push": True, "draftPullRequests": True, "readyPullRequests": True, "grantedBy": "the operator",
            "grantedOn": "2026-11-04"})
        # Drafting again keeps the grant the file holds.
        code, out = fx.flow(*setup, "--force", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(path.read_text())["authority"]["grantedBy"], "the operator")
        self.assertTrue(json.loads(path.read_text())["authority"]["readyPullRequests"])
        code, out = fx.flow(*setup, "--force", "--no-authority", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertNotIn("authority", json.loads(path.read_text()))

    def test_an_adapter_that_prints_nothing_fails_and_names_its_path(self) -> None:
        fx = self.fixture()
        setup_delivery(fx)
        m = fx.main
        (m / "tools/silent_forge.py").write_text("")
        conf_path = m / ".agents/run-delivery.json"
        conf = json.loads(conf_path.read_text())
        conf["forge"] = {"argv": [sys.executable, "-B", "tools/silent_forge.py"]}
        conf_path.write_text(json.dumps(conf, indent=2))
        with self.assertRaises(Stopped) as refused:
            fx.call("forge", "find", "--source", "feature/FX-2", cwd=m, delivery="FX-1")
        self.assertIn("forge find printed nothing", str(refused.exception))
        self.assertIn("tools/silent_forge.py", str(refused.exception))
        (m / "tools/silent_forge.py").write_text("raise SystemExit(1)\n")
        with self.assertRaises(Stopped) as refused:
            fx.call("forge", "find", "--source", "feature/FX-2", cwd=m, delivery="FX-1")
        self.assertIn("forge find failed: it printed nothing; the adapter it ran:", str(refused.exception))
        self.assertIn("tools/silent_forge.py", str(refused.exception))
        (m / "tools/silent_forge.py").write_text("print('{}')\n")
        self.assertEqual(fx.call("forge", "find", "--source", "feature/FX-2", cwd=m, delivery="FX-1"), {})


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

    def test_doctor_names_a_reasonless_added_change_until_lead_plan_why_records_one(self) -> None:
        fx = self.fixture()
        m = fx.main
        path = plan_delivery(fx, m / ".agents/deliveries/FX-1.json")
        code, out = fx.flow("lead-init", "--no-supervisor", cwd=m)
        self.assertEqual(code, 0, out)
        row = lambda: doctor_lines(self.doctor(fx, "--offline"))["added stories' reasons"]
        self.assertEqual(row(), ("ok", "every story added to the plan has its reason"))
        # A story waiting to be registered with no reason is named too: registration will refuse it.
        add_story(fx, path, "FX-4")
        status, detail = row()
        self.assertEqual(status, "wrong", detail)
        self.assertIn("FX-4 has no `why`, so registration refuses it; fix: give it a `why` in", detail)
        d = json.loads(path.read_text())
        d["stories"]["FX-4"]["why"] = "Split from FX-2."
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: FX-4's reason")
        self.assertEqual(fx.flow("lead-brief", cwd=m)[0], 0)
        self.assertEqual(row()[0], "ok")
        # A record an earlier release wrote holds the change without it.
        log = fx.tmp / "flow-log/plan-changes.jsonl"
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        change = next(e for e in entries if e["type"] == "change")
        del change["why"]
        log.write_text("".join(json.dumps(e) + "\n" for e in entries))
        status, detail = row()
        self.assertEqual(status, "wrong", detail)
        self.assertIn(f"the record holds an `added` change with no reason: {change['id']} adds FX-4; fix: ", detail)
        self.assertIn(f"lead-plan why {change['id']} --why TEXT", detail)
        self.assertEqual(fx.flow("lead-plan", "why", change["id"], "--why", "Split from FX-2.", cwd=m)[0], 0)
        self.assertEqual(row(), ("ok", "every story added to the plan has its reason"))
        # A removed story's addition is the plan's history, not a story in the delivery file.
        self.assertNotIn("why", change)
        log.write_text("".join(json.dumps(e) + "\n" for e in entries))
        d = json.loads(path.read_text())
        del d["stories"]["FX-4"]
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: FX-4 dropped")
        self.assertEqual(row()[0], "ok")
        # A criterion added by hand is recorded as an unrecorded `added` change with no reason: the plan chapter's flag,
        # not a story added without one.
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"].append({"id": "one-scrolls", "text": "Thing one scrolls"})
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a criterion added by hand")
        self.assertEqual(fx.flow("lead-brief", cwd=m)[0], 0)
        logged = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue([e for e in logged if e["type"] == "change" and e["kind"] == "added"
                         and e.get("criteria") == ["one-scrolls"] and "why" not in e], logged)
        self.assertEqual(row(), ("ok", "every story added to the plan has its reason"))

    def test_the_candidate_row_proves_the_merge_queues_candidate_step_can_run(self) -> None:
        fx = self.fixture()
        path = fx.main / ".agents/orchestration.json"
        self.assertEqual(doctor_lines(self.doctor(fx, "--offline"))["merge queue candidate step"],
                         ("ok", "true {candidate} resolves"))
        binding = json.loads(path.read_text())
        for argv, problem in (
                ([sys.executable, "-B", "tools/missing.py", "{candidate}"], "its script tools/missing.py doesn't exist in the repository"),
                (["no-such-candidate-program", "{candidate}"], "its program no-such-candidate-program isn't on PATH"),
                ([sys.executable, "-B", "./tools", "{candidate}"], "its script ./tools doesn't exist in the repository")):
            with self.subTest(argv=argv):
                path.write_text(json.dumps({**binding, "candidate": argv}, indent=2))
                out = self.doctor(fx, "--offline")
                self.assertEqual(failing(out), {"merge queue candidate step": (
                    f"the binding's candidate step can't run: {problem}; fix: correct its argv in .agents/orchestration.json, "
                    "then run it once by hand with a branch that exists")}, out)
        # A script the repository holds resolves.
        path.write_text(json.dumps({**binding, "candidate": [sys.executable, "-B", "tools/fake_forge.py", "{candidate}"]}, indent=2))
        self.assertEqual(doctor_lines(self.doctor(fx, "--offline"))["merge queue candidate step"][0], "ok")
        # An argv without {candidate} fails the binding itself, and the candidate row waits on it.
        path.write_text(json.dumps({**binding, "candidate": ["true", "feature"]}, indent=2))
        lines = doctor_lines(self.doctor(fx, "--offline"))
        self.assertEqual(lines["orchestration binding"][0], "wrong")
        self.assertIn("/candidate: the candidate argv must use {candidate} as a whole element", lines["orchestration binding"][1])
        self.assertEqual(lines["merge queue candidate step"], ("skipped", "(needs binding)"))

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

    def test_the_story_files_row_warns_of_a_named_file_outside_its_lane_without_failing(self) -> None:
        fx = self.fixture()
        m = fx.main
        out = self.doctor(fx, "--offline")
        self.assertEqual(doctor_lines(out)["story files"], ("ok", "every file a story's plan section names is its lane's"), out)
        plan = m / "docs/plan.md"
        plan.write_text(plan.read_text()
                        .replace("- Scenarios: it loads; it fails.\n",
                                 "- Scenarios: it loads; it fails.\n- Files: `src/one-new.txt`, `src/two.txt`, `tools/fake_check.py`, "
                                 "`app.txt`, `refs/notes.md`, `run_delivery.py`, `run_delivery.py gate`, `/api/two$`.\n")
                        .replace("| Copy | \"Couldn't be loaded.\" |\n",
                                 "| Copy | \"Couldn't be loaded.\" |\n| S1's check | `tools/fake_forge.py` |\n"
                                 "| Lane 2's files | `tools/lane-two.py` | `src/two-only.txt`, after FX-2 |\n"))
        (m / "docs/refs").mkdir()
        (m / "docs/refs/notes.md").write_text("notes\n")
        commit(m, "FX-1: the plan names files")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        # The installed release is tracked too, but no lane edits it: a bare name only it holds names nothing.
        self.assertTrue(sh("git", "ls-files", "*/run_delivery.py", cwd=m).stdout.strip())
        out = self.doctor(fx, "--offline")
        self.assertEqual(doctor_lines(out)["story files"],
                         ("warn", "FX-2 (lane-1) names docs/refs/notes.md (no lane's), src/app.txt (no lane's), "
                                  "src/two.txt (lane-2's) and 2 more; fix: move each to the story's lane's owns before it "
                                  "starts, or plan the story on the lane that owns it"), out)
        self.assertEqual(failing(out), {}, out)
        self.assertEqual(fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=m)[0], 0, "a warning failed doctor")
        # Past five stories, doctor's one line counts the rest.
        path = m / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        heading = "### S2 (FX-3). Thing two (AC2)\n"
        extra = "".join(f"### S{k} (FX-{k}). Thing {k}\n\n- Files: `src/two.txt`.\n\n" for k in range(4, 10))
        plan.write_text(plan.read_text().replace(heading, extra + heading))
        for k in range(4, 10):
            d["stories"][f"FX-{k}"] = {"title": f"Thing {k}", "lane": "lane-1", "label": f"S{k}"}
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: more stories")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        status, detail = doctor_lines(self.doctor(fx, "--offline"))["story files"]
        self.assertEqual(status, "warn")
        self.assertEqual(detail.count(" names "), 5, detail)
        self.assertIn("; and 2 more stories (lead-assign lists each one's); fix:", detail)

    def test_a_binding_serves_a_delivery_to_any_of_its_bases(self) -> None:
        """Bases: a delivery committed only on integration-2 runs from the lead's setup to a lane's draft PR."""
        fx = self.fixture()
        m = fx.main
        harness = lambda ref: (m / "harness.config.ts").write_text(
            f"export default {{ baseRef: '{ref}', preparationCommands: ["
            "{ id: 'lint', command: ['sh', '-c', 'test -f src/app.txt'] }, { id: 'test', command: ['true'] }] };\n")
        before_bases = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        binding = {**doctor_binding(), "bases": ["feature/FX-1", "integration-*"]}
        (m / ".agents/orchestration.json").write_text(json.dumps(binding, indent=2))
        commit(m, "FX-1: the binding serves the integration bases")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        # The delivery is cut onto integration-2 and committed only there.
        sh("git", "checkout", "-q", "-b", "integration-2", cwd=m)
        path = m / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        path.write_text(json.dumps({**d, "baseBranch": "integration-2"}, indent=2))
        if self.node22:
            harness("origin/integration-2")
        commit(m, "FX-1: deliver to integration-2")
        sh("git", "push", "-q", "-u", "origin", "integration-2", cwd=m)
        sh("git", "checkout", "-q", "feature/FX-1", cwd=m)
        sh("git", "rm", "-q", ".agents/deliveries/FX-1.json", cwd=m)
        commit(m, "FX-1: the delivery lives on integration-2")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        sh("git", "checkout", "-q", "integration-2", cwd=m)

        # The lead, on integration-2, reads its working tree.
        out = self.doctor(fx, "--offline", expect_code=0)
        self.assertEqual(failing(out), {}, out)
        self.assertIn("origin/integration-2 carries the installed generation",
                      doctor_lines(out)["release on the delivery base"][1], out)
        code, out = fx.flow("lead-init", cwd=m); expect(code == 0, out)
        code, out = fx.flow("lead-brief", cwd=m); expect(code == 0, out)
        self.assertIn("origin/integration-2", (m / "docs/lane-brief.md").read_text())

        # lane-start serves a base the binding's bases match by pattern, and only by the pattern's own segment.
        code, out = fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lane-start", "FX-3", "--base", "integration-9/sub", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("doesn't serve the base integration-9/sub", out)
        code, out = fx.flow("lane-start", "FX-3", "--base", "integration-2", cwd=m)
        self.assertEqual(code, 0, out)
        started = m / "lanes/FX-3"
        self.assertEqual(sh("git", "rev-parse", "HEAD", cwd=started).stdout.strip(),
                         sh("git", "rev-parse", "origin/integration-2", cwd=m).stdout.strip())
        # A lane command there reads the base it was started on, served by the pattern, with no variable to name it.
        c = fx.call("Config", started, "FX-1", cwd=started, attrs=["base_branch", "delivery_where"])
        self.assertEqual(c["base_branch"], "integration-2")
        self.assertTrue(c["delivery_where"].startswith("origin/integration-2:"), c)
        code, out = fx.flow("gate", "FX-3", cwd=started)
        self.assertEqual(code, 0, out)

        # A lane reads the delivery's settings from origin/integration-2, with the base it was given.
        wt = m / "lanes/FX-2"
        sh("git", "worktree", "add", "-q", "--no-track", "-b", "feature/FX-2", wt, "origin/integration-2", cwd=m)
        code, out = fx.flow("gate", "FX-2", cwd=wt)
        self.assertEqual(code, 2, out)
        self.assertIn("this checkout read origin/feature/FX-1, the binding's base.ref", out)
        self.assertIn("RUN_DELIVERY_BASE", out)
        lane_env = {**fx.env, "RUN_DELIVERY_BASE": "integration-2"}
        code, out = fx.flow("gate", "FX-2", cwd=wt, env=lane_env); expect(code == 0, out)
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        sh("git", "push", "-q", "origin", "feature/FX-2", cwd=wt)
        code, out = fx.flow("open-pr", "FX-2", "--title", "FX-2: one", "--body-file", "/dev/null", cwd=wt, env=lane_env)
        expect(code == 0, out)
        prs = json.loads((fx.tmp / "forge.json").read_text())
        self.assertEqual([pr["destination"] for pr in prs.values()], ["integration-2"], prs)
        # The base a lane was started on, recorded in its checkout, serves without the variable.
        fx.call("record_lane_base", wt, "integration-2", cwd=wt)
        c = fx.call("Config", wt, "FX-1", cwd=wt, attrs=["base_branch", "delivery_where"])
        self.assertEqual(c["base_branch"], "integration-2")
        self.assertTrue(c["delivery_where"].startswith("origin/integration-2:"), c)

        # A base outside the bases is refused, naming the guide's anchor.
        sh("git", "push", "-q", "origin", "origin/integration-2:refs/heads/release/1", cwd=m)
        code, out = fx.flow("gate", "FX-2", cwd=wt, env={**fx.env, "RUN_DELIVERY_BASE": "integration-9/sub"})
        self.assertEqual(code, 2, out)
        self.assertIn("doesn't serve the base integration-9/sub", out)
        sh("git", "push", "-q", "origin", "origin/integration-2:refs/heads/integration-3", cwd=m)
        code, out = fx.flow("gate", "FX-2", cwd=wt, env={**fx.env, "RUN_DELIVERY_BASE": "integration-3"})
        self.assertEqual(code, 2, out)
        self.assertIn("names base integration-2, but this checkout read it from origin/integration-3", out)
        served = {name: fx.call("serves", SERVING_BINDING, name, cwd=wt) for name in SERVING_RULE}
        self.assertEqual(served, SERVING_RULE, "run-delivery and the merge queue disagree on which bases are served")
        for code, out in (fx.flow("config", cwd=wt, env={**fx.env, "RUN_DELIVERY_BASE": "release/1"}),
                          fx.flow("lead-setup", "FX-9", "--plan", "docs/plan.md", "--base", "release/1",
                                  "--pattern", PATTERN, "--no-authority", cwd=m)):
            self.assertEqual(code, 2, out)
            self.assertIn("doesn't serve the base release/1", out)
            self.assertIn("adopting.md#base-mismatch", out)
        code, out = fx.flow("lead-setup", "FX-9", "--plan", "docs/plan.md", "--pattern", PATTERN, "--no-authority", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("pass --base", out)
        self.assertFalse((m / ".agents/deliveries/FX-9.json").exists())
        code, out = fx.flow("lead-setup", "FX-9", "--plan", "docs/plan.md", "--base", "integration-2",
                            "--pattern", PATTERN, "--no-authority", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads((m / ".agents/deliveries/FX-9.json").read_text())["baseBranch"], "integration-2")
        (m / ".agents/deliveries/FX-9.json").unlink()
        if self.node22:
            harness("origin/feature/FX-1")
            code, out = fx.flow("config", cwd=m)
            self.assertEqual(code, 2, out)
            self.assertIn("isn't the delivery's base origin/integration-2", out)
            harness("origin/integration-2")

        # A base cut before its binding gained bases still needs that setting on it, and doctor says so.
        sh("git", "push", "-q", "origin", f"{before_bases}:refs/heads/integration-old", cwd=m)
        sh("git", "update-ref", "-d", "refs/remotes/origin/integration-old", cwd=m)
        (m / ".agents/deliveries/FX-8.json").write_text(json.dumps({**d, "baseBranch": "integration-old"}, indent=2))
        if self.node22:
            harness("origin/integration-old")
        _, out = fx.flow("doctor", cwd=m, env={**fx.env, "RUN_DELIVERY_DELIVERY": "FX-8"})  # the variable names it
        status, detail = doctor_lines(out)["release on the delivery base"]
        self.assertEqual(status, "wrong", out)
        self.assertIn("origin/integration-old's binding doesn't serve the base integration-old", detail)
        self.assertIn("as a base cut before its binding gained bases doesn't; fix: commit the binding", detail)
        (m / ".agents/deliveries/FX-8.json").unlink()
        if self.node22:
            harness("origin/feature/FX-1")

        # A binding without bases refuses integration-2 exactly as today.
        (m / ".agents/orchestration.json").write_text(json.dumps(doctor_binding(), indent=2))
        code, out = fx.flow("config", cwd=m, env={**fx.env, "RUN_DELIVERY_LOCAL": "1", "RUN_DELIVERY_DELIVERY": "FX-1"})
        self.assertEqual(code, 2, out)
        self.assertIn("names base integration-2, but the binding here serves feature/FX-1;", out)
        (wt / ".agents/orchestration.json").write_text(json.dumps(doctor_binding(), indent=2))
        code, out = fx.flow("config", cwd=wt, env=lane_env)
        self.assertEqual(code, 2, out)
        self.assertIn("the binding doesn't serve the base integration-2: it serves feature/FX-1;", out)

    def test_a_clean_repository_passes_every_check(self) -> None:
        fx = self.fixture()
        out = self.doctor(fx, "--run-adapters", "--run-gates", expect_code=0)
        lines = doctor_lines(out)
        self.assertEqual(list(lines), DOCTOR_LABELS, out)
        expected_skips = {"pipeline PR review", "gh", "forge write access", "plan chapter", "added stories' reasons",
                          "pull requests marked ready", "delivery supervisor"}
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
            # JSON of the wrong shape is the schema's to refuse, never a traceback.
            ("orchestration binding", "wrong", "correct it against orchestration-binding/1",
             lambda: (rewrite(binding_path, "[]"), None)),
            ("orchestration binding", "wrong", "correct it against orchestration-binding/1",
             lambda: (edit_json(binding_path, lambda b: b["state"].update(publishers=5)), None)),
            ("orchestration binding", "wrong", "correct it against orchestration-binding/1",
             lambda: (edit_json(binding_path, lambda b: b.update(state=["x"])), None)),
            ("orchestration binding", "wrong", "correct it against orchestration-binding/1",
             lambda: (edit_json(binding_path, lambda b: b["state"].update(trackerSnapshot="x")), None)),
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
            ("gh", "missing", "install gh", lambda: (edit_json(conf_path, github), {**fx.env, "PATH": self.without("gh")})),
            ("forge find", "wrong", "", lambda: (edit_json(conf_path, lambda c: c.update(forge={"argv": [
                sys.executable, "-c", "import sys; sys.exit('the forge is unreachable')"]})), None)),
            ("forge find", "missing", "export FX_FORGE_TOKEN", lambda: (edit_json(conf_path, lambda c: c.update(
                forge={"adapter": "bitbucket", "repository": "ws/repo", "tokenEnv": "FX_FORGE_TOKEN"})), None)),
            ("tracker snapshot token", "missing", "export FX_TRACKER_TOKEN", lambda: env(FX_TRACKER_TOKEN=None)),
            # The tracker adapter's own variable, which no snapshot names.
            ("tracker snapshot token", "missing", "export FX_LINEAR_KEY", lambda: (edit_json(conf_path, lambda c: c[
                "tracker"].update(adapter="linear", tokenEnv="FX_LINEAR_KEY")), None)),
            # And with no tracker snapshot at all, the adapter's variable is still checked.
            ("tracker snapshot token", "missing", "export FX_LINEAR_KEY", lambda: (both(
                edit_json(conf_path, lambda c: c["tracker"].update(adapter="linear", tokenEnv="FX_LINEAR_KEY")),
                edit_json(binding_path, lambda b: b["state"].pop("trackerSnapshot"))), None)),
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
            cases.append(("Node", "missing", "install Node 22.6",
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

    def test_each_setup_gap_is_reported_by_doctor_and_refused_by_lead_init(self) -> None:
        fx = self.fixture()
        m = fx.main
        binding_path, delivery_path = m / ".agents/orchestration.json", m / ".agents/deliveries/FX-1.json"
        conf_rel = ".agents/run-delivery.json"

        def edit_json(path: Path, change):
            original = path.read_text()
            document = json.loads(original)
            change(document)
            path.write_text(json.dumps(document))
            return lambda: path.write_text(original)

        def both(*undos):
            return lambda: [undo() for undo in reversed(undos)]

        def without_harness():
            # The harness configuration needs a Node too, and the configuration refuses one it can't read.
            path = m / "harness.config.ts"
            if not path.exists():
                return lambda: None
            original = path.read_text()
            path.unlink()
            return lambda: path.write_text(original)

        def old_node():
            folder = fx.tmp / "old-node-bin"
            folder.mkdir(exist_ok=True)
            (folder / "node").write_text("#!/bin/sh\necho v20.0.0\n")
            (folder / "node").chmod(0o755)
            return f"{folder}{os.pathsep}{self.without('node')}"

        def remote_base_without_configuration():
            # Lanes read the configuration from the remote base; the lead's own working tree still has it.
            original = (m / conf_rel).read_text()
            sh("git", "rm", "-q", conf_rel, cwd=m)
            commit(m, "FX-1: the configuration leaves the base")
            sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
            (m / conf_rel).write_text(original)

            def undo():
                commit(m, "FX-1: the configuration returns")
                sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
            return undo

        record = fx.tmp / "record"
        cases = [
            # (the failing row, the words its detail carries, a phrase of its fix, how to open the gap, environment)
            ("plan sections", "stories with no plan section: FX-9", "add a heading naming each",
             lambda: edit_json(delivery_path, lambda d: d["stories"].update({"FX-9": {"title": "Nine", "lane": "lane-1"}})),
             None),
            ("context sections", "context.sections the plan lacks: 'Constraints'", "add the heading to docs/plan.md",
             lambda: edit_json(delivery_path, lambda d: d["context"]["sections"].append("Constraints")), None),
            ("snapshots and publishers", "the binding has no tracker publisher", "add one to state.publishers",
             lambda: edit_json(binding_path, lambda b: b["state"].update(publishers=[])), None),
            ("snapshots and publishers", "the binding has no state.forgeSnapshot", "add state.forgeSnapshot",
             lambda: edit_json(binding_path, lambda b: b["state"].pop("forgeSnapshot")), None),
            ("snapshots and publishers", "reviewWhen is pipeline-green, which waits for the pipeline's review",
             "correct `tracker`", lambda: edit_json(m / conf_rel, lambda c: c["tracker"].update(
                 transitions={"review": ["Submit for review"], "reviewWhen": "pipeline-green"})), None),
            ("snapshots and publishers", "the binding has no state.forgeSnapshot", "add state.forgeSnapshot",
             lambda: both(edit_json(binding_path, lambda b: b["state"].pop("forgeSnapshot")),
                          edit_json(m / conf_rel, lambda c: c.update(forge={"adapter": "bitbucket", "repository": "ws/r",
                                                                             "tokenEnv": "FX_FORGE_TOKEN"}))), None),
            ("Node", "Node 20.0.0 is below the runtime's nodeMinimum 22.6.0", "install Node 22.6.0 or newer",
             without_harness, {"PATH": old_node(), "HOME": str(fx.tmp)}),
            ("configuration on the remote base", "origin/feature/FX-1 has no configuration for lanes",
             "set RUN_DELIVERY_LOCAL=1", remote_base_without_configuration, None),
        ]
        for label, detail, fix, open_gap, environment in cases:
            with self.subTest(failing=label, detail=detail):
                undo = open_gap()
                env = {**fx.env, **(environment or {})}
                try:
                    out = self.doctor(fx, "--offline", env=env, expect_code=1)
                    code, refused = fx.flow("lead-init", cwd=m, env=env)
                finally:
                    undo()
                self.assertEqual(list(failing(out)), [label], out)
                found, fixed = doctor_lines(out)[label][1].split("; fix: ", 1)
                self.assertIn(detail, found, out)
                self.assertIn(fix, fixed, out)
                self.assertEqual(code, 2, refused)
                self.assertIn(detail, refused)
                self.assertIn(fix, refused)
                self.assertFalse(record.exists(), f"lead-init kept a record while refusing: {refused}")
        # A publisher that runs nothing is refused by doctor and lead-init, naming the fix, whether or not the tracker
        # publisher is bound beside it and whatever tracker.updates says; with none bound, the binding needs one.
        tracker = json.loads(binding_path.read_text())["state"]["publishers"]
        for publishers, updates in (([{"id": "tracker", "kind": "host"}], None),
                                    ([{"id": "live", "kind": "host"}, *tracker], None),
                                    ([{"id": "tracker", "kind": "host"}], "manual")):
            with self.subTest(publishers=publishers, updates=updates):
                undo = both(edit_json(binding_path, lambda b: b["state"].update(publishers=publishers)),
                            edit_json(m / conf_rel, lambda c: c["tracker"].update(updates=updates))
                            if updates else (lambda: None))
                try:
                    out = self.doctor(fx, "--offline", expect_code=1)
                    # The binding no longer validates, and the line says what replaces the publisher.
                    self.assertEqual(list(failing(out)), ["orchestration binding"], out)
                    found, fixed = failing(out)["orchestration binding"].split("; fix: ", 1)
                    self.assertIn("doesn't validate: the binding names a publisher that runs nothing", found, out)
                    self.assertIn("run-delivery's tracker publisher (lead-setup adds it)", fixed, out)
                    record = fx.tmp / "record"
                    code, refused = fx.flow("lead-init", cwd=m)
                    self.assertEqual(code, 2, refused)
                    self.assertIn("the binding names a publisher that runs nothing", refused)
                    self.assertFalse(record.exists(), f"lead-init kept a record while refusing: {refused}")
                finally:
                    undo()
        undo = edit_json(binding_path, lambda b: b["state"].update(publishers=[]))
        try:
            out = self.doctor(fx, "--offline", expect_code=1)
            self.assertIn("the binding has no tracker publisher", failing(out)["snapshots and publishers"], out)
            manual = edit_json(m / conf_rel, lambda c: c["tracker"].update(updates="manual"))
            try:
                line = doctor_lines(self.doctor(fx, "--offline"))["snapshots and publishers"]
                self.assertEqual(line[0], "ok")
                self.assertIn("the lead posts tracker updates by hand", line[1])
            finally:
                manual()
        finally:
            undo()
        # doctor names when the lanes' review move is made, and that it is opt-in.
        for transitions, said in (({"review": ["Submit for review"]},
                                   "tracker.transitions.reviewWhen is unset, so lanes make the review move by hand"),
                                  ({"review": ["Submit for review"], "reviewWhen": "gates-passed"},
                                   "tracker.transitions.reviewWhen is gates-passed")):
            undo = edit_json(m / conf_rel, lambda c: c["tracker"].update(transitions=transitions))
            try:
                line = doctor_lines(self.doctor(fx, "--offline"))["snapshots and publishers"]
                self.assertEqual(line[0], "ok", line)
                self.assertIn(said, line[1])
            finally:
                undo()
        # Node exactly at the runtime's nodeMinimum is enough.
        exact = fx.tmp / "exact-node-bin"
        exact.mkdir()
        (exact / "node").write_text("#!/bin/sh\necho v22.6.0\n")
        (exact / "node").chmod(0o755)
        undo = without_harness()
        try:
            out = self.doctor(fx, "--offline", env={**fx.env, "PATH": f"{exact}{os.pathsep}{self.without('node')}",
                                                    "HOME": str(fx.tmp)})
        finally:
            undo()
        self.assertEqual(doctor_lines(out)["Node"], ("ok", f"{exact / 'node'} meets the runtime's nodeMinimum 22.6.0"), out)
        # Each gap closed again, the clean fixture passes, and lead-init starts the record.
        self.assertEqual(failing(self.doctor(fx, "--offline", expect_code=0)), {})
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)

    def test_a_local_setup_needs_no_configuration_on_the_remote_base(self) -> None:
        fx = self.fixture()
        m = fx.main
        # The remote base carries neither the configuration nor the delivery file; the lead's working tree has both.
        kept = {rel: (m / rel).read_text() for rel in (".agents/run-delivery.json", ".agents/deliveries/FX-1.json")}
        sh("git", "rm", "-q", *kept, cwd=m)
        commit(m, "FX-1: the configuration leaves the base")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        for rel, text in kept.items():
            (m / rel).parent.mkdir(parents=True, exist_ok=True)
            (m / rel).write_text(text)
        out = self.doctor(fx, "--offline", expect_code=1)
        self.assertEqual(list(failing(out)), ["configuration on the remote base"], out)
        self.assertIn("origin/feature/FX-1 has no configuration for lanes: it lacks .agents/run-delivery.json and "
                      ".agents/deliveries/FX-1.json", doctor_lines(out)["configuration on the remote base"][1])
        code, refused = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 2, refused)
        self.assertIn("set RUN_DELIVERY_LOCAL=1", refused)
        # A base that isn't on the remote at all.
        sh("git", "push", "-q", "origin", "HEAD:refs/heads/elsewhere", cwd=m)
        sh("git", "push", "-q", "origin", "--delete", "feature/FX-1", cwd=m)
        self.assertIn("has no configuration for lanes: the branch isn't on the remote",
                      doctor_lines(self.doctor(fx, "--offline"))["configuration on the remote base"][1])
        sh("git", "push", "-q", "origin", "HEAD:refs/heads/feature/FX-1", cwd=m)
        sh("git", "fetch", "-q", "origin", cwd=m)
        # A local setup's lanes read their own working tree: doctor skips the check and lead-init starts.
        local = {**fx.env, "RUN_DELIVERY_LOCAL": "1"}
        self.assertEqual(doctor_lines(self.doctor(fx, "--offline", env=local))["configuration on the remote base"][0],
                         "skipped")
        code, out = fx.flow("lead-init", cwd=m, env=local)
        self.assertEqual(code, 0, out)

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

    def test_a_busy_port_outside_the_lease_names_its_listener_in_the_adapter_run(self) -> None:
        fx = self.fixture()
        listener = subprocess.Popen([sys.executable, "-c", LISTENER, str(fx.port), "served-by-hand", "plain"],
                                    stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(listener.stdout.readline().strip(), "listening")
            out = self.doctor(fx, "--run-adapters")
            status, detail = doctor_lines(out)["in-app adapter contract"]
            self.assertEqual(status, "wrong", out)
            self.assertIn(f"outside the in-app lease: pid {listener.pid} (", detail)
            self.assertIn(f"kill {listener.pid}", detail)
            self.assertIsNone(listener.poll(), "doctor stopped a process it didn't start")
        finally:
            if listener.poll() is None:  # started here, stopped by the pid recorded here
                listener.kill()
            listener.wait()
            listener.stdout.close()

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
                              capture_output=True, env=clean_environment(), cwd=tempfile.gettempdir())
        self.assertEqual(done.returncode, 2, done.stderr)
        script = ("import os, runpy, sys; os.name = 'nt'; sys.argv = [sys.argv[1], 'doctor']; "
                  "runpy.run_path(sys.argv[0], run_name='__main__')")
        done = subprocess.run([sys.executable, "-B", "-c", script, str(self.tool)], text=True, capture_output=True,
                              env=clean_environment(), cwd=tempfile.gettempdir())
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


# A serve for the two-run race, coordinated through marker files in `sync` so the race happens the same way every
# time: the first run's serve binds only once the second run's serve has started, which is after the second run found
# the port free; the second's then finds the port taken. "second-exits" tries the bind and dies of it, as a static
# server does, a moment after the other has bound; "second-hangs" never listens, as a serve still starting up does;
# "second-waits" binds as soon as the other run lets the port go.
RACE_SERVE = r'''
import functools, http.server, os, socket, socketserver, sys, time
role, port, site, sync = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
mark = lambda name: open(os.path.join(sync, name), "w").write(str(os.getpid()))
def until(name):
    deadline = time.monotonic() + 60
    while not os.path.exists(os.path.join(sync, name)):
        if time.monotonic() > deadline:
            sys.exit("never saw " + name)
        time.sleep(0.02)
if role == "first":
    mark("first-started")
    until("second-started")
else:
    mark("second-started")
    until("first-bound")
    if role == "second-hangs":
        time.sleep(120)
        sys.exit(0)
    if role == "second-waits":  # binds once the other run has let the port go
        while True:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.05)
    else:
        time.sleep(0.3)  # still alive when the port first answers, as a serve slowed by a loaded machine is
class Server(http.server.ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
server = Server(("127.0.0.1", port), functools.partial(http.server.SimpleHTTPRequestHandler, directory=site))
mark(role + "-bound")
server.serve_forever()
'''

# The first run's check: it waits for the case's release, so the first run's server stays up for as long as the case
# needs it, then runs the contract check.
GATED_CHECK = r'''
import os, runpy, sys, time
release, check = sys.argv.pop(1), sys.argv.pop(1)
deadline = time.monotonic() + 60
while not os.path.exists(release) and time.monotonic() < deadline:
    time.sleep(0.02)
sys.argv[0] = check
runpy.run_path(check, run_name="__main__")
'''

# The tool's command line with a shorter wait for the serve to listen, so a case can see the wait run out.
SHORT_READY_TOOL = r'''
import sys
sys.path.insert(0, sys.argv[1])
import run_delivery as flow
flow.SERVE_READY_SECONDS = float(sys.argv[2])
flow.main(sys.argv[3:])
'''

# run_in_app, called as app-check calls it, on a stamped copy of the adapter fixture's page; prints the report.
RUN_IN_APP = r'''
import json, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import run_delivery as flow
flow.CFG = flow.Config(Path.cwd(), need_delivery=False)
if len(sys.argv) > 2:
    flow.SERVE_READY_SECONDS = float(sys.argv[2])
with tempfile.TemporaryDirectory() as tmp:
    site = Path(tmp) / "site"
    shutil.copytree(flow.TOOL / "assets/adapter-fixture", site)
    flow.write_stamp(site, "f" * 40, "e" * 40)
    try:
        report = flow.run_in_app(site, {"path": "/", "holds": [], "fails": [], "clicks": [], "evals": [],
                                        "afterEvals": [], "wait": 800}, [], "race", 5)
    except flow.Stop as stop:
        sys.exit("run-delivery: " + str(stop))
print(json.dumps({"ready": report.get("ready"), "build": report.get("build")}))
'''


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class ServeRaceTests(unittest.TestCase):
    """Two runs on one port, each under a lease of its own (as suites running at once each hold their own): the run
    whose serve loses the port refuses, naming why, and never checks against the other run's server."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="run-delivery-serve-race-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.port = free_port()
        self.sync = self.root / "sync"
        self.sync.mkdir()
        (self.root / "race_serve.py").write_text(RACE_SERVE)
        (self.root / "static_serve.py").write_text(STATIC_SERVE)
        (self.root / "contract_check.py").write_text(FAKE_CONTRACT_CHECK)
        (self.root / "gated_check.py").write_text(GATED_CHECK)
        (self.root / "home").mkdir()
        self.env = {**clean_environment(), "HOME": str(self.root / "home")}
        self.procs: list[subprocess.Popen] = []
        self.addCleanup(self.nothing_left_running)
        self.addCleanup(self.stop_everything)

    def adopter(self, name: str, serve: list[str], gated: bool = False) -> Path:
        """A repository whose binding declares only the in-app adapters, on the case's one port, with its own lock.
        A gated one's check waits for the case's release first."""
        repo = self.root / name
        (repo / ".agents").mkdir(parents=True)
        sh("git", "init", "-q", "-b", "main", cwd=repo)
        (repo / ".agents/run-delivery.json").write_text(json.dumps({"schemaVersion": "delivery-flow/1", "inApp": {
            "lease": "app", "lock": str(self.root / f"{name}.lock"), "port": self.port,
            "build": {"argv": ["true"], "artifact": "out"}, "serve": {"argv": serve},
            "check": {"argv": [sys.executable, "-B", *([str(self.root / "gated_check.py"), str(self.sync / "release")]
                                                       if gated else []),
                               str(self.root / "contract_check.py"), "{spec}", "{port}", "{args}"]},
            "lockWaitSeconds": 5, "checkTimeoutSeconds": 60}}, indent=2) + "\n")
        commit(repo, "binding")
        return repo

    def race_serve(self, role: str) -> list[str]:
        return [sys.executable, "-B", str(self.root / "race_serve.py"), role, "{port}", "{dir}", str(self.sync)]

    def start(self, command: str, repo: Path, ready_seconds: float | None = None) -> subprocess.Popen:
        tool, short = (ROOT / TOOL_RELATIVE).parent, [] if ready_seconds is None else [str(ready_seconds)]
        if command == "adapter-test":
            argv = ([sys.executable, "-B", str(ROOT / TOOL_RELATIVE), "adapter-test"] if not short
                    else [sys.executable, "-B", "-c", SHORT_READY_TOOL, str(tool), *short, "adapter-test"])
        else:
            argv = [sys.executable, "-B", "-c", RUN_IN_APP, str(tool), *short]
        proc = subprocess.Popen(argv, cwd=repo, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append(proc)
        return proc

    def stop_everything(self) -> None:
        """Each run, by the pid recorded at its start, then any serve group a run's lease recorded and left (a run
        stopped here never stops its own)."""
        for proc in self.procs:
            stop_and_reap(proc)
            if proc.stdout:
                proc.stdout.close()
        for lock in self.root.glob("*.lock"):
            for group in re.findall(r"pgid (\d+)", lock.read_text()):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(int(group), signal.SIGKILL)

    def finish(self, proc: subprocess.Popen) -> tuple[int, str]:
        out, _ = proc.communicate(timeout=180)
        return proc.returncode, out

    def nothing_left_running(self) -> None:
        left = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            left = [line for line in sh("ps", "-axo", "command=").stdout.splitlines() if str(self.root) in line]
            if not left:
                break
            time.sleep(0.2)
        self.assertEqual(left, [], "the race left processes running")
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", self.port)), 0, "the race left the port listening")

    def race(self, command: str, loser: str, release_early: bool = False,
             tag: str = "") -> tuple[tuple[int, str], tuple[int, str]]:
        """The first run's serve binds only once the second run, having found the port free, has started its own. The
        first run's server stays up until the second run ends, or, released early, for a second after it binds."""
        first = self.start(command, self.adopter(f"first{tag}", self.race_serve("first"), gated=True))
        deadline = time.monotonic() + 60
        while not (self.sync / "first-started").exists():
            self.assertLess(time.monotonic(), deadline, "the first run's serve never started")
            self.assertIsNone(first.poll(), first.stdout.read() if first.poll() is not None else "")
            time.sleep(0.02)
        second = self.start(command, self.adopter(f"second{tag}", self.race_serve(loser)))
        if release_early:
            while not (self.sync / "first-bound").exists():
                self.assertLess(time.monotonic(), deadline + 60, "the first run's serve never bound")
                time.sleep(0.02)
            time.sleep(1)
            (self.sync / "release").touch()
        lost = self.finish(second)
        (self.sync / "release").touch()
        done = self.finish(first), lost
        self.nothing_left_running()  # before the case's own cleanup, which stops what a lease recorded
        return done

    def assert_refused_for_the_other_server(self, done: tuple[int, str], state: str) -> None:
        code, out = done
        self.assertNotEqual(code, 0, f"the run whose serve lost the port went on against the other run's:\n{out}")
        self.assertIn(f"port {self.port} answered, but not from this run's serve", out)
        self.assertIn(state, out)
        self.assertIn(str(self.root / "second.lock.serve.log"), out, "the refusal doesn't say where the serve's output is")
        self.assertNotIn("Connection refused", out)

    def test_adapter_test_refuses_when_its_serve_dies_of_the_port_another_run_took(self) -> None:
        winner, loser = self.race("adapter-test", "second-exits")
        self.assertEqual(winner[0], 0, winner[1])
        self.assert_refused_for_the_other_server(loser, "This run's serve exited with code 1")
        self.assertIn("Address already in use", loser[1], "the refusal doesn't carry the serve's own error")

    def test_adapter_test_refuses_when_another_run_answers_while_its_serve_starts(self) -> None:
        winner, loser = self.race("adapter-test", "second-hangs")
        self.assertEqual(winner[0], 0, winner[1])
        self.assert_refused_for_the_other_server(loser, "This run's serve is still running")
        self.assertIn(f"pid {(self.sync / 'first-started').read_text()} (", loser[1],
                      "the refusal doesn't name the other run's server")

    def test_an_app_check_refuses_when_its_serve_dies_of_the_port_another_run_took(self) -> None:
        winner, loser = self.race("run_in_app", "second-exits")
        self.assertEqual(winner[0], 0, winner[1])
        self.assertTrue(json.loads(winner[1].strip().splitlines()[-1])["ready"], winner[1])
        self.assert_refused_for_the_other_server(loser, "This run's serve exited with code 1")
        self.assertIn("Address already in use", loser[1], "the refusal doesn't carry the serve's own error")

    def test_an_app_check_refuses_when_another_run_answers_while_its_serve_starts(self) -> None:
        winner, loser = self.race("run_in_app", "second-hangs")
        self.assertEqual(winner[0], 0, winner[1])
        self.assert_refused_for_the_other_server(loser, "This run's serve is still running")
        self.assertIn(f"pid {(self.sync / 'first-started').read_text()} (", loser[1],
                      "the refusal doesn't name the other run's server")

    def test_a_serve_that_gets_the_port_once_the_other_run_lets_it_go_is_this_runs(self) -> None:
        """The allow side of the race: the other run answered first, but stopped while this run's serve was still
        starting, and this run's serve then took the port. Its check runs against its own server."""
        for command in ("adapter-test", "run_in_app"):
            with self.subTest(command=command):
                for marker in self.sync.iterdir():
                    marker.unlink()
                winner, second = self.race(command, "second-waits", release_early=True, tag=f"-{command}")
                self.assertEqual(winner[0], 0, winner[1])
                self.assertEqual(second[0], 0, second[1])
                self.assertTrue((self.sync / "second-waits-bound").exists(), "this run's serve never took the port")

    def test_a_single_run_uses_its_own_serve_and_keeps_its_output(self) -> None:
        """The allow side: alone on the port, each command passes, and its serve's output is kept beside its lock."""
        for command in ("adapter-test", "run_in_app"):
            with self.subTest(command=command):
                repo = self.adopter(f"alone-{command}", [sys.executable, "-B", str(self.root / "static_serve.py"),
                                                         "{port}", "{dir}"])
                code, out = self.finish(self.start(command, repo))
                self.assertEqual(code, 0, out)
                self.nothing_left_running()
                log = self.root / f"alone-{command}.lock.serve.log"
                self.assertIn("GET /.flow-build.json", log.read_text(), "the serve's output wasn't kept")

    def test_a_serve_whose_listener_is_its_own_child_is_its_own(self) -> None:
        """A serve that runs its server as a child (npm run serve, sh -c ...) listens from the serve's own process
        group: that listener is this run's."""
        serve = ["sh", "-c", f'"{sys.executable}" -B "{self.root / "static_serve.py"}" "$0" "$1" & wait', "{port}", "{dir}"]
        for command in ("adapter-test", "run_in_app"):
            with self.subTest(command=command):
                code, out = self.finish(self.start(command, self.adopter(f"child-{command}", serve)))
                self.assertEqual(code, 0, out)
                self.nothing_left_running()

    def without_lsof(self) -> None:
        """The case's commands run where the host can't name a port's listeners: every program on PATH but lsof."""
        folder = self.root / "bin-without-lsof"
        folder.mkdir()
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            for program in (Path(directory).iterdir() if Path(directory).is_dir() else []):
                target = folder / program.name
                if program.name != "lsof" and not target.exists() and os.access(program, os.X_OK):
                    target.symlink_to(program)
        self.env = {**self.env, "PATH": str(folder)}

    def test_where_listeners_cant_be_named_a_running_serve_is_taken_as_the_listener(self) -> None:
        self.without_lsof()
        for command in ("adapter-test", "run_in_app"):
            with self.subTest(command=command):
                repo = self.adopter(f"unnamed-{command}", [sys.executable, "-B", str(self.root / "static_serve.py"),
                                                           "{port}", "{dir}"])
                code, out = self.finish(self.start(command, repo))
                self.assertEqual(code, 0, out)
                self.nothing_left_running()

    def test_where_listeners_cant_be_named_a_serve_that_died_of_the_port_is_still_refused(self) -> None:
        self.without_lsof()
        winner, loser = self.race("run_in_app", "second-exits")
        self.assertEqual(winner[0], 0, winner[1])
        self.assertIn("a process this host can't name", loser[1])
        self.assert_refused_for_the_other_server(loser, "This run's serve exited with code 1")

    def test_a_serve_still_running_that_never_listens_is_named(self) -> None:
        serve = ["sh", "-c", "echo the serve is still starting >&2; exec sleep 60"]
        for command in ("adapter-test", "run_in_app"):
            with self.subTest(command=command):
                code, out = self.finish(self.start(command, self.adopter(f"silent-{command}", serve), ready_seconds=1))
                self.assertNotEqual(code, 0, out)
                self.assertIn(f"the serve command didn't start listening on port {self.port}", out)
                self.assertIn("the serve is still starting", out)
                self.nothing_left_running()

    def test_a_serve_that_exits_without_listening_is_named(self) -> None:
        serve = ["sh", "-c", "echo the serve gave up >&2; exit 3"]
        for command in ("adapter-test", "run_in_app"):
            with self.subTest(command=command):
                code, out = self.finish(self.start(command, self.adopter(f"quit-{command}", serve)))
                self.assertNotEqual(code, 0, out)
                self.assertIn(f"the serve command exited with code 3 before it listened on port {self.port}", out)
                self.assertIn("the serve gave up", out)
                self.nothing_left_running()


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
    "the binding's <forge-owner> and <forge-name> -> the fake forge's repository",
    "supervisor.passSeconds -> 1, so the supervisor's passes come within the case's time",
    "inApp.port -> a free port, so suites running at once on one machine never share the guide's one port",
)
FIXTURE_KEYS = {"<delivery-key>": "FX-1", "<story-key>": "FX-2"}
# Section 8 of the guide: the operating rules, by their opening words.
OPERATING_RULES = [
    "Lanes are sessions of their own.", "Lanes never merge the base.", "Review evidence is captured at hand-off.",
    "Restart long-running commands after an update.", "Start the merge queue with default signals.",
    "The evidence page follows each marked story's merge.", "Sign-in values stay out of commands.",
]
GUIDE_SECTIONS = ["1. What the workflow does", "2. Prerequisites", "3. Install a release", "4. Configure the repository",
                  "5. Run the setup check", "6. A first delivery", "7. A worked adopter",
                  "8. Running deliveries", "9. Host notes", "10. Operating rules", "Troubleshooting"]
# The guide's walk-through: what the lead does, in order. Everything else is the supervisor's or a lane's.
GUIDE_WALKTHROUGH = ["Plan and review", "Set up", "Assign", "The lane delivers a story", "Accept", "Answer"]
# What the guide must name, as phrases in its whitespace-normalized text.
# What the guide must say, each where it belongs: (the section it is in, the phrase), whitespace-normalized.
GUIDE_NAMES = {
    "the supervisor": ("## 1. What the workflow does", "The **supervisor**, `lead-run`, does every other lead duty"),
    "lane-start": ("### The lane delivers a story", "lane-start <story-key> --base main"),
    "the settings-PR rule": ("## 8. Running deliveries", "**Settings land through reviewed pull requests.** On a protected "
                             "base, the binding, `.agents/run-delivery.json`, the delivery file and the lane brief reach "
                             "the base through a pull request, reviewed and merged like any change"),
    "finish lines by covers and proofs": ("### Answer", "It judges each finish line by the record's covers and proofs"),
    "the review carry": ("### Accept", "lead-carry-review <story-key> --from <reviewed-head> --to <queued-head> --reason"),
}
# The worked adopter's binding: its bases, and a merge step that calls the forge adapter, pinned to the checked head.
WORKED_BASES = ["main", "integration-*"]
WORKED_MERGE = ["python3", "-B", ".agent-skills/current/skills/run-delivery/adapters/forge_bitbucket.py", "merge",
                "--source", "{branch}", "--head", "{expectedHead}", "--strategy", "merge", "--destination", "{base}"]
WORKED_CANDIDATE = ["python3", "-B", ".agent-skills/current/skills/run-delivery/adapters/forge_bitbucket.py",
                    "candidate", "--key", "{candidate}"]
# The worked adopter's review gate: run-delivery's own check of the pipeline's review at the head the queue merges.
WORKED_REVIEW_GATE = ["python3", "-B", ".agent-skills/current/skills/run-delivery/scripts/run_delivery.py",
                      "pr-review-current", "--branch", "{branch}", "--head", "{expectedHead}", "--wait"]
# Every push in an example is labelled, on the line just above it, for an unprotected base.
UNPROTECTED = "unprotected base"
PUSH = re.compile(r"\bgit\b(?:\s+-[Cc]\s+\S+)*\s+push\b|git\(\"push\"")


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
            conf["inApp"]["serve"]["argv"] = serve_argv(fakes / "static_serve.py")
            # Each case's lease is its own (its lock is under the case's HOME), so a fixed port would be shared by
            # every suite on the machine that reached this case at once, with nothing to keep them apart.
            declared = conf["inApp"]["port"]
            self.assertIsInstance(declared, int)
            conf["inApp"]["port"] = next(port for port in iter(free_port, None) if port != declared)
            used.add(GUIDE_SUBSTITUTIONS[8])
            self.assertTrue(listens_under_stalled_lookup(conf["inApp"]["serve"]["argv"], 5),
                            "the guide's serve waited for a stalled name lookup")
            conf["inApp"]["check"]["argv"] = [sys.executable, "-B", str(fakes / "contract_check.py"), "{spec}", "{port}",
                                              "{args}"]
            used.add(GUIDE_SUBSTITUTIONS[1])
            self.assertNotIn("supervisor", conf, "the guide shows the default pass; the case sets its own")
            conf["supervisor"] = {"passSeconds": 1}
            used.add(GUIDE_SUBSTITUTIONS[7])
            body = json.dumps(conf, indent=2) + "\n"
            self.assertNotEqual(json.loads(body)["inApp"]["port"], json.loads(block["body"])["inApp"]["port"],
                                "the case runs on the guide's own port, which every suite on the machine shares")
        if path == ".agents/orchestration.json" and "<forge-owner>" in body:
            body = body.replace("<forge-owner>", "fixture").replace("<forge-name>", "repository")
            used.add(GUIDE_SUBSTITUTIONS[6])
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
        self.addCleanup(forget_case, root)
        # Registered after the folder's removal, so it runs first: a failed case stops the supervisor it started.
        self.addCleanup(stop_supervisors, root)
        watch_case(root)  # and an interrupted one, too
        fakes, home, bin_dir = root / "fakes", root / "home", root / "bin"
        for folder in (fakes, home, bin_dir):
            folder.mkdir()
        (fakes / "fake_forge.py").write_text(FAKE_FORGE)
        (fakes / "contract_check.py").write_text(FAKE_CONTRACT_CHECK)
        (bin_dir / "python3").symlink_to(sys.executable)
        # The adopter's Node, first on PATH: the guide's prerequisites ask for the runtime's minimum.
        node = find_node22(self.tool)
        if node:
            (bin_dir / "node").symlink_to(node)
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
        # The guide's last block is the lane's last word: the supervisor merges, cleans up, meets the finish line and
        # closes the delivery itself, then stops. Nothing more is run.
        closed = repo / ".orchestration/flow-log/FX-1.closed.json"
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and not closed.exists():
            time.sleep(0.5)
        log = repo / ".orchestration/flow-log/FX-1.supervisor.jsonl"
        self.assertTrue(closed.exists(), "the supervisor never closed the delivery: "
                        + (log.read_text()[-3000:] if log.exists() else "it never ran a pass"))
        lock = repo / ".orchestration/flow-log/FX-1.supervisor.lock"
        while time.monotonic() < deadline:
            with open(lock, "a+") as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(probe, fcntl.LOCK_UN)
                    break
                except BlockingIOError:
                    time.sleep(0.5)
        self.assertTrue(Path(json.loads(closed.read_text())["friction"]).is_file(), "no friction note stub")
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
        met = [e for e in state["journal"] if e["kind"] == "finish.met"]
        self.assertEqual([e.get("subject") for e in met], ["greeting"], "the finish line wasn't met once, by its proofs")
        self.assertFalse(lane.exists(), "the lane's checkout outlived its merge")
        # The operator's checkout was clean and on the base, so the close-out fast-forwarded it.
        self.assertEqual((repo / "src/greeting.txt").read_text(), "Hello\n")
        sh("git", "fetch", "-q", "origin", cwd=repo)
        self.assertEqual(sh("git", "show", "origin/main:src/greeting.txt", cwd=repo).stdout, "Hello\n")
        self.assertTrue(list((home / ".local/share/run-delivery/archive/repository/FX-1").glob("*/state/journal.jsonl")))
        left = []
        while time.monotonic() < deadline:
            left = [line for line in sh("ps", "-axo", "command=").stdout.splitlines() if str(root) in line]
            if not left:
                break
            time.sleep(0.5)
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

    def test_the_guide_says_the_line_map_shows_needs_you_and_when_it_is_unavailable(self) -> None:
        text = " ".join(GUIDE.read_text(encoding="utf-8").split())
        self.assertIn("The line map's Now chapter shows the same \"Needs you\" from the supervisor's last pass, what "
                      "holds the close first, each item with where it comes from and what clears it. While the supervisor "
                      "isn't running, or its list is a pass overdue, the chapter says the list is unavailable.", text)
        self.assertNotIn("The live view doesn't show this list", text)

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
        help_text = sh(sys.executable, "-B", self.tool, "--help", cwd=tempfile.gettempdir(), env=clean_environment()).stdout
        commands = set(re.search(r"\{([a-z,-]+)\}", help_text).group(1).split(","))
        self.assertTrue(named)
        self.assertLessEqual(named, commands, f"the guide or skill text names commands the tool lacks: {named - commands}")
        # Its queue and state commands are the release's own.
        for script, command in re.findall(r"orchestrate-work/scripts/(\w+\.py) [^\n]*? (run|journal) ", text):
            with self.subTest(script=script, command=command):
                usage = sh(sys.executable, "-B", self.generation / "skills/orchestrate-work/scripts" / script, "--help").stdout
                self.assertIn(command, usage)

    def test_the_walk_through_is_the_leads_own_steps(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        prose = re.sub(r"^```.*?^```$", "", text, flags=re.MULTILINE | re.DOTALL)
        section = prose[prose.index("## 6. A first delivery"):prose.index("## 7. A worked adopter")]
        self.assertEqual(re.findall(r"^### (.+)$", section, re.MULTILINE), GUIDE_WALKTHROUGH)

    def section(self, heading: str) -> str:
        """One section of the guide, by its heading line, up to the next heading at its level or above."""
        text = GUIDE.read_text(encoding="utf-8")
        level = heading.split(" ", 1)[0]
        start = text.index(f"\n{heading}\n") + 1
        following = [m.start() for m in re.finditer(r"^#{1,%d} " % len(level), text[start + len(heading):], re.MULTILINE)]
        end = start + len(heading) + following[0] if following else len(text)
        return text[start:end]

    def test_the_guide_names_what_the_workflow_now_does(self) -> None:
        for what, (heading, phrase) in GUIDE_NAMES.items():
            with self.subTest(what=what):
                self.assertIn(" ".join(phrase.split()), " ".join(self.section(heading).split()))
        blocks = guide_blocks()
        # The formatter ignore list, in its own file, carries the generated lane brief.
        ignore = next(b for b in blocks if b["path"] == ".prettierignore")
        self.assertIn("docs/plans/*.lane-brief.md", ignore["body"].splitlines())
        # The worked adopter's binding and configuration, read as the JSON they are.
        worked = [json.loads(b["body"]) for b in blocks
                  if b["kind"] == "outline" and b["lang"] == "json" and b in [m.groupdict() for m in GUIDE_BLOCK.finditer(
                      self.section("## 7. A worked adopter"))]]
        binding = next(document for document in worked if "merge" in document)
        self.assertEqual(binding["bases"], WORKED_BASES)
        self.assertEqual(binding["merge"], WORKED_MERGE)
        self.assertEqual(binding["candidate"], WORKED_CANDIDATE)
        self.assertEqual(binding["reviewGate"]["argv"], WORKED_REVIEW_GATE)
        conf = next(document for document in worked if "forge" in document)
        # The queue's bound on the gate outlasts the gate's own wait for the review, and the binding's ceiling allows it.
        self.assertGreater(binding["reviewGate"]["timeoutSeconds"], conf["review"]["pr"]["waitSeconds"])
        self.assertLessEqual(binding["reviewGate"]["timeoutSeconds"], binding["checkTimeoutCeiling"])
        self.assertEqual(set(binding["environment"]), {"FLOW_FORGE_REPOSITORY", "FLOW_FORGE_TOKEN_ENV"})
        self.assertTrue(any("tracker_publisher.py" in part for entry in binding["state"]["publishers"]
                            for part in entry["argv"]))
        conf = next(document for document in worked if "forge" in document)
        self.assertEqual(conf["tracker"]["transitions"]["reviewWhen"], "pipeline-green")
        self.assertTrue(conf["forge"]["mergeNeedsReady"])
        # The base-mismatch refusal points at this anchor; the heading it names is there, exactly.
        tool = (SKILL / "scripts/run_delivery.py").read_text(encoding="utf-8")
        self.assertIn("adopting.md#base-mismatch", tool)
        self.assertRegex(GUIDE.read_text(encoding="utf-8"), r"(?m)^### Base mismatch$")

    def test_no_example_pushes_unless_labelled_for_an_unprotected_base(self) -> None:
        for block in guide_blocks():
            lines = block["body"].splitlines()
            for index, line in enumerate(lines):
                if PUSH.search(line) and not line.lstrip().startswith("#"):
                    above = next((earlier for earlier in reversed(lines[:index]) if earlier.strip()), "")
                    with self.subTest(line=line):
                        self.assertTrue(above.lstrip().startswith("#") and UNPROTECTED in above,
                                        f"a push not labelled, on the line above it, for an unprotected base: {line}")

    def test_the_orchestration_guide_says_which_duties_run_delivery_does(self) -> None:
        guide = " ".join((ROOT / "skills/orchestrate-work/references/orchestration-guide.md").read_text(
            encoding="utf-8").split())
        for phrase in ("With run-delivery's supervisor running, the lead does none of these by hand",
                       "the supervisor judges `finish.met` by the record's covers and proofs",
                       "a finish line the record can't prove holds the close",
                       "an entry the lead journalled by hand is skipped",
                       "run-delivery's tracker publisher posts every update in this table",
                       "With `tracker.updates` set to `manual`, the lead and the lanes make them instead"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, guide)
        skill = " ".join((ROOT / "skills/orchestrate-work/SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("With run-delivery's supervisor running, the lead assigns, accepts and answers", skill)

    def test_the_operating_rules_are_pinned(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        section = text[text.index("## 10. Operating rules"):text.index("## Troubleshooting")]
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


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryArgumentTests(unittest.TestCase):
    """Python 3.11's argparse refuses `--` after a positional that takes any number of values; 3.11 is a declared minimum."""

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("run_delivery_for_arguments", ROOT / "skills/run-delivery/scripts/run_delivery.py")
        cls.tool = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(cls.tool)
        finally:
            sys.dont_write_bytecode = previous

    def setUp(self) -> None:
        """Each case calls main() from a repository of its own, which holds no delivery, with none of the settings the
        tool reads: so the status line every lead command ends with, and anything else that resolves a delivery, looks
        only inside the case. (main() itself drops any configuration an earlier call left.)"""
        folder = Path(tempfile.mkdtemp(prefix="run-delivery-arguments-"))
        self.addCleanup(shutil.rmtree, folder, True)
        sh("git", "init", "-q", "-b", "main", cwd=folder)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(folder)
        settings = without_tool_settings()
        settings.__enter__()
        self.addCleanup(settings.__exit__, None, None, None)

    def parsed(self, *argv: str) -> list[list[str]]:
        """What reaches argparse, and what the command then receives."""
        seen, received = [], []
        real = argparse.ArgumentParser.parse_args

        def recording(parser, args=None, namespace=None):
            if parser.prog == "run_delivery.py":
                seen.append(list(args))
            return real(parser, args, namespace)

        with mock.patch.object(argparse.ArgumentParser, "parse_args", recording), \
                mock.patch.object(self.tool, "app_check", lambda args: received.append(list(args.passthrough))), \
                mock.patch.object(self.tool, "app_video", lambda args: received.append(list(args.passthrough))), \
                mock.patch.object(self.tool.signal, "signal"):
            self.tool.main(list(argv))
        return [seen[0], received[0]]

    def test_arguments_after_the_separator_go_to_the_adapter_and_never_reach_argparse(self) -> None:
        for command in (["app-check", "FX-1", "page"], ["app-video", "FX-1", "clip"]):
            for tail in (["--fixture-user", "reader"], ["--password=x"], ["plain", "--flag"], []):
                with self.subTest(command=command[0], tail=tail):
                    given = [*command, "--mode", "loaded", "--path", "/one"]
                    reached, received = self.parsed(*given, "--", *tail)
                    self.assertEqual(reached, given)
                    self.assertEqual(received, tail)

    def test_a_later_separator_belongs_to_the_adapter(self) -> None:
        given = ["app-check", "FX-1", "page", "--mode", "loaded", "--path", "/one"]
        self.assertEqual(self.parsed(*given, "--", "a", "--", "b")[1], ["a", "--", "b"])

    def test_a_second_separator_reaches_the_video_recording_as_given(self) -> None:
        recorded = []

        def recording(dist, spec, passthrough, *rest):
            recorded.append(list(passthrough))
            raise self.tool.Stop("recorded enough")

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(self.tool, "cfg", lambda: mock.Mock(require=lambda key: key)), \
                mock.patch.object(self.tool, "video_tools_problem", lambda: None), \
                mock.patch.object(self.tool, "need_in_app", lambda: {"baseline": "before", "build": {"artifact": "after"}}), \
                mock.patch.object(self.tool, "repo", lambda: Path(folder)), \
                mock.patch.object(self.tool, "stamped", lambda dist: {"head": "h"}), \
                mock.patch.object(self.tool, "clean_head", lambda: "h"), \
                mock.patch.object(self.tool, "review_dir", lambda key: Path(folder)), \
                mock.patch.object(self.tool, "run_in_app", recording), \
                mock.patch.object(self.tool.signal, "signal"), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.suppress(SystemExit):
            self.tool.main(["app-video", "FX-1", "clip", "--mode", "loaded", "--path", "/one",
                            "--", "--", "--fixture-user", "reader"])
        self.assertEqual(recorded, [["--", "--fixture-user", "reader"]])

    def test_a_command_without_pass_through_arguments_parses_as_it_always_did(self) -> None:
        # A required positional may follow the separator, as argparse allows.
        seen, received = [], []
        real = argparse.ArgumentParser.parse_args

        def recording(parser, args=None, namespace=None):
            if parser.prog == "run_delivery.py":
                seen.append(list(args))
            return real(parser, args, namespace)

        given = ["lead-assign", "--lane", "lane-1", "--title", "A title", "--", "FX-1"]
        with mock.patch.object(argparse.ArgumentParser, "parse_args", recording), \
                mock.patch.object(self.tool, "lead_assign", lambda args: received.append(list(args.keys))), \
                mock.patch.object(self.tool.signal, "signal"):
            self.tool.main(given)
        self.assertEqual(seen, [given])
        self.assertEqual(received, [["FX-1"]])

    def test_only_the_command_decides_not_a_value_that_spells_one(self) -> None:
        for given, keys in ((["lead-assign", "FX-1", "--lane", "app-check", "--title", "t"], ["FX-1"]),
                            (["lead-assign", "FX-1", "--lane", "l", "--title", "app-video"], ["FX-1"]),
                            (["--delivery", "app-check", "lead-assign", "FX-1", "--lane", "l", "--title", "t"], ["FX-1"]),
                            (["lead-assign", "--lane", "app-check", "--title", "t", "--", "FX-1"], ["FX-1"])):
            with self.subTest(given=given):
                received = []
                with mock.patch.object(self.tool, "lead_assign", lambda args: received.append(list(args.keys))), \
                        mock.patch.object(self.tool.signal, "signal"):
                    self.tool.main(given)
                self.assertEqual(received, [keys])
        received = []
        with mock.patch.object(self.tool, "app_check", lambda args: received.append(list(args.passthrough))), \
                mock.patch.object(self.tool.signal, "signal"):
            self.tool.main(["--delivery", "lead-assign", "app-check", "FX-1", "page", "--mode", "loaded", "--path", "/one", "--", "x"])
            self.tool.main(["--delivery=lead-assign", "app-check", "FX-1", "page", "--mode", "loaded", "--path", "/one", "--", "y"])
        self.assertEqual(received, [["x"], ["y"]])

    def test_an_abbreviated_top_level_option_is_refused_on_every_python(self) -> None:
        # The command is found after `--delivery` as spelled, so a shortened spelling is an error, not a 3.11-only one.
        with mock.patch.object(self.tool, "app_check", side_effect=AssertionError("reached the command")), \
                mock.patch.object(self.tool.signal, "signal"), contextlib.redirect_stderr(io.StringIO()) as stderr, \
                self.assertRaises(SystemExit) as refused:
            self.tool.main(["--deliv", "K", "app-check", "FX-1", "page", "--mode", "loaded", "--path", "/one", "--", "x"])
        self.assertEqual(refused.exception.code, 2)
        self.assertIn("invalid choice: 'K'", stderr.getvalue(), "read as a command, so never as an option's value")

    def test_no_separator_leaves_the_arguments_as_they_were(self) -> None:
        given = ["app-check", "FX-1", "page", "--mode", "loaded", "--path", "/one"]
        self.assertEqual(self.parsed(*given), [given, []])

    def delivery_of(self, *argv: str) -> str | None:
        """The delivery key a command line selects, as the command sees it."""
        seen = []
        record = lambda args: seen.append(self.tool.DELIVERY_ARG)
        with mock.patch.object(self.tool, "lead_init", record), mock.patch.object(self.tool, "plan_review_prepare", record), \
                mock.patch.object(self.tool, "lead_plan", record), mock.patch.object(self.tool, "lead_assign", record), \
                mock.patch.object(self.tool, "view", record), mock.patch.object(self.tool.signal, "signal"):
            self.tool.main(list(argv))
        return seen[0]

    def test_the_delivery_option_goes_before_or_after_the_command(self) -> None:
        for argv in (["--delivery", "K", "lead-init"], ["lead-init", "--delivery", "K"], ["--delivery=K", "lead-init"],
                     ["lead-init", "--delivery=K"], ["--delivery", "K", "lead-init", "--delivery", "K"],
                     ["plan-review", "prepare", "--delivery", "K"], ["lead-plan", "why", "c1", "--why", "x", "--delivery", "K"],
                     ["lead-assign", "FX-1", "--delivery", "K", "--lane", "l", "--title", "t"],
                     ["view", "--delivery", "K"], ["--delivery", "K", "view"], ["view", "--archive", "a", "--delivery=K"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.delivery_of(*argv), "K")
        self.assertIsNone(self.delivery_of("lead-init"))

    def written_by_patch_from(self, answers: list[dict], compute) -> tuple[object, list[list[str]], list[int]]:
        """patch_from against a fake state tool: each read returns the next version, each patch the next answer."""
        reads, calls = [], []

        def show():
            reads.append(len(reads) + 1)
            return {"state": {"version": reads[-1], "n": reads[-1]}}

        def state_tool(*args, check=True):
            calls.append(list(args))
            answer = answers[len(calls) - 1]
            if isinstance(answer, str):  # a state tool that crashed: no JSON, its traceback on stderr
                return types.SimpleNamespace(returncode=1, stdout=answer, stderr="Traceback: the state tool broke")
            return types.SimpleNamespace(returncode=0 if answer.get("ok") else 1, stdout=json.dumps(answer, indent=2),
                                         stderr="")
        with mock.patch.object(self.tool, "load_state", show), mock.patch.object(self.tool, "state_tool", state_tool):
            written = self.tool.patch_from(compute, "the thing")
        return written, calls, reads

    def test_patch_from_computes_each_try_from_its_own_fresh_read(self) -> None:
        stale = {"ok": False, "code": "version_stale", "error": "patch is pinned to version 1; state is at 2"}
        written, calls, reads = self.written_by_patch_from([stale, stale, {"ok": True}], lambda doc: {"n": doc["state"]["n"]})
        self.assertEqual(written, {"n": 3})
        self.assertEqual([(c[c.index("--version") + 1], c[-1]) for c in calls], [("1", '{"n": 1}'), ("2", '{"n": 2}'), ("3", '{"n": 3}')])

    def test_patch_from_doesnt_retry_another_refusal_or_write_nothing(self) -> None:
        refused = {"ok": False, "code": "writer_not_owned", "error": "the lead can't write that"}
        with self.assertRaises(self.tool.Stop) as stopped:
            self.written_by_patch_from([refused, {"ok": True}], lambda doc: {"n": 1})
        self.assertIn("the state tool refused the thing: the lead can't write that", str(stopped.exception))
        written, calls, _ = self.written_by_patch_from([], lambda doc: None)
        self.assertEqual((written, calls), (None, []))
        def gone(doc):
            raise self.tool.Stop("it moved on")
        with self.assertRaises(self.tool.Stop) as stopped:
            self.written_by_patch_from([], gone)
        self.assertEqual(str(stopped.exception), "it moved on")

    def test_patch_from_stops_at_once_on_a_refusal_it_cant_read(self) -> None:
        with self.assertRaises(self.tool.Stop) as stopped:
            self.written_by_patch_from(["not json", {"ok": True}], lambda doc: {"n": 1})
        self.assertIn("the state tool refused the thing: Traceback: the state tool broke", str(stopped.exception))
        self.assertNotIn("version_stale", str(stopped.exception))

    def test_patch_from_passes_an_element_patch_through(self) -> None:
        element = ["--element", "queue[subject=K]", "--set", '{"/stage": "queued"}']
        written, calls, _ = self.written_by_patch_from([{"ok": True}], lambda doc: element)
        self.assertEqual(written, element)
        self.assertEqual(calls[0][-4:], element)

    def test_two_different_deliveries_are_a_usage_error(self) -> None:
        with mock.patch.object(self.tool, "lead_init", side_effect=AssertionError("reached the command")), \
                mock.patch.object(self.tool.signal, "signal"), contextlib.redirect_stderr(io.StringIO()) as stderr, \
                self.assertRaises(SystemExit) as refused:
            self.tool.main(["--delivery", "A", "lead-init", "--delivery", "B"])
        self.assertEqual(refused.exception.code, 2)
        self.assertIn("--delivery A and --delivery B", stderr.getvalue())

    def test_lead_init_resolve_and_setup_take_their_new_options(self) -> None:
        received = []
        with mock.patch.object(self.tool, "lead_init", lambda args: received.append(args.replace)), \
                mock.patch.object(self.tool, "lead_resolve", lambda args: received.append((args.id, args.why))), \
                mock.patch.object(self.tool, "lead_setup", lambda args: received.append(
                    (args.authority, args.granted_by, args.granted_on, args.no_authority))), \
                mock.patch.object(self.tool.signal, "signal"):
            self.tool.main(["lead-init"])
            self.tool.main(["lead-init", "--replace"])
            self.tool.main(["lead-resolve", "12", "--why", "the operator ruled"])
            self.tool.main(["lead-setup", "K", "--plan", "p.md", "--pattern", "x", "--no-authority"])
            self.tool.main(["lead-setup", "K", "--plan", "p.md", "--pattern", "x", "--authority", "push",
                            "--granted-by", "op", "--granted-on", "2026-11-03"])
        self.assertEqual(received, [False, True, ("12", "the operator ruled"), (None, None, None, True),
                                    ("push", "op", "2026-11-03", False)])

    def test_a_flag_after_the_separator_is_not_read_as_the_tools_own(self) -> None:
        given = ["app-check", "FX-1", "page", "--mode", "loaded", "--path", "/one"]
        reached, received = self.parsed(*given, "--", "--path", "/other")
        self.assertEqual(reached, given)
        self.assertEqual(received, ["--path", "/other"])


class SupervisorLineFallbackTests(unittest.TestCase):
    """The supervisor's status line names each candidate checking on the binding's fallback because the lead selected
    no checks for it, as the runner records it on the candidate's row."""

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("run_delivery_for_status", ROOT / "skills/run-delivery/scripts/run_delivery.py")
        cls.tool = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(cls.tool)
        finally:
            sys.dont_write_bytecode = previous

    def line(self, queue: list[dict]) -> str:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "state.json").write_text(json.dumps({"queue": queue}))
            c = types.SimpleNamespace(state=state)
            with mock.patch.object(self.tool, "read_lock", lambda _: (True, {"pid": 42, "address": "http://127.0.0.1:1"})), \
                    mock.patch.object(self.tool, "supervisor_lock_path", lambda _: state / "lock"), \
                    mock.patch.object(self.tool, "waits_on", lambda _: ""), \
                    mock.patch.object(self.tool, "lead_listener", lambda *_: {"pid": 42}):  # a lead listens here
                return self.tool.supervisor_line(c)

    def test_a_candidate_checking_on_the_fallback_is_named_with_the_fallback(self) -> None:
        row = lambda subject, stage, selection: {"subject": subject, "stage": stage, "checkSelection": selection}
        fallback = {"by": "provider", "fallback": ["tests.full-suite", "lint"]}
        self.assertEqual(self.line([row("FX-2", "checking", fallback)]),
                         "supervisor: running (pid 42); live view http://127.0.0.1:1; FX-2 checks on the fallback, "
                         "tests.full-suite, lint: the lead selected no checks")
        self.assertIn("FX-2 checks on the fallback", self.line([row("FX-2", "checked", fallback)]))
        # A row syncing again keeps the last run's selection until the runner selects anew: it isn't named from it.
        quiet = [row("FX-2", "checking", {"by": "provider"}), row("FX-3", "checking", {"by": "lead", "head": "a" * 40,
                                                                                       "reason": "r"}),
                 row("FX-4", "merged", fallback), row("FX-5", "queued", fallback), row("FX-7", "syncing", fallback),
                 row("FX-8", "synced", fallback), {"subject": "FX-6", "stage": "checking"}]
        self.assertEqual(self.line(quiet), "supervisor: running (pid 42); live view http://127.0.0.1:1")


class SupervisorLineListenerTests(unittest.TestCase):
    """The status line names a missing listener from the record it is given, never from another delivery's."""

    def test_the_status_line_reads_the_listener_from_its_own_record(self) -> None:
        spec = importlib.util.spec_from_file_location("run_delivery_for_listener_line", ROOT / "skills/run-delivery/scripts/run_delivery.py")
        tool = importlib.util.module_from_spec(spec)
        previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            spec.loader.exec_module(tool)
        finally:
            sys.dont_write_bytecode = previous
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            c = types.SimpleNamespace(state=state)
            (state / "state.json").write_text(json.dumps({"lanes": [{"id": "lane-9", "state": "working"}], "queue": []}))
            with mock.patch.object(tool, "load_state", side_effect=AssertionError("read another record")):
                self.assertEqual(tool.listener_words(c), "; the lead isn't listening: no lead-wait runs while lane-9 (working)")
                (state / "state.json").write_text(json.dumps({"lanes": [{"id": "lane-9", "state": "idle"}], "queue": []}))
                self.assertEqual(tool.listener_words(c), "")
                (state / "state.json").write_text("{")
                self.assertEqual(tool.listener_words(c), "")


class RunDeliveryRenderHalvesTests(unittest.TestCase):
    """A supervisor pass publishes, derives "Needs you", then renders: each half runs only its own entries."""

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("run_delivery_for_halves", ROOT / "skills/run-delivery/scripts/run_delivery.py")
        cls.tool = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(cls.tool)
        finally:
            sys.dont_write_bytecode = previous

    def test_each_half_runs_only_its_own_entries_and_no_half_runs_both(self) -> None:
        state = {"directory": "s", "renderers": [{"id": "page", "builtin": "static-page", "output": "p.html"}],
                 "publishers": [{"id": "post", "argv": ["true"]}], "liveHome": "page"}
        unhomed = {key: value for key, value in state.items() if key != "liveHome"}
        c = types.SimpleNamespace(release=Path("/release"), state=Path("/record"), root=Path("/repo"))
        for only, expected in ((None, state), ("publishers", {**unhomed, "renderers": []}),
                               ("renderers", {**state, "publishers": []})):
            with self.subTest(only=only):
                seen = []
                done = types.SimpleNamespace(returncode=0, stdout="", stderr="")
                with mock.patch.object(self.tool, "binding_state", lambda _: state), \
                        mock.patch.object(self.tool, "run", lambda argv, **_: seen.append(argv) or done):
                    self.assertEqual(self.tool.render_views(c, only=only), "rendered")
                self.assertEqual(json.loads(seen[0][seen[0].index("--state-config") + 1]), expected)

    def test_the_publishers_half_of_a_binding_with_a_live_home_runs_its_publishers(self) -> None:
        state_tool = ROOT / "skills/orchestrate-work/scripts/delivery_state.py"
        config = {"directory": "s", "liveHome": "home",
                  "publishers": [{"id": "sink", "argv": ["sh", "-c", "cat > \"$0/published-copy.json\"", "{stateDirectory}"]}],
                  "renderers": [{"id": "home", "argv": ["sh", "-c", "cat >/dev/null; echo home"], "output": "home.html"}]}
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / "state"
            c = types.SimpleNamespace(release=ROOT, state=record, root=Path(folder))
            subprocess.run([sys.executable, "-B", str(state_tool), "--dir", str(record), "init", "--headline", "A delivery"],
                           check=True, capture_output=True)
            with mock.patch.object(self.tool, "binding_state", lambda _: config):
                self.assertEqual(self.tool.render_views(c, only="publishers"), "rendered")
                self.assertTrue((record / "published-copy.json").is_file())
                self.assertFalse((record / "home.html").exists(), "the publishers half draws no view")
                # The renderers half still answers to the home it names.
                self.assertEqual(self.tool.render_views(c, only="renderers"), "rendered")
                self.assertTrue((record / "home.html").is_file())


class RunDeliveryContinuousIntegrationTests(unittest.TestCase):
    def test_posix_runners_install_what_the_video_and_harness_cases_need(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^      - uses: actions/setup-node@v4\n"
                                   r"        with:\n          node-version: \"22\.6\.0\"$")
        self.assertRegex(workflow, r"(?m)^        if: runner\.os == 'Linux'\n        run: .*apt-get install -y ffmpeg$")
        self.assertRegex(workflow, r"(?m)^        if: runner\.os == 'macOS'\n        run: brew install ffmpeg$")
        self.assertNotIn("Windows", workflow)
        # Both are in place before the suite runs.
        self.assertLess(workflow.index("install -y ffmpeg"), workflow.index("scripts/run-tests.py"))
        self.assertLess(workflow.index("brew install ffmpeg"), workflow.index("scripts/run-tests.py"))


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class DeliveryPostureReaderTests(unittest.TestCase):
    """The one reader of `.agents/delivery-posture.json`, against every vector the harness's resolver also runs."""

    SECONDS = 1

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("run_delivery_for_posture", ROOT / "skills/run-delivery/scripts/run_delivery.py")
        cls.tool = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(cls.tool)
        finally:
            sys.dont_write_bytecode = previous
        cls.file = json.loads(POSTURE_VECTORS.read_text(encoding="utf-8"))

    def resolve(self, vector: dict) -> tuple[dict, float]:
        with tempfile.TemporaryDirectory() as folder:
            # The reader runs with the cleaned environment: none of the runner's delivery settings reach it.
            with mock.patch.dict(os.environ, {name: PLANTED_MARKER for name in TOOL_SETTINGS}):
                clone, env = posture_vector_repo(vector, Path(folder))
            self.assertEqual(sorted(set(TOOL_SETTINGS) & set(env)), [], "the posture reader got a delivery setting")
            started = time.monotonic()
            with mock.patch.dict(os.environ, env, clear=True):
                result = self.tool.resolve_delivery_posture(clone, "origin", "main", seconds=self.SECONDS)
            return result, time.monotonic() - started

    def test_every_vector_gives_its_posture_code_and_source(self) -> None:
        self.assertEqual(self.file["path"], self.tool.POSTURE_FILE)
        for vector in self.file["vectors"]:
            with self.subTest(vector=vector["id"]):
                result, took = self.resolve(vector)
                self.assertEqual({key: result[key] for key in ("posture", "code", "source")}, vector["expect"], result)
                self.assertEqual(set(result), {"posture", "reason", "source", "baseTipSha", "code"})
                self.assertTrue(result["reason"].strip(), result)
                if vector["expect"]["code"]:  # each code's reason names the path and the base
                    self.assertIn(self.file["path"], result["reason"])
                    self.assertIn(self.file["base"], result["reason"])
                self.assertEqual(result["baseTipSha"] is None, vector["expect"]["source"] == "fallback", result)
                self.assertLess(took, 20, "a read outran its bound")

    def test_every_blank_character_has_its_vector_and_nothing_else_is_blank(self) -> None:
        listed = {int(code.removeprefix("U+"), 16) for code in self.file["blankCharacters"]}
        ids = {v["id"] for v in self.file["vectors"] if v["id"].startswith("whitespace-reason-u")}
        self.assertEqual(ids, {f"whitespace-reason-u{code:04x}" for code in listed})
        self.assertEqual(listed, {code for code in range(0x10000) if self.tool.POSTURE_BLANK.fullmatch(chr(code))})

    def vector(self, name: str) -> dict:
        return next(v for v in self.file["vectors"] if v["id"] == name)

    def test_one_git_command_failing_past_the_ref_reads_as_unreadable(self) -> None:
        real = shutil.which("git")
        for name, body in (("an unparseable listing", 'if [ "$1" = ls-tree ]; then echo garbage; exit 0; fi'),
                           ("a listing with no size", 'if [ "$1" = ls-tree ]; then printf \'100644 blob abc x\\tp\\0\'; exit 0; fi'),
                           ("a failing show", 'if [ "$1" = show ]; then exit 1; fi')):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as folder:
                clone, env = posture_vector_repo(self.vector("valid-focused"), Path(folder))
                stubs = Path(folder) / "stubs"
                stubs.mkdir()
                (stubs / "git").write_text(f'#!/bin/sh\n{body}\nexec {real} "$@"\n')
                (stubs / "git").chmod(0o755)
                with mock.patch.dict(os.environ, {"PATH": f"{stubs}{os.pathsep}{env['PATH']}"}):
                    result = self.tool.resolve_delivery_posture(clone, "origin", "main", seconds=5)
                self.assertEqual((result["posture"], result["code"], result["source"]), ("gate", "posture_unreadable", "fallback"), result)

    def test_a_rewritten_base_is_read_at_its_new_tip_and_no_tag_arrives(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            clone, _ = posture_vector_repo(self.vector("valid-focused"), Path(folder))
            self.assertEqual(self.tool.resolve_delivery_posture(clone, "origin", "main")["posture"], "focused")
            author = Path(folder) / "author"
            sh("git", "tag", "a-tag", cwd=author)
            sh("git", "reset", "-q", "--hard", "HEAD~1", cwd=author)
            (author / POSTURE_PATH).parent.mkdir(exist_ok=True)
            (author / POSTURE_PATH).write_text(json.dumps({"schemaVersion": "delivery-posture/1", "posture": "gate", "reason": "Back on."}))
            commit(author, "rewritten")
            sh("git", "push", "-q", "--force", "--tags", "origin", "main", cwd=author)
            result = self.tool.resolve_delivery_posture(clone, "origin", "main")
            self.assertEqual((result["posture"], result["code"]), ("gate", None), result)
            self.assertEqual(sh("git", "tag", "--list", cwd=clone).stdout.strip(), "", "the read imported a tag")

    def test_the_bound_is_fifteen_seconds_by_default(self) -> None:
        self.assertEqual(self.tool.POSTURE_SECONDS, 15)
        self.assertEqual(inspect.signature(self.tool.resolve_delivery_posture).parameters["seconds"].default, 15)

    def test_the_declared_reason_comes_back_and_the_tip_is_the_fetched_one(self) -> None:
        vector = next(v for v in self.file["vectors"] if v["id"] == "valid-focused")
        with tempfile.TemporaryDirectory() as folder:
            clone, _ = posture_vector_repo(vector, Path(folder))
            result = self.tool.resolve_delivery_posture(clone, "origin", "main")
            tip = sh("git", "ls-remote", "origin", "refs/heads/main", cwd=clone).stdout.split()[0]
        self.assertEqual((result["reason"], result["baseTipSha"]), ("The gate is still being optimized.", tip))

    def test_a_hanging_git_is_stopped_with_the_child_that_holds_its_output(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            child = Path(folder) / "child.pid"
            stub = Path(folder) / "git"
            stub.write_text(f'#!/bin/sh\n{POSTURE_HANG} &\necho $! > "{child}"\nwait\n')
            stub.chmod(0o755)
            started = time.monotonic()
            with mock.patch.dict(os.environ, {"PATH": f"{folder}{os.pathsep}{os.environ['PATH']}"}):
                done = self.tool.posture_git(Path(folder), ["status"], self.SECONDS)
            took = time.monotonic() - started
            pid = int(child.read_text())
        self.assertIsNone(done)
        self.assertLess(took, self.SECONDS + 5, "the child holding the pipe kept the bound from holding")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        self.fail("the child that held the output outlived the bound")

    def test_no_hanging_stub_s_command_line_matches_the_machine_wide_sleep_pattern(self) -> None:
        for name, text in POSTURE_GIT_STUBS.items():
            with self.subTest(stub=name):
                self.assertIsNone(MACHINE_WIDE_SLEEP.search(text), text)
        # What the pattern matches is a live command line, so read the running stub's own, in its own group.
        with tempfile.TemporaryDirectory() as folder:
            stub = Path(folder) / "git"
            stub.write_text(POSTURE_GIT_STUBS["hangs"])
            stub.chmod(0o755)
            proc = subprocess.Popen([str(stub)], start_new_session=True)
            try:
                deadline, lines = time.monotonic() + 5, []
                while time.monotonic() < deadline:
                    rows = sh("ps", "-A", "-o", "pgid=,command=").stdout.splitlines()
                    lines = [row.split(None, 1)[1] for row in rows
                             if row.split(None, 1)[0] == str(proc.pid) and len(row.split(None, 1)) == 2]
                    if any("sleep" in line for line in lines):
                        break
                    time.sleep(0.05)
                self.assertTrue(any(line.split()[:2] == POSTURE_HANG.split() for line in lines), lines)
                self.assertEqual([line for line in lines if MACHINE_WIDE_SLEEP.search(line)], [], lines)
            finally:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()

    def test_a_tip_with_a_declaration_in_a_subfolder_read_from_there_gives_the_same_answer(self) -> None:
        vector = next(v for v in self.file["vectors"] if v["id"] == "valid-focused")
        with tempfile.TemporaryDirectory() as folder:
            clone, _ = posture_vector_repo(vector, Path(folder))
            (clone / "deep").mkdir()
            self.assertEqual(self.tool.resolve_delivery_posture(clone / "deep", "origin", "main")["posture"], "focused")

    def test_what_the_record_shows_of_a_declaration_is_always_valid_plan_text(self) -> None:
        from tests.test_delivery_views_plan import plan, views

        declared = {"posture": "focused", "source": "origin/main", "code": None, "baseTipSha": "a" * 40}
        for name, change in (("long", {"reason": "x" * 70000}), ("lines", {"reason": "first\nsecond\u2028third\ttab"}),
                             ("surrogate", {"reason": "bad \ud800 text"}), ("controls", {"reason": "\x01\x02"}),
                             ("long source", {"reason": "r", "source": "o/" + "b" * 500})):
            with self.subTest(case=name):
                said = self.tool.posture_said({**declared, **change})
                self.assertTrue(said["reason"].strip())
                # delivery-plan/1's caps, whatever the declaration held.
                self.assertLessEqual(len(said["source"]), 200, said)
                self.assertLessEqual(len(said["reason"]), 600, said)
                if name == "long source":
                    self.assertEqual(said["source"], ("o/" + "b" * 500)[:200])
                if name == "long":
                    self.assertEqual(said["reason"], "x" * 600)
                said["reason"].encode("utf-8")
                closing = {"fullHealth": "skipped", **said}
                views.validate_plan({**plan(), "closeOut": closing})

    def test_the_close_out_reviews_the_leads_integration_between_integrating_and_merging(self) -> None:
        guide = " ".join((ROOT / "skills/orchestrate-work/references/orchestration-guide.md").read_text(encoding="utf-8").split())
        close_out = guide[guide.index("### 8. Close out"):guide.index("## Lane brief template")]
        steps = ["1. **Integrate.**", "2. **Review the integration.**", "3. **Merge to the default branch**"]
        at = [close_out.index(step) for step in steps]
        self.assertEqual(at, sorted(at), "the review sits between the integration and the merge")
        for phrase in (
            "Run `$review-work` with the delivery's lenses and round bound on the lead's own changes since the last unit merged",
            "removing the delivery-only files, any merge of the default branch with its conflict resolutions, read as "
            "`git show --cc` and not as the whole incoming branch, and any fix the merged tree needed",
            "A delivery whose integration changed nothing beyond removing the delivery-only files still runs it, on that removal",
            "Findings are triaged as a lane's are",
            "each round is journaled under the delivery as `review.round`, with the delivery's key as its subject",
            "3. **Merge to the default branch** only once that review is aligned.",
            "Where the lead closes by hand, take these steps before journaling `delivery.closed`, so the review's rounds "
            "reach the archived record. Under the supervisor, which archives the record once nothing holds the close, "
            "the rounds land in the live record after the archive, and the lead's post of each round on the epic is "
            "their durable trace.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, close_out)
        self.assertIn("| `review.round` | None: a lane posts each round on its own ticket, as its brief says, and the lead "
                      "posts the integration review's rounds on the epic. |", guide)

    def test_the_close_out_says_how_far_a_close_got_by_its_two_marks(self) -> None:
        def normal(path: str) -> str:
            return " ".join((ROOT / path).read_text(encoding="utf-8").split())
        guide = normal("skills/orchestrate-work/references/orchestration-guide.md")
        close_out = guide[guide.index("### 8. Close out"):guide.index("## Lane brief template")]
        for phrase in (
            "The record marks how far a close got, in `meta.closing`, with two marks that each name the "
            "`delivery.closed` journal entry they belong to.",
            "The `started` mark goes into the record with that entry, whoever journals it through the state tool",
            "The `finished` mark is written once the close's archive has landed, by the supervisor's close-out or by "
            "`lead-close`, in the live record and in the archived copy",
            "`closed.json` names the closing entry too.",
            "Only the latest close counts. New work after a close, a `lane.assigned`, `candidate.handed` or "
            "`check.started` entry, reopens the delivery",
            "A mark or a `closed.json` naming an earlier entry says nothing of the new close, which archives again.",
            "A delivery `lead-close` closed stays closed to `lead-run` until a later `delivery.closed` is journaled.",
            "**Finished:** both marks name the closing entry.",
            "**Stopped partway:** the `started` mark names the closing entry, no `finished` mark does, and no close is running.",
            "`lead-run --resume` finishes it when the record meets the close condition: no planned story, nothing "
            "unmerged, every finish line met and no busy lane. A lane checkout that cleanup hasn't removed yet doesn't count",
            "Otherwise `lead-close` finishes it, since a resumed supervisor would hold on what is still open; "
            "`lead-close` doesn't wait for the tracker and lists the updates it still owes.",
            "A resumed supervisor finds the archive it already made once `closed.json` names the close, and archives "
            "afresh otherwise, leaving the earlier folder without the `finished` mark; `lead-close` always archives the "
            "record afresh. Marking a close finished again changes nothing.",
            "whoever journals it through the state tool, the supervisor or the lead by hand.",
            "The supervisor's friction note comes after it.",
            "**Finished:** both marks name the closing entry. The status line run-delivery's lead commands print, "
            "`doctor`'s supervisor row and the line map say the delivery is closed.",
            "The status line and `doctor`'s supervisor row, as `wrong`, say the close stopped partway and name what "
            "finishes it, as the line map's \"The close stopped partway\" box does.",
            # The by-hand step reads its entry off the step before it.
            "- Journal `delivery.closed`, and render the views one last time. - Mark the close finished with "
            "`delivery_state.py close-finished --entry <its journal id>`",
            "**Unmarked:** no `started` mark names the closing entry, as in a record from before the marks or a "
            "`delivery.closed` journaled by a tool that writes none. The close still runs and archives, writes no mark, "
            "and reads as closed from `closed.json` alone.",
            "Mark the close finished with `delivery_state.py close-finished --entry <its journal id>`, and render again; "
            "a close left with only its `started` mark reads as stopped partway.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, close_out)
        skill = normal("skills/run-delivery/SKILL.md")
        for phrase in (
            "ends with the supervisor's status line, which reads the record's close marks",
            "a stopped supervisor whose close has both marks says the delivery is closed",
            "lists the tracker updates still owed without waiting for them",
            "says the close stopped partway and names `lead-run --resume` or `lead-close` to finish it",
            "writes the close's `finished` mark in the record and the archived copy once the archive has landed, and "
            "names the closing entry in `closed.json`. It finishes a close that stopped partway.",
            "A close with its `started` mark and no `finished` mark, and no supervisor running, is `wrong`: it stopped "
            "partway, and the row names what finishes it",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, skill)

    def test_the_depth_vectors_nest_as_deep_as_they_say(self) -> None:
        # An independent count of the deepest point, so a vector that drifts from its case shows here, not as agreement.
        def deepest(text: str) -> int:
            depth = most = 0
            decoder = json.JSONDecoder()
            for token in re.finditer(r'"(?:[^"\\]|\\.)*"|[\[\]{}]', text):
                if token.group()[0] == '"':
                    decoder.decode(token.group())  # a real JSON string, or the vector is malformed for another reason
                elif token.group() in "[{":
                    depth += 1
                    most = max(most, depth)
                else:
                    depth -= 1
            self.assertEqual(depth, 0, "brackets don't balance")
            return most
        vectors = {vector["id"]: vector for vector in self.file["vectors"]}
        for name, depth in {"nested-64-deep": 64, "nested-65-deep": 65, "objects-nested-65-deep": 65, "nested-1500-deep": 1500,
                            "objects-then-arrays-each-64-deep": 64, "closing-brackets-in-a-string-at-depth-64": 64,
                            "escaped-backslash-ends-a-string-before-65-deep": 65, "sixty-five-sibling-arrays-at-depth-2": 2,
                            "sixty-five-sibling-objects-at-depth-2": 2, "sixty-five-brackets-in-a-string": 1,
                            "escaped-quote-then-sixty-five-brackets-in-a-string": 1}.items():
            with self.subTest(vector=name):
                self.assertEqual(deepest(expanded(vectors[name]["base"])), depth)
        filled = expanded(vectors["nested-as-deep-as-64-kib-holds"]["base"]).encode("utf-8")
        self.assertGreater(len(filled), self.tool.POSTURE_LIMIT_BYTES - 2)
        self.assertLessEqual(len(filled), self.tool.POSTURE_LIMIT_BYTES)
        self.assertGreater(deepest(filled.decode("utf-8")), 32000)
        self.assertEqual(len(re.findall(r"\[", vectors["sixty-five-sibling-arrays-at-depth-2"]["base"]["text"])), 65)
        self.assertEqual(len(re.findall(r'"o\d+": \{\}', vectors["sixty-five-sibling-objects-at-depth-2"]["base"]["text"])), 65)

    def test_the_posture_table_says_nesting_past_64_levels_is_malformed(self) -> None:
        # The reader gates a declaration nested past POSTURE_MAX_DEPTH, so the table an adopter reads has to say so.
        config = " ".join((ROOT / "skills/run-delivery/references/config.md").read_text(encoding="utf-8").split())
        self.assertEqual(self.tool.POSTURE_MAX_DEPTH, 64)
        self.assertIn("| Not valid UTF-8 JSON, JSON nesting arrays or objects more than 64 levels deep (the top-level object "
                      "being 1), an object failing the shape above, or an unknown `posture` | `gate` | `posture_malformed` |",
                      config)

    def test_neither_page_promises_a_rerun_while_the_base_says_focused(self) -> None:
        # Under focused no pass runs a fullHealth rerun, so lead-resolve's wording says it waits for gate.
        def normal(path: str) -> str:
            return " ".join((ROOT / path).read_text(encoding="utf-8").split())
        self.assertIn("or asks the supervisor's next pass to rerun a failed `fullHealth` (under the `focused` posture, once "
                      "the base returns to `gate`).", normal("skills/run-delivery/SKILL.md"))
        self.assertIn("`lead-resolve full-health --why TEXT` asks the next pass to rerun a failed `fullHealth` (under the "
                      "`focused` posture, once the base returns to `gate`).", normal("skills/run-delivery/references/config.md"))

    def test_the_rule_is_pinned_where_a_delivery_without_the_supervisor_reads_it(self) -> None:
        def normal(path: str) -> str:
            return " ".join((ROOT / path).read_text(encoding="utf-8").split())
        skill = normal("skills/orchestrate-work/SKILL.md")
        guide = normal("skills/orchestrate-work/references/orchestration-guide.md")
        self.assertIn("With `focused` declared in `.agents/delivery-posture.json` at the tip of the delivery's base, "
                      "the close-out runs no `fullHealth`, and a failed run at that head holds nothing", skill)
        self.assertIn("Nothing runs in its place", skill)
        self.assertIn("Unless the delivery's base declares the `focused` posture at its tip, run the binding's "
                      "`fullHealth` once, on the final base", guide)
        self.assertIn("runs `fullHealth` once per base head, unless the base declares the `focused` posture", guide)
        # Under gate the post-merge tier runs after each merge; close-out starts a run only where none has happened.
        self.assertIn("`fullHealth` is the post-merge tier, and under `gate` it doesn't wait for the close: after each "
                      "merge or batch, the supervisor runs it at the new base head", guide)
        self.assertIn("the supervisor runs `fullHealth` at the final head only when no run at that head has happened, as "
                      "in a delivery that merged nothing", guide)
        self.assertIn("Nothing runs in its place, and no `fullHealth` runs after a merge either", skill)
        self.assertIn("remains the backstop for anything a selection missed, unless the base declares the `focused` posture", guide)
        self.assertIn("say the run was skipped under the posture, naming its source and reason", guide)
        # fullHealth is a backstop only under gate: under focused nothing stands in for what a selection left out.
        self.assertIn("Under `gate` it is the backstop for every lead-selected check set: anything a selection left out "
                      "runs here. Under `focused` nothing is: a check a selection left out never runs.", guide)
        self.assertNotIn("It is the backstop for every", guide)
        self.assertIn("A selection is the lead's judgment; the post-merge `fullHealth` run is its backstop, unless the "
                      "base declares the `focused` posture.", skill)
        config = normal("skills/run-delivery/references/config.md")
        self.assertIn("the post-merge tier: once the delivery has merged anything, and unless the base declares the "
                      "`focused` posture, the binding's `fullHealth` at the base head", config)
        self.assertIn("a failed `fullHealth` at the current base head, unless the base declares the `focused` posture, "
                      "a broken base", config)



@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class LaneStartAndReadinessTests(unittest.TestCase):
    """lane-start moves its story to building in the record in every tracker mode, and in manual mode prints the start
    move and post for the lane to make. lead-assign and doctor --delivery check that a story's tracker item offers the
    first start move, from the tracker snapshot, before its lane starts."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def delivery(self, updates: str | None = "manual", transitions: dict | None = None, assign: bool = True,
                 init: bool = True):
        """FX-3 (lane-2) planned, with in-app checks off, and the tracker settings given; initialized and assigned unless
        told not."""
        from tests.test_run_delivery_tracker import FileBackend  # here, so neither module imports the other at load
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        m = fx.main
        path = setup_delivery(fx)
        d = json.loads(path.read_text())
        d["stories"]["FX-3"]["inApp"] = "none"
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: FX-3 needs no in-app checks")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        backend = FileBackend(fx.tmp)
        with backend.edit() as store:
            for key in ("FX-1", "FX-2", "FX-3"):
                store["issues"][key] = {"status": "Backlog", "comments": [], "markers": [], "labels": [], "parent": None,
                                        "summary": key}
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        tracker = {**conf["tracker"], **backend.adapter(),
                   "transitions": {"start": ["Start work"]} if transitions is None else transitions}
        if updates:
            tracker["updates"] = updates
        conf["tracker"] = tracker
        own = fx.tmp / "run-delivery.json"
        own.write_text(json.dumps(conf, indent=2))
        fx.env.update({"RUN_DELIVERY_CONFIG": str(own), **backend.env()})
        if not init:
            return fx
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        if assign:
            self.assign(fx)
        return fx

    def assign(self, fx, *extra: str) -> tuple[int, str]:
        code, out = fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", *extra, cwd=fx.main)
        self.assertEqual(code, 0, out)
        return code, out

    def record(self, fx) -> dict:
        return json.loads(sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py",
                             "--dir", fx.tmp / "record", "show").stdout)

    def stage(self, fx, key: str = "FX-3") -> str:
        doc = self.record(fx)["state"]
        return next(entry["stage"] for entry in [*doc.get("planned", []), *doc["queue"]] if entry["subject"] == key)

    def started(self, fx) -> str:
        code, out = fx.flow("lane-start", "FX-3", "--base", "feature/FX-1", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return out

    def doctor(self, fx) -> tuple[str, str]:
        """doctor --delivery's readiness line, with the repository bound so its delivery rows run."""
        bound = doctor_binding()
        bound["state"]["publishers"] = []
        (fx.main / ".agents/orchestration.json").write_text(json.dumps(bound, indent=2))
        own = Path(fx.env["RUN_DELIVERY_CONFIG"])
        conf = json.loads(own.read_text())
        del conf["gates"]  # The binding's fallback checks are the gates.
        own.write_text(json.dumps(conf, indent=2))
        code, out = fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=fx.main)
        self.assertIn("story readiness", doctor_lines(out), out)
        return doctor_lines(out)["story readiness"]

    def snapshot(self, fx, path: str = "record/snapshots/tracker.json", ago: int = 0, subject: str = "FX-3",
                 others: tuple = (), **item) -> None:
        """A kept tracker snapshot, serve's unless another path is given, taken `ago` seconds back, holding the item
        given and any others."""
        path = fx.tmp / path
        path.parent.mkdir(parents=True, exist_ok=True)
        taken = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        path.write_text(json.dumps({"schemaVersion": "work-snapshot/1", "source": "tracker", "takenAt": taken,
                                    "items": [{"subject": subject, **item}, *others]}))

    # lane-start, in each mode.

    def test_in_manual_mode_a_new_start_prints_the_move_and_post_and_moves_the_story_to_building(self) -> None:
        fx = self.delivery("manual")
        self.assertEqual(self.stage(fx), "planned")
        out = self.started(fx)
        self.assertIn('Tracker move (your story FX-3): "Start work", with your tracker tools.', out)
        self.assertIn("Tracker post (your story FX-3): Started (lane-2): S2: Thing two", out)
        self.assertIn("lane-2 started it: now building", out)
        self.assertEqual(self.stage(fx), "building")
        self.assertFalse((fx.tmp / "flow-log/FX-1.lane-prints.jsonl").exists(), "no log of prints is kept")

    def test_in_manual_mode_a_re_run_prints_and_changes_nothing(self) -> None:
        fx = self.delivery("manual")
        self.started(fx)
        before = [(fx.tmp / "record" / name).read_bytes() for name in ("state.json", "journal.jsonl")]
        out = self.started(fx)
        self.assertIn("already started; nothing to do", out)
        self.assertNotIn("Tracker", out)
        self.assertNotIn("now building", out)
        self.assertEqual([(fx.tmp / "record" / name).read_bytes() for name in ("state.json", "journal.jsonl")], before)

    def test_in_publisher_mode_the_publisher_makes_the_move_and_the_story_moves_to_building(self) -> None:
        fx = self.delivery(None)
        out = self.started(fx)
        self.assertNotIn("Tracker move", out)
        self.assertNotIn("Tracker post", out)
        self.assertEqual(self.stage(fx), "building")

    def test_a_reassigned_story_already_in_review_doesnt_move_back(self) -> None:
        fx = self.delivery("manual")
        self.started(fx)
        sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir",
           fx.tmp / "record", "patch", "--writer", "lead", "--version", str(self.record(fx)["state"]["version"]),
           "--element", "planned[subject=FX-3]", "--patch", json.dumps({"stage": "review"}))
        self.assign(fx)
        out = self.started(fx)
        self.assertIn("Tracker move (your story FX-3)", out, "a new assignment is a new start")
        self.assertNotIn("now building", out)
        self.assertEqual(self.stage(fx), "review")

    def test_the_brief_says_who_makes_the_start_move_in_each_mode(self) -> None:
        for updates, said in (("manual", "`lane-start` prints it; make it with your tracker tools."),
                              (None, "the tracker publisher makes it once `lane-start` records your start.")):
            with self.subTest(updates=updates or "publisher"):
                fx = self.delivery(updates, assign=False)
                code, out = fx.flow("lead-brief", cwd=fx.main)
                self.assertEqual(code, 0, out)
                brief = " ".join((fx.main / "docs/lane-brief.md").read_text().split())
                self.assertIn(f'The start move, "Start work": {said}', brief)
                self.assertIn("moves the story from planned to building in the record, so the line map shows it started",
                              brief)

    # Readiness before assignment.

    def test_a_story_offered_its_start_move_is_assigned_with_no_readiness_line(self) -> None:
        fx = self.delivery(assign=False)
        ready = {"state": "backlog", "status": "Ready for Dev", "offeredTransitions": ["Start work", "Close"]}
        self.snapshot(fx, **ready, others=({"subject": "FX-2", **ready},))
        _, out = self.assign(fx)
        self.assertNotIn("readiness", out.lower())
        self.assertNotIn("isn't offered", out)
        self.assertIn("lane-start FX-3", out)
        self.assertEqual(self.doctor(fx)[0], "ok")

    def test_a_story_not_offered_its_start_move_is_refused_and_nothing_is_written(self) -> None:
        fx = self.delivery(assign=False)
        self.snapshot(fx, state="backlog", status="To Be Refined", offeredTransitions=["Refined"])
        record = fx.tmp / "record"
        before = [(record / name).read_bytes() for name in ("state.json", "journal.jsonl")]
        code, out = fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=fx.main)
        self.assertNotEqual(code, 0, out)
        self.assertIn("FX-3 is in To Be Refined: \"Start work\" isn't offered; refine it first", out)
        self.assertIn("--force", out)
        self.assertEqual([(record / name).read_bytes() for name in ("state.json", "journal.jsonl")], before)
        status, detail = self.doctor(fx)
        self.assertEqual(status, "warn", detail)
        self.assertIn("FX-3 is in To Be Refined: \"Start work\" isn't offered; refine it first", detail)
        self.assertEqual([(record / name).read_bytes() for name in ("state.json", "journal.jsonl")], before,
                         "doctor moved something")

    def test_force_assigns_a_story_not_offered_its_start_move_and_still_names_it(self) -> None:
        fx = self.delivery(assign=False)
        self.snapshot(fx, state="backlog", status="To Be Refined", offeredTransitions=["Refined"])
        _, out = self.assign(fx, "--force")
        self.assertIn("FX-3 is in To Be Refined: \"Start work\" isn't offered", out)
        self.assertIn("lane-start FX-3", out)
        self.assertEqual([e["subject"] for e in self.record(fx)["journal"] if e["kind"] == "lane.assigned"], ["FX-3"])

    def test_a_story_with_no_snapshot_is_assigned_and_both_commands_say_readiness_wasnt_checked(self) -> None:
        fx = self.delivery(assign=False)
        line = "FX-3: readiness wasn't checked: there is no tracker snapshot"
        _, out = self.assign(fx)
        self.assertIn(line, out)
        self.assertIn(line, self.doctor(fx)[1])

    def test_a_snapshot_without_offered_moves_says_why_readiness_wasnt_checked(self) -> None:
        fx = self.delivery(assign=False)
        self.snapshot(fx, state="backlog")
        line = "FX-3: readiness wasn't checked: the tracker snapshot doesn't list its offered moves"
        _, out = self.assign(fx)
        self.assertIn(line, out)
        self.assertIn(line, self.doctor(fx)[1])

    def test_a_start_recorded_without_its_stage_move_gets_it_on_the_next_run(self) -> None:
        fx = self.delivery("manual")
        assigned = [e for e in self.record(fx)["journal"] if e["kind"] == "lane.assigned"][-1]["at"]
        starts = fx.tmp / "flow-log/FX-1.lane-starts.jsonl"
        starts.parent.mkdir(parents=True, exist_ok=True)
        # A run that recorded the start and stopped before the record moved: what the next run finds.
        with open(starts, "a") as log:
            log.write(json.dumps({"subject": "FX-3", "lane": "lane-2", "at": assigned}) + "\n")
        out = self.started(fx)
        self.assertIn("lane-2 started it: now building", out)
        self.assertEqual(self.stage(fx), "building")
        self.assertNotIn("already started", out)

    def test_the_newer_of_the_two_kept_snapshots_decides(self) -> None:
        # lead-reconcile's, beside the record, says not ready; serve's says ready. Whichever is newer decides.
        for reconcile_ago, serve_ago, refused in ((0, 60, True), (60, 0, False)):
            with self.subTest(newer="lead-reconcile's" if refused else "serve's"):
                fx = self.delivery(assign=False)
                self.snapshot(fx, path="snapshots/trackerSnapshot.json", ago=reconcile_ago, state="backlog",
                              status="To Be Refined", offeredTransitions=["Refined"])
                self.snapshot(fx, ago=serve_ago, state="backlog", status="Ready for Dev", offeredTransitions=["Start work"])
                code, out = fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=fx.main)
                self.assertEqual(code != 0, refused, out)
                self.assertEqual("FX-3 is in To Be Refined" in out, refused, out)

    def test_only_the_first_start_move_counts(self) -> None:
        fx = self.delivery(transitions={"start": ["Start work", "Complete"]}, assign=False)
        self.snapshot(fx, state="backlog", status="Triage", offeredTransitions=["Complete"])
        code, out = fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=fx.main)
        self.assertNotEqual(code, 0, out)
        self.assertIn("FX-3 is in Triage: \"Start work\" isn't offered; refine it first", out)

    def test_doctor_checks_readiness_before_lead_init(self) -> None:
        fx = self.delivery(init=False)
        self.assertFalse((fx.tmp / "record/state.json").exists())
        self.snapshot(fx, state="backlog", status="To Be Refined", offeredTransitions=["Refined"])
        status, detail = self.doctor(fx)
        self.assertEqual(status, "warn", detail)
        self.assertIn("FX-3 is in To Be Refined: \"Start work\" isn't offered; refine it first", detail)

    def test_with_no_start_move_there_is_no_readiness_to_check(self) -> None:
        fx = self.delivery(transitions={}, assign=False)
        self.snapshot(fx, state="backlog", status="To Be Refined", offeredTransitions=["Refined"])
        _, out = self.assign(fx)
        self.assertNotIn("readiness", out.lower())
        self.assertNotIn("isn't offered", out)
        self.assertEqual(self.doctor(fx), ("ok", "lanes make no start move, so there is no readiness to check"))

    def test_a_snapshot_that_doesnt_list_the_story_says_so(self) -> None:
        fx = self.delivery(assign=False)
        self.snapshot(fx, subject="FX-9", state="backlog", status="To Be Refined", offeredTransitions=["Refined"])
        _, out = self.assign(fx)
        self.assertIn("FX-3: readiness wasn't checked: the tracker snapshot doesn't list it", out)

    def test_a_story_already_under_way_isnt_held_to_its_start_move(self) -> None:
        fx = self.delivery(assign=False)
        self.snapshot(fx, state="started", status="In Review", offeredTransitions=["Done"])
        _, out = self.assign(fx)
        self.assertNotIn("isn't offered", out)
        self.assertNotIn("readiness", out.lower())


# Every setting the tool's Config or setting() reads from the environment, current and earlier names alike. Each one
# set in the test runner's own environment would point the fixtures' commands somewhere other than the fixture.
TOOL_SETTINGS = ("DELIVERY_STATE", "RUN_DELIVERY_BASE", "RUN_DELIVERY_SUPERVISOR", "RUN_DELIVERY_CONFIG",
                 "DELIVERY_FLOW_CONFIG", "RUN_DELIVERY_DELIVERY", "DELIVERY_FLOW_DELIVERY", "RUN_DELIVERY_LOCAL",
                 "DELIVERY_FLOW_LOCAL")
PLANTED_KEY = "PLANTED-9"
PLANTED_MARKER = "planted-live-delivery-7f3c"


def tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under `root` outside git's own folders, by relative path: its bytes and its mtime."""
    found = {}
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts or not path.is_file() or path.is_symlink():
            continue
        found[path.relative_to(root).as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
    return found


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class LiveDeliveryOutOfReachTests(unittest.TestCase):
    """The module's lead commands never reach a live delivery: not the checkout the suite runs from, and not one the
    runner's environment names."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def test_the_cleaned_environment_strips_every_setting_the_tool_reads(self) -> None:
        source = (SKILL / "scripts/run_delivery.py").read_text()
        quoted = r"""\s*["']([A-Z][A-Z0-9_]*)["']"""

        def scan(text: str) -> set[str]:
            """Every way Python reads one: os.environ[...] / .get / .pop / .setdefault, os.getenv, `... in os.environ`,
            and the tool's own setting()."""
            found = set(re.findall(r"os\.environ(?:\.(?:get|pop|setdefault)\(|\[)" + quoted, text))
            found |= set(re.findall(r"os\.getenv\(" + quoted, text))
            found |= set(re.findall(quoted + r"""\s+(?:not\s+)?in\s+os\.environ\b""", text))
            found |= set(re.findall(r"\bsetting\(" + quoted, text))
            return found

        read = scan(source)
        # setting()'s earlier names, from the tool's own table of them.
        legacy = re.search(r"^LEGACY_NAMES = \((.*?)^\)", source, re.MULTILINE | re.DOTALL).group(1)
        read |= {name for pair in re.findall(r"\(" + quoted + r"\s*," + quoted + r"\s*\)", legacy) for name in pair}
        read -= {"PATH"}  # the runner's own, which every case needs
        self.assertLessEqual(read, set(TOOL_SETTINGS), "the tool reads a setting this list doesn't name")
        probe = ("os.getenv('A_ONE')\nos.environ.get(\"A_TWO\")\nos.environ['A_THREE']\nif 'A_FOUR' in os.environ: pass\n"
                 "os.environ.pop(\"A_FIVE\", None)\nsetting('A_SIX')\n")
        self.assertEqual(scan(probe), {"A_ONE", "A_TWO", "A_THREE", "A_FOUR", "A_FIVE", "A_SIX"}, "the scan misses a form of read")
        self.assertEqual(set(TOOL_SETTINGS) - read, set(), "a listed setting the tool no longer reads")
        with mock.patch.dict(os.environ, {name: PLANTED_MARKER for name in TOOL_SETTINGS}):
            cleaned = clean_environment()
        self.assertEqual(sorted(set(TOOL_SETTINGS) & set(cleaned)), [])

    def plant(self) -> tuple[Path, Path, dict[str, str]]:
        """A main checkout with a linked worktree, as a story's checkout is, holding a delivery of its own under
        .agents/deliveries/ and .orchestration/ whose supervisor is running: (main, worktree, settings naming it)."""
        folder = Path(tempfile.mkdtemp(prefix="run-delivery-planted-"))
        self.addCleanup(shutil.rmtree, folder, True)
        main = folder / "main"
        main.mkdir()
        sh("git", "init", "-q", "-b", "main", cwd=main)
        quieten(main)
        (main / ".gitignore").write_text(".orchestration/\nlanes/\n")
        (main / ".agents/deliveries").mkdir(parents=True)
        (main / ".agents/run-delivery.json").write_text(json.dumps({
            "schemaVersion": "delivery-flow/1", "branchPattern": "claude/{key}", "laneCheckout": ".orchestration/lanes/{key}",
            "tracker": {"issueUrl": f"https://{PLANTED_MARKER}.test/{{key}}"}}, indent=2) + "\n")
        (main / f".agents/deliveries/{PLANTED_KEY}.json").write_text(json.dumps({
            "schemaVersion": "delivery-flow-delivery/1", "delivery": PLANTED_MARKER, "baseBranch": "main",
            "plan": f"docs/{PLANTED_MARKER}.md", "lanes": {"lane-1": {"owns": ["*"]}},
            "stories": {"PLANTED-10": {"lane": "lane-1", "title": PLANTED_MARKER}}}, indent=2) + "\n")
        commit(main, "a live delivery")
        worktree = folder / "story"
        sh("git", "worktree", "add", "-q", "-b", "claude/PLANTED-10", worktree, cwd=main)
        live = main / f".orchestration/lanes/{PLANTED_KEY}/.orchestration"
        state = live / "state"
        sh(sys.executable, "-B", self.generation / "skills/orchestrate-work/scripts/delivery_state.py", "--dir", state,
           "init", "--headline", PLANTED_MARKER, env=clean_environment())
        # Its supervisor holds its lock, as a live one does; the pid is one no process has, so nothing can be stopped.
        pid = 4_000_000
        while True:
            try:
                os.kill(pid, 0)
                pid += 1
            except ProcessLookupError:
                break
            except PermissionError:
                pid += 1
        # Under its own key and its delivery's name (the tool's lock path names the delivery), and under the fixture's,
        # which a command that took this state directory would look for.
        (live / "flow-log").mkdir(parents=True)
        for key in (PLANTED_KEY, PLANTED_MARKER, "FX-1"):
            lock = live / f"flow-log/{key}.supervisor.lock"
            lock.write_text(json.dumps({"pid": pid, "address": f"http://{PLANTED_MARKER}.test/"}))
            holder = open(lock, "a+")
            fcntl.flock(holder, fcntl.LOCK_EX)
            self.addCleanup(holder.close)
        settings = {"DELIVERY_STATE": str(state), "RUN_DELIVERY_BASE": "main",
                    "RUN_DELIVERY_CONFIG": str(main / ".agents/run-delivery.json"),
                    "DELIVERY_FLOW_CONFIG": str(main / ".agents/run-delivery.json"),
                    "RUN_DELIVERY_DELIVERY": PLANTED_KEY, "DELIVERY_FLOW_DELIVERY": PLANTED_KEY,
                    "RUN_DELIVERY_LOCAL": "1", "DELIVERY_FLOW_LOCAL": "1", "RUN_DELIVERY_SUPERVISOR": "on"}
        self.assertEqual(set(settings), set(TOOL_SETTINGS))
        return main, worktree, settings

    def test_a_supervisor_pass_keeps_its_copy_of_the_record_inside_the_case(self) -> None:
        """The pass's `latest` copy lands under the case's folder, never under the runner's HOME."""
        home = Path(tempfile.mkdtemp(prefix="run-delivery-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        identity = {f"GIT_{who}_{what}": value for who in ("AUTHOR", "COMMITTER")
                    for what, value in (("NAME", "Runner"), ("EMAIL", "runner@example.invalid"))}
        with mock.patch.dict(os.environ, {"HOME": str(home), **identity}):
            fx = Fixture(self.generation, self.receipt)
            self.addCleanup(fx.close)
            setup_delivery(fx)
            for argv in (["lead-init"], ["lead-run", "--once"]):
                code, out = fx.flow(*argv, cwd=fx.main)
                self.assertEqual(code, 0, out)
        self.assertEqual(sorted(str(p.relative_to(home)) for p in home.rglob("*")), [],
                         "a lead command wrote under the runner's HOME")
        self.assertTrue((fx.tmp / "archive/FX-1/latest/record/state.json").is_file(), "the pass made no latest copy")

    def test_the_lead_commands_run_from_a_story_checkout_never_reach_its_live_delivery(self) -> None:
        main, worktree, settings = self.plant()
        before = tree_snapshot(main)
        self.assertIn(f".orchestration/lanes/{PLANTED_KEY}/.orchestration/state/state.json", before)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(worktree)  # where the suite runs from: a story's own checkout of the live repository
        # In the runner's own environment before the fixture is built, never passed to a command with env=.
        with mock.patch.dict(os.environ, settings):
            fx = Fixture(self.generation, self.receipt)
            self.addCleanup(fx.close)
            m = fx.main
            outputs = {}
            setup_delivery(fx)
            for argv in (["lead-init"], ["lead-brief"], ["lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane 1"],
                         ["lead-reconcile"], ["lead-run", "--once"]):
                code, out = fx.flow(*argv, cwd=m)
                outputs[argv[0]] = out
                with self.subTest(command=argv[0]):
                    self.assertEqual(code, 0, out)  # each ran its whole course, not refused before it looked
                    self.assertNotIn(PLANTED_MARKER, out)
                    self.assertNotIn(str(main), out)
            self.assertIn(f"initialized {fx.tmp / 'record'}", outputs["lead-init"])
            self.assertTrue((fx.tmp / "record/state.json").is_file())
            # Nor copied: no file the commands left in the case's folder carries the planted delivery's marker.
            copied = [path for path in fx.tmp.rglob("*") if path.is_file() and not path.is_symlink()
                      and ".git" not in path.relative_to(fx.tmp).parts
                      and PLANTED_MARKER.encode() in path.read_bytes()]
            self.assertEqual(copied, [], "a lead command copied the planted delivery into the case")
        self.assertEqual(tree_snapshot(main), before, "a lead command changed the live delivery's files")


# Folders no in-process call may touch while watched, each with what touched it: (resolved root, [event path, ...]).
_CONTACT_WATCH: list[tuple[str, list[str]]] = []
_CONTACT_AUDITING: list[bool] = []
_CONTACT_BUSY = threading.local()
# Each audit event that names a path, and which of its arguments are paths.
_CONTACT_EVENTS = {"open": (0,), "os.listdir": (0,), "os.scandir": (0,), "os.chdir": (0,), "os.mkdir": (0,),
                   "os.remove": (0,), "os.rmdir": (0,), "os.rename": (0, 1), "os.truncate": (0,), "os.utime": (0,),
                   "os.chmod": (0,), "os.symlink": (0, 1), "os.link": (0, 1), "shutil.rmtree": (0,),
                   "shutil.copyfile": (0, 1), "shutil.move": (0, 1), "fcntl.flock": ()}


def _contact_audit(event: str, args: tuple) -> None:
    if not _CONTACT_WATCH or getattr(_CONTACT_BUSY, "on", False):
        return
    if event == "subprocess.Popen":
        _executable, argv, cwd, _env = args
        # A command run without a cwd runs where the process is; of its arguments, those that name a path.
        named = [arg for arg in (argv if isinstance(argv, (list, tuple)) else [argv])
                 if isinstance(arg, (str, bytes, os.PathLike)) and os.sep in os.fsdecode(arg)]
        paths = [cwd if cwd is not None else os.getcwd(), *named]
    elif event in _CONTACT_EVENTS:
        paths = [args[i] for i in _CONTACT_EVENTS[event] if i < len(args)]
    else:
        return
    _CONTACT_BUSY.on = True
    try:
        for path in paths:
            if path is None or isinstance(path, int):
                continue
            try:
                text = os.fsdecode(path)
            except TypeError:
                continue
            real = os.path.realpath(os.path.join(os.getcwd(), text))
            for root, seen in _CONTACT_WATCH:
                if real == root or real.startswith(root + os.sep):
                    seen.append(f"{event} {text}")
    finally:
        _CONTACT_BUSY.on = False


@contextlib.contextmanager
def watching_contact(root: Path):
    """Record every file opened, folder listed, file changed or command run under `root` while it is watched, by this
    process's audit events: yields the list, one `<event> <path>` per contact."""
    if not _CONTACT_AUDITING:
        sys.addaudithook(_contact_audit)  # an audit hook stays for the process's life, so one serves every watch
        _CONTACT_AUDITING.append(True)
    watch = (os.path.realpath(root), [])
    real_rmtree = shutil.rmtree

    def rmtree(path, *args, **kwargs):
        # Counted once, by the folder it removes: its own steps name entries relative to a folder it holds open,
        # which would otherwise read as relative to the working directory.
        _contact_audit("shutil.rmtree", (path, None))
        busy, _CONTACT_BUSY.on = getattr(_CONTACT_BUSY, "on", False), True
        try:
            return real_rmtree(path, *args, **kwargs)
        finally:
            _CONTACT_BUSY.on = busy

    _CONTACT_WATCH.append(watch)
    try:
        with mock.patch.object(shutil, "rmtree", rmtree):
            yield watch[1]
    finally:
        _CONTACT_WATCH.remove(watch)


@contextlib.contextmanager
def without_tool_settings():
    """The test process's own environment with none of the settings the tool reads, put back afterwards."""
    with mock.patch.dict(os.environ):
        for name in TOOL_SETTINGS:
            os.environ.pop(name, None)
        yield


def fresh_tool(name: str) -> types.ModuleType:
    """A copy of run_delivery.py of this case's own, loaded without writing bytecode: no global another case set."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills/run-delivery/scripts/run_delivery.py")
    tool = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(tool)
    finally:
        sys.dont_write_bytecode = previous
    return tool


def stop_and_reap(proc: subprocess.Popen) -> None:
    """Stop a process this case started in a session of its own, by the pid its Popen recorded (so its whole group),
    and wait for it, so it neither runs on nor is left unreaped."""
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def alive_pid(pid: int) -> bool:
    """Whether a process with this pid exists. A stopped leader's orphaned child is reaped by init, so a caller polls
    for a bounded while rather than reading once."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def unreaped_cleanups(source: str) -> list[str]:
    """Each `addCleanup(lambda: ...)` in `source` that stops a process the module started with `subprocess.Popen`
    without waiting for it, which leaves it unreaped: `<line> <lambda>`. A pid the case only recorded (a detached
    supervisor's, say) is no child of the case's and can't be waited for, so stopping it by that pid is enough."""
    tree = ast.parse(source)
    started = {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
               and node.value.func.attr == "Popen" for target in node.targets if isinstance(target, ast.Name)}
    stops = {"kill", "killpg", "terminate", "send_signal"}
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "addCleanup"
                and node.args and isinstance(node.args[0], ast.Lambda)):
            continue
        body = node.args[0]
        calls = [call for call in ast.walk(body) if isinstance(call, ast.Call)]
        named = lambda call: call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", None)
        procs = {n.id for n in ast.walk(body) if isinstance(n, ast.Name)} & started
        waited = {call.func.value.id for call in calls if named(call) in ("wait", "communicate")
                  and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)}
        waited |= {arg.id for call in calls if named(call) == "stop_and_reap" for arg in call.args[:1]
                   if isinstance(arg, ast.Name)}
        if procs - waited and any(named(call) in stops for call in calls):
            found.append(f"{node.lineno} {ast.unparse(body)}")
    return found


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class InProcessCallsStayInTheirCaseTests(unittest.TestCase):
    """A call into the tool's main() in the test process resolves its repository, delivery and record inside its own
    case: never from the checkout the suite runs from, never from a configuration an earlier call left, and never by
    touching a live record or lock. And every process a case starts is waited for, so none is left running or
    unreaped."""

    plant = LiveDeliveryOutOfReachTests.plant

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def case_folder(self) -> Path:
        folder = Path(tempfile.mkdtemp(prefix="run-delivery-in-process-"))
        self.addCleanup(shutil.rmtree, folder, True)
        return folder

    def status_lines(self, out: str) -> list[str]:
        return [line for line in out.splitlines() if line.startswith("supervisor:") or PLANTED_MARKER in line]

    def test_the_in_process_calls_run_from_a_live_checkout_never_reach_its_delivery(self) -> None:
        main, worktree, settings = self.plant()
        before = tree_snapshot(main)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(worktree)  # where the suite runs from: a story's own checkout of the live repository
        # Each test run by itself under its class's own setup, never as a suite, whose end would also run this module's
        # cleanups and so remove the release other cases share.
        cases = RunDeliveryArgumentTests
        tests = list(unittest.defaultTestLoader.loadTestsFromTestCase(cases))
        self.assertGreater(len(tests), 10)
        out, result = io.StringIO(), unittest.TestResult()
        with mock.patch.dict(os.environ, settings), watching_contact(main.parent) as contact, \
                contextlib.redirect_stdout(out):
            cases.setUpClass()
            try:
                for test in tests:
                    test.run(result)
            finally:
                cases.tearDownClass()
                cases.doClassCleanups()
        self.assertEqual(result.testsRun, len(tests))
        self.assertTrue(result.wasSuccessful(), [text for _, text in result.failures + result.errors][:3])
        self.assertEqual(self.status_lines(out.getvalue()), [], "an in-process call printed the live supervisor's status")
        # Only a case's cleanup going back to where the runner was is no contact by a call.
        back = f"os.chdir {os.path.realpath(worktree)}"
        touched = sorted({entry for entry in contact if f"{entry.split()[0]} {os.path.realpath(entry.split(' ', 1)[1])}" != back})
        self.assertEqual(touched, [], "an in-process call touched the live checkout")
        self.assertEqual(tree_snapshot(main), before, "an in-process call changed the live delivery's files")

    def test_a_configuration_an_earlier_call_left_is_never_the_next_calls(self) -> None:
        main, worktree, _ = self.plant()
        tool = fresh_tool("run_delivery_left_configured")
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(worktree)
        with without_tool_settings():
            left = tool.Config(worktree)  # what an earlier call, run from the live checkout, leaves cached
        self.assertTrue((left.state / "state.json").is_file(), "the earlier call didn't resolve the live record")
        tool.CFG, tool.DELIVERY_ARG = left, None
        os.chdir(self.case_folder())  # this call's own case, which holds no repository
        out = io.StringIO()
        with without_tool_settings(), mock.patch.object(tool, "lead_plan", lambda args: None), \
                mock.patch.object(tool.signal, "signal"), watching_contact(main.parent) as contact, \
                contextlib.redirect_stdout(out):
            tool.main(["--delivery", "K", "lead-plan", "why", "c1", "--why", "x"])
        self.assertEqual(self.status_lines(out.getvalue()), [], "the call reported the earlier call's delivery")
        self.assertEqual(contact, [], "the call touched the delivery an earlier call left configured")

    def test_a_configuration_resolves_its_main_checkout_and_delivery_from_its_own_root(self) -> None:
        main, worktree, _ = self.plant()
        case = self.case_folder() / "case"
        case.mkdir()
        sh("git", "init", "-q", "-b", "main", cwd=case)
        quieten(case)
        (case / ".gitignore").write_text(".orchestration/\n")
        (case / ".agents/deliveries").mkdir(parents=True)
        (case / ".agents/run-delivery.json").write_text(json.dumps({
            "schemaVersion": "delivery-flow/1", "branchPattern": "claude/{key}", "laneCheckout": ".orchestration/lanes/{key}",
            "tracker": {"issueUrl": "https://case.test/{key}"}}, indent=2) + "\n")
        for key in ("CASE-1", "CASE-5"):
            (case / f".agents/deliveries/{key}.json").write_text(json.dumps({
                "schemaVersion": "delivery-flow-delivery/1", "delivery": key, "baseBranch": "main",
                "plan": f"docs/{key}.md", "lanes": {"lane-1": {"owns": ["*"]}},
                "stories": {f"{key}0": {"lane": "lane-1", "title": key}}}, indent=2) + "\n")
        commit(case, "two deliveries")
        sh("git", "checkout", "-q", "-b", "claude/CASE-10", cwd=case)  # a story of CASE-1, so the branch picks it
        tool = fresh_tool("run_delivery_rooted")
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(worktree)  # the process runs from the live checkout; the configuration is for the case
        with without_tool_settings(), watching_contact(main.parent) as contact:
            c = tool.Config(case)
        self.assertEqual(Path(c.main).resolve(), case.resolve(), "the main checkout came from the process's checkout")
        self.assertEqual(c.delivery, "CASE-1", "the delivery was picked by the process's branch")
        self.assertTrue(Path(c.state).resolve().is_relative_to(case.resolve()), c.state)
        self.assertEqual(contact, [], "resolving the case's configuration touched the live checkout")

    def test_the_contact_watch_sees_a_read_a_lock_and_a_command_under_its_folder(self) -> None:
        folder = self.case_folder()
        (folder / "file").write_text("x")
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        with watching_contact(folder) as contact:
            (folder / "file").read_text()
            with open(folder / "file", "a+") as probe:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe, fcntl.LOCK_UN)
            os.chdir(folder)
            subprocess.run(["true"], check=True)
            os.chdir(here)
        self.assertEqual([entry.split()[0] for entry in contact],
                         ["open", "open", "os.chdir", "subprocess.Popen"], contact)
        elsewhere = self.case_folder()
        (elsewhere / "inner").mkdir()
        (elsewhere / "inner/file").write_text("x")
        (folder / "inner").mkdir()
        (folder / "inner/file").write_text("x")
        with watching_contact(folder) as contact:
            subprocess.run(["true"], check=True)
            shutil.rmtree(elsewhere / "inner")
        self.assertEqual(contact, [], "a command run, or a folder removed, elsewhere counted")
        with watching_contact(folder) as contact:
            shutil.rmtree(folder / "inner")
        self.assertEqual([entry.split()[0] for entry in contact], ["shutil.rmtree"], contact)

    def test_every_cleanup_that_stops_a_process_also_waits_for_it(self) -> None:
        for module in ("test_run_delivery.py", "test_run_delivery_supervisor.py"):
            with self.subTest(module=module):
                self.assertEqual(unreaped_cleanups((ROOT / "tests" / module).read_text()), [])
        probe = "".join(f"{name} = subprocess.Popen(['sleep', '60'])\n" for name in "abcde") + (
            "self.addCleanup(lambda: a.poll() is None and os.killpg(a.pid, signal.SIGKILL))\n"
            "self.addCleanup(lambda: b.poll() is None and b.kill())\n"
            "self.addCleanup(lambda: c.poll() is None and (os.killpg(c.pid, signal.SIGKILL), c.wait()))\n"
            "self.addCleanup(lambda: stop_and_reap(d))\n"
            "self.addCleanup(lambda: e.poll() is None and (e.kill(), a.wait()))\n"
            "self.addCleanup(lambda: alive(pid) and os.kill(pid, signal.SIGTERM))\n")
        self.assertEqual([line.split()[0] for line in unreaped_cleanups(probe)], ["6", "7", "10"],
                         "the scan misses a form, or flags a recorded pid it can't wait for")

    def test_a_lease_given_up_closes_its_lock_file(self) -> None:
        tool = fresh_tool("run_delivery_lease")
        lock = self.case_folder() / "app.lock"
        holder = open(lock, "a+")
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX)  # held through another open file, as a live check holds it
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            try:
                tool.Lease(lock, 0, "lead-close", 0)
            except tool.Stop as held:
                self.assertIn("the in-app lease is held", str(held))
            else:
                self.fail("took a held lease")
            gc.collect()
        self.assertEqual([str(w.message) for w in caught if issubclass(w.category, ResourceWarning)], [],
                         "the lease it gave up left its lock file open")

    def test_stopping_and_reaping_stops_the_whole_group_the_process_leads(self) -> None:
        # A leader with a child in its session, as `lead-run --once` starts its pass's own: stopping only the leader
        # would leave the child running after the case.
        leader = subprocess.Popen(["sh", "-c", "sleep 60 & echo $!; wait"], stdout=subprocess.PIPE, text=True,
                                  start_new_session=True)
        self.addCleanup(stop_and_reap, leader)  # on any way out, as a case's cleanup does
        self.addCleanup(leader.stdout.close)
        child = int(leader.stdout.readline())
        self.addCleanup(lambda: alive_pid(child) and os.kill(child, signal.SIGKILL))
        self.assertEqual(os.getpgid(child), leader.pid, "the child isn't in the leader's group")
        self.assertTrue(alive_pid(child), "alive_pid can't see the running child")
        stop_and_reap(leader)
        deadline = time.monotonic() + 5
        while alive_pid(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive_pid(child), "the leader's child outlived stop_and_reap")

    def test_a_stopped_and_reaped_process_is_gone_and_leaves_no_warning(self) -> None:
        def released(reap: bool) -> list[str]:
            proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
            if reap:
                started = time.monotonic()
                stop_and_reap(proc)
                # Stopped, not waited out: it ends by the signal, long before its own minute is up.
                self.assertLess(time.monotonic() - started, 5)
                self.assertEqual(proc.returncode, -signal.SIGKILL)
            else:  # the form that warned: stopped, never waited for
                os.killpg(proc.pid, signal.SIGKILL)
                time.sleep(0.2)
            pid = proc.pid
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                del proc
                gc.collect()
            if not reap:  # the control's own zombie, reaped by the pid it had unless subprocess already did
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(pid, 0)
            return [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]

        self.assertEqual(released(reap=True), [])
        self.assertTrue(any("is still running" in message for message in released(reap=False)), "the control didn't warn")


if __name__ == "__main__":
    unittest.main()
