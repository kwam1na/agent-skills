#!/usr/bin/env python3
"""run-delivery: the mechanical steps of an agent-skills delivery (orchestrate-work), as commands, for any
repository and any delivery.

Lanes and the lead spend their attention on the work, not on hand-assembling lens briefs, round files, records
and evidence. The tool wraps the repository's installed agent-skills release (its state tool, review validator
and lens charters), found in the installed generation it runs from. It reads the repository's settings from
`.agents/run-delivery.json` and each delivery's from
`.agents/deliveries/<key>.json` (references/config.md). Forges and in-app checks are reached through argv adapters.

Lane:
  lane-start     <key> [--base B]
  review-prepare <key> --round N [--grace] [--restart]
  review-reduce  <key> --round N
  gate           <key>
  build          <key>
  app-check      <key> <name> --mode hold|fail|loaded --path P [spec flags] [--baseline | --probe] [-- adapter args]
  open-pr        <key> --title T --body-file F
  handoff        <key> --pr URL [--findings TEXT] [--blocked LABEL=REASON ...]
Lead:
  lead-setup <key> --plan P --base B --pattern RE ... | lead-init | lead-brief | lead-assign <key...> --lane L --title T
  lead-accept <key> [--accept-blocked LABEL ...] [--accept-restarts] | ship | adapter-test
  lead-carry-review <key> --from HEAD --to HEAD --reason TEXT
Merge queue:
  pr-review-current --branch B --head H [--wait]   (a reviewGate: exit 0 only for a current, passing review.pr review)
Anyone:
  view [--archive PATH] [--port N]

Run with python3 -B from anywhere inside a checkout of the delivery base or a lane branch. With several deliveries
in the repository, name one with --delivery KEY (or RUN_DELIVERY_DELIVERY); otherwise the current branch picks it.
"""

from __future__ import annotations

import os
import sys

if os.name == "nt":
    print("run-delivery runs on POSIX hosts only (it uses fcntl, process groups and sh); this host is Windows",
          file=sys.stderr)
    sys.exit(2)

import argparse
import contextlib
import datetime
import fcntl
import collections
import fnmatch
import hashlib
import json
import re
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import warnings
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_review_gates  # noqa: E402
import tracker_publisher  # noqa: E402

TOOL = Path(__file__).resolve().parent.parent
# The installed generation this tool runs from, through any projection link: the folder three above scripts/.
GENERATION = Path(__file__).resolve().parents[3]
RECEIPT = ".agent-skills/active.json"
# How briefs and printed commands name this tool: a stable path every checkout of the delivery base resolves.
TOOL_PATH = ".agent-skills/current/skills/run-delivery/scripts/run_delivery.py"
CONFIG = ".agents/run-delivery.json"
# Earlier names, still honoured when the new one is absent: (old, new).
LEGACY_NAMES = (
    (".agents/flow.json", CONFIG),
    ("DELIVERY_FLOW_CONFIG", "RUN_DELIVERY_CONFIG"),
    ("DELIVERY_FLOW_DELIVERY", "RUN_DELIVERY_DELIVERY"),
    ("DELIVERY_FLOW_LOCAL", "RUN_DELIVERY_LOCAL"),
)
LEGACY_USED: list[tuple[str, str]] = []
# Set by `doctor --offline`: read the local remote-tracking refs without fetching.
OFFLINE = False
# Process names lead-close recognizes as this tool, the earlier name included.
TOOL_NAMES = ("run_delivery.py", "flow.py")
# The commands each shipped forge adapter answers; an adopter's own argv adapter is taken to answer all four.
ADAPTER_COMMANDS = {name: ("find", "open", "get", "reviews", "ready", "merge") for name in ("bitbucket", "github")}
DEFAULT_ARCHIVE = "~/.local/share/run-delivery/archive/{repo}/{delivery}"
# Adapter arguments that would carry a sign-in value into records, logs and evidence (flag name or assignment key).
SECRET_ARGUMENT = re.compile(r"password|passwd|token|secret|api[-_]?key|credential", re.IGNORECASE)
SEVERITIES = ("P0", "P1", "P2", "P3")
CLOSED = ("closed", "fixed", "withdrawn", "dropped", "deferred")
CONTEXT_TODO = "TODO: replace this line with your evidence so far"
# The round brief's severity rules: a story's review defers P2 and P3; a plan review has no deferral path.
# The out-of-delta rule, in obtain-review's words, which both kinds of brief carry as obtain-review states it.
OUT_OF_DELTA = (
    "    In a verification round, for delivery review, a new finding outside the delta is filed only at P0, and a P1\n"
    "    becomes a deferral with a follow-up item; for plan review (`unbounded`), it is filed at P0 or P1, with no\n"
    "    deferral.\n")
STORY_SEVERITIES = (
    "  - P0 and P1 are actionable, and a P0 is never deferred.\n" + OUT_OF_DELTA +
    "  - P2 and P3 are deferrable: file each with `\"state\": \"deferred\"` and `\"deferredIssue\": \"<what its follow-up item\n"
    "    must say>\"`, and so is that P1, which also lists its id in `lateFindings`. A deferred finding doesn't block\n"
    "    `aligned`.")
PLAN_SEVERITIES = (
    "  - P0 and P1 are actionable, and a plan review never defers them.\n" + OUT_OF_DELTA +
    "  - P2 and P3 notes belong in `evidence`, never in `findings`: a plan review has no deferral path, so a result\n"
    "    carries no `state` or `deferredIssue` on any finding.")
STAMP = ".flow-build.json"
EVIDENCE_FINISH = {"id": "evidence-page", "text": "The delivery's evidence page is published and current: every merged "
                   "story marked for video has its before/after clip on it, and the epic and stories link to it"}
SPEC_FLAGS = ("--path", "--hold", "--fail", "--eval", "--after-eval", "--click", "--for", "--wait", "--after-for", "--spec")
DEFAULT_TEST_FILES = r"(^|/)(tests?|__tests__)/|\.(spec|test)\.[^/]+$"


class Stop(Exception):
    pass


class Transient(Stop):
    """An adapter's transient failure: it exited 75 (EX_TEMPFAIL) for a timeout, a connection error, or an HTTP 5xx or
    429. A PR-review wait's loop tries it again; everywhere else it refuses at once, as any Stop does."""


def _terminate(signum, _frame):
    # A kill from the agent's tool timeout becomes an exception, so `finally` blocks stop processes and leases.
    raise SystemExit(128 + signum)




# -- process and git ----------------------------------------------------------------------

def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def run(argv: list[str], cwd: Path | None = None, check: bool = True, **kw) -> subprocess.CompletedProcess:
    done = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, **kw)
    if check and done.returncode != 0:
        raise Stop(f"{' '.join(map(str, argv[:4]))} ... failed: {(done.stderr or done.stdout).strip()[-600:]}")
    return done


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    return run(["git", *args], cwd=cwd, check=check).stdout.strip()


def repo() -> Path:
    return Path(git("rev-parse", "--show-toplevel"))


def main_checkout(cwd: Path | None = None) -> Path:
    """The main checkout of the repository at `cwd`, or at the working directory when no folder is given."""
    return Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd)).parent


def clean_head(cwd: Path | None = None) -> str:
    """HEAD, refusing uncommitted or untracked files: an untracked file would be tested but never reviewed."""
    dirty = git("status", "--porcelain", "--untracked-files=all", cwd=cwd)
    if dirty:
        raise Stop("the checkout has uncommitted or untracked files; commit or remove them first:\n" + dirty[:800])
    return git("rev-parse", "HEAD", cwd=cwd)


def expand(value: str, **values: str) -> str:
    """`{name}` placeholders and a leading `~`."""
    out = re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), value)
    return os.path.expanduser(out)


def expand_argv(argv: list[str], **values) -> list[str]:
    result = []
    for item in argv:
        if item == "{args}":
            result += list(values.get("args", []))
        else:
            result.append(expand(item, **{k: v for k, v in values.items() if k != "args"}))
    return result


def setting(name: str) -> str | None:
    """An environment setting by its name, or by its earlier name when the new one is unset."""
    old = next(o for o, n in LEGACY_NAMES if n == name)
    if os.environ.get(name):
        return os.environ[name]
    if os.environ.get(old):
        if (old, name) not in LEGACY_USED:
            LEGACY_USED.append((old, name))
        return os.environ[old]
    return None


def config_rel(shelf: "Shelf") -> str:
    """The repository's configuration file: .agents/run-delivery.json, or the earlier .agents/flow.json without it."""
    if shelf.text(CONFIG) is None and shelf.text(LEGACY_NAMES[0][0]) is not None:
        if LEGACY_NAMES[0] not in LEGACY_USED:
            LEGACY_USED.append(LEGACY_NAMES[0])
        return LEGACY_NAMES[0][0]
    return CONFIG


def stop_group(proc: subprocess.Popen | None) -> None:
    """Stop a process started with start_new_session=True, and wait until it has exited (SIGKILL after 10s)."""
    if not proc:
        return
    if proc.poll() is not None:  # the leader has gone, but its group may not have: signal what's left
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        return
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def run_group(argv: list[str], cwd: Path, timeout: int, capture: bool = True,
              started: Callable[[int], None] | None = None) -> tuple[int, str]:
    """Run a command in its own process group; on timeout or interruption the whole group is stopped. `started` is
    told the group's pid once it runs."""
    proc = subprocess.Popen(argv, cwd=cwd, text=True, start_new_session=True,
                            stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if capture else None)
    try:
        if started:
            started(proc.pid)
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        raise Stop(f"{' '.join(argv[:3])} ... took longer than {timeout}s and was stopped")
    finally:
        stop_group(proc)


# -- configuration ------------------------------------------------------------------------

def load_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


# Where a refusal of a base the binding doesn't serve sends the reader.
BASE_MISMATCH = "the adoption guide, references/adopting.md#base-mismatch"
# Where a lane's checkout records the base it was started on, inside its own git directory.
LANE_BASE = "run-delivery/lane.json"


def serves(binding: dict, base: str) -> bool:
    """Whether the binding serves `base`: its base.ref, or a name or `*` pattern in its bases, where `*` matches any
    run of characters within one segment of the branch name, never a `/`. The merge queue applies the same rule."""
    names = [binding.get("base", {}).get("ref"), *binding.get("bases", [])]
    return any(name and re.fullmatch("[^/]*".join(map(re.escape, name.split("*"))), base) for name in names)


def unserved(binding: dict, base: str) -> Stop:
    """The refusal of a base the binding doesn't serve."""
    names = ", ".join(filter(None, [binding.get("base", {}).get("ref"), *binding.get("bases", [])]))
    return Stop(f"the binding doesn't serve the base {base}: it serves {names}; add {base} to the binding's bases, "
                f"or deliver to a base it serves (see {BASE_MISMATCH})")


def lane_base_file(root: Path) -> Path:
    return root / git("rev-parse", "--git-path", LANE_BASE, cwd=root)


def record_lane_base(root: Path, base: str) -> None:
    """Record in a lane's checkout the base it was started on, which its settings are then read from."""
    path = lane_base_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"base": base}, indent=2) + "\n")


def lane_base(root: Path) -> str | None:
    """The base a lane names: RUN_DELIVERY_BASE, or the one its checkout recorded when it was started."""
    return os.environ.get("RUN_DELIVERY_BASE") or (load_json(lane_base_file(root)) or {}).get("base")


class Shelf:
    """Where the delivery's shared files are read from: the own config, the binding, delivery-views.json, the delivery
    file, the plan and the brief notes. The lead's checkout, on the delivery base, reads its own working tree. A lane
    reads them from the base on the remote, so what the lead pushes reaches every lane without a merge, and a lane
    never has to move its reviewed head to pick up a tooling change."""

    def __init__(self, root: Path, ref: str | None = None, base: str | None = None, named: bool = False):
        # base: the branch ref is on; named: the lane named it, rather than taking the binding's base.ref.
        self.root, self.ref, self.base, self.named = root, ref, base, named

    def text(self, rel: str | Path) -> str | None:
        if self.ref:
            done = run(["git", "show", f"{self.ref}:{Path(rel).as_posix()}"], cwd=self.root, check=False)
            return done.stdout if done.returncode == 0 else None
        path = self.root / rel
        return path.read_text() if path.exists() else None

    def json(self, rel: str | Path) -> dict | None:
        text = self.text(rel)
        return json.loads(text) if text is not None else None

    def names(self, folder: str | Path, suffix: str) -> list[str]:
        if self.ref:
            done = run(["git", "ls-tree", "--name-only", f"{self.ref}:{Path(folder).as_posix()}"], cwd=self.root, check=False)
            found = done.stdout.split() if done.returncode == 0 else []
        else:
            path = self.root / folder
            found = [f.name for f in path.iterdir()] if path.is_dir() else []
        return sorted(n for n in found if n.endswith(suffix))

    def where(self, rel: str | Path) -> str:
        return f"{self.ref}:{Path(rel).as_posix()}" if self.ref else str(self.root / rel)


def shelf_for(root: Path) -> Shelf:
    """The lead (on a base the binding serves) and explicit overrides read the working tree; a lane reads its base
    on the remote: the one it names (lane_base), checked against the binding, or else the binding's base.ref."""
    binding = load_json(root / ".agents/orchestration.json") or {}
    own = load_json(root / config_rel(Shelf(root))) or {}
    base = binding.get("base", {}).get("ref")
    remote = binding.get("base", {}).get("remote") or own.get("remote") or "origin"
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root, check=False)
    if not base or setting("RUN_DELIVERY_LOCAL") == "1":
        return Shelf(root)
    named = lane_base(root)
    if named and not serves(binding, named):
        raise unserved(binding, named)
    if branch == base or (binding.get("bases") and serves(binding, branch)):
        return Shelf(root)
    base = named or base
    ref = f"{remote}/{base}"
    if not OFFLINE:
        run(["git", "fetch", "-q", remote, base], cwd=root, check=False)
    if run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=root, check=False).returncode != 0:
        return Shelf(root)  # no such ref yet (a fresh repository): nothing shared to read
    return Shelf(root, ref, base, bool(named))


def node_versions() -> list[tuple[str, tuple[int, ...]]]:
    """Each Node the tool may run, with its version: the one on PATH, then nvm's, newest first."""
    candidates = [shutil.which("node")] + sorted(str(p) for p in Path.home().glob(".nvm/versions/node/v*/bin/node"))[::-1]
    found = []
    for node in filter(None, candidates):
        done = subprocess.run([node, "--version"], text=True, capture_output=True)
        m = re.match(r"v(\d+)\.(\d+)\.(\d+)", done.stdout.strip())
        if m:
            found.append((node, tuple(int(n) for n in m.groups())))
    return found


def find_node(minimum: tuple[int, ...]) -> str | None:
    return next((node for node, version in node_versions() if version >= minimum), None)


def find_node22() -> str | None:
    return find_node((22, 6))


def node_problem(release: Path) -> tuple[str, str] | None:
    """Why no Node here meets the runtime's nodeMinimum, as (status, detail), or None. A generation without a runtime
    asks for none."""
    wanted = (load_json(release / "runtime/runtime.json") or {}).get("nodeMinimum")
    if not wanted:
        return None
    minimum = tuple(int(n) for n in wanted.split("."))
    versions = node_versions()
    if any(version >= minimum for _, version in versions):
        return None
    fix = f"fix: install Node {wanted} or newer, on PATH or under nvm"
    if not versions:
        return "missing", f"no Node on PATH or under nvm, and the runtime needs Node {wanted}; {fix}"
    seen = ", ".join(sorted({".".join(map(str, version)) for _, version in versions}))
    return "wrong", f"Node {seen} is below the runtime's nodeMinimum {wanted}; {fix}"


def load_harness(root: Path, release: Path) -> tuple[dict | None, str]:
    """harness.config.ts, loaded the way the installed runtime loads it: (settings, how) or (None, why not)."""
    path = root / "harness.config.ts"
    if not path.exists():
        return None, "no harness.config.ts"
    node = find_node22()
    if not node:
        return None, "harness.config.ts not read: it needs Node >= 22.6"
    hook = (release / "runtime/loader.mjs").resolve()
    script = ("import(require('node:url').pathToFileURL(process.argv[1]).href).then(m => { const c = m.default; "
              "console.log(JSON.stringify({baseRef: c.baseRef, preparationCommands: c.preparationCommands || [], "
              "additionalReviewLenses: c.additionalReviewLenses || []})) })")
    register = ('data:text/javascript,import {register} from "node:module"; import {pathToFileURL} from "node:url"; '
                f'register(pathToFileURL({json.dumps(str(hook))}));')
    # Node before 22.18 and 23.6 strips types only behind this flag; later ones accept it unchanged.
    done = subprocess.run([node, "--no-warnings", "--experimental-strip-types", "--import", register, "-e", script, str(path)],
                          text=True, capture_output=True, cwd=root)
    if done.returncode != 0:
        return None, f"harness.config.ts not read: {done.stderr.strip()[-200:]}"
    return json.loads(done.stdout.strip().splitlines()[-1]), "harness.config.ts"


class Config:
    """Settings resolved from what the repository already declares, in this order of precedence:
    the orchestrate-work binding (.agents/orchestration.json: what the merge queue runs), the delivery-views config
    (.agents/delivery-views.json), the harness config (harness.config.ts), then .agents/run-delivery.json for what
    none of them declares, then defaults. One delivery's settings come from .agents/deliveries/<key>.json."""

    def __init__(self, root: Path, delivery: str | None = None, need_delivery: bool = True, tolerant: bool = False,
                 pick: bool = True):
        # Its main checkout and, below, its branch are the root's own, never the working directory's: a configuration
        # built for one repository from inside another resolves that repository's deliveries, and no other's.
        self.root, self.main = root, main_checkout(root)
        self.sources: dict[str, str] = {}
        self.conflicts: list[str] = []
        self.shelf = shelf_for(root)
        explicit = setting("RUN_DELIVERY_CONFIG")
        if explicit:
            self.path = Path(explicit)
            f = load_json(self.path) or {}
        else:
            rel = config_rel(self.shelf)
            self.path = self.shelf.where(rel)
            f = self.shelf.json(rel) or {}
        own = self.name = Path(self.path).name
        # How a brief names it: the path given explicitly, or the repository-relative file that was read.
        self.config_named = explicit or rel
        if f and f.get("schemaVersion") != "delivery-flow/1":
            raise Stop(f"{self.path}: schemaVersion must be delivery-flow/1")
        self.flow_raw = f
        self.binding_path = root / ".agents/orchestration.json"
        b = self.shelf.json(".agents/orchestration.json") or {}
        views = self.shelf.json(".agents/delivery-views.json") or {}
        self.release = root / os.path.expanduser(f["release"]) if f.get("release") else GENERATION
        self.release_override = f.get("release")
        if f.get("harness") == "ignore":
            h, how = None, f"harness.config.ts ignored ({own} says so)"
        else:
            h, how = load_harness(root, self.release)
            if h is None and (root / "harness.config.ts").exists() and not tolerant:
                # Skipping it would drop its lenses and its cross-check without anyone noticing.
                raise Stop(f"{how}; fix that, or set \"harness\": \"ignore\" in {self.path}")
        self.harness_note = how

        def take(name, *options, flow=None, default=None, default_source="default", same=lambda a, b: a == b):
            """The first declared value; a different own-config value under a higher source is a conflict."""
            for value, source in options:
                if value not in (None, [], {}):
                    self.sources[name] = source
                    if flow not in (None, [], {}) and not same(flow, value):
                        self.conflicts.append(f"{own} sets {name} to {flow!r}, but {source} declares {value!r}")
                    return value
            if flow not in (None, [], {}):
                self.sources[name] = own
                return flow
            self.sources[name] = default_source
            return default

        self.remote = take("remote", (b.get("base", {}).get("remote"), "orchestration binding"), flow=f.get("remote"),
                           default="origin")
        self.binding_base = b.get("base", {}).get("ref")
        self.binding, self.bases = b, b.get("bases", [])
        work_root = b.get("workRoot")
        self.work = take("workDirectory", (str(Path(work_root).parent) if work_root else None,
                                           "orchestration binding (workRoot's folder)"),
                         flow=f.get("workDirectory"), default=".orchestration")
        self.state_rel = take("stateDirectory", (b.get("state", {}).get("directory"), "orchestration binding"),
                              default=".orchestration/state")
        self.branch_pattern = take("branchPattern", flow=f.get("branchPattern"), default="feature/{key}")
        self.lane_pattern = take("laneCheckout", flow=f.get("laneCheckout"), default=".orchestration/lanes/{key}")
        self.lane_checkout = lambda key: self.main / expand(self.lane_pattern, key=key)
        self.deliveries_rel = take("deliveries", flow=f.get("deliveries"), default=".agents/deliveries")
        self.deliveries = root / self.deliveries_rel
        self.issue_url = take("tracker.issueUrl", (views.get("tracker", {}).get("issueUrl"), "delivery-views.json"),
                              flow=f.get("tracker", {}).get("issueUrl"), default="{key}")
        derived: dict = {}
        if views.get("forges"):
            first = views["forges"][0]
            derived = {"adapter": first.get("name", "").lower(), "repository": first.get("repository", "")}
            snapshot = b.get("state", {}).get("forgeSnapshot", {}).get("argv", [])
            if "--token-env" in snapshot:
                derived["tokenEnv"] = snapshot[snapshot.index("--token-env") + 1]
        flow_forge = dict(f.get("forge") or {})
        for field in ("adapter", "repository", "tokenEnv"):
            if field in flow_forge and field in derived and flow_forge[field] != derived[field]:
                self.conflicts.append(f"{own} sets forge.{field} to {flow_forge[field]!r}, but delivery-views.json "
                                      f"or the binding declares {derived[field]!r}")
        # The own config may add what nothing else declares (a custom adapter's argv, say) but not contradict.
        self.forge = {**flow_forge, **derived}
        self.sources["forge"] = ("delivery-views.json (forges[0])" + (", token from the binding's forgeSnapshot"
                                 if "tokenEnv" in derived else "") if derived else "") + \
            (f" + {own}" if flow_forge else "") or "none declared"
        review = f.get("review", {})
        self.bound = take("review.bound", flow=review.get("bound"), default=4, default_source="review-work's default")
        # (short name, charter file): the workflow's lenses' charters are the release's personas; extra lenses name a
        # charter file in the repository.
        lenses = {"lens.outcome-correctness": ("outcome-correctness", self.release / "personas/outcome-correctness.md"),
                  "lens.adversarial-testing": ("adversarial-testing", self.release / "personas/testing-policy.md")}
        self.sources["review.lenses"] = "the delivery workflow's two lenses"
        for extra in (h or {}).get("additionalReviewLenses", []):
            lenses[extra["lensId"]] = (extra["lensId"].split(".", 1)[-1], root / extra["charterPath"])
            self.sources["review.lenses"] += f" + {extra['lensId']} (harness.config.ts)"
        for lens, spec in review.get("lenses", {}).items():
            if lens in lenses:
                self.conflicts.append(f"{own} review.lenses redefines {lens}, which is already declared")
            lenses[lens] = (spec["short"], root / spec["charter"])
            self.sources["review.lenses"] += f" + {lens} ({own})"
        for lens, (_, charter) in lenses.items():
            if not charter.exists():
                self.conflicts.append(f"lens {lens}'s charter {charter} doesn't exist")
        self.lenses = lenses
        self.prepare = take("review.prepare", (b.get("prepare"), "orchestration binding"), flow=review.get("prepare"),
                            default=[])
        if b.get("provider") or b.get("alwaysRun") or b.get("profiles"):
            # The merge queue may select checks beyond the fallback list; lanes need them spelled out.
            if not f.get("gates"):
                self.conflicts.append("the binding selects checks with a provider, alwaysRun or profiles; list the "
                                      f"lane gates in {own} `gates`")
            gates = []
        else:
            timeout = b.get("checkTimeoutSeconds", 1800)
            gates = [{"id": g["id"], "argv": [("{base}" if a == "{baseHead}" else a) for a in g["argv"]],
                      "cwd": g.get("cwd", "."), "timeoutSeconds": g.get("timeoutSeconds", timeout)}
                     for g in b.get("fallback", [])]
        flow_gates = f.get("gates")
        if flow_gates == "none" and (b.get("fallback") or b.get("provider") or b.get("alwaysRun") or b.get("profiles")):
            self.conflicts.append(f"{own} says \"gates\": \"none\", but the binding declares checks the merge queue runs")
        if flow_gates == "none":
            self.gates, self.sources["gates"] = [], f"{own} (\"none\": no gates)"
        else:
            self.gates = take("gates", (gates, "orchestration binding (fallback checks: what the merge queue runs)"),
                              flow=flow_gates, default=[],
                              same=lambda a, b: [(g["id"], g["argv"]) for g in a] == [(g["id"], g["argv"]) for g in b])
        self.harness_settings = h
        if not self.bases:  # one base: check the harness config against it now; else against the delivery's base
            self.conflicts += self.harness_conflicts(self.binding_base, "the binding's base")
        self.never_touch = take("neverTouch", flow=f.get("neverTouch"), default=[])
        self.in_app = f.get("inApp")
        self.sources["inApp"] = own if self.in_app else "none declared"
        leases = {lease["id"] for lease in b.get("leases", [])}
        if self.in_app and leases and self.in_app.get("lease") not in leases:
            self.conflicts.append(f"inApp.lease {self.in_app.get('lease')!r} isn't one of the binding's leases {sorted(leases)}")
        self.test_files = (self.in_app or {}).get("testFilePattern", DEFAULT_TEST_FILES)
        if self.conflicts:
            raise Stop("the repository's configs disagree:\n  " + "\n  ".join(self.conflicts))
        self.delivery = None
        key = delivery or setting("RUN_DELIVERY_DELIVERY") or (self.pick_delivery(need_delivery) if pick else None)
        if key:
            self.load_delivery(key)

    def harness_conflicts(self, base: str | None, whose: str) -> list[str]:
        """Where harness.config.ts disagrees with the lane gates or the base: its baseRef and preparationCommands."""
        h, found = self.harness_settings, []
        if not h or not base:
            return found
        base_ref = f"{self.remote}/{base}"
        if h.get("preparationCommands") and self.gates:
            ours = [[base_ref if a == "{base}" else a for a in g["argv"]] for g in self.gates]
            theirs = [list(cmd["command"]) for cmd in h["preparationCommands"]]
            if ours != theirs:
                found.append(f"the lane gates {ours} and harness.config.ts preparationCommands {theirs} disagree")
        if h.get("baseRef") and h["baseRef"] != base_ref:
            found.append(f"harness.config.ts baseRef {h['baseRef']} isn't {whose} {base_ref}")
        return found

    def base_hint(self) -> str:
        """For a lane that read the binding's base.ref by default although the binding serves several bases."""
        if self.bases and self.shelf.ref and not self.shelf.named:
            return (f"; this checkout read {self.shelf.ref}, the binding's base.ref: a lane of a delivery to another "
                    "base names it with RUN_DELIVERY_BASE")
        return ""

    def delivery_keys(self) -> list[str]:
        return [name[:-len(".json")] for name in self.shelf.names(self.deliveries_rel, ".json")]

    def delivery_files(self) -> dict[str, dict]:
        return {key: self.shelf.json(f"{self.deliveries_rel}/{key}.json") or {} for key in self.delivery_keys()}

    def state_of(self, key: str, d: dict) -> Path:
        """Where a delivery's record is kept on this machine, as load_delivery finds it."""
        if os.environ.get("DELIVERY_STATE"):
            return Path(os.environ["DELIVERY_STATE"])
        if d.get("stateDirectory"):
            return self.main / d["stateDirectory"]
        return self.lane_checkout(key) / self.state_rel

    def closed_here(self, key: str, d: dict) -> bool:
        """Whether this machine's flow log for the delivery holds the closed.json lead-close writes."""
        return (self.state_of(key, d).parent / "flow-log" / f"{key}.closed.json").exists()

    def open_here(self, key: str, d: dict) -> bool:
        """Whether the delivery's record is open in this checkout: it is kept under it, and not in a checkout nested
        inside it (a lead's under the lanes folder, say), holds state.json, and isn't closed. A record that names its
        tracker parent must name this delivery."""
        state, root = self.state_of(key, d).resolve(), self.root.resolve()
        try:
            state.relative_to(root)
        except ValueError:
            return False
        if any((folder / ".git").exists() for folder in [state, *state.parents] if folder != root and
               folder.is_relative_to(root)):
            return False
        if not (state / "state.json").is_file() or self.closed_here(key, d):
            return False
        parent = ((load_json(state / "state.json") or {}).get("meta") or {}).get("trackerParent")
        return parent in (None, key)

    def tracker_closed(self, key: str, d: dict) -> bool:
        """Whether the binding's tracker snapshot, run for the delivery's own key, shows its parent completed or
        canceled. Without a tracker snapshot, or when it fails or doesn't say, the delivery isn't known to be closed."""
        asked = self.__dict__.setdefault("tracker_answers", {})  # each delivery's tracker is asked once per command
        if key not in asked:
            asked[key] = self.ask_tracker(key, d)
        return asked[key]

    def ask_tracker(self, key: str, d: dict) -> bool:
        spec = (self.binding.get("state") or {}).get("trackerSnapshot")
        if not spec:
            return False
        argv = expand_argv(spec["argv"], stateDirectory=str(self.state_of(key, d)), parent=key)
        try:
            taken = run(argv, cwd=self.root, check=False, timeout=int(spec.get("timeoutSeconds", 60)))
            parent = json.loads(taken.stdout).get("parent") if taken.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError, ValueError, AttributeError):
            return False
        return (isinstance(parent, dict) and parent.get("subject") == key
                and parent.get("state") in ("completed", "canceled"))

    def not_known_closed(self, files: dict[str, dict]) -> list[str]:
        """The deliveries not known to be closed: no closed.json here, and no tracker snapshot showing the parent
        closed. The tracker is asked whenever there is more than one delivery file."""
        left = [key for key, d in files.items() if not self.closed_here(key, d)]
        if len(files) > 1:
            left = [key for key in left if not self.tracker_closed(key, files[key])]
        return left

    def pick_delivery(self, required: bool) -> str | None:
        """The delivery a command without --delivery is for: the only delivery file; else the one whose record is
        open in this checkout; else the one whose story branch this checkout is on; else the only one not known to be
        closed. Otherwise it refuses, naming each delivery not known to be closed."""
        files = self.delivery_files()
        if len(files) == 1:
            return next(iter(files))
        here = [key for key, d in files.items() if self.open_here(key, d)]
        if len(here) == 1:
            return here[0]
        branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.root, check=False)
        matches = [key for key, d in files.items()
                   if any(branch == self.branch_pattern.format(key=k) for k in d.get("stories", {}))]
        if len(matches) == 1:
            return matches[0]
        left = self.not_known_closed(files)
        if len(left) == 1:
            return left[0]
        if required:
            named = (f"{', '.join(left)} aren't known to be closed, and no record of theirs is open here" if left
                     else f"deliveries {', '.join(files) or 'none'}, none of them open")
            raise Stop(f"can't tell which delivery this is (branch {branch}; {named}); pass --delivery KEY"
                       f"{self.base_hint()}")
        return None

    def missing_delivery(self, key: str) -> str:
        """For a delivery file that is missing: whether the key is a story of a delivery, and which delivery is open."""
        files = self.delivery_files()
        owners = [other for other, d in files.items() if key in (d.get("stories") or {})]
        said = (f"; {key} looks like a story key: it is a story of {', '.join(owners)}" if owners
                else f"; {key} is no story of any delivery here either")
        try:
            default = self.pick_delivery(False)
        except Stop:
            default = None
        if default:
            said += f"; the open delivery is {default}: pass --delivery {default}"
        else:
            left = self.not_known_closed(files)
            said += (f"; the deliveries not known to be closed are {', '.join(left)}" if left
                     else "; no delivery here is open")
        return said

    def load_delivery(self, key: str) -> None:
        path = self.deliveries / f"{key}.json"
        d = self.shelf.json(f"{self.deliveries_rel}/{key}.json")
        if d is None:
            raise Stop(f"{self.shelf.where(f'{self.deliveries_rel}/{key}.json')} is missing (lead-setup writes it)"
                       f"{self.missing_delivery(key)}{self.base_hint()}")
        if d.get("schemaVersion") != "delivery-flow-delivery/1":
            raise Stop(f"{path}: schemaVersion must be delivery-flow-delivery/1")
        self.raw, self.delivery_path = d, path
        self.delivery_where = self.shelf.where(f"{self.deliveries_rel}/{key}.json")
        self.delivery = d["delivery"]
        base = d.get("baseBranch")
        if base and self.binding_base and not serves(self.binding, base):
            raise Stop(f"{path} names base {base}, but the binding here serves "
                       f"{', '.join([self.binding_base, *self.bases])}; add {base} to the binding's bases, or deliver "
                       f"to a base it serves (see {BASE_MISMATCH})")
        self.base_branch = base or self.binding_base
        if not self.base_branch:
            raise Stop(f"no delivery base: the binding has no base.ref and {path} names no baseBranch")
        if self.shelf.base and self.shelf.base != self.base_branch:
            raise Stop(f"{self.delivery_where} names base {self.base_branch}, but this checkout read it from "
                       f"{self.shelf.ref}; name the delivery's base with RUN_DELIVERY_BASE")
        conflicts = self.harness_conflicts(self.base_branch, "the delivery's base") if self.bases else []
        if conflicts:
            raise Stop("the repository's configs disagree:\n  " + "\n  ".join(conflicts))
        self.sources["baseBranch"] = f"{path.name}" if base else "orchestration binding (base.ref)"
        self.base_ref = f"{self.remote}/{self.base_branch}"
        self.state = self.state_of(key, d)
        if os.environ.get("DELIVERY_STATE"):
            self.sources["state"] = "DELIVERY_STATE"
        elif d.get("stateDirectory"):
            self.sources["state"] = path.name
        else:
            self.sources["state"] = "the lead's checkout (laneCheckout with the delivery key) + the binding's state.directory"
        self.flow_log = self.state.parent / "flow-log"
        self.plan = Path(d["plan"])
        self.lane_brief = Path(d.get("laneBrief", f"docs/{key}.lane-brief.md"))
        self.context_sections = d.get("context", {}).get("sections", [])
        self.lanes = d["lanes"]
        self.stories = d["stories"]
        self.finish = d.get("finish", [])
        self.ship = d.get("ship")
        self.brief_notes = d.get("brief", {}).get("notes")
        for k in self.stories:
            self.requirements(k)  # validated on load

    def check_lanes(self) -> None:
        for key, story in self.stories.items():
            if story.get("lane") not in self.lanes:
                raise Stop(f"{self.delivery_where}: story {key} has lane {story.get('lane')!r}, which isn't in `lanes`")

    def branch(self, key: str) -> str:
        return self.branch_pattern.format(key=key)

    def lane_of(self, key: str) -> str:
        return self.stories[key]["lane"]

    def label(self, key: str) -> str:
        return self.stories[key].get("label", key)

    def require(self, key: str) -> str:
        if key not in self.stories:
            raise Stop(f"{key} is not a story of {self.delivery} ({self.delivery_where})")
        self.check_lanes()
        return key

    def requirements(self, key: str) -> list[dict]:
        """The in-app checks a story's hand-off needs: [{mode, pattern?, args?}], mode any|hold|fail|loaded."""
        if not self.in_app:
            return []
        story = self.stories[key]
        pinned = story.get("pinned", [])
        need = story.get("inApp", "checks" if pinned else "none")
        if need == "none":
            return []
        if need == "loaded":
            return [{"mode": "loaded"}]
        if need == "checks":
            return [{"mode": "hold"}, {"mode": "fail"}] + [{"mode": "any", "pattern": p} for p in pinned]
        if not isinstance(need, list):
            raise Stop(f"story {key}: inApp must be none, loaded, checks or a list of requirements")
        for req in need:
            if not isinstance(req, dict) or req.get("mode") not in ("hold", "fail", "loaded", "any"):
                raise Stop(f"story {key}: requirement {req!r} needs mode hold, fail, loaded or any")
            if req.get("pattern") is not None and req["pattern"] not in pinned:
                raise Stop(f"story {key}: requirement pattern {req['pattern']!r} isn't one the story pins")
            if "args" in req and (not isinstance(req["args"], list) or not all(isinstance(a, str) for a in req["args"])):
                raise Stop(f"story {key}: requirement args must be a list of strings")
        return need


def requirement_label(req: dict) -> str:
    label = f"needs-{req['mode']}"
    if req.get("pattern"):
        label += f" {req['pattern']}"
    if "args" in req:
        label += " args " + json.dumps(req["args"])
    return label


CFG: Config | None = None
DELIVERY_ARG: str | None = None


def cfg() -> Config:
    global CFG
    if CFG is None:
        CFG = Config(repo(), DELIVERY_ARG)
    return CFG


def state_tool(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    script = cfg().release / "skills/orchestrate-work/scripts/delivery_state.py"
    if not script.exists():
        raise Stop(f"no agent-skills release at {cfg().release} (its delivery_state.py is missing)")
    return run([sys.executable, "-B", str(script), "--dir", str(cfg().state), *args], check=check)


def load_state() -> dict:
    return json.loads(state_tool("show").stdout)


def journal_entries(kind: str, subject: str) -> list[dict]:
    return [e for e in load_state()["journal"] if e.get("kind") == kind and e.get("subject") == subject]


def review_dir(key: str, root: Path | None = None) -> Path:
    path = (root or repo()) / cfg().work / "review" / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def receipt_path(c: Config) -> Path:
    """The repository's receipt; a release named by the `release` key that lives in another installation is
    identified by that installation's receipt, beside its generations."""
    own = c.root / RECEIPT
    if own.exists() or not c.release_override:
        return own
    return c.release.resolve().parent.parent / "active.json"


def generation_problem(c: Config) -> str | None:
    """Why the running generation's release identity can't be read from the receipt, as the setup check says it."""
    return generation_problem_at(receipt_path(c), c.release.resolve().name)


def generation_problem_at(path: Path, generation: str) -> str | None:
    if not path.exists():
        return (f"wrong: no receipt at {path}, so the running generation {generation} has no release identity; "
                f"fix: install the release into the repository and run {TOOL_PATH} from it")
    try:
        receipt = json.loads(path.read_text())
    except ValueError as error:
        return f"wrong: the receipt {path} isn't JSON ({error}); fix: reinstall the release into the repository"
    if not isinstance(receipt, dict):
        return f"wrong: the receipt {path} isn't JSON describing a release; fix: reinstall the release into the repository"
    entries = [receipt] + ([receipt["previous"]] if isinstance(receipt.get("previous"), dict) else [])
    if not any(entry.get("generation") == generation for entry in entries):
        return (f"wrong: the running generation {generation} is neither the current nor the previous generation in "
                f"{path}; fix: run the tool as {TOOL_PATH}, from the repository's installed generation")
    return None


def release_tuple() -> dict:
    """The binding tuple of the generation that is running, from its own entry in the receipt: the current one, or
    `previous` while an older generation is still running after an update. Never another generation's."""
    c = cfg()
    problem = generation_problem(c)
    if problem:
        raise Stop(problem)
    receipt = json.loads(receipt_path(c).read_text())
    generation = c.release.resolve().name
    entry = next(e for e in [receipt, receipt.get("previous") or {}] if e.get("generation") == generation)
    return {k: entry["release"][k] for k in ("releaseId", "profile", "archiveSha256", "metadataSha256")}


def graph_digest() -> str:
    return hashlib.sha256((cfg().release / "workflows/delivery-v1.json").read_bytes()).hexdigest()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- plan context ---------------------------------------------------------------------------

def plan_sections(text: str) -> list[tuple[int, str, str]]:
    """(level, heading text, section text including the heading) for every Markdown heading."""
    heads = [(m.start(), len(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^(#{1,6}) (.+)$", text, re.M)]
    sections = []
    for i, (start, level, title) in enumerate(heads):
        end = next((s for s, lv, _ in heads[i + 1:] if lv <= level), len(text))
        sections.append((level, title, text[start:end].strip()))
    return sections


def story_section(sections: list[tuple[int, str, str]], key: str) -> str | None:
    """The first section whose heading names the story's key."""
    return next((body for _, title, body in sections if re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", title)), None)


def context_section(sections: list[tuple[int, str, str]], name: str) -> str | None:
    return next((body for _, title, body in sections if title == name or title.startswith(name)), None)


def plan_context(key: str, text: str | None = None) -> str:
    """The plan's part for one story: its section, the table rows naming it and the delivery's context sections. From
    `text` when given (the plan at a named commit), else as the settings shelf reads it."""
    c = cfg()
    if text is None:
        text = c.shelf.text(c.plan)
    if text is None:
        raise Stop(f"the plan {c.shelf.where(c.plan)} is missing")
    sections = plan_sections(text)
    own = story_section(sections, key)
    if own is None:
        raise Stop(f"no heading in {c.plan} names {key}")
    names = {key, c.label(key)}
    rows = [line for line in text.splitlines() if line.startswith("|")
            and any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", line) for n in names)]
    parts = [own]
    if rows:
        parts += ["", "Table rows naming this story:", *rows]
    for name in c.context_sections:
        body = context_section(sections, name)
        parts += ["", body if body else f"(no '{name}' section in the plan)"]
    return "\n".join(parts)


# -- lane: review rounds -------------------------------------------------------------------

def lens_checkout(key: str, short: str) -> Path:
    """A lens's one checkout for a story's review, kept across its rounds."""
    return repo() / cfg().work / "lens" / f"{key}-{short}"


def prepared_stamp(checkout: Path) -> Path:
    """Beside the checkout, not in it: what the checkout was last prepared for."""
    return checkout.with_name(checkout.name + ".prepared.json")


# Files that decide what the prepare commands install, by name at any depth, besides the files those commands name.
DEPENDENCY_FILES = {"package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
                    "bun.lockb", "pyproject.toml", "setup.py", "setup.cfg", "uv.lock", "poetry.lock", "Pipfile",
                    "Pipfile.lock", "Gemfile", "Gemfile.lock", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
                    ".python-version", ".nvmrc", ".node-version", ".tool-versions"}


def dependency_fingerprint(candidate: str, commands: list[list[str]]) -> str:
    """The prepare commands and the candidate's blobs of every dependency file: a change in either re-prepares."""
    named = {a.removeprefix("./") for argv in commands for a in argv}
    picked = []
    for line in filter(None, git("ls-tree", "-r", "-z", "--full-tree", candidate).split("\0")):
        path = line.split("\t", 1)[1]
        name = path.rsplit("/", 1)[-1]
        if name in DEPENDENCY_FILES or re.fullmatch(r"requirements.*\.txt", name) or path in named:
            picked.append(line)
    return hashlib.sha256(json.dumps([commands, picked]).encode()).hexdigest()


def ready_lens_checkout(checkout: Path, candidate: str) -> None:
    """Bring a lens's checkout to exactly this round's candidate. One kept from an earlier round is reset: tracked
    changes and untracked files go, while ignored files (the prepared environment) stay unless a dependency changed, in
    which case they go too and the prepare commands run again. A missing or broken checkout is made afresh."""
    commands = prepare_commands()
    fingerprint = dependency_fingerprint(candidate, commands)
    stamp = prepared_stamp(checkout)
    top = run(["git", "rev-parse", "--show-toplevel"], cwd=checkout, check=False) if checkout.is_dir() else None
    if top is not None and top.returncode == 0 and Path(top.stdout.strip()).resolve() == checkout.resolve():
        git("checkout", "-q", "--force", "--detach", candidate, cwd=checkout)
        git("clean", "-q", "-ffd", cwd=checkout)
        prepared = json.loads(stamp.read_text()).get("dependencies") if stamp.exists() else None
        if prepared == fingerprint:
            return
        git("clean", "-q", "-ffdx", cwd=checkout)
    else:
        remove_checkout(checkout)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "--detach", "-q", str(checkout), candidate)
    stamp.unlink(missing_ok=True)
    for argv in commands:
        run(argv, cwd=checkout)
    stamp.write_text(json.dumps({"candidate": candidate, "dependencies": fingerprint}, indent=2) + "\n")


def remove_lens_checkout(checkout: Path) -> None:
    remove_checkout(checkout)
    prepared_stamp(checkout).unlink(missing_ok=True)


def remove_checkout(path: Path) -> None:
    if path.exists() or path.is_symlink():
        git("worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)
    git("worktree", "prune", check=False)


def prepare_commands() -> list[list[str]]:
    c = cfg()
    if c.prepare is not None:
        return c.prepare
    return (c.shelf.json(".agents/orchestration.json") or {}).get("prepare", [])


EVIDENCE_HEADING = "## Evidence so far"


def seed_context(key: str) -> Path:
    """The review context: the plan's part, read from the delivery base as it stands now, and the lane's own evidence.
    Every round seeds the plan's part again, so a lens never reviews against a plan the lead has since changed; the
    lane's "Evidence so far" is kept as written. A context without that heading is the lane's own, left as it is."""
    path = review_dir(key) / "context.md"
    c = cfg()
    sha = git("rev-parse", "--verify", "--quiet", c.base_ref, check=False)
    # The plan as the base has it now: a lane's own checkout, and so a shelf reading it, holds the copy its branch forked with.
    shown = run(["git", "show", f"{sha}:{c.plan}"], check=False) if sha else None
    plan = shown.stdout if shown is not None and shown.returncode == 0 else None
    sha = sha if plan is not None else None
    where = (f"Delivery base `{c.base_ref}` at `{sha}`; the plan is `{c.plan}` there (approved, binding). Read it as "
             f"`git show {sha}:{c.plan}`, not your checkout's copy, which is only as new as your candidate's fork."
             if sha else f"Delivery base `{c.base_ref}`; the plan is `{c.plan}` (approved, binding).")
    head = (f"# Review context: {key} ({c.label(key)}), part of {c.delivery}\n\n"
            f"Repository: your private checkout; read its root instruction file. {where} Reference stories, once the "
            "lead names them, are in the assignment.\n\n"
            f"## Ticket contract and plan unit\n\n{plan_context(key, plan)}\n\n")
    if path.exists():
        text = path.read_text()
        # The lane's heading is a line of its own, and the last such line: the plan's part may mention it too.
        found = list(re.finditer(rf"^{re.escape(EVIDENCE_HEADING)}[ \t]*$", text, re.M))
        if found:
            path.write_text(head + text[found[-1].start():])
        return path
    path.write_text(head + f"{EVIDENCE_HEADING}\n\n"
                    f"{CONTEXT_TODO}: the test-first or characterization runs (red then green), the gate results "
                    "(run_delivery.py gate), mutants tried, and known limits. The in-app check records are attached to each brief.\n")
    return path


def flow_log(name: str, key: str) -> Path:
    """The lead-side, append-only logs beside the record (lanes write them through this tool, never by hand)."""
    folder = cfg().flow_log
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{key}.{name}.jsonl"


def read_log(name: str, key: str) -> list[dict]:
    path = flow_log(name, key)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def append_log(name: str, key: str, entry: dict) -> None:
    with open(flow_log(name, key), "a") as log:
        log.write(json.dumps({**entry, "at": now()}) + "\n")


def tracker_conf(c) -> dict:
    """How the tracker's updates are made: by the tracker publisher, through the host, or by hand (tracker-adapter/1)."""
    return tracker_publisher.settings(c.root, c.flow_raw, c.shelf.json(".agents/orchestration.json") or {})


@contextlib.contextmanager
def marked_decision(c, followup: dict | None = None):
    """Journal a decision the tracker publisher posts: the ledger's lock is held, waiting for it, across the journal
    write and its mark, so no publisher run sees the entry unmarked. Yields a list the journalled entry's id goes in."""
    written: list[int] = []
    with tracker_publisher.ledger_lock(c.state, wait=True):
        yield written
        for journal_id in written:
            tracker_publisher.mark(c.flow_log, c.delivery, journal_id, followup)


def journal_id(done: subprocess.CompletedProcess) -> int:
    return json.loads(done.stdout)["entry"]["id"]


def spec_digest(spec: dict, passthrough: list[str]) -> str:
    return hashlib.sha256(json.dumps([spec, passthrough], sort_keys=True).encode()).hexdigest()[:16]


def check_history(key: str, records: list[dict]) -> list[str]:
    """Checks whose current record passes under a spec that differs from an earlier failing run of the same name, and
    runs whose records were removed: what a lens or the lead must see to judge whether an eval was weakened."""
    runs = read_log("app-checks", key)
    notes = []
    names = {r["name"] for r in records}
    current = {r["name"]: spec_digest(r["spec"], r.get("passthrough", [])) for r in records}
    for name in sorted({e["name"] for e in runs}):
        failed_specs = {e["spec"]: e for e in runs if e["name"] == name and not e["pass"]}
        if name not in names:
            notes.append(f"`{name}`: its record was removed after {len([e for e in runs if e['name'] == name])} run(s)")
        else:
            changed = [e for d, e in failed_specs.items() if d != current[name]]
            if changed:
                notes.append(f"`{name}`: passes now, but failed earlier with a different spec: "
                             + "; ".join(f"`{json.dumps(e['specValue'], separators=(',', ':'))}`" for e in changed))
    return notes


def in_app_summary(key: str) -> str:
    """The story's in-app check records, so the testing lens can judge the evals, not just the verdicts."""
    folder = review_dir(key) / "app-checks"
    records = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))] if folder.exists() else []
    if not records:
        return "None recorded yet."
    lines = []
    for r in records:
        failing = [rule for rule, ok in r["rules"].items() if not ok]
        results = [f"`{str(e.get('expr'))[:120]}` -> `{json.dumps(e.get('result', e.get('error')))[:80]}`"
                   for e in [*r["report"].get("evals", []), *r["report"].get("afterEvals", [])]]
        lines.append(f"- `{r['name']}` ({r['mode']}{', baseline' if r.get('baseline') else ''}, "
                     f"{'PASS' if r['pass'] else 'FAIL: ' + '; '.join(failing)}, head {str(r.get('head'))[:10]}): "
                     f"spec `{json.dumps(r['spec'], separators=(',', ':'))}`"
                     + (f", adapter args `{' '.join(r['passthrough'])}`" if r.get("passthrough") else "")
                     + (f"; results: {', '.join(results)}" if results else ""))
    history = check_history(key, records)
    if history:
        lines += ["", "History:", *[f"- {h}" for h in history]]
    return "\n".join(lines) + ("\n\nEverything you need from the records is above; you don't need to read them. Judge "
                               "whether each eval actually proves its acceptance criterion.")


def scoped_checks(span: str) -> str:
    """The brief's two per-round fills: the sensors to run, and the files the round's span changes."""
    changed = git("diff", "--name-only", span).splitlines()
    return ("- scoped sensor commands: the sensors the plan unit names, in the context refs below.\n"
            f"- test files in scope for this round: the tests among the files `git diff {span}` changes, and the "
            "tests that cover the rest: " + (", ".join(f"`{path}`" for path in changed) or "none") + ".")


def brief(key: str, round_no: int, lens: str, candidate: str, tree: str, merge_base: str,
          checkout: Path, prior: list[dict], prior_candidate: str | None) -> str:
    c = cfg()
    short, charter_file = c.lenses[lens]
    charter = Path(charter_file).read_text().strip()
    rel = release_tuple()
    context = (review_dir(key) / "context.md").read_text().strip()
    template = (TOOL / "templates/round-brief.md").read_text()
    if round_no == 1:
        kind = (f"`assessment round`: this lens's first result-carrying round; review the whole subject, the "
                f"delivered diff `git diff {merge_base}..{candidate}` in your checkout, and carry no prior entries "
                "or candidate resolution pairs.")
        carry = "None: this is an assessment round, which carries no prior entries."
        resolutions = "None: an assessment round spans no delta and needs no resolution pair."
        later = "- `lateFindings`: none on an assessment round; omit it."
    else:
        kind = ("`verification round`: re-check every carried finding and its approval condition against the delta "
                f"you derive yourself (`git diff {prior_candidate}..{candidate}`), then review that delta. A change "
                "adopted differently from its approval condition, or declined, is re-checked like any carried finding.")
        carry = "\n".join(
            f"- round {e['binding']['round']}: `{json.dumps({'binding': e['binding'], 'state': e['state'], 'result': e.get('result')}, separators=(',', ':'))}`"
            for e in prior)
        resolutions = (f"- `{prior_candidate}` resolves to git commit `{prior_candidate}`\n"
                       f"- `{candidate}` resolves to git commit `{candidate}`")
        later = ("- `lateFindings`: ids of this result's own findings you raised late (against lines unchanged since "
                 "your previous round); omit when none.\n"
                 "- `resolvedFindings`: required. One entry per finding, "
                 '`{"id", "state": "closed|fixed|withdrawn|deferred|open", "resolution": "..."}`. Every finding this '
                 "lens raised in an earlier round that is still open must appear (one deferred earlier needn't); one "
                 "closed in an earlier round may be listed again or left out. A deferral (P2/P3 only) also carries "
                 "`deferredIssue`.")
    span = f"{merge_base}..{candidate}" if round_no == 1 else f"{prior_candidate}..{candidate}"
    if round_no <= c.bound:
        bound_text = f"`ordinary counted`: round {round_no} of {c.bound}, counted against the declared bound."
    else:
        bound_text = f"`grace`: round {round_no} with declared bound {c.bound}, outside the count."
    values = {
        "lens": lens, "short": short, "key": key, "round": str(round_no), "candidate": candidate, "tree": tree,
        "releaseId": rel["releaseId"], "profile": str(rel["profile"]), "archiveSha256": rel["archiveSha256"],
        "metadataSha256": rel["metadataSha256"], "graph": graph_digest(), "bound_text": bound_text, "kind": kind,
        "carry": carry, "resolutions": resolutions, "charter": charter, "later": later, "checkout": str(checkout),
        "context": context, "in_app": in_app_summary(key), "severities": STORY_SEVERITIES, "checks": scoped_checks(span),
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def round_files(folder: Path) -> list[Path]:
    files = [p for p in folder.glob("round-*.json") if re.fullmatch(r"round-\d+\.json", p.name)]
    return sorted(files, key=lambda p: int(p.stem.split("-")[1]))


def result_path(folder: Path, n: int, short: str) -> Path:
    return folder / f"result-r{n}-{short}.json"


def review_prepare(args) -> None:
    c = cfg()
    key = c.require(args.key)
    release_tuple()  # refuse an unidentified generation before anything is discarded, checked out or written
    n = args.round
    folder = review_dir(key)
    if n < 1 or n > c.bound + 1:
        raise Stop(f"round {n} is outside 1..{c.bound} (plus one grace round)")
    if n == c.bound + 1 and not args.grace:
        raise Stop(f"round {n} is past the bound of {c.bound}: stop and report the blocker to the lead "
                   "(a grace round needs the lead's say-so, then --grace)")
    if (folder / f"round-{n}.json").exists():
        raise Stop(f"round {n} is already reduced; prepare round {n + 1}")
    for k in range(1, n):
        if not (folder / f"round-{k}.json").exists():
            raise Stop(f"round {k} has no round file; reduce it before preparing round {n}")
    meta_path = folder / f"round-{n}.meta.json"
    previous = json.loads(meta_path.read_text()) if meta_path.exists() else None
    if previous and not args.restart:
        raise Stop(f"round {n} is already prepared (at {previous['candidate'][:10]}). Reduce it, or pass --restart to "
                   "discard it; a restart is recorded and the lead has to accept it")
    git("fetch", "-q", c.remote, c.base_branch)  # before the context, which reads the plan from the base
    context = seed_context(key)
    if CONTEXT_TODO in context.read_text():
        raise Stop(f"fill in 'Evidence so far' in {context} first (the rest is seeded from the plan)")
    candidate = clean_head()
    tree = git("rev-parse", "HEAD^{tree}")
    merge_base = git("merge-base", c.base_ref, "HEAD")
    prior_rounds = [json.loads(p.read_text()) for p in round_files(folder)]
    prior_candidate = prior_rounds[-1]["entries"][0]["binding"]["candidateRef"] if prior_rounds else None
    if prior_candidate == candidate:
        raise Stop("HEAD is the candidate of the previous round; commit the fixes first. When the round's findings were "
                   "settled without a change here (a ruling, or a fix on the base, which a lane doesn't merge), record "
                   f'that as its own commit, `git commit --allow-empty -m "{key}: <the ruling>"`, and note the ruling in '
                   "context.md's Evidence so far, so each round still has its own candidate")
    restarts = list(previous.get("restarts", [])) if previous else []
    attempts = previous.get("attempts", 0) if previous else 0
    if previous:
        discarded = {}
        for lens, (short, _) in c.lenses.items():
            path = result_path(folder, n, short)
            if path.exists():
                discarded[lens] = digest(path)
                path.rename(folder / f"result-r{n}-{short}.discarded{len(restarts) + 1}.json")
        restarts.append({"at": now(), "previousCandidate": previous["candidate"], "discardedResults": discarded})
    briefs = []
    for lens, (short, _) in c.lenses.items():
        checkout = lens_checkout(key, short)
        ready_lens_checkout(checkout, candidate)
        if git("status", "--porcelain", cwd=checkout):
            raise Stop(f"preparing {checkout} left it dirty; the repository's prepare commands must not touch tracked files")
        prior = [next(e for e in r["entries"] if e["lens"] == lens) for r in prior_rounds]
        path = folder / f"brief-r{n}-{short}.md"
        path.write_text(brief(key, n, lens, candidate, tree, merge_base, checkout, prior, prior_candidate))
        briefs.append((lens, path, result_path(folder, n, short)))
    meta_path.write_text(json.dumps({"round": n, "candidate": candidate, "tree": tree, "startedAt": now(),
                                     "lenses": list(c.lenses), "attempts": attempts, "restarts": restarts}, indent=2))
    append_log("prepares", key, {"round": n, "candidate": candidate, "restart": bool(previous)})
    state_tool("focus", "--lane", c.lane_of(key), "--text", f"{key} review round {n}", "--state", "reviewing", check=False)
    print(f"{key} round {n}: candidate {candidate[:10]}, tree {tree[:10]}" + (f" (restart {len(restarts)})" if restarts else ""))
    print()
    if n == 1 and not restarts:
        print("Start one reviewer per lens, in parallel, each with exactly this prompt. Keep track of each reviewer: "
              "later rounds resume the same one.")
    else:
        print("Resume the same reviewer for each lens, in parallel, with exactly this message. Only if a reviewer "
              "can't be resumed, start a fresh one with the same prompt and say so in the hand-off.")
    print("Save each final JSON message, exactly as returned, to its result path. Never write or edit a result.\n")
    for lens, path, result in briefs:
        print(f"- {lens}\n  prompt: Read {path} and follow it exactly. Your final message is only the JSON it asks for.\n"
              f"  save to: {result}")
    print(f"\nThen: {flow_command()} review-reduce {key} --round {n}")


def flow_command() -> str:
    """The command briefs and messages name: the stable installed path, never a generation or projection path, or
    the configured release's own copy when the `release` key names one."""
    c = CFG
    if c is not None and c.release_override:
        return f"python3 -B {Path(c.release_override) / 'skills/run-delivery/scripts/run_delivery.py'}"
    return f"python3 -B {TOOL_PATH}"


def _validator():
    path = str(cfg().release / "skills/orchestrate-work/scripts")
    if path not in sys.path:
        sys.path.insert(0, path)
    sys.dont_write_bytecode = True  # the release's modules leave no bytecode in the installed generation
    import delivery_state  # noqa: E402  (the installed release's own validator)
    return delivery_state


def check_result(result: object, *, key: str, lens: str, short: str, n: int, meta: dict,
                 carried: set[str]) -> list[str]:
    """What's wrong with one lens result, as messages; empty when it matches its brief."""
    if not isinstance(result, dict):
        return ["is not a JSON object"]
    problems = []
    expect = {"schemaVersion": "review-lens-result/1", "lensId": lens, "subjectRef": key,
              "candidateRef": meta["candidate"], "round": n}
    for field, value in expect.items():
        if result.get(field) != value:
            problems.append(f"{field} is {result.get(field)!r}, expected {value!r}")
    if result.get("outcome") not in ("aligned", "changes-requested"):
        problems.append(f"outcome is {result.get('outcome')!r}")
    evidence = result.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(e, str) for e in evidence):
        problems.append("evidence must be a non-empty list of strings")
    elif not any(meta["tree"] in e for e in evidence):
        problems.append(f"evidence doesn't name the reviewed tree {meta['tree']}")
    findings = result.get("findings")
    if not isinstance(findings, list):
        return problems + ["findings must be a list"]
    open_blocking = False
    ids = set()
    late_ids = result.get("lateFindings", [])
    late_ids = set(late_ids) if isinstance(late_ids, list) and all(isinstance(x, str) for x in late_ids) else set()
    for f in findings:
        if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not isinstance(f.get("summary"), str):
            problems.append(f"finding {f!r} lacks id or summary")
            continue
        ids.add(f["id"])
        if not f["id"].startswith(f"{short}-r{n}-"):
            problems.append(f"finding id {f['id']} doesn't start with {short}-r{n}-")
        if f.get("severity") not in SEVERITIES:
            problems.append(f"finding {f['id']} has severity {f.get('severity')!r}")
        elif f["severity"] in ("P0", "P1"):
            if f.get("state") != "deferred":
                open_blocking = True
            elif f["severity"] == "P0":
                problems.append(f"finding {f['id']} is P0 and can't be deferred")
            # The out-of-delta rule's one deferral: a verification round's new P1 against lines unchanged since the
            # lens's last round, marked late, with what its follow-up item must say.
            elif n == 1:
                problems.append(f"finding {f['id']} is P1 and can't be deferred in an assessment round")
            elif f["id"] not in late_ids:
                problems.append(f"finding {f['id']} is a deferred P1 not listed in lateFindings: only a P1 outside "
                                "the delta is deferred")
            elif not str(f.get("deferredIssue", "")).strip():
                problems.append(f"finding {f['id']} is a deferred P1 with no deferredIssue: say what its follow-up "
                                "item must say")
        elif f.get("state") != "deferred" or not str(f.get("deferredIssue", "")).strip():
            problems.append(f"finding {f['id']} is {f['severity']}: file it with state deferred and a deferredIssue")
    if result.get("outcome") == "aligned" and open_blocking:
        problems.append("outcome is aligned but it raises an open P0/P1 finding")
    late = result.get("lateFindings", [])
    if not isinstance(late, list) or any(x not in ids for x in late):
        problems.append("lateFindings must list ids of this result's own findings")
    closures = result.get("resolvedFindings", result.get("resolutions", []))
    if not isinstance(closures, list):
        problems.append("resolvedFindings must be a list")
        closures = []
    missing = carried - {x.get("id") for x in closures if isinstance(x, dict)}
    if missing:
        problems.append(f"resolvedFindings leaves out carried finding(s) {sorted(missing)}")
    return problems


def round_counts(outcome: dict) -> tuple[dict, dict]:
    verdicts = {e["lens"]: ("approve" if e["result"]["outcome"] == "aligned" else "changes") for e in outcome["entries"]}
    counts = {p: sum(1 for e in outcome["entries"] for f in e["result"].get("findings", []) if f.get("severity") == p)
              for p in SEVERITIES}
    return verdicts, counts


def ensure_recorded(key: str, outcome: dict, lane: str, folder: Path) -> None:
    """The round in the record as the lane's own: its review.round, then the rounds so far captured at the round's
    candidate, so the live view shows the round's card and notes before hand-off. Each write is skipped once made,
    so a re-run finishes what a failure left; a story with a product run is reviewed through its run instead."""
    doc = load_state()
    ensure_journaled(key, outcome, lane, doc)
    item = next((i for i in [*doc["state"]["queue"], *doc["state"].get("planned", [])] if i["subject"] == key), None)
    if item is None or item.get("runId"):
        return
    rounds = [folder / f"round-{k}.json" for k in range(1, outcome["round"] + 1)]
    state_tool("capture-review", "--subject", key, "--candidate", outcome["entries"][0]["binding"]["candidateRef"],
               *[x for p in rounds for x in ("--outcome", str(p))])


def ensure_journaled(key: str, outcome: dict, lane: str, doc: dict) -> None:
    n = outcome["round"]
    if any(e.get("kind") == "review.round" and e.get("subject") == key and e["review"]["round"] == n
           for e in doc["journal"]):
        return
    verdicts, counts = round_counts(outcome)
    review = {"round": n, "startedAt": outcome["startedAt"], "finishedAt": outcome["finishedAt"],
              "verdicts": verdicts, "findings": counts}
    extra = round_history_text(outcome)
    state_tool("journal", "--writer", "lane", "--kind", "review.round", "--subject", key, "--lane", lane,
               "--review", json.dumps(review),
               *(["--summary", f"Round {n}: " + ", ".join(f"{k} {v}" for k, v in verdicts.items()) + f"; {extra}"] if extra else []))


def round_history_text(outcome: dict) -> str:
    parts = []
    if outcome.get("attempts"):
        parts.append(f"{outcome['attempts']} refused attempt(s)")
    if outcome.get("restarts"):
        parts.append(f"{len(outcome['restarts'])} restart(s) discarding results")
    return ", ".join(parts)


def summarize(key: str, outcome: dict) -> None:
    n = outcome["round"]
    verdicts, counts = round_counts(outcome)
    history = round_history_text(outcome)
    print(f"{key} round {n}: {outcome['outcome']} ({', '.join(f'{k}: {v}' for k, v in verdicts.items())}); "
          f"findings P0 {counts['P0']}, P1 {counts['P1']}, P2 {counts['P2']}, P3 {counts['P3']}"
          + (f"; {history}" if history else ""))
    for b in outcome["blockers"]:
        print(f"  open {b['severity']} {b['id']} ({b['lens']}): {b['summary']}")
    for e in outcome["entries"]:
        for f in e["result"].get("findings", []):
            if f.get("state") == "deferred":
                print(f"  deferred {f['severity']} {f['id']} ({e['lens']}): {f['summary']} -> follow-up: {f['deferredIssue']}")
    print(f"  {outcome['action']}")
    blockers = "; ".join(f"{b['id']}: {b['summary']}" for b in outcome["blockers"])
    print(f"  Tracker post (your story): round {n}: {', '.join(f'{k} {v}' for k, v in verdicts.items())}; findings "
          f"P0 {counts['P0']}, P1 {counts['P1']}, P2 {counts['P2']}, P3 {counts['P3']}"
          + (f"; {blockers}" if blockers else "") + (f"; {history}" if history else ""))


def review_reduce(args) -> None:
    c = cfg()
    key = c.require(args.key)
    n = args.round
    lane = c.lane_of(key)
    folder = review_dir(key)
    done_path = folder / f"round-{n}.json"
    if done_path.exists():  # reduced already: never rewrite it, only finish the journaling if that failed
        outcome = json.loads(done_path.read_text())
        ensure_recorded(key, outcome, lane, folder)
        summarize(key, outcome)
        return
    meta_path = folder / f"round-{n}.meta.json"
    if not meta_path.exists():
        raise Stop(f"round {n} was never prepared: run review-prepare {key} --round {n} first")
    meta = json.loads(meta_path.read_text())
    if meta["lenses"] != list(c.lenses):
        raise Stop(f"round {n} was prepared for {meta['lenses']}, not this repository's lenses {list(c.lenses)}")
    prior = [json.loads(p.read_text()) for p in round_files(folder)]
    if [r["round"] for r in prior] != list(range(1, n)):
        raise Stop(f"rounds before {n} are missing or out of order: {[r['round'] for r in prior]}")
    rel, graph = release_tuple(), graph_digest()
    entries, lens_rounds, digests, problems, bad = [], [], {}, [], set()
    for lens, (short, _) in c.lenses.items():
        path = result_path(folder, n, short)
        if not path.exists():
            problems.append(f"{lens}: missing {path}; save its subagent's final JSON there")
            continue
        raw = path.read_bytes()
        digests[lens] = hashlib.sha256(raw).hexdigest()
        text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.decode())
        try:
            result = json.loads(text)
        except json.JSONDecodeError as error:
            problems.append(f"{lens}: {path.name} is not JSON ({error})")
            bad.add(lens)
            continue
        carried: set[str] = set()
        for r in prior:
            for e in r["entries"]:
                if e["lens"] == lens:
                    carried |= {f["id"] for f in e["result"].get("findings", []) if f.get("state") != "deferred"}
                    carried -= {x["id"] for x in e["result"].get("resolvedFindings", e["result"].get("resolutions", []))
                                if x.get("state") in CLOSED}
        own = check_result(result, key=key, lens=lens, short=short, n=n, meta=meta, carried=carried)
        if own:
            bad.add(lens)
            problems += [f"{lens}: {p}" for p in own]
        entries.append({
            "lens": lens,
            "binding": {"subjectRef": key, "candidateRef": meta["candidate"], "round": n, "release": rel,
                        "graphSha256": graph, "candidateTree": meta["tree"]},
            "state": "obtained", "independence": {"status": "unverified"}, "result": result})
        lens_rounds.append({"lens": lens, "outcome": result.get("outcome"), "findings": result.get("findings", []),
                            "evidence": result.get("evidence"), "failure": None,
                            "late_findings": result.get("lateFindings", [])})

    def refuse(lenses_at_fault: set[str], message: str) -> None:
        meta["attempts"] = meta.get("attempts", 0) + 1
        meta_path.write_text(json.dumps(meta, indent=2))
        for lens in lenses_at_fault:
            short = c.lenses[lens][0]
            path = result_path(folder, n, short)
            if path.exists():
                path.rename(folder / f"result-r{n}-{short}.attempt{meta['attempts']}.json")
        raise Stop(message + f"\n(kept as attempt {meta['attempts']}; re-run only "
                   f"{', '.join(sorted(lenses_at_fault))} with the same brief; the other results stand)")

    if problems and not bad:  # only missing files: nothing was refused, so it isn't an attempt
        raise Stop("\n".join(problems))
    if problems:
        refuse(bad, "results that don't match their briefs:\n  " + "\n  ".join(problems))
    open_findings: dict[str, dict] = {}
    for e in [x for r in prior for x in r["entries"]] + entries:
        res = e["result"]
        for f in res.get("findings", []):
            if f.get("state") != "deferred":
                open_findings[f["id"]] = {"id": f["id"], "lens": e["lens"], "severity": f["severity"], "summary": f["summary"]}
        for x in res.get("resolvedFindings", res.get("resolutions", [])):
            if x.get("state") in CLOSED:
                open_findings.pop(x["id"], None)
    blockers = [f for f in open_findings.values() if f["severity"] in ("P0", "P1")]
    aligned = all(e["result"]["outcome"] == "aligned" for e in entries) and not blockers
    if aligned:
        status, action = "aligned", "Continue with the caller-owned delivery workflow."
    elif n >= c.bound:
        status, action = "blocked", "Bound reached (review.loop-bound-reached): stop and report the open findings to the lead."
        blockers = blockers + [{"id": "review.loop-bound-reached", "lens": None, "severity": "P1",
                                "summary": f"round {n} of {c.bound} ended without alignment"}]
    else:
        status, action = "unresolved", "Fix the open P0/P1 findings, commit, then run the next round."
    candidate_ref = {"schemaVersion": "workflow-candidate-ref/1", "opaque": meta["candidate"]}
    subject_ref = {"schemaVersion": "workflow-subject-ref/1", "opaque": key}
    outcome = {
        "round": n, "outcome": status, "boundRounds": c.bound, "action": action, "blockers": blockers,
        "startedAt": meta["startedAt"], "finishedAt": now(), "resultDigests": digests,
        "attempts": meta.get("attempts", 0), "restarts": meta.get("restarts", []),
        "acquisitionEnvelope": {
            "schemaVersion": "review-acquisition-envelope/1", "release": rel, "graphSha256": graph,
            "subjectRef": subject_ref, "candidateRef": candidate_ref, "round": n, "requestedLenses": meta["lenses"],
            "entries": [{"release": rel, "graphSha256": graph, "subjectRef": subject_ref, "candidateRef": candidate_ref,
                         "round": n, "lens": e["lens"], "state": e["state"], "independence": e["independence"],
                         "result": e["result"]} for e in entries]},
        "entries": entries,
        "reviewWorkReturn": {
            "status": status,
            "rounds": [*[r["reviewWorkReturn"]["rounds"][-1] for r in prior], lens_rounds],
            "dissent": [], "failures": [], "blockers": blockers, "action": action,
            "late_findings": [x for lr in lens_rounds for x in lr["late_findings"]],
            "grace_verification": None, "round_history": [], "base_move_reopenings": [],
            "counted_rounds": min(n, c.bound)},
    }
    try:
        _validator().validate_review_outcomes([*prior, outcome], key, meta["candidate"])
    except Exception as error:
        refuse(set(c.lenses), f"the release's validator refuses the round: {error}")
    done_path.write_text(json.dumps(outcome, indent=1) + "\n")
    if status != "unresolved":  # another round reuses each lens's checkout; an aligned or bounded review is done with it
        for lens, (short, _) in c.lenses.items():
            remove_lens_checkout(lens_checkout(key, short))
    ensure_recorded(key, outcome, lane, folder)
    summarize(key, outcome)


# -- the plan's review -------------------------------------------------------------------------
# A plan review runs like a story's review, with no bound: one clean checkout and one brief per lens, carried findings,
# and a reduction per round. Its store, .orchestration/review/<delivery>/plan/ without checkouts/, keeps every round in
# full; build_plan reads it, and fits what the plan file holds of it to the plan contract's caps. A plan that isn't a
# delivery yet is reviewed with --plan: its store is .orchestration/plan-review/<reviewSlug>/, keyed by the slug in the
# plan's front matter so a renamed plan keeps its review, and lead-setup or lead-init moves it into the delivery's.

PLAN_REVIEW_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PLAN_REVIEW_REASON = 600
PLAN_REVIEW_CLOSED = ("closed", "fixed", "withdrawn", "dropped")
REVIEW_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def plan_review_dir(c) -> Path:
    return getattr(c, "plan_store", None) or c.main / c.work / "review" / c.delivery / "plan"


def plan_review_subject(c) -> str:
    """The store's own subject, kept when a delivery adopts it; a delivery's own review is `<delivery>.plan`."""
    own = load_json(plan_review_dir(c) / "store.json")
    return own["subject"] if own else f"{c.delivery}.plan"


def plan_review_name(c) -> str:
    """Who the review belongs to, as its messages name it."""
    return f"plan {c.plan}" if getattr(c, "plan_store", None) else c.delivery


def plan_review_command(c, action: str) -> str:
    return f"{flow_command()} plan-review {action}" + (f" --plan {shlex.quote(str(c.plan))}"
                                                       if getattr(c, "plan_store", None) else "")


def plan_review_lenses(c) -> list[str]:
    """The lenses the delivery file names for its plan review, or --lenses (round 1's when omitted) for a plan's own
    review: declared lenses, each with a charter."""
    if getattr(c, "plan_store", None):
        where = "--lenses"
        lenses = c.plan_lenses
        if lenses is None:
            rounds = plan_review_rounds(c)
            if rounds:
                return rounds[0]["lenses"]
            raise Stop(f"name the plan review's lenses with --lenses, such as --lenses {','.join(c.lenses)}")
    else:
        where = f"{c.delivery_where} planReview.lenses"
        lenses = (c.raw.get("planReview") or {}).get("lenses")
        if not isinstance(lenses, list) or not lenses:
            raise Stop(f"{c.delivery_where} names no plan review lenses: set planReview.lenses, such as {list(c.lenses)}")
    if not lenses or len(lenses) > 12 or len(set(lenses)) != len(lenses):
        raise Stop(f"{where} must be 1 to 12 different lenses")
    unknown = [lens for lens in lenses if lens not in c.lenses or not PLAN_REVIEW_ID.fullmatch(lens)]
    if unknown:
        raise Stop(f"{where} names {', '.join(map(str, unknown))}, which this repository doesn't declare; its lenses "
                   f"are {', '.join(c.lenses)}")
    return lenses


def front_matter(text: str) -> dict[str, str]:
    """A plan's leading `---` block, as its `key: value` lines."""
    block = re.match(r"---\n(.*?)\n---(?:\n|$)", text, re.S)
    if not block:
        return {}
    pairs = (line.partition(":") for line in block.group(1).splitlines())
    return {key.strip(): value.strip().strip("'\"") for key, colon, value in pairs if colon}


def review_slug(plan: Path, text: str) -> str | None:
    """The plan's `reviewSlug`, or None when its front matter has none."""
    slug = front_matter(text).get("reviewSlug")
    if slug is not None and not REVIEW_SLUG.fullmatch(slug):
        raise Stop(f"{plan}: reviewSlug {slug!r} must be 1 to 64 lowercase letters, digits and hyphens, starting with a "
                   "letter or digit")
    return slug


def plan_review_config(args):
    """The config a plan-review command runs under: the delivery's, or with --plan the plan's own store, which needs no
    delivery file, binding or record."""
    if not getattr(args, "plan", None):
        return cfg()
    global CFG
    c = CFG = Config(repo(), need_delivery=False, pick=False)
    plan = Path(args.plan)
    path = c.root / plan
    if not path.is_file():
        raise Stop(f"no plan at {path}")
    slug = review_slug(plan, path.read_text())
    if slug is None:
        suggested = re.sub(r"[^a-z0-9]+", "-", plan.stem.lower()).strip("-")[:64] or "plan"
        raise Stop(f"{plan} has no review slug, which keeps its review when the plan is renamed. Add this front matter "
                   f"at its top and commit it before round 1:\n---\nreviewSlug: {suggested}\n---")
    c.plan, c.plan_store = plan, c.main / c.work / "plan-review" / slug
    adopted = load_json(c.plan_store / "adopted.json")
    if adopted:
        raise Stop(f"the review of {plan} was adopted by delivery {adopted['delivery']} ({adopted['store']}); run "
                   f"{flow_command()} plan-review {args.action} without --plan")
    given = getattr(args, "lenses", None)
    c.plan_lenses = [lens.strip() for lens in given.split(",") if lens.strip()] if given is not None else None
    return c


def adoptable_plan_review(c, plan: Path) -> Path | None:
    """The plan's own review store, when it holds rounds no delivery has adopted; refused while a round is prepared,
    since its checkouts live in the store."""
    path = c.root / plan
    slug = review_slug(plan, path.read_text()) if path.is_file() else None
    source = c.main / c.work / "plan-review" / slug if slug else None
    if not source or not source.is_dir() or (source / "adopted.json").exists():
        return None
    n = len(round_files(source)) + 1
    if (source / f"round-{n}.meta.json").exists():
        raise Stop(f"the plan's review round {n} is prepared in {source}; reduce it ({flow_command()} plan-review reduce "
                   f"--plan {shlex.quote(str(plan))}) before a delivery adopts the review")
    return source if n > 1 else None


def adopt_plan_review(c, source: Path, delivery: str) -> str:
    """Move the plan's own review store into the delivery's, leaving a pointer behind, so the plan chapter shows it."""
    target = c.main / c.work / "review" / delivery / "plan"
    if target.exists() and any(target.iterdir()):
        raise Stop(f"delivery {delivery} already has a plan review in {target}, so it can't adopt the one in {source}; "
                   "keep one of them and remove the other")
    checkouts = source / "checkouts"
    if checkouts.exists():
        if any(checkouts.iterdir()):
            raise Stop(f"{checkouts} still holds lens checkouts; reduce the round, or remove them, before adopting")
        checkouts.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rmdir()
    rounds = len(round_files(source))
    shutil.move(str(source), str(target))
    source.mkdir()
    (source / "adopted.json").write_text(json.dumps({"delivery": delivery, "store": str(target), "at": now()},
                                                    indent=2) + "\n")
    return f"adopted the plan review ({rounds} round{'' if rounds == 1 else 's'}) from {source} into {target}"


def plan_review_rounds(c) -> list[dict]:
    """Every reduced round, in order."""
    return [json.loads(p.read_text()) for p in round_files(plan_review_dir(c))] if plan_review_dir(c).exists() else []


def plan_review_ended(c) -> dict | None:
    return load_json(plan_review_dir(c) / "ended.json")


def plan_review_open(rounds: list[dict]) -> dict[str, dict]:
    """The findings still open after the given rounds, by id."""
    found: dict[str, dict] = {}
    for outcome in rounds:
        for entry in outcome["entries"]:
            result = entry["result"]
            for f in result.get("findings", []):
                found[f["id"]] = {"id": f["id"], "lens": entry["lens"], "severity": f["severity"], "summary": f["summary"]}
            for x in result.get("resolvedFindings", []):
                if x.get("state") in PLAN_REVIEW_CLOSED:
                    found.pop(x["id"], None)
    return found


def plan_review_state(c, rounds: list[dict]) -> str:
    if plan_review_ended(c):
        return "ended"
    return "aligned" if rounds and rounds[-1]["outcome"] == "aligned" else "open"


def plan_review_of(c=None) -> dict | None:
    """The plan file's `planReview`: the store's rounds through the shared projection, fitted to the plan's caps; None
    with no reduced round."""
    c = c or cfg()
    rounds = plan_review_rounds(c)
    if not rounds:
        return None
    plan_validator(c)  # puts the release's delivery-views scripts first on the path
    import review_projection  # noqa: E402  (the installed release's own projection)
    detail = review_projection.round_detail(rounds, {r["round"]: r["candidate"] for r in rounds})
    ended = plan_review_ended(c)
    return review_projection.fit_plan_review(detail, rounds[0]["lenses"], plan_review_state(c, rounds),
                                             ended["reason"] if ended else None)


def plan_review_words(review: dict | None) -> str | None:
    """The approval line's fixed words, without its date or reason: "after 3 review rounds · every lens aligned"."""
    if not review:
        return None
    count = review["rounds"][-1]["round"]
    ending = {"aligned": "every lens aligned", "ended": "ended without alignment", "open": "still open"}[review["state"]]
    return f"after {count} review round{'' if count == 1 else 's'} · {ending}"


def plan_checkout_problem(path: Path, candidate: str, tree: str) -> str | None:
    """Why a lens's checkout fails its identity check, or None: at the candidate, with its tree, and clean."""
    if not path.exists():
        return "is missing"
    head = git("rev-parse", "HEAD", cwd=path, check=False)
    if head != candidate:
        return f"is at {head[:10] or 'no commit'}, not the candidate {candidate[:10]}"
    if git("rev-parse", "HEAD^{tree}", cwd=path, check=False) != tree:
        return "holds another tree"
    if git("status", "--porcelain", "--untracked-files=all", cwd=path, check=False):
        return "has changes"
    return None


def plan_brief(c, n: int, lens: str, candidate: str, tree: str, checkout: Path, prior: list[dict],
               prior_candidate: str | None) -> str:
    short, charter_file = c.lenses[lens]
    rel = release_tuple()
    subject = plan_review_subject(c)
    if n == 1:
        kind = (f"`assessment round`: this lens's first result-carrying round; review the whole plan, `{c.plan}` in your "
                "checkout, and carry no prior entries or candidate resolution pairs.")
        carry = "None: this is an assessment round, which carries no prior entries."
        resolutions = "None: an assessment round spans no delta and needs no resolution pair."
        later = "- `lateFindings`: none on an assessment round; omit it."
    else:
        kind = ("`verification round`: re-check every carried finding and its approval condition against the plan's "
                f"revision, the delta you derive yourself (`git diff {prior_candidate}..{candidate}`), then review that "
                "delta. A change adopted differently from its approval condition, or declined, is re-checked like any "
                "carried finding.")
        carry = "\n".join(
            f"- round {e['binding']['round']}: `{json.dumps({'binding': e['binding'], 'state': e['state'], 'result': e.get('result')}, separators=(',', ':'))}`"
            for e in prior) or "None: this lens has carried nothing."
        resolutions = (f"- `{prior_candidate}` resolves to git commit `{prior_candidate}`\n"
                       f"- `{candidate}` resolves to git commit `{candidate}`")
        later = ("- `lateFindings`: ids of this result's own findings you raised late (against lines unchanged since "
                 "your previous round); omit when none.\n"
                 "- `resolvedFindings`: required. One entry for every finding this lens raised in an earlier round and "
                 'has not closed, `{"id", "state": "closed|fixed|withdrawn|open", "resolution": "..."}`. Every carried '
                 "finding must appear; a plan review defers none.")
    whose = "" if getattr(c, "plan_store", None) else f", the plan of delivery {c.delivery}"
    context = (f"The plan under review is `{c.plan}` in your checkout{whose}. Judge whether it is ready to approve: its outcome, scope, contract decisions, units, sensors and finish line. The plan "
               "is the whole subject; there is no ticket or code diff to review beyond it.")
    values = {
        "lens": lens, "short": short, "key": subject, "round": str(n), "candidate": candidate, "tree": tree,
        "releaseId": rel["releaseId"], "profile": str(rel["profile"]), "archiveSha256": rel["archiveSha256"],
        "metadataSha256": rel["metadataSha256"], "graph": graph_digest(),
        "bound_text": f"`unbounded`: unbounded round {n}, with no bound supplied or invented.", "kind": kind,
        "carry": carry, "resolutions": resolutions, "charter": Path(charter_file).read_text().strip(), "later": later,
        "checkout": str(checkout), "context": context, "in_app": "None: a plan review has no in-app evidence.",
        "severities": PLAN_SEVERITIES,
        "checks": ("- scoped sensor commands: none; a plan review runs no checks, since the plan is the whole subject.\n"
                   "- test files in scope for this round: none."),
    }
    template = (TOOL / "templates/round-brief.md").read_text()
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def plan_review_prepare(args) -> None:
    c = plan_review_config(args)
    release_tuple()  # refuse an unidentified generation before anything is checked out or written
    folder = plan_review_dir(c)
    lenses = plan_review_lenses(c)
    if plan_review_ended(c):
        raise Stop(f"the plan review was ended ({folder / 'ended.json'}); a new review needs a new store")
    rounds = plan_review_rounds(c)
    if rounds and rounds[0]["lenses"] != lenses:
        where = "--lenses" if getattr(c, "plan_store", None) else c.delivery_where
        raise Stop(f"the plan review's lenses were fixed at round 1 as {rounds[0]['lenses']}; {where} now "
                   f"names {lenses}. An amendment is judged by the lenses that reviewed the plan")
    n = len(rounds) + 1
    meta_path = folder / f"round-{n}.meta.json"
    previous = load_json(meta_path)
    if previous and not args.restart:
        raise Stop(f"plan review round {n} is already prepared (at {previous['candidate'][:10]}). Reduce it, or pass "
                   "--restart to discard it")
    root = repo()
    candidate = clean_head(root)
    if run(["git", "cat-file", "-e", f"{candidate}:{c.plan.as_posix()}"], cwd=root, check=False).returncode != 0:
        raise Stop(f"the plan {c.plan} isn't committed at {candidate[:10]}; commit it first")
    tree = git("rev-parse", "HEAD^{tree}", cwd=root)
    prior_candidate = rounds[-1]["candidate"] if rounds else None
    if prior_candidate == candidate:
        raise Stop(f"HEAD {candidate[:10]} is the candidate of round {n - 1}; commit the plan's revision first")
    folder.mkdir(parents=True, exist_ok=True)
    if getattr(c, "plan_store", None) and not (folder / "store.json").exists():
        slug = folder.name
        (folder / "store.json").write_text(json.dumps({"slug": slug, "plan": c.plan.as_posix(),
                                                       "subject": f"{slug}.plan"}, indent=2) + "\n")
    if previous:
        for lens in lenses:
            path = result_path(folder, n, c.lenses[lens][0])
            if path.exists():
                path.rename(path.with_suffix(f".discarded-{now().replace(':', '')}.json"))
    briefs, checks = [], {}
    for lens in lenses:
        short = c.lenses[lens][0]
        checkout = folder / "checkouts" / lens
        remove_checkout(checkout)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "--detach", "-q", str(checkout), candidate, cwd=root)
        for argv in prepare_commands():
            run(argv, cwd=checkout)
        problem = plan_checkout_problem(checkout, candidate, tree)
        if problem:
            raise Stop(f"{lens}: its checkout {checkout} {problem} before its brief was written")
        checks[lens] = "passed"
        prior = [e for r in rounds for e in r["entries"] if e["lens"] == lens]
        path = folder / f"brief-r{n}-{short}.md"
        path.write_text(plan_brief(c, n, lens, candidate, tree, checkout, prior, prior_candidate))
        briefs.append((lens, path, result_path(folder, n, short)))
    meta_path.write_text(json.dumps({"round": n, "candidate": candidate, "tree": tree, "startedAt": now(),
                                     "lenses": lenses, "startChecks": checks}, indent=2) + "\n")
    print(f"{plan_review_name(c)} plan review round {n}: candidate {candidate[:10]}, tree {tree[:10]}; no round bound")
    print()
    print("Start one reviewer per lens, in parallel, each with exactly this prompt" if n == 1 and not previous else
          "Resume the same reviewer for each lens, in parallel, with exactly this message; only if one can't be resumed, "
          "start a fresh one with the same prompt")
    print("Save each final JSON message, exactly as returned, to its result path. Never write or edit a result.\n")
    for lens, path, result in briefs:
        print(f"- {lens}\n  prompt: Read {path} and follow it exactly. Your final message is only the JSON it asks for.\n"
              f"  save to: {result}")
    print(f"\nThen: {plan_review_command(c, 'reduce')}")


def plan_check_result(result: object, *, subject: str, lens: str, short: str, n: int, meta: dict, carried: set[str],
                      known: dict[str, str]) -> list[str]:
    """What's wrong with one plan-review lens result, as messages; empty when it matches its brief."""
    if not isinstance(result, dict):
        return ["is not a JSON object"]
    problems = []
    expect = {"schemaVersion": "review-lens-result/1", "lensId": lens, "subjectRef": subject,
              "candidateRef": meta["candidate"], "round": n}
    for field, value in expect.items():
        if result.get(field) != value:
            problems.append(f"{field} is {result.get(field)!r}, expected {value!r}")
    if result.get("outcome") not in ("aligned", "changes-requested"):
        problems.append(f"outcome is {result.get('outcome')!r}")
    evidence = result.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(e, str) for e in evidence):
        problems.append("evidence must be a non-empty list of strings")
    elif not any(meta["tree"] in e for e in evidence):
        problems.append(f"evidence doesn't name the reviewed tree {meta['tree']}")
    findings = result.get("findings")
    if not isinstance(findings, list):
        return problems + ["findings must be a list"]
    open_blocking = False
    for f in findings:
        if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not isinstance(f.get("summary"), str) or not f["summary"].strip():
            problems.append(f"finding {f!r} lacks id or summary")
            continue
        if not PLAN_REVIEW_ID.fullmatch(f["id"]) or not f["id"].startswith(f"{short}-r{n}-"):
            problems.append(f"finding id {f['id']} must start with {short}-r{n}- and hold only letters, digits, dots, "
                            "dashes or underscores, at most 64")
        elif f["id"] in known:
            problems.append(f"finding id {f['id']} was already raised")
        if f.get("severity") not in SEVERITIES:
            problems.append(f"finding {f['id']} has severity {f.get('severity')!r}")
        elif f["severity"] in ("P2", "P3"):
            problems.append(f"finding {f['id']} is {f['severity']}: a plan review files P2 and P3 notes in evidence")
        else:
            open_blocking = True
        if "state" in f or "deferredIssue" in f:
            problems.append(f"finding {f['id']} carries a state or deferredIssue: a plan review defers nothing")
    closures = result.get("resolvedFindings", [])
    if not isinstance(closures, list) or not all(isinstance(x, dict) for x in closures):
        problems.append("resolvedFindings must be a list of objects")
        closures = []
    for x in closures:
        if x.get("state") not in (*PLAN_REVIEW_CLOSED, "open"):
            problems.append(f"resolvedFindings entry {x.get('id')!r} has state {x.get('state')!r}: closed, fixed, "
                            "withdrawn or open")
        if x.get("id") not in carried:
            problems.append(f"resolvedFindings entry {x.get('id')!r} isn't an open finding this lens raised earlier")
        if not isinstance(x.get("resolution"), str) or not x["resolution"].strip():
            problems.append(f"resolvedFindings entry {x.get('id')!r} needs a resolution")
        if x.get("state") == "open":
            open_blocking = True
    missing = carried - {x.get("id") for x in closures}
    if missing:
        problems.append(f"resolvedFindings leaves out carried finding(s) {sorted(missing)}")
    if result.get("outcome") == "aligned" and open_blocking:
        problems.append("outcome is aligned but it raises an open P0/P1 finding")
    return problems


def plan_review_reduce(args) -> None:
    c = plan_review_config(args)
    folder = plan_review_dir(c)
    rounds = plan_review_rounds(c)
    n = len(rounds) + 1
    meta = load_json(folder / f"round-{n}.meta.json")
    if meta is None:
        if rounds:
            plan_review_summary(c, rounds)
            return
        raise Stop(f"no plan review round is prepared: run {plan_review_command(c, 'prepare')} first")
    subject = plan_review_subject(c)
    opened = plan_review_open(rounds)
    known = {f["id"]: e["lens"] for r in rounds for e in r["entries"] for f in e["result"].get("findings", [])}
    rel, graph = release_tuple(), graph_digest()
    entries, digests, problems = [], {}, []
    for lens in meta["lenses"]:
        short = c.lenses[lens][0]
        path = result_path(folder, n, short)
        if not path.exists():
            problems.append(f"{lens}: missing {path}; save its reviewer's final JSON there")
            continue
        raw = path.read_bytes()
        digests[lens] = hashlib.sha256(raw).hexdigest()
        try:
            result = json.loads(re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.decode()))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            problems.append(f"{lens}: {path.name} is not JSON ({error})")
            continue
        carried = {f["id"] for f in opened.values() if f["lens"] == lens}
        problems += [f"{lens}: {p}" for p in plan_check_result(result, subject=subject, lens=lens, short=short, n=n,
                                                                meta=meta, carried=carried, known=known)]
        entries.append({"lens": lens, "binding": {"subjectRef": subject, "candidateRef": meta["candidate"], "round": n,
                                                  "release": rel, "graphSha256": graph, "candidateTree": meta["tree"]},
                        "state": "obtained", "independence": {"status": "unverified"}, "result": result})
    if problems:
        raise Stop("results that don't match their briefs:\n  " + "\n  ".join(problems)
                   + "\n(re-run only those lenses with the same brief, saving over their results)")
    failed = []
    for lens in meta["lenses"]:
        checkout = folder / "checkouts" / lens
        problem = plan_checkout_problem(checkout, meta["candidate"], meta["tree"])
        if problem:
            failed.append(f"{lens}: its checkout {checkout} failed the end identity check: it {problem}")
    if failed:
        raise Stop("the round fails; its checkouts are kept for inspection:\n  " + "\n  ".join(failed)
                   + f"\nPrepare it again with {plan_review_command(c, 'prepare')} --restart")
    after = plan_review_open([*rounds, {"entries": entries}])
    aligned = all(e["result"]["outcome"] == "aligned" for e in entries) and not after
    outcome = {"round": n, "outcome": "aligned" if aligned else "unresolved", "candidate": meta["candidate"],
               "tree": meta["tree"], "lenses": meta["lenses"], "startedAt": meta["startedAt"], "finishedAt": now(),
               "startChecks": meta.get("startChecks", {}), "resultDigests": digests,
               "open": list(after.values()), "entries": entries}
    (folder / f"round-{n}.json").write_text(json.dumps(outcome, indent=1) + "\n")
    for lens in meta["lenses"]:
        remove_checkout(folder / "checkouts" / lens)
    plan_review_summary(c, [*rounds, outcome])
    if getattr(c, "plan_store", None):
        if aligned:
            print(f"  {write_plan_review_record(c, [*rounds, outcome])}")
    elif (c.state / "state.json").exists():
        print(f"  {rebuild_plan_quietly(c)}")


def plan_review_summary(c, rounds: list[dict]) -> None:
    outcome = rounds[-1]
    n = outcome["round"]
    if outcome["outcome"] == "aligned":
        print(f"{plan_review_name(c)} plan review round {n}: every lens aligned; the plan is ready for the operator's "
              "approval")
        return
    count = len(outcome["open"])
    print(f"{plan_review_name(c)} plan review round {n}: changes requested; {count} open finding"
          f"{'' if count == 1 else 's'}")
    for f in outcome["open"]:
        print(f"  open {f['severity']} {f['id']} ({f['lens']}): {f['summary']}")
    if n > 1:
        before = len(rounds[-2]["open"])
        raised = any(e["result"].get("findings") for e in outcome["entries"])
        if raised and count >= before:
            print(f"The plan review isn't converging: round {n} raised a new finding and leaves {count} open, no fewer "
                  f"than round {n - 1}'s {before}. The operator may end it: {plan_review_command(c, 'end')} --reason "
                  "TEXT. Nothing ends it on its own.")
    print(f"  Revise the plan for the open findings, commit, then: {plan_review_command(c, 'prepare')}")


def plan_review_end(args) -> None:
    c = plan_review_config(args)
    reason = args.reason.strip()
    if not reason or len(reason) > PLAN_REVIEW_REASON or re.search(r"[\x00-\x1f\x7f-\x9f  ]", reason):
        raise Stop(f"--reason must be one line of 1 to {PLAN_REVIEW_REASON} characters")
    folder = plan_review_dir(c)
    rounds = plan_review_rounds(c)
    if not rounds:
        raise Stop("the plan review has no reduced round to end")
    if plan_review_ended(c):
        raise Stop(f"the plan review was already ended ({folder / 'ended.json'})")
    if rounds[-1]["outcome"] == "aligned":
        raise Stop(f"round {rounds[-1]['round']} ended with every lens aligned; there is no open review to end")
    if (folder / f"round-{len(rounds) + 1}.meta.json").exists():
        raise Stop(f"round {len(rounds) + 1} is prepared; reduce it first, then end the review")
    (folder / "ended.json").write_text(json.dumps({"at": now(), "round": rounds[-1]["round"], "reason": reason},
                                                  indent=2) + "\n")
    print(f"{plan_review_name(c)} plan review ended after round {rounds[-1]['round']}: {reason}")
    if getattr(c, "plan_store", None):
        print(f"  {write_plan_review_record(c, rounds)}")
    elif (c.state / "state.json").exists():
        print(f"  {rebuild_plan_quietly(c)}")


# A standalone plan's review record, `<plan stem>.review.json` beside the plan: the shape the hand-written records in a
# repository's plans already have, with what the store adds. One validator checks both.

PLAN_REVIEW_RECORD = "plan-review-evidence/1"
PLAN_REVIEW_RECORD_WRITER = "run-delivery plan-review"
PLAN_REVIEW_OUTCOMES = {"aligned": "unanimous-alignment", "ended": "ended-without-alignment"}


def plan_review_record_path(c) -> Path:
    return c.root / c.plan.with_name(c.plan.stem + ".review.json")


def plan_review_record(c, rounds: list[dict]) -> dict:
    """The record of a review that aligned or was ended, from its store alone."""
    last = rounds[-1]
    ended = plan_review_ended(c)
    plan = c.plan.as_posix()
    reviewed = subprocess.run(["git", "show", f"{last['candidate']}:{plan}"], cwd=c.root, capture_output=True,
                              check=True).stdout  # the blob's own bytes, with no newline translation
    folder = plan_review_dir(c)
    reviews = []
    for lens in last["lenses"]:
        n, entry = next((r["round"], e) for r in reversed(rounds) for e in r["entries"] if e["lens"] == lens)
        raw = result_path(folder, n, c.lenses[lens][0]).read_bytes()
        reviews.append({"lens": lens, "round": n, "findings": entry["result"].get("findings", []),
                        "result": entry["result"], "resultSha256": hashlib.sha256(raw).hexdigest()})
    count = len(rounds)
    risks = [f"{count} review round{'' if count == 1 else 's'}; findings open after each round: "
             f"{', '.join(str(len(r['open'])) for r in rounds)}."]
    if ended:
        risks.append(f"Ended without alignment after round {ended['round']}: {ended['reason']}")
        risks += [f"Open {f['severity']} {f['id']} ({f['lens']}): {f['summary']}" for f in last["open"]]
    current = c.root / c.plan
    if not current.is_file() or current.read_bytes() != reviewed:
        risks.append(f"The plan's current text differs from round {last['round']}'s candidate {last['candidate'][:12]}; "
                     "this record covers the reviewed text.")
    record = {"schemaVersion": PLAN_REVIEW_RECORD, "writtenBy": PLAN_REVIEW_RECORD_WRITER, "plan": plan,
              "planSha256": hashlib.sha256(reviewed).hexdigest(), "planCommit": last["candidate"],
              "outcome": PLAN_REVIEW_OUTCOMES["ended" if ended else "aligned"], "requiredLenses": last["lenses"],
              "rounds": [{"round": r["round"], "candidate": r["candidate"], "outcome": r["outcome"],
                          "startedAt": r["startedAt"], "finishedAt": r["finishedAt"],
                          "open": [f["id"] for f in r["open"]]} for r in rounds],
              "reviews": reviews, "residualRisks": risks}
    if ended:
        record["ended"] = {"round": ended["round"], "reason": ended["reason"], "at": ended["at"]}
    return record


def write_plan_review_record(c, rounds: list[dict]) -> str:
    """Write the review's record beside the plan, unless a hand-written one is there; says which."""
    path = plan_review_record_path(c)
    shown = path.relative_to(c.root).as_posix()
    existing = load_json(path) if path.is_file() else None
    if path.exists() and not (isinstance(existing, dict) and existing.get("writtenBy") == PLAN_REVIEW_RECORD_WRITER):
        return f"kept the hand-written review record {shown}; the store's record wasn't written"
    record = plan_review_record(c, rounds)
    problems = plan_review_record_problems(record)
    if problems:
        raise Stop(f"the review record for {shown} fails its own check:\n  " + "\n  ".join(problems))
    path.write_text(json.dumps(record, indent=2) + "\n")
    return f"wrote the review record {shown}; commit it with the plan"


def plan_review_record_problems(record: object) -> list[str]:
    """What's wrong with a `plan-review-evidence/1` record, hand-written or generated, as messages; empty when valid."""
    if not isinstance(record, dict):
        return ["the record must be a JSON object"]
    problems = []
    if record.get("schemaVersion") != PLAN_REVIEW_RECORD:
        problems.append(f"schemaVersion must be {PLAN_REVIEW_RECORD}")
    plan = record.get("plan")
    if (not isinstance(plan, str) or not plan.strip() or plan.startswith("/") or "\\" in plan
            or ".." in plan.split("/")):
        problems.append("plan must be the plan's path inside the repository, with / separators")
    if not isinstance(record.get("planSha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["planSha256"]):
        problems.append("planSha256 must be 64 lowercase hex digits")
    outcome = record.get("outcome")
    if outcome not in PLAN_REVIEW_OUTCOMES.values():
        problems.append(f"outcome must be one of {', '.join(PLAN_REVIEW_OUTCOMES.values())}")
    lenses = record.get("requiredLenses")
    if (not isinstance(lenses, list) or not 1 <= len(lenses) <= 12 or len(set(map(str, lenses))) != len(lenses)
            or not all(isinstance(lens, str) and lens.strip() for lens in lenses)):
        problems.append("requiredLenses must be 1 to 12 different lens names")
        lenses = None
    reviews = record.get("reviews")
    if not isinstance(reviews, list) or not all(isinstance(r, dict) for r in reviews):
        problems.append("reviews must be a list of objects, one per required lens")
        reviews = []
    elif lenses is not None and sorted(map(str, (r.get("lens") for r in reviews))) != sorted(lenses):
        problems.append("reviews must hold exactly one review for each required lens")
    risks = record.get("residualRisks")
    if not isinstance(risks, list) or not all(isinstance(r, str) and r.strip() for r in risks):
        problems.append("residualRisks must be a list of non-empty strings")
    for review in reviews:
        lens = review.get("lens")
        findings = review.get("findings")
        if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
            problems.append(f"{lens}: findings must be a list of objects")
            continue
        if findings and outcome == PLAN_REVIEW_OUTCOMES["aligned"]:
            problems.append(f"{lens}: a review with findings contradicts outcome unanimous-alignment")
    ended = record.get("ended")
    if outcome == PLAN_REVIEW_OUTCOMES["ended"]:
        if not isinstance(ended, dict) or not isinstance(ended.get("reason"), str) or not ended["reason"].strip():
            problems.append("ended must give the operator's reason when the outcome is ended-without-alignment")
    elif ended is not None:
        problems.append("ended belongs only to outcome ended-without-alignment")
    rounds = record.get("rounds")
    if rounds is None:  # a hand-written record names no rounds; what follows is the store's part
        return problems
    if (not isinstance(rounds, list) or not rounds or not all(isinstance(r, dict) for r in rounds)
            or [r.get("round") for r in rounds] != list(range(1, len(rounds) + 1))):
        return problems + ["rounds must be the review's rounds, numbered from 1 with none skipped"]
    outcomes = [r.get("outcome") for r in rounds]
    # An amendment reviews on after an aligned round, so an earlier round may be aligned; the last decides the outcome.
    if any(o not in ("unresolved", "aligned") for o in outcomes[:-1]) or outcomes[-1] != (
            "aligned" if outcome == PLAN_REVIEW_OUTCOMES["aligned"] else "unresolved"):
        problems.append("rounds: each round is aligned or unresolved, and the last must agree with outcome")
    if not all(isinstance(r.get("candidate"), str) and re.fullmatch(r"[0-9a-f]{40,64}", r["candidate"]) for r in rounds):
        problems.append("rounds: each round's candidate must be a full commit id")
    if record.get("planCommit") != rounds[-1].get("candidate"):
        problems.append("planCommit must be the last round's candidate")
    if isinstance(ended, dict) and ended.get("round") != len(rounds):
        problems.append("ended.round must be the last round")
    for review in reviews:
        lens, result, n = review.get("lens"), review.get("result"), review.get("round")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(rounds):
            problems.append(f"{lens}: round must be one of the review's rounds")
        if not isinstance(result, dict) or result.get("lensId") != lens or result.get("round") != n:
            problems.append(f"{lens}: result must be this lens's own result from its round (lensId and round)")
        elif result.get("findings", []) != review.get("findings"):
            problems.append(f"{lens}: findings must be its result's findings")
        if not isinstance(review.get("resultSha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", review["resultSha256"]):
            problems.append(f"{lens}: resultSha256 must be 64 lowercase hex digits")
    return problems


def plan_review_check(args) -> None:
    """Validate review records; prints one line per record and refuses when any fails."""
    failed = False
    for given in args.records:
        path = Path(given)
        try:
            record = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            print(f"{path}: can't be read as JSON ({error})")
            failed = True
            continue
        problems = plan_review_record_problems(record)
        if problems:
            failed = True
            print(f"{path}: not a valid {PLAN_REVIEW_RECORD} record:\n  " + "\n  ".join(problems))
        else:
            print(f"{path}: a valid {PLAN_REVIEW_RECORD} record")
    if failed:
        raise Stop("some review records aren't valid")


# -- lane: gates, build and in-app checks -------------------------------------------------

def write_stamp(artifact: Path, head: str, tree: str) -> None:
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / STAMP).write_text(json.dumps({"head": head, "tree": tree, "builtAt": now()}))


def gate(args) -> None:
    c = cfg()
    key = c.require(args.key)
    if not c.gates:
        raise Stop(f"{c.path} declares no gates")
    head = clean_head()
    tree = git("rev-parse", "HEAD^{tree}")
    git("fetch", "-q", c.remote, c.base_branch)
    results, passed, built = [], True, False
    build_argv = (c.in_app or {}).get("build", {}).get("argv")
    for g in c.gates:
        argv = expand_argv(g["argv"], base=c.base_ref, key=key, delivery=c.delivery)
        started = time.time()
        print(f"gate {g['id']}: {' '.join(shlex.quote(a) for a in argv)}", flush=True)
        code, out = run_group(argv, repo() / g.get("cwd", "."), g.get("timeoutSeconds", 1800))
        seconds = round(time.time() - started)
        tail = out.strip().splitlines()[-15:]
        results.append({"id": g["id"], "argv": argv, "exit": code, "seconds": seconds, "tail": tail})
        print(f"  {'ok' if code == 0 else 'FAILED'} in {seconds}s" + ("" if code == 0 else "\n    " + "\n    ".join(tail)))
        if code != 0:
            passed = False
            break
        # Only the in-app build itself, run from the repository root, writes the artifact the stamp belongs to.
        built = built or (build_argv is not None and g["argv"] == build_argv and g.get("cwd", ".") in (".", ""))
    if clean_head() != head:
        raise Stop("HEAD moved, or a gate left files behind; run the gates again")
    if passed and built:  # the gate built the in-app artifact from this clean head: stamp it, saving a rebuild
        write_stamp(repo() / c.in_app["build"]["artifact"], head, tree)
    (review_dir(key) / "gates.json").write_text(json.dumps(
        {"head": head, "at": now(), "passed": passed, "results": results}, indent=2) + "\n")
    print(f"gates {'passed' if passed else 'FAILED'} at {head[:10]}" + (" (in-app build stamped)" if passed and built else ""))
    if not passed:
        sys.exit(1)


def gates_summary(key: str, root: Path, head: str) -> tuple[str, list[str]]:
    c = cfg()
    if not c.gates:
        if c.flow_raw.get("gates") == "none":
            return f"no gates ({c.name} says none)", []
        return "", ["no gates are declared (the binding's fallback checks are empty); declare them, or set "
                    f"'gates': 'none' in {c.name} if the repository truly has none"]
    path = root / c.work / "review" / key / "gates.json"
    if not path.exists():
        return "", [f"no gate run recorded: run {flow_command()} gate {key}"]
    record = json.loads(path.read_text())
    if record["head"] != head:
        return "", [f"the gates ran at {record['head'][:10]}, not {head[:10]}: run them again"]
    if not record["passed"] or [r["id"] for r in record["results"]] != [g["id"] for g in c.gates]:
        return "", ["the recorded gate run didn't pass every gate"]
    return "gates at " + head[:10] + ": " + ", ".join(f"{r['id']} ok ({r['seconds']}s)" for r in record["results"]), []


def need_in_app() -> dict:
    if not cfg().in_app:
        raise Stop(f"{cfg().path} declares no inApp adapter")
    return cfg().in_app


def build(args) -> None:
    """Build HEAD for the in-app checks: a story's build, or with no key the delivery's integration build."""
    c = cfg()
    # The integration build of the whole delivery goes by the delivery's own key, as the lead's checkout does.
    key = c.require(args.key) if args.key is not None else c.delivery
    spec = need_in_app()["build"]
    head = clean_head()
    tree = git("rev-parse", "HEAD^{tree}")
    artifact = repo() / spec["artifact"]
    (artifact / STAMP).unlink(missing_ok=True)
    code, out = run_group(expand_argv(spec["argv"], key=key), repo(), spec.get("timeoutSeconds", 1800))
    if code != 0:
        raise Stop("the build failed:\n" + "\n".join(out.strip().splitlines()[-20:]))
    if clean_head() != head:
        raise Stop("HEAD moved or the build left files behind; build again")
    write_stamp(artifact, head, tree)
    print(f"built {head[:10]} into {artifact}")


def port_open(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family) as s:
                s.settimeout(0.5)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def port_holders(port: int) -> list[tuple[int, str]]:
    """The processes listening on `port`, as (pid, command line); empty when none can be seen (or lsof is missing)."""
    try:
        listing = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], check=False).stdout
    except OSError:
        return []
    holders = []
    for pid in sorted({int(line) for line in listing.split() if line.isdigit()}):
        try:
            command = run(["ps", "-o", "command=", "-p", str(pid)], check=False).stdout.strip()
        except OSError:
            command = ""
        holders.append((pid, command or "command unknown"))
    return holders


def short_command(command: str) -> str:
    """A long command keeps its head (the program) and its tail (the arguments, which tell one server from another)."""
    return command if len(command) <= 160 else f"{command[:40]} ... {command[-110:]}"


def port_busy(port: int) -> str:
    """Why the in-app port can't be used: who listens on it, outside the lease, and how to release it."""
    holders = port_holders(port)
    if not holders:
        return f"something else is listening on {port}, outside the in-app lease (its process isn't visible here); stop it first"
    named = ", ".join(f"pid {pid} ({short_command(command)})" for pid, command in holders)
    pids = " ".join(str(pid) for pid, _ in holders)
    return (f"something else is listening on {port}, outside the in-app lease: {named}. A build served by hand doesn't "
            f"hold the lease, so app-check can't use the port: stop it (kill {pids}) and run again")


# How long the serve has to start listening.
SERVE_READY_SECONDS = 20
# How long a serve that lost its port to another listener is given to exit, so its refusal can name the exit, or to
# take the port once that listener lets it go.
SERVE_SETTLE_SECONDS = 5
# Where the host can't name a port's listeners, how long the serve must stay running once the port answers to be taken
# as the listener: a serve that lost the bind dies of it well within this.
SERVE_UNNAMED_SECONDS = 2


def serve_log(ia: dict) -> Path:
    """Where the serve's output goes: beside the lease's lock, written by one holder at a time, as the check's spec is."""
    lock = Path(expand(ia["lock"]))
    return lock.with_name(lock.name + ".serve.log")


def start_serve(ia: dict, dist: Path, port: int, lease: "Lease") -> subprocess.Popen:
    """Start the serve adapter on `dist` under `lease` and wait until `port` answers from it. Refuses, having stopped
    what it started, unless the listener is this serve's: a serve that loses the port to another listener (another run
    on the same port, under a lease of its own) exits or never listens, and the port's answer is then someone else's."""
    log = serve_log(ia)
    with open(log, "w") as out:
        server = subprocess.Popen(expand_argv(ia["serve"]["argv"], dir=str(dist), port=str(port)),
                                  stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        lease.track(server.pid)
        ready = time.monotonic() + SERVE_READY_SECONDS
        while time.monotonic() < ready:
            if port_open(port) or server.poll() is not None:
                break
            time.sleep(0.2)
        problem = serve_problem(server, port, log)
    except BaseException:
        stop_group(server)
        raise
    if problem:
        stop_group(server)
        raise Stop(problem)
    return server


def serve_problem(server: subprocess.Popen, port: int, log: Path) -> str | None:
    """Why the listener on `port` isn't `server`'s, or None when it is: a listener in the serve's own process group
    (its child, for a serve that runs its server as one), or, where the host can't name listeners, a serve that stays
    running a moment after the port answers."""
    def group(pid: int) -> int | None:
        try:
            return os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            return None

    def output() -> str:
        try:
            tail = log.read_text(errors="replace").strip()[-600:]
        except OSError:
            tail = ""
        return f"; its output is in {log}" + (f", ending:\n{tail}" if tail else "")

    if not port_open(port):
        if server.poll() is not None:
            return f"the serve command exited with code {server.returncode} before it listened on port {port}" + output()
        return f"the serve command didn't start listening on port {port}" + output()
    holders = port_holders(port)
    if any(group(pid) == server.pid for pid, _ in holders):
        return None
    if not holders:
        settled = time.monotonic() + SERVE_UNNAMED_SECONDS
        while server.poll() is None and time.monotonic() < settled:
            time.sleep(0.1)
        if server.poll() is None:
            return None
    # Give the serve a moment: it exits of the lost bind, so its refusal can name the exit, or it takes the port once
    # the other listener lets it go, and is then this run's after all.
    deadline = time.monotonic() + SERVE_SETTLE_SECONDS
    while server.poll() is None and time.monotonic() < deadline:
        holders = port_holders(port) or holders
        if any(group(pid) == server.pid for pid, _ in holders):
            return None
        time.sleep(0.2)
    named = ", ".join(f"pid {pid} ({short_command(command)})" for pid, command in holders) or "a process this host can't name"
    state = (f"exited with code {server.returncode}" if server.poll() is not None
             else f"is still running (pid {server.pid}) and isn't listening")
    return (f"port {port} answered, but not from this run's serve: {named} took the port while the serve was starting, "
            f"so the check would have run against that server. This run's serve {state}" + output() + ". Another run "
            "using the same port under a lease of its own does this: give each its own port, or have them share one lock")


class Lease:
    """An in-app lease: an OS lock held for the whole check (released if we die). It records the process groups it
    starts, so the next holder can stop a server or browser that a killed holder left behind."""

    def __init__(self, lock: Path, port: int, owner: str, wait: int):
        lock.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(lock, "a+")
        deadline = time.time() + wait
        while True:
            try:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    self.file.seek(0)
                    holder = self.file.read().strip() or "unknown holder"
                    self.file.close()  # a lease given up holds nothing, not even its file
                    raise Stop(f"the in-app lease is held ({holder}); gave up after {wait}s")
                time.sleep(min(3, max(0.1, wait / 10)))
        self.file.seek(0)
        for group in re.findall(r"pgid (\d+)", self.file.read()):  # we hold the lock, so their owner is gone
            try:
                os.killpg(int(group), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        if port_open(port):
            time.sleep(1)
        self.owner = f"{owner} pid {os.getpid()} since {now()}"
        self.groups: list[int] = []
        self.write()

    def track(self, pid: int) -> None:
        self.groups.append(pid)
        self.write()

    def write(self, extra: str = "") -> None:
        self.file.seek(0)
        self.file.truncate()
        self.file.write(self.owner + "".join(f" pgid {g}" for g in self.groups) + (f" {extra}" if extra else "") + "\n")
        self.file.flush()

    def close(self) -> None:
        self.groups = []
        self.write("released")
        fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


def rules(report: dict, *, mode: str, spec: dict, pinned: list[str], build_head: str | None,
          allowed_errors: set[str], error_pattern: str) -> dict[str, bool]:
    """The in-app rules for one check. Pure, so the self-test can feed it fixtures."""
    holds, fails = spec.get("holds", []), spec.get("fails", [])
    evals, after = report.get("evals", []), report.get("afterEvals", [])
    served = (report.get("build") or {}).get("head")
    r = {
        "the app was ready (signed in, page rendered)": report.get("ready") is True,
        "served the stamped build": bool(build_head) and served == build_head,
        "no page errors": not report.get("pageErrors"),
        "no new console errors": not [e for e in report.get("consoleErrors", [])
                                      if re.search(error_pattern, e) and e not in allowed_errors],
        "at least one eval": bool(evals),
        "every eval returned true": all(e.get("result") is True for e in [*evals, *after]),
    }
    if report.get("afterForFound") is False:
        r["after-for found"] = False
    first_eval = min((e["evalAt"] for e in evals), default=None)
    if mode == "hold":
        held = [e for e in report.get("held", []) if not e.get("afterRelease")]  # later re-fetches aren't held
        r["hold mode: holds given, no fails"] = bool(holds) and not fails
        r["every hold is a pinned pattern"] = all(h in pinned for h in holds)
        r["every hold held a request before the evals"] = bool(first_eval) and all(
            any(e.get("regex") == h and e["heldAt"] <= first_eval for e in held) for h in holds)
        r["at least one after-eval"] = bool(after)
        held_at = max((e["heldAt"] for e in held), default=None)
        released = min((e["releasedAt"] for e in held if e.get("releasedAt")), default=None)
        r["evals ran while held"] = bool(held_at and released) and all(held_at <= e["evalAt"] <= released for e in evals)
        r["no alert appeared during the hold (alertsSeen reported and empty)"] = (
            isinstance(report.get("alertsSeen"), list) and not report["alertsSeen"])
    elif mode == "fail":
        failed = report.get("failed", [])
        r["fail mode: fails given, no holds"] = bool(fails) and not holds
        r["every fail is a pinned pattern"] = all(f in pinned for f in fails)
        r["every fail failed a request before the evals"] = bool(first_eval) and all(
            any(e.get("regex") == f and e.get("at") and e["at"] <= first_eval for e in failed) for f in fails)
        r["failures were error statuses"] = all(int(e.get("status", 0)) >= 400 for e in failed)
    elif mode == "loaded":
        r["loaded mode: no holds or fails"] = not holds and not fails
    else:
        r[f"known mode ({mode})"] = False
    return r


def refuse_secrets(passthrough: list[str]) -> None:
    """Sign-in values never travel as adapter arguments: those are recorded in logs, records and evidence."""
    for arg in passthrough:
        name = arg.lstrip("-").split("=", 1)[0] if arg.startswith("-") else (arg.split("=", 1)[0] if "=" in arg else "")
        if name and SECRET_ARGUMENT.search(name):
            raise Stop(f"adapter argument {name!r} looks like a sign-in value: sign-in values come only from the "
                       "environment or the adapter itself, never from arguments, which are recorded")


def run_in_app(dist: Path, spec: dict, passthrough: list[str], owner: str, lock_wait: int | None) -> dict:
    """Serve `dist` and run one check spec against it, under the in-app lease. Returns the check's report."""
    ia = need_in_app()
    port = ia["port"]
    lock = Path(expand(ia["lock"]))
    lease = Lease(lock, port, owner, lock_wait or ia.get("lockWaitSeconds", 480))
    server = check = None
    # Beside the lease's lock, under the lease: one holder at a time writes it, so a holder that is killed before it
    # can remove it leaves one file the next holder overwrites, never a file per check in the system's temp folder.
    spec_file = lock.with_name(lock.name + ".spec.json")
    try:
        spec_file.write_text(json.dumps(spec))
        if port_open(port):
            raise Stop(port_busy(port))
        server = start_serve(ia, dist, port, lease)
        check = subprocess.Popen(expand_argv(ia["check"]["argv"], spec=str(spec_file), port=str(port), args=passthrough),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        lease.track(check.pid)
        timeout = ia.get("checkTimeoutSeconds", 300)
        try:
            out, err = check.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Stop(f"the check took longer than {timeout}s and was stopped")
        if check.returncode != 0:
            raise Stop(f"the check failed: {(err or out).strip()[-500:]}")
        try:
            report = json.loads(out)
        except json.JSONDecodeError:
            raise Stop(f"the check printed no JSON report: {out.strip()[-300:]}")
    finally:
        for proc in (check, server):  # wait for both to exit before the lease is released
            stop_group(proc)
        spec_file.unlink(missing_ok=True)  # still under the lease: once it is released, the path is the next holder's
        lease.close()
    return report


def app_check(args) -> None:
    c = cfg()
    key = c.require(args.key)
    ia = need_in_app()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.name) or args.name.endswith("-baseline"):
        raise Stop("name the check with lowercase letters, digits and dashes (no -baseline suffix)")
    if args.baseline and args.probe:
        raise Stop("--baseline and --probe don't combine")
    if args.probe and (args.mode != "loaded" or args.hold or args.fail):
        raise Stop("a probe is a plain load (--mode loaded, no --hold or --fail): it looks things up, it isn't a check")
    passthrough = list(args.passthrough)
    smuggled = [a for a in passthrough if a.split("=")[0] in SPEC_FLAGS]
    if smuggled:
        raise Stop(f"{smuggled} belong to the check spec: pass them to app-check itself, not after --")
    refuse_secrets(passthrough)
    spec = {"path": args.path, "holds": args.hold, "fails": args.fail, "clicks": args.click, "evals": args.eval,
            "afterEvals": args.after_eval, "for": args.for_, "wait": args.wait, "afterFor": args.after_for}
    if args.baseline:
        if not ia.get("baseline"):
            raise Stop(f"{c.path} declares no inApp.baseline build")
        dist, head, tree = Path(expand(ia["baseline"])), None, None
    else:
        dist = repo() / ia["build"]["artifact"]
        head = clean_head() if not args.probe else git("rev-parse", "HEAD")
        tree = git("rev-parse", "HEAD^{tree}")
    stamp = dist / STAMP
    built = json.loads(stamp.read_text()) if stamp.exists() else {}
    if not args.baseline and not args.probe and built.get("head") != head:
        raise Stop(f"the build in {dist} isn't from HEAD {head[:10]} (built: {str(built.get('head'))[:10]}); "
                   f"run {flow_command()} build {key} first")
    if not built.get("head"):
        raise Stop(f"{dist} has no {STAMP}; build it with {flow_command()} build (or stamp the baseline)")
    pinned = c.stories[key].get("pinned", [])
    out_dir = review_dir(key) / ("probes" if args.probe else "app-checks")
    out_dir.mkdir(exist_ok=True)
    name = args.name + ("-baseline" if args.baseline else "")
    pattern = ia.get("consoleErrorPattern", ".")
    allowed: set[str] = set()
    baseline_record = out_dir / f"{args.name}-baseline.json"
    if not args.baseline and args.mode != "fail" and baseline_record.exists():
        base = json.loads(baseline_record.read_text())
        # Only a plain load of the same page can excuse an error: never a hold or fail run, which provoke them.
        if base["mode"] == "loaded" and base["spec"].get("path") == args.path and base.get("passthrough") == passthrough:
            allowed = {e for e in base["report"].get("consoleErrors", []) if re.search(pattern, e)}
    report = run_in_app(dist, spec, passthrough, f"{key} {name}", args.lock_wait)
    if args.probe:
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps({"story": key, "name": name, "at": now(), "spec": spec, "report": report}, indent=2) + "\n")
        print(f"probe {key} {name} (not evidence) -> {path}")
        for e in [*report.get("evals", []), *report.get("afterEvals", [])]:
            print(f"  {str(e.get('expr'))[:70]} -> {json.dumps(e.get('result', e.get('error')))[:300]}")
        return
    verdict = rules(report, mode=args.mode, spec=spec, pinned=pinned, build_head=built.get("head"),
                    allowed_errors=allowed, error_pattern=pattern)
    passed = all(verdict.values())
    record = {"story": key, "name": name, "mode": args.mode, "baseline": args.baseline, "head": head, "tree": tree,
              "at": now(), "dist": str(dist), "spec": spec, "passthrough": passthrough, "pinned": pinned,
              "rules": verdict, "pass": passed, "report": report}
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    # Every run, lead-side, so a removed record or an eval changed after failing is visible to the lens and the lead.
    append_log("app-checks", key, {"name": name, "mode": args.mode, "pass": passed, "head": head, "record": digest(path),
                                   "spec": spec_digest(spec, passthrough), "specValue": spec, "passthrough": passthrough})
    print(f"{'PASS' if passed else 'FAIL'} {key} {name} ({args.mode}) -> {path}")
    for rule, ok in verdict.items():
        print(f"  {'ok ' if ok else 'NO '} {rule}")
    for e in [*report.get("evals", []), *report.get("afterEvals", [])]:
        extra = f" (first {json.dumps(e.get('firstResult'))}, {e['polls']} polls)" if "polls" in e else ""
        print(f"  eval {json.dumps(e.get('result', e.get('error')))[:100]}{extra} <- {str(e.get('expr'))[:80]}")
    if not report.get("ready"):
        print("  the app wasn't ready (signed out, or the page never rendered): if signed out, ask the lead")
    if not passed:
        sys.exit(1)


def needs_video(key: str) -> bool:
    return cfg().stories[key].get("evidence") == "video"


def stamped(dist: Path) -> dict:
    stamp = dist / STAMP
    built = json.loads(stamp.read_text()) if stamp.exists() else {}
    if not built.get("head"):
        raise Stop(f"{dist} has no {STAMP}; build it with {flow_command()} build (or stamp it)")
    return built


def page_crop(clip: Path) -> str:
    """A crop to the page's area. A browser's recording can be taller than what it painted, padded with flat grey
    rows at the bottom; find them on the last second (grey to black, everything else to white, then cropdetect)."""
    probe = run(["ffmpeg", "-hide_banner", "-sseof", "-1", "-i", str(clip), "-vf",
                 "lutyuv=y='if(between(val,118,138),0,255)':u=128:v=128,cropdetect=limit=16:round=2:reset=0",
                 "-f", "null", "-"], check=False)
    found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", probe.stderr)
    size = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                "-of", "csv=p=0", str(clip)]).stdout.strip().split(",")
    if not found:
        return ""
    w, h, x, y = map(int, found[-1])
    if (w, h) == (int(size[0]), int(size[1])) or h < int(size[1]) // 3:
        return ""
    return f"crop={w}:{h}:{x}:{y},"


def common_crop(clips: list[Path]) -> tuple[str, ...]:
    """The same crop for every clip (the shortest painted area), so they stack side by side at one height."""
    found = []
    for clip in clips:
        crop = page_crop(clip)
        size = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                    "-of", "csv=p=0", str(clip)]).stdout.strip().split(",")
        found.append(int(crop.split(":")[1]) if crop else int(size[1]))
        width = int(size[0])
    height = min(found) // 2 * 2
    return tuple(f"crop={width}:{height}:0:0," for _ in clips)


def side_by_side(before: Path, after: Path, out: Path, labels: tuple[str, str], caption: str,
                 crops: tuple[str, str] = ("", "")) -> None:
    """One clip: before on the left, after on the right, each labelled, both held on their last frame until the
    longer one ends."""
    if not shutil.which("ffmpeg"):
        raise Stop("ffmpeg isn't installed; it stitches the before and after recordings")
    # Labels are burned in only where ffmpeg has drawtext (it needs freetype); the evidence page labels them anyway.
    texts = "drawtext" in run(["ffmpeg", "-hide_banner", "-filters"], check=False).stdout
    esc = lambda t: t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
    label = lambda t: (f",drawtext=text='{esc(t)}':x=16:y=16:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.6:"
                       "boxborderw=8") if texts else ""
    top = (f",pad=iw:ih+56:0:56:color=0x111111,drawtext=text='{esc(caption)}':x=16:y=16:fontsize=24:fontcolor=white"
           if texts else "")
    graph = (f"[0:v]{crops[0]}scale=960:-2,tpad=stop_mode=clone:stop_duration=30{label(labels[0])},pad=iw+8:ih:0:0:color=0x111111[a];"
             f"[1:v]{crops[1]}scale=960:-2,tpad=stop_mode=clone:stop_duration=30{label(labels[1])}[b];"
             f"[a][b]hstack=inputs=2{top}[v]")
    longest = max(float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]).stdout
                        or 0) for f in (before, after))
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(before), "-i", str(after), "-filter_complex", graph,
         "-map", "[v]", "-t", f"{longest + 0.5:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28",
         "-movflags", "+faststart", str(out)])


def app_video(args) -> None:
    """Record the same scripted run on the baseline build (before) and on this build (after), and stitch them."""
    c = cfg()
    key = c.require(args.key)
    problem = video_tools_problem()
    if problem:
        raise Stop(problem)
    ia = need_in_app()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.name):
        raise Stop("name the video with lowercase letters, digits and dashes")
    if not ia.get("baseline"):
        raise Stop(f"{c.path} declares no inApp.baseline build to record the 'before' on")
    if args.mode == "hold" and not args.hold or args.mode == "fail" and not args.fail:
        raise Stop(f"a {args.mode} video needs at least one --{args.mode} pattern")
    passthrough = list(args.passthrough)
    smuggled = [a for a in passthrough if a.split("=")[0] in SPEC_FLAGS]
    if smuggled:
        raise Stop(f"{smuggled} belong to the spec: pass them to app-video itself, not after --")
    refuse_secrets(passthrough)
    before_dist = Path(expand(ia["baseline"]))
    after_dist = Path(expand(args.after_build)) if args.after_build else repo() / ia["build"]["artifact"]
    before_built, after_built = stamped(before_dist), stamped(after_dist)
    if args.after_build:
        head = after_built["head"]
    else:
        head = clean_head()
        if after_built["head"] != head:
            raise Stop(f"the build in {after_dist} isn't from HEAD {head[:10]}; run {flow_command()} build {key} first")
    folder = review_dir(key) / "videos"
    folder.mkdir(exist_ok=True)
    clips, reports = {}, {}
    work = Path(tempfile.mkdtemp(prefix="flow-video-"))
    try:
        for side, dist in (("before", before_dist), ("after", after_dist)):
            # A held request is released after the last step; when the steps end in a click, keep it held for the
            # wait after the click too, so the state the click opens stays on screen long enough to see.
            dwell = [f"new Promise(r => setTimeout(() => r(true), {args.wait}))"] if args.mode == "hold" and args.click else []
            spec = {"path": args.path, "holds": args.hold if args.mode == "hold" else [],
                    "fails": args.fail if args.mode == "fail" else [], "clicks": args.click, "evals": dwell,
                    "afterEvals": [], "for": args.for_, "wait": 800 if dwell else args.wait, "afterFor": args.after_for,
                    "video": str(work / side), "settle": args.settle}
            report = run_in_app(dist, spec, passthrough, f"{key} video {args.name} {side}", args.lock_wait)
            problems = []
            # Not `ready`: a page that shows nothing while it loads is exactly what a "before" clip may need to show.
            if not report.get("signedIn", report.get("ready")):
                problems.append("the app wasn't signed in")
            if args.mode == "hold" and not report.get("held"):
                problems.append("no request matched --hold, so the loading state was never shown")
            if args.mode == "fail" and not report.get("failed"):
                problems.append("no request matched --fail, so the failure was never shown")
            if not report.get("video") or not Path(report["video"]).exists():
                problems.append("the check recorded no video (does the adapter support the spec's `video`?)")
            if problems:
                raise Stop(f"the {side} recording isn't evidence: " + "; ".join(problems))
            clips[side], reports[side] = Path(report["video"]), report
        out = folder / f"{args.name}.mp4"
        caption = args.caption or f"{key}: {args.mode} {args.path}"
        side_by_side(clips["before"], clips["after"], out,
                     (f"Before ({before_built['head'][:8]})", f"After ({head[:8]})"), caption,
                     common_crop([clips["before"], clips["after"]]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    record = {"story": key, "name": args.name, "mode": args.mode, "path": args.path, "caption": caption,
              "head": head, "before": before_built["head"], "after": after_built["head"], "at": now(),
              "file": out.name, "digest": digest(out), "held": [h["regex"] for h in reports["after"].get("held", [])],
              "failed": [f["regex"] for f in reports["after"].get("failed", [])]}
    (folder / f"{args.name}.json").write_text(json.dumps(record, indent=2) + "\n")
    append_log("videos", key, {"name": args.name, "head": head, "digest": record["digest"]})
    print(f"recorded {key} {args.name}: before {before_built['head'][:10]} | after {head[:10]} -> {out}")


def story_folders(key: str) -> list[Path]:
    """Where a story's lane evidence lives: its checkout's review folder, the copy kept after cleanup, the lead's."""
    c = cfg()
    return [c.lane_checkout(key) / c.work / "review" / key, evidence_dir(key), repo() / c.work / "review" / key]


def html_escape(text: object) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def latest_clips(key: str) -> dict[str, tuple[dict, Path]]:
    """A story's clips by name, intact ones only; later folders win (the lead's recordings on the merged base)."""
    clips = {}
    for folder in story_folders(key):
        for rec_path in sorted((folder / "videos").glob("*.json")):
            rec = json.loads(rec_path.read_text())
            clip = rec_path.with_name(rec["file"])
            if clip.exists() and digest(clip) == rec["digest"]:
                clips[rec["name"]] = (rec, clip)
    return clips


def evidence_status(keys: list[str]) -> tuple[list[str], list[str]]:
    """Whether the published evidence page carries a clip for every merged story marked for video."""
    c = cfg()
    merged = {q["subject"] for q in load_state()["state"]["queue"] if q.get("stage") == "merged"}
    saved = c.state.parent / "evidence-page.json"
    page = json.loads(saved.read_text()) if saved.exists() else None
    current, problems = [], []
    if page is None:
        problems.append("no evidence page is published yet: lead-evidence page, publish it, then lead-evidence published --url")
    for key in keys:
        if key not in merged:
            current.append(f"{key}: not merged yet, so not expected on the page")
            continue
        clips = latest_clips(key)
        if not clips:
            problems.append(f"{key}: no clip; record one (lead-evidence record {key} --after-build ...)")
            continue
        published = set((page or {}).get("clips", {}).values())
        missing = [name for name, (_, clip) in clips.items() if digest(clip) not in published]
        if page is not None and missing:
            problems.append(f"{key}: clip(s) {missing} aren't on the published page; rebuild and republish it")
        elif page is not None:
            current.append(f"{key}: {len(clips)} clip(s) on the page")
    return current, problems


def evidence_inputs(c, args, keys: list[str]) -> dict:
    """The evidence page's words, its title, intro and per-story notes, kept in the record beside the published page's
    record, so a page rebuilt after the lead's context is gone says what it said. A flag given now replaces what's
    kept; `--note KEY=` with no text removes that story's note."""
    path = c.state.parent / "evidence-notes.json"
    kept = json.loads(path.read_text()) if path.exists() else {}
    notes = dict(kept.get("notes") or {})
    given = {}
    for note in args.note or []:
        key, sep, text = note.partition("=")
        key = key.strip()
        if not sep or not key:
            raise Stop(f"--note takes KEY=TEXT, not {note!r}")
        if key not in c.stories:
            raise Stop(f"--note names {key}, which isn't a story of {c.delivery}")
        given[key] = text.strip()
    for key, text in given.items():
        if text:
            notes[key] = text
        else:
            notes.pop(key, None)
    inputs = {"title": args.title if args.title is not None else kept.get("title"),
              "intro": args.intro if args.intro is not None else kept.get("intro"), "notes": notes}
    if inputs != {k: kept.get(k, {} if k == "notes" else None) for k in ("title", "intro", "notes")}:
        path.write_text(json.dumps({**inputs, "at": now()}, indent=2) + "\n")
        print(f"recorded the page's words in {path}")
    reused = sorted(k for k in keys if k in notes and k not in given)
    if reused:
        print(f"reused the recorded note(s) for {', '.join(reused)}")
    return inputs


def lead_evidence(args) -> None:
    c = cfg()
    keys = [c.require(k) for k in args.keys] if args.keys else [k for k in c.stories if needs_video(k)]
    if args.action == "record":
        if not args.after_build:
            raise Stop("record needs --after-build: a stamped build of the merged delivery base")
        head = stamped(Path(expand(args.after_build)))["head"]
        for key in keys:
            have = {json.loads(p.read_text())["mode"] for p in (review_dir(key) / "videos").glob("*.json")
                    if json.loads(p.read_text())["head"] == head}
            records = []
            for folder in story_folders(key):
                records += [json.loads(p.read_text()) for p in sorted((folder / "app-checks").glob("*.json"))]
            for mode in ("hold", "fail"):
                if mode in have:
                    continue
                rec = next((r for r in records if r.get("mode") == mode and r.get("pass") and not r.get("baseline")
                            and not r.get("passthrough")), None)
                if not rec:
                    print(f"{key}: no passing {mode} check to record from; skipped")
                    continue
                sp = rec["spec"]
                ns = argparse.Namespace(key=key, name=f"{c.label(key).lower()}-{'loading' if mode == 'hold' else 'failed-load'}",
                                        mode=mode, path=sp["path"], hold=sp.get("holds", []), fail=sp.get("fails", []),
                                        click=sp.get("clicks", []), for_=sp.get("for") if sp.get("clicks") else None,
                                        wait=2500 if mode == "hold" else max(int(sp.get("wait") or 0), 1500),
                                        after_for=None, settle=2500, lock_wait=None, passthrough=[],
                                        after_build=args.after_build,
                                        caption=f"{key} {c.stories[key]['title']}: "
                                                + ("while it loads" if mode == "hold" else "when the load fails"))
                try:
                    app_video(ns)
                except Stop as error:
                    print(f"{key} {mode}: not recorded: {error}")
        return
    if args.action == "published":
        if not args.url:
            raise Stop("published needs --url: the page's address")
        out = Path(expand(args.out))
        clips = {f.name: digest(f) for f in sorted((out / "videos").glob("*.mp4"))}
        if not (out / "index.html").exists() or not clips:
            raise Stop(f"{out} has no built page with clips: run lead-evidence page first")
        words = out / "evidence-notes.json"  # the words this folder's page was built with, not the latest kept ones
        kept = json.loads(words.read_text()) if words.exists() else {}
        record = {"url": args.url, "at": now(), "clips": clips,
                  **{k: kept.get(k, {} if k == "notes" else None) for k in ("title", "intro", "notes")}}
        (c.state.parent / "evidence-page.json").write_text(json.dumps(record, indent=2) + "\n")
        state_tool("journal", "--kind", "decision.recorded", "--summary",
                   f"Evidence page published at {args.url} with {len(clips)} before/after clip(s).", check=False)
        print(f"recorded the evidence page {args.url} ({len(clips)} clips); link it from the epic and each story")
        return
    if args.action == "status":
        current, problems = evidence_status(keys)
        for line in current:
            print(f"  ok: {line}")
        for line in problems:
            print(f"  NO: {line}")
        if problems:
            sys.exit(1)
        print("the evidence page is current")
        return
    words = evidence_inputs(c, args, keys)
    out = Path(expand(args.out))
    (out / "videos").mkdir(parents=True, exist_ok=True)
    doc = load_state()
    prs = {q["subject"]: q.get("changeRequest") for q in doc["state"]["queue"]}
    sections, befores, afters, count = [], set(), set(), 0
    for key in keys:
        clips = latest_clips(key)
        figures = []
        for name, (rec, clip) in sorted(clips.items(), key=lambda kv: (kv[1][0]["mode"] != "hold", kv[0])):
            target = f"videos/{key}-{name}.mp4"
            shutil.copy2(clip, out / target)
            befores.add(rec["before"][:8]); afters.add(rec["head"][:8]); count += 1
            figures.append(f"""    <figure>
      <div class="sides"><span>Before</span><span>After</span></div>
      <video src="{target}" controls muted loop playsinline preload="metadata" aria-label="{html_escape(rec['caption'])}"></video>
      <figcaption><span class="chip">{'Loading' if rec['mode'] == 'hold' else 'Failed load' if rec['mode'] == 'fail' else 'Loaded'}</span>{html_escape(rec['caption'])} · <code>{html_escape(rec['path'])}</code></figcaption>
    </figure>""")
        links = [f'<a href="{html_escape(c.issue_url.format(key=key))}">Ticket</a>']
        if prs.get(key):
            links.append(f'<a href="{html_escape(prs[key])}">Pull request</a>')
        notes = words["notes"]
        body = "\n".join(figures) or (f'    <p class="none">{html_escape(notes[key])}</p>' if key in notes else
                                       '    <p class="none">No recording: this story has no visible loading or failure state to show.</p>')
        sections.append(f"""  <section class="story" id="{key.lower()}">
    <div class="story-head"><span class="key">{key} · {html_escape(c.label(key))}</span><h2>{html_escape(c.stories[key]['title'])}</h2><span class="links">{' '.join(links)}</span></div>
{body}
  </section>""")
    values = {
        "title": html_escape(words["title"] or f"{c.delivery} Evidence"),
        "eyebrow": html_escape(f"{c.delivery} · delivery evidence"),
        "heading": html_escape(c.raw.get("headline", c.delivery)),
        "intro": html_escape(words["intro"] or "Each clip runs the same steps twice: on the base before this delivery (left) and "
                             "on the delivered code (right). Loading clips hold the page's request for a few seconds; "
                             "failure clips make it fail."),
        "before": html_escape(", ".join(sorted(befores)) or "-"), "after": html_escape(", ".join(sorted(afters)) or "-"),
        "stories": "\n".join(sections),
        "footer": html_escape(f"{count} clip(s) across {len(keys)} stories. Recorded with run-delivery app-video."),
    }
    page = re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], (TOOL / "templates/evidence.html").read_text())
    (out / "index.html").write_text(page)
    (out / "evidence-notes.json").write_text(json.dumps(words, indent=2) + "\n")
    print(f"wrote {out / 'index.html'} with {count} clip(s); publish the folder (index.html plus videos/)")


def video_evidence(key: str, root: Path, head: str) -> tuple[list[str], list[str]]:
    """The story's before/after videos, when its delivery entry asks for them: (lines, problems)."""
    if not needs_video(key):
        return [], []
    folder = root / cfg().work / "review" / key / "videos"
    records = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))] if folder.exists() else []
    current = []
    for rec in records:
        ok, _ = record_is_current(rec, head, root)
        clip = folder / rec["file"]
        if ok and clip.exists() and digest(clip) == rec["digest"]:
            current.append(rec)
    if not current:
        return [], [f"{key} needs before/after video evidence: run {flow_command()} app-video {key} <name> ..."]
    return [f"video {r['name']}: {r['mode']} {r['path']} ({r['before'][:8]} -> {r['head'][:8]})" for r in current], []


def record_is_current(rec: dict, head: str, root: Path) -> tuple[bool, str]:
    if rec["head"] == head:
        return True, ""
    done = run(["git", "diff", "--name-only", rec["head"], head, "--", *((cfg().ship or {}).get("paths") or ["."])],
               cwd=root, check=False)
    if done.returncode != 0:
        return False, f"ran at {rec['head'][:10]}, which can't be compared"
    changed = [f for f in done.stdout.splitlines() if f and not re.search(cfg().test_files, f)]
    return (not changed), (f"ran at {rec['head'][:10]}; {len(changed)} shipped file(s) changed since" if changed else "")


def in_app_evidence(key: str, root: Path, head: str, blocked: dict[str, str]) -> tuple[list[str], list[str]]:
    """(lines describing the evidence, problems) for a story's in-app checks at `head`."""
    c = cfg()
    review = root / c.work / "review" / key
    folder = review / "app-checks"
    records = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))] if folder.exists() else []
    current, lines, problems = [], [], []
    for rec in records:
        if rec.get("baseline"):
            lines.append(f"{rec['name']}: baseline {rec['mode']}, {'PASS' if rec['pass'] else 'FAIL'}")
            continue
        fresh, why = record_is_current(rec, head, root)
        state = ("PASS" if rec["pass"] else "FAIL") + ("" if fresh else f", STALE ({why})")
        lines.append(f"{rec['name']}: {rec['mode']}, {state}")
        if (not rec["pass"] or not fresh) and rec["name"] not in blocked:
            problems.append(f"in-app check {rec['name']} is {state}; re-run it or pass --blocked {rec['name']}=<reason>")
        if rec["pass"] and fresh:
            current.append(rec)
    lines += [f"history: {h}" for h in check_history(key, records)]
    reqs = c.requirements(key)
    met = satisfied_requirements(reqs, current)
    # Mode requirements first, then pattern coverage, as each is matched.
    for qi, req in sorted(enumerate(reqs), key=lambda pair: pair[1]["mode"] == "any"):
        label = requirement_label(req)
        if qi not in met and label not in blocked:
            problems.append(f"missing a passing check for {label}; run it or pass --blocked '{label}=<reason>'")
    for label, reason in blocked.items():
        lines.append(f"BLOCKED {label}: {reason}")
    return lines, problems


# -- forge -----------------------------------------------------------------------------------

TRANSIENT_EXIT = 75  # EX_TEMPFAIL: an adapter's transient failure, which a PR-review wait retries


def call_adapter(kind: str, prefix: list[str], args: tuple[str, ...], env: dict[str, str]) -> dict:
    """Run a forge or tracker adapter and read the one JSON object it prints. An adapter that prints nothing has
    failed, whatever its exit code: the refusal names the adapter it ran. Exit 75 is a transient failure (Transient),
    which only a PR-review wait's loop retries."""
    argv = [*map(os.path.expanduser, prefix), *args]
    done = subprocess.run(argv, text=True, capture_output=True, env=env, cwd=repo())
    ran = shlex.join(prefix)
    if done.returncode == TRANSIENT_EXIT:
        said = (done.stderr or done.stdout).strip()[-400:] or "it printed nothing"
        raise Transient(f"{kind} {args[0]} failed transiently (exit {TRANSIENT_EXIT}): {said}; the adapter it ran: {ran}")
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip()[-400:] or "it printed nothing"
        raise Stop(f"{kind} {args[0]} failed: {said}; the adapter it ran: {ran}")
    if not done.stdout.strip():
        raise Stop(f"{kind} {args[0]} printed nothing; the adapter it ran: {ran}")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        raise Stop(f"{kind} {args[0]} printed no JSON: {done.stdout.strip()[-200:]}; the adapter it ran: {ran}")


def forge(*args: str) -> dict:
    f = cfg().forge
    prefix = f.get("argv") or [sys.executable, "-B", str(TOOL / "adapters" / f"forge_{f['adapter']}.py")]
    env = {**os.environ, "FLOW_FORGE_REPOSITORY": f.get("repository", ""), "FLOW_FORGE_TOKEN_ENV": f.get("tokenEnv", "")}
    return call_adapter("forge", prefix, args, env)


def forge_answers(command: str) -> bool:
    f = cfg().forge
    return bool(f.get("argv")) or command in ADAPTER_COMMANDS.get(f.get("adapter", ""), ())


def same_commit(reported: object, full: str) -> bool:
    """Forges may report abbreviated hashes (Bitbucket gives 12 characters)."""
    return isinstance(reported, str) and len(reported) >= 7 and full.startswith(reported)


def remote_head(branch: str) -> str:
    line = git("ls-remote", cfg().remote, f"refs/heads/{branch}")
    return line.split("\t")[0] if line else ""


def pushed(key: str, head: str) -> None:
    branch = cfg().branch(key)
    remote = remote_head(branch)
    if remote != head:
        raise Stop(f"{cfg().remote}/{branch} is at {remote[:10] or 'nothing'}, not HEAD {head[:10]}; "
                   f"push first: git push -u {cfg().remote} HEAD:{branch}")


def authority() -> dict:
    """What the operator let lanes do on the forge: {push, draftPullRequests, grantedBy, grantedOn}. Absent: nothing."""
    granted = cfg().raw.get("authority") or {}
    if granted and not (granted.get("grantedBy") and granted.get("grantedOn")):
        raise Stop(f"{cfg().delivery_where}: `authority` must say who granted it and when (grantedBy, grantedOn)")
    return granted


def require_authority(what: str) -> dict:
    granted = authority()
    if not granted.get(what):
        raise Stop(f"this delivery grants lanes no `{what}` authority ({cfg().delivery_where} `authority`); "
                   "hand your branch to the lead instead")
    return granted


def authority_text() -> str:
    c, granted = cfg(), authority()
    if not granted.get("push"):
        return ("Lanes have no authority to push, open pull requests or mark one ready in this delivery. Commit on "
                "your branch and tell the lead it is ready; the lead pushes it.")
    what = f"push your own `{c.branch_pattern.replace('{key}', '<key>')}` branch, with `{flow_command()} push <key>`"
    if granted.get("draftPullRequests"):
        what += f", and open its draft pull request into `{c.base_branch}`, with `{flow_command()} open-pr`"
    return (f"{granted['grantedBy']} gave lanes authority on {granted['grantedOn']} to {what}. That is all: never push "
            f"any other branch, never force-push, never merge, and never mark a pull request ready or approve one. "
            "The lead's merge queue merges.")


def transitions_text() -> str:
    """The lane's status moves, from the own config's `tracker.transitions`, and which command makes each."""
    conf = tracker_conf(cfg())
    if conf["problems"]:
        raise Stop(f"{cfg().name}: {'; '.join(conf['problems'])}")
    moves = conf["transitions"]
    if not moves.get("start") and not moves.get("review"):
        return "Don't move your story's status: this repository gives lanes no status moves."
    said = lambda names: ", then ".join(f'"{n}"' for n in names)
    publishes = conf["mode"] == "publisher"
    lines = []
    if moves.get("start"):
        lines.append(f"- The start move, {said(moves['start'])}: "
                     + ("the tracker publisher makes it once `lane-start` records your start." if publishes else
                        "`lane-start` prints it; make it with your tracker tools."))
    if moves.get("review"):
        when = moves.get("reviewWhen")
        made = {"gates-passed": "`open-pr` makes it once the PR is open and the gates pass at its head",
                "pr-open": "`open-pr` makes it as the PR opens",
                "pipeline-green": "`pr-review` makes it once the pipeline's review is current and its checks green"}
        lines.append(f"- The review move, {said(moves['review'])}: "
                     + (made[when] + ("" if publishes else ", by printing it for you to make with your tracker tools")
                        if when else "`open-pr` prints it once the PR is open; make it then with your tracker tools")
                     + ".")
    lines.append("Nothing else: later moves (after merge, after a deploy) aren't yours. A move the commands couldn't make "
                 "is retried by running the command again; `handoff` retries a review move that is still owed."
                 + (f" The workflow: {moves['source']}" if moves.get("source") else ""))
    return "\n".join(lines)


def said_moves(names: list[str]) -> str:
    return ", then ".join(f'"{n}"' for n in names)


def review_move_made(c, key: str, names: list[str]) -> bool:
    """Whether this record's ledger holds the story's whole review move: every step made or made by someone else, or
    the move found made or not offered. A move a refusal stopped partway isn't made: the next run resumes it."""
    init = tracker_publisher.init_id(sorted(load_state()["journal"], key=lambda e: e["id"]))
    lines = [line for line in tracker_publisher.read_ledger(c.state) if line.get("init") == init
             and line.get("ticket") == key and line.get("move") == "review"]
    if any(line["outcome"] == "handled" or str(line.get("action", "")).endswith(":made") for line in lines):
        return True
    done = {line.get("transition") for line in lines if line["outcome"] in ("transition", "skip")}
    return all(name in done for name in names)


def start_owed(c, doc: dict, key: str) -> bool:
    """Whether the story's latest assignment has no ledger line yet under this record: its start isn't posted."""
    journal = sorted(doc["journal"], key=lambda e: e["id"])
    assigned = [e for e in journal if e["kind"] == "lane.assigned" and e.get("subject") == key]
    if not assigned:
        return False
    init = tracker_publisher.init_id(journal)
    return not any(line["journal"] == assigned[-1]["id"] and line.get("init") == init
                   for line in tracker_publisher.read_ledger(c.state))


def append_owed(c, doc: dict, key: str, names: list[str], why: str, locked: bool = False) -> None:
    """A review move the hand-off couldn't make, left in the ledger as `failed`, where Needs you shows it. `locked`:
    the caller already holds the ledger's lock (a second lock from this process would wait on itself)."""
    init = tracker_publisher.init_id(sorted(doc["journal"], key=lambda e: e["id"]))
    with tracker_publisher.ledger_lock(c.state, wait=True) if not locked else contextlib.nullcontext():
        tracker_publisher.append_ledger(c.state, {
            "journal": tracker_publisher.LANE_MOVE, "init": init, "outcome": "failed", "writer": "lane",
            "kind": "lane.review", "ticket": key, "move": "review", "transitions": names, "error": why[:500]})


def review_move(c, key: str, when: str, last: bool = False) -> None:
    """The story's review move, at the moment `when` names. With the tracker publisher and that reviewWhen, the
    command makes it through the ledger; otherwise it prints it for the lane, as the brief did. A move the tracker
    refuses writes no line and doesn't fail the command: running the command again, or `handoff`, retries it."""
    conf = tracker_conf(c)
    names = conf["transitions"].get("review") or []
    configured = conf["transitions"].get("reviewWhen")
    if not names or conf["problems"]:
        return
    if configured is None:
        # Opt-in: with no reviewWhen nothing moves by itself, and open-pr prints the move as the brief did.
        if when == "pr-open":
            print(f"Tracker move (your story {key}): {said_moves(names)}, with your tracker tools.")
        return
    if configured != when:
        return
    if conf["mode"] != "publisher":
        print(f"Tracker move (your story {key}): {said_moves(names)}, with your tracker tools.")
        return
    if review_move_made(c, key, names):
        print(f"{key}'s review move is already made.")
        return
    doc = load_state()
    if start_owed(c, doc, key):
        # A review move before the start is posted would leave the start skipped and the ticket behind.
        if last:
            append_owed(c, doc, key, names, "the story's start hadn't been posted yet")
            print(f"{key}'s review move waits for its start to be posted; the lead's Needs you shows it, to make by hand")
        else:
            print(f"{key}'s review move waits for its start to be posted; run this command again, or hand off, later")
        return
    with tracker_publisher.ledger_lock(c.state, wait=True):
        publisher = tracker_publisher.Publisher(doc, c.state, conf, tracker_publisher.Adapter(conf, c.root),
                                                c.delivery, c.flow_log, writer="lane")
        publisher.ticket = key
        entry = {"id": tracker_publisher.LANE_MOVE, "kind": "lane.review", "subject": key}
        notes: list[str] = []
        try:
            markers = publisher.adapter.markers(key)
            earlier = [m for m in markers if m.startswith(f"agent-delivery:{c.delivery}:")
                       and not m.startswith(f"agent-delivery:{c.delivery}:{publisher.init}:")
                       and m.endswith(f":transition:{names[-1]}")]
            if earlier:
                # Made under an earlier record of this delivery, before a --replace: not made again.
                publisher.write(entry, "skip", move="review", ticket=key, action=f"transition:{names[-1]}:made",
                                reason="made under an earlier record")
                print(f"{key}'s review move was made under an earlier record of this delivery.")
                return
            publisher.move(entry, key, "review", names, markers, notes)
        except (tracker_publisher.AdapterFailure, tracker_publisher.Transient, tracker_publisher.Stop) as failure:
            if not last:
                print(f"{key}'s review move wasn't made ({failure}); run this command again, or hand off, to retry it")
                return
            # The hand-off is the lane's last chance: the owed move is left in the ledger, where Needs you shows it.
            append_owed(c, doc, key, names, str(failure), locked=True)
            print(f"{key}'s review move wasn't made ({failure}); the lead's Needs you shows it, to make by hand")
            return
    print(f"Tracker move (your story {key}): " + (" ".join(notes) or "made."))


def handoff_and_post(c, key: str, lane: str, head: str, pr: str, rounds: int, post: str,
                     checks: list[str] | None = None) -> str | None:
    """Hand off to the record and, with the tracker publisher, post the hand-off update under the entry it journals,
    holding the ledger's lock across the journal write, the post and its line. Returns what was posted, or None when the
    lane posts it (no publisher) or the publisher will (the call failed, so no line was written)."""
    conf = tracker_conf(c)
    lead_moves = ((conf["transitions"].get("lead") or {}).get("candidate.handed")) if not conf["problems"] else None
    handed = ["handoff", "--lane", lane, "--subject", key, "--head", head, "--change-request", pr, "--rounds",
              str(rounds), *(["--checks", ",".join(checks)] if checks else [])]
    if conf["mode"] != "publisher" or conf["problems"] or lead_moves:
        # With lead moves for a hand-off, the publisher makes them and posts the update with what they did.
        state_tool(*handed)
        return ("Tracker post: the tracker publisher posts the hand-off update, with its moves." if lead_moves
                and conf["mode"] == "publisher" else None)
    with tracker_publisher.ledger_lock(c.state, wait=True):
        state_tool(*handed)
        doc = load_state()
        journal = sorted(doc["journal"], key=lambda e: e["id"])
        entry = [e for e in journal if e["kind"] == "candidate.handed" and e.get("subject") == key][-1]
        init = tracker_publisher.init_id(journal)
        stamp = tracker_publisher.marker(c.delivery, init, entry["id"], "comment")
        adapter = tracker_publisher.Adapter(conf, c.root)
        try:
            found = stamp in adapter.markers(key)
            if not found:
                limit = adapter.status(key).get("commentLimit")
                text = tracker_publisher.body_of(entry, "Handed off and queued", [post[0].upper() + post[1:]], c.delivery,
                                                 limit if isinstance(limit, int) else None)
                with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as body:
                    body.write(text)
                try:
                    adapter.call("comment", "--ticket", key, "--marker", stamp, "--body-file", body.name)
                finally:
                    os.unlink(body.name)
        except (tracker_publisher.AdapterFailure, tracker_publisher.Transient) as failure:
            return (f"Tracker post: not posted ({failure}); the tracker publisher posts it on its next run. "
                    "The hand-off stands.")
        tracker_publisher.append_ledger(c.state, {"journal": entry["id"], "init": init, "outcome": "comment",
                                                  "writer": "lane", "kind": entry["kind"], "ticket": key,
                                                  "action": "comment", **({"found": True} if found else {})})
    return f"Tracker post: posted the hand-off update on {key}."


def lane_start(args) -> None:
    """Begin a story: its checkout from the base, the prepare step, the lane's focus, and the story's start in the flow
    log, which the tracker publisher's start move and update wait for. Run again, it changes nothing."""
    c = cfg()
    key = c.require(args.key)
    lane = c.lane_of(key)
    base = args.base or c.base_branch
    # Without a binding base, the delivery's own base is the one it serves.
    served = c.binding if c.binding_base else {"base": {"ref": c.base_branch}}
    if not serves(served, base):
        raise unserved(served, base)
    path, branch = c.lane_checkout(key), c.branch(key)
    did = []
    if path.exists():
        current = git("rev-parse", "--abbrev-ref", "HEAD", cwd=path, check=False)
        if current != branch:
            raise Stop(f"{path} exists on {current or 'no branch'}, not {branch}: move it aside, or start from it by hand")
        recorded = (load_json(lane_base_file(path)) or {}).get("base")
        if recorded and recorded != base:
            raise Stop(f"{path} was started on {recorded}, not {base}: finish it on {recorded}, or move it aside")
    else:
        git("fetch", "-q", c.remote, base, cwd=c.main)
        if run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=c.main, check=False).returncode == 0:
            if run(["git", "merge-base", branch, f"{c.remote}/{base}"], cwd=c.main, check=False).returncode != 0:
                raise Stop(f"the branch {branch} shares no history with {c.remote}/{base}: delete it, or start it by hand")
            git("worktree", "add", "-q", str(path), branch, cwd=c.main)
            did.append(f"made {path} on the existing branch {branch}")
        else:
            git("worktree", "add", "-q", "--no-track", "-b", branch, str(path), f"{c.remote}/{base}", cwd=c.main)
            did.append(f"made {path} on {branch} from {c.remote}/{base}")
        record_lane_base(path, base)
    prepared = lane_base_file(path).with_name("prepared")
    if not prepared.exists():
        # Marked only once every prepare step passed, so a failed one runs again on the next lane-start.
        for argv in prepare_commands():
            run(argv, cwd=path)
        prepared.write_text(now() + "\n")
        did.append("prepared it")
    journal = load_state()["journal"]
    assigned = [e for e in journal if e["kind"] == "lane.assigned" and e.get("subject") == key]
    since = assigned[-1]["at"] if assigned else None
    starts = [s for s in tracker_publisher.read_flow(c.flow_log, c.delivery, tracker_publisher.LANE_STARTS)
              if s.get("subject") == key and (since is None or s.get("at", "") >= since)]
    # On every run, so a start recorded without its stage move gets it on the next one; a story past planned is left as
    # it is. It comes first, so the stage is moved before the start is recorded.
    if mark_building(c, key, lane):
        did.append(f"{lane} started it: now building")
    if not starts:
        # The focus is set only with the start, so running it again never resets what the lane has said since.
        state_tool("focus", "--lane", lane, "--text", f"{key}: started", "--state", "working", "--waiting-on", "")
        did.append(f"set {lane}'s focus")
        tracker_publisher.record_lane_start(c.flow_log, c.delivery, key, lane)
        did.append("recorded the start")
        conf = tracker_conf(c)
        # Made by hand, with the lane's own tracker tools: the publisher makes it in publisher mode.
        if conf["mode"] == "manual":
            moves = conf["transitions"].get("start") or []
            update = f"Started ({lane}): {c.label(key)}: {c.stories[key]['title']}"
            if moves:
                print(f"Tracker move (your story {key}): {said_moves(moves)}, with your tracker tools.")
            print(f"Tracker post (your story {key}): {update}")
    print(f"{key}: " + ("; ".join(did) if did else "already started; nothing to do") + f". Work in {path}.")


def mark_building(c, key: str, lane: str) -> bool:
    """Move a story still planned to building in the record, as its lane: the one stage a lane writes. A story already
    past planned is left as it is. Returns whether it moved."""
    def compute(doc: dict):
        entry = next((e for e in doc["state"].get("planned", []) if e["subject"] == key), None)
        if entry is None or entry.get("stage") != "planned" or entry.get("lane") != lane:
            return None
        return ["--lane", lane, "--element", f"planned[subject={key}]", "--patch", json.dumps({"stage": "building"})]
    return patch_from(compute, f"{key}'s start", writer="lane") is not None


def push(args) -> None:
    c = cfg()
    key = c.require(args.key)
    require_authority("push")
    head = clean_head()
    branch = c.branch(key)
    current = git("rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        raise Stop(f"this checkout is on {current}, not {branch}: push from the story's own checkout")
    done = run(["git", "push", "-u", c.remote, f"HEAD:refs/heads/{branch}"], check=False)  # never forced
    if done.returncode != 0:
        raise Stop(f"the push was refused (it is never forced): {done.stderr.strip()[-400:]}")
    print(f"pushed {branch} at {head[:10]}")


def open_pr(args) -> None:
    c = cfg()
    key = c.require(args.key)
    require_authority("draftPullRequests")
    if not args.title.startswith(f"{key}: "):
        raise Stop(f"the title must start with '{key}: '")
    head = clean_head()
    pushed(key, head)
    body = Path(args.body_file).read_text()
    issue = c.issue_url.format(key=key)
    if issue not in body:
        body += f"\n\nTracker: {issue}\n"
    found = forge("find", "--source", c.branch(key))
    if found.get("url"):
        print(found["url"])
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write(body)
        try:
            print(forge("open", "--source", c.branch(key), "--destination", c.base_branch, "--title", args.title,
                        "--body-file", handle.name)["url"])
        finally:
            os.unlink(handle.name)
    review_move(c, key, "pr-open")
    if tracker_conf(c)["transitions"].get("reviewWhen") == "gates-passed":
        _, problems = gates_summary(key, repo(), head)
        if problems:
            print(f"{key}'s review move waits for the gates to pass at {head[:10]}: run the gates, then open-pr again")
        else:
            review_move(c, key, "gates-passed")


def parse_blocked(items: list[str]) -> dict[str, str]:
    blocked = {}
    for item in items or []:
        label, _, reason = item.partition("=")
        if not label.strip() or not reason.strip():
            raise Stop(f"--blocked needs LABEL=REASON, got {item!r}")
        blocked[label.strip()] = reason.strip()
    return blocked


LEGACY_REVIEWER = "review"  # the legacy {author, waitSeconds}'s one reviewer, and an old-shape record's
REVIEWER_MEMBERS = ("id", "account", "author", "signature", "required", "gate", "report")
REVIEW_GATES = ("approval", "verdict")
WAIT_SECONDS, SETTLE_SECONDS = 1800, 300
RECORD_SCHEMA = "pr-review/2"
# A wait's retry of a transient adapter failure: the first sleep, and the most any one sleeps (Decision 6).
BACKOFF_FIRST, BACKOFF_MOST = 5, 60


class Clock:
    """The time, the sleeps and the poll interval of every PR-review wait, read through one place. RUN_DELIVERY_CLOCK
    names a JSON file, {"now": seconds since the epoch, "poll"?: seconds}, that stands in for them: each sleep moves
    its `now` on and is added to its `sleeps`, so a wait runs through without waiting."""

    def __init__(self, path: str | None):
        self.path = Path(path) if path else None

    def read(self) -> dict:
        return json.loads(self.path.read_text())

    def time(self) -> float:
        return float(self.read()["now"]) if self.path else time.time()

    def sleep(self, seconds: float) -> None:
        if not self.path:
            time.sleep(seconds)
            return
        doc = self.read()
        doc["now"] = float(doc["now"]) + seconds
        doc.setdefault("sleeps", []).append(seconds)
        self.path.write_text(json.dumps(doc))

    def poll(self, seconds: float) -> float:
        return float(self.read().get("poll", seconds)) if self.path else seconds


def clock() -> Clock:
    return Clock(os.environ.get("RUN_DELIVERY_CLOCK"))


def pr_review_config() -> dict | None:
    """review.pr, read one way by every reader: None when the repository declares no PR review.

    The legacy {author, waitSeconds} is read as it always was (`author` required, other members ignored, waitSeconds
    read with int()), as one required reviewer, `review`, with no settle. {waitSeconds?, settleSeconds?, reviewers}
    is the list: each reviewer {id, account?, author?, signature?, required?, gate?, report?}. Anything else in the
    list's shape is refused, naming the reviewer and the member."""
    c = cfg()
    conf = (c.flow_raw.get("review") or {}).get("pr")
    if not conf:
        return None
    if "reviewers" not in conf:
        if not conf.get("author"):
            raise Stop(f"{c.name} review.pr needs `author`: the name the pipeline's review comment is posted as")
        return {"legacy": True, "raw": conf, "settleSeconds": None,
                "reviewers": [{"id": LEGACY_REVIEWER, "author": conf["author"], "required": True}]}
    where = f"{c.name} review.pr"
    unknown = sorted(set(conf) - {"waitSeconds", "settleSeconds", "reviewers"})
    if unknown:
        raise Stop(f"{where}: `{unknown[0]}` is not a member of review.pr (it takes waitSeconds, settleSeconds and "
                   "reviewers)")
    for member, default in (("waitSeconds", WAIT_SECONDS), ("settleSeconds", SETTLE_SECONDS)):
        value = conf.get(member, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise Stop(f"{where}: `{member}` must be a whole number of seconds, 0 or more, not {value!r}")
    listed = conf["reviewers"]
    if not isinstance(listed, list) or not listed:
        raise Stop(f"{where}: `reviewers` must be a list of one reviewer or more, not {listed!r}")
    reviewers: list[dict] = []
    for n, entry in enumerate(listed, 1):
        rid = entry.get("id") if isinstance(entry, dict) else None
        name = f"reviewer {rid!r}" if isinstance(rid, str) and rid else f"reviewer {n}"

        def bad(member: str, why: str) -> Stop:
            return Stop(f"{where}, {name}: `{member}` {why}")
        if not isinstance(entry, dict):
            raise Stop(f"{where}, {name}: a reviewer is an object, {{id, account?, author?, ...}}, not {entry!r}")
        extra = sorted(set(entry) - set(REVIEWER_MEMBERS))
        if extra:
            raise bad(extra[0], f"is not a reviewer member (a reviewer takes {', '.join(REVIEWER_MEMBERS)})")
        if not isinstance(rid, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", rid):
            raise bad("id", "must be lowercase letters, digits and hyphens, starting with a letter")
        if any(r["id"] == rid for r in reviewers):
            raise bad("id", "is another reviewer's too: each reviewer's id is its own")
        for member in ("account", "author", "signature", "report"):
            if member in entry and not (isinstance(entry[member], str) and entry[member]):
                raise bad(member, "must be a non-empty string")
        if not entry.get("account") and not entry.get("author"):
            raise bad("account", "or `author` is needed: the forge account id, or the display name, it posts as")
        if "required" in entry and not isinstance(entry["required"], bool):
            raise bad("required", f"must be true or false, not {entry['required']!r}")
        if "gate" in entry and entry["gate"] not in REVIEW_GATES:
            raise bad("gate", f"must be \"approval\" or \"verdict\", not {entry['gate']!r}")
        if entry.get("gate") == "approval" and not entry.get("account"):
            raise bad("account", "is needed for gate \"approval\": an approval is matched by account, never by name")
        reviewers.append({**entry, "required": entry.get("required", True)})
    return {"legacy": False, "raw": conf, "waitSeconds": conf.get("waitSeconds", WAIT_SECONDS),
            "settleSeconds": conf.get("settleSeconds", SETTLE_SECONDS), "reviewers": reviewers}


def wait_seconds(conf: dict) -> int:
    return int(conf["raw"].get("waitSeconds", WAIT_SECONDS)) if conf["legacy"] else conf["waitSeconds"]


def instant(stamp: object) -> float:
    """An ISO 8601 time from the forge as seconds since the epoch; one without a zone is UTC."""
    try:
        parsed = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        raise Stop(f"the forge adapter's `reviews` gave a time that isn't ISO 8601: {stamp!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def reviewer_matches(reviewer: dict, found: dict, legacy: bool) -> bool:
    """Decision 2: by account when the reviewer names one, else by author; and by signature, as plain text, when set.
    The list never matches an empty body, which a bodiless approval has; the legacy shape matches as it always did."""
    body = found.get("body") or ""
    if not legacy and not body:
        return False
    if reviewer.get("account"):
        if found.get("account") != reviewer["account"]:
            return False
    elif found.get("author") != reviewer.get("author"):
        return False
    return reviewer.get("signature", "") in body


def missing_members(found: dict, conf: dict) -> list[str]:
    """Decision 5: what the list's reviewers need of `reviews` that the adapter didn't return. Present but empty is
    not missing."""
    needed = []
    if any(r.get("account") for r in conf["reviewers"]) and any("account" not in r for r in found.get("reviews", [])):
        needed.append("`account` on each review comment (a reviewer is matched by account)")
    if any(r.get("gate") == "approval" for r in conf["reviewers"]) and "approvals" not in found:
        needed.append("`approvals` (a reviewer has gate \"approval\")")
    if any(r.get("report") for r in conf["reviewers"]) and "reports" not in found:
        needed.append("`reports` (a reviewer has `report`)")
    return needed


def classify_reviews(found: dict, conf: dict, now: float) -> dict:
    """One read of `reviews` at the head, classified: {status, checks, reviewers: {id: {state, review}}}.

    status is `failed` when a check at the head failed, `pending` while a check or a reviewer is, and `done` otherwise.
    A reviewer is `current` once every check passed and its latest matching review is no earlier than the latest
    check. In the list, an optional reviewer with none once the settle has passed is `absent`: the settle starts when
    the last check finished (its doneAt, else its at) and runs settleSeconds by `now`, its end belonging to it."""
    checks = found.get("checks", [])
    out = {"checks": checks, "reviewers": {}}
    if any(ch["state"] == "failed" for ch in checks):
        out["status"] = "failed"
        return out
    passed = bool(checks) and all(ch["state"] == "passed" for ch in checks)
    if conf["legacy"]:
        started = max((ch["at"] for ch in checks), default=None)
        reviewer = conf["reviewers"][0]
        mine = [r for r in found.get("reviews", []) if r["author"] == reviewer["author"]]
        review = max(mine, key=lambda r: r["at"], default=None)
        current = passed and review is not None and review["at"] >= started
        out["reviewers"][reviewer["id"]] = {"state": "current" if current else "pending", "review": review}
    else:
        missing = missing_members(found, conf)
        if missing:
            raise Stop("the forge adapter's `reviews` returned no " + "; no ".join(missing) + ": use an adapter that "
                       "returns it, or configure review.pr without it")
        # Only once every check passed, when each carries its time: a pending one may have none yet.
        latest = max((instant(ch["at"]) for ch in checks), default=None) if passed else None
        settled = passed and now > max(instant(ch.get("doneAt") or ch["at"]) for ch in checks) + conf["settleSeconds"]
        for reviewer in conf["reviewers"]:
            mine = [r for r in found.get("reviews", []) if reviewer_matches(reviewer, r, False)]
            review = max(mine, key=lambda r: instant(r["at"]), default=None)
            if passed and review is not None and instant(review["at"]) >= latest:
                state = "current"
            elif settled and not reviewer["required"]:
                state = "absent"
            else:
                state = "pending"  # a required reviewer past the settle too, until it posts
            out["reviewers"][reviewer["id"]] = {"state": state, "review": review}
    waiting = not passed or any(r["state"] == "pending" for r in out["reviewers"].values())
    out["status"] = "pending" if waiting else "done"
    return out


def retried(call: Callable[[], dict], deadline: float) -> dict:
    """A forge call inside a wait's loop: a transient failure (exit 75) is tried again after 5 seconds, doubling to at
    most 60, never sleeping past the deadline. At the deadline the refusal carries the last transient message."""
    clk, delay = clock(), BACKOFF_FIRST
    while True:
        try:
            return call()
        except Transient as error:
            left = deadline - clk.time()
            if left <= 0:
                raise Stop(f"{error} (a transient failure, retried until the wait's deadline)") from None
            clk.sleep(min(delay, left))
            delay = min(delay * 2, BACKOFF_MOST)


def read_pr_reviews(url: str, head: str, conf: dict, deadline: float | None) -> dict:
    """`reviews` at the head, classified now. With a deadline, inside a wait, a transient failure is retried."""
    def ask() -> dict:
        return forge("reviews", "--url", url, "--head", head)
    found = retried(ask, deadline) if deadline is not None else ask()
    read = classify_reviews(found, conf, clock().time())
    refused = pr_review_gates.attach(read, found, conf, head)  # each current review's gate, beside it
    if refused:
        raise Stop(refused)
    return read


def pr_review_state(url: str, head: str, conf: dict) -> tuple[str, dict | None, list[dict]]:
    """The legacy reviewer's (status, review, the head's checks), status current, pending or failed: one read."""
    read = read_pr_reviews(url, head, conf, None)
    if read["status"] == "failed":
        return "failed", None, read["checks"]
    one = read["reviewers"][LEGACY_REVIEWER]
    return ("current" if read["status"] == "done" else "pending"), one["review"], read["checks"]


def check_states(checks: list[dict]) -> str:
    return ", ".join(f"{ch['name']} {ch['state']}" for ch in checks) or "no checks"


def record_reviewers(recorded: dict) -> dict[str, dict]:
    """A saved pr-review.json's reviewers by id: the list's own, or an old-shape record as reviewer `review`."""
    if recorded.get("schema") == RECORD_SCHEMA:
        return recorded.get("reviewers") or {}
    if "author" in recorded:
        return {LEGACY_REVIEWER: {"state": "current", "required": True,
                                  **{k: recorded[k] for k in ("author", "at", "body", "triage") if k in recorded}}}
    return {}


def triage_by_reviewer(given: list[str], entries: dict[str, dict]) -> tuple[dict[str, str], list[str]]:
    """Decision 8: each --pr-review-triage, ID=TEXT for a reviewer or a bare TEXT when exactly one review is current,
    matched to the current reviews. Returns ({id: text}, problems): every current review needs one; an absent or
    unknown reviewer takes none."""
    current = [rid for rid, e in entries.items() if e.get("state") == "current"]
    named: dict[str, str] = {}
    bare: list[str] = []
    problems: list[str] = []
    for item in given:
        rid, sep, text = item.partition("=")
        if sep and re.fullmatch(r"[a-z][a-z0-9-]*", rid.strip()) and text.strip():
            rid = rid.strip()
            if rid not in entries:
                problems.append(f"--pr-review-triage names {rid}, which is no reviewer of the PR review recorded "
                                f"(they are {', '.join(entries) or 'none'})")
            elif entries[rid].get("state") != "current":
                problems.append(f"--pr-review-triage names {rid}, which was absent: an absent reviewer takes no triage")
            elif rid in named:
                problems.append(f"--pr-review-triage names {rid} twice")
            else:
                named[rid] = text.strip()
        elif item.strip():
            bare.append(item.strip())
    if bare:
        if len(bare) > 1 or len(current) != 1 or named:
            problems.append(f"a bare --pr-review-triage TEXT is accepted only alone, with exactly one current review; "
                            f"{len(current)} are current ({', '.join(current) or 'none'}): give each "
                            "--pr-review-triage ID=TEXT")
        else:
            named[current[0]] = bare[0]
    problems += [f"say how {rid}'s review was handled: --pr-review-triage {rid}=TEXT"
                 for rid in current if rid not in named]
    return named, problems


def pr_review_text() -> str:
    conf = pr_review_config()
    if not conf:
        return ""
    if conf["legacy"]:
        return (f"   **The PR review.** The forge's pipeline reviews every PR and posts as \"{conf['raw']['author']}\". "
                "Open the draft PR as soon as the gates first pass, so that review runs alongside your lens rounds; "
                f"each push re-runs it. Before you hand off, at your final head: `{flow_command()} pr-review <key> "
                "--wait` waits for the pipeline and its review, saves it and prints it. Triage its findings as you "
                "would a lens's: fix anything that should be fixed in the lane (then the gates, a lens round and this "
                "review again at the new head), and fix or defer the rest, deferring only with a follow-up. Pass "
                "`--pr-review-triage \"<what you fixed, deferred and why>\"` to `handoff`. The lead's acceptance "
                "re-checks that the review is current at your head.\n")
    named = "; ".join(f"`{r['id']}` ({'required' if r['required'] else 'optional'})" for r in conf["reviewers"])
    return ("   **The PR reviews.** The forge's pipeline reviews every PR with several reviewers, each under its id: "
            f"{named}. Open the draft PR as soon as the gates first pass, so they run alongside your lens rounds; each "
            f"push re-runs them. Before you hand off, at your final head: `{flow_command()} pr-review <key> --wait` "
            "waits for the pipeline and every reviewer, saves them and prints each under its id. A required "
            "reviewer is waited for; an optional one that posts nothing within the settle after the pipeline goes "
            "green is recorded absent. Triage each current review's findings as you would a lens's: fix anything "
            "that should be fixed in the lane (then the gates, a lens round and these reviews again at the new head), "
            "and fix or defer the rest, deferring only with a follow-up. Pass one `--pr-review-triage <id>=\"<what "
            "you fixed, deferred and why>\"` to `handoff` for each current review (a bare text only when exactly one "
            "is current). The lead's acceptance re-checks that each is still current and unchanged at your head, and "
            "sends you back to read an optional review recorded absent that has since arrived.\n"
            + pr_review_gates.brief_text(conf["reviewers"]))


def pr_review(args) -> None:
    c = cfg()
    key = c.require(args.key)
    conf = pr_review_config()
    if not conf:
        raise Stop(f"this repository declares no PR review ({c.name} review.pr)")
    if not forge_answers("reviews"):
        raise Stop(f"PR reviews are unsupported by this forge adapter ({c.forge.get('adapter')} has no `reviews` "
                   f"command): remove review.pr from {c.name}, or use an adapter that answers `reviews`")
    head = clean_head()
    pushed(key, head)
    url = forge("find", "--source", c.branch(key)).get("url")
    if not url:
        raise Stop(f"no open PR for {c.branch(key)}: open the draft PR first")
    clk = clock()
    deadline = clk.time() + (wait_seconds(conf) if args.wait else 0)
    while True:
        read = read_pr_reviews(url, head, conf, deadline if args.wait else None)
        left = deadline - clk.time()
        if read["status"] != "pending" or left <= 0:
            break
        clk.sleep(min(clk.poll(30), left))
    checks = read["checks"]
    if read["status"] == "failed":
        raise Stop(f"the pipeline failed at {head[:10]}: " + ", ".join(f"{ch['name']} {ch['state']}" for ch in checks))
    if conf["legacy"]:
        review = read["reviewers"][LEGACY_REVIEWER]["review"]
        author = conf["raw"]["author"]
        if read["status"] == "pending":
            raise Stop(f"no {author} review after the pipeline for {head[:10]} yet"
                       + ("" if args.wait else "; run with --wait") + ": " + (", ".join(f"{ch['name']} {ch['state']}" for ch in checks) or "no checks"))
        record = {"head": head, "url": url, "author": review["author"], "at": review["at"], "body": review["body"]}
        (review_dir(key) / "pr-review.json").write_text(json.dumps(record, indent=2))
        review_move(c, key, "pipeline-green")
        print(f"{author} review of {head[:10]} ({review['at']}), saved for the hand-off:\n")
        print(review["body"])
        print("\nTriage every finding like a lens finding: fix what blocks in the lane (then gates, a lens round and this "
              "review again at the new head), and fix or defer the rest. Hand off with --pr-review-triage saying which.")
        return
    if read["status"] == "pending":
        waiting = [rid for rid, r in read["reviewers"].items() if r["state"] == "pending"]
        said = (f"no current review from {', '.join(waiting)} after the pipeline for {head[:10]} yet" if waiting
                else f"the pipeline for {head[:10]} hasn't finished yet")
        raise Stop(said + ("" if args.wait else "; run with --wait") + ": " + check_states(checks))
    entries: dict[str, dict] = {}
    for reviewer in conf["reviewers"]:
        state = read["reviewers"][reviewer["id"]]
        entry: dict = {"state": state["state"], "required": reviewer["required"]}
        if state["state"] == "current":
            review = state["review"]
            entry.update(author=review["author"], **({"account": review["account"]} if "account" in review else {}),
                         at=review["at"], body=review["body"])
            if state.get("gate"):
                entry["gate"] = state["gate"]
        entries[reviewer["id"]] = entry
    record = {"schema": RECORD_SCHEMA, "head": head, "url": url, "reviewers": entries}
    (review_dir(key) / "pr-review.json").write_text(json.dumps(record, indent=2))
    # Every required reviewer is current here (none is pending), so the pipeline's review move is due.
    review_move(c, key, "pipeline-green")
    print(f"PR reviews of {head[:10]}, saved for the hand-off:")
    for rid, entry in entries.items():
        kind = "required" if entry["required"] else "optional"
        if entry["state"] == "current":
            gate_line = f"\n{pr_review_gates.shown(entry['gate'])}" if entry.get("gate") else ""
            print(f"\n## {rid} ({kind}): {entry['author']} at {entry['at']}{gate_line}\n\n{entry['body']}")
        else:
            print(f"\n## {rid} ({kind}): absent, no review within {conf['settleSeconds']}s after the pipeline went "
                  "green")
    print("\nTriage every finding like a lens finding: fix what blocks in the lane (then gates, a lens round and these "
          "reviews again at the new head), and fix or defer the rest. Hand off with one --pr-review-triage ID=TEXT for "
          "each current review saying which.")


def pr_review_current(args) -> None:
    """The merge queue's reviewGate: exit 0 only when review.pr's review is current and passing at --head.

    It runs in the queue's checkout of the base, where no delivery is named, so it reads only the repository's own
    configuration. Every refusal is one line on stderr, an adapter's own included, for the queue's log."""
    try:
        review_gate(args)
    except Stop as error:
        raise Stop(" ".join(str(error).split())) from None


def review_gate(args) -> None:
    global CFG
    c = CFG = Config(repo(), need_delivery=False, pick=False)
    conf = pr_review_config()
    if not conf:
        raise Stop(f"this repository declares no PR review ({c.name} review.pr), so there is no review to gate on")
    if not forge_answers("reviews"):
        raise Stop(f"PR reviews are unsupported by this forge adapter ({c.forge.get('adapter')} has no `reviews` "
                   f"command): remove review.pr from {c.name}, or use an adapter that answers `reviews`")
    url = forge("find", "--source", args.branch).get("url")
    if not url:
        raise Stop(f"no open PR for {args.branch}")
    wait = wait_seconds(conf) if args.wait else 0
    clk = clock()
    deadline = clk.time() + wait
    within = deadline if args.wait else None
    while True:
        # Read each time: a forge can show the PR at its old head for a while after the queue pushes the synced one.
        def get() -> dict:
            return forge("get", "--url", url)
        reported = (retried(get, within) if within is not None else get()).get("head")
        moved = not same_commit(reported, args.head)
        read = ({"status": "pending", "checks": [], "reviewers": {}} if moved
                else read_pr_reviews(url, args.head, conf, within))
        left = deadline - clk.time()
        if read["status"] != "pending" or left <= 0:
            break
        clk.sleep(min(clk.poll(10), left))  # a merge waits on it, so it looks again sooner than a lane's pr-review
    if moved:
        raise Stop(f"the PR is at {str(reported)[:10]}, not {args.head[:10]}: its review is of another head"
                   + (f" (waited {wait}s)" if args.wait else ""))
    states = check_states(read["checks"])
    if read["status"] == "failed":
        raise Stop(f"the pipeline failed at {args.head[:10]}: {states}")
    if conf["legacy"]:
        author = conf["raw"]["author"]
        if read["status"] == "pending":
            raise Stop(f"no current {author} review at {args.head[:10]}"
                       + (f" (waited {wait}s)" if args.wait else "") + f": {states}")
        review = read["reviewers"][LEGACY_REVIEWER]["review"]
        print(f"{author} review of {args.head[:10]} at {review['at']} is current: {states}")
        return
    # Only required reviewers block: an optional one absent, or not yet in, never does (triage is lead-accept's).
    required = [r["id"] for r in conf["reviewers"] if r["required"]]
    missing = [rid for rid in required if read["reviewers"][rid]["state"] != "current"]
    if missing or any(ch["state"] != "passed" for ch in read["checks"]) or not read["checks"]:
        raise Stop(f"no current review at {args.head[:10]} from {', '.join(missing) or 'the pipeline'}"
                   + (f" (waited {wait}s)" if args.wait else "") + f": {states}")
    unmet = pr_review_gates.live_refusals(conf["reviewers"], read["reviewers"], args.head)
    if unmet:
        raise Stop("; ".join(unmet))
    said = [f"{rid} at {r['review']['at']}" if r["state"] == "current" else f"{rid} {r['state']}"
            for rid, r in read["reviewers"].items()]
    print(f"PR reviews of {args.head[:10]} are current ({', '.join(said)}): {states}")


def no_change_handoff(c, key: str, lane: str, args) -> None:
    """A story whose criteria already hold on the base: handed off with the lane's evidence and the base head it ran
    at, with no branch, change request or review round. It never enters the merge queue, and nothing is committed."""
    if args.pr or args.findings or args.blocked or args.pr_review_triage:
        raise Stop("--no-change hands off no change: it takes --evidence alone, no --pr, --findings, --blocked or "
                   "--pr-review-triage")
    if not args.evidence:
        raise Stop("--no-change needs --evidence: the tests or commands, run at the base head, that show the story's "
                   "criteria already hold (text, or @FILE)")
    evidence = args.evidence
    if evidence.startswith("@"):
        try:
            evidence = Path(evidence[1:]).read_text()
        except OSError as error:
            raise Stop(f"can't read the evidence file {evidence[1:]}: {error}")
    evidence = evidence.strip()
    head = clean_head()
    git("fetch", "-q", c.remote, c.base_branch)
    if not is_ancestor(head, c.base_ref):
        raise Stop(f"{head[:10]} isn't on {c.base_ref}: this checkout holds a change, so hand it off with --pr, or run "
                   f"the evidence on the base")
    state_tool("handoff", "--no-change", "--lane", lane, "--subject", key, "--base-head", head,
               "--evidence-text", evidence)
    post = f"hand-off: no change needed, its evidence run at {c.base_branch} {head[:10]}: {evidence}"
    print("\n".join([
        f"**Hand-off: {key}** ({c.label(key)}, {lane})",
        "- candidate: none, no change needed (it never enters the merge queue)",
        f"- base head the evidence ran at: `{head}`",
        f"- evidence: {evidence}",
        "- review rounds: none",
        "- lead inputs: none",
    ]))
    print(f"\nTracker post (your story): {post}")


def selection_of(c) -> dict:
    selection = (c.binding or {}).get("selection")
    return selection if isinstance(selection, dict) else {}


def lead_selects(c) -> bool:
    """Whether the binding lets the lead choose a candidate's checks: then one accepted without a choice checks on the
    fallback."""
    return selection_of(c).get("lead") is True


def catalogue_ids(c) -> list[str]:
    return [entry["id"] for entry in selection_of(c).get("catalogue") or []
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)]


def check_ids(c, text: str, what: str) -> list[str]:
    """A comma-separated list of the binding's catalogue ids, each once, in the order given; refused naming any other."""
    known = catalogue_ids(c)
    if not known:
        raise Stop(f"{what} takes catalogue ids, and the binding declares no selection.catalogue")
    names = [name.strip() for name in text.split(",")]
    if not all(names) or len(set(names)) != len(names):
        raise Stop(f"{what} is a comma-separated list of catalogue ids, each once: {text!r}")
    unknown = [name for name in names if name not in known]
    if unknown:
        raise Stop(f"{what} names checks the binding's catalogue doesn't have: {', '.join(unknown)} (it has "
                   f"{', '.join(known)})")
    return names


def provider_selects(c) -> bool:
    """Whether the binding names a provider: then a candidate with no lead selection runs the provider's answer, and
    the fallback only when it can't be had."""
    return (c.binding or {}).get("provider") is not None


def fallback_ids(c) -> list[str]:
    return [str(entry.get("id")) for entry in (c.binding or {}).get("fallback") or [] if isinstance(entry, dict)]


def proposed_checks(c, changed: list[str], named: list[str]) -> list[str]:
    """What lead-accept proposes when the lead chose nothing: the catalogue ids of the test modules the diff changes,
    then the checks the lane named at hand-off, each once. select adds the guards and the readers' checks."""
    known = set(catalogue_ids(c))
    ids = [f"tests.{match.group(1)}" for path in changed
           for match in [re.fullmatch(r"tests/(test_\w+)\.py", path)] if match]
    ids += list(named)
    return [name for index, name in enumerate(ids) if name in known and name not in ids[:index]]


def pr_review_handoff(key: str, conf: dict, recorded: dict, given: list[str]) -> tuple[str | None, list[str]]:
    """The list's PR review at hand-off, from the record at the head: every configured reviewer recorded, and each
    current review triaged. Writes each triage into `recorded`. Returns (the hand-off's PR review line, problems)."""
    entries = record_reviewers(recorded)
    unrecorded = [r["id"] for r in conf["reviewers"] if r["id"] not in entries]
    if unrecorded:
        return None, [f"the PR review recorded at {recorded['head'][:10]} has no entry for {', '.join(unrecorded)}: run "
                      f"{flow_command()} pr-review {key} --wait again"]
    triage, problems = triage_by_reviewer(given, entries)
    if problems:
        return None, problems
    shown = []
    for rid, entry in entries.items():
        if entry.get("state") == "current":
            entry["triage"] = triage[rid]
            shown.append(f"{rid}: {entry.get('author')} at {str(entry.get('at'))[:16]}, triage: {triage[rid]}")
        else:
            shown.append(f"{rid}: {entry.get('state')}")
    if recorded.get("schema") != RECORD_SCHEMA and LEGACY_REVIEWER in triage:
        recorded["triage"] = triage[LEGACY_REVIEWER]  # an old-shape record keeps its own shape
    return "; ".join(shown), []


def handoff(args) -> None:
    c = cfg()
    key = c.require(args.key)
    lane = c.lane_of(key)
    if args.no_change:
        no_change_handoff(c, key, lane, args)
        return
    if not args.pr:
        raise Stop("a hand-off needs --pr, the story's open PR; a story that needs no change hands off with "
                   "--no-change --evidence")
    review_conf = pr_review_config()  # refused here, before anything else, when review.pr is malformed
    ran = check_ids(c, args.checks, "--checks") if args.checks is not None else []
    folder = review_dir(key)
    head = clean_head()
    pushed(key, head)
    pr = forge("get", "--url", args.pr)
    if pr.get("state") != "open" or pr.get("source") != c.branch(key) or pr.get("destination") != c.base_branch:
        raise Stop(f"{args.pr} isn't an open PR from {c.branch(key)} into {c.base_branch}: {pr}")
    rounds = round_files(folder)
    if not rounds:
        raise Stop("no review rounds yet: run review-prepare and review-reduce")
    outcomes = [json.loads(p.read_text()) for p in rounds]
    last = outcomes[-1]
    if last["outcome"] != "aligned":
        raise Stop(f"round {last['round']} is {last['outcome']}; the lenses have not all approved")
    if {e["lens"] for e in last["entries"]} != set(c.lenses):
        raise Stop(f"round {last['round']} doesn't carry every lens")
    if last["entries"][0]["binding"]["candidateRef"] != head:
        raise Stop(f"the approved round reviewed {last['entries'][0]['binding']['candidateRef'][:10]}, but HEAD is "
                   f"{head[:10]}; review the new head")
    validation, gate_problems = gates_summary(key, repo(), head)
    pr_line = None
    if review_conf:
        saved = folder / "pr-review.json"
        recorded = json.loads(saved.read_text()) if saved.exists() else {}
        if recorded.get("head") != head:
            gate_problems.append(f"no PR review recorded at {head[:10]}: run {flow_command()} pr-review {key} --wait")
        elif review_conf["legacy"]:
            # As it always was: one text, the last given, whatever it says.
            text = (args.pr_review_triage or [""])[-1].strip()
            if not text:
                gate_problems.append("say how the PR review's findings were handled: --pr-review-triage TEXT")
            else:
                recorded["triage"] = text
                saved.write_text(json.dumps(recorded, indent=2))
                pr_line = f"{recorded['author']} at {recorded['at'][:16]}; triage: {recorded['triage']}"
        else:
            pr_line, problems_ = pr_review_handoff(key, review_conf, recorded, args.pr_review_triage or [])
            problems_ += pr_review_gates.recorded_refusals(review_conf["reviewers"], record_reviewers(recorded), head)
            if problems_:
                gate_problems += problems_
            else:
                saved.write_text(json.dumps(recorded, indent=2))
    blocked = parse_blocked(args.blocked)
    (folder / "blocked.json").write_text(json.dumps({"head": head, "blocked": blocked}, indent=2))
    lines, problems = in_app_evidence(key, repo(), head, blocked)
    video_lines, video_problems = video_evidence(key, repo(), head)
    lines += video_lines
    problems = gate_problems + problems + video_problems
    if problems:
        raise Stop("the hand-off isn't complete:\n  " + "\n  ".join(problems))
    state_tool("capture-review", "--subject", key, "--candidate", head, *[x for p in rounds for x in ("--outcome", str(p))])
    conf = tracker_conf(c)
    when = conf["transitions"].get("reviewWhen")
    if when and when != "pipeline-green" or when == "pipeline-green" and pr_line:
        # A review move a refused call left owed is retried here, at the latest.
        review_move(c, key, when, last=True)
    post = f"hand-off: {args.pr}, head {head[:10]}, {len(rounds)} review round(s); {validation}."
    posted = handoff_and_post(c, key, lane, head, args.pr, len(rounds), post, ran)
    histories = [f"r{o['round']}: {round_history_text(o)}" for o in outcomes if round_history_text(o)]
    print("\n".join([
        f"**Hand-off: {key}** ({c.label(key)}, {lane})",
        f"- candidate: {args.pr}",
        f"- reviewed head: `{head}`",
        "- review rounds: " + "; ".join(f"r{o['round']} {o['outcome']}" for o in outcomes) + " (review evidence captured)"
        + (f"; history: {'; '.join(histories)}" if histories else ""),
        f"- validation: {validation}",
        *([f"- checks run: {', '.join(ran)}"] if ran else []),
        *([f"- PR review: {pr_line}"] if pr_line else []),
        "- in-app checks: " + ("; ".join(lines) or "none required"),
        "- run: none",
        "- lead inputs: none",
        f"- expensive non-blocking findings: {args.findings or 'none'}",
    ]))
    if posted:
        print(f"\n{posted}")
    else:
        print(f"\nTracker post (your story): {post}")


# -- the delivery posture ---------------------------------------------------------------------
# One declaration on the delivery's base says whether its close-out runs fullHealth. It is read fresh on every use,
# through git, from the tip of the remote's branch: never from the shelf, a working tree or a merge base, and never
# held between passes. The vectors in tests/fixtures/delivery-posture-vectors.json keep this reader and the harness's
# exported resolver giving the same answer for the same bytes.

POSTURE_FILE = ".agents/delivery-posture.json"
POSTURE_SCHEMA = "delivery-posture/1"
POSTURE_LIMIT_BYTES = 64 * 1024
POSTURE_SECONDS = 15
# Arrays and objects open at once, the top-level object being 1. Measured on the text before it is parsed, as the
# harness's reader measures it, so no decoder's own limit, which differs by Python version, is what decides.
POSTURE_MAX_DEPTH = 64
# What Python's strip and JavaScript's trim remove, together: the two disagree, and both readers must not.
POSTURE_BLANK = re.compile("[\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*")
# delivery-plan/1's caps and refusals, so what the record shows of a declaration can always go in the plan.
POSTURE_SOURCE_SHOWN, POSTURE_REASON_SHOWN = 200, 600
POSTURE_UNSHOWABLE = re.compile("[\u0000-\u001f\u007f-\u009f\u2028\u2029]+")


def posture_git(root: Path, args: list[str], seconds: int) -> subprocess.CompletedProcess | None:
    """One bounded git command, as bytes: None when it can't start or outruns its bound. In its own group, so a stopped
    fetch takes its transport with it and nothing is left holding the output open."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.Popen(["git", *args], cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError:
        return None
    try:
        out, err = proc.communicate(timeout=seconds)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=seconds)
        return None


def posture_nests_too_deep(text: str) -> bool:
    """Whether more than POSTURE_MAX_DEPTH arrays and objects are open at once anywhere in ``text``, outside strings.
    A string runs from an unescaped quote to the next, and inside one a backslash escapes exactly the next character."""
    depth, in_string, escaped = 0, False, False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > POSTURE_MAX_DEPTH:
                return True
        elif char in "]}":
            depth -= 1
    return False


def posture_of_bytes(raw: bytes) -> tuple[dict | None, str]:
    """A declaration's posture and reason, or None and what is wrong with it."""
    def constant(name: str) -> None:
        raise ValueError(f"{name} isn't JSON")
    try:
        text = raw.decode("utf-8")
    except ValueError:
        return None, "it isn't valid UTF-8 JSON"
    if posture_nests_too_deep(text):
        return None, f"it nests deeper than {POSTURE_MAX_DEPTH} levels"
    try:
        # Numbers as floats: a member is ignored whatever its size, as JSON.parse ignores it.
        value = json.loads(text, parse_constant=constant, parse_int=float)
    except (ValueError, RecursionError):
        return None, "it isn't valid UTF-8 JSON"
    if not isinstance(value, dict):
        return None, "it isn't an object"
    if value.get("schemaVersion") != POSTURE_SCHEMA:
        return None, f"its schemaVersion isn't {POSTURE_SCHEMA}"
    if not isinstance(value.get("posture"), str) or value["posture"] not in ("gate", "focused"):
        return None, "its posture is neither gate nor focused"
    reason = value.get("reason")
    if not isinstance(reason, str) or POSTURE_BLANK.fullmatch(reason):
        return None, "its reason is missing or blank"
    return {"posture": value["posture"], "reason": reason}, ""


def resolve_delivery_posture(root: Path, remote: str, branch: str, seconds: int = POSTURE_SECONDS) -> dict:
    """The posture the tip of `<remote>/<branch>` declares: {posture, reason, source, baseTipSha, code}. Anything but a
    valid declaration is `gate`, with the code that says why. Offline, the last fetched tip decides."""
    base = f"{remote}/{branch}"
    ref = f"refs/remotes/{remote}/{branch}"

    def gate(code: str, reason: str, tip: str | None, source: str = base) -> dict:
        return {"posture": "gate", "reason": f"{POSTURE_FILE} on {base}: {reason}; the posture is gate.", "source": source,
                "baseTipSha": tip, "code": code}

    def unreadable(what: str) -> dict:
        return gate("posture_unreadable", what, None, "fallback")

    posture_git(root, ["fetch", "-q", "--no-tags", remote, f"+refs/heads/{branch}:{ref}"], seconds)  # best effort
    found = posture_git(root, ["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], seconds)
    if found is None or found.returncode != 0:
        return unreadable(f"{ref} can't be resolved")
    tip = found.stdout.decode("ascii", "replace").strip()
    listed = posture_git(root, ["ls-tree", "--full-tree", "-z", "-l", tip, "--", POSTURE_FILE], seconds)
    if listed is None or listed.returncode != 0:
        return unreadable(f"the tree of {tip[:10]} can't be read")
    if not listed.stdout:
        return gate("posture_undeclared", "no file at the tip", tip)
    try:
        mode, kind, blob, size = listed.stdout.split(b"\t", 1)[0].decode("ascii", "replace").split()
    except ValueError:
        return unreadable(f"the tree of {tip[:10]} can't be read")
    if kind != "blob" or mode == "120000":
        return gate("posture_malformed", "a symlink, tree or submodule is at the path, not a file", tip)
    if not size.isdigit():
        return unreadable(f"the tree of {tip[:10]} can't be read")
    if int(size) > POSTURE_LIMIT_BYTES:
        return gate("posture_malformed", f"the file holds over {POSTURE_LIMIT_BYTES} bytes", tip)
    shown = posture_git(root, ["show", blob], seconds)
    if shown is None or shown.returncode != 0:
        return unreadable(f"the file at {tip[:10]} can't be read")
    declared, wrong = posture_of_bytes(shown.stdout)
    if declared is None:
        return gate("posture_malformed", wrong, tip)
    return {**declared, "source": base, "baseTipSha": tip, "code": None}


def delivery_posture(c) -> dict:
    """The delivery's own base decides, not the binding's: an integration base is the lead's own."""
    return resolve_delivery_posture(c.root, c.remote, c.base_branch)


def posture_said(posture: dict) -> dict:
    """{posture, source, reason} as the record shows them: within delivery-plan/1's caps and free of what it refuses
    or an argument can't carry, whatever the declaration held."""
    def shown(text: str, cap: int) -> str:
        clean = POSTURE_UNSHOWABLE.sub(" ", text.encode("utf-8", "replace").decode("utf-8")).strip()
        return clean[:cap].strip() or "(the declaration's text can't be shown)"
    return {"posture": posture["posture"], "source": shown(posture["source"], POSTURE_SOURCE_SHOWN),
            "reason": shown(posture["reason"], POSTURE_REASON_SHOWN)}


# -- the plan chapter's producer -------------------------------------------------------------------
# plan.json, the delivery-views input `delivery-plan/1`, is a pure function of the delivery file at the base, the
# append-only plan-changes.jsonl beside the lead's flow logs, the app-check and video flow logs, the acceptance log and
# the full-health log. Only lead-side commands and the reconcile sweep rebuild it, under one lock; lanes only append their flow logs,
# and the file is never read back as a source.

PLAN_FILE = "plan.json"
PLAN_CHANGES = "plan-changes.jsonl"
PLAN_RECORD_KINDS = [{"id": "review", "label": "Review", "source": "record"},
                     {"id": "handed", "label": "Handed off", "source": "record"},
                     {"id": "merged", "label": "Merged", "source": "record"}]
# One producer kind per in-app mode, in this order, and one for before/after clips.
PLAN_IN_APP_KINDS = {"hold": "In-app: loading state", "fail": "In-app: failure state", "loaded": "In-app: page loads",
                     "any": "In-app: pinned requests"}
PLAN_CLIP_KIND = {"id": "clip", "label": "Before/after clip", "source": "producer"}
LATE_SNAPSHOT = "taken after the delivery started"
PLAN_DEPTH = [0]


def plan_declared(c) -> bool:
    """The plan chapter is on once any story declares criteria."""
    return any(story.get("criteria") for story in c.stories.values())


def plan_log_path(c) -> Path:
    return c.flow_log / PLAN_CHANGES


def read_plan_log(c) -> list[dict]:
    path = plan_log_path(c)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def append_plan_log(c, entry: dict) -> dict:
    c.flow_log.mkdir(parents=True, exist_ok=True)
    entry = {**entry, "at": entry.get("at") or now()}
    with open(plan_log_path(c), "a") as log:
        log.write(json.dumps(entry) + "\n")
    return entry


class plan_lock:
    """The one writer's lock: the changes log, plan.json and its journal entry change only under it. Re-entered by
    the same process, so a command that records a change and then rebuilds holds it throughout."""

    def __init__(self, c):
        self.path = c.flow_log / "plan.lock"

    def __enter__(self):
        PLAN_DEPTH[0] += 1
        if PLAN_DEPTH[0] == 1:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.file = open(self.path, "a+")
            fcntl.flock(self.file, fcntl.LOCK_EX)
            PLAN_DEPTH.append(self.file)
        return self

    def __exit__(self, *_):
        PLAN_DEPTH[0] -= 1
        if PLAN_DEPTH[0] == 0:
            held = PLAN_DEPTH.pop()
            fcntl.flock(held, fcntl.LOCK_UN)
            held.close()


def plan_proofs(c, key: str) -> list[str]:
    """The proofs a story owes: the record's three, one per in-app mode its hand-off needs, and a clip if marked."""
    modes = {req["mode"] for req in c.requirements(key)}
    owed = [kind["id"] for kind in PLAN_RECORD_KINDS] + [f"in-app-{mode}" for mode in PLAN_IN_APP_KINDS if mode in modes]
    return owed + (["clip"] if c.stories[key].get("evidence") == "video" else [])


def plan_unit(c, key: str) -> dict:
    story = c.stories[key]
    return {"subject": key, **({"label": story["label"]} if story.get("label") else {}), "title": story["title"],
            "criteria": [{"id": item["id"], "text": item["text"]} for item in story.get("criteria", [])],
            "proofs": plan_proofs(c, key)}


def plan_covers(line: dict) -> dict:
    """A finish line's covers; a line that declares none covers every criterion, by every proof its units owe."""
    return line.get("covers", {"criteria": "all", "proofs": []})


def plan_finish(c, at: str) -> list[dict]:
    return [{"id": line["id"], "label": line.get("label", line["id"]), "text": line["text"], "at": line.get("at", at),
             "covers": plan_covers(line)} for line in c.finish]


def plan_view(c) -> dict:
    """What drift detection compares: each story's criteria and each finish line's covers."""
    return {"criteria": {key: {item["id"]: item["text"] for item in story.get("criteria", [])}
                         for key, story in c.stories.items()},
            "covers": {line["id"]: plan_covers(line) for line in c.finish}}


def view_of_snapshot(snapshot: dict) -> dict:
    return {"criteria": {unit["subject"]: {item["id"]: item["text"] for item in unit["criteria"]} for unit in snapshot["units"]},
            "covers": {line["id"]: line["covers"] for line in snapshot["finish"]}}


def view_digest(view: dict) -> str:
    return hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()


def snapshot_entry(c, late: bool) -> dict:
    at = now()
    return {"type": "snapshot", "at": at, "late": late, "units": [plan_unit(c, key) for key in c.stories],
            "finish": plan_finish(c, at)}


def write_snapshot(c, late: bool) -> dict:
    """The approved plan, frozen once: the changes log's first entry. Checked before it is frozen, since no later
    correction of the delivery file could repair a snapshot the view can't read."""
    entry = snapshot_entry(c, late)
    check_plan(build_plan(c, [entry]), c)
    return append_plan_log(c, entry)


def record_plan_change(c, change: dict) -> dict:
    """Append one change, numbered after every change before it."""
    with plan_lock(c):
        count = sum(1 for entry in read_plan_log(c) if entry["type"] == "change")
        return append_plan_log(c, {"type": "change", "id": f"c{count + 1}", **change})


def plan_drift(c, log: list[dict]) -> list[dict]:
    """The entries a rebuild appends when the delivery file moved since the last baseline: one `unrecorded` change per
    difference no recorded change explains, then the new baseline. Nothing when it has not moved."""
    view = plan_view(c)
    digest_now = view_digest(view)
    last_index = max((i for i, entry in enumerate(log) if entry["type"] in ("snapshot", "baseline")), default=None)
    if last_index is None:
        return []
    last = log[last_index]
    before = last["view"] if last["type"] == "baseline" else view_of_snapshot(last)
    if view_digest(before) == digest_now:
        return []
    recorded = [entry for entry in log[last_index + 1:] if entry["type"] == "change" and entry["by"] != "unrecorded"]

    def explained(kind: str, key: str | None = None, criterion: str | None = None, after=None, finish: str | None = None) -> bool:
        for change in recorded:
            if kind == "story-added" and change["kind"] == "added" and key in change["subjects"] and not change.get("criteria"):
                return True
            if kind == "story-removed" and change["kind"] == "removed" and key in change["subjects"]:
                return True
            if kind == "criterion" and criterion in change.get("criteria", []) and change.get("after") == after:
                return True
            if kind == "finish" and change["kind"] == "finish" and change.get("finish") == finish and change.get("after") == after:
                return True
        return False

    found = []
    for key in before["criteria"]:
        if key not in view["criteria"] and not explained("story-removed", key):
            found.append({"kind": "removed", "subjects": [key]})
    for key, criteria in view["criteria"].items():
        old = before["criteria"].get(key)
        if old is None and explained("story-added", key):
            continue
        old = old or {}
        for ident, text in criteria.items():
            if old.get(ident) != text and not explained("criterion", key, ident, text):
                found.append({"kind": "added" if ident not in old else "changed", "subjects": [key], "criteria": [ident],
                              **({"before": old[ident]} if ident in old else {}), "after": text})
        for ident, text in old.items():
            if ident not in criteria and not explained("criterion", key, ident, None):
                found.append({"kind": "changed", "subjects": [key], "criteria": [ident], "before": text})
    for ident, covers in view["covers"].items():
        if before["covers"].get(ident) != covers and not explained("finish", finish=ident, after=covers):
            found.append({"kind": "finish", "subjects": [], "finish": ident,
                          **({"before": before["covers"][ident]} if ident in before["covers"] else {}), "after": covers})
    count = sum(1 for entry in log if entry["type"] == "change")
    entries = [{"type": "change", "id": f"c{count + i + 1}", "by": "unrecorded", "source": "drift", **change}
               for i, change in enumerate(found)]
    return [*entries, {"type": "baseline", "digest": digest_now, "view": view}]


def satisfied_requirements(reqs: list[dict], runs: list[dict]) -> set[int]:
    """The requirements a set of passing runs meets: each mode requirement by a run of its own (a bipartite matching,
    so one run never stands in for two checks), each pattern requirement by any run that used its pattern."""
    def satisfies(req, r):
        if req["mode"] != "any" and r["mode"] != req["mode"]:
            return False
        if req.get("pattern") and req["pattern"] not in r["spec"].get("holds", []) + r["spec"].get("fails", []):
            return False
        args = req.get("args")
        if args is None:
            return True
        if r.get("passthrough") is None:
            return False
        if not args:  # "args": [] means a run with no adapter arguments
            return not r["passthrough"]
        return any(r["passthrough"][i:i + len(args)] == args for i in range(len(r["passthrough"])))

    modal = [(qi, q) for qi, q in enumerate(reqs) if q["mode"] != "any"]
    owner: dict[int, int] = {}  # run index -> requirement index

    def assign(qi: int, req: dict, seen: set[int]) -> bool:
        for ri, run_ in enumerate(runs):
            if ri in seen or not satisfies(req, run_):
                continue
            seen.add(ri)
            if ri not in owner or assign(owner[ri], reqs[owner[ri]], seen):
                owner[ri] = qi
                return True
        return False

    met = {qi for qi, req in modal if assign(qi, req, set())}
    return met | {qi for qi, req in enumerate(reqs) if req["mode"] == "any" and any(satisfies(req, r) for r in runs)}


def plan_flow_log(c, name: str, key: str) -> list[dict]:
    path = c.flow_log / f"{key}.{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def plan_current(c, at: str, head: str) -> bool:
    """Whether a run at `at` still stands at `head`: the same commit, or no shipped file changed between them."""
    if at == head:
        return True
    done = run(["git", "diff", "--name-only", at, head, "--", *((c.ship or {}).get("paths") or ["."])], cwd=c.root,
               check=False)
    return done.returncode == 0 and not [f for f in done.stdout.splitlines() if f and not re.search(c.test_files, f)]


def plan_evidence(c, key: str) -> dict:
    """The story's producer proofs. The head they are judged at is the one the lead last accepted, or before any
    acceptance, the latest head with a passing check. A kind is done when every required item passed at a head current
    with it, and waived when every missing item was accepted with --accept-blocked there."""
    runs = [entry for entry in plan_flow_log(c, "app-checks", key) if entry.get("head")]
    accepted = (plan_flow_log(c, "acceptance", key) or [None])[-1]
    passing = [entry for entry in runs if entry["pass"]]
    head = accepted["head"] if accepted else (passing[-1]["head"] if passing else None)
    current = lambda at: head is not None and plan_current(c, at, head)
    fresh = [{"mode": e["mode"], "spec": e.get("specValue") or {}, "passthrough": e.get("passthrough"), "at": e["at"]}
             for e in passing if current(e["head"])]
    reqs = c.requirements(key)
    met = satisfied_requirements(reqs, fresh)
    evidence = {}
    for mode in PLAN_IN_APP_KINDS:
        mine = [(qi, req) for qi, req in enumerate(reqs) if req["mode"] == mode]
        if not mine:
            continue
        items = [requirement_label(req) for _, req in mine]
        missing = [requirement_label(req) for qi, req in mine if qi not in met]
        blocked = (accepted or {}).get("blocked", {})
        if not missing:
            evidence[f"in-app-{mode}"] = {"state": "done", "head": head, "at": max(e["at"] for e in fresh), "items": items}
        elif accepted and all(label in blocked for label in missing):
            evidence[f"in-app-{mode}"] = {"state": "waived", "head": head, "at": accepted["at"], "items": items,
                                          "reason": "; ".join(f"{label}: {blocked[label]}" for label in missing)}
        else:
            evidence[f"in-app-{mode}"] = {"state": "todo", "items": items}
    if c.stories[key].get("evidence") == "video":
        clips = [entry for entry in plan_flow_log(c, "videos", key) if current(entry["head"])]
        evidence["clip"] = ({"state": "done", "head": head, "at": clips[-1]["at"], "items": [e["name"] for e in clips]}
                            if clips else {"state": "todo"})
    return evidence


def build_plan(c, log: list[dict]) -> dict:
    """The plan document, from the delivery file and the changes log."""
    snapshot = next(entry for entry in log if entry["type"] == "snapshot")
    units = [plan_unit(c, key) for key in c.stories]
    held = {unit["subject"] for unit in units}
    last_seen = {unit["subject"]: unit for unit in snapshot["units"]}
    for entry in log:
        for unit in entry.get("units", []) if entry["type"] == "change" else []:
            last_seen[unit["subject"]] = unit
        if entry["type"] == "change" and entry["kind"] == "added":
            for key in entry["subjects"]:
                last_seen.setdefault(key, {"subject": key, "title": entry.get("title", key), "criteria": []})
    for key, unit in last_seen.items():
        if key not in held:
            units.append({**unit, "removed": True, "proofs": [kind["id"] for kind in PLAN_RECORD_KINDS]})
    owed = {kind for unit in [*units, *snapshot["units"]] for kind in unit["proofs"]}
    kinds = [*PLAN_RECORD_KINDS, *({"id": f"in-app-{mode}", "label": label, "source": "producer"}
                                    for mode, label in PLAN_IN_APP_KINDS.items() if f"in-app-{mode}" in owed),
             *([PLAN_CLIP_KIND] if "clip" in owed else [])]
    whys = {entry["change"]: entry["why"] for entry in log if entry["type"] == "why"}
    changes = []
    for entry in (e for e in log if e["type"] == "change"):
        change = {key: entry[key] for key in ("id", "at", "kind", "subjects", "criteria", "by", "why", "title", "source",
                                              "ref", "before", "after") if key in entry}
        if entry["id"] in whys:
            change["why"] = whys[entry["id"]]
        changes.append(change)
    outcome = c.raw.get("outcome") or {"summary": c.raw.get("headline", c.delivery), "text": c.raw.get("headline", c.delivery)}
    review = plan_review_of(c)
    said = " · ".join(words for words in (LATE_SNAPSHOT if snapshot.get("late") else None, plan_review_words(review))
                      if words)
    plan = {
        "schemaVersion": "delivery-plan/1", "updated": now(), "outcome": outcome,
        "requiredLenses": c.raw.get("requiredLenses", list(c.lenses)), "proofKinds": kinds, "units": units,
        "finish": plan_finish(c, snapshot["at"]),
        "approved": {"at": snapshot["at"], **({"review": said} if said else {}),
                     "snapshot": {"units": snapshot["units"], "finish": snapshot["finish"]}},
        "changes": changes,
        "evidence": {key: found for key in c.stories if (found := plan_evidence(c, key))},
    }
    for key in ("approvals", "outOfScope"):
        if key in c.raw:
            plan[key] = c.raw[key]
    if review:
        plan["planReview"] = review
    skip = full_health_skip(c)
    if skip:
        plan["closeOut"] = {"fullHealth": "skipped", **{key: skip[key] for key in ("posture", "source", "reason")}}
    return plan


def plan_validator(c):
    """The release's own delivery-plan/1 validator, so the producer and the view never drift apart."""
    path = str(c.release / "skills/delivery-views/scripts")
    if path not in sys.path:
        sys.path.insert(0, path)
    sys.dont_write_bytecode = True
    import views_common  # noqa: E402  (the installed release's own validator)
    return views_common


def check_plan(plan: dict, c) -> None:
    views = plan_validator(c)
    try:
        views.validate_plan(json.loads(json.dumps(plan)))
    except views.ConfigError as error:
        raise Stop(f"the plan this delivery file makes isn't a valid delivery-plan/1: {error}")
    # Measured as rebuild_plan writes it, so the view never meets a file it refuses unread.
    size = len((json.dumps(plan, indent=2) + "\n").encode("utf-8"))
    if size > views.PLAN_LIMIT_BYTES:
        raise Stop(f"the plan this delivery file makes holds {size} bytes, over the 1 MiB a plan may hold")


def rebuild_plan(c, mark: bool = False) -> str | None:
    """Rebuild plan.json under the lock. Writes it, atomically, only when its content apart from `updated` changed,
    then journals the write so the live view re-renders. Returns what changed, or None when nothing did. With
    `mark`, a plan change the lead made, the journal entry is marked for the tracker publisher to post."""
    if not (c.state / "state.json").exists():
        return None
    with plan_lock(c):
        # The delivery file as it stands now, under the lock: a long-running sweep must not build from the one it
        # read when it started. A story added to it is registered first, so its `added` change explains it.
        c.load_delivery(c.delivery)
        register_stories(c)
        log = read_plan_log(c)
        if not any(entry["type"] == "snapshot" for entry in log):
            if not plan_declared(c):
                return None
            log.append(write_snapshot(c, late=True))
        for entry in plan_drift(c, log):
            log.append(append_plan_log(c, entry))
        plan = build_plan(c, log)
        check_plan(plan, c)
        path = c.state / PLAN_FILE
        old = json.loads(path.read_text()) if path.exists() else None
        same = lambda a, b: {k: v for k, v in a.items() if k != "updated"} == {k: v for k, v in b.items() if k != "updated"}
        if old is not None and same(old, plan):
            return None
        moved = "first build" if old is None else "changed " + ", ".join(
            key for key in sorted({*plan, *old}) if key != "updated" and old.get(key) != plan.get(key))
        temporary = path.with_name(f".{PLAN_FILE}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(plan, indent=2) + "\n")
        os.replace(temporary, path)
        summary = f"Plan updated: {plan['updated']}; {moved}"
        if mark:
            with marked_decision(c) as written:
                written.append(journal_id(state_tool("journal", "--kind", "decision.recorded", "--summary", summary)))
        else:
            state_tool("journal", "--kind", "decision.recorded", "--summary", summary)
        return moved


def rebuild_plan_quietly(c) -> str:
    """For the sweep: one line, and a plan that won't build never stops the sweep."""
    try:
        moved = rebuild_plan(c)
    except (Stop, ValueError) as error:  # a refusal, or a delivery file saved mid-edit that isn't JSON
        return f"plan: not rebuilt: {error}"
    return f"plan: {moved}" if moved else "plan: unchanged"


def lead_plan_checkout(c) -> Path:
    """The lead's checkout a plan change or approval commits in: on the base branch, as before, or a lead's own
    worktree on its own branch (several deliveries on one machine, the operator's checkout holding the base), whose
    HEAD is the base's tip or ahead of it with this delivery's own delivery file and plan commits only. Either is
    clean and holds the base; a story branch is a lane's. A lead worktree then reads its own working tree."""
    root = c.root
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    # Any delivery's story branch here is a lane's, not only this delivery's: every delivery file, this one's included.
    stories = {key for d in c.delivery_files().values() for key in (d.get("stories") or {})}
    if any(branch == c.branch(key) for key in stories):
        raise Stop(f"run lead-plan in the lead's checkout of {c.base_branch}, or in a lead worktree of its own; "
                   f"{branch} is a story branch")
    if git("status", "--porcelain", cwd=root):
        raise Stop(f"{root} has uncommitted changes; commit or remove them first, so the plan change commits alone")
    git("fetch", "-q", c.remote, c.base_branch, cwd=root)
    if not is_ancestor(c.base_ref, "HEAD"):
        if is_ancestor("HEAD", c.base_ref):
            raise Stop(f"{root} is behind {c.base_ref}; bring it up to date first: git merge --ff-only {c.base_ref}")
        raise Stop(f"{root} has diverged from {c.base_ref}; bring it up to date first")
    if branch == c.base_branch:
        return root
    own = {f"{c.deliveries_rel}/{c.delivery}.json", Path(c.plan).as_posix()}
    for sha in git("rev-list", "--reverse", f"{c.base_ref}..HEAD", cwd=root).split():
        # --cc lists what a merge itself adds beyond its parents, nothing for a clean one; -z keeps any path whole.
        changed = run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--cc", sha], cwd=root).stdout
        foreign = [name for name in changed.split("\0") if name and name not in own]
        if foreign:
            subject = git("log", "-1", "--format=%s", sha, cwd=root)
            raise Stop(f"{root} is ahead of {c.base_ref} with {sha[:12]} ({subject}), which changes "
                       f"{', '.join(foreign)}: a lead worktree off {c.base_branch} may be ahead with only "
                       f"{c.delivery}'s own delivery file and plan; push that commit or drop it first")
    # What the base holds, plus this delivery's own unpushed plan commits: the working tree is the plan to change.
    # Read it again through that shelf, so the stories a change names (a removal's units, say) are the worktree's.
    c.shelf = Shelf(root)
    c.load_delivery(c.delivery)
    return root


def lead_plan(args) -> None:
    """Record a change to the approved plan in the delivery file, or a reason for one already recorded."""
    c = cfg()
    log = read_plan_log(c)
    if not plan_declared(c) and not any(entry["type"] == "snapshot" for entry in log):
        raise Stop(f"{c.delivery_where} declares no criteria, so the plan chapter is off; add criteria to its stories first")
    # Its rebuild registers any story added since, so a story registration would refuse is refused before anything
    # is written; one this change removes needs no reason.
    if (c.state / "state.json").exists():
        removing = set(args.subjects or []) if args.action == "change" and args.kind == "removed" else set()
        problems = unreasoned_problems(c, [key for key in unregistered(c, load_state()["state"]) if key not in removing])
        if problems:
            raise unreasoned_refusal(c, problems)
    if args.action == "why":
        with plan_lock(c):
            log = read_plan_log(c)
            if not any(entry["type"] == "change" and entry["id"] == args.change for entry in log):
                raise Stop(f"no recorded change {args.change}; the changes are in {plan_log_path(c)}")
            append_plan_log(c, {"type": "why", "change": args.change, "why": args.why})
            rebuild_plan(c)
        print(f"recorded the reason for {args.change}")
        return
    root = lead_plan_checkout(c)
    raw = json.loads(c.delivery_path.read_text())
    if args.action == "approval":
        plan_approval(c, raw, args)
        return
    stories = raw["stories"]
    change: dict = {"kind": args.kind, "subjects": args.subjects or [], "by": args.by, "why": args.why, "source": "lead-plan"}
    for key in ("title", "ref"):
        if getattr(args, key):
            change[key] = getattr(args, key)
    new_story = args.kind == "added" and len(change["subjects"]) == 1 and change["subjects"][0] not in stories
    unknown = [key for key in change["subjects"] if key not in stories]
    if unknown and not new_story:
        raise Stop(f"{', '.join(unknown)} isn't a story of {c.delivery}")
    planned_after = None
    record_finish = None
    reword_finish = None
    wording = None  # a finish line's (before, after) wording, recorded as its own change when its covers change too
    plan_text = None
    if new_story:
        # The whole story in one change: its plan heading, its delivery-file story carrying the reason its `added`
        # change gives, and its planned entry, so registration has nothing left to refuse.
        key = change["subjects"][0]
        if not args.title or not args.text or not args.lane:
            raise Stop(f"{key} isn't a story of {c.delivery} yet: --kind added adds it whole with --title, --text (its "
                       "first criterion) and --lane, with --after for the stories it comes after")
        if args.lane not in c.lanes:
            raise Stop(f"--lane {args.lane} isn't a lane of {c.delivery}: {', '.join(c.lanes)}")
        after = args.after or []
        strangers = [other for other in after if other not in stories]
        if strangers:
            raise Stop(f"--after {', '.join(strangers)} isn't a story of {c.delivery}")
        problem = why_problem(c, args.why)
        if problem:
            raise Stop(f"{key} {problem}: --why is one line saying why it joins the delivery")
        ident = args.criterion or f"{key.lower()}-1"
        if ident in [item["id"] for story in stories.values() for item in story.get("criteria", [])]:
            raise Stop(f"criterion {ident} already exists")
        stories[key] = {"title": args.title, "lane": args.lane, "why": args.why,
                        "criteria": [{"id": ident, "text": args.text}], **({"after": after} if after else {})}
        current = (c.root / c.plan).read_text()
        if story_section(plan_sections(current), key) is None:
            plan_text = plan_with_story(current, key, args.title, args.lane, after, args.why)
    elif args.kind in ("added", "changed", "relaxed"):
        if len(change["subjects"]) != 1 or not args.criterion or args.text is None:
            raise Stop(f"--kind {args.kind} takes one story in --subjects, --criterion and --text")
        criteria = stories[change["subjects"][0]].setdefault("criteria", [])
        existing = next((item for item in criteria if item["id"] == args.criterion), None)
        taken = [item["id"] for story in stories.values() for item in story.get("criteria", [])]
        if args.kind == "added":
            if args.criterion in taken:
                raise Stop(f"criterion {args.criterion} already exists")
            criteria.append({"id": args.criterion, "text": args.text})
        else:
            if existing is None:
                raise Stop(f"{change['subjects'][0]} has no criterion {args.criterion}")
            change["before"] = existing["text"]
            existing["text"] = args.text
        change.update(criteria=[args.criterion], after=args.text)
    elif args.kind == "removed":
        if not change["subjects"]:
            raise Stop("--kind removed names the stories it removes in --subjects")
        doc = load_state()
        queued = [q["subject"] for q in doc["state"]["queue"] if q["subject"] in change["subjects"]]
        if queued:
            raise Stop(f"{', '.join(queued)} is already in the queue; it can't be removed from the plan")
        change["units"] = [plan_unit(c, key) for key in change["subjects"]]
        for key in change["subjects"]:
            del stories[key]
        planned_after = True
    elif args.kind == "finish":
        lines = raw.setdefault("finish", [])
        line = next((item for item in lines if item["id"] == args.finish), None) if args.finish else None
        # An existing line's wording alone may change: --text without --covers keeps what it covers.
        if not args.finish or (args.covers is None and (line is None or args.text is None)):
            raise Stop("--kind finish takes --finish <id> and --covers <JSON>; an existing line's --text alone "
                       "rewords it")
        try:
            covers = json.loads(args.covers) if args.covers is not None else plan_covers(line)
        except ValueError:
            raise Stop("--covers must be JSON, such as {\"criteria\": \"all\", \"proofs\": [\"merged\"]}")
        if line is None:
            if not args.text:
                raise Stop(f"finish line {args.finish} is new: give its --text")
            line = {"id": args.finish, "text": args.text, **({"label": args.label} if args.label else {})}
            lines.append(line)
            record_finish = {"id": args.finish, "text": args.text, "met": False}
            change.update(after=covers)
        else:
            reworded = args.text is not None and args.text != line["text"]
            if not reworded and covers == plan_covers(line):
                raise Stop(f"finish line {args.finish} already reads so and covers that: nothing to change")
            # What changed is recorded: what it covers, and its wording when that moved, as a change of its own.
            if covers != plan_covers(line):
                change.update(before=plan_covers(line), after=covers)
                if reworded:
                    wording = (line["text"], args.text)
            else:
                change.update(before=line["text"], after=args.text)
            if reworded:
                reword_finish = (args.finish, args.text)
                line["text"] = args.text
        line.update(covers=covers, at=now())
        change.update(finish=args.finish)
    with plan_lock(c):
        # A delivery started before its criteria has no snapshot yet: it is taken of the plan before this change.
        if (c.state / "state.json").exists() and not any(entry["type"] == "snapshot" for entry in read_plan_log(c)):
            write_snapshot(c, late=True)
        original = c.delivery_path.read_text()
        plan_path = c.root / c.plan
        plan_original = plan_path.read_text() if plan_text is not None else None
        c.delivery_path.write_text(json.dumps(raw, indent=2) + "\n")
        if plan_text is not None:
            plan_path.write_text(plan_text)
        try:
            c.load_delivery(c.delivery)
            check_plan(build_plan(c, read_plan_log(c) or [snapshot_entry(c, late=True)]), c)
        except Stop:
            c.delivery_path.write_text(original)
            if plan_original is not None:
                plan_path.write_text(plan_original)
            c.load_delivery(c.delivery)
            raise
        git("add", str(c.delivery_path), *([str(plan_path)] if plan_text is not None else []), cwd=root)
        entry = record_plan_change(c, change)
        ids = entry["id"]
        if wording is not None:
            ids += f" and {record_plan_change(c, {**change, 'before': wording[0], 'after': wording[1]})['id']}"
        git("commit", "-q", "-m", f"{c.delivery}: plan change {ids} ({'added story' if new_story else args.kind} "
            f"{', '.join(change['subjects']) or change.get('finish', '')})".rstrip(), cwd=root)
        if planned_after is not None:  # as it stands now: a story registered since the check above stays planned
            patch_from(lambda doc: {"planned": [p for p in doc["state"].get("planned", [])
                                                if p["subject"] not in change["subjects"]]}, "the plan change's planned work")
        if record_finish is not None:
            patch_from(lambda doc: {"finish": [*doc["state"]["finish"], record_finish]}, "the new finish line")
        if reword_finish is not None and (c.state / "state.json").exists():  # the record's line reads as the plan's
            ident, text = reword_finish
            patch_from(lambda doc: None if all(line["id"] != ident or line["text"] == text for line in doc["state"]["finish"])
                       else {"finish": [{**line, "text": text} if line["id"] == ident else line
                                        for line in doc["state"]["finish"]]}, "the reworded finish line")
        if new_story and (c.state / "state.json").exists():
            # Planned here, so the rebuild's registration finds it held and records no second `added` change.
            key = change["subjects"][0]
            patched = patch_from(lambda doc: None if any(item["subject"] == key for item in
                                                        [*doc["state"]["queue"], *doc["state"].get("planned", [])])
                                 else {"planned": [*doc["state"].get("planned", []), planned_entry(c, key)],
                                       "followups": [f for f in doc["state"]["followups"]
                                                     if key not in (f["id"], f.get("reference"))]},
                                 "the added story's planned work")
            if patched is not None:
                state_tool("journal", "--kind", "decision.recorded", "--subject", key, "--summary",
                           f"{key} added to the delivery as planned work for {c.lane_of(key)}")
        rebuild_plan(c, mark=True)
    print(f"recorded {ids}: {args.kind} {', '.join(change['subjects']) or change.get('finish', '')}".rstrip())
    print(f"committed the delivery file{' and the plan' if plan_text is not None else ''}; push it so lanes see it: "
          f"git push {c.remote} HEAD:{c.base_branch}")


def plan_approval(c, raw: dict, args) -> None:
    """Mark one approval done or waiting, with lead-plan change's discipline: one commit, never a push."""
    entry = next((item for item in raw.get("approvals") or [] if item.get("id") == args.id), None)
    if entry is None:
        raise Stop(f"no approval {args.id} in {c.delivery_where}; an approval without an id is changed by hand")
    entry["state"] = args.state
    if args.who:
        entry["who"] = args.who
    with plan_lock(c):
        original = c.delivery_path.read_text()
        c.delivery_path.write_text(json.dumps(raw, indent=2) + "\n")
        try:
            c.load_delivery(c.delivery)
            check_plan(build_plan(c, read_plan_log(c) or [snapshot_entry(c, late=True)]), c)
        except Stop:
            c.delivery_path.write_text(original)
            c.load_delivery(c.delivery)
            raise
        git("add", str(c.delivery_path), cwd=c.root)
        git("commit", "-q", "-m", f"{c.delivery}: approval {args.id} {args.state}", cwd=c.root)
        rebuild_plan(c)
    print(f"approval {args.id}: {args.state}")
    print(f"committed the delivery file; push it so lanes see it: git push {c.remote} HEAD:{c.base_branch}")


def archive_plan_review(archive: Path) -> Path | None:
    """The plan review's store, without its checkouts, copied into a delivery's archive."""
    c = cfg()
    source = plan_review_dir(c)
    if not source.exists():
        return None
    target = archive / "plan-review"
    shutil.copytree(source, target, ignore=lambda folder, names: ["checkouts"] if Path(folder) == source else [])
    return target


# -- setup refusals ------------------------------------------------------------------------

def supervisor_lock_path(c) -> Path:
    """The lock the delivery's supervisor holds while it runs. It records {pid, startedAt, children}."""
    return c.flow_log / f"{c.delivery}.supervisor.lock"


def read_lock(path: Path) -> tuple[bool, dict]:
    """(whether a process holds the lock, what the lock file records)."""
    if not path.exists():
        return False, {}
    with open(path, "a+") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe, fcntl.LOCK_UN)
            held = False
        except BlockingIOError:
            held = True
    try:
        recorded = json.loads(path.read_text() or "{}")
    except ValueError:
        recorded = {}
    return held, recorded if isinstance(recorded, dict) else {}


def record_started(c, journal: list[dict]) -> str:
    if journal:
        return journal[0]["at"]
    stamp = (c.state / "state.json").stat().st_mtime
    return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stale_record(c) -> str | None:
    """What an earlier lead-init left where this one would start: a record, a supervisor's lock, or a plan snapshot.
    A lead-plan change made before lead-init, and plan.lock, are this delivery's own and don't count."""
    fix = ("fix: run lead-init --replace, which stops its supervisor and archives the record and the flow log, "
           "carrying nothing over; or give this delivery its own stateDirectory, in a folder of its own, since the "
           "flow log sits beside it")
    if (c.state / "state.json").exists():
        try:
            doc = load_state()
        except Stop as unreadable:
            return f"{c.state} already holds a record the state tool can't read ({unreadable}); {fix}"
        meta, journal = doc["state"]["meta"], doc["journal"]
        name = meta.get("trackerParent") or meta.get("headline") or "with no name"
        recorded = next((re.search(r"^Record starts at base \S+ on (\S+?)\.(?: |$)", e.get("summary", ""))
                         for e in journal if e.get("kind") == "decision.recorded"), None)
        base = recorded.group(1) if recorded else c.base_ref if name == c.delivery else "an unrecorded base"
        return f"{c.state} already holds a record: delivery {name} on {base}, started {record_started(c, journal)}; {fix}"
    lock = supervisor_lock_path(c)
    if lock.exists():
        held, recorded = read_lock(lock)
        who = f"held by pid {recorded.get('pid')}" if held else "which no process holds"
        return (f"{lock} holds the supervisor lock of delivery {c.delivery} on {c.base_ref}, {who}, started "
                f"{recorded.get('startedAt', 'at an unrecorded time')}; {fix}")
    snapshot = next((entry for entry in read_plan_log(c) if entry["type"] == "snapshot"), None)
    if snapshot:
        return (f"{c.flow_log} already holds the plan snapshot of delivery {c.delivery} on {c.base_ref}, taken "
                f"{snapshot['at']}; {fix}")
    return None


def process_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def stop_supervisor(c) -> str | None:
    """Stop the process holding the supervisor lock, by the pid it recorded, and confirm it is gone."""
    lock = supervisor_lock_path(c)
    held, recorded = read_lock(lock)
    if not held:
        return None
    pid = recorded.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        raise Stop(f"{lock} is held, but records no pid to stop; fix: stop the delivery's supervisor yourself, then "
                   "run lead-init --replace again")
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(pid, sig)  # the supervisor leads its own session, so its children go with it
        except ProcessLookupError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        except PermissionError:
            raise Stop(f"the supervisor in {lock} (pid {pid}) can't be stopped from here; fix: stop it as its own user, "
                       "then run lead-init --replace again")
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if process_gone(pid) and not read_lock(lock)[0]:
                return f"stopped the supervisor (pid {pid})"
            time.sleep(0.1)
    raise Stop(f"the supervisor in {lock} (pid {pid}) is still running after SIGKILL; fix: stop it, then run lead-init "
               "--replace again")


def archive_root(c) -> Path:
    return Path(expand((c.flow_raw.get("cleanup") or {}).get("archive", DEFAULT_ARCHIVE), repo=c.main.name,
                       delivery=c.delivery))


def replace_record(c) -> list[str]:
    """lead-init --replace: stop any supervisor, then move the record and the flow log into the archive. Nothing
    carries over: every flow-log file names entries of the archived journal, whose ids the fresh one reuses."""
    said = [line for line in [stop_supervisor(c), stop_full_health_run(c)] if line]
    present = [path for path in (c.state, c.flow_log) if path.exists()]
    if present:
        archive = archive_root(c) / (datetime.datetime.now().strftime("%Y%m%dT%H%M%S.%f") + "-replaced")
        archive.mkdir(parents=True)
        for source in present:
            shutil.move(str(source), str(archive / source.name))
        said.append(f"archived the record and the flow log in {archive}")
    return said


def plan_problems(c, keys: list[str]) -> list[str]:
    """Each story with no section in the plan, which its lane's review context is drawn from."""
    text = c.shelf.text(c.plan)
    if text is None:
        return [f"no plan at {c.shelf.where(c.plan)}; fix: commit the plan there, or correct `plan` in {c.delivery_where}"]
    sections = plan_sections(text)
    missing = [key for key in keys if story_section(sections, key) is None]
    if not missing:
        return []
    return [f"stories with no plan section: {', '.join(missing)}; fix: add a heading naming each to {c.plan}, or "
            f"correct its key in {c.delivery_where}"]


# Ownership is the plan's: lead-accept refuses a hand-off that changes a file outside its lane's `owns`. These warn
# before a lane starts of the files its story will need: the ones its plan names, and (for lead-assign) the ones that
# have tended to change with them.

PATH_TOKEN = re.compile(r"[A-Za-z0-9._*?\[\]-][A-Za-z0-9._/*?\[\]-]*")
CO_CHANGE = {"commits": 200, "sweep": 40, "support": 3, "share": 0.25, "shown": 5}


def owner_of(c, path: str) -> str | None:
    return next((lane for lane, entry in c.lanes.items() if any(fnmatch.fnmatch(path, g) for g in entry["owns"])), None)


def owned_by(c, lane: str, path: str) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in c.lanes[lane]["owns"])


def owner_words(c, path: str) -> str:
    owner = owner_of(c, path)
    return f"{owner}'s" if owner else "no lane's"


def files_ref(c) -> str:
    return c.base_ref if run(["git", "rev-parse", "--verify", "--quiet", c.base_ref], check=False).returncode == 0 else "HEAD"


def tracked_at_base(c) -> list[str]:
    """The base's files a story can change: not the installed release, which no lane edits."""
    installed = Path(RECEIPT).parent.as_posix() + "/"
    return [f for f in git("ls-tree", "-r", "--name-only", files_ref(c), check=False).splitlines()
            if not f.startswith(installed)]


def story_named_files(c, key: str, tracked: list[str]) -> list[str]:
    """The files a story's plan section, and the table rows naming it, name in backticks: a full path, a directory
    (trailing /), a glob, or a bare file name, resolved against the base's files. A bare name that matches several takes
    the lane's own match when there is one; a path not tracked yet is the new file it names. A row names the story when
    a cell with no backticked text does, and its files come from its other cells, so a passing mention in a files cell
    (such as "after" another story's key) doesn't hand that row's files to the story."""
    text = c.shelf.text(c.plan) or ""
    own = story_section(plan_sections(text), key) or ""
    names = {key, c.label(key)}
    naming = lambda cell: "`" not in cell and any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", cell) for n in names)
    rows = []
    for line in text.splitlines():
        cells = line.strip().strip("|").split("|") if line.startswith("|") else []
        if any(naming(cell) for cell in cells):
            rows += [cell for cell in cells if not naming(cell)]
    lane = c.lane_of(key)
    found: set[str] = set()
    for token in re.findall(r"`([^`\n]+)`", "\n".join([own, *rows])):
        token = token.strip().removeprefix("./")
        if not PATH_TOKEN.fullmatch(token) or not ("/" in token or re.search(r"\.[A-Za-z0-9]{1,8}$", token)):
            continue
        if token.endswith("/"):
            found |= {f for f in tracked if f.startswith(token)}
        elif "/" not in token:
            matches = [f for f in tracked if fnmatch.fnmatch(f.rsplit("/", 1)[-1], token)]
            mine = [f for f in matches if owned_by(c, lane, f)]
            found |= set(mine or matches)
        elif re.search(r"[*?\[]", token):
            found |= {f for f in tracked if fnmatch.fnmatch(f, token)}
        elif token in tracked or not (tail := [f for f in tracked if f.endswith("/" + token)]):
            found.add(token)  # a tracked path, or the new file it names
        else:  # the end of a tracked path, such as references/adopting.md
            mine = [f for f in tail if owned_by(c, lane, f)]
            found |= set(mine or tail)
    return sorted(found)


def outside_lane(c, key: str, tracked: list[str]) -> list[str]:
    """The files a story names that its lane doesn't own."""
    lane = c.lane_of(key)
    return [f for f in story_named_files(c, key, tracked) if not owned_by(c, lane, f)]


def named_outside(c, key: str, outside: list[str], shown: int | None = None) -> str:
    """"FX-2 names a (lane-2's), b (no lane's)", to `shown` files and a count of the rest."""
    listed = ", ".join(f"{f} ({owner_words(c, f)})" for f in outside[:shown])
    rest = len(outside) - len(outside[:shown])
    return f"{key} names {listed}" + (f" and {rest} more" if rest else "")


def story_file_problems(c, keys: list[str], tracked: list[str]) -> list[str]:
    """One phrase per story that names a file outside its lane, as doctor's one line has room for: three files a
    story, five stories, and how many more."""
    found = [(key, outside) for key in keys if (outside := outside_lane(c, key, tracked))]
    phrases = [named_outside(c, f"{key} ({c.lane_of(key)})", outside, 3) for key, outside in found[:5]]
    if len(found) > 5:
        phrases.append(f"and {len(found) - 5} more stories (lead-assign lists each one's)")
    return phrases


def co_changed(c, path: str, ref: str) -> tuple[int, collections.Counter]:
    """How many of the recent changes to `path` on `ref` count, and how often each other file changed with it. A
    sweeping change (more than CO_CHANGE['sweep'] files) says nothing about coupling, so it isn't counted."""
    out = git("log", "--no-merges", "--full-diff", f"-n{CO_CHANGE['commits']}", "--format=tformat:@", "--name-only", ref, "--",
              path, check=False)
    count, together = 0, collections.Counter()
    for change in out.split("@")[1:]:
        files = set(change.split())
        if not files or len(files) > CO_CHANGE["sweep"]:
            continue
        count += 1
        together.update(files - {path})
    return count, together


def co_change_warning(c, key: str, tracked: list[str]) -> str | None:
    """The files outside a story's lane that changed with a file it names in at least CO_CHANGE['support'] and
    CO_CHANGE['share'] of that file's changes: the CO_CHANGE['shown'] most coupled, each with the named file it
    changes with most."""
    lane = c.lane_of(key)
    named = story_named_files(c, key, tracked)
    ref, present = files_ref(c), set(tracked)
    best: dict[str, tuple[float, int, int, str]] = {}
    for path in named:
        if path not in present:
            continue
        count, together = co_changed(c, path, ref)
        for f, n in together.items():
            if (n >= CO_CHANGE["support"] and n >= CO_CHANGE["share"] * count and f in present and f not in named
                    and not owned_by(c, lane, f) and (n / count, n) > best.get(f, (0, 0))[:2]):
                best[f] = (n / count, n, count, path)
    if not best:
        return None
    often = sorted(best.items(), key=lambda item: (-item[1][0], -item[1][1], item[0]))[:CO_CHANGE["shown"]]
    return (f"{key}'s files often change with "
            + ", ".join(f"{f} ({owner_words(c, f)}; with {path} in {n} of {count} changes)"
                        for f, (_, n, count, path) in often))


def context_problems(c) -> list[str]:
    """Each context.sections heading the plan lacks, which every lane's review context would go without."""
    text = c.shelf.text(c.plan)
    if text is None:
        return plan_problems(c, [])
    sections = plan_sections(text)
    lacking = [name for name in c.context_sections if context_section(sections, name) is None]
    if not lacking:
        return []
    return [f"context.sections the plan lacks: {', '.join(map(repr, lacking))}; fix: add the heading to {c.plan}, or "
            f"remove it from context.sections in {c.delivery_where}"]


def publishing_problems(c, binding: dict) -> list[str]:
    """A forge or tracker the repository configures, with nothing in the binding that reads or posts to it."""
    state = binding.get("state") or {}
    problems = []
    if (c.forge.get("adapter") or c.forge.get("argv")) and not state.get("forgeSnapshot"):
        problems.append("the forge is configured, but the binding has no state.forgeSnapshot, so the record never sees "
                        "the delivery's pull requests; fix: add state.forgeSnapshot to the binding (delivery-views' "
                        "snapshot.py prints one for GitHub and Bitbucket)")
    # The one tracker check (tracker-adapter/1): a tracker whose updates nothing posts, unless tracker.updates is manual.
    tracker = tracker_publisher.refusal(tracker_publisher.settings(c.root, c.flow_raw, binding))
    if tracker:
        problems.append(tracker)
    return problems


def remote_base_problem(c) -> str | None:
    """Lanes read the configuration and the delivery file from the remote base. A local setup, whose lanes read
    their own working tree, says so with RUN_DELIVERY_LOCAL=1."""
    if setting("RUN_DELIVERY_LOCAL") == "1" or setting("RUN_DELIVERY_CONFIG"):
        return None
    fix = (f"fix: commit them on {c.base_branch} and push it, or set RUN_DELIVERY_LOCAL=1 for a local setup, whose "
           "lanes read their own working tree")
    if not OFFLINE:
        run(["git", "fetch", "-q", c.remote, c.base_branch], cwd=c.root, check=False)
    if run(["git", "rev-parse", "--verify", "--quiet", c.base_ref], cwd=c.root, check=False).returncode != 0:
        return f"{c.base_ref} has no configuration for lanes: the branch isn't on the remote; {fix}"
    shelf = Shelf(c.root, c.base_ref)
    lacking = [CONFIG] if shelf.text(CONFIG) is None and shelf.text(LEGACY_NAMES[0][0]) is None else []
    lacking += [rel for rel in [f"{c.deliveries_rel}/{c.delivery}.json"] if shelf.text(rel) is None]
    if lacking:
        return f"{c.base_ref} has no configuration for lanes: it lacks {' and '.join(lacking)}; {fix}"
    return None


def ready_problem(c) -> str | None:
    """A merge step that refuses drafts, with no grant for the lead to mark them ready at acceptance."""
    if c.forge.get("mergeNeedsReady") and not (c.raw.get("authority") or {}).get("readyPullRequests"):
        return (f"forge.mergeNeedsReady says the merge step refuses a draft, but the delivery grants no "
                f"readyPullRequests; fix: add \"readyPullRequests\": true to `authority` in {c.delivery_where} (lead-setup "
                "--force --authority push,draftPullRequests,readyPullRequests writes it), so lead-accept marks each draft "
                "ready, or remove forge.mergeNeedsReady")
    return None


def setup_problems(c) -> list[str]:
    """Every duty the workflow can't do for this delivery here, each with its fix: what doctor --delivery reports and
    lead-init refuses on."""
    problems = plan_problems(c, list(c.stories)) + context_problems(c)
    ready = ready_problem(c)
    if ready:
        problems.append(ready)
    binding = c.shelf.json(".agents/orchestration.json")
    if binding is not None:
        problems += publishing_problems(c, binding)
    node = node_problem(c.release)
    if node:
        problems.append(node[1])
    remote = remote_base_problem(c)
    if remote:
        problems.append(remote)
    return list(dict.fromkeys(problems))


# -- lead ----------------------------------------------------------------------------------

# How many times a write computed from a read is tried before it gives up: each try reads the record afresh.
PATCH_ATTEMPTS = 5


def patch_from(compute, what: str, writer: str = "lead", attempts: int = PATCH_ATTEMPTS):
    """Read the record, compute a patch from that read, and write it pinned to the version read. When another writer
    (the supervisor, the queue, a lane) moved the record in between, the state tool refuses it as version_stale. Then
    read again and compute again, so no patch is ever written from a stale read, up to `attempts` times. `compute`
    takes the record and returns the patch body, a list of the state tool's own patch arguments (an element patch),
    or None for nothing to write; it may raise Stop when the fresh record no longer allows the write. Returns what was
    written, or None."""
    for _ in range(attempts):
        doc = load_state()
        body = compute(doc)
        if body is None:
            return None
        given = body if isinstance(body, list) else ["--patch", json.dumps(body)]
        done = state_tool("patch", "--writer", writer, "--version", str(doc["state"]["version"]), *given, check=False)
        if done.returncode == 0:
            return body
        try:  # the state tool answers with one JSON document, pretty-printed
            answer = json.loads(done.stdout)
        except ValueError:
            answer = {}
        answer = answer if isinstance(answer, dict) else {}
        if answer.get("code") != "version_stale":
            raise Stop(f"the state tool refused {what}: {(answer.get('error') or done.stderr or done.stdout).strip()[-600:]}")
    raise Stop(f"version_stale: the record kept changing while {what} was written; tried {attempts} times, reading "
               "it afresh each time. Run it again once the other writer settles")


LANE_GRANTS = ("push", "draftPullRequests")
# The lead's own grant, beside the lanes': lead-accept marks a draft ready under it. No lane reads it.
LEAD_GRANTS = ("readyPullRequests",)


def lanes_grant(args, existing: dict | None) -> dict | None:
    """The operator's grant, as lead-setup is told it: the lanes' push and draft pull requests, and the lead's
    readyPullRequests. None when there is none. A file drafted again keeps the grant it holds unless told otherwise."""
    if args.authority and args.no_authority:
        raise Stop("pass --authority or --no-authority, not both")
    if args.no_authority:
        return None
    if not args.authority:
        if (existing or {}).get("authority"):
            return existing["authority"]
        raise Stop("lead-setup needs the lanes' authority grant: ask the operator what lanes may do on the forge, then "
                   "pass --authority push,draftPullRequests --granted-by WHO (either or both grants; add "
                   "readyPullRequests, the lead's, when lead-accept marks drafts ready), or --no-authority "
                   "when lanes get none and hand their branches to the lead")
    names = [name.strip() for name in args.authority.split(",") if name.strip()]
    unknown = [name for name in names if name not in (*LANE_GRANTS, *LEAD_GRANTS)]
    if unknown or not names:
        raise Stop(f"--authority takes {', '.join(LANE_GRANTS)} (the lanes') and {', '.join(LEAD_GRANTS)} (the lead's, "
                   f"to mark a draft ready at acceptance), comma-separated, not {', '.join(unknown) or 'nothing'}")
    if "draftPullRequests" in names and "push" not in names:
        raise Stop("draftPullRequests needs push: a lane opens its draft pull request from the branch it pushed")
    if not args.granted_by:
        raise Stop("--authority needs --granted-by: who granted it")
    return {**{name: True for name in (*LANE_GRANTS, *LEAD_GRANTS) if name in names}, "grantedBy": args.granted_by,
            "grantedOn": args.granted_on or datetime.date.today().isoformat()}


def lead_setup(args) -> None:
    """Draft .agents/deliveries/<key>.json from the approved plan (and, optionally, the tracker snapshot)."""
    c = Config(repo(), need_delivery=False)
    if c.bases and not args.base:
        raise Stop(f"the binding serves several bases ({', '.join([c.binding_base, *c.bases])}): pass --base to name "
                   "the one this delivery lands on")
    if args.base and c.binding_base and not serves(c.binding, args.base):
        raise unserved(c.binding, args.base)
    if tracker_conf(c)["unrunnable"]:
        # Refused before anything is drafted: a binding naming a publisher that runs nothing no longer validates.
        raise Stop(tracker_publisher.refusal(tracker_conf(c)))
    key = args.key
    path = c.deliveries / f"{key}.json"
    if path.exists() and not args.force:
        raise Stop(f"{path} exists; edit it, or pass --force to draft it again")
    existing = json.loads(path.read_text()) if path.exists() else None
    grant = lanes_grant(args, existing)
    plan = c.root / args.plan
    text = plan.read_text()
    pattern = re.compile(args.pattern or STORY_HEADING, re.M)
    if "key" not in pattern.groupindex or "title" not in pattern.groupindex:
        raise Stop("the pattern must name groups `key` and `title` (and may name `label` and `pinned`)")
    sections = plan_sections(text)
    # The plan format's headings are read under its `## Stories`; a pattern of the lead's own reads the whole plan.
    scope = next((body for level, title, body in sections if level == 2 and title == "Stories"), None) \
        if args.pattern is None else None
    stories, read = {}, {}
    notes = []
    for m in pattern.finditer(scope if scope is not None else text):
        g = m.groupdict()
        story = {"title": g["title"].strip(), "lane": "unassigned"}
        heading = m.group(0).strip()
        section = next((body for _, _, body in sections if body.startswith(heading)), "")
        found = story_plan(section)
        if found is None:
            notes.append(f"plan story {g['key']}'s section doesn't open with its lane sentence (\"Lane N, tier T, not "
                         "blocked.\" or \"Lane N, tier T, after KEY.\"): left it unassigned")
        else:
            story["lane"] = found["lane"]
            if found["after"]:
                story["after"] = found["after"]
            read[g["key"]] = found
        if g.get("label"):
            story["label"] = g["label"]
        if g.get("pinned"):
            story["pinned"] = re.findall(r"`([^`]+)`", g["pinned"])
            if c.in_app:
                # The rule for "when relevant": a story whose in-app checks hold or fail a request changes what the
                # page shows while it loads or fails, so it gets a before/after clip. The lead removes it if not.
                story["evidence"] = "video"
        stories[g["key"]] = story
    if not stories:
        raise Stop(f"the pattern matched no heading in {args.plan}")
    for subject, found in read.items():
        unknown = [after for after in found["after"] if after not in stories]
        if unknown:
            notes.append(f"plan story {subject} comes after {', '.join(unknown)}, which the plan has no story for")
    lanes, missing = units_owns(text)
    if missing:
        notes.append(missing)
    for lane in sorted({story["lane"] for story in stories.values() if story["lane"] != "unassigned"} - set(lanes)):
        notes.append(f"{lane} has stories but no row in the plan's '{UNITS_SECTION}' table: drafted it owning nothing")
        lanes[lane] = []
    snapshot, said = setup_tracker_snapshot(c, args, key)
    if said:
        notes.append(said)
    if snapshot is not None:
        tracked = {i["subject"]: i for i in snapshot if i["subject"] != key and i.get("state") != "canceled"}
        for subject, item in tracked.items():
            if subject not in stories:
                notes.append(f"tracker item {subject} ({item.get('title')}) has no plan heading; add it or cancel it")
        for subject, story in stories.items():
            if subject not in tracked:
                notes.append(f"plan story {subject} has no tracker item")
            elif tracked[subject].get("criteria"):
                story["criteria"] = [{"id": f"{subject.lower()}-{n}", "text": criterion}
                                     for n, criterion in enumerate(tracked[subject]["criteria"], start=1)]
            else:
                notes.append(f"plan story {subject}'s tracker item has no Acceptance Criteria section: drafted no "
                             "criteria for it")
    drafted = {}
    for field, name, noun in (("approvals", args.approvals_section, "approvals"),
                            ("outOfScope", args.out_of_scope_section, "out-of-scope items")):
        if not name:
            continue
        body = next((b for _, title, b in sections if title == name), None)
        items = plan_list_items(body) if body is not None else []
        if body is None:
            notes.append(f"the plan has no '{name}' section: drafted no {noun}")
        elif not items:
            notes.append(f"the plan's '{name}' section holds no list: drafted no {noun}")
        else:
            drafted[field] = ([{"id": f"approval-{i + 1}", "text": text, "state": "waiting"} for i, text in enumerate(items)]
                            if field == "approvals" else items)
            print(f"drafted {len(items)} {noun} from '{name}'")
    if existing:
        drafted.update({name: existing[name] for name in ("approvals", "outOfScope", "planReview") if name in existing})
    adoptable = adoptable_plan_review(c, Path(args.plan))
    if adoptable:
        drafted.setdefault("planReview", {"lenses": json.loads(round_files(adoptable)[0].read_text())["lenses"]})
        target = c.main / c.work / "review" / key / "plan"
        if target.exists() and any(target.iterdir()):
            raise Stop(f"delivery {key} already has a plan review in {target}, so it can't adopt the one in {adoptable}; "
                       "keep one of them and remove the other")
    git("fetch", "-q", c.remote)
    base = args.base or c.binding_base
    if not base:
        raise Stop("no delivery base: the binding names none here; pass --base")
    base_ref = f"{c.remote}/{base}"
    tooling = None
    if args.ship_from:
        first = git("rev-list", "--reverse", f"{args.ship_from}..{base_ref}", check=False).splitlines()
        tooling = first[0][:12] if first else None
        if not tooling:
            notes.append(f"no commit on {base_ref} after {args.ship_from}: set ship.toolingCommit once the tooling is committed")
    delivery = {
        "schemaVersion": "delivery-flow-delivery/1",
        "delivery": key,
        "headline": args.headline or key,
        **({"baseBranch": args.base} if args.base and args.base != c.binding_base else {}),
        "plan": args.plan,
        "laneBrief": str(Path(args.plan).with_suffix("")) + ".lane-brief.md",
        "brief": {"notes": None},
        "context": {"sections": args.context_section or []},
        "lanes": {lane: {"owns": owns} for lane, owns in lanes.items()},
        "stories": stories,
        "finish": [EVIDENCE_FINISH] if any(st.get("evidence") == "video" for st in stories.values()) else [],
        **({"authority": grant} if grant else {}),
        **drafted,
    }
    if args.ship_from:
        delivery["ship"] = {"from": args.ship_from, "toolingCommit": tooling, "paths": args.ship_path or ["src/"],
                            "branch": "ship/{delivery}"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(delivery, indent=2) + "\n")
    print(f"drafted {path}: {len(stories)} stories")
    added = add_tracker_publisher(c)
    if added:
        print(f"added the tracker publisher ({added}) to {c.binding_path.name}'s state.publishers: commit it with the "
              "delivery file")
    review_when_note(c, args.review_when, notes)
    loose = tracker_publisher.untargeted(tracker_conf(c)) if tracker_conf(c)["mode"] == "publisher" else []
    if loose:
        notes.append(f"name the status each of these moves leads to in tracker.transitions.targets, so one a person "
                     f"already made reads as made: {', '.join(loose)}")
    if adoptable:
        print(adopt_plan_review(c, adoptable, key))
    print("next: check each story's lane, criteria and order and the lanes' owns against the approved plan, fill in "
          "what the notes below name, set inApp requirements, the finish lines and the brief notes, then lead-init and "
          "lead-brief")
    for n in notes:
        print(f"  note: {n}")


def lead_hints(args) -> None:
    """Print the delivery's work-hints/1 for orchestrate-work's `next`: each story's files, from its plan section's
    `Files:` sentence, and its blockers, the `after` keys its delivery-file story holds. A binding names this command
    as its state.workHints, so `next` reads the plan's order without the tracker."""
    c = cfg()
    sections = plan_sections(c.shelf.text(c.plan) or "")
    items = {}
    for key, story in c.stories.items():
        section = story_section(sections, key) or ""
        hint = {}
        files = section_files(section)
        if files:
            hint["files"] = files
        if story.get("after"):
            hint["blockers"] = list(story["after"])
        items[key] = hint
    print(json.dumps({"items": items}, indent=2))


def setup_tracker_snapshot(c, args, key: str) -> tuple[list[dict] | None, str | None]:
    """The tracker snapshot lead-setup drafts criteria from, as its items: the --tracker-snapshot file, else the
    binding's trackerSnapshot command run for the delivery's own key. None, with why, when there is neither or the
    command fails."""
    if args.tracker_snapshot:
        return json.loads(Path(args.tracker_snapshot).read_text()).get("items", []), None
    spec = (c.binding.get("state") or {}).get("trackerSnapshot")
    if not spec:
        return None, ("no tracker snapshot (pass --tracker-snapshot, or declare the binding's state.trackerSnapshot): "
                      "drafted no story's criteria")
    argv = expand_argv(spec["argv"], stateDirectory=str(c.main / c.state_rel), parent=key)
    try:
        taken = run(argv, cwd=c.root, check=False, timeout=int(spec.get("timeoutSeconds", 60)))
    except subprocess.TimeoutExpired:
        return None, "the binding's tracker snapshot timed out: drafted no story's criteria"
    if taken.returncode != 0:
        return None, (f"the binding's tracker snapshot failed ({taken.returncode}: "
                      f"{(taken.stderr or taken.stdout).strip()[-200:]}): drafted no story's criteria")
    try:
        items = json.loads(taken.stdout).get("items", [])
    except (ValueError, AttributeError):
        return None, "the binding's tracker snapshot printed no snapshot: drafted no story's criteria"
    return items, None


def review_when_note(c, answer: str | None, notes: list[str]) -> None:
    """Record lead-setup's answer for when the review move is made, or ask for it: never inferred from review.pr."""
    tracker = c.flow_raw.get("tracker") or {}
    if answer:
        raw = json.loads(Path(c.path).read_text()) if Path(c.path).exists() else {"schemaVersion": "delivery-flow/1"}
        raw.setdefault("tracker", {}).setdefault("transitions", {})["reviewWhen"] = answer
        Path(c.path).write_text(json.dumps(raw, indent=2) + "\n")
        c.flow_raw = raw
        print(f"recorded tracker.transitions.reviewWhen: {answer} in {c.name}: commit it with the delivery file")
    elif (tracker.get("transitions") or {}).get("review") and not tracker["transitions"].get("reviewWhen"):
        notes.append("tracker.transitions.reviewWhen is unset, so lanes make the review move by hand when open-pr prints "
                     "it; to have the commands make it, run lead-setup again with --force and --review-when "
                     f"{'|'.join(tracker_publisher.REVIEW_WHEN)}")


def add_tracker_publisher(c) -> str | None:
    """With a tracker adapter configured and no tracker publisher bound, add one to the binding's state.publishers, so
    every render posts the delivery's tracker updates. Returns the publisher's id, or None when nothing was added."""
    conf = tracker_conf(c)
    if conf["mode"] != "publisher" or conf["publisher"] or conf["problems"] or not c.binding_path.exists():
        return None
    binding = json.loads(c.binding_path.read_text())
    publishers = binding.setdefault("state", {}).setdefault("publishers", [])
    entry = tracker_publisher.publisher_entry(TOOL_PATH.replace("run_delivery.py", "tracker_publisher.py"))
    taken = {item.get("id") for item in publishers if isinstance(item, dict)}
    entry["id"] = next(name for name in ("tracker", *(f"tracker-{n}" for n in range(2, len(taken) + 3)))
                       if name not in taken)
    publishers.append(entry)
    c.binding_path.write_text(json.dumps(binding, indent=2) + "\n")
    return entry["id"]


# The plan format lead-setup reads (plan-work's SKILL.md documents it, with an example): a story is a `### KEY. Title`
# heading under `## Stories`, whose section opens with its lane sentence and may hold a `Files:` sentence, and the
# `## Units and order` table gives each lane's files.
PLAN_KEY = r"[A-Z][A-Z0-9]*-[0-9]+"
STORY_HEADING = rf"^### (?P<key>{PLAN_KEY})\. (?P<title>.+)$"
LANE_SENTENCE = re.compile(rf"Lane ([0-9]+), tier ([0-9]+), (?:not blocked|after ({PLAN_KEY}(?:, {PLAN_KEY})*"
                           rf"(?: and {PLAN_KEY})?))\.(?=\s|$)")
UNITS_SECTION = "Units and order"
UNITS_COLUMNS = ("Tier", "Lane", "Items, in order", "Owns")


def plan_paths(text: str) -> list[str]:
    """The backticked repository paths in a plan's `Files:` sentence or `Owns` cell, in order, each once; a folder
    (ending in /) becomes the glob of everything under it."""
    paths = []
    for token in re.findall(r"`([^`\n]+)`", text):
        token = token.strip()
        path = token + "*" if token.endswith("/") else token
        if path not in paths:
            paths.append(path)
    return paths


def plan_sentence(text: str, opening: str) -> str | None:
    """The sentence of `text` (one line, whitespace folded) that starts with `opening`, without it and its full stop;
    a full stop inside backticks, as in a file name, ends no sentence."""
    found = re.search(rf"(?:^|(?<=[.:] )){re.escape(opening)}", text)
    if found is None:
        return None
    rest = text[found.end():]
    masked = re.sub(r"`[^`]*`", lambda m: "x" * len(m.group()), rest)
    end = re.search(r"\.(?=\s|$)", masked)
    return rest[:end.start()] if end else rest


def section_body(section: str) -> str:
    """A plan section's text after its heading, as one line with its whitespace folded."""
    return " ".join(section.split("\n", 1)[1].split()) if "\n" in section else ""


def section_files(section: str) -> list[str]:
    """The paths a story's plan section lists in its `Files:` sentence; none without one."""
    files = plan_sentence(section_body(section), "Files:")
    return plan_paths(files) if files is not None else []


def story_plan(section: str) -> dict | None:
    """A story's lane, tier and `after` keys from its plan section (heading included); None when the section doesn't
    open with its lane sentence."""
    found = LANE_SENTENCE.match(section_body(section))
    if found is None:
        return None
    lane, tier, keys = found.groups()
    return {"lane": f"lane-{int(lane)}", "tier": int(tier), "after": re.findall(PLAN_KEY, keys or "")}


def units_rows(text: str) -> tuple[list[tuple[int, str, list[str]]] | None, str | None]:
    """The plan's `Units and order` table as (tier, lane, owns) rows, or None with what's missing."""
    body = next((b for level, title, b in plan_sections(text) if level == 2 and title == UNITS_SECTION), None)
    if body is None:
        return None, f"the plan has no '{UNITS_SECTION}' section"
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in body.splitlines()
            if line.strip().startswith("|")]
    if not rows or any(name not in rows[0] for name in UNITS_COLUMNS):
        return None, f"the plan's '{UNITS_SECTION}' section holds no table with the columns {', '.join(UNITS_COLUMNS)}"
    at = {name: rows[0].index(name) for name in UNITS_COLUMNS}
    found = []
    for row in rows[1:]:
        if len(row) < len(rows[0]) or set("".join(row)) <= set("-: "):
            continue
        tier, lane = re.fullmatch(r"[0-9]+", row[at["Tier"]]), re.fullmatch(r"(?:Lane )?([0-9]+)", row[at["Lane"]])
        if tier is not None and lane is not None:
            found.append((int(tier.group()), f"lane-{int(lane.group(1))}", plan_paths(row[at["Owns"]])))
    return found, None


def units_owns(text: str) -> tuple[dict[str, list[str]], str | None]:
    """Each lane's `owns` from the plan's `Units and order` table, read from its lowest-tier row; a later row lists
    only what the lane gains when an earlier item merges, which the lead applies then. With what's missing, if any."""
    rows, missing = units_rows(text)
    if rows is None:
        return {}, f"{missing}: drafted no lane's owns"
    lowest: dict[str, tuple[int, list[str]]] = {}
    for tier, lane, owns in rows:
        if lane not in lowest or tier < lowest[lane][0]:
            lowest[lane] = (tier, owns)
    return {name: owns for name, (_, owns) in sorted(lowest.items())}, None


def plan_with_story(text: str, key: str, title: str, lane: str, after: list[str], why: str) -> str:
    """The plan with a story added mid-delivery: its `### KEY. Title` heading at the end of `## Stories` (or of the
    plan, without one), its lane sentence in plan-work's format at the lane's latest tier, and its reason."""
    rows, _ = units_rows(text)
    tier = max((row_tier for row_tier, row_lane, _ in rows or [] if row_lane == lane), default=1)
    number = re.fullmatch(r"lane-([0-9]+)", lane)
    order = ("not blocked" if not after else "after " + (after[0] if len(after) == 1 else
                                                         ", ".join(after[:-1]) + " and " + after[-1]))
    opening = f"Lane {number.group(1)}, tier {tier}, {order}. " if number else ""
    section = f"### {key}. {title}\n\n{opening}Added mid-delivery: {why}\n"
    body = next((body for level, heading, body in plan_sections(text) if level == 2 and heading == "Stories"), None)
    if body is None:
        return text.rstrip("\n") + "\n\n" + section
    end = text.index(body) + len(body)
    rest = text[end:].lstrip("\n")
    return text[:end] + "\n\n" + section + ("\n" + rest if rest else "")


def plan_list_items(body: str) -> list[str]:
    """Each top-level list item of a plan section, before its first subheading, as one line of at most 600
    characters; an item's indented continuation lines join it."""
    items: list[str] = []
    for line in body.splitlines()[1:]:
        if line.startswith("#"):
            break
        item = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if item:
            items.append(item.group(1).strip())
        elif items and line[:1] in (" ", "\t") and line.strip():
            items[-1] += " " + line.strip()
    items = [re.sub(r"\s+", " ", text) for text in items if text][:100]
    return [text if len(text) <= 600 else text[:599] + "\u2026" for text in items]


def lead_carry_review(args) -> None:
    """Carry a story's captured review across commits the lead judged need no new round, showing them first.

    The state tool's carry-review does the carry and every check on it; this shows from..to, the diff the judgement
    rests on, before that write, and passes the state tool's refusal through unchanged."""
    c = cfg()
    key = c.require(args.key)
    heads = []
    for given in (args.from_head, args.to_head):
        found = git("rev-parse", "--verify", "--quiet", f"{given}^{{commit}}", check=False)
        if not found:
            git("fetch", "-q", c.remote, c.branch(key), check=False)
            found = git("rev-parse", "--verify", "--quiet", f"{given}^{{commit}}", check=False)
        if not found:
            raise Stop(f"no commit {given} here or on {c.remote}/{c.branch(key)}: name the heads the record holds")
        heads.append(found)
    from_head, to_head = heads
    changed = git("diff", "--name-status", "--no-renames", f"{from_head}..{to_head}").splitlines()
    summary = git("diff", "--shortstat", f"{from_head}..{to_head}") or "no changes"
    print(f"{key}'s review carries from {from_head[:10]} to {to_head[:10]} across: {summary.strip()}")
    for line in changed:
        status, _, path = line.partition("\t")
        print(f"  {status:<2} {path}")
    sys.stdout.flush()  # shown before the write, and before a refusal on stderr when both go to one pipe
    done = state_tool("carry-review", "--writer", "lead", "--subject", key, "--from", from_head, "--to", to_head,
                      "--reason", args.reason, check=False)
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        answer = {}
    answer = answer if isinstance(answer, dict) else {}
    if done.returncode != 0:
        said = f"{answer['code']}: {answer.get('error', '')}" if answer.get("code") else (done.stderr or done.stdout).strip()
        raise Stop(f"the state tool refused the carry: {said}")
    print(f"carried: {answer.get('entry', {}).get('summary', '')}")


def lead_init(args) -> None:
    c = cfg()
    c.check_lanes()
    if c.ship and not c.ship.get("toolingCommit"):
        raise Stop(f"{c.delivery_where}: ship.toolingCommit isn't set")
    adoptable = adoptable_plan_review(c, c.plan)
    if adoptable:
        print(adopt_plan_review(c, adoptable, c.delivery))
    if plan_declared(c):
        rounds = plan_review_rounds(c)
        if rounds and plan_review_state(c, rounds) == "open" and (args.replace or not any(
                entry["type"] == "snapshot" for entry in read_plan_log(c))):
            raise Stop(f"the plan review is still open after round {rounds[-1]['round']}: align it with another round, "
                       f"or have the operator end it ({flow_command()} plan-review end --reason TEXT), before freezing "
                       "the plan")
        # Refused before the record starts, so a criterion the view can't read is corrected before anything is kept.
        check_plan(build_plan(c, [snapshot_entry(c, late=False)]), c)
    problems = setup_problems(c)
    stale = None if args.replace else stale_record(c)
    if stale:
        problems.insert(0, stale)
    if problems:
        raise Stop(f"lead-init refuses until each of these is fixed (doctor --delivery {c.delivery} reports them too):"
                   + "".join(f"\n  - {problem}" for problem in problems))
    for line in replace_record(c) if args.replace else []:
        print(line)
    state_tool("init", "--headline", c.raw.get("headline", c.delivery))
    planned = [planned_entry(c, key) for key in c.stories]
    # The record's finish items take no other keys: covers, label and at are the plan's.
    finish = [{"id": f["id"], "text": f["text"], "met": False} for f in c.finish]
    patch_from(lambda doc: {"meta": {"trackerParent": c.delivery, "handoffsNeedAcceptance": True}, "planned": planned,
                            "finish": finish}, "the delivery's planned work and finish lines")
    lease = (f" The {c.in_app.get('lease', 'in-app')} lease is granted on demand through run-delivery app-check's OS lock,"
             " held only for a lane's own in-app checks.") if c.in_app else ""
    shipping = (f" The tooling never ships: the product branch is built from {', '.join(c.ship['paths'])} of the "
                "delivery base.") if c.ship else ""
    state_tool("journal", "--kind", "decision.recorded", "--summary",
               f"Record starts at base {git('rev-parse', '--short', c.base_ref)} on {c.base_ref}. Lanes merge "
               f"through the runner; hand-offs wait for the lead's acceptance.{shipping}{lease}")
    if plan_declared(c):
        with plan_lock(c):
            if not any(entry["type"] == "snapshot" for entry in read_plan_log(c)):
                write_snapshot(c, late=False)
            rebuild_plan(c)
    print(f"initialized {c.state} with {len(planned)} planned stories across {len(c.lanes)} lanes"
          + (f"; wrote {c.state / PLAN_FILE}" if (c.state / PLAN_FILE).exists() else ""))
    if args.no_supervisor or supervisor_off():
        print(f"supervisor: not started; start it with {flow_command()} lead-run --resume")
    else:
        print(f"supervisor: running; live view {start_supervisor(c)}")


def planned_entry(c, key: str) -> dict:
    s = c.stories[key]
    return {"subject": key, "title": f"{c.label(key)}: {s['title']}" if s.get("label") else s["title"],
            "lane": s["lane"], "stage": "planned"}


class Unreasoned(Stop):
    """Registration refused: a story added after the plan froze, with no `why` for its `added` change."""


def unregistered(c, state: dict) -> list[str]:
    """The delivery file's stories the record doesn't hold yet. A record holding no work at all is lead-init's to
    fill, so it has none."""
    if not state.get("planned") and not state.get("queue"):
        return []
    held = {item["subject"] for item in [*state["queue"], *state.get("planned", [])]}
    return [key for key in c.stories if key not in held]


def records_changes(c) -> bool:
    """Registration records an `added` change once the plan chapter is on or its snapshot is kept."""
    return plan_declared(c) or any(entry["type"] == "snapshot" for entry in read_plan_log(c))


def story_why_problem(c, key: str) -> str | None:
    """What is wrong with a story's `why`, as its `added` change would carry it; None when it can carry it."""
    return why_problem(c, c.stories[key].get("why"))


def why_problem(c, why: object) -> str | None:
    """What is wrong with a `why` as an `added` change would carry it; None when the plan can carry it."""
    if why is None:
        return "has no `why`"
    views = plan_validator(c)
    try:
        views._text(why, "why", views.PLAN_TEXT)
    except views.ConfigError as error:
        return f"has a `why` the plan can't carry ({str(error).removeprefix('why: ')})"
    return None


def unreasoned_problems(c, keys: list[str]) -> list[str]:
    if not keys or not records_changes(c):
        return []
    return [f"{key} {problem}" for key in keys if (problem := story_why_problem(c, key))]


def unreasoned_refusal(c, problems: list[str]) -> Unreasoned:
    return Unreasoned(
        f"{'; '.join(problems)}: a story added to {c.delivery_where} after the plan froze at lead-init is registered "
        "only with its reason; fix: give it a `why` there, one line saying why it joins the delivery, which its "
        "`added` change carries, commit and push the delivery file, then run this again. Nothing was registered")


def check_registrable(c) -> None:
    """Refuse, before anything is written, while registration would refuse a story added without its reason."""
    if not (c.state / "state.json").exists():
        return
    problems = unreasoned_problems(c, unregistered(c, load_state()["state"]))
    if problems:
        raise unreasoned_refusal(c, problems)


def register_stories(c) -> list[str]:
    """Stories added to the delivery after lead-init join the record as planned work, so the live view shows them
    before they're assigned. One the tracker reconcile already filed as a follow-up moves out of the follow-ups.
    Each one's `added` change carries the `why` its story gives in the delivery file; a story with none is refused,
    with nothing registered. Returns the keys added; a delivery not yet initialized is left to lead-init."""
    if not (c.state / "state.json").exists():
        return []
    # Under the plan's lock: lead-plan's whole-story add writes the delivery file before it plans the story, and a
    # sweep registering it in between would record its `added` change a second time.
    with plan_lock(c):
        return _register_stories(c)


def _register_stories(c) -> list[str]:
    added: list[str] = []

    def compute(doc: dict) -> dict | None:
        state = doc["state"]
        added.clear()
        added.extend(unregistered(c, state))
        if not added:
            return None
        problems = unreasoned_problems(c, added)
        if problems:
            raise unreasoned_refusal(c, problems)
        followups = [f for f in state["followups"] if f["id"] not in added and f.get("reference") not in added]
        return {"planned": [*state.get("planned", []), *(planned_entry(c, key) for key in added)], "followups": followups}
    if patch_from(compute, "the stories added to the delivery") is None:
        return []
    for key in added:
        state_tool("journal", "--kind", "decision.recorded", "--subject", key, "--summary",
                   f"{key} added to the delivery as planned work for {c.lane_of(key)}")
    if records_changes(c):
        for key in added:
            change = {"kind": "added", "subjects": [key], "by": "lead", "title": c.stories[key]["title"],
                      "source": "registration"}
            if "why" in c.stories[key]:
                change["why"] = c.stories[key]["why"]
            record_plan_change(c, change)
    return added


def lead_brief(args) -> None:
    c = cfg()
    c.check_lanes()
    for key in register_stories(c):
        print(f"registered {key} in the record as planned work")
    moved = rebuild_plan(c)
    if moved:
        print(f"rebuilt {c.state / PLAN_FILE}: {moved}")
    template = (TOOL / "templates/lane-brief.md").read_text()
    in_app = (TOOL / "templates/lane-brief.in-app.md").read_text() if c.in_app else ""
    notes = (c.shelf.text(c.brief_notes) or "").strip() if c.brief_notes else ""
    lane_rows = "\n".join(f"| {lane} | {', '.join(k for k, s in c.stories.items() if s['lane'] == lane)} | "
                          f"{', '.join(f'`{g}`' for g in entry['owns'])} |" for lane, entry in c.lanes.items())
    story_rows = "\n".join(
        f"| {k} | {c.label(k)} | {', '.join(f'`{p}`' for p in s.get('pinned', [])) or '-'} | "
        f"{'; '.join([requirement_label(r) for r in c.requirements(k)] + (['video'] if s.get('evidence') == 'video' else [])) or 'none'} |"
        for k, s in c.stories.items())
    values = {
        "delivery": c.delivery, "headline": c.raw.get("headline", c.delivery), "plan": str(c.plan),
        "flow": flow_command(), "config": c.config_named, "base_branch": c.base_branch, "base_ref": c.base_ref, "remote": c.remote,
        "branch": c.branch_pattern.replace("{key}", "<key>"),
        "lane_checkout": c.lane_pattern.replace("{key}", "<key>"),
        "state": str(c.state), "bound": str(c.bound), "work": c.work,
        "lenses": ", ".join(f"`{lens}`" for lens in c.lenses),
        "gates": "\n".join(f"   - `{' '.join(expand_argv(g['argv'], base=c.base_ref, key='<key>', delivery=c.delivery))}`"
                           for g in c.gates) or "   - (none declared)",
        "never_touch": ", ".join(f"`{p}`" for p in c.never_touch) or "(nothing listed)",
        "lanes": lane_rows, "stories": story_rows, "in_app": in_app, "notes": notes or "(none)",
        "lease": (c.in_app or {}).get("lease", "in-app"), "port": str((c.in_app or {}).get("port", "")),
        "tracker": c.issue_url.replace("{key}", "<key>"), "authority": authority_text(),
        "transitions": transitions_text(),
        "pr_review": pr_review_text(),
    }
    fill = lambda t: re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), t)
    values["in_app"] = fill(values["in_app"])  # the in-app part has its own placeholders; notes are left as written
    out = c.root / c.lane_brief
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(fill(template))
    print(f"wrote {out}")


# Where the tracker snapshot is kept: serve's refresh, under the record, and lead-reconcile's, beside it.
TRACKER_SNAPSHOTS = ("snapshots/tracker.json", "../snapshots/trackerSnapshot.json")


def tracker_snapshot_items(c) -> dict | None:
    """The newest tracker snapshot kept for the delivery, as its items by subject; None when none is kept."""
    newest = None
    for rel in TRACKER_SNAPSHOTS:
        try:
            snapshot = json.loads((c.state / rel).read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(snapshot, dict) or snapshot.get("source") != "tracker" or not isinstance(snapshot.get("items"), list):
            continue
        if newest is None or str(snapshot.get("takenAt", "")) > str(newest.get("takenAt", "")):
            newest = snapshot
    if newest is None:
        return None
    return {item["subject"]: item for item in newest["items"] if isinstance(item, dict) and "subject" in item}


def story_readiness(c, key: str) -> tuple[str, str | None]:
    """Whether a story's tracker item offers the first start move, from the tracker snapshot: ("ready", None),
    ("not-ready", why), or ("not-checked", why) when the snapshot can't say. Nothing to check with no start move, or
    for an item already under way. It reads; it never moves anything."""
    moves = tracker_conf(c)["transitions"].get("start") or []
    if not moves:
        return "ready", None
    items = tracker_snapshot_items(c)
    if items is None:
        return "not-checked", f"{key}: readiness wasn't checked: there is no tracker snapshot"
    item = items.get(key)
    if item is None:
        return "not-checked", f"{key}: readiness wasn't checked: the tracker snapshot doesn't list it"
    if item.get("state") != "backlog":
        return "ready", None
    offered = item.get("offeredTransitions")
    if not isinstance(offered, list):
        return "not-checked", f"{key}: readiness wasn't checked: the tracker snapshot doesn't list its offered moves"
    if moves[0] in offered:
        return "ready", None
    return "not-ready", (f"{key} is in {item.get('status') or 'a status the snapshot does not name'}: "
                         f"\"{moves[0]}\" isn't offered; refine it first")


def lead_assign(args) -> None:
    c = cfg()
    keys = [c.require(k) for k in args.keys]
    for key in keys:
        if c.lane_of(key) != args.lane:
            raise Stop(f"{key} is planned for {c.lane_of(key)}, not {args.lane}")
    problems = plan_problems(c, keys)
    if problems:
        raise Stop("; ".join(problems))
    # Before anything is written: a story its tracker isn't ready to start is refused unless the lead forces it.
    readiness = {key: story_readiness(c, key) for key in keys}
    unready = [line for state, line in readiness.values() if state == "not-ready"]
    if unready and not args.force:
        raise Stop("; ".join(unready) + "; nothing was assigned. Have the item made ready, or assign it anyway with --force")
    register_stories(c)
    rebuild_plan(c)
    tracked = tracked_at_base(c)
    warnings = [*(named_outside(c, key, outside) for key in keys if (outside := outside_lane(c, key, tracked))),
                *(line for key in keys if (line := co_change_warning(c, key, tracked)))]
    if warnings:
        print(f"Files outside {args.lane}'s owns, before it starts (lead-accept refuses a change to any of them):")
        for line in warnings:
            print(f"  {line}")
        print(f"Move each to {args.lane}'s owns now, or plan the story on the lane that owns it.\n")
    row = {"id": args.lane, "title": args.title, "focus": f"assigned {', '.join(keys)}", "state": "assigned",
           "waitingOn": None, "since": now()}
    patch_from(lambda doc: {"lanes": [*(entry for entry in doc["state"]["lanes"] if entry["id"] != args.lane), row]},
               f"{args.lane}'s assignment")
    # One entry per story, so each ticket's start is its own, made when its lane begins it.
    for key in keys:
        state_tool("journal", "--kind", "lane.assigned", "--lane", args.lane, "--subject", key, "--summary",
                   f"{args.lane} assigned {key}: {c.stories[key]['title']}" + (f" ({args.note})" if args.note else ""))
    said = [line + ("; assigned anyway (--force)" if state == "not-ready" else "")
            for state, line in readiness.values() if line]
    if said:
        print("Readiness, from the tracker snapshot:")
        for line in said:
            print(f"  {line}")
        print()
    print(f"Assignment message for {args.lane} ({args.title}):\n")
    print(f"You are {args.lane} in the {c.delivery} delivery. Read the lane brief first: {c.root / c.lane_brief}\n")
    print("Your stories, in order (one branch, one review and one hand-off each):")
    for key in keys:
        print(f"- {key} {c.issue_url.format(key=key)} ({c.label(key)}: {c.stories[key]['title']})")
    print("\nBegin each story, when you start it, with:")
    for key in keys:
        print(f"  {flow_command()} lane-start {key} --base {c.base_branch}")
    if args.note:
        print(f"\nNote from the lead: {args.note}")
    print(f"\nRecord: {c.state}")
    print("\nWhen you hand off a story, or are blocked, end your turn with a short report that opens with the story "
          "key and the outcome (handed off, or blocked and on what): the lead sees its first lines. Then the branch, "
          "PR, reviewed head, review rounds and verdicts, gates, in-app checks, and any decision you need from the "
          "lead. The lead checks the hand-off and tells you when to start your next story.")


def accept_no_change(c, key: str, entry: dict) -> None:
    """Accept a no-change hand-off: its evidence ran at a head the base still holds, and names every one of the story's
    criteria. The record then holds the story settled: never queued, never removed, counted as done."""
    if entry["stage"] == "settled":
        raise Stop(f"{key} is already settled with no change")
    criteria = [item["id"] for item in c.stories[key].get("criteria", [])]
    if not criteria:
        raise Stop(f"{key} has no criteria, so its evidence can't be checked against them; fix: record its criteria "
                   f"first (lead-plan change --kind added --subjects {key} --criterion ID --text TEXT --why W), then "
                   "accept it")
    base_head = entry["noChange"]["baseHead"]
    git("fetch", "-q", c.remote, c.base_branch)
    problems = []
    if not is_ancestor(base_head, c.base_ref):
        problems.append(f"the evidence ran at {base_head[:10]}, which {c.base_ref} doesn't hold")
    evidence = entry["noChange"]["evidence"]
    missing = [ident for ident in criteria if not re.search(rf"(?<![\w.-]){re.escape(ident)}(?![\w-]|\.[\w-])", evidence)]
    if missing:
        problems.append(f"the evidence names no proof of criterion {', '.join(missing)}; the lane hands it off again "
                        "with evidence naming every criterion by id")
    if problems:
        print(f"NOT accepted: {key}")
        for problem in problems:
            print(f"  - {problem}")
        sys.exit(1)
    state_tool("handoff", "--accept", key)
    append_log("acceptance", key, {"head": base_head, "noChange": True, "ready": False, "blocked": {}})
    state_tool("journal", "--kind", "decision.recorded", "--subject", key, "--summary",
               f"No-change hand-off checked as a claim: its evidence ran at {base_head[:10]}, which {c.base_branch} "
               f"holds, and names each of its criteria ({', '.join(criteria)}). Settled with no change needed; no base "
               "commit is made.")
    rebuild_plan(c)
    print(f"accepted {key}: settled with no change needed")


def pr_review_acceptance(conf: dict, url: str, head: str, recorded: dict) -> tuple[list[str], list[str]]:
    """The list's PR review re-checked at acceptance, one read of the forge at the handed head: every reviewer the lane
    recorded current is still current, triaged and unchanged; a required one was never recorded absent; and an
    optional one recorded absent hasn't posted since. An old-shape record is reviewer `review`. Returns (problems,
    decisions)."""
    read = read_pr_reviews(url, head, conf, None)
    if read["status"] == "failed":
        return [f"the PR's pipeline failed at {head[:10]}: {check_states(read['checks'])}"], []
    if recorded.get("head") != head:
        return [f"the lane recorded no PR review at the handed head {head[:10]}"], []
    entries = record_reviewers(recorded)
    problems, decisions = [], []
    for reviewer in conf["reviewers"]:
        rid = reviewer["id"]
        entry, live = entries.get(rid), read["reviewers"][rid]
        if entry is None:
            problems.append(f"the lane's PR review record at {head[:10]} has no entry for {rid}")
        elif entry.get("state") != "current":
            if reviewer["required"]:
                problems.append(f"{rid} is required, but the lane recorded it {entry.get('state')}")
            elif live["state"] == "current":
                problems.append(f"{rid} was recorded absent, but its review is now current at {head[:10]} "
                                f"({live['review']['at']}): send the lane back to read and triage it")
        elif live["state"] != "current":
            problems.append(f"{rid}'s PR review isn't current at {head[:10]} ({live['state']})")
        elif not entry.get("triage"):
            problems.append(f"the lane recorded no triage of {rid}'s PR review at the handed head")
        elif (live["review"]["at"], live["review"]["body"]) != (entry.get("at"), entry.get("body")):
            problems.append(f"{rid}'s PR review changed after the lane read it ({entry.get('at')} -> "
                            f"{live['review']['at']}); the lane must read and triage it again")
        else:
            decisions.append(f"PR review {rid} by {live['review']['author']} at {live['review']['at'][:16]} triaged: "
                             f"{entry['triage']}")
    problems += pr_review_gates.live_refusals(conf["reviewers"], read["reviewers"], head)
    return problems, decisions


def lead_accept(args) -> None:
    c = cfg()
    if args.requeue:
        requeue(c, c.require(args.requeue))
        return
    if not args.key:
        raise Stop("name the story to accept, or --requeue a stopped one")
    key = c.require(args.key)
    review_conf = pr_review_config()  # refused here, before anything else, when review.pr is malformed
    if args.checks is not None and args.full_suite:
        raise Stop("choose one: --checks, the candidate's checks, or --full-suite, the binding's fallback")
    if (args.checks is not None or args.full_suite) and not lead_selects(c):
        raise Stop("the binding doesn't enable lead selection (selection.lead), so the queue selects the checks: "
                   "accept with neither --checks nor --full-suite")
    if args.full_suite and provider_selects(c):
        raise Stop("the binding names a provider, so a candidate with no lead selection runs the provider's answer, "
                   "and the fallback only when the provider can't give one: --full-suite can't promise the fallback; "
                   "choose the checks with --checks")
    chosen = check_ids(c, args.checks, "--checks") if args.checks is not None else None
    doc = load_state()
    item = next((q for q in doc["state"]["queue"] if q["subject"] == key), None)
    waiting = next((p for p in doc["state"].get("planned", []) if p["subject"] == key and p.get("noChange")), None)
    if not item and waiting:
        accept_no_change(c, key, waiting)
        return
    if not item:
        raise Stop(f"{key} is not in the queue; the lane has not handed it off")
    problems, decisions = [], []
    # Work merged outside the queue skipped the lead's acceptance; its decisions can still be recorded, once.
    merged = item["stage"] == "merged"
    if merged and item.get("reportedRounds") is None:
        raise Stop(f"{key} merged with no hand-off, so there are no claims to check: journal your decision on it")
    if merged:
        if any(entry["head"] == item["head"] for entry in read_log("acceptance", key)):
            problems.append("the hand-off was already accepted")
    elif not item.get("pending"):
        problems.append("the hand-off is not waiting for acceptance")
    head = item["head"]
    branch = c.branch(key)
    if not merged and remote_head(branch) != head:
        problems.append(f"{c.remote}/{branch} is not at the handed head {head[:10]}")
    rounds = [e for e in doc["journal"] if e.get("kind") == "review.round" and e.get("subject") == key]
    numbers = [e["review"]["round"] for e in rounds]
    if len(numbers) != len(set(numbers)):
        problems.append(f"round numbers journaled more than once: {sorted(numbers)}")
    if item.get("reportedRounds") != len(set(numbers)):
        problems.append(f"the hand-off reports {item.get('reportedRounds')} rounds; the journal has {len(set(numbers))}")
    last = max(rounds, key=lambda e: e["review"]["round"], default=None)
    if not last or set(last["review"]["verdicts"]) != set(c.lenses) or set(last["review"]["verdicts"].values()) != {"approve"}:
        problems.append("the last journaled round is not approved by every lens")
    evidence = [e for e in doc["journal"] if e.get("kind") == "review.evidence" and e.get("subject") == key]
    if not evidence or evidence[-1]["reviewArtifact"]["candidateRef"] != head:
        problems.append("the latest captured review evidence is not for the handed head")
    pr = forge("get", "--url", item.get("changeRequest") or "")
    if pr.get("state") != ("merged" if merged else "open"):
        problems.append(f"the PR is {pr.get('state')}")
    if not same_commit(pr.get("head"), head):
        problems.append(f"the PR is at {str(pr.get('head'))[:10]}, not the handed head")
    if pr.get("destination") != c.base_branch or pr.get("source") != branch:
        problems.append(f"the PR is {pr.get('source')} -> {pr.get('destination')}, not {branch} -> {c.base_branch}")
    conf = review_conf
    if conf and not conf["legacy"]:
        lane_root_ = c.lane_checkout(key)
        saved = lane_root_ / c.work / "review" / key / "pr-review.json"
        recorded = json.loads(saved.read_text()) if saved.exists() else {}
        found, made = pr_review_acceptance(conf, item.get("changeRequest") or "", head, recorded)
        problems += found
        decisions += made
    elif conf:
        status, review, _ = pr_review_state(item.get("changeRequest") or "", head, conf)
        lane_root_ = c.lane_checkout(key)
        saved = lane_root_ / c.work / "review" / key / "pr-review.json"
        recorded = json.loads(saved.read_text()) if saved.exists() else {}
        if status != "current":
            problems.append(f"the PR's pipeline review isn't current at {head[:10]} ({status})")
        elif recorded.get("head") != head or not recorded.get("triage"):
            problems.append("the lane recorded no triage of the PR review at the handed head")
        elif review["at"] != recorded.get("at"):
            problems.append(f"the PR review changed after the lane read it ({recorded.get('at')} -> {review['at']}); "
                            "the lane must read and triage it again")
        else:
            decisions.append(f"PR review by {review['author']} at {review['at'][:16]} triaged: {recorded['triage']}")
    git("fetch", "-q", c.remote, c.base_branch)
    base = git("merge-base", c.base_ref, head)
    # Once merged, the base holds the head or a squash of it, so a diff against it no longer says what the work changed.
    changed = [] if merged else git("diff", "--name-only", f"{base}..{head}").splitlines()
    owns = c.lanes[c.lane_of(key)]["owns"]
    outside = [f for f in changed if not any(fnmatch.fnmatch(f, g) for g in owns)]
    if outside:
        problems.append(f"the branch changes files outside {c.lane_of(key)}'s: {outside}")
    proposal = None
    if lead_selects(c) and not merged and chosen is None and not args.full_suite:
        # Under lead selection, a candidate accepted with no choice would check on the fallback: the lead says which.
        proposal = proposed_checks(c, changed, item.get("namedChecks") or [])
        if provider_selects(c):
            problems.append(f"choose {key}'s checks: the binding enables lead selection, and a candidate accepted with "
                            "none checks on the provider's selection, widened to the fallback "
                            f"({', '.join(fallback_ids(c))}) only when it fails; accept with --checks")
        else:
            problems.append(f"choose {key}'s checks: the binding enables lead selection, and a candidate accepted with "
                            f"none checks on the fallback ({', '.join(fallback_ids(c))}); accept with --checks, or "
                            "with --full-suite to run the fallback")
    lane_root = c.lane_checkout(key)
    accepted_blocked = set(args.accept_blocked or [])
    if lane_root.exists():
        _, gate_problems = gates_summary(key, lane_root, head)
        problems += gate_problems
        review = lane_root / c.work / "review" / key
        saved = json.loads((review / "blocked.json").read_text()) if (review / "blocked.json").exists() else {}
        blocked = saved.get("blocked", {}) if saved.get("head") == head else {}
        for label, reason in blocked.items():
            if label in accepted_blocked:
                decisions.append(f"accepted blocked '{label}' ({reason})")
            else:
                problems.append(f"the lane blocked '{label}': {reason} (decide; accept with --accept-blocked '{label}')")
        app_lines, app_problems = in_app_evidence(key, lane_root, head, blocked)
        problems += app_problems
        video_lines, video_problems = video_evidence(key, lane_root, head)
        app_lines += video_lines
        problems += video_problems
        for line in app_lines:
            print(f"  in-app: {line}")
        outcomes = [json.loads(p.read_text()) for p in round_files(review)]
        histories = [f"round {o['round']}: {round_history_text(o)}" for o in outcomes if o.get("restarts")]
        prepares = read_log("prepares", key)
        for o in outcomes:
            count = sum(1 for e in prepares if e["round"] == o["round"])
            if count != 1 + len(o.get("restarts", [])):
                note = (f"round {o['round']} was prepared {count} time(s) but records {len(o.get('restarts', []))} "
                        "restart(s): its files were discarded outside the tool (a recreated checkout, say)")
                if o["round"] in (args.accept_discarded or []):
                    decisions.append(f"accepted {note}")
                else:
                    problems.append(f"{note}; check why, then accept with --accept-discarded {o['round']}")
        if histories and not args.accept_restarts:
            problems.append("review rounds were restarted, discarding results (" + "; ".join(histories) + "); read the "
                            f"discarded results in {review}, then accept with --accept-restarts")
        elif histories:
            decisions.append("accepted review restarts (" + "; ".join(histories) + ")")
    else:
        problems.append(f"can't read the lane's gate, in-app and review evidence: no checkout at {lane_root}")
    selected = False
    before = item.get("leadSelection") if item else None
    if chosen is not None and not merged and not problems:
        # Through the state tool's own select, with its validation, guards and readers (read from this checkout, the
        # one the command runs in): what it refuses, this refuses.
        reason = args.reason or f"chosen at acceptance from {key}'s diff and its hand-off's checks"
        done = state_tool("select", "--subject", key, "--binding", str(c.binding_path), "--head", head, "--checks",
                          ",".join(chosen), "--reason", reason, check=False)
        answer = json.loads(done.stdout or "{}") if done.stdout.strip().startswith("{") else {}
        if done.returncode != 0:
            problems.append(f"the selection was refused: {answer.get('error') or (done.stderr or done.stdout).strip()[-400:]}")
        else:
            selected = True
            ids = [check.get("id") for check in (answer.get("item") or {}).get("leadSelection", {}).get("checks", [])]
            decisions.append(f"checks selected at {head[:10]}: {', '.join(map(str, ids or chosen))}")
    if args.full_suite and not merged:
        decisions.append(f"checks: the binding's fallback ({', '.join(fallback_ids(c))}), the lead's decision"
                         + (f": {args.reason}" if args.reason else ""))
    ready = bool(authority().get("readyPullRequests"))
    if ready and not merged and not problems and pr.get("draft") is not False:
        # Before the hand-off is released, so the runner never takes a draft the merge step would refuse.
        try:
            forge("ready", "--url", item.get("changeRequest") or "")
        except Stop as error:
            problems.append(f"the draft pull request couldn't be marked ready, so the hand-off stays pending: {error}")
        else:
            decisions.append(f"marked {item.get('changeRequest')} ready under the delivery's readyPullRequests grant")
    if problems:
        if selected:
            # Not accepted after all: the selection this made is undone, and one that stood before it is put back.
            if before and before.get("head") == head:
                state_tool("select", "--subject", key, "--binding", str(c.binding_path), "--head", head, "--checks",
                           json.dumps(before["checks"]), "--reason", before["reason"], check=False)
            else:
                state_tool("select", "--subject", key, "--clear", check=False)
        print(f"NOT accepted: {key}")
        for p in problems:
            print(f"  - {p}")
        if proposal is not None:
            alternative = "" if provider_selects(c) else (", or" if proposal else "") + " --full-suite"
            print(f"  proposed: lead-accept {key} " + (f"--checks {','.join(proposal)} " if proposal else "")
                  + "--reason \"<why these cover the diff>\"" + alternative)
        sys.exit(1)
    if args.full_suite and not merged and before:
        # A selection still standing would be what the runner runs: the fallback the lead chose wouldn't.
        state_tool("select", "--subject", key, "--clear")
        decisions.append(f"cleared the lead selection recorded at {str(before.get('head'))[:10]}, so the fallback runs")
    if not merged:
        state_tool("handoff", "--accept", key)
    # What the plan's producer proofs are judged by: the accepted head and the in-app items accepted as blocked.
    append_log("acceptance", key, {"head": head, "ready": ready, "blocked": {label: reason for label, reason in
                                                                           blocked.items() if label in accepted_blocked}})
    state_tool("journal", "--kind", "decision.recorded", "--subject", key, "--summary",
               (f"Hand-off checked as a claim after its merge: its PR merged at {head[:10]} into {c.base_branch}, "
                f"{len(set(numbers))} round(s) journaled, last approved by every lens, review evidence captured at "
                f"that head, gates and in-app evidence current; its files could not be checked against "
                f"{c.lane_of(key)}'s." if merged else
                f"Hand-off checked as a claim: {c.remote}/{branch} at {head[:10]}, its PR open at that head into "
                f"{c.base_branch}, {len(set(numbers))} round(s) journaled, last approved by every lens, review evidence "
                f"captured at that head, files within {c.lane_of(key)}'s, gates and in-app evidence current.")
               + (" " + "; ".join(decisions) + "." if decisions else ""))
    # Accepted already: a plan its registration can't rebuild now (a story added without its reason) says why, and
    # the next rebuild catches up.
    try:
        rebuild_plan(c)
    except Unreasoned as error:
        print(f"plan: not rebuilt: {error}")
    print(f"accepted {key} after its merge" if merged else f"accepted {key}; the runner may take it")
    if needs_video(key):
        print("  once it merges: lead-evidence page, republish the page (same address), lead-evidence published --url, "
              "and link its section from the story in the tracker")


# -- lead: cleanup ---------------------------------------------------------------------------------

def is_ancestor(commit: str, ref: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", commit, ref], check=False).returncode == 0


def worktrees_under(path: Path) -> list[Path]:
    """Registered worktrees at or inside `path`, deepest first."""
    root = path.resolve()
    found = [Path(line[len("worktree "):]) for line in git("worktree", "list", "--porcelain").splitlines()
             if line.startswith("worktree ")]
    inside = [p for p in found if p.resolve() == root or root in p.resolve().parents]
    return sorted(inside, key=lambda p: len(p.parts), reverse=True)


def evidence_dir(key: str) -> Path:
    """Where a story's lane evidence is kept once its checkout is gone: beside the record."""
    return cfg().flow_log.parent / "evidence" / key


def squash_merged(key: str, tips: dict[str, str]) -> tuple[str | None, list[str]]:
    """For a story with tips off the base: (what merged it, None) when the forge reports its change request merged and
    every tip is that request's head or below it, as a squash merge leaves them; otherwise (None, why not)."""
    item = next((q for q in load_state()["state"]["queue"] if q["subject"] == key), None)
    url = (item or {}).get("changeRequest")
    if not url or not forge_answers("get"):
        return None, []
    try:
        pr = forge("get", "--url", url)
    except Stop as error:
        return None, [f"couldn't read {url}: {error}"]
    if pr.get("state") != "merged":
        return None, [f"{url} is {pr.get('state') or 'in no state the forge names'}, not merged"]
    reported = pr.get("head")
    head = git("rev-parse", "--verify", "--quiet", f"{reported}^{{commit}}", check=False) \
        if isinstance(reported, str) and len(reported) >= 7 else ""
    if not same_commit(reported, head):
        return None, [f"{url}'s merged head {reported or '(none)'} isn't a commit in this repository"]
    past = [f"{name} ({sha[:10]})" for name, sha in tips.items() if not is_ancestor(sha, head)]
    if past:
        return None, [f"{', '.join(past)} {'is' if len(past) == 1 else 'are'} past {url}'s merged head {head[:10]}"]
    return f"{url} merged at {head[:10]}, and every tip of {key} is at or below it", []


def clean_story(key: str) -> tuple[str, list[str], list[str]]:
    """Remove one merged story's checkout and branches. Returns (status, done, left); status is cleaned, nothing,
    unmerged or refused. Nothing is removed unless every tip of the story is already on the delivery base, or its change
    request merged (by squash, say) at a head every tip is at or below: the forge is asked only in that second case."""
    c = cfg()
    branch, checkout = c.branch(key), c.lane_checkout(key)
    git("fetch", "-q", c.remote, c.base_branch)
    tips, left = {}, []
    if checkout.exists():
        dirty = git("status", "--porcelain", "--untracked-files=all", cwd=checkout, check=False)
        if dirty:
            left.append(f"{checkout} has uncommitted or untracked files:\n      " + dirty[:400].replace("\n", "\n      "))
        tips["the checkout's HEAD"] = git("rev-parse", "HEAD", cwd=checkout)
    local = git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    if local:
        tips[f"local {branch}"] = local
    remote = remote_head(branch)
    if remote:
        git("fetch", "-q", c.remote, branch, check=False)
        tips[f"{c.remote}/{branch}"] = remote
    if not tips and not left:
        return "nothing", [], []
    off_base = [f"{name} ({sha[:10]})" for name, sha in tips.items() if not is_ancestor(sha, c.base_ref)]
    merged_by = None
    if off_base:
        merged_by, why = squash_merged(key, tips)
        if merged_by is None:
            return "unmerged", [], [f"not on {c.base_ref}: {', '.join(off_base)}"] + why + left
    if left:
        return "refused", [], left
    done = []
    if checkout.exists():
        review = checkout / c.work / "review" / key
        if review.exists():
            keep = evidence_dir(key)
            shutil.copytree(review, keep, dirs_exist_ok=True, ignore=shutil.ignore_patterns("checkout", "dist"))
            done.append(f"kept the lane's review and in-app evidence in {keep}")
        for nested in worktrees_under(checkout):
            if nested.resolve() != checkout.resolve():
                remove_checkout(nested)
                done.append(f"removed the leftover checkout {nested}")
        removed = run(["git", "worktree", "remove", str(checkout)], check=False)
        if removed.returncode != 0:
            return "refused", done, [f"git worktree remove {checkout} failed: {removed.stderr.strip()[-300:]}"]
        done.append(f"removed {checkout}")
    if local:
        git("branch", "-D", branch)  # -D: its tip is on the base or at or below the merged head (checked above)
        done.append(f"deleted the local branch {branch} ({local[:10]})")
    if remote:
        allowed = (c.flow_raw.get("cleanup") or {}).get("deleteRemoteBranches") is True
        pr = forge("find", "--source", branch) if allowed else {}
        if not allowed:
            left.append(f"{c.remote}/{branch} kept ({c.name} cleanup.deleteRemoteBranches is not true)")
        elif pr.get("url"):
            left.append(f"{c.remote}/{branch} kept: its PR {pr['url']} is still open")
        else:
            git("push", "-q", c.remote, "--delete", branch)
            done.append(f"deleted {c.remote}/{branch} ({remote[:10]})")
    if not done:  # already cleaned: nothing new to journal, only what still waits (a PR not yet closed)
        left = [line for line in left if "deleteRemoteBranches is not true" not in line]
        return ("waiting" if left else "nothing"), [], left
    done = ([merged_by] if merged_by else []) + done
    state_tool("journal", "--kind", "decision.recorded", "--subject", key, "--summary",
               f"Cleaned up {key} after it merged into {c.base_branch}: " + "; ".join(done + left) + ".", check=False)
    return "cleaned", done, left


def lead_cleanup(args) -> None:
    c = cfg()
    keys = [c.require(k) for k in args.keys] if args.keys else list(c.stories)
    failed = False
    doc = load_state()
    for key in keys:
        # As the supervisor's cleanup does: lead-accept reads the checkout, and cleaning up journals a decision of its own.
        item = next((entry for entry in doc["state"]["queue"] if entry["subject"] == key), None)
        if item and unaccepted_merge(doc, item):
            print(f"{key}: waits for the lead's decision on its merge")
            failed |= bool(args.keys)
            continue
        status, done, left = clean_story(key)
        if status == "nothing":
            print(f"{key}: nothing to clean")
            continue
        print(f"{key}: {status}")
        for line in done:
            print(f"  done: {line}")
        for line in left:
            print(f"  left: {line}")
        failed |= status == "refused" or (status == "unmerged" and bool(args.keys))
    if failed:
        sys.exit(1)


def delivery_processes(lead: Path, state: Path, delivery: str, main: Path) -> list[tuple[int, str]]:
    """This delivery's merge queue, live view and reconcile sweep: orchestrate-work's runner or renderer working in
    the lead's checkout or naming its record, or this tool's `lead-reconcile` working there or in the main checkout.
    A sweep that names another delivery is left alone."""
    found = []
    listing = run(["ps", "-axo", "pid=,command="], check=False).stdout
    for line in listing.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        names = "|".join(re.escape(name) for name in TOOL_NAMES)
        if not re.search(rf"(merge_queue|render_state)\.py|(?:^|/|\s)(?:{names})\s.*\blead-(?:reconcile|run)\b", command) \
                or int(pid_text) == os.getpid():
            continue
        named = re.search(r"--delivery[ =](\S+)", command)
        if named and named.group(1) != delivery:
            continue
        cwd = run(["lsof", "-a", "-p", pid_text, "-d", "cwd", "-Fn"], check=False).stdout
        here = next((Path(l[1:]) for l in cwd.splitlines() if l.startswith("n")), None)
        homes = [lead.resolve()] + ([main.resolve()] if re.search(r"\blead-(?:reconcile|run)\b", command) else [])
        if str(state) in command or (here and here.resolve() in homes):
            found.append((int(pid_text), command))
    return found


def archived_record(path: Path) -> Path:
    """The record in an archived copy: the folder itself when it holds state.json, else its one subfolder that does
    (the supervisor's `latest` holds the record beside the flow log)."""
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    if (path / "state.json").is_file():
        return path
    found = sorted(child for child in path.iterdir() if (child / "state.json").is_file()) if path.is_dir() else []
    if len(found) != 1:
        held = "no record" if not found else "several records (" + ", ".join(child.name for child in found) + ")"
        raise Stop(f"{path} holds {held}: name the folder that holds the record's state.json")
    return found[0]


def view(args) -> None:
    """Serve the live view of the delivery's record, or of an archived copy of it, and print its address. Needs no
    checkout of the delivery: the live record is found from the delivery file at the base, and an archive needs
    nothing but itself. Views only: publishers never run from here."""
    if args.archive:
        # A copy outlives every checkout, so it is shown with the built-in page, which needs none and is drawn from
        # the record in memory: no view is rendered into the copy.
        record, release, root = archived_record(args.archive), GENERATION, None
        config = {"renderers": [], "publishers": []}
    else:
        c = cfg()
        record, release, root = c.state, c.release, c.root
        if not (record / "state.json").is_file():
            raise Stop(f"no record at {record}: it starts at lead-init. With the lead's checkout gone, serve the "
                       "supervisor's copy: view --archive PATH")
        binding = c.root / ".agents/orchestration.json"
        config = json.loads(binding.read_text()).get("state") if binding.exists() else None
    scripts = str(release / "skills/orchestrate-work/scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    sys.dont_write_bytecode = True  # the release's modules leave no bytecode in the installed generation
    import render_state  # noqa: E402  (the installed release's own live view)
    if config is None:
        config = {"renderers": [dict(entry) for entry in render_state.DEFAULT_RENDERERS], "publishers": []}
    try:
        render_state.parse_config(config)
        render_state.show(record)
    except (render_state.ConfigError, render_state.StateError) as error:
        raise Stop(f"can't serve {record}: {error}")
    if root is not None:
        os.chdir(root)  # the binding's renderers run from the checkout, as the lead's renders do

    def announce(address: str) -> None:
        print(f"http://{address}/", flush=True)
        print(f"serving {record}" + (", an archived copy: it changes only when its writer copies the record again"
                                     if args.archive else "") + "; stop with Ctrl-C", flush=True)

    try:
        render_state.serve_live(record, config, host="127.0.0.1", port=args.port, announce=announce)
    except OSError as error:
        raise Stop(f"can't serve the live view on port {args.port}: {error}")
    except KeyboardInterrupt:
        return


def reconcile_once() -> list[str]:
    """Take the binding's forge and tracker snapshots and reconcile the record with each: an open PR moves a planned
    story to review, a started ticket to building. Returns one line per snapshot."""
    c = cfg()
    binding = c.root / ".agents/orchestration.json"
    states = json.loads(binding.read_text()).get("state", {}) if binding.exists() else {}
    folder = c.state.parent / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    # First, so a story just added to the delivery is planned work, not a follow-up, when the tracker lists it. A story
    # registration refuses stays unregistered, and the sweep goes on without it.
    refused = None
    try:
        added = register_stories(c)
    except Unreasoned as error:
        added, refused = [], error
    lines = [f"registered {', '.join(added)} as planned work"] if added else []
    if refused:
        lines.append(f"registration refused: {refused}")
    for name in ("forgeSnapshot", "trackerSnapshot"):
        spec = states.get(name)
        if not spec:
            lines.append(f"{name}: the binding declares none")
            continue
        argv = expand_argv(spec["argv"], stateDirectory=str(c.state), parent=c.delivery)
        try:  # stdout alone: the snapshot is JSON, and warnings go to stderr
            taken = run(argv, cwd=c.root, check=False, timeout=int(spec.get("timeoutSeconds", 60)))
        except subprocess.TimeoutExpired:
            lines.append(f"{name}: timed out")
            continue
        if taken.returncode != 0:
            lines.append(f"{name}: failed ({taken.returncode}): {(taken.stderr or taken.stdout).strip()[-200:]}")
            continue
        snapshot = folder / f"{name}.json"
        snapshot.write_text(taken.stdout)
        done = state_tool("reconcile", "--snapshot", str(snapshot), "--binding", str(binding), check=False)
        summary = re.search(r'"summary":\s*"([^"]*)"', done.stdout or "")
        lines.append(f"{name}: " + (summary.group(1) if summary else (done.stderr or done.stdout).strip()[-200:]))
        lines += [f"{name}: {line}" for line in reconcile_report_lines(done.stdout)]
    if (plan_declared(c) or plan_log_path(c).exists()) and not refused:  # its rebuild would refuse the same story
        lines.append(rebuild_plan_quietly(c))
    return lines


def reconcile_report_lines(output: str | None) -> list[str]:
    """What a reconcile reported, from its output; output that isn't its answer reports nothing."""
    try:
        reports = json.loads(output or "").get("reports", [])
    except (ValueError, AttributeError):
        return []
    return [f"{report.get('subject') or 'the record'}: {report['note']}" for report in reports]


def lead_reconcile(args) -> None:
    """One reconcile sweep, by hand: the supervisor's every pass runs it. By hand, a story registration would refuse
    refuses the sweep, before anything is taken."""
    check_registrable(cfg())
    print(now(), "; ".join(reconcile_once()), flush=True)


def lead_close(args) -> None:
    c = cfg()
    settings = [".agents", str(c.binding_path.relative_to(c.root))]
    dirty = git("status", "--porcelain", "--", *settings, cwd=c.root, check=False)
    if dirty:
        raise Stop(f"the lead's settings have uncommitted changes; commit or drop them first:\n{dirty[:600]}")
    # Everything that can refuse is checked before the supervisor, and the serve it keeps, is stopped.
    doc = load_state()
    closed = latest_close(doc["journal"])
    if closed is None:
        raise Stop("journal delivery.closed first (orchestrate-work's close-out): lead-close runs after it")
    state = doc["state"]
    busy = [q["subject"] for q in state["queue"] if q.get("stage") not in ("merged", "stopped")]
    if busy or (state["meta"].get("checking") or {}).get("now"):
        raise Stop(f"the queue still holds work ({busy or 'a check is running'}); let it finish or stop it first")
    # The run is stopped while the supervisor finishes its pass, which under --once waits on it, and again once the
    # supervisor has stopped, in case that pass started one.
    runs: list[str | None] = []
    stopped = ask_supervisor_to_stop(c, meanwhile=lambda: runs.append(stop_full_health_run(c)))
    for line in (stopped, *runs, stop_full_health_run(c)):
        if line:
            print(line)
    lead = c.lane_checkout(c.delivery)
    done, left = [], []
    # Holds nothing: before the final render and the archive, so both carry it.
    try:
        posture = delivery_posture(c)
        if posture["posture"] == "focused":
            done.append(record_focused(c, posture))
        else:
            with plan_lock(c):
                note_posture(c, posture)
    except (Stop, ValueError, OSError, subprocess.SubprocessError) as error:
        left.append(f"the delivery posture wasn't recorded: {error}")
    for key in c.stories:
        status, d, l = clean_story(key)
        done += [f"{key}: {x}" for x in d]
        left += [f"{key}: {x}" for x in l]
    release = c.release / "skills/orchestrate-work/scripts/render_state.py"
    binding = c.root / ".agents/orchestration.json"

    def render(what: str) -> None:
        if not binding.exists():
            return
        rendered = run([sys.executable, "-B", str(release), "--dir", str(c.state), "--binding", str(binding)], check=False)
        if rendered.returncode == 0:
            done.append(what)
        else:
            left.append(f"the final render failed: {(rendered.stderr or rendered.stdout).strip()[-300:]}")
    render("rendered the views one last time")
    owed = tracker_owed(c, load_state(), closed)
    if owed:
        left.append(f"the tracker hasn't had every update: it is still waiting for {owed}. lead-close doesn't wait "
                    "for the tracker; post them, or let the publisher run")
    archive, archived = archive_delivery(c)
    done += archived
    done += finish_close(c, closed, archive)
    c.flow_log.mkdir(parents=True, exist_ok=True)
    lead_file(c, "closed.json").write_text(json.dumps({"archive": str(archive), "said": archived, "by": "lead-close",
                                                       "at": now(), "entry": closed["id"], **closed_full_health(c)},
                                                      indent=2) + "\n")
    render("rendered the views again, with the close marked finished")
    for pid, command in delivery_processes(lead, c.state, c.delivery, c.main):
        try:
            os.kill(pid, signal.SIGTERM)
            done.append(f"stopped {pid}: {command[:120]}")
        except ProcessLookupError:
            pass
    work_root = json.loads(binding.read_text()).get("workRoot") if binding.exists() else None
    if work_root:
        for nested in worktrees_under(lead / work_root):
            remove_checkout(nested)
            done.append(f"removed the runner's checkout {nested}")
    if c.ship:
        branch = expand(c.ship.get("branch", "ship/{delivery}"), delivery=c.delivery)
        path = c.main / Path(expand(c.ship.get("checkout", str(Path(c.lane_pattern).parent / "{branch}")),
                                    branch=branch.replace("/", "-")))
        if path.exists():
            dirty = git("status", "--porcelain", "--untracked-files=all", cwd=path, check=False)
            tip, pushed_tip = git("rev-parse", "HEAD", cwd=path), remote_head(branch)
            if dirty or tip != pushed_tip:
                left.append(f"{path} kept: " + ("it has uncommitted files" if dirty else f"{branch} isn't pushed at its HEAD"))
            else:
                git("worktree", "remove", str(path))
                git("branch", "-D", branch)
                done.append(f"removed {path} and the local {branch} ({c.remote}/{branch} is kept for its PR)")
    video_keys = [k for k in c.stories if needs_video(k)]
    if video_keys:
        current, problems = evidence_status(video_keys)
        if problems:
            left += [f"evidence: {line}" for line in problems]
        else:
            done.append("the evidence page is current")
    if c.in_app:
        lock, port = Path(expand(c.in_app["lock"])), int(c.in_app["port"])
        try:
            Lease(lock, port, "lead-close", 0).close()  # stops whatever a dead holder left running
            done.append(f"the in-app lease {lock} is free")
        except Stop as held:  # a live check's: its server is inside the lease, and isn't the operator's to kill
            left.append(str(held))
        else:
            if port_open(port):
                left.append(port_busy(port))
    print(f"{c.delivery}: closed out")
    for line in done:
        print(f"  done: {line}")
    for line in left:
        print(f"  left: {line}")
    print("  yours: archive the lane sessions, align your main checkout with the final base, and remove the lead's "
          f"checkout {lead} when you no longer need it (its record is archived above).")
    if left:
        sys.exit(1)


# -- lead: the supervisor ------------------------------------------------------------------

SUPERVISOR_PASS_SECONDS = 60
# A child restarted this many times within the window won't stay up, and says so in "Needs you".
CHILD_FLAPS, CHILD_FLAP_SECONDS = 3, 600
SUPERVISOR_WAIT_SECONDS = 120
# The runner's own blocker, which the hold clearing or base.repaired resolves (merge_queue.py writes it).
RUNNER_HELD = "Serving is held:"
# How the state tool's journal entry ends for a merge recovered before the lead accepted it.
MERGE_SKIPPED_ACCEPTANCE = " before the lead accepted it"
LANE_DONE = ("idle", "done")


def lead_file(c, name: str) -> Path:
    """A lead-side file beside the flow logs, named with the delivery key as every lead-side file is."""
    return c.flow_log / f"{c.delivery}.{name}"


def valid_pid(pid: object) -> bool:
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 1


def group_of(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return None


def waits_on(c) -> str:
    """What the supervisor's last pass found holding the close: its "Needs you", and the close condition's open parts.
    Read from its pass log, where each pass writes it; empty before the first pass, or once nothing holds it."""
    path = lead_file(c, "supervisor.jsonl")
    lines = [line for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    try:
        last = json.loads(lines[-1]) if lines else {}
    except ValueError:  # a pass still writing its line
        last = {}
    held = [str(item) for item in last.get("closeHeldBy") or []]
    if not held:
        return ""
    shown = "; ".join(held[:5]) + (f"; and {len(held) - 5} more" if len(held) > 5 else "")
    return f". The close waits on: {shown}"


def on_fallback(c) -> str:
    """Under selection.lead, the candidates checking on the binding's fallback because the lead selected no checks for
    them, as the runner records it on each row; empty when none is."""
    state = load_json(c.state / "state.json") or {}
    named = [(item.get("subject"), (item.get("checkSelection") or {}).get("fallback"))
             for item in state.get("queue") or [] if isinstance(item, dict)
             and item.get("stage") in ("checking", "checked")]
    named = [(subject, checks) for subject, checks in named if isinstance(checks, list) and checks]
    if not named:
        return ""
    return "; " + "; ".join(f"{subject} checks on the fallback, {', '.join(map(str, checks))}: the lead selected no "
                            "checks" for subject, checks in named)


def supervisor_line(c) -> str | None:
    """What every lead command repeats: the supervisor's state, the live view's address, any candidate checking on the
    fallback, and what the close waits on, or how to restart it."""
    if not (c.state / "state.json").exists():
        return None
    held, recorded = read_lock(supervisor_lock_path(c))
    if held:
        return (f"supervisor: running (pid {recorded.get('pid')}); live view {recorded.get('address') or 'starting'}"
                + on_fallback(c) + waits_on(c))
    said = closed_status(c)
    if said is not None:
        return "supervisor: " + said
    return f"supervisor: down; restart it with {flow_command()} lead-run --resume" + waits_on(c)


def close_progress(doc: dict) -> str | None:
    """How far this close got, by the record's marks: "finished", "partway", or None when the record has no started
    mark for it (an older record, or a delivery not closed)."""
    closed = latest_close(doc["journal"])
    closing = doc["state"]["meta"].get("closing") or {}
    if closed is None or (closing.get("started") or {}).get("entry") != closed["id"]:
        return None
    return "finished" if (closing.get("finished") or {}).get("entry") == closed["id"] else "partway"


def finishing_command(c, doc: dict) -> str:
    """What finishes a close that stopped partway: a resumed supervisor when the record meets the close condition (no
    planned story, nothing unmerged, every finish line met, no busy lane, as close_holds reads it); otherwise lead-close,
    since a resumed supervisor would hold on what is unmet and never finish it."""
    # A checkout cleanup hasn't removed yet holds only until the resumed supervisor's own next pass cleans it up, and
    # so do the needs a later pass clears by itself: the publisher's lag, and a fullHealth run ending. Every other
    # holding need waits on the lead, so a resumed supervisor would hold on it for good.
    with configured(c):  # doctor reads a delivery's close while its own configuration is the global one
        needs = [item for item in needs_you(c, doc, closing=True) if not clears_by_itself(item)]
    unmet = [hold for hold in close_holds(c, doc, needs) if not hold.startswith("not cleaned up:")]
    return "lead-run --resume" if not unmet else "lead-close"


@contextlib.contextmanager
def configured(c):
    """Run with `c` as the configuration the helpers read, then put the one before it back."""
    global CFG
    before, CFG = CFG, c
    try:
        yield c
    finally:
        CFG = before


def clears_by_itself(item: dict) -> bool:
    """Whether a "Needs you" item clears on the supervisor's own passes, with nothing from the lead."""
    return item["source"] == "tracker publisher" or item["clears"] == FULL_HEALTH_RUNNING_CLEARS


def closed_status(c) -> str | None:
    """What a stopped supervisor says of the close: finished, or stopped partway and what finishes it. A record with no
    marks reads as before, by closed.json alone."""
    tool = c.release / "skills/orchestrate-work/scripts/delivery_state.py"
    doc = json.loads(run([sys.executable, "-B", str(tool), "--dir", str(c.state), "show"]).stdout)
    closed = kept_close(c, last_close(doc["journal"]))
    progress = close_progress(doc)
    if progress == "partway":
        command = finishing_command(c, doc)
        return (f"stopped partway through the close; finish it with {flow_command()} {command}"
                + ("; lead-close doesn't wait for the tracker" if command == "lead-close" else ""))
    if progress == "finished":
        return "stopped; the delivery is closed. " + (closed_words(closed) if closed else "")
    if closed:
        return "stopped; the delivery is closed. " + closed_words(closed)
    return None


def closed_words(closed: dict) -> str:
    """Where a closed delivery went, and what is left: the friction note to write up, and the host's steps."""
    note = closed.get("friction")
    return f"Archive {closed.get('archive')}" + (f"; write up the friction note {note}, then archive the lane "
                                                  "sessions" if note else "")


def supervisor_off() -> bool:
    """RUN_DELIVERY_SUPERVISOR=off: lead-init starts no supervisor, as --no-supervisor says."""
    return os.environ.get("RUN_DELIVERY_SUPERVISOR") == "off"


def start_supervisor(c) -> str:
    """Start `lead-run` detached, in a session of its own, and return the live view's address once it holds the lock."""
    log = lead_file(c, "supervisor.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--delivery", c.delivery, "lead-run"]
    with open(log, "a") as out:
        proc = subprocess.Popen(argv, cwd=c.root, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    deadline = time.monotonic() + SUPERVISOR_WAIT_SECONDS
    try:
        while time.monotonic() < deadline:
            held, recorded = read_lock(supervisor_lock_path(c))
            if held and recorded.get("pid") == proc.pid and recorded.get("address"):
                return recorded["address"]
            if proc.poll() is not None:
                raise Stop(f"the supervisor stopped as it started: {log.read_text()[-600:].strip()}; its log is {log}")
            time.sleep(0.1)
        raise Stop(f"the supervisor didn't start within {SUPERVISOR_WAIT_SECONDS}s; its log is {log}")
    finally:
        # The supervisor is started to outlive this command, which never waits for it, so the handle is let go here:
        # without this, dropping it later warns that the subprocess "is still running".
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            del proc


def stop_orphans(c) -> list[str]:
    """Stop the children an earlier supervisor left running: each recorded child still in its group, by that group.
    A pid now outside the group belongs to someone else and is left alone."""
    lock = supervisor_lock_path(c)
    held, recorded = read_lock(lock)
    if held:
        raise Stop(f"a supervisor already runs (pid {recorded.get('pid')}); live view {recorded.get('address')}")
    group = recorded.get("pid")
    if not valid_pid(group):
        return []
    left = [child for child in recorded.get("children") or []
            if isinstance(child, dict) and valid_pid(child.get("pid")) and group_of(child["pid"]) == group]
    if not left:
        return []
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, sig)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and any(group_of(child["pid"]) == group for child in left):
            time.sleep(0.1)
        if not any(group_of(child["pid"]) == group for child in left):
            break
    else:
        raise Stop(f"the earlier supervisor's children (group {group}) are still running after SIGKILL; stop them, "
                   "then run lead-run --resume again")
    return [f"stopped {child.get('name')} (pid {child['pid']}), left by the earlier supervisor (pid {group})"
            for child in left]


def ask_supervisor_to_stop(c, meanwhile: Callable[[], None] | None = None) -> str | None:
    """Ask a running supervisor to stop after its pass, and wait for it to release its lock. `meanwhile` runs once the
    request is written, and again while the wait goes on, for what the pass may be waiting on or just starting."""
    lock = supervisor_lock_path(c)
    held, recorded = read_lock(lock)
    if not held:
        return None
    request = lead_file(c, "supervisor.stop")
    request.write_text(now() + "\n")
    if meanwhile:
        meanwhile()
    deadline = time.monotonic() + SUPERVISOR_WAIT_SECONDS + supervisor_pass_seconds(c)
    while time.monotonic() < deadline:
        if not read_lock(lock)[0]:
            request.unlink(missing_ok=True)
            return f"stopped the supervisor (pid {recorded.get('pid')}) after its pass"
        if meanwhile:
            meanwhile()
        time.sleep(0.2)
    raise Stop(f"the supervisor (pid {recorded.get('pid')}) didn't stop after its pass; see "
               f"{lead_file(c, 'supervisor.log')}")


def supervisor_pass_seconds(c) -> float:
    value = (c.flow_raw.get("supervisor") or {}).get("passSeconds", SUPERVISOR_PASS_SECONDS)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise Stop(f"{c.name} supervisor.passSeconds must be a positive number of seconds")
    return float(value)


def binding_state(c) -> dict | None:
    binding = c.shelf.json(".agents/orchestration.json")
    return binding.get("state") if binding else None


def base_head(c) -> str:
    return git("rev-parse", c.base_ref, check=False)


# One pass's steps. Each returns what it did, in a line or a few.

LANE_AT_WORK = ("assigned", "working", "reviewing", "blocked")


def lane_words(doc: dict, remembered: dict | None) -> dict:
    """Each lane's last state in its own words, with when it said it: a row the queue wrote doesn't replace it."""
    words = dict(remembered or {})
    for lane in doc["state"]["lanes"]:
        if lane.get("focusBy") != "runner":
            words[lane["id"]] = {"state": lane.get("state"), "at": lane.get("focusAt") or lane.get("since")}
    return words


def lane_moved_off(doc: dict, item: dict, words: dict | None = None) -> bool:
    """A merged story's lane has moved off it: it is idle or done, or was assigned other work after the merge. The
    queue rests a lane with nothing else open once its story merges; that row is the queue's word, so a lane whose own
    last word since its hand-off was that it is at work still stands in its checkout."""
    lane = next((entry for entry in doc["state"]["lanes"] if entry["id"] == item["lane"]), None)
    if lane is None:
        return True
    rested = lane.get("focusBy") == "runner" and lane.get("focus") == f"{item['subject']} merged"
    # The lane's own word the record kept when the queue rested its row first; a pass's memory of it after that.
    said = (lane.get("restedFrom") if rested else None) or (words or {}).get(item["lane"]) or {}
    handed = max((e["at"] for e in doc["journal"] if e["kind"] == "candidate.handed" and e.get("subject") == item["subject"]),
                 default=None)
    at_work = rested and said.get("state") in LANE_AT_WORK and bool(said.get("at")) and handed is not None \
        and said["at"] >= handed
    if lane.get("state") in LANE_DONE and not at_work:
        return True
    merged = max((e["id"] for e in doc["journal"] if e["kind"] == "candidate.merged" and e.get("subject") == item["subject"]),
                 default=None)
    if merged is None and item.get("stage") == "settled":
        # Settled with no change, it never merged: its last hand-off is where the lane left it.
        merged = max((e["id"] for e in doc["journal"] if e["kind"] == "candidate.handed"
                      and e.get("subject") == item["subject"]), default=None)
    later = [e for e in doc["journal"] if e["kind"] == "lane.assigned" and e.get("lane") == item["lane"]
             and merged is not None and e["id"] > merged and e.get("subject") != item["subject"]]
    return bool(later)


def unaccepted_merge(doc: dict, item: dict) -> dict | None:
    """The merge entry of a story recovered from the forge before the lead accepted it, while no decision follows it."""
    subject = item["subject"]
    if item["stage"] != "merged":
        return None
    merges = [e for e in doc["journal"] if e["kind"] == "candidate.merged" and e.get("subject") == subject]
    if not merges or not merges[-1].get("summary", "").endswith(MERGE_SKIPPED_ACCEPTANCE):
        return None
    if any(e["kind"] == "decision.recorded" and e.get("subject") == subject and e["id"] > merges[-1]["id"] for e in doc["journal"]):
        return None
    return None if any(entry.get("head") == item["head"] for entry in read_log("acceptance", subject)) else merges[-1]


def cleanup_merged(c, doc: dict, words: dict | None = None) -> list[str]:
    """What lead-cleanup does, for each merged story whose lane has moved off it. A story already cleaned up, by hand
    or by an earlier pass, has nothing left to clean and journals nothing."""
    said = []
    for item in doc["state"]["queue"]:
        if item["stage"] != "merged" or item["subject"] not in c.stories:
            continue
        if not lane_moved_off(doc, item, words):
            said.append(f"{item['subject']}: waits for {item['lane']} to move off it")
            continue
        # lead-accept reads the lane's evidence from its checkout, so the checkout stays until the lead has decided.
        if unaccepted_merge(doc, item):
            said.append(f"{item['subject']}: waits for the lead's decision on its merge")
            continue
        status, done, left = clean_story(item["subject"])
        if status not in ("nothing", "waiting"):
            said.append(f"{item['subject']}: {status}" + "".join(f"; {line}" for line in left))
    # A story settled with no change leaves its lane's checkout too, once the lane has moved off it.
    for entry in doc["state"].get("planned", []):
        if entry["stage"] != "settled" or entry["subject"] not in c.stories or not lane_moved_off(doc, entry, words):
            continue
        status, done, left = clean_story(entry["subject"])
        if status not in ("nothing", "waiting"):
            said.append(f"{entry['subject']}: {status}" + "".join(f"; {line}" for line in left))
    return said


def proof_state(c, plan: dict, doc: dict, unit: dict, kind: str) -> str:
    """A unit's proof, as the line map judges it: a producer proof from the plan's evidence at the queued head; handed,
    merged and review from the record, review as the lead's acceptance at the queued head."""
    subject = unit["subject"]
    if any(entry["subject"] == subject and entry["stage"] == "settled" for entry in doc["state"].get("planned", [])):
        return "done"  # closed with no change needed: every proof it owes is the lead's accepted evidence
    item = next((entry for entry in doc["state"]["queue"] if entry["subject"] == subject), None)
    source = next((k.get("source") for k in plan.get("proofKinds", []) if k.get("id") == kind), None)
    if source == "producer":
        entry = ((plan.get("evidence") or {}).get(subject) or {}).get(kind)
        if not entry or entry.get("state") == "todo":
            return "todo"
        return "stale" if item and entry.get("head") != item.get("head") else entry["state"]
    if kind == "handed":
        # What merged is past waiting, whatever the flag says, as the line map reads it.
        return "done" if item and item["stage"] != "stopped" and (item["stage"] == "merged" or not item.get("pending")) else "open"
    if kind == "merged":
        return "done" if item and item["stage"] == "merged" else "open"
    if kind == "review":
        accepted = read_log("acceptance", subject)
        return "done" if item and accepted and accepted[-1].get("head") == item.get("head") else "open"
    return "open"


def finished_lines(c, plan: dict, doc: dict) -> list[str]:
    """The finish lines whose covered criteria have every covered proof done (a waiver isn't done)."""
    units = [unit for unit in plan.get("units", []) if not unit.get("removed")]
    criteria = [(item["id"], unit) for unit in units for item in unit.get("criteria", [])]
    ready = []
    for line in plan.get("finish", []):
        covers = line.get("covers") or {"criteria": "all", "proofs": []}
        named = covers.get("criteria")
        if named == "all":
            rows = [unit for _, unit in criteria]
        elif not named:
            rows = units
        else:
            rows = [unit for cid, unit in criteria if cid in named]
        kinds = covers.get("proofs") or []

        def settled(unit: dict) -> bool:
            owed = unit.get("proofs", [])
            wanted = kinds or owed
            return all(kind not in owed or proof_state(c, plan, doc, unit, kind) == "done" for kind in wanted)
        if rows and all(settled(unit) for unit in rows):
            ready.append(line["id"])
    return ready


def journal_finish_met(c, doc: dict) -> list[str]:
    """Mark and journal each finish line whose criteria and proofs are all done, once: a line the lead already
    marked or journalled by hand is left as it is."""
    plan = load_json(c.state / PLAN_FILE)
    if not plan:
        return []
    ready = set(finished_lines(c, plan, doc))
    said = []
    for line in doc["state"]["finish"]:
        if line["id"] not in ready:
            continue
        journalled = any(e["kind"] == "finish.met" and (e.get("subject") == line["id"] or re.search(
            rf"(?<![\w-]){re.escape(line['id'])}(?![\w-])", e.get("summary", ""))) for e in doc["journal"])
        if not line.get("met"):
            patch_from(lambda fresh, line=line: {"finish": [{**entry, "met": True} if entry["id"] == line["id"] else entry
                                                            for entry in fresh["state"]["finish"]]},
                       f"finish line {line['id']} met")
        if not journalled:
            state_tool("journal", "--kind", "finish.met", "--subject", line["id"], "--summary",
                       f"{line['id']}: {line['text']} Every criterion it covers has its proofs done.")
            said.append(f"finish line {line['id']} met")
    return said


def render_views(c, only: str | None = None) -> str:
    """Render with the binding's whole `state` member, publishers included, as the runner's writes do; `only`
    "publishers" or "renderers" runs just that half, so a pass can derive "Needs you" between the two."""
    config = binding_state(c)
    if config is None:
        return "no binding: nothing to render"
    if only == "publishers":
        # `liveHome` names a renderer, and this half declares none; the renderers half still answers to it.
        config = {**{key: value for key, value in config.items() if key != "liveHome"}, "renderers": []}
    elif only == "renderers":
        config = {**config, "publishers": []}
    script = c.release / "skills/orchestrate-work/scripts/render_state.py"
    done = run([sys.executable, "-B", str(script), "--dir", str(c.state), "--state-config", json.dumps(config)],
               cwd=c.root, check=False, timeout=300)
    return "rendered" if done.returncode == 0 else f"render failed ({done.returncode}): " \
        f"{(done.stderr or done.stdout).strip()[-300:]}"


def copy_latest(c) -> Path:
    """Copy the record and the flow log, under the state lock, into the archive location's one `latest` folder, so a
    current copy outlives the lead's checkout."""
    target = archive_root(c) / "latest"
    staging = target.with_name(f".latest.{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with open(c.state / "state.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            shutil.copytree(c.state, staging / c.state.name, ignore=shutil.ignore_patterns("*.lock"))
            if c.flow_log.exists():
                shutil.copytree(c.flow_log, staging / "flow-log", ignore=shutil.ignore_patterns("*.lock"))
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    old = target.with_name(f".latest.{os.getpid()}.old")
    if target.exists():
        os.replace(target, old)
    os.replace(staging, target)
    shutil.rmtree(old, ignore_errors=True)
    return target


# "Needs you": derived each pass from live conditions, never stored.

def full_health_entries(c) -> list[dict]:
    """The full-health log: `result` entries, the lead's `rerun` entries, and what the close-out saw of the posture: a
    `skipped` entry, which has no `result` so nothing can read it as one, and a `posture` entry, for a change or a named
    code. Read by path, since the plan's producer also runs for a delivery that isn't the configured one."""
    path = c.flow_log / f"{c.delivery}.full-health.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def latest_close_out(entries: list[dict]) -> dict | None:
    """The latest entry that says what the close-out did or saw. A rerun request isn't one."""
    return next((e for e in reversed(entries) if e.get("result") or e.get("skipped") or e.get("posture")), None)


def full_health_skip(c) -> dict | None:
    """The skip the close-out stands on, if its latest word is one."""
    latest = latest_close_out(full_health_entries(c))
    return latest if latest and latest.get("skipped") else None


def full_health_results(c, head: str) -> tuple[dict | None, bool]:
    """(the latest fullHealth result at this base head, whether the lead asked for a rerun after it)."""
    entries = read_log("full-health", c.delivery)
    results = [e for e in entries if e.get("head") == head and e.get("result")]
    last = results[-1] if results else None
    rerun = last is not None and any(e.get("rerun") and e.get("head") == head and e["at"] > last["at"] for e in entries)
    return last, rerun


def blocker_resolved(c, doc: dict, entry: dict, resolutions: set[int]) -> bool:
    if entry["id"] in resolutions:
        return True
    if not entry.get("lane") and not entry.get("subject") and entry.get("summary", "").startswith(RUNNER_HELD):
        repaired = any(e["kind"] == "base.repaired" and e["id"] > entry["id"] for e in doc["journal"])
        return repaired or not doc["state"]["meta"].get("hold")
    return False


# A running fullHealth's need: the run ending clears it, on a pass of its own.
FULL_HEALTH_RUNNING_CLEARS = "the run ending, or lead-close, which stops it"


def tracker_item(item: dict) -> dict:
    """A tracker "Needs you" item with where it comes from and what clears it: the publisher's lag clears when it
    posts; a ledger line is only shown, and stays."""
    if not item["holding"]:
        return {**item, "source": "tracker ledger", "clears": "nothing: it stays listed for the friction note"}
    return {**item, "source": "tracker publisher", "clears": "the publisher posting those entries on a later pass"}


def needs_you(c, doc: dict, flapping: tuple[str, ...] = (), closing: bool = False) -> list[dict]:
    """What waits on the lead or the operator, each {holding, source, text, clears}: holding items hold the close;
    shown ones are listed in the friction note. The close condition, the supervisor's pass log and the line map read
    this same list."""
    conf = tracker_conf(c)
    items = [tracker_item(item) for item in
             tracker_publisher.needs_you(doc, c.state, c.flow_log, c.delivery, conf, closing=closing)]
    head = base_head(c)
    running = full_health_running(c)
    if running is not None:
        items.append({"holding": True, "source": "fullHealth", "text": f"{running_words(running)}; the close waits "
                      "for its result", "clears": FULL_HEALTH_RUNNING_CLEARS})
    last, rerun = full_health_results(c, head) if head else (None, False)
    if last and last["result"] == "failed" and not rerun and delivery_posture(c)["posture"] != "focused":
        items.append({"holding": True, "source": "fullHealth",
                      "text": f"fullHealth failed at {head[:10]}: {last.get('output', '')[-300:]}. It "
                      f"runs again at a later base head, or when you judge it flaky: {flow_command()} lead-resolve "
                      "full-health --why TEXT",
                      "clears": f"a pass at a later base head, or {flow_command()} lead-resolve full-health --why TEXT"})
    events = [e for e in doc["journal"] if e["kind"] in ("base.broken", "base.repaired")]
    if events and events[-1]["kind"] == "base.broken":
        # A break fullHealth found clears with its next pass; the queue's own break waits for the lead. Under focused no
        # run comes to clear fullHealth's, so, like its failed run, it holds nothing, but it stays listed: serve still
        # waits for base.repaired whatever the posture.
        found = only_full_health_breaks(doc["journal"])
        posture = delivery_posture(c) if found else None
        source = f"journal entry {events[-1]['id']} (base.broken)"
        if posture and posture["posture"] == "focused":
            said = posture_said(posture)
            items.append({"holding": False, "source": source,
                          "text": f"The delivery base is broken: {events[-1].get('summary', '')[:300]}. It holds nothing "
                          f"while the base declares the focused posture on {said['source']} ({said['reason'][:200]}), "
                          "and serve still waits for base.repaired",
                          "clears": "journalling base.repaired, or a fullHealth pass once the base returns to gate"})
        else:
            items.append({"holding": True, "source": source,
                          "text": f"The delivery base is broken: {events[-1].get('summary', '')[:300]}. "
                          "Repair it, then journal base.repaired",
                          "clears": ("a fullHealth pass at a later base head, or " if found else "")
                          + "journalling base.repaired"})
    for lane in doc["state"]["lanes"]:
        if lane.get("state") == "blocked":
            items.append({"holding": True, "source": f"{lane['id']}'s state", "clears": "the lane's state changing",
                          "text": f"{lane['id']} is blocked: {lane.get('focus', '')}"})
        elif lane.get("waitingOn") == "lead":
            items.append({"holding": True, "source": f"{lane['id']}'s state", "clears": "the lane no longer waiting on you",
                          "text": f"{lane['id']} waits on you: {lane.get('focus', '')}"})
    for item in doc["state"]["queue"]:
        merge = unaccepted_merge(doc, item)
        if merge:
            items.append({"holding": True, "source": f"journal entry {merge['id']} (candidate.merged)",
                          "text": f"{item['subject']} merged before you accepted it, and its checkout is kept until you decide",
                          "clears": f"{flow_command()} lead-accept {item['subject']}, or journalling decision.recorded "
                          "for it if lead-accept can't take it"})
    resolutions = {entry.get("journal") for entry in read_log("resolutions", c.delivery)}
    for entry in doc["journal"]:
        if entry["kind"] == "blocker.recorded" and not blocker_resolved(c, doc, entry, resolutions):
            runners = not entry.get("lane") and not entry.get("subject") and entry.get("summary", "").startswith(RUNNER_HELD)
            items.append({"holding": True, "source": f"journal entry {entry['id']} (blocker.recorded)",
                          "text": f"Blocker {entry['id']}"
                          + (f" ({entry.get('subject') or entry.get('lane')})" if entry.get("subject") or entry.get("lane")
                             else "") + f": {entry.get('summary', '')[:300]}. Resolve it with {flow_command()} "
                          f"lead-resolve {entry['id']} --why TEXT",
                          "clears": ("the runner's hold clearing, journalling base.repaired, or " if runners else "")
                          + f"{flow_command()} lead-resolve {entry['id']} --why TEXT"})
    for name in flapping:
        items.append({"holding": True, "source": f"the supervisor's {name}", "clears": f"the {name} staying up",
                      "text": f"The supervisor's {name} won't stay up; its log is "
                      f"{lead_file(c, f'supervisor.{name}.log')}"})
    if c.forge.get("mergeNeedsReady") and not authority().get("readyPullRequests"):
        drafts = [item["subject"] for item in doc["state"]["queue"] if item["stage"] != "merged" and not item.get("pending")
                  and not any(a.get("ready") for a in read_log("acceptance", item["subject"]))]
        if drafts:
            items.append({"holding": True, "source": "acceptance", "text": f"Accepted as drafts while the merge step "
                          f"refuses drafts: {', '.join(drafts)}. Mark each ready, then lead-accept --requeue it if it "
                          "stopped", "clears": "marking each ready, then lead-accept --requeue for one that stopped"})
    return items


NEEDS_FILE = "needs-you.json"


def write_needs(c, items: list[dict] | None, number: int, started: float) -> None:
    """Replace the record's "Needs you" list, which renderers read: this pass's items, or, with None, the word that
    the supervisor isn't running. A running list is stale once one pass is overdue: after the pass interval and once
    more the longer of that interval and this pass's own time so far."""
    at = datetime.datetime.now(datetime.timezone.utc)
    written: dict = {"spec": "delivery-needs-you/1", "running": items is not None, "pass": number,
                     "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "items": items or []}
    if items is not None:
        interval = supervisor_pass_seconds(c)
        stale = at + datetime.timedelta(seconds=interval + max(interval, time.monotonic() - started))
        written["staleAfter"] = stale.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = c.state / NEEDS_FILE
    staging = path.with_name(f".{NEEDS_FILE}.{os.getpid()}")
    staging.write_text(json.dumps(written, indent=2) + "\n")
    with open(c.state / "state.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            os.replace(staging, path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def removed_subjects(c) -> set[str]:
    return {key for entry in read_plan_log(c) if entry.get("type") == "change" and entry.get("kind") == "removed"
            for key in entry.get("subjects", [])}


def close_holds(c, doc: dict, needs: list[dict]) -> list[str]:
    """What holds the close: the close condition, read as the reasons it doesn't hold yet."""
    state, removed = doc["state"], removed_subjects(c)
    holds = []
    planned = [p["subject"] for p in state.get("planned", []) if p["subject"] not in removed and p["stage"] != "settled"]
    if planned:
        holds.append(f"not handed off: {', '.join(planned)}")
    unmerged = [q["subject"] for q in state["queue"] if q["stage"] != "merged" and q["subject"] not in removed]
    if unmerged:
        holds.append(f"not merged: {', '.join(unmerged)}")
    unmet = [line["id"] for line in state["finish"] if not line.get("met")]
    if unmet:
        holds.append(f"finish lines not met: {', '.join(unmet)}")
    busy = [lane["id"] for lane in state["lanes"] if lane.get("state") not in LANE_DONE]
    if busy:
        holds.append(f"lanes not idle or done: {', '.join(busy)}")
    # A merge can land after the pass's cleanup read the record, and the close ends the passes: it waits for the next
    # pass's cleanup, as it does for a checkout cleanup couldn't remove.
    left = [q["subject"] for q in state["queue"] if q["stage"] == "merged" and q["subject"] in c.stories
            and c.lane_checkout(q["subject"]).exists()]
    if left:
        holds.append(f"not cleaned up: {', '.join(left)}")
    holds += [item["text"] for item in needs if item["holding"]]
    return holds


# The close-out (C6): each step must succeed before the next, and each is skipped once done.

def append_full_health(c, entry: dict) -> str:
    """Append to the full-health log, which is an input of plan.json, and rebuild it. One line on the rebuild."""
    append_log("full-health", c.delivery, entry)
    return rebuild_plan_quietly(c)


def journal_posture(c, summary: str) -> None:
    with marked_decision(c) as written:
        written.append(journal_id(state_tool("journal", "--kind", "decision.recorded", "--summary", summary)))


def note_posture(c, posture: dict) -> str | None:
    """Journal what is new about the posture at close-out, once however many passes see it: a change, and a declaration
    that didn't take. Nothing is written for a repository whose gate has nothing to say. Returns the plan's line."""
    code, said = posture["code"], posture_said(posture)
    seen = latest_close_out(full_health_entries(c))
    before = (seen.get("posture") or "gate") if seen else None
    moved = before is not None and before != posture["posture"]
    named = code in ("posture_malformed", "posture_unreadable") and (seen or {}).get("code") != code
    if not (moved or named):
        return None
    if moved:
        journal_posture(c, f"The delivery posture changed from {before} to {posture['posture']} on {said['source']}: "
                        f"{said['reason']}")
    if named:
        journal_posture(c, f"fullHealth runs as under gate, {code}: {said['reason']}")
    return append_full_health(c, {"head": posture["baseTipSha"], "code": code, **said})


def record_focused(c, posture: dict) -> str:
    """The close-out under `focused`: no run, and a skip in the log unless a real pass already stands at this head. Once
    per head, source and reason. The first skip is journalled; each later one is the posture change's to say."""
    head, said = posture["baseTipSha"], posture_said(posture)
    with plan_lock(c):
        note_posture(c, posture)
        last, _ = full_health_results(c, head)
        if last and last["result"] == "passed":
            return f"fullHealth passed at {head[:10]}"
        words = (f"fullHealth skipped at {head[:10]} under the {said['posture']} posture declared on {said['source']}, and "
                 f"no run started: {said['reason']}")
        entries = full_health_entries(c)
        latest = latest_close_out(entries)
        rebuilt = ""
        if not (latest and latest.get("skipped") and latest.get("head") == head
                and all(latest.get(key) == said[key] for key in said)):
            if not any(e.get("skipped") for e in entries):
                journal_posture(c, words)
            rebuilt = append_full_health(c, {"head": head, "skipped": True, **said})
    return words + (f"; {rebuilt}" if "not rebuilt" in rebuilt else "")


def full_health_timeout(binding: dict) -> int:
    """The seconds one fullHealth run may take: the binding's fullHealthTimeoutSeconds, or its checkTimeoutSeconds when
    it sets none. Never checkTimeoutCeiling, which bounds the queue's checks: the post-merge tier is not one of them."""
    return int(binding.get("fullHealthTimeoutSeconds", binding.get("checkTimeoutSeconds", 1800)))


def full_health_break(entry: dict) -> bool:
    """Whether a base.broken is the one a fullHealth run journalled, which a later pass of it repairs."""
    return entry["kind"] == "base.broken" and entry.get("summary", "").startswith("fullHealth failed")


def only_full_health_breaks(journal: list[dict]) -> bool:
    """Whether every base.broken since the latest base.repaired is fullHealth's own: a pass may repair those, never a
    break the queue found, which the lead confirms however many breaks of fullHealth's follow it."""
    repaired = max((e["id"] for e in journal if e["kind"] == "base.repaired"), default=0)
    return all(full_health_break(e) for e in journal if e["kind"] == "base.broken" and e["id"] > repaired)


def merge_words(c, doc: dict, head: str, since: int) -> tuple[dict | None, str]:
    """The merge a run at this head follows, as {subject, journal} and in words: the latest candidate.merged, with any
    others since the run before it (a batch), and whether the head moved past the queue's last merge outside it."""
    merges = [e for e in doc["journal"] if e["kind"] == "candidate.merged"]
    if not merges:
        return None, "with nothing merged"
    last = merges[-1]
    words = f"after {last.get('subject') or 'a candidate'}'s merge (journal entry {last['id']})"
    batch = [e.get("subject") for e in merges[:-1] if e["id"] > since and e.get("subject")]
    if batch:
        words += f", which landed with {', '.join(batch)}"
    if doc["state"]["meta"].get("baseHead") not in (None, head):
        words += f", and {head[:10]}, which landed outside the queue"
    return {"subject": last.get("subject"), "journal": last["id"]}, words


def run_full_health(c, binding: dict, head: str, posture: dict, named: str,
                    started: Callable[[int], None] | None = None, checkout: Path | None = None) -> tuple[bool, str]:
    """One gate run of fullHealth at this base head, in a fresh checkout of it. Its result goes to the full-health log,
    and the journal says what it means for the base, naming the merge it follows: base.verified on a pass,
    base.broken on a failure, and base.repaired on the first pass after a failed run. The result and its journal entry
    are written with SIGTERM held off, so a stop never leaves one without the other."""
    doc = load_state()
    before = [e for e in read_log("full-health", c.delivery) if e.get("result")]
    merge, after = merge_words(c, doc, head, before[-1].get("journalThrough", 0) if before else 0)
    checkout = checkout or Path(tempfile.mkdtemp(prefix="run-delivery-full-health-")) / "base"
    try:
        git("worktree", "add", "--detach", "-q", str(checkout), head, cwd=c.root)
        for step in prepare_commands():
            run(step, cwd=checkout)
        code, out = run_group(list(binding["fullHealth"]), checkout, full_health_timeout(binding), started=started)
    except Stop as error:
        code, out = 1, str(error)
    finally:
        run(["git", "worktree", "remove", "--force", str(checkout)], cwd=c.root, check=False)
        shutil.rmtree(checkout.parent, ignore_errors=True)
    result = "passed" if code == 0 else "failed"
    through = max((e["id"] for e in doc["journal"]), default=0)
    # Held off through the journal entry, and in the state tool it starts, which inherits the mask.
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        return record_full_health(c, head, result, out, posture, merge, after, through, before, doc, named)
    finally:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})


def record_full_health(c, head: str, result: str, out: str, posture: dict, merge: dict | None, after: str,
                       through: int, before: list[dict], doc: dict, named: str) -> tuple[bool, str]:
    """A run's result in the full-health log, and the journal entry that says what it means for the base."""
    with plan_lock(c):
        append_full_health(c, {"head": head, "result": result, "output": out[-2000:], "posture": "gate",
                               "code": posture["code"], "merge": merge, "journalThrough": through})
    if result == "failed":
        kind, words = "base.broken", f"fullHealth failed at {head[:10]} {after}: {out.strip()[-300:] or 'no output'}"
    else:
        # A repair only of a break fullHealth found: a pass never confirms a break the queue found, which the lead does.
        repairs = before and before[-1]["result"] == "failed" and only_full_health_breaks(doc["journal"])
        kind = "base.repaired" if repairs else "base.verified"
        words = f"fullHealth passed at {head[:10]} {after}"
    state_tool("journal", "--kind", kind, "--summary", words,
               *(["--subject", merge["subject"]] if merge and merge.get("subject") else []))
    return result == "passed", f"fullHealth {result} at {head[:10]}{named}"


def declared_words(posture: dict) -> str:
    """What a gate run adds to its report: only a declaration that didn't take."""
    return f" ({posture['code']}: {posture_said(posture)['reason']})" if posture["code"] in (
        "posture_malformed", "posture_unreadable") else ""


# A fullHealth run leads a session of its own and holds its lock while it runs, recording its pid, head and start, its
# checkout, and its command's pid, each pid with the time its process started. A pass starts one and polls it, so the
# supervisor keeps passing beside it; one killed with its supervisor alive is adopted by the next, since the lock still
# says it runs; a start that finds the lock held runs nothing. A pid is signalled only while its process is the one
# recorded, by its start time, never a process that came to reuse it.
FULL_HEALTH_STARTS: list[subprocess.Popen] = []  # the children this process started, reaped once they exit
FULL_HEALTH_LOCK_SECONDS = 2  # how long a start retries the lock, which a pass's probe holds for an instant


def full_health_lock_path(c) -> Path:
    return lead_file(c, "full-health.lock")


def full_health_running(c) -> dict | None:
    """The fullHealth run holding its lock, as its lock records it, or None when none runs."""
    held, recorded = read_lock(full_health_lock_path(c))
    return recorded if held else None


def running_words(run: dict) -> str:
    return (f"fullHealth running at {str(run.get('head', ''))[:10]} since {run.get('startedAt', 'its start')} "
            f"(pid {run.get('pid')})")


def process_started(pid: int) -> str | None:
    """When the process with this pid started, as ps says it, or None when none has it. In UTC and the C locale, so the
    supervisor that records it and the lead's shell that checks it read the same words."""
    done = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False,
                          env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
    return done.stdout.strip() or None


def same_group(pid: object, started: object) -> bool:
    """Whether pid still leads the group of the process recorded as started then."""
    return valid_pid(pid) and isinstance(started, str) and group_of(pid) == pid and process_started(pid) == started


def reap_full_health_starts() -> None:
    for proc in list(FULL_HEALTH_STARTS):
        if proc.poll() is not None:
            FULL_HEALTH_STARTS.remove(proc)


def record_identity(c) -> str:
    """What names this record to a run started for it: its first journal entry's time, which lead-init --replace
    changes, so a run that starts after the record is replaced runs nothing."""
    journal = load_state()["journal"]
    return journal[0]["at"] if journal else ""


def start_full_health(c, head: str, wait: bool) -> dict | None:
    """Start a fullHealth run at this head in a session of its own. With wait, as lead-run --once does, return once it
    has ended; otherwise once it holds its lock, returning what the lock records, or None when it ended first."""
    reap_full_health_starts()
    argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--delivery", c.delivery, "full-health-run",
            "--head", head, "--record", record_identity(c)]
    log = lead_file(c, "full-health.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as out:
        proc = subprocess.Popen(argv, cwd=c.root, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    if wait:
        proc.wait()
        return None
    FULL_HEALTH_STARTS.append(proc)
    deadline = time.monotonic() + SUPERVISOR_WAIT_SECONDS
    while time.monotonic() < deadline:
        running = full_health_running(c)
        if running and running.get("pid") == proc.pid:
            return running
        if proc.poll() is not None:
            return None
        time.sleep(0.05)
    return full_health_running(c)


def launch_full_health(c, head: str, named: str, wait: bool) -> str:
    """Start a run at this head and say what it is doing, or, once it has ended, what it found. None starts once the
    supervisor is asked to stop: lead-close stops the run it finds, and must not find another."""
    if lead_file(c, "supervisor.stop").exists():
        return f"fullHealth not started at {head[:10]}: the supervisor is asked to stop"
    started = start_full_health(c, head, wait)
    if started is not None:
        return running_words(started)
    last, _ = full_health_results(c, head)
    if last:
        return f"fullHealth {last['result']} at {head[:10]}{named}"
    return f"fullHealth at {head[:10]} ended with no result; its log is {lead_file(c, 'full-health.log')}"


def post_merge_step(c, wait: bool = False) -> str:
    """The post-merge tier: under gate, fullHealth runs at each base head the delivery's merges reach, once a merge has
    landed, and not again at that head until lead-resolve asks. It runs beside the passes, in a child of its own, and a
    pass that finds it running waits for it to end. Close-out runs it only at a head with no run."""
    binding = c.shelf.json(".agents/orchestration.json") or {}
    if not binding.get("fullHealth"):
        return "fullHealth: the binding declares none"
    if not any(e["kind"] == "candidate.merged" for e in load_state()["journal"]):
        return "fullHealth: nothing merged yet"
    posture = delivery_posture(c)
    if posture["posture"] == "focused":
        return "fullHealth: not run after merges under the focused posture"
    with plan_lock(c):
        note_posture(c, posture)
    named = declared_words(posture)
    reap_full_health_starts()
    running = full_health_running(c)
    if running is not None:
        return running_words(running)
    git("fetch", "-q", c.remote, c.base_branch, check=False)
    head = base_head(c)
    last, rerun = full_health_results(c, head)
    if last and not rerun:
        return f"fullHealth {last['result']} at {head[:10]}{named}"
    return launch_full_health(c, head, named, wait)


def full_health_step(c, wait: bool = False) -> tuple[bool, str]:
    binding = c.shelf.json(".agents/orchestration.json") or {}
    argv = binding.get("fullHealth")
    if not argv:
        return True, "fullHealth: the binding declares none"
    posture = delivery_posture(c)
    if posture["posture"] == "focused":
        return True, record_focused(c, posture)
    with plan_lock(c):
        note_posture(c, posture)
    named = declared_words(posture)
    running = full_health_running(c)
    if running is not None:
        return False, running_words(running)
    git("fetch", "-q", c.remote, c.base_branch, check=False)
    head = base_head(c)
    last, rerun = full_health_results(c, head)
    if last and last["result"] == "passed":
        return True, f"fullHealth passed at {head[:10]}{named}"
    if last and not rerun:
        return False, f"fullHealth failed at {head[:10]}{named}; it runs again at a later head, or on lead-resolve full-health"
    # No run at this head: after a merge the post-merge step has run, so this is a delivery that merged nothing.
    line = launch_full_health(c, head, named, wait)
    last, _ = full_health_results(c, head)
    return bool(last and last["result"] == "passed" and line.startswith("fullHealth passed")), line


def full_health_run(args) -> None:
    """One fullHealth run at --head, as a pass starts it, in a session of its own, for the record --record names. It
    holds the run's lock while it runs, so a second start runs nothing, and checks again under the lock that no result
    stands at the head. A run killed outright left its command running and its checkout behind: this start stops the
    one and removes the other first. Stopped, it stops its own command's group, removes its checkout and records
    nothing, unless it was writing its result, which it finishes."""
    c = cfg()
    # Its log is a file: each line is flushed as it's said, so a reader sees it while the run goes on.
    sys.stdout.reconfigure(line_buffering=True)
    path = full_health_lock_path(c)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    deadline = time.monotonic() + FULL_HEALTH_LOCK_SECONDS
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() < deadline:
                time.sleep(0.05)
                continue
            handle.close()
            _, recorded = read_lock(path)
            print(f"a fullHealth run already runs at {str(recorded.get('head', ''))[:10]} (pid {recorded.get('pid')}); "
                  "this start runs nothing")
            return
    try:
        handle.seek(0)
        try:
            earlier = json.loads(handle.read() or "{}")
        except ValueError:
            earlier = {}
        earlier = earlier if isinstance(earlier, dict) else {}
        said = {"pid": os.getpid(), "pidStarted": process_started(os.getpid()), "head": args.head, "startedAt": now()}

        def record(fields: dict) -> None:
            said.update(fields)
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(said))
            handle.flush()

        record({})
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
        if same_group(earlier.get("command"), earlier.get("commandStarted")):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(earlier["command"], signal.SIGKILL)
            print(f"stopped the command (pid {earlier['command']}) of a fullHealth run killed at "
                  f"{str(earlier.get('head', ''))[:10]}")
        left = earlier.get("checkout")
        if isinstance(left, str) and Path(left).name == "base" and Path(left).parent.name.startswith(
                "run-delivery-full-health-"):
            forget_checkout(c, Path(left))
        if record_identity(c) != args.record:
            print("the record was replaced since this run was started; it runs nothing")
            return
        binding = c.shelf.json(".agents/orchestration.json") or {}
        if not binding.get("fullHealth"):
            print("fullHealth: the binding declares none")
            return
        posture = delivery_posture(c)
        if posture["posture"] == "focused":
            print("fullHealth: not run under the focused posture")
            return
        last, rerun = full_health_results(c, args.head)
        if last and not rerun:
            print(f"fullHealth {last['result']} at {args.head[:10]} already; this start runs nothing")
            return
        checkout = Path(tempfile.mkdtemp(prefix="run-delivery-full-health-")) / "base"
        record({"checkout": str(checkout)})
        try:
            print(run_full_health(c, binding, args.head, posture, declared_words(posture),
                                  started=lambda pid: record({"command": pid, "commandStarted": process_started(pid)}),
                                  checkout=checkout)[1])
        finally:
            # Stopped with its run, by run_group, and its checkout removed by run_full_health.
            record({"command": None, "commandStarted": None, "checkout": None})
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def forget_checkout(c, checkout: Path) -> None:
    """Remove a killed run's checkout and its entry in the repository's worktrees, and no other: a prune would also drop
    any other checkout whose folder is away just then."""
    run(["git", "worktree", "remove", "--force", str(checkout)], cwd=c.root, check=False)
    shutil.rmtree(checkout.parent, ignore_errors=True)
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=c.root))
    for entry in (common / "worktrees").glob("*/gitdir") if (common / "worktrees").is_dir() else []:
        with contextlib.suppress(OSError):
            # Git records the resolved folder: /private/var for a checkout under /var.
            if Path(entry.read_text().strip()).parent.resolve() == checkout.resolve():
                shutil.rmtree(entry.parent, ignore_errors=True)


def stop_full_health_run(c) -> str | None:
    """Stop the fullHealth run holding its lock, by the group its recorded pid leads, once the lock names the process
    that holds it, and confirm it let the lock go. Says whether it recorded its result first."""
    path = full_health_lock_path(c)
    deadline = time.monotonic() + FULL_HEALTH_LOCK_SECONDS * 5
    while True:
        held, recorded = read_lock(path)
        if not held:
            return None
        pid = recorded.get("pid")
        if same_group(pid, recorded.get("pidStarted")):
            break
        # A start that has only just taken the lock still shows the last run's record: wait for its own.
        if time.monotonic() > deadline:
            raise Stop(f"{path} is held by a process its record doesn't name; stop the fullHealth run yourself, then "
                       "run this again")
        time.sleep(0.1)
    for sig, wait in ((signal.SIGTERM, 30), (signal.SIGKILL, 5)):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, sig)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and read_lock(path)[0]:
            time.sleep(0.1)
        if not read_lock(path)[0]:
            break
    else:
        raise Stop(f"the fullHealth run (pid {pid}) still holds {path} after SIGKILL; stop it, then run this again")
    # A run stopped by SIGKILL can't stop its command's group itself.
    if same_group(recorded.get("command"), recorded.get("commandStarted")):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(recorded["command"], signal.SIGKILL)
    head = str(recorded.get("head", ""))
    recorded_first = any(e.get("result") and e.get("head") == head and e["at"] >= recorded.get("startedAt", "")
                         for e in read_log("full-health", c.delivery))
    return (f"stopped the fullHealth run at {head[:10]} (pid {pid}); "
            + ("it had recorded its result" if recorded_first else "it recorded no result"))


def deferred_findings(c) -> list[dict]:
    """Every finding a story's review deferred, {story, finding, title}: from the lane's review folder, or the
    evidence kept beside the record once its checkout is gone."""
    found: dict[tuple[str, str], dict] = {}
    for key in c.stories:
        for folder in (c.lane_checkout(key) / c.work / "review" / key, evidence_dir(key)):
            for path in round_files(folder) if folder.is_dir() else []:
                outcome = json.loads(path.read_text())
                for entry in outcome.get("entries", []):
                    for finding in (entry.get("result") or {}).get("findings", []):
                        if finding.get("state") == "deferred":
                            found[(key, finding["id"])] = {"story": key, "finding": finding["id"],
                                                           "title": str(finding.get("deferredIssue", ""))[:200]}
    return list(found.values())


FOLLOWUP = "Deferred finding {finding} of {story} becomes follow-up {story}-{finding}: {title}"


def followups_step(c, doc: dict) -> tuple[bool, str]:
    """One follow-up item per deferred finding, keyed by story and finding id, which is also its followups[] id.
    Each is journalled as a decision marked for the tracker publisher to file; the step holds until the publisher's
    ledger covers every one: a transient failure is retried by its next run, and a permanent one is shown. By hand,
    the record's followups are all there is."""
    have = {item["id"] for item in doc["state"]["followups"]}
    missing = [f for f in deferred_findings(c) if f"{f['story']}-{f['finding']}" not in have]
    for finding in missing:
        with marked_decision(c, followup=finding) as written:
            written.append(journal_id(state_tool("journal", "--kind", "decision.recorded", "--subject",
                                                 finding["story"], "--summary", FOLLOWUP.format(**finding))))
    if missing:
        patch_from(lambda fresh: {"followups": [*fresh["state"]["followups"],
                                                *({"id": f"{f['story']}-{f['finding']}", "title": f["title"],
                                                   "reference": None} for f in missing)]},
                   "the deferred findings' follow-ups")
        render_views(c)
        doc = load_state()
    if tracker_conf(c)["mode"] != "publisher":
        return True, f"follow-ups: {len(doc['state']['followups'])} recorded"
    marks = {entry["journal"] for entry in tracker_publisher.read_flow(c.flow_log, c.delivery, tracker_publisher.MARKS)
             if entry.get("followup")}
    journal = sorted(doc["journal"], key=lambda e: e["id"])
    done = tracker_publisher.covered(tracker_publisher.read_ledger(c.state), tracker_publisher.init_id(journal))
    waiting = sorted(marks - done)
    if waiting:
        return False, f"follow-ups: waiting for the tracker publisher to file journal entries {', '.join(map(str, waiting))}"
    return True, f"follow-ups: {len(marks)} filed or refused"


# New work after a close reopens the delivery, as the line map reads it.
REOPENING = ("lane.assigned", "candidate.handed", "check.started")


def latest_close(journal: list[dict]) -> dict | None:
    """The delivery.closed entry that closes the delivery now: the latest one, unless work resumed after it."""
    closed = None
    for entry in sorted(journal, key=lambda e: e["id"]):
        if entry["kind"] == "delivery.closed":
            closed = entry
        elif entry["kind"] in REOPENING:
            closed = None
    return closed


def last_close(journal: list[dict]) -> dict | None:
    """The latest delivery.closed entry, reopened since or not."""
    return next((e for e in sorted(journal, key=lambda e: e["id"], reverse=True) if e["kind"] == "delivery.closed"), None)


def tracker_owed(c, doc: dict, closed: dict) -> str | None:
    """What the tracker hasn't had yet through the closing entry, or None when it has every update."""
    mode = tracker_conf(c)["mode"]
    if mode == "publisher":
        journal = sorted(doc["journal"], key=lambda e: e["id"])
        done = tracker_publisher.covered(tracker_publisher.read_ledger(c.state), tracker_publisher.init_id(journal))
        owed = [e["id"] for e in journal if e["id"] <= closed["id"] and e["id"] not in done]
        if owed:
            return f"the tracker publisher to cover journal entries {', '.join(map(str, owed[:10]))}"
    return None


def closed_step(c) -> tuple[bool, str]:
    """Journal delivery.closed once for this close, render, then wait until the tracker has every entry through it.
    A delivery closed, reopened by new work and closed again is closed by a new entry."""
    doc = load_state()
    closed = latest_close(doc["journal"])
    if closed is None:
        skip = full_health_skip(c)
        skipped = (f" fullHealth was skipped under the {skip['posture']} posture declared on {skip['source']}: "
                   f"{skip['reason']}" if skip else "")
        state_tool("journal", "--kind", "delivery.closed", "--summary",
                   f"{c.delivery} closed: every story merged or removed, every finish line met, and nothing holding.{skipped}")
        render_views(c)
        doc = load_state()
        closed = latest_close(doc["journal"])
    owed = tracker_owed(c, doc, closed)
    if owed:
        return False, f"waiting for {owed}"
    return True, f"delivery.closed is journal entry {closed['id']}"


def new_archive_folder(root: Path) -> Path:
    """A new, empty folder under `root`, named by the time to the microsecond as replace_record's are, so two close-outs
    in one second don't collide. A name already taken, by a clock that didn't move or another process, gets the next
    free -002, -003 suffix. Every name sorts in the order the folders were made."""
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S.%f")
    root.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        folder = root / (stamp if n == 1 else f"{stamp}-{n:03d}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise Stop(f"{root} already holds 999 archives stamped {stamp}; remove some, then run it again")


def archive_delivery(c) -> tuple[Path, list[str]]:
    """Copy the record, the lead-side logs, the lanes' evidence and the plan review into a new archive folder."""
    archive = new_archive_folder(archive_root(c))
    for source in (c.state, c.flow_log, c.flow_log.parent / "evidence"):
        if source.exists():
            shutil.copytree(source, archive / source.name)
    reviewed = archive_plan_review(archive)
    return archive, [f"archived the record, the lead-side logs{', the plan review' if reviewed else ''} and the lanes' "
                     f"evidence in {archive}"]


def closed_full_health(c) -> dict:
    """What closed.json says of a skipped run: nothing at all when a real result stands."""
    skip = full_health_skip(c)
    return {"fullHealth": {"state": "skipped", **{key: skip[key] for key in ("posture", "source", "reason", "head")}}} if skip else {}


def kept_close(c, closed: dict | None) -> dict | None:
    """closed.json, when it is this close's: it names the closing entry, or is an older one that names none."""
    kept = load_json(lead_file(c, "closed.json"))
    if not kept:
        return None
    return kept if kept.get("entry") is None or (closed is not None and kept["entry"] == closed["id"]) else None


def finish_close(c, closed: dict, archive: Path) -> list[str]:
    """The close's finished mark, once its archive has landed: in the live record, and in the archived copy and its
    views. Writing it again changes nothing. A record with no started mark for this close, journaled by a tool that
    wrote no marks, closes as before: unmarked."""
    if close_progress(load_state()) is None:
        return []
    state_tool("close-finished", "--entry", str(closed["id"]))
    copy = archive / c.state.name
    if not (copy / "state.json").exists():
        return ["marked the close finished in the record"]
    tool = c.release / "skills/orchestrate-work/scripts/delivery_state.py"
    run([sys.executable, "-B", str(tool), "--dir", str(copy), "close-finished", "--entry", str(closed["id"])])
    config = binding_state(c)
    if config is not None:
        script = c.release / "skills/orchestrate-work/scripts/render_state.py"
        run([sys.executable, "-B", str(script), "--dir", str(copy), "--state-config",
             json.dumps({**{key: value for key, value in config.items() if key != "liveHome"}, "publishers": []})],
            cwd=c.root, check=False, timeout=300)
    return ["marked the close finished in the record and its archive"]


def archive_step(c, closed: dict) -> tuple[Path, list[str]]:
    """Release every lease the lead holds, archive the record, and fast-forward the operator's checkout when it is
    clean and on the final base. Done once for each close: a resumed close-out finds its archive."""
    marker = lead_file(c, "closed.json")
    kept = kept_close(c, closed)
    if kept:
        return Path(kept["archive"]), kept["said"]
    held: list[dict] = []

    def release(doc: dict) -> dict | None:
        leases = doc["state"]["leases"]
        held[:] = [lease for lease in leases if lease.get("holder") == "lead"]
        return {"leases": [lease for lease in leases if lease.get("holder") != "lead"]} if held else None
    said = []
    if patch_from(release, "the lead's released leases") is not None:
        said.append(f"released the lead's leases: {', '.join(str(lease.get('id')) for lease in held)}")
    archive, archived = archive_delivery(c)
    said += archived
    operator = c.main
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=operator, check=False)
    dirty = git("status", "--porcelain", cwd=operator, check=False)
    if branch == c.base_branch and not dirty:
        moved = run(["git", "merge", "--ff-only", "-q", c.base_ref], cwd=operator, check=False)
        said.append(f"fast-forwarded {operator} to {c.base_ref}" if moved.returncode == 0
                    else f"left {operator} where it is: it can't fast-forward to {c.base_ref}")
    else:
        said.append(f"left {operator} where it is: " + ("it has uncommitted changes" if dirty else f"it is on {branch}"))
    marker.write_text(json.dumps({"archive": str(archive), "said": said, "at": now(), "entry": closed["id"],
                                  **closed_full_health(c)}, indent=2) + "\n")
    return archive, said


def friction_step(c, archive: Path, said: list[str], shown: list[str]) -> Path:
    """The friction note stub beside the archive, for the lead's compound-learning pass."""
    note = archive.parent / f"{archive.name}.friction.md"
    if note.exists():
        return note
    lines = [f"# {c.delivery}: friction", "", "Written by the supervisor at close-out. Add what was hard and why, then "
             "run compound-learning on it.", "", "## Shown, never holding", ""]
    lines += [f"- {text}" for text in shown] or ["- (none)"]
    lines += ["", "## What the close-out left to you", ""]
    lines += [f"- {text}" for text in said if text.startswith("left ")]
    lines += [f"- Remove the lead's checkout {c.lane_checkout(c.delivery)} once you no longer need it.",
              "- Archive the lane sessions with your host's own tools.", ""]
    note.write_text("\n".join(lines))
    return note


def close_out(c, flapping: tuple[str, ...] = (), wait: bool = False) -> tuple[list[str], bool]:
    """C6, in order. Returns (what it did, whether the delivery is closed and the supervisor may stop)."""
    said = []
    for step in (lambda c: full_health_step(c, wait), lambda c: followups_step(c, load_state()), closed_step):
        ok, line = step(c)
        said.append(line)
        if not ok:
            return said, False
    closed = latest_close(load_state()["journal"])
    archive, archived = archive_step(c, closed)
    said += archived
    said += finish_close(c, closed, archive)
    # Read once the steps above ran, so a filing the publisher refused for good during them is listed.
    shown = [item["text"] for item in needs_you(c, load_state(), flapping, closing=True) if not item["holding"]]
    note = friction_step(c, archive, archived, shown)
    marker = lead_file(c, "closed.json")
    marker.write_text(json.dumps({**load_json(marker), "friction": str(note)}, indent=2) + "\n")
    said.append(f"wrote the friction note stub {note}: write it up, and archive the lane sessions")
    return said, True


class Supervisor:
    """lead-run: one process per record. It holds the flow log's supervisor lock, recording its pid, start time,
    children and the live view's address; keeps `serve` and the live view running; and runs one pass after another
    until the delivery closes or lead-close asks it to stop."""

    def __init__(self, c, once: bool):
        self.c, self.once = c, once
        self.children: dict[str, subprocess.Popen] = {}
        self.restarts: dict[str, list[float]] = {}
        self.address: str | None = None
        self.handle = None
        self.started = now()
        # Why it stopped serving, once what it serves is gone: then it exits instead of passing on over nothing.
        self.lost: str | None = None

    def gone(self) -> str | None:
        """What it serves that is no longer there: its record (the record's state file, which the state tool only ever
        replaces whole, so a folder a removal left behind, or one a writer made again, doesn't count), or the install
        it runs from (this file, inside the generation)."""
        if not (self.c.state / "state.json").is_file():
            return f"its record {self.c.state} is gone"
        if not Path(__file__).exists():
            return f"its install {GENERATION} is gone"
        return None

    def lose(self) -> bool:
        """Whether it has lost what it serves; the first time, it says so in its log, which stays open."""
        if self.lost is None:
            self.lost = self.gone()
            if self.lost:
                print(f"{now()} supervisor: stopping: {self.lost}", flush=True)
        return self.lost is not None

    def write_lock(self) -> None:
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps({"pid": os.getpid(), "startedAt": self.started, "address": self.address,
                                      "children": [{"name": name, "pid": proc.pid} for name, proc in self.children.items()]}))
        self.handle.flush()

    def child_argv(self, name: str) -> list[str] | None:
        c = self.c
        scripts = c.release / "skills/orchestrate-work/scripts"
        config = binding_state(c)
        if name == "live":
            return [sys.executable, "-B", str(scripts / "render_state.py"), "live", "--dir", str(c.state),
                    *(["--state-config", json.dumps(config)] if config is not None else [])]
        if config is None:
            return None  # the queue needs a binding
        return [sys.executable, "-B", str(scripts / "merge_queue.py"), "--repo", str(c.root), "--state", str(c.state),
                "--remote", c.remote, "--base", c.base_branch, "serve", "--poll",
                str(min(30.0, supervisor_pass_seconds(c)))]

    def start(self, name: str) -> None:
        argv = self.child_argv(name)
        if argv is None:
            return
        log = lead_file(self.c, f"supervisor.{name}.log")
        with open(log, "a") as out:
            # In the supervisor's own group, so stopping the group stops them; signals are the defaults it set.
            self.children[name] = subprocess.Popen(argv, cwd=self.c.root, stdin=subprocess.DEVNULL, stdout=out,
                                                   stderr=subprocess.STDOUT)
        self.write_lock()

    def start_live(self) -> None:
        log = lead_file(self.c, "supervisor.live.log")
        offset = log.stat().st_size if log.exists() else 0
        self.start("live")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            for line in log.read_text()[offset:].splitlines():
                with contextlib.suppress(ValueError):
                    said = json.loads(line)
                    if said.get("live") and said.get("ok"):
                        self.address = f"http://{said['live']}/"
                        self.write_lock()
                        return
            if self.children["live"].poll() is not None:
                raise Stop(f"the live view stopped as it started: {log.read_text()[offset:][-400:].strip()}")
            time.sleep(0.1)
        raise Stop(f"the live view didn't start within 60s; its log is {log}")

    def flapping(self) -> tuple[str, ...]:
        horizon = time.monotonic() - CHILD_FLAP_SECONDS
        return tuple(name for name, times in self.restarts.items()
                     if len([t for t in times if t > horizon]) >= CHILD_FLAPS)

    def keep_alive(self) -> None:
        """Restart a child that exited, at most once a pass once it has exited CHILD_FLAPS times in the window: a
        child that won't stay up is shown in "Needs you", and a restart that fails never ends the supervisor."""
        for name, proc in list(self.children.items()):
            if proc.poll() is None:
                continue
            times = self.restarts.setdefault(name, [])
            if name in self.flapping() and times and time.monotonic() - times[-1] < supervisor_pass_seconds(self.c):
                continue
            times.append(time.monotonic())
            try:
                self.start_live() if name == "live" else self.start(name)
            except (Stop, OSError) as error:
                with open(lead_file(self.c, f"supervisor.{name}.log"), "a") as log:
                    log.write(f"{now()} restart failed: {error}\n")

    def stop_asked(self) -> bool:
        return lead_file(self.c, "supervisor.stop").exists()

    def stop_children(self) -> None:
        for proc in self.children.values():
            if proc.poll() is None:
                proc.terminate()
        deadline = time.monotonic() + 10
        for proc in self.children.values():
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def one_pass(self, number: int) -> bool:
        c = self.c
        started = time.monotonic()
        entry: dict = {"pass": number, "startedAt": now()}
        try:
            c.load_delivery(c.delivery)
        except (Stop, ValueError) as error:  # a delivery file saved mid-edit
            entry["delivery"] = f"not reread: {error}"
        derived: dict = {}

        def derive() -> str:
            # After the publishers, so their ledger is current; before the views, so the line map shows this list.
            derived["doc"] = load_state()
            derived["needs"] = needs_you(c, derived["doc"], self.flapping())
            write_needs(c, derived["needs"], number, started)
            return f"{len(derived['needs'])} item(s)"

        earlier = read_log("supervisor", c.delivery)
        remembered = (earlier[-1].get("laneWords") if earlier else None) or {}

        def cleanup() -> list[str]:
            doc = load_state()
            entry["laneWords"] = lane_words(doc, remembered)
            return cleanup_merged(c, doc, entry["laneWords"])

        steps = (("reconcile", reconcile_once), ("cleanup", cleanup), ("fullHealth", lambda: post_merge_step(c, self.once)),
                 ("finish", lambda: journal_finish_met(c, load_state())),
                 ("publish", lambda: render_views(c, only="publishers")), ("needs", derive),
                 ("render", lambda: render_views(c, only="renderers")), ("latest", lambda: str(copy_latest(c))))
        for name, step in steps:
            if self.lose():
                return False
            try:
                entry[name] = step()
            except (Stop, ValueError, OSError, subprocess.SubprocessError) as error:
                entry[name] = f"failed: {error}"
        closed = False
        try:
            doc = derived.get("doc") or load_state()
            needs = derived["needs"] if "needs" in derived else needs_you(c, doc, self.flapping())
            holds = close_holds(c, doc, needs)
            if all(hold.startswith("not cleaned up: ") for hold in holds):
                # A lane may have moved off its story since this pass's cleanup, as its last word: clean up again, so
                # the close never outruns the checkout it leaves, then read the holds again. A checkout this cleanup
                # still can't remove keeps holding the close until a later pass removes it.
                entry["cleanupBeforeClose"] = cleanup()
                holds = close_holds(c, load_state(), needs)
            entry["needsYou"], entry["closeHeldBy"] = needs, holds
            if not holds:
                entry["closeOut"], closed = close_out(c, self.flapping(), self.once)
                render_views(c)
        except (Stop, ValueError, OSError, subprocess.SubprocessError) as error:
            entry["close"] = f"failed: {error}"
        if self.lose():
            return False
        entry["finishedAt"] = now()
        append_log("supervisor", c.delivery, entry)
        return closed

    def run(self) -> None:
        c = self.c
        # Detached by its own session, never by ignoring signals: its children start with the defaults.
        for number in (signal.SIGINT, signal.SIGQUIT):
            signal.signal(number, signal.SIG_DFL)
        lock = supervisor_lock_path(c)
        lock.parent.mkdir(parents=True, exist_ok=True)
        held, recorded = read_lock(lock)
        if held:
            raise Stop(f"a supervisor already runs (pid {recorded.get('pid')}); live view {recorded.get('address')}")
        orphans = [child for child in recorded.get("children") or [] if isinstance(child, dict)
                   and valid_pid(child.get("pid")) and group_of(child["pid"]) == recorded.get("pid")]
        if orphans:
            raise Stop(f"the earlier supervisor (pid {recorded.get('pid')}) left children running; "
                       f"{flow_command()} lead-run --resume stops them and starts it again")
        self.handle = open(lock, "a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise Stop("another supervisor took the lock first")
        lead_file(c, "supervisor.stop").unlink(missing_ok=True)
        try:
            self.write_lock()
            if not self.once:
                self.start_live()
                self.start("serve")
                print(self.address, flush=True)  # the first line: where the delivery is watched
            number = len(read_log("supervisor", c.delivery))
            while True:
                number += 1
                if self.one_pass(number) or self.once or self.stop_asked() or self.lose():
                    return
                deadline = time.monotonic() + supervisor_pass_seconds(c)
                while time.monotonic() < deadline and not self.stop_asked():
                    if self.lose():
                        return
                    self.keep_alive()
                    time.sleep(0.2)
                if self.stop_asked():
                    return
        finally:
            self.stop_children()
            # Drawn and copied once more, so neither the line map nor `latest` keeps the last running list; not when
            # the record or install is gone, which would only write a record back that no one is serving.
            if not self.lost:
                with contextlib.suppress(OSError, Stop, subprocess.SubprocessError):
                    write_needs(c, None, len(read_log("supervisor", c.delivery)), time.monotonic())
                    render_views(c, only="renderers")
                    copy_latest(c)
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()


def lead_run(args) -> None:
    """The delivery's supervisor: lead-init starts it; --resume stops what an earlier one left and starts it again;
    --once runs one pass in the foreground."""
    c = cfg()
    if not (c.state / "state.json").exists():
        raise Stop(f"no record at {c.state}: lead-init starts it, and its supervisor")
    closed = kept_close(c, last_close(load_state()["journal"]))
    if closed and closed.get("by") == "lead-close":
        raise Stop("the delivery is closed: lead-close closed it. " + closed_words(closed))
    if args.resume:
        for line in stop_orphans(c):
            print(line)
        address = start_supervisor(c)
        print(f"supervisor: running; live view {address}")
        return
    Supervisor(c, once=args.once).run()


def lead_resolve(args) -> None:
    """Resolve the blocker a journal entry records, or ask the next pass to rerun a failed fullHealth, with the lead's
    reason. The resolution is the flow log's; the journal says so."""
    c = cfg()
    doc = load_state()
    if args.id == "full-health":
        head = base_head(c)
        last, rerun = full_health_results(c, head)
        if not last or last["result"] != "failed":
            raise Stop(f"fullHealth hasn't failed at the base head {head[:10]}; there is nothing to rerun")
        # Under focused no run starts, so the rerun waits on the base returning to gate.
        when = ("if the base returns to gate" if delivery_posture(c)["posture"] == "focused"
                else "on the supervisor's next pass")
        if rerun:
            raise Stop(f"a rerun of fullHealth at {head[:10]} is already asked for; it runs {when}")
        with plan_lock(c):
            append_full_health(c, {"rerun": True, "head": head, "why": args.why})
        with marked_decision(c) as written:
            written.append(journal_id(state_tool("journal", "--kind", "decision.recorded", "--summary",
                                                 f"fullHealth at {head[:10]} runs again {when}: {args.why}")))
        print(f"asked for a rerun of fullHealth at {head[:10]}, which runs {when}")
        return
    try:
        number = int(args.id)
    except ValueError:
        raise Stop(f"lead-resolve takes a journal id or full-health, not {args.id!r}")
    entry = next((e for e in doc["journal"] if e["id"] == number), None)
    if entry is None or entry["kind"] != "blocker.recorded":
        raise Stop(f"journal entry {number} isn't a blocker.recorded" + (f" ({entry['kind']})" if entry else ""))
    resolutions = {r.get("journal") for r in read_log("resolutions", c.delivery)}
    if blocker_resolved(c, doc, entry, resolutions):
        raise Stop(f"blocker {number} is already resolved")
    append_log("resolutions", c.delivery, {"journal": number, "why": args.why})
    with marked_decision(c) as written:
        written.append(journal_id(state_tool("journal", "--kind", "decision.recorded", "--summary",
                                             f"Blocker {number} resolved: {args.why}",
                                             *(["--subject", entry["subject"]] if entry.get("subject") else []))))
    print(f"resolved blocker {number}")


def requeue(c, key: str) -> None:
    """Put a stopped candidate the lead has resolved back to queued at its handed head, with no new hand-off. The
    stage is the runner's, so the write is the runner's, exactly as the queue puts back what it takes again."""
    found: list[dict] = []

    def back_to_queued(doc: dict) -> list[str]:
        # Checked against each fresh read: a write in between may have taken the candidate past stopped.
        item = next((q for q in doc["state"]["queue"] if q["subject"] == key), None)
        if item is None or item["stage"] != "stopped":
            raise Stop(f"{key} isn't stopped ({item['stage'] if item else 'not queued'}); --requeue takes back a "
                       "stopped candidate")
        if item.get("pending"):
            raise Stop(f"{key}'s hand-off still waits for acceptance; accept it instead")
        if remote_head(c.branch(key)) not in (item["head"], item.get("syncedHead")):
            raise Stop(f"{c.remote}/{c.branch(key)} moved from the handed head {item['head'][:10]}"
                       + (f" and the queue's synced head {item['syncedHead'][:10]}" if item.get("syncedHead") else "")
                       + ": the lane hands it off again")
        found[:] = [item]
        return ["--element", f"queue[subject={key}]", "--set", json.dumps({"/stage": "queued", "/checks": []})]
    patch_from(back_to_queued, f"{key}'s re-queue", writer="runner")
    item = found[0]
    state_tool("journal", "--kind", "decision.recorded", "--subject", key, "--summary",
               f"{key} re-queued at {item['head'][:10]} once its stop was resolved, with no new hand-off")
    print(f"re-queued {key}; the runner takes it again")



def apply_patch(diff: str, cwd: Path) -> subprocess.CompletedProcess:
    """Apply a diff exactly as git printed it: stripping its trailing newline corrupts a final blank context line."""
    return subprocess.run(["git", "apply", "--3way", "--index"], cwd=cwd, input=diff, text=True, capture_output=True)


def ship(args) -> None:
    c = cfg()
    if not c.ship:
        raise Stop(f"{c.delivery_where} declares no `ship`")
    s = c.ship
    git("fetch", "-q", c.remote)
    branch = expand(s.get("branch", "ship/{delivery}"), delivery=c.delivery)
    checkouts = Path(expand(s.get("checkout", str(Path(c.lane_pattern).parent / "{branch}")), branch=branch.replace("/", "-")))
    path = c.main / checkouts
    if path.exists() or git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
        raise Stop(f"{path} or the branch {branch} already exists; remove both first if you mean to rebuild")
    if run(["git", "merge-base", "--is-ancestor", s["toolingCommit"], c.base_ref], check=False).returncode != 0:
        raise Stop(f"the tooling commit {s['toolingCommit']} is not an ancestor of {c.base_ref}")
    diff, tooling = ship_diff(c, s)
    if not diff:
        raise Stop(f"nothing under {s['paths']} to ship")
    git("worktree", "add", "-q", "--no-track", "-b", branch, str(path), s["from"])
    applied = apply_patch(diff, path)
    files = git("diff", "--cached", "--name-only", cwd=path).splitlines()
    outside = [f for f in files if not any(f == p.rstrip("/") or f.startswith(p.rstrip("/") + "/") or p in (".", "./")
                                           for p in s["paths"])]
    print(f"{branch} at {path}, from {s['from']} ({git('rev-parse', '--short', s['from'])})")
    print(f"  left out the delivery's tooling: {', '.join(tooling[:10])}" + (f" and {len(tooling) - 10} more" if len(tooling) > 10 else ""))
    # A tooling file the delivery changed again later is left out too: name every one, so a product change to a file
    # the tooling commit also touched can't go missing unnoticed.
    committed = set(git("diff", "--name-only", git("merge-base", s["from"], s["toolingCommit"]), s["toolingCommit"]).splitlines())
    later = sorted(f for f in git("diff", "--name-only", s["toolingCommit"], c.base_ref, "--", *s["paths"]).splitlines()
                   if f in committed and not f.startswith((".agents/", ".agent-skills/")))
    if later:
        print(f"  changed after the tooling commit and left out: {', '.join(later)}; a product change there doesn't ship")
    print(f"  {len(files)} file(s) staged; outside {s['paths']}: {outside or 'none'}")
    if applied.returncode == 0 and not files:  # a clean apply that changed nothing is no product branch
        git("worktree", "remove", "--force", str(path))
        git("branch", "-D", branch)
        raise Stop(f"nothing to ship: {s['from']} already has every change the delivery made under {s['paths']}; "
                   f"removed {path} and {branch}")
    if applied.returncode != 0:
        unmerged = git("diff", "--name-only", "--diff-filter=U", cwd=path)
        raise Stop("git apply failed" + (f"; resolve the conflicts in {path}: {unmerged}" if unmerged else f": {applied.stderr.strip()}"))
    if outside:
        raise Stop("files outside the ship paths were staged; this must not happen")
    # The lead's own integration is reviewed like a lane's unit before it reaches the default branch: the
    # orchestration guide's close-out, "Review the integration".
    review = ("then review the integration with `$review-work` before it reaches the default branch, as the "
              "orchestration guide's close-out says")
    if args.no_gates or not c.gates:
        print(f"  next: prepare the checkout, run the gates there, then commit as one change, {review}. "
              "Its PR waits for the user.")
        return
    ship_gates(c, path, s)
    print(f"  next: commit the staged change as one commit, {review}. Its PR waits for the user.")


def ship_diff(c, s: dict) -> tuple[str, list[str]]:
    """The delivery's product changes under the ship paths, as one diff to apply onto `ship.from`, and the tooling it
    leaves out. The diff starts where the base and `ship.from` last met, so changes the base merged from the default
    branch aren't shipped again. Tooling never ships, whenever it changed: every file the tooling commits touched
    (from that meeting point to `ship.toolingCommit`), run-delivery's own `.agents/` settings, the installed generation
    and `ship.gateTooling`."""
    since = git("merge-base", s["from"], c.base_ref)
    fork = git("merge-base", s["from"], s["toolingCommit"])
    tooling = sorted(set(git("diff", "--name-only", fork, s["toolingCommit"]).splitlines())
                     | {".agents", ".agent-skills", *s.get("gateTooling", [])})
    left_out = [f":(top,exclude,literal){rel.rstrip('/')}" for rel in tooling]
    diff = run(["git", "diff", "--binary", since, c.base_ref, "--", *s["paths"], *left_out]).stdout  # unstripped
    return diff, tooling


def ship_tooling(c, path: Path, s: dict) -> list[str]:
    """The delivery base's gate tooling that the ship tree lacks: for each file a gate or prepare step names that only
    the base has, every file beside it that only the base has (a gate script's helpers), plus `ship.gateTooling`.
    The ship's own files are never replaced, so the gates check what ships with the delivery's harness."""
    def base_files(spec: str, recursive: bool) -> list[tuple[str, str]]:
        out = git("ls-tree", *(["-r"] if recursive else []), c.base_ref, "--", spec, check=False)
        return [(line.split()[0], line.split("\t", 1)[1]) for line in out.splitlines() if line.split()[1] == "blob"]

    named = set()
    for argv in [g["argv"] for g in c.gates] + prepare_commands():
        for arg in argv:
            for token in shlex.split(arg) if " " in arg else [arg]:
                if "/" in token and not token.startswith(("-", "/", "{")) and not (path / token).exists():
                    named.add(token)
    wanted = {}
    for token in named:
        if base_files(token, False):
            parent = str(Path(token).parent)
            wanted.update({p: m for m, p in base_files(parent + "/", False)} if parent != "." else {})
            wanted.update({p: m for m, p in base_files(token, False)})
    for spec in s.get("gateTooling", []):
        wanted.update({p: m for m, p in base_files(spec, True)})
    copied = []
    for rel, mode in sorted(wanted.items()):
        target = path / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.run(["git", "show", f"{c.base_ref}:{rel}"], cwd=c.main, capture_output=True,
                                          check=True).stdout)
        target.chmod(0o755 if mode == "100755" else 0o644)
        copied.append(rel)
    return copied


def ship_gates(c, path: Path, s: dict) -> None:
    """Prepares the ship checkout and runs the delivery's gates in it, with `{base}` as the ship's own base. The base's
    gate tooling is lent for the run and removed after, so nothing but the staged change is left to commit."""
    copied = ship_tooling(c, path, s)
    if copied:
        print(f"  lent the base's gate tooling for the run: {', '.join(copied)}")
    results, passed = [], True
    try:
        for argv in prepare_commands():
            code, out = run_group(argv, path, 1800)
            if code != 0:
                raise Stop(f"prepare {' '.join(argv)} failed ({code}): {out.strip()[-300:]}")
        for g in c.gates:
            argv = expand_argv(g["argv"], base=s["from"], key=c.delivery, delivery=c.delivery)
            started = time.time()
            print(f"  gate {g['id']}: {' '.join(shlex.quote(a) for a in argv)}", flush=True)
            code, out = run_group(argv, path / g.get("cwd", "."), g.get("timeoutSeconds", 1800))
            seconds = round(time.time() - started)
            tail = out.strip().splitlines()[-15:]
            results.append({"id": g["id"], "exit": code, "seconds": seconds, "tail": tail})
            print(f"    {'ok' if code == 0 else 'FAILED'} in {seconds}s" + ("" if code == 0 else "\n      " + "\n      ".join(tail)))
            if code != 0:
                passed = False
                break
    finally:
        for rel in copied:
            (path / rel).unlink(missing_ok=True)
    staged = git("diff", "--cached", "--binary", cwd=path)
    append_log("ship-gates", c.delivery, {"from": git("rev-parse", s["from"]), "base": git("rev-parse", c.base_ref),
                                          "staged": hashlib.sha256(staged.encode()).hexdigest(), "passed": passed,
                                          "results": results, "lent": copied})
    if not passed:
        raise Stop(f"a gate failed in {path}; fix it on the delivery base and rebuild the ship branch")
    print(f"  gates passed in {path}")


def show_config(args) -> None:
    """Every resolved setting and the file it came from."""
    c = Config(repo(), DELIVERY_ARG, need_delivery=False, tolerant=True)
    rows = [
        ("remote", c.remote), ("workDirectory", c.work), ("stateDirectory (in the lead's checkout)", c.state_rel),
        ("branchPattern", c.branch_pattern), ("laneCheckout", c.lane_pattern), ("deliveries", c.deliveries),
        ("tracker.issueUrl", c.issue_url), ("forge", json.dumps(c.forge)), ("review.bound", c.bound),
        ("review.lenses", ", ".join(f"{k} ({v[1]})" for k, v in c.lenses.items())), ("review.prepare", json.dumps(c.prepare)),
        ("gates", "; ".join(f"{g['id']}: {' '.join(g['argv'])}" for g in c.gates)),
        ("neverTouch", ", ".join(c.never_touch)), ("inApp", "declared" if c.in_app else "none"),
    ]
    keys = ["remote", "workDirectory", "stateDirectory", "branchPattern", "laneCheckout", "deliveries", "tracker.issueUrl",
            "forge", "review.bound", "review.lenses", "review.prepare", "gates", "neverTouch", "inApp"]
    if c.delivery:
        rows += [("delivery", c.delivery), ("baseBranch", c.base_branch), ("state", c.state),
                 ("stories", len(c.stories)), ("lanes", len(c.lanes))]
        keys += ["delivery", "baseBranch", "state", "stories", "lanes"]
    for (label, value), key in zip(rows, keys):
        source = c.sources.get(key, c.delivery_path.name if c.delivery and key in ("delivery", "stories", "lanes") else "")
        print(f"{label:42} {value}\n{'':42} <- {source}")
    print(f"\nharness config: {c.harness_note}")


def adapter_test(args) -> None:
    """Prove the repository's in-app serve and check adapters keep the contract, on a fixture page: no app needed."""
    c = Config(repo(), need_delivery=False)
    global CFG
    CFG = c
    ia = need_in_app()
    lease = Lease(Path(expand(ia["lock"])), ia["port"], "adapter-test", ia.get("lockWaitSeconds", 480))
    failures = adapter_contract(ia, lease, print)
    if failures:
        raise Stop("the in-app adapters break the contract:\n  " + "\n  ".join(failures))
    print("the in-app adapters keep the contract")


def adapter_contract(ia: dict, lease: "Lease", say) -> list[str]:
    """Run the fixture page's cases through the serve and check adapters under a lease already held, then release it.
    Returns one line per broken case."""
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        shutil.copytree(TOOL / "assets/adapter-fixture", site)
        write_stamp(site, "f" * 40, "e" * 40)
        server = None
        failures = []
        try:
            if port_open(ia["port"]):
                raise Stop(port_busy(ia["port"]))
            server = start_serve(ia, site, ia["port"], lease)
            busy = '[aria-busy="true"]'
            loaded = "document.querySelector('#state').textContent === 'loaded'"
            hold = {"holds": ["/api/one$"], "fails": [], "clicks": [], "for": busy, "wait": 300,
                    "evals": ['!!document.querySelector(\'[aria-busy="true"]\')'], "afterEvals": [loaded], "afterFor": "#state"}
            # (name, mode, spec, adapter args, expected verdict, extra checks on the report)
            cases = [
                ("hold", "hold", {**hold, "path": "/"}, ["--fail", "/api/one$"], True,
                 lambda rep: [] if not rep.get("failed") else ["an adapter --fail after -- overrode the spec"]),
                ("hold-refetch", "hold", {**hold, "path": "/?refetch"}, [], True,
                 lambda rep: [] if any(e.get("afterRelease") for e in rep.get("held", []))
                 else ["a request after the release wasn't reported with afterRelease"]),
                ("hold-flash", "hold", {**hold, "path": "/?flash", "afterEvals": [loaded],
                                        "evals": ['!!document.querySelector(\'[aria-busy="true"]\')']}, [], False,
                 lambda rep: [] if "transient" in (rep.get("alertsSeen") or []) else ["a flashed alert wasn't reported in alertsSeen"]),
                ("hold-flash-attr", "hold", {**hold, "path": "/?flash-attr"}, [], False,
                 lambda rep: [] if "became an alert" in (rep.get("alertsSeen") or [])
                 else ["an element that became an alert wasn't reported"]),
                ("hold-flash-text", "hold", {**hold, "path": "/?flash-text"}, [], False,
                 lambda rep: [] if "live text" in (rep.get("alertsSeen") or [])
                 else ["text that appeared in an alert wasn't reported"]),
                ("fail", "fail", {"path": "/", "holds": [], "fails": ["/api/one$"], "clicks": [], "afterEvals": [],
                                  "evals": ["[...document.querySelectorAll('[role=alert]')].some(a => a.textContent === 'failed')"], "wait": 800}, [], True,
                 lambda rep: [] if all(e.get("at") for e in rep.get("failed", [])) and rep.get("failed")
                 else ["failed entries lack their time"]),
                ("loaded", "loaded", {"path": "/", "holds": [], "fails": [], "clicks": [], "afterEvals": [], "wait": 800,
                                      "evals": [loaded]}, [], True, lambda rep: []),
            ]
            for name, mode, spec, extra, expected, more in cases:
                spec_file = Path(tmp) / f"{name}.json"
                spec_file.write_text(json.dumps(spec))
                code, out = run_group(expand_argv(ia["check"]["argv"], spec=str(spec_file), port=str(ia["port"]), args=extra),
                                      repo(), ia.get("checkTimeoutSeconds", 300))
                try:
                    report = json.loads(out[out.index("{"):])
                except ValueError:
                    failures.append(f"{name}: no JSON report ({out.strip()[-200:]})")
                    continue
                verdict = rules(report, mode=mode, spec=spec, pinned=["/api/one$"], build_head="f" * 40,
                                allowed_errors=set(), error_pattern=r"^(?!Failed to load resource)")
                bad = [] if all(verdict.values()) == expected else [
                    f"expected the rules to {'pass' if expected else 'fail'}: " + str([k for k, ok in verdict.items() if not ok])]
                bad += more(report)
                if "alertsSeen" not in report:
                    bad.append("the report has no alertsSeen")
                if any(not isinstance(e.get("result"), bool) for e in report.get("evals", [])):
                    bad.append("eval results aren't JSON booleans")
                say(f"{'ok ' if not bad else 'FAIL'} {name}" + (f": {bad}" if bad else ""))
                if bad:
                    failures.append(f"{name}: {bad} (report: {json.dumps(report)[:600]})")
        finally:
            stop_group(server)
            lease.close()
    return failures




# -- the setup check -----------------------------------------------------------------------------

GUIDE = "the adoption guide, references/adopting.md"
# Filters and the encoder the before/after clips use; drawtext is optional (labels are burned in only where it exists).
VIDEO_FILTERS = ("lutyuv", "cropdetect", "crop", "scale", "tpad", "pad", "hstack")


def video_tools_problem() -> str | None:
    """Why video evidence can't be recorded here, as the setup check says it, or None."""
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        return (f"missing: {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed, and video "
                "evidence needs both; fix: install ffmpeg (it brings ffprobe)")
    filters = run(["ffmpeg", "-hide_banner", "-filters"], check=False).stdout
    names = {line.split()[1] for line in filters.splitlines() if len(line.split()) > 2 and line.startswith(" ")}
    lacking = [name for name in VIDEO_FILTERS if name not in names]
    encoders = run(["ffmpeg", "-hide_banner", "-encoders"], check=False).stdout
    if "libx264" not in encoders:
        lacking.append("the libx264 encoder")
    if lacking:
        return (f"wrong: this ffmpeg lacks {', '.join(lacking)}, which video evidence uses; fix: install an ffmpeg "
                "build with them (most distributions' full build has them)")
    return None


def version_of(argv: list[str]) -> tuple[int, ...] | None:
    done = run(argv, check=False)
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", done.stdout or "")
    return tuple(int(n) for n in found.groups() if n is not None) if done.returncode == 0 and found else None


def resolves(argv0: str, root: Path, cwd: str = ".") -> bool:
    """An argv's program: on PATH, or a file in the repository (from the command's own folder)."""
    if not argv0:
        return False
    path = Path(os.path.expanduser(argv0))
    if "/" in argv0:
        return (path if path.is_absolute() else root / cwd / path).exists()
    return shutil.which(argv0) is not None


def writable(folder: Path) -> str | None:
    """Create and remove one temporary file in the folder (or the nearest folder that exists); why not, or None."""
    probe = folder
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        with tempfile.NamedTemporaryFile(dir=probe, prefix=".run-delivery-doctor-"):
            pass
    except OSError as error:
        return f"{folder} isn't writable ({error.strerror})"
    return None


class Doctor:
    """The setup check: one line per check, `ok`, `missing`, `wrong` or `skipped`. A check whose prerequisite failed
    is skipped, naming it, so one missing prerequisite yields one line that is neither ok nor skipped."""

    def __init__(self, args):
        self.args, self.root = args, Path(git("rev-parse", "--show-toplevel", check=False) or os.getcwd())
        self.results: dict[str, tuple[str, str]] = {}
        self.c: Config | None = None
        self.binding: dict | None = None
        self.delivery: Config | None = None

    # (id, label, prerequisites, check); a check returns ("ok" | "missing" | "wrong" | "skipped", detail).
    def rows(self):
        return [
            ("platform", "host and tools", (), self.platform),
            ("binding", "orchestration binding", ("platform",), self.check_binding),
            ("candidate", "merge queue candidate step", ("binding",), self.candidate_step),
            ("config", "configuration", ("platform",), self.config),
            ("generation", "release generation", ("platform",), self.generation),
            ("committed", "release on the delivery base", ("config", "binding", "generation"), self.committed),
            ("gates", "gates", ("config", "binding"), self.gates),
            ("lanes", "lane checkouts ignored", ("config",), self.lanes),
            ("tracker", "tracker issue URL", ("config",), self.tracker),
            ("review-pr", "pipeline PR review", ("config",), self.review_pr),
            ("gh", "gh", ("config",), self.gh),
            ("forge", "forge find", ("config", "gh"), self.forge),
            ("forge-write", "forge write access", ("config",), self.forge_write),
            ("tracker-token", "tracker snapshot token", ("binding",), self.tracker_token),
            ("publishing", "snapshots and publishers", ("config", "binding"), self.publishing),
            ("in-app", "in-app adapters", ("config",), self.in_app),
            ("adapters", "in-app adapter contract", ("in-app",), self.adapters),
            ("video", "video evidence tools", ("config",), self.video),
            ("node", "Node", ("platform",), self.node),
            ("harness", "harness configuration", ("config", "generation", "node"), self.harness),
            ("delivery", "delivery file", ("config", "binding"), self.check_delivery),
            ("plan", "plan chapter", ("delivery",), self.check_plan),
            ("plan-reasons", "added stories' reasons", ("delivery",), self.plan_reasons),
            ("plan-sections", "plan sections", ("delivery",), self.stories_planned),
            ("story-files", "story files", ("plan-sections",), self.story_files),
            ("readiness", "story readiness", ("delivery",), self.story_readiness),
            ("context-sections", "context sections", ("delivery",), self.context_planned),
            ("remote-base", "configuration on the remote base", ("delivery",), self.remote_base),
            ("ready", "pull requests marked ready", ("delivery",), self.ready),
            ("supervisor", "delivery supervisor", ("delivery",), self.supervisor),
            ("base-gates", "gates on the base", ("gates", "committed"), self.base_gates),
        ]

    def run(self) -> bool:
        failed = False
        for row, label, needs, check in self.rows():
            blocked = [n for n in needs if self.results.get(n, ("skipped",))[0] not in ("ok", "skipped-ok")]
            if blocked:
                status, detail = "skipped", f"(needs {', '.join(blocked)})"
            else:
                try:
                    status, detail = check()
                except (Stop, ValueError) as error:  # a refusal, or a file that isn't JSON
                    status, detail = "wrong", f"{error}; fix: see the troubleshooting section of {GUIDE}"
            detail = " ".join(detail.split())  # one line per check
            self.results[row] = (status, detail)
            shown = "skipped" if status == "skipped-ok" else status
            print(f"{shown:8} {label}: {detail}".rstrip())
            failed |= status in ("missing", "wrong")
        return not failed

    # A check that doesn't apply here is skipped-ok: skipped, and no prerequisite of anything.
    @staticmethod
    def not_applicable(why: str) -> tuple[str, str]:
        return "skipped-ok", f"({why})"

    def platform(self):
        problems = []
        git_version = version_of(["git", "--version"]) if shutil.which("git") else None
        if not git_version or git_version < (2, 31):
            problems.append("git 2.31 or newer")
        if sys.version_info < (3, 11):
            problems.append("Python 3.11 or newer")
        problems += [tool for tool in ("ps", "lsof") if not shutil.which(tool)]
        if problems:
            return "missing", f"this host lacks {', '.join(problems)}; fix: install {', '.join(problems)}"
        return "ok", (f"POSIX, git {'.'.join(map(str, git_version))}, Python {sys.version_info.major}."
                      f"{sys.version_info.minor}, ps, lsof")

    def config(self):
        if self.results.get("binding", ("ok",))[0] == "wrong":
            return "skipped", "(needs binding: the configuration reads it)"  # a missing binding is read as empty
        explicit = setting("RUN_DELIVERY_CONFIG")
        if explicit and not Path(explicit).exists():
            return "missing", (f"no {explicit}, which RUN_DELIVERY_CONFIG names; fix: point RUN_DELIVERY_CONFIG at the "
                               "configuration file, or unset it")
        if not explicit and not (self.root / CONFIG).exists() and not (self.root / LEGACY_NAMES[0][0]).exists():
            return "missing", f"no {CONFIG}; fix: write it (see {GUIDE})"
        try:
            self.c = Config(self.root, need_delivery=False, tolerant=True, pick=False)
        except ValueError as error:
            return "wrong", f"the configuration isn't JSON ({error}); fix: correct it"
        except Stop as error:
            return "wrong", f"{error}; fix: correct {CONFIG} (run_delivery.py config shows every source)"
        global CFG
        CFG = self.c
        for old, new in LEGACY_NAMES[1:]:  # set in the environment, whether or not this run consulted it
            if os.environ.get(old) and (old, new) not in LEGACY_USED:
                LEGACY_USED.append((old, new))
        if LEGACY_USED:
            renames = ", ".join(f"{old} -> {new}" for old, new in LEGACY_USED)
            return "wrong", f"earlier names in use: {renames}; fix: rename them (the earlier names still work for now)"
        return "ok", f"{self.c.path} resolves with no conflicts"

    def release(self) -> Path:
        """The generation the checks read: the configured release, or the one this tool runs from."""
        return self.c.release if self.c else GENERATION

    def generation(self):
        path = receipt_path(self.c) if self.c else self.root / RECEIPT
        running = self.release().resolve().name
        problem = generation_problem_at(path, running)
        if problem:
            status, _, rest = problem.partition(": ")
            return status, rest
        receipt = json.loads(path.read_text())
        if receipt.get("generation") != running:
            return "wrong", (f"the running generation {running} is the receipt's previous one, not its current "
                             f"{receipt.get('generation')}; fix: run the tool as {TOOL_PATH}")
        return "ok", f"{running[:12]} is the current generation in {path.name} ({receipt['release']['releaseId']})"

    def check_binding(self):
        path = self.root / ".agents/orchestration.json"
        if not path.exists():
            return "missing", f"no {path.relative_to(self.root)}; fix: write the orchestrate-work binding (see {GUIDE})"
        try:
            binding = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            return "wrong", f"{path.name} isn't JSON ({error}); fix: correct it"
        scripts = str(self.release() / "skills/orchestrate-work/scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        sys.dont_write_bytecode = True  # the release's modules leave no bytecode in the installed generation
        import merge_queue  # noqa: E402  (the installed release's own binding check)
        errors = merge_queue.binding_errors(binding)
        unrunnable = tracker_publisher.settings(self.root, self.c.flow_raw if self.c else None, binding)
        if errors and unrunnable["unrunnable"]:
            # The schema refuses it too, but only this says what replaces it.
            return "wrong", f"{path.name} doesn't validate: {tracker_publisher.refusal(unrunnable)}"
        if errors:
            return "wrong", (f"{path.name} doesn't validate: {'; '.join(errors[:3])}"
                             f"{f' (and {len(errors) - 3} more)' if len(errors) > 3 else ''}; fix: correct it against "
                             "orchestration-binding/1")
        self.binding = binding
        return "ok", f"{path.name} validates against orchestration-binding/1"

    def candidate_step(self):
        import merge_queue  # noqa: E402  (the installed release's own, which check_binding put on the path)
        argv = self.binding["candidate"]
        problems = merge_queue.candidate_problems(argv, lambda relative: (self.root / relative).is_file(),
                                                  os.environ.get("PATH"))
        if problems:
            return "wrong", (f"the binding's candidate step can't run: {'; '.join(problems)}; fix: correct its argv in "
                             ".agents/orchestration.json, then run it once by hand with a branch that exists")
        return "ok", f"{' '.join(argv)} resolves"

    def base(self) -> str | None:
        """The delivery base: the named delivery file's baseBranch, or the binding's base.ref (also when the file
        can't be read, which the delivery row reports)."""
        key = self.args.doctor_delivery or self.args.delivery or setting("RUN_DELIVERY_DELIVERY")
        try:
            d = self.c.shelf.json(f"{self.c.deliveries_rel}/{key}.json") if key else None
        except ValueError:
            d = None
        return (d if isinstance(d, dict) else {}).get("baseBranch") or self.c.binding_base

    def base_ref(self) -> str | None:
        base = self.base()
        return f"{self.c.remote}/{base}" if base else None

    def committed(self):
        ref = self.base_ref()
        if not ref:
            return "wrong", "no delivery base; fix: set base.ref in the binding"
        if not OFFLINE:  # what lanes will check out; --offline reads the local remote-tracking ref instead
            run(["git", "fetch", "-q", self.c.remote, self.base()], cwd=self.root, check=False)
        if run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=self.root, check=False).returncode != 0:
            return "wrong", (f"the delivery base {ref} doesn't exist{' locally' if OFFLINE else ''}; fix: push the "
                             "delivery base")
        shown = run(["git", "show", f"{ref}:.agents/orchestration.json"], cwd=self.root, check=False)
        try:
            theirs = json.loads(shown.stdout) if shown.returncode == 0 else None
        except json.JSONDecodeError:
            theirs = None
        if isinstance(theirs, dict) and isinstance(theirs.get("base"), dict) and not serves(theirs, self.base()):
            # Lanes and the merge queue read the binding from the base itself.
            return "wrong", (f"{ref}'s binding doesn't serve the base {self.base()}, as a base cut before its binding "
                             f"gained bases doesn't; fix: commit the binding with its bases on {self.base()} and push "
                             f"it (see {BASE_MISMATCH})")
        entry = git("ls-tree", ref, ".agent-skills/current", cwd=self.root, check=False)
        fix = f"commit the installed release (.agent-skills) on {ref} and push it, so every lane resolves {TOOL_PATH}"
        if not entry:
            return "wrong", f"{ref} has no .agent-skills/current; fix: {fix}"
        target = git("cat-file", "-p", entry.split()[2], cwd=self.root, check=False)
        if not entry.startswith("120000") or target.startswith("/"):
            return "wrong", f".agent-skills/current on {ref} isn't a link inside the repository; fix: {fix}"
        tool = (Path(".agent-skills") / target / "skills/run-delivery/scripts/run_delivery.py").as_posix()
        if not git("ls-tree", ref, tool, cwd=self.root, check=False):
            return "wrong", f"{ref} doesn't carry {tool}; fix: {fix}"
        running = self.c.release.resolve().name
        if Path(target).name != running and not self.c.release_override:
            return "wrong", f"{ref} carries generation {Path(target).name[:12]}, not the running {running[:12]}; fix: {fix}"
        return "ok", f"{ref} carries the installed generation, so lanes resolve {TOOL_PATH}"

    def gates(self):
        c = self.c
        if not c.gates and c.flow_raw.get("gates") != "none":
            return "wrong", ("no gates are declared; fix: declare the binding's fallback checks, or set "
                             f"\"gates\": \"none\" in {c.name} if the repository has none")
        lost = [f"gate {g['id']} ({g['argv'][0]})" for g in c.gates if not resolves(g["argv"][0], self.root, g.get("cwd", "."))]
        lost += [f"prepare step {argv[0]}" for argv in prepare_commands() if not resolves(argv[0], self.root)]
        if lost:
            return "wrong", f"these don't resolve: {', '.join(lost)}; fix: install them, or correct the binding"
        return "ok", (f"{len(c.gates)} gate(s): {', '.join(g['id'] for g in c.gates)}" if c.gates
                      else f"none ({c.name} says so)")

    def lanes(self):
        c = self.c
        probe = c.lane_checkout("doctor-probe")
        try:
            relative = probe.relative_to(c.main)
        except ValueError:
            return "ok", f"lane checkouts live outside the repository ({c.lane_pattern})"
        if run(["git", "check-ignore", "-q", str(relative)], cwd=c.main, check=False).returncode != 0:
            return "wrong", (f"{relative.parent}/ isn't ignored by git, so lane checkouts would show as untracked; "
                             f"fix: add {relative.parent}/ to .gitignore")
        return "ok", f"{c.lane_pattern} is ignored by git"

    def tracker(self):
        url = self.c.issue_url
        if not url or url == "{key}" or "{key}" not in url:
            return "missing", ("no tracker issue URL; fix: set tracker.issueUrl (with {key}) in "
                               f"{self.c.name} or .agents/delivery-views.json")
        return "ok", f"{url} ({self.c.sources.get('tracker.issueUrl')})"

    def review_pr(self):
        try:
            conf = pr_review_config()
        except Stop as error:
            return "wrong", f"{error}; fix: correct review.pr (see references/config.md)"
        if not conf:
            return self.not_applicable("no review.pr configured")
        if not forge_answers("reviews"):
            return "wrong", (f"review.pr is configured, but the {self.c.forge.get('adapter')} adapter has no `reviews` "
                             "command, so hand-offs would wait on a review that never comes; fix: remove review.pr, "
                             "or use an adapter that answers `reviews`")
        warnings_ = []
        if not conf["legacy"]:
            unsigned = [r for r in conf["reviewers"] if not r.get("signature")]
            alone = [r["id"] for r in unsigned if not r.get("account")]
            if alone:
                warnings_.append(f"{', '.join(alone)} {'is' if len(alone) == 1 else 'are'} matched by display name "
                                 "alone, which another bot posting under that name also matches; fix: give each an "
                                 "`account` or a `signature`")
            for member in ("account", "author"):
                by: dict[str, list[str]] = {}
                for r in unsigned:
                    if r.get(member):
                        by.setdefault(r[member], []).append(r["id"])
                for value, ids in by.items():
                    if len(ids) > 1:
                        warnings_.append(f"{' and '.join(ids)} share the {member} {value!r} with no `signature`, so "
                                         "each matches the other's comments; fix: give each a `signature`")
        if warnings_:
            return "warn", "; ".join(warnings_)
        return "ok", "the forge adapter answers `reviews`"

    def gh(self):
        if self.c.forge.get("argv") or self.c.forge.get("adapter") != "github":
            return self.not_applicable("the GitHub adapter isn't configured")
        if not shutil.which("gh"):
            return "missing", "gh isn't installed, and the GitHub adapter uses it; fix: install gh"
        if OFFLINE:
            return "ok", "gh is installed (its sign-in isn't checked with --offline)"
        if run(["gh", "auth", "status"], check=False).returncode != 0:
            return "wrong", "gh isn't signed in; fix: gh auth login"
        return "ok", "gh is installed and signed in"

    def forge(self):
        f = self.c.forge
        if not f.get("argv") and not f.get("adapter"):
            return self.not_applicable("no forge configured")
        if OFFLINE:
            return "skipped", "(--offline)"
        if not f.get("argv") and f.get("adapter") == "bitbucket":
            name = f.get("tokenEnv")
            if not name:
                return "missing", ("no token variable for the Bitbucket adapter; fix: name it as forge.tokenEnv, or "
                                   "in the binding's forgeSnapshot --token-env")
            if not os.environ.get(name):
                return "missing", f"{name} is unset, and the Bitbucket adapter reads its token from it; fix: export {name}"
        try:
            forge("find", "--source", self.c.branch("doctor-probe"))
        except Stop as error:
            return "wrong", (f"{error}; fix: check the forge adapter's settings and that the forge is reachable, or "
                             "run doctor with --offline")
        return "ok", f"the {f.get('adapter') or 'configured'} adapter answers `find`"

    def forge_write(self):
        f = self.c.forge
        if not f.get("argv") and not f.get("adapter"):
            return self.not_applicable("no forge configured")
        return "skipped-ok", "(not verified: doctor never writes to a forge; open-pr is the first write)"

    def tracker_token(self):
        argv = (((self.binding or {}).get("state") or {}).get("trackerSnapshot") or {}).get("argv") or []
        # Without a readable configuration there is no adapter, only the snapshot's variables.
        adapter = (tracker_publisher.settings(self.root, self.c.flow_raw, self.binding or {}).get("adapter")
                   if self.c else None) or {}
        own = [adapter[field] for field in ("tokenEnv", "userEnv") if adapter.get(field)]
        if not argv and not own:
            return self.not_applicable("the binding declares no tracker snapshot")
        names = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("--token-env", "--user-env")]
        names += [name for name in own if name not in names]
        unset = [name for name in names if not os.environ.get(name)]
        if unset:
            return "missing", (f"{', '.join(unset)} {'is' if len(unset) == 1 else 'are'} unset, and the tracker "
                               f"snapshot or adapter reads {'it' if len(unset) == 1 else 'them'}; fix: export "
                               f"{', '.join(unset)}")
        return "ok", f"{', '.join(names) or 'no variables'} set"

    def publishing(self):
        problems = publishing_problems(self.c, self.binding)
        if problems:
            return "wrong", "; ".join(problems)
        state = self.binding.get("state") or {}
        conf = tracker_publisher.settings(self.root, self.c.flow_raw, self.binding or {})
        updates = {"publisher": f"the {conf['publisher']} tracker publisher posts tracker updates",
                   "manual": f"the lead posts tracker updates by hand ({self.c.name} sets tracker.updates to manual)"}
        said = [*(["a forge snapshot"] if state.get("forgeSnapshot") else []),
                *([updates[conf["mode"]]] if conf["mode"] in updates else [])]
        loose = tracker_publisher.untargeted(conf) if conf["mode"] == "publisher" else []
        if conf["transitions"].get("review"):
            when = conf["transitions"].get("reviewWhen")
            said.append(f"tracker.transitions.reviewWhen is {when}" if when else
                        "tracker.transitions.reviewWhen is unset, so lanes make the review move by hand")
        return "ok", ("; ".join(said) or "the binding has neither a forge snapshot nor a tracker publisher, and needs "
                      "neither") + (f"; these moves name no status in tracker.transitions.targets, so one a person "
                                    f"already made shows as not offered: {', '.join(loose)}" if loose else "")

    def node(self):
        problem = node_problem(self.release())
        if problem:
            return problem
        wanted = (load_json(self.release() / "runtime/runtime.json") or {}).get("nodeMinimum")
        if not wanted:
            return self.not_applicable("the generation carries no runtime")
        return "ok", f"{find_node(tuple(int(n) for n in wanted.split('.')))} meets the runtime's nodeMinimum {wanted}"

    def in_app(self):
        ia = self.c.in_app
        if not ia:
            return self.not_applicable("no in-app checks declared")
        program = lambda part: ((ia.get(part) or {}).get("argv") or [""])[0]
        lost = [f"{part} ({program(part) or 'not declared'})" for part in ("build", "serve", "check")
                if not resolves(program(part), self.root)]
        if lost:
            return "wrong", f"these in-app adapters don't resolve: {', '.join(lost)}; fix: install them, or correct inApp"
        folders = [Path(expand(ia["lock"])).parent] + ([Path(expand(ia["baseline"]))] if ia.get("baseline") else [])
        unwritable = [problem for problem in map(writable, folders) if problem]
        if unwritable:
            return "wrong", f"{'; '.join(unwritable)}; fix: make it writable, or point inApp elsewhere"
        return "ok", "build, serve and check resolve; the lease and baseline folders are writable"

    def adapters(self):
        ia = self.c.in_app
        if not ia:
            return self.not_applicable("no in-app checks declared")
        if not self.args.run_adapters:
            return "skipped", "(needs --run-adapters)"
        try:
            lease = Lease(Path(expand(ia["lock"])), ia["port"], "doctor", 0)
        except Stop as held:
            holder = re.search(r"held \((.*)\); gave up", str(held))
            return "skipped", f"(lease held by {holder.group(1) if holder else 'another holder'})"
        failures = adapter_contract(ia, lease, lambda line: None)
        if failures:
            return "wrong", f"the in-app adapters break the contract: {'; '.join(failures)[:600]}; fix: see {GUIDE}"
        return "ok", "the in-app adapters keep the contract on the fixture page"

    def video(self):
        c = self.c
        def stories(key: str) -> dict:
            try:  # an unreadable delivery file is the delivery check's to report
                return (c.shelf.json(f"{c.deliveries_rel}/{key}.json") or {}).get("stories") or {}
            except ValueError:
                return {}
        marked = [k for key in c.delivery_keys() for k, s in stories(key).items() if s.get("evidence") == "video"]
        if not marked:
            return self.not_applicable("no story is marked for video evidence")
        problem = video_tools_problem()
        if problem:
            status, _, rest = problem.partition(": ")
            return status, rest
        return "ok", f"ffmpeg and ffprobe have what video evidence uses ({', '.join(marked)} marked)"

    def harness(self):
        c = self.c
        if not (self.root / "harness.config.ts").exists():
            return self.not_applicable("no harness.config.ts")
        if c.flow_raw.get("harness") == "ignore":
            return self.not_applicable(f"{c.name} ignores harness.config.ts")
        if not find_node22():
            return "missing", "harness.config.ts needs Node 22.6 or newer to read; fix: install Node 22.6 or newer"
        if not (c.release / "runtime/loader.mjs").exists():
            return "wrong", ("the generation carries no runtime loader (runtime/loader.mjs); fix: install a product "
                             "release, which carries the runtime")
        settings, how = load_harness(self.root, c.release)
        if settings is None:
            return "wrong", f"{how}; fix: correct harness.config.ts"
        return "ok", "Node reads harness.config.ts through the generation's runtime loader"

    def check_delivery(self):
        key = self.args.doctor_delivery or self.args.delivery
        if not key:
            return "skipped", "(needs --delivery)"
        try:
            d = Config(self.root, key, tolerant=True)
        except ValueError as error:
            return "wrong", f"the delivery file isn't JSON ({error}); fix: correct it"
        except Stop as error:
            return "missing" if "is missing" in str(error) else "wrong", f"{error}; fix: see {GUIDE}"
        unassigned = [k for k, s in d.stories.items() if s.get("lane") not in d.lanes]
        if unassigned:
            return "wrong", f"stories without a lane: {', '.join(unassigned)}; fix: give each a lane in `lanes`"
        files = git("ls-tree", "-r", "--name-only", d.base_ref if self.base_ref() else "HEAD", cwd=self.root,
                    check=False).splitlines()
        overlaps = []
        lanes = list(d.lanes.items())
        for i, (a, first) in enumerate(lanes):
            for b, second in lanes[i + 1:]:
                # A pattern one lane owns that the other's patterns also match, or a file at the base both match.
                shared = {g for g in first["owns"] if any(fnmatch.fnmatch(g, h) for h in second["owns"])}
                shared |= {h for h in second["owns"] if any(fnmatch.fnmatch(h, g) for g in first["owns"])}
                shared |= {f for f in files if any(fnmatch.fnmatch(f, g) for g in first["owns"])
                           and any(fnmatch.fnmatch(f, g) for g in second["owns"])}
                if shared:
                    overlaps.append(f"{a} and {b} both own {', '.join(sorted(shared)[:3])}")
        if overlaps:
            return "wrong", f"lanes own the same files: {'; '.join(overlaps)}; fix: make each lane's `owns` disjoint"
        self.delivery = d
        return "ok", f"{key}: {len(d.stories)} stories across {len(d.lanes)} lanes, each owning its own files"

    def check_plan(self):
        d = Config(self.root, self.args.doctor_delivery or self.args.delivery, tolerant=True)
        if not plan_declared(d):
            return self.not_applicable("no criteria declared; the plan chapter is off")
        log = read_plan_log(d) if d.flow_log.exists() else []
        if not any(entry["type"] == "snapshot" for entry in log):
            log = [snapshot_entry(d, late=(d.state / "state.json").exists())]
        plan = build_plan(d, log)
        check_plan(plan, d)
        criteria = sum(len(unit["criteria"]) for unit in plan["units"] if not unit.get("removed"))
        return "ok", (f"{criteria} criteria across {len(d.stories)} stories; the plan they make passes "
                      "delivery-views check-plan")

    def plan_reasons(self):
        """Every story the record holds as an `added` change has its reason, and none is waiting to be registered
        without one."""
        d = self.delivery
        log = read_plan_log(d) if d.flow_log.exists() else []
        whys = {entry["change"] for entry in log if entry["type"] == "why"}
        # A story-level addition: a criterion added to a story carries its criteria, and is the plan chapter's own flag.
        bare = [entry for entry in log if entry["type"] == "change" and entry["kind"] == "added"
                and not entry.get("criteria") and not entry.get("why") and entry["id"] not in whys
                and any(key in d.stories for key in entry["subjects"])]
        waiting = []
        if (d.state / "state.json").exists():
            doc = json.loads(run([sys.executable, "-B", str(d.release / "skills/orchestrate-work/scripts/delivery_state.py"),
                                  "--dir", str(d.state), "show"]).stdout)
            waiting = unreasoned_problems(d, unregistered(d, doc["state"]))
        problems = []
        if bare:
            problems.append("the record holds an `added` change with no reason: "
                            + ", ".join(f"{entry['id']} adds {', '.join(entry['subjects'])}" for entry in bare) + "; fix: "
                            + "; ".join(f"{flow_command()} lead-plan why {entry['id']} --why TEXT" for entry in bare))
        if waiting:
            problems.append(f"{'; '.join(waiting)}, so registration refuses it; fix: give it a `why` in "
                            f"{d.delivery_where}, then commit and push it")
        if problems:
            return "wrong", "; ".join(problems)
        if not log and not waiting and not plan_declared(d):
            return self.not_applicable("no plan chapter, so no story is recorded as an added change")
        return "ok", "every story added to the plan has its reason"

    def stories_planned(self):
        d = self.delivery
        problems = plan_problems(d, list(d.stories))
        return ("wrong", problems[0]) if problems else ("ok", f"each of the {len(d.stories)} stories has a section in {d.plan}")

    def story_files(self):
        d = self.delivery
        problems = story_file_problems(d, list(d.stories), tracked_at_base(d))
        if not problems:
            return "ok", "every file a story's plan section names is its lane's"
        # A warning, not a failure: a plan may move a file between lanes as its stories merge.
        return "warn", ("; ".join(problems) + "; fix: move each to the story's lane's owns before it starts, or plan the "
                        "story on the lane that owns it")

    def story_readiness(self):
        d = self.delivery
        if not tracker_conf(d)["transitions"].get("start"):
            return "ok", "lanes make no start move, so there is no readiness to check"
        if (d.state / "state.json").exists():
            doc = json.loads(run([sys.executable, "-B", str(d.release / "skills/orchestrate-work/scripts/delivery_state.py"),
                                  "--dir", str(d.state), "show"]).stdout)
            waiting = [e["subject"] for e in doc["state"].get("planned", []) if e.get("stage") == "planned"]
        else:  # Before lead-init, every story is still to start.
            waiting = list(d.stories)
        found = {key: story_readiness(d, key) for key in waiting if key in d.stories}
        unready = [line for state, line in found.values() if state == "not-ready"]
        unchecked = [line for state, line in found.values() if state == "not-checked"]
        if unready:
            # A warning: lead-assign refuses these, and --force overrides it.
            return "warn", "; ".join(unready + unchecked) + "; fix: have each item made ready before its lane starts"
        if unchecked:
            return self.not_applicable("; ".join(unchecked))
        return "ok", (f"each of the {len(found)} stories still planned is offered its first start move"
                      if found else "no story is still waiting to start")

    def context_planned(self):
        d = self.delivery
        if not d.context_sections:
            return self.not_applicable("no context.sections declared")
        problems = context_problems(d)
        return ("wrong", problems[0]) if problems else ("ok", f"{d.plan} has each of {', '.join(map(repr, d.context_sections))}")

    def remote_base(self):
        d = self.delivery
        if setting("RUN_DELIVERY_LOCAL") == "1" or setting("RUN_DELIVERY_CONFIG"):
            return self.not_applicable("a local setup: lanes read their own working tree")
        problem = remote_base_problem(d)
        return ("wrong", problem) if problem else ("ok", f"{d.base_ref} carries the configuration and the delivery file")

    def ready(self):
        problem = ready_problem(self.delivery)
        if problem:
            return "wrong", problem
        if not self.delivery.forge.get("mergeNeedsReady"):
            return self.not_applicable("the merge step takes a draft (no forge.mergeNeedsReady)")
        return "ok", "lead-accept marks each draft ready under the readyPullRequests grant"

    def supervisor(self):
        d = self.delivery
        if not (d.state / "state.json").exists():
            return self.not_applicable("no record yet: lead-init starts it, and its supervisor")
        held, recorded = read_lock(supervisor_lock_path(d))
        if not held:
            said = closed_status(d)
            if said is not None:
                return ("wrong" if said.startswith("stopped partway") else "ok"), said
            return "wrong", f"the supervisor is down; fix: {flow_command()} lead-run --resume"
        if not Path("/proc/self/status").exists():
            return "ok", (f"running (pid {recorded.get('pid')}); live view {recorded.get('address')}. Its children's "
                          "signal dispositions can't be read on this host (no /proc); it starts them with the defaults"
                          + waits_on(d))
        ignored = []
        for child in recorded.get("children") or []:
            status = Path(f"/proc/{child.get('pid')}/status")
            mask = next((int(line.split()[1], 16) for line in status.read_text().splitlines()
                         if line.startswith("SigIgn:")), 0) if status.exists() else 0
            names = [name for name, number in (("SIGINT", 2), ("SIGHUP", 1), ("SIGQUIT", 3)) if mask >> (number - 1) & 1]
            if names:
                ignored.append(f"{child.get('name')} (pid {child.get('pid')}) ignores {', '.join(names)}")
        if ignored:
            return "wrong", (f"the supervisor's children don't have the default signal dispositions: "
                             f"{'; '.join(ignored)}, so their checks fail as a broken base; fix: lead-close or stop "
                             f"them, then {flow_command()} lead-run --resume")
        return "ok", f"running (pid {recorded.get('pid')}); live view {recorded.get('address')}" + waits_on(d)

    def base_gates(self):
        if not self.args.run_gates:
            return "skipped", "(needs --run-gates)"
        ref = self.base_ref()
        checkout = Path(tempfile.mkdtemp(prefix="run-delivery-doctor-")) / "base"
        try:
            git("worktree", "add", "--detach", "-q", str(checkout), ref, cwd=self.root)
            for argv in prepare_commands():
                run(argv, cwd=checkout)
            for g in self.c.gates:
                argv = expand_argv(g["argv"], base=ref, key="doctor", delivery=self.c.delivery or "")
                code, out = run_group(argv, checkout / g.get("cwd", "."), int(g.get("timeoutSeconds", 1800)))
                if code != 0:
                    return "wrong", f"gate {g['id']} fails on {ref}: {out.strip()[-300:]}; fix: repair the base first"
        finally:
            run(["git", "worktree", "remove", "--force", str(checkout)], cwd=self.root, check=False)
            shutil.rmtree(checkout.parent, ignore_errors=True)
        return "ok", f"every gate passes on {ref}, in a fresh prepared checkout (removed)"


def doctor(args) -> None:
    """Check this repository's setup for a delivery: every missing prerequisite, and how to fix it."""
    global OFFLINE
    OFFLINE = args.offline
    sys.exit(0 if Doctor(args).run() else 1)


# -- entry ---------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    global CFG, DELIVERY_ARG
    # Each call resolves its own configuration: none an earlier call in this process left, for another repository or
    # delivery, is this one's.
    CFG = None
    parser = argparse.ArgumentParser(prog="run_delivery.py", description=__doc__.split("\n\n")[0], allow_abbrev=False)
    parser.add_argument("--delivery", help="the delivery key, when the repository has several")
    sub = parser.add_subparsers(dest="command", required=True)
    # Every command but doctor, whose own --delivery adds the delivery's checks, takes --delivery after its name too.
    after = argparse.ArgumentParser(add_help=False)
    after.add_argument("--delivery", dest="delivery_after", default=argparse.SUPPRESS,
                       help="the delivery key, when the repository has several")
    command = lambda group, name, **kw: group.add_parser(name, parents=[after], **kw)
    p = command(sub, "review-prepare"); p.add_argument("key"); p.add_argument("--round", type=int, required=True)
    p.add_argument("--grace", action="store_true"); p.add_argument("--restart", action="store_true")
    p.set_defaults(fn=review_prepare)
    p = command(sub, "review-reduce"); p.add_argument("key"); p.add_argument("--round", type=int, required=True)
    p.set_defaults(fn=review_reduce)
    p = command(sub, "plan-review", help="review the plan before it is approved: prepare, reduce or end a round")
    review_commands = p.add_subparsers(dest="action", required=True)
    plan_help = "review this plan in its own store, keyed by its reviewSlug, with no delivery"
    q = command(review_commands, "prepare", help="the next round's checkouts and briefs, one per lens")
    q.add_argument("--restart", action="store_true", help="discard the prepared round's results and prepare it again")
    q.add_argument("--plan", help=plan_help)
    q.add_argument("--lenses", help="with --plan: the comma-separated lenses, fixed at round 1 (later rounds keep them)")
    q.set_defaults(fn=plan_review_prepare)
    q = command(review_commands, "reduce", help="check the lenses' results and record the round")
    q.add_argument("--plan", help=plan_help); q.set_defaults(fn=plan_review_reduce)
    q = command(review_commands, "end", help="end a review that isn't converging, with the operator's reason")
    q.add_argument("--plan", help=plan_help)
    q.add_argument("--reason", required=True); q.set_defaults(fn=plan_review_end)
    q = command(review_commands, "check", help="validate plan-review-evidence/1 records, hand-written or written by a review")
    q.add_argument("records", nargs="+", metavar="RECORD"); q.set_defaults(fn=plan_review_check)
    p = command(sub, "gate"); p.add_argument("key"); p.set_defaults(fn=gate)
    p = command(sub, "build"); p.add_argument("key", nargs="?"); p.set_defaults(fn=build)
    p = command(sub, "app-check"); p.add_argument("key"); p.add_argument("name")
    p.add_argument("--mode", choices=("hold", "fail", "loaded"), required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--hold", action="append", default=[]); p.add_argument("--fail", action="append", default=[])
    p.add_argument("--click", action="append", default=[]); p.add_argument("--eval", action="append", default=[])
    p.add_argument("--after-eval", action="append", default=[]); p.add_argument("--for", dest="for_")
    p.add_argument("--wait", type=int); p.add_argument("--after-for")
    p.add_argument("--baseline", action="store_true"); p.add_argument("--probe", action="store_true")
    p.add_argument("--lock-wait", type=int)
    p.add_argument("passthrough", nargs="*", help="after --: extra arguments for the check adapter")
    p.set_defaults(fn=app_check)
    p = command(sub, "lane-start", help="begin a story: its checkout from the base, prepared, the focus and its start")
    p.add_argument("key"); p.add_argument("--base", help="the base to start from (default: the delivery's base)")
    p.set_defaults(fn=lane_start)
    p = command(sub, "push"); p.add_argument("key"); p.set_defaults(fn=push)
    p = command(sub, "app-video"); p.add_argument("key"); p.add_argument("name")
    p.add_argument("--mode", choices=["hold", "fail", "loaded"], required=True); p.add_argument("--path", required=True)
    p.add_argument("--hold", action="append", default=[]); p.add_argument("--fail", action="append", default=[])
    p.add_argument("--click", action="append", default=[]); p.add_argument("--for", dest="for_")
    p.add_argument("--wait", type=int, default=2500); p.add_argument("--after-for"); p.add_argument("--settle", type=int, default=2500)
    p.add_argument("--caption"); p.add_argument("--after-build", help="a stamped build to record as 'after' (the lead's base build)")
    p.add_argument("--lock-wait", type=int)
    p.add_argument("passthrough", nargs="*", help="after --: extra arguments for the check adapter")
    p.set_defaults(fn=app_video)
    p = command(sub, "open-pr"); p.add_argument("key"); p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True); p.set_defaults(fn=open_pr)
    p = command(sub, "pr-review-current", help="a reviewGate for the merge queue: exit 0 only when review.pr's "
                "review is current and passing at --head")
    p.add_argument("--branch", required=True); p.add_argument("--head", required=True)
    p.add_argument("--wait", action="store_true", help="wait up to review.pr's waitSeconds for the review")
    p.set_defaults(fn=pr_review_current)
    p = command(sub, "pr-review"); p.add_argument("key"); p.add_argument("--wait", action="store_true")
    p.set_defaults(fn=pr_review)
    p = command(sub, "handoff"); p.add_argument("key"); p.add_argument("--pr")
    p.add_argument("--checks", help="the checks the lane ran, by catalogue id, comma-separated: shown in the hand-off "
                   "and the record, and proposed at acceptance")
    p.add_argument("--no-change", action="store_true", help="the story's criteria already hold on the base: hand it "
                   "off with --evidence, no branch, PR or review")
    p.add_argument("--evidence", help="with --no-change: what shows the criteria already hold, as text or @FILE")
    p.add_argument("--pr-review-triage", action="append", help="how a PR review's findings were handled: ID=TEXT "
                   "for each current reviewer (repeat it), or a bare TEXT when exactly one review is current")
    p.add_argument("--findings"); p.add_argument("--blocked", action="append", default=[]); p.set_defaults(fn=handoff)
    p = command(sub, "lead-hints", help="print the delivery's work-hints/1: each story's plan files and its after keys")
    p.set_defaults(fn=lead_hints)
    p = command(sub, "lead-setup"); p.add_argument("key"); p.add_argument("--plan", required=True)
    p.add_argument("--base", help="default: the binding's base.ref"); p.add_argument("--headline")
    p.add_argument("--pattern", help="a regex naming groups key and title for each story heading; default: the plan "
                   "format's `### KEY. Title` under `## Stories`")
    p.add_argument("--tracker-snapshot"); p.add_argument("--ship-from"); p.add_argument("--ship-path", action="append")
    p.add_argument("--context-section", action="append"); p.add_argument("--force", action="store_true")
    p.add_argument("--approvals-section", help="draft approvals from this plan section's list")
    p.add_argument("--out-of-scope-section", help="draft outOfScope from this plan section's list")
    p.add_argument("--authority", help="the operator's grant: push,draftPullRequests for lanes (either or both), and "
                   "readyPullRequests for the lead")
    p.add_argument("--granted-by", help="who granted --authority"); p.add_argument("--granted-on", help="default: today")
    p.add_argument("--no-authority", action="store_true", help="lanes get no grant, and hand their branches to the lead")
    p.add_argument("--review-when", choices=tracker_publisher.REVIEW_WHEN,
                   help="when lane commands make the review move: recorded as tracker.transitions.reviewWhen")
    p.set_defaults(fn=lead_setup)
    p = command(sub, "lead-init")
    p.add_argument("--replace", action="store_true",
                   help="stop the earlier record's supervisor and archive its record and flow log, then start afresh")
    p.add_argument("--no-supervisor", action="store_true", help="start no supervisor; lead-run --resume starts it later")
    p.set_defaults(fn=lead_init)
    p = command(sub, "lead-run", help="the delivery's supervisor: every mechanical lead duty, pass after pass")
    p.add_argument("--resume", action="store_true", help="stop what an earlier supervisor left, then start it again")
    p.add_argument("--once", action="store_true", help="run one pass in the foreground, then stop")
    p.set_defaults(fn=lead_run)
    p = command(sub, "full-health-run", help="one post-merge fullHealth run at a base head, as the supervisor starts it")
    p.add_argument("--head", required=True); p.add_argument("--record", required=True); p.set_defaults(fn=full_health_run)
    p = command(sub, "lead-brief"); p.set_defaults(fn=lead_brief)
    p = command(sub, "lead-assign"); p.add_argument("keys", nargs="+"); p.add_argument("--lane", required=True)
    p.add_argument("--title", required=True); p.add_argument("--note")
    p.add_argument("--force", action="store_true",
                   help="assign a story whose tracker item doesn't offer the first start move")
    p.set_defaults(fn=lead_assign)
    p = command(sub, "lead-accept"); p.add_argument("key", nargs="?"); p.add_argument("--accept-blocked", action="append")
    p.add_argument("--requeue", metavar="KEY", help="re-queue a stopped candidate you resolved, with no new hand-off")
    p.add_argument("--accept-restarts", action="store_true")
    p.add_argument("--accept-discarded", action="append", type=int, metavar="ROUND")
    p.add_argument("--checks", help="under selection.lead: the candidate's checks, by catalogue id, comma-separated, "
                   "recorded through the state tool's select at the handed head")
    p.add_argument("--full-suite", action="store_true", help="under selection.lead: check on the binding's fallback, "
                   "journalled as the lead's decision")
    p.add_argument("--reason", help="with --checks or --full-suite: why"); p.set_defaults(fn=lead_accept)
    p = command(sub, "lead-carry-review", help="carry a story's captured review to its queued head across commits that "
                "need no new round, showing their diff first")
    p.add_argument("key"); p.add_argument("--from", dest="from_head", required=True, help="the head the evidence covers")
    p.add_argument("--to", dest="to_head", required=True, help="the story's queued head")
    p.add_argument("--reason", required=True, help="why the commits between them need no new round")
    p.set_defaults(fn=lead_carry_review)
    p = command(sub, "lead-resolve", help="resolve a recorded blocker, or rerun a failed fullHealth")
    p.add_argument("id", help="the blocker's journal id, or full-health to rerun a failed fullHealth")
    p.add_argument("--why", required=True)
    p.set_defaults(fn=lead_resolve)
    p = command(sub, "lead-cleanup"); p.add_argument("keys", nargs="*"); p.set_defaults(fn=lead_cleanup)
    p = command(sub, "lead-reconcile", help="one reconcile sweep; the supervisor runs one every pass")
    p.set_defaults(fn=lead_reconcile)
    p = command(sub, "lead-evidence"); p.add_argument("action", choices=["record", "page", "published", "status"])
    p.add_argument("--url", help="published: the page's address")
    p.add_argument("keys", nargs="*"); p.add_argument("--after-build"); p.add_argument("--out", default="evidence-page")
    p.add_argument("--title"); p.add_argument("--intro")
    p.add_argument("--note", action="append", help="KEY=TEXT: what to say for a story without clips; kept in the record "
                   "for later rebuilds, and KEY= removes it")
    p.set_defaults(fn=lead_evidence)
    p = command(sub, "lead-plan", help="record a change to the approved plan, or a reason for one")
    plan_commands = p.add_subparsers(dest="action", required=True)
    q = command(plan_commands, "change", help="edit and commit the delivery file, recording before and after")
    q.add_argument("--kind", required=True, choices=["added", "removed", "relaxed", "changed", "finish"])
    q.add_argument("--subjects", nargs="*", default=[]); q.add_argument("--criterion"); q.add_argument("--text")
    q.add_argument("--finish", help="finish: the line's id"); q.add_argument("--covers", help="finish: its covers, as JSON")
    q.add_argument("--label", help="finish: a new line's label"); q.add_argument("--why", required=True)
    q.add_argument("--by", default="lead"); q.add_argument("--ref"); q.add_argument("--title")
    q.add_argument("--lane", help="added, for a story not yet in the delivery: its lane")
    q.add_argument("--after", nargs="*", help="added, for a story not yet in the delivery: the stories it comes after")
    q.set_defaults(fn=lead_plan)
    q = command(plan_commands, "approval", help="mark one approval done or waiting, and commit the delivery file")
    q.add_argument("id"); q.add_argument("--state", required=True, choices=["done", "waiting"]); q.add_argument("--who")
    q.set_defaults(fn=lead_plan)
    q = command(plan_commands, "why", help="give a recorded change its reason")
    q.add_argument("change"); q.add_argument("--why", required=True); q.set_defaults(fn=lead_plan)
    p = command(sub, "lead-close"); p.set_defaults(fn=lead_close)
    p = command(sub, "view", help="serve the record's live view, or an archived copy's, without a checkout")
    p.add_argument("--archive", type=Path, help="a copy of the record, such as the supervisor's latest")
    p.add_argument("--port", type=int, default=0, help="the port; 0 picks a free one")
    p.set_defaults(fn=view)
    p = command(sub, "ship"); p.add_argument("--no-gates", action="store_true", help="stage only; run the gates yourself")
    p.set_defaults(fn=ship)
    p = command(sub, "adapter-test"); p.set_defaults(fn=adapter_test)
    p = command(sub, "config"); p.set_defaults(fn=show_config)
    p = sub.add_parser("doctor", help="check this repository's setup: every missing prerequisite and its fix")
    p.add_argument("--delivery", dest="doctor_delivery", help="also check this delivery's file")
    p.add_argument("--offline", action="store_true", help="don't fetch the base or ask the forge; read local refs")
    p.add_argument("--run-adapters", action="store_true", help="also run the in-app adapters on the fixture page")
    p.add_argument("--run-gates", action="store_true", help="also run the gates on the base, in a fresh checkout")
    p.set_defaults(fn=doctor)
    # What follows `--` is the adapter's. Python 3.11's argparse refuses it after a positional taking any number of values.
    argv = list(sys.argv[1:] if argv is None else argv)
    separator = argv.index("--") if "--" in argv else len(argv)
    # The command is the first word after the top-level option, which a value elsewhere may spell too.
    first = 2 if argv[:1] == ["--delivery"] else 1 if argv[:1] and argv[0].startswith("--delivery=") else 0
    if argv[first:first + 1] in (["app-check"], ["app-video"]):
        args = parser.parse_args(argv[:separator])
        args.passthrough = [*args.passthrough, *argv[separator + 1:]]
    else:
        args = parser.parse_args(argv)
    later = getattr(args, "delivery_after", None)
    if later and args.delivery and later != args.delivery:
        parser.error(f"--delivery {args.delivery} and --delivery {later} name two deliveries; name one")
    DELIVERY_ARG = args.delivery or later
    for _signal in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(_signal, _terminate)
    try:
        args.fn(args)
    except Stop as error:
        print(f"run-delivery: {error}", file=sys.stderr)
        sys.exit(2)
    if args.command.startswith("lead-") and args.command not in ("lead-setup", "lead-init", "lead-run"):
        with contextlib.suppress(Stop, ValueError, OSError):
            line = supervisor_line(cfg())
            if line:
                print(line)


if __name__ == "__main__":
    main()
