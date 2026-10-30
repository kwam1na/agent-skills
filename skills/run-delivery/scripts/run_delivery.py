#!/usr/bin/env python3
"""run-delivery: the mechanical steps of an agent-skills delivery (orchestrate-work), as commands, for any
repository and any delivery.

Lanes and the lead spend their attention on the work, not on hand-assembling lens briefs, round files, records
and evidence. The tool wraps the repository's installed agent-skills release (its state tool, review validator
and lens charters), found in the installed generation it runs from. It reads the repository's settings from
`.agents/run-delivery.json` and each delivery's from
`.agents/deliveries/<key>.json` (references/config.md). Forges and in-app checks are reached through argv adapters.

Lane:
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
import datetime
import fcntl
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
from pathlib import Path

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
ADAPTER_COMMANDS = {"bitbucket": ("find", "open", "get", "reviews"), "github": ("find", "open", "get")}
DEFAULT_ARCHIVE = "~/.local/share/run-delivery/archive/{repo}/{delivery}"
# Adapter arguments that would carry a sign-in value into records, logs and evidence (flag name or assignment key).
SECRET_ARGUMENT = re.compile(r"password|passwd|token|secret|api[-_]?key|credential", re.IGNORECASE)
SEVERITIES = ("P0", "P1", "P2", "P3")
CLOSED = ("closed", "fixed", "withdrawn", "dropped", "deferred")
CONTEXT_TODO = "TODO: replace this line with your evidence so far"
STAMP = ".flow-build.json"
EVIDENCE_FINISH = {"id": "evidence-page", "text": "The delivery's evidence page is published and current: every merged "
                   "story marked for video has its before/after clip on it, and the epic and stories link to it"}
SPEC_FLAGS = ("--path", "--hold", "--fail", "--eval", "--after-eval", "--click", "--for", "--wait", "--after-for", "--spec")
DEFAULT_TEST_FILES = r"(^|/)(tests?|__tests__)/|\.(spec|test)\.[^/]+$"


class Stop(Exception):
    pass


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


def main_checkout() -> Path:
    return Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).parent


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


def run_group(argv: list[str], cwd: Path, timeout: int, capture: bool = True) -> tuple[int, str]:
    """Run a command in its own process group; on timeout or interruption the whole group is stopped."""
    proc = subprocess.Popen(argv, cwd=cwd, text=True, start_new_session=True,
                            stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if capture else None)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        raise Stop(f"{' '.join(argv[:3])} ... took longer than {timeout}s and was stopped")
    finally:
        stop_group(proc)


# -- configuration ------------------------------------------------------------------------

def load_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


class Shelf:
    """Where the delivery's shared files are read from: the own config, the binding, delivery-views.json, the delivery
    file, the plan and the brief notes. The lead's checkout, on the delivery base, reads its own working tree. A lane
    reads them from the base on the remote, so what the lead pushes reaches every lane without a merge, and a lane
    never has to move its reviewed head to pick up a tooling change."""

    def __init__(self, root: Path, ref: str | None = None):
        self.root, self.ref = root, ref

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
    """The lead (on the base branch) and explicit overrides read the working tree; a lane reads the base ref."""
    binding = load_json(root / ".agents/orchestration.json") or {}
    own = load_json(root / config_rel(Shelf(root))) or {}
    base = binding.get("base", {}).get("ref")
    remote = binding.get("base", {}).get("remote") or own.get("remote") or "origin"
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root, check=False)
    if not base or branch == base or setting("RUN_DELIVERY_LOCAL") == "1":
        return Shelf(root)
    ref = f"{remote}/{base}"
    if not OFFLINE:
        run(["git", "fetch", "-q", remote, base], cwd=root, check=False)
    if run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=root, check=False).returncode != 0:
        return Shelf(root)  # no such ref yet (a fresh repository): nothing shared to read
    return Shelf(root, ref)


def find_node22() -> str | None:
    candidates = [shutil.which("node")] + sorted(str(p) for p in Path.home().glob(".nvm/versions/node/v2*/bin/node"))[::-1]
    for node in filter(None, candidates):
        done = subprocess.run([node, "--version"], text=True, capture_output=True)
        m = re.match(r"v(\d+)\.(\d+)", done.stdout.strip())
        if m and (int(m.group(1)), int(m.group(2))) >= (22, 6):
            return node
    return None


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
    done = subprocess.run([node, "--no-warnings", "--import", register, "-e", script, str(path)],
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
        self.root, self.main = root, main_checkout()
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
        if h and h.get("preparationCommands") and self.gates and self.binding_base:
            base_ref = f"{self.remote}/{self.binding_base}"
            ours = [[base_ref if a == "{base}" else a for a in g["argv"]] for g in self.gates]
            theirs = [list(cmd["command"]) for cmd in h["preparationCommands"]]
            if ours != theirs:
                self.conflicts.append(f"the lane gates {ours} and harness.config.ts preparationCommands {theirs} disagree")
        if h and h.get("baseRef") and self.binding_base and h["baseRef"] != f"{self.remote}/{self.binding_base}":
            self.conflicts.append(f"harness.config.ts baseRef {h['baseRef']} isn't the binding's base "
                                  f"{self.remote}/{self.binding_base}")
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

    def delivery_keys(self) -> list[str]:
        return [name[:-len(".json")] for name in self.shelf.names(self.deliveries_rel, ".json")]

    def pick_delivery(self, required: bool) -> str | None:
        keys = self.delivery_keys()
        if len(keys) == 1:
            return keys[0]
        branch = git("rev-parse", "--abbrev-ref", "HEAD", check=False)
        matches = []
        for key in keys:
            d = self.shelf.json(f"{self.deliveries_rel}/{key}.json") or {}
            base = d.get("baseBranch") or self.binding_base
            if branch == base or any(branch == self.branch_pattern.format(key=k) for k in d.get("stories", {})):
                matches.append(key)
        if len(matches) == 1:
            return matches[0]
        if required:
            raise Stop(f"can't tell which delivery this is (branch {branch}; deliveries: "
                       f"{keys or 'none'}); pass --delivery KEY")
        return None

    def load_delivery(self, key: str) -> None:
        path = self.deliveries / f"{key}.json"
        d = self.shelf.json(f"{self.deliveries_rel}/{key}.json")
        if d is None:
            raise Stop(f"{self.shelf.where(f'{self.deliveries_rel}/{key}.json')} is missing (lead-setup writes it)")
        if d.get("schemaVersion") != "delivery-flow-delivery/1":
            raise Stop(f"{path}: schemaVersion must be delivery-flow-delivery/1")
        self.raw, self.delivery_path = d, path
        self.delivery_where = self.shelf.where(f"{self.deliveries_rel}/{key}.json")
        self.delivery = d["delivery"]
        base = d.get("baseBranch")
        if base and self.binding_base and base != self.binding_base:
            raise Stop(f"{path} names base {base}, but the binding here serves {self.binding_base}")
        self.base_branch = base or self.binding_base
        if not self.base_branch:
            raise Stop(f"no delivery base: the binding has no base.ref and {path} names no baseBranch")
        self.sources["baseBranch"] = f"{path.name}" if base else "orchestration binding (base.ref)"
        self.base_ref = f"{self.remote}/{self.base_branch}"
        if os.environ.get("DELIVERY_STATE"):
            self.state, self.sources["state"] = Path(os.environ["DELIVERY_STATE"]), "DELIVERY_STATE"
        elif d.get("stateDirectory"):
            self.state, self.sources["state"] = self.main / d["stateDirectory"], path.name
        else:
            self.state = self.lane_checkout(key) / self.state_rel
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


def plan_context(key: str) -> str:
    c = cfg()
    text = c.shelf.text(c.plan)
    if text is None:
        raise Stop(f"the plan {c.shelf.where(c.plan)} is missing")
    sections = plan_sections(text)
    own = next((body for _, title, body in sections if re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", title)), None)
    if own is None:
        raise Stop(f"no heading in {c.plan} names {key}")
    names = {key, c.label(key)}
    rows = [line for line in text.splitlines() if line.startswith("|")
            and any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", line) for n in names)]
    parts = [own]
    if rows:
        parts += ["", "Table rows naming this story:", *rows]
    for name in c.context_sections:
        body = next((b for _, title, b in sections if title == name or title.startswith(name)), None)
        parts += ["", body if body else f"(no '{name}' section in the plan)"]
    return "\n".join(parts)


# -- lane: review rounds -------------------------------------------------------------------

def lens_checkout(key: str, round_no: int, short: str) -> Path:
    return repo() / cfg().work / "lens" / f"{key}-r{round_no}-{short}"


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


def seed_context(key: str) -> Path:
    path = review_dir(key) / "context.md"
    if not path.exists():
        c = cfg()
        path.write_text(
            f"# Review context: {key} ({c.label(key)}), part of {c.delivery}\n\n"
            f"Repository: your private checkout; read its root instruction file. Delivery base `{c.base_ref}`; the plan "
            f"is `{c.plan}` (approved, binding). Reference stories, once the lead names them, are in the assignment.\n\n"
            f"## Ticket contract and plan unit\n\n{plan_context(key)}\n\n"
            "## Evidence so far\n\n"
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
                 "- `resolvedFindings`: required. One entry for every finding this lens raised in an earlier round, "
                 '`{"id", "state": "closed|fixed|withdrawn|deferred|open", "resolution": "..."}`. Every carried '
                 "finding must appear; a deferral (P2/P3 only) also carries `deferredIssue`.")
    if round_no <= c.bound:
        bound_text = f"`ordinary counted`: round {round_no} of {c.bound}, counted against the declared bound."
    else:
        bound_text = f"`grace`: round {round_no} with declared bound {c.bound}, outside the count."
    values = {
        "lens": lens, "short": short, "key": key, "round": str(round_no), "candidate": candidate, "tree": tree,
        "releaseId": rel["releaseId"], "profile": str(rel["profile"]), "archiveSha256": rel["archiveSha256"],
        "metadataSha256": rel["metadataSha256"], "graph": graph_digest(), "bound_text": bound_text, "kind": kind,
        "carry": carry, "resolutions": resolutions, "charter": charter, "later": later, "checkout": str(checkout),
        "context": context, "in_app": in_app_summary(key),
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
    context = seed_context(key)
    if CONTEXT_TODO in context.read_text():
        raise Stop(f"fill in 'Evidence so far' in {context} first (the rest is seeded from the plan)")
    candidate = clean_head()
    tree = git("rev-parse", "HEAD^{tree}")
    git("fetch", "-q", c.remote, c.base_branch)
    merge_base = git("merge-base", c.base_ref, "HEAD")
    prior_rounds = [json.loads(p.read_text()) for p in round_files(folder)]
    prior_candidate = prior_rounds[-1]["entries"][0]["binding"]["candidateRef"] if prior_rounds else None
    if prior_candidate == candidate:
        raise Stop("HEAD is the candidate of the previous round; commit the fixes first")
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
        checkout = lens_checkout(key, n, short)
        remove_checkout(checkout)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "--detach", "-q", str(checkout), candidate)
        for argv in prepare_commands():
            run(argv, cwd=checkout)
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
            if f.get("state") == "deferred":
                problems.append(f"finding {f['id']} is {f['severity']} and can't be deferred")
            open_blocking = True
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


def ensure_journaled(key: str, outcome: dict, lane: str) -> None:
    n = outcome["round"]
    if any(e["review"]["round"] == n for e in journal_entries("review.round", key)):
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
        ensure_journaled(key, outcome, lane)
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
    for lens, (short, _) in c.lenses.items():
        remove_checkout(lens_checkout(key, n, short))
    ensure_journaled(key, outcome, lane)
    summarize(key, outcome)


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
    c = cfg()
    key = c.require(args.key)
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
                    raise Stop(f"the in-app lease is held ({self.file.read().strip() or 'unknown holder'}); gave up after {wait}s")
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
    lease = Lease(Path(expand(ia["lock"])), port, owner, lock_wait or ia.get("lockWaitSeconds", 480))
    server = check = None
    spec_file = Path(tempfile.mkstemp(suffix=".json")[1])
    try:
        spec_file.write_text(json.dumps(spec))
        if port_open(port):
            raise Stop(f"something else is listening on {port} (a dev server?); stop it first")
        server = subprocess.Popen(expand_argv(ia["serve"]["argv"], dir=str(dist), port=str(port)),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        lease.track(server.pid)
        for _ in range(100):
            if port_open(port) or server.poll() is not None:
                break
            time.sleep(0.2)
        if not port_open(port):
            raise Stop("the serve command didn't start listening")
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
        lease.close()
        spec_file.unlink(missing_ok=True)
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
    passthrough = args.passthrough[1:] if args.passthrough[:1] == ["--"] else list(args.passthrough)
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
    passthrough = args.passthrough[1:] if args.passthrough[:1] == ["--"] else list(args.passthrough)
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
        record = {"url": args.url, "at": now(), "clips": clips}
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
        notes = dict(n.split("=", 1) for n in (args.note or []))
        body = "\n".join(figures) or (f'    <p class="none">{html_escape(notes[key])}</p>' if key in notes else
                                       '    <p class="none">No recording: this story has no visible loading or failure state to show.</p>')
        sections.append(f"""  <section class="story" id="{key.lower()}">
    <div class="story-head"><span class="key">{key} · {html_escape(c.label(key))}</span><h2>{html_escape(c.stories[key]['title'])}</h2><span class="links">{' '.join(links)}</span></div>
{body}
  </section>""")
    values = {
        "title": html_escape(args.title or f"{c.delivery} Evidence"),
        "eyebrow": html_escape(f"{c.delivery} · delivery evidence"),
        "heading": html_escape(c.raw.get("headline", c.delivery)),
        "intro": html_escape(args.intro or "Each clip runs the same steps twice: on the base before this delivery (left) and "
                             "on the delivered code (right). Loading clips hold the page's request for a few seconds; "
                             "failure clips make it fail."),
        "before": html_escape(", ".join(sorted(befores)) or "-"), "after": html_escape(", ".join(sorted(afters)) or "-"),
        "stories": "\n".join(sections),
        "footer": html_escape(f"{count} clip(s) across {len(keys)} stories. Recorded with run-delivery app-video."),
    }
    page = re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], (TOOL / "templates/evidence.html").read_text())
    (out / "index.html").write_text(page)
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

def forge(*args: str) -> dict:
    c = cfg()
    f = c.forge
    prefix = f.get("argv") or [sys.executable, "-B", str(TOOL / "adapters" / f"forge_{f['adapter']}.py")]
    env = {**os.environ, "FLOW_FORGE_REPOSITORY": f.get("repository", ""), "FLOW_FORGE_TOKEN_ENV": f.get("tokenEnv", "")}
    done = subprocess.run([*map(os.path.expanduser, prefix), *args], text=True, capture_output=True, env=env, cwd=repo())
    if done.returncode != 0:
        raise Stop(f"forge {args[0]} failed: {(done.stderr or done.stdout).strip()[-400:]}")
    try:
        return json.loads(done.stdout or "{}")
    except json.JSONDecodeError:
        raise Stop(f"forge {args[0]} printed no JSON: {done.stdout.strip()[-200:]}")


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
        return ("Lanes have no authority to push or open pull requests in this delivery. Commit on your branch and "
                "tell the lead it is ready; the lead pushes it.")
    what = f"push your own `{c.branch_pattern.replace('{key}', '<key>')}` branch, with `{flow_command()} push <key>`"
    if granted.get("draftPullRequests"):
        what += f", and open its draft pull request into `{c.base_branch}`, with `{flow_command()} open-pr`"
    return (f"{granted['grantedBy']} gave lanes authority on {granted['grantedOn']} to {what}. That is all: never push "
            f"any other branch, never force-push, never merge, and never mark a pull request ready or approve one. "
            "The lead's merge queue merges.")


def transitions_text() -> str:
    """The lane's status moves, from the own config's `tracker.transitions`; without it, lanes move nothing."""
    moves = (cfg().flow_raw.get("tracker") or {}).get("transitions")
    if not moves:
        return "Don't move your story's status: this repository gives lanes no status moves."
    if not isinstance(moves, dict) or not all(isinstance(moves.get(k, []), list) for k in ("start", "review")):
        raise Stop(f"{cfg().name} tracker.transitions must be {{start: [...], review: [...], source?}}")
    said = lambda names: ", then ".join(f'"{n}"' for n in names)
    lines = ["Move your story's status with your tracker tools, by these transitions, in order:"]
    if moves.get("start"):
        lines.append(f"- When you start the story: {said(moves['start'])}.")
    if moves.get("review"):
        lines.append(f"- Once its draft PR is open and the gates pass at its head: {said(moves['review'])}.")
    lines.append("Nothing else: later moves (after merge, after a deploy) aren't yours. If a transition fails, retry it "
                 "once, then say so in your report." + (f" The workflow: {moves['source']}" if moves.get("source") else ""))
    return "\n".join(lines)


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
        return
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
        handle.write(body)
    try:
        print(forge("open", "--source", c.branch(key), "--destination", c.base_branch, "--title", args.title,
                    "--body-file", handle.name)["url"])
    finally:
        os.unlink(handle.name)


def parse_blocked(items: list[str]) -> dict[str, str]:
    blocked = {}
    for item in items or []:
        label, _, reason = item.partition("=")
        if not label.strip() or not reason.strip():
            raise Stop(f"--blocked needs LABEL=REASON, got {item!r}")
        blocked[label.strip()] = reason.strip()
    return blocked


def pr_review_config() -> dict | None:
    """The own config's review.pr: {author, waitSeconds?}: a review the forge's pipeline posts on each PR."""
    conf = (cfg().flow_raw.get("review") or {}).get("pr")
    if conf and not conf.get("author"):
        raise Stop(f"{cfg().name} review.pr needs `author`: the name the pipeline's review comment is posted as")
    return conf


def pr_review_state(url: str, head: str, conf: dict) -> tuple[str, dict | None, list[dict]]:
    """(status, the review, the head's checks). status: current, pending or failed."""
    found = forge("reviews", "--url", url, "--head", head)
    checks = found.get("checks", [])
    if any(ch["state"] == "failed" for ch in checks):
        return "failed", None, checks
    started = max((ch["at"] for ch in checks), default=None)
    mine = [r for r in found.get("reviews", []) if r["author"] == conf["author"]]
    review = max(mine, key=lambda r: r["at"], default=None)
    if not checks or any(ch["state"] != "passed" for ch in checks) or not review or review["at"] < started:
        return "pending", review, checks
    return "current", review, checks


def pr_review_text() -> str:
    conf = pr_review_config()
    if not conf:
        return ""
    return (f"   **The PR review.** The forge's pipeline reviews every PR and posts as \"{conf['author']}\". Open the draft "
            "PR as soon as the gates first pass, so that review runs alongside your lens rounds; each push re-runs it. "
            f"Before you hand off, at your final head: `{flow_command()} pr-review <key> --wait` waits for the pipeline "
            "and its review, saves it and prints it. Triage its findings as you would a lens's: fix anything that "
            "should be fixed in the lane (then the gates, a lens round and this review again at the new head), and fix "
            "or defer the rest, deferring only with a follow-up. Pass `--pr-review-triage \"<what you fixed, deferred "
            "and why>\"` to `handoff`. The lead's acceptance re-checks that the review is current at your head.\n")


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
    deadline = time.time() + (int(conf.get("waitSeconds", 1800)) if args.wait else 0)
    while True:
        status, review, checks = pr_review_state(url, head, conf)
        if status != "pending" or time.time() >= deadline:
            break
        time.sleep(30)
    if status == "failed":
        raise Stop(f"the pipeline failed at {head[:10]}: " + ", ".join(f"{ch['name']} {ch['state']}" for ch in checks))
    if status == "pending":
        raise Stop(f"no {conf['author']} review after the pipeline for {head[:10]} yet"
                   + ("" if args.wait else "; run with --wait") + ": " + (", ".join(f"{ch['name']} {ch['state']}" for ch in checks) or "no checks"))
    record = {"head": head, "url": url, "author": review["author"], "at": review["at"], "body": review["body"]}
    (review_dir(key) / "pr-review.json").write_text(json.dumps(record, indent=2))
    print(f"{conf['author']} review of {head[:10]} ({review['at']}), saved for the hand-off:\n")
    print(review["body"])
    print("\nTriage every finding like a lens finding: fix what blocks in the lane (then gates, a lens round and this "
          "review again at the new head), and fix or defer the rest. Hand off with --pr-review-triage saying which.")


def handoff(args) -> None:
    c = cfg()
    key = c.require(args.key)
    lane = c.lane_of(key)
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
    if pr_review_config():
        saved = folder / "pr-review.json"
        recorded = json.loads(saved.read_text()) if saved.exists() else {}
        if recorded.get("head") != head:
            gate_problems.append(f"no PR review recorded at {head[:10]}: run {flow_command()} pr-review {key} --wait")
        elif not (args.pr_review_triage or "").strip():
            gate_problems.append("say how the PR review's findings were handled: --pr-review-triage TEXT")
        else:
            recorded["triage"] = args.pr_review_triage.strip()
            saved.write_text(json.dumps(recorded, indent=2))
            pr_line = f"{recorded['author']} at {recorded['at'][:16]}; triage: {recorded['triage']}"
    blocked = parse_blocked(args.blocked)
    (folder / "blocked.json").write_text(json.dumps({"head": head, "blocked": blocked}, indent=2))
    lines, problems = in_app_evidence(key, repo(), head, blocked)
    video_lines, video_problems = video_evidence(key, repo(), head)
    lines += video_lines
    problems = gate_problems + problems + video_problems
    if problems:
        raise Stop("the hand-off isn't complete:\n  " + "\n  ".join(problems))
    state_tool("capture-review", "--subject", key, "--candidate", head, *[x for p in rounds for x in ("--outcome", str(p))])
    state_tool("handoff", "--lane", lane, "--subject", key, "--head", head, "--change-request", args.pr,
               "--rounds", str(len(rounds)))
    histories = [f"r{o['round']}: {round_history_text(o)}" for o in outcomes if round_history_text(o)]
    print("\n".join([
        f"**Hand-off: {key}** ({c.label(key)}, {lane})",
        f"- candidate: {args.pr}",
        f"- reviewed head: `{head}`",
        "- review rounds: " + "; ".join(f"r{o['round']} {o['outcome']}" for o in outcomes) + " (review evidence captured)"
        + (f"; history: {'; '.join(histories)}" if histories else ""),
        f"- validation: {validation}",
        *([f"- PR review: {pr_line}"] if pr_line else []),
        "- in-app checks: " + ("; ".join(lines) or "none required"),
        "- run: none",
        "- lead inputs: none",
        f"- expensive non-blocking findings: {args.findings or 'none'}",
    ]))
    print(f"\nTracker post (your story): hand-off: {args.pr}, head {head[:10]}, {len(rounds)} review round(s); {validation}.")


# -- the plan chapter's producer -------------------------------------------------------------------
# plan.json, the delivery-views input `delivery-plan/1`, is a pure function of the delivery file at the base, the
# append-only plan-changes.jsonl beside the lead's flow logs, the app-check and video flow logs, and the acceptance
# log. Only lead-side commands and the reconcile sweep rebuild it, under one lock; lanes only append their flow logs,
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
    plan = {
        "schemaVersion": "delivery-plan/1", "updated": now(), "outcome": outcome,
        "requiredLenses": c.raw.get("requiredLenses", list(c.lenses)), "proofKinds": kinds, "units": units,
        "finish": plan_finish(c, snapshot["at"]),
        "approved": {"at": snapshot["at"], **({"review": LATE_SNAPSHOT} if snapshot.get("late") else {}),
                     "snapshot": {"units": snapshot["units"], "finish": snapshot["finish"]}},
        "changes": changes,
        "evidence": {key: found for key in c.stories if (found := plan_evidence(c, key))},
    }
    for key in ("approvals", "outOfScope"):
        if key in c.raw:
            plan[key] = c.raw[key]
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


def rebuild_plan(c) -> str | None:
    """Rebuild plan.json under the lock. Writes it, atomically, only when its content apart from `updated` changed,
    then journals the write so the live view re-renders. Returns what changed, or None when nothing did."""
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
            key for key in plan if key != "updated" and old.get(key) != plan.get(key))
        temporary = path.with_name(f".{PLAN_FILE}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(plan, indent=2) + "\n")
        os.replace(temporary, path)
        state_tool("journal", "--kind", "decision.recorded", "--summary", f"Plan updated: {plan['updated']}; {moved}")
        return moved


def rebuild_plan_quietly(c) -> str:
    """For the sweep: one line, and a plan that won't build never stops the sweep."""
    try:
        moved = rebuild_plan(c)
    except (Stop, ValueError) as error:  # a refusal, or a delivery file saved mid-edit that isn't JSON
        return f"plan: not rebuilt: {error}"
    return f"plan: {moved}" if moved else "plan: unchanged"


def lead_plan(args) -> None:
    """Record a change to the approved plan in the delivery file, or a reason for one already recorded."""
    c = cfg()
    log = read_plan_log(c)
    if not plan_declared(c) and not any(entry["type"] == "snapshot" for entry in log):
        raise Stop(f"{c.delivery_where} declares no criteria, so the plan chapter is off; add criteria to its stories first")
    if args.action == "why":
        with plan_lock(c):
            log = read_plan_log(c)
            if not any(entry["type"] == "change" and entry["id"] == args.change for entry in log):
                raise Stop(f"no recorded change {args.change}; the changes are in {plan_log_path(c)}")
            append_plan_log(c, {"type": "why", "change": args.change, "why": args.why})
            rebuild_plan(c)
        print(f"recorded the reason for {args.change}")
        return
    root = c.root
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    if branch != c.base_branch:
        raise Stop(f"run lead-plan in the lead's checkout of {c.base_branch}; this one is on {branch}")
    if git("status", "--porcelain", cwd=root):
        raise Stop(f"{root} has uncommitted changes; commit or remove them first, so the plan change commits alone")
    git("fetch", "-q", c.remote, c.base_branch, cwd=root)
    if not is_ancestor(c.base_ref, "HEAD"):
        raise Stop(f"{root} has diverged from {c.base_ref}; bring it up to date first")
    raw = json.loads(c.delivery_path.read_text())
    stories = raw["stories"]
    change: dict = {"kind": args.kind, "subjects": args.subjects or [], "by": args.by, "why": args.why, "source": "lead-plan"}
    for key in ("title", "ref"):
        if getattr(args, key):
            change[key] = getattr(args, key)
    unknown = [key for key in change["subjects"] if key not in stories]
    if unknown:
        raise Stop(f"{', '.join(unknown)} isn't a story of {c.delivery}")
    planned_after = None
    record_finish = None
    if args.kind in ("added", "changed", "relaxed"):
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
        if not args.finish or args.covers is None:
            raise Stop("--kind finish takes --finish <id> and --covers <JSON>")
        try:
            covers = json.loads(args.covers)
        except ValueError:
            raise Stop("--covers must be JSON, such as {\"criteria\": \"all\", \"proofs\": [\"merged\"]}")
        lines = raw.setdefault("finish", [])
        line = next((item for item in lines if item["id"] == args.finish), None)
        if line is None:
            if not args.text:
                raise Stop(f"finish line {args.finish} is new: give its --text")
            line = {"id": args.finish, "text": args.text, **({"label": args.label} if args.label else {})}
            lines.append(line)
            record_finish = {"id": args.finish, "text": args.text, "met": False}
        else:
            change["before"] = plan_covers(line)
        line.update(covers=covers, at=now())
        change.update(finish=args.finish, after=covers)
    with plan_lock(c):
        # A delivery started before its criteria has no snapshot yet: it is taken of the plan before this change.
        if (c.state / "state.json").exists() and not any(entry["type"] == "snapshot" for entry in read_plan_log(c)):
            write_snapshot(c, late=True)
        original = c.delivery_path.read_text()
        c.delivery_path.write_text(json.dumps(raw, indent=2) + "\n")
        try:
            c.load_delivery(c.delivery)
            check_plan(build_plan(c, read_plan_log(c) or [snapshot_entry(c, late=True)]), c)
        except Stop:
            c.delivery_path.write_text(original)
            c.load_delivery(c.delivery)
            raise
        git("add", str(c.delivery_path), cwd=root)
        entry = record_plan_change(c, change)
        git("commit", "-q", "-m", f"{c.delivery}: plan change {entry['id']} ({args.kind} "
            f"{', '.join(change['subjects']) or change.get('finish', '')})".rstrip(), cwd=root)
        if planned_after is not None:
            doc = load_state()  # as it stands now: a story registered since the check above stays planned
            patch({"planned": [p for p in doc["state"].get("planned", []) if p["subject"] not in change["subjects"]]},
                  doc["state"]["version"])
        if record_finish is not None:
            doc = load_state()
            patch({"finish": [*doc["state"]["finish"], record_finish]}, doc["state"]["version"])
        rebuild_plan(c)
    print(f"recorded {entry['id']}: {args.kind} {', '.join(change['subjects']) or change.get('finish', '')}".rstrip())
    print(f"committed the delivery file; push it so lanes see it: git push {c.remote} HEAD:{c.base_branch}")


# -- lead ----------------------------------------------------------------------------------

def patch(body: dict, version: int) -> None:
    state_tool("patch", "--writer", "lead", "--version", str(version), "--patch", json.dumps(body))


def lead_setup(args) -> None:
    """Draft .agents/deliveries/<key>.json from the approved plan (and, optionally, the tracker snapshot)."""
    c = Config(repo(), need_delivery=False)
    key = args.key
    path = c.deliveries / f"{key}.json"
    if path.exists() and not args.force:
        raise Stop(f"{path} exists; edit it, or pass --force to draft it again")
    plan = c.root / args.plan
    pattern = re.compile(args.pattern, re.M)
    if "key" not in pattern.groupindex or "title" not in pattern.groupindex:
        raise Stop("the pattern must name groups `key` and `title` (and may name `label` and `pinned`)")
    stories = {}
    for m in pattern.finditer(plan.read_text()):
        g = m.groupdict()
        story = {"title": g["title"].strip(), "lane": "unassigned"}
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
    notes = []
    if args.tracker_snapshot:
        items = json.loads(Path(args.tracker_snapshot).read_text()).get("items", [])
        tracked = {i["subject"]: i for i in items if i["subject"] != key and i.get("state") != "canceled"}
        for subject, item in tracked.items():
            if subject not in stories:
                notes.append(f"tracker item {subject} ({item.get('title')}) has no plan heading; add it or cancel it")
        for subject in stories:
            if subject not in tracked:
                notes.append(f"plan story {subject} has no tracker item")
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
        "lanes": {},
        "stories": stories,
        "finish": [EVIDENCE_FINISH] if any(st.get("evidence") == "video" for st in stories.values()) else [],
    }
    if args.ship_from:
        delivery["ship"] = {"from": args.ship_from, "toolingCommit": tooling, "paths": args.ship_path or ["src/"],
                            "branch": "ship/{delivery}"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(delivery, indent=2) + "\n")
    print(f"drafted {path}: {len(stories)} stories")
    print("next: give each story its lane, list the lanes and the files each owns, set inApp requirements, the finish "
          "lines and the brief notes (all from the approved plan), then lead-init and lead-brief")
    for n in notes:
        print(f"  note: {n}")


def lead_init(args) -> None:
    c = cfg()
    c.check_lanes()
    if c.ship and not c.ship.get("toolingCommit"):
        raise Stop(f"{c.delivery_where}: ship.toolingCommit isn't set")
    if plan_declared(c):
        # Refused before the record starts, so a criterion the view can't read is corrected before anything is kept.
        check_plan(build_plan(c, [snapshot_entry(c, late=False)]), c)
    if not (c.state / "state.json").exists():
        state_tool("init", "--headline", c.raw.get("headline", c.delivery))
    doc = load_state()
    if doc["state"].get("planned"):
        raise Stop(f"{c.state} already has planned work")
    planned = [planned_entry(c, key) for key in c.stories]
    # The record's finish items take no other keys: covers, label and at are the plan's.
    finish = [{"id": f["id"], "text": f["text"], "met": False} for f in c.finish]
    patch({"meta": {"trackerParent": c.delivery, "handoffsNeedAcceptance": True}, "planned": planned, "finish": finish},
          doc["state"]["version"])
    if not any(e.get("kind") == "decision.recorded" for e in doc["journal"]):
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


def planned_entry(c, key: str) -> dict:
    s = c.stories[key]
    return {"subject": key, "title": f"{c.label(key)}: {s['title']}" if s.get("label") else s["title"],
            "lane": s["lane"], "stage": "planned"}


def register_stories(c) -> list[str]:
    """Stories added to the delivery after lead-init join the record as planned work, so the live view shows them
    before they're assigned. One the tracker reconcile already filed as a follow-up moves out of the follow-ups.
    Returns the keys added; a delivery not yet initialized is left to lead-init."""
    if not (c.state / "state.json").exists():
        return []
    doc = load_state()
    state = doc["state"]
    if not state.get("planned") and not state.get("queue"):
        return []
    held = {item["subject"] for item in [*state["queue"], *state.get("planned", [])]}
    added = [key for key in c.stories if key not in held]
    if not added:
        return []
    followups = [f for f in state["followups"] if f["id"] not in added and f.get("reference") not in added]
    patch({"planned": [*state.get("planned", []), *(planned_entry(c, key) for key in added)], "followups": followups},
          state["version"])
    for key in added:
        state_tool("journal", "--kind", "decision.recorded", "--subject", key, "--summary",
                   f"{key} added to the delivery as planned work for {c.lane_of(key)}")
    if any(entry["type"] == "snapshot" for entry in read_plan_log(c)) or plan_declared(c):
        for key in added:
            # Its reason is the lead's to give, with lead-plan why; until then the plan flags it.
            record_plan_change(c, {"kind": "added", "subjects": [key], "by": "lead", "title": c.stories[key]["title"],
                                   "source": "registration"})
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


def lead_assign(args) -> None:
    c = cfg()
    keys = [c.require(k) for k in args.keys]
    for key in keys:
        if c.lane_of(key) != args.lane:
            raise Stop(f"{key} is planned for {c.lane_of(key)}, not {args.lane}")
    register_stories(c)
    rebuild_plan(c)
    doc = load_state()
    lanes = [entry for entry in doc["state"]["lanes"] if entry["id"] != args.lane]
    lanes.append({"id": args.lane, "title": args.title, "focus": f"assigned {', '.join(keys)}", "state": "assigned",
                  "waitingOn": None, "since": now()})
    patch({"lanes": lanes}, doc["state"]["version"])
    state_tool("journal", "--kind", "lane.assigned", "--lane", args.lane, "--subject", keys[0], "--summary",
               f"{args.lane} assigned {', '.join(keys)}" + (f" ({args.note})" if args.note else ""))
    print(f"Assignment message for {args.lane} ({args.title}):\n")
    print(f"You are {args.lane} in the {c.delivery} delivery. Read the lane brief first: {c.root / c.lane_brief}\n")
    print("Your stories, in order (one branch, one review and one hand-off each):")
    for key in keys:
        print(f"- {key} {c.issue_url.format(key=key)} ({c.label(key)}: {c.stories[key]['title']})")
    if args.note:
        print(f"\nNote from the lead: {args.note}")
    print(f"\nRecord: {c.state}")
    print("\nWhen you hand off a story, or are blocked, end your turn with a short report that opens with the story "
          "key and the outcome (handed off, or blocked and on what): the lead sees its first lines. Then the branch, "
          "PR, reviewed head, review rounds and verdicts, gates, in-app checks, and any decision you need from the "
          "lead. The lead checks the hand-off and tells you when to start your next story.")


def lead_accept(args) -> None:
    c = cfg()
    key = c.require(args.key)
    doc = load_state()
    item = next((q for q in doc["state"]["queue"] if q["subject"] == key), None)
    if not item:
        raise Stop(f"{key} is not in the queue; the lane has not handed it off")
    problems, decisions = [], []
    if not item.get("pending"):
        problems.append("the hand-off is not waiting for acceptance")
    head = item["head"]
    branch = c.branch(key)
    if remote_head(branch) != head:
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
    if pr.get("state") != "open":
        problems.append(f"the PR is {pr.get('state')}")
    if not same_commit(pr.get("head"), head):
        problems.append(f"the PR is at {str(pr.get('head'))[:10]}, not the handed head")
    if pr.get("destination") != c.base_branch or pr.get("source") != branch:
        problems.append(f"the PR is {pr.get('source')} -> {pr.get('destination')}, not {branch} -> {c.base_branch}")
    conf = pr_review_config()
    if conf:
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
    changed = git("diff", "--name-only", f"{base}..{head}").splitlines()
    owns = c.lanes[c.lane_of(key)]["owns"]
    outside = [f for f in changed if not any(fnmatch.fnmatch(f, g) for g in owns)]
    if outside:
        problems.append(f"the branch changes files outside {c.lane_of(key)}'s: {outside}")
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
    if problems:
        print(f"NOT accepted: {key}")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    state_tool("handoff", "--accept", key)
    # What the plan's producer proofs are judged by: the accepted head and the in-app items accepted as blocked.
    append_log("acceptance", key, {"head": head, "blocked": {label: reason for label, reason in blocked.items()
                                                             if label in accepted_blocked}})
    state_tool("journal", "--kind", "decision.recorded", "--subject", key, "--summary",
               f"Hand-off checked as a claim: {c.remote}/{branch} at {head[:10]}, its PR open at that head into "
               f"{c.base_branch}, {len(set(numbers))} round(s) journaled, last approved by every lens, review evidence "
               f"captured at that head, files within {c.lane_of(key)}'s, gates and in-app evidence current."
               + (" " + "; ".join(decisions) + "." if decisions else ""))
    rebuild_plan(c)
    print(f"accepted {key}; the runner may take it")
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


def clean_story(key: str) -> tuple[str, list[str], list[str]]:
    """Remove one merged story's checkout and branches. Returns (status, done, left); status is cleaned, nothing,
    unmerged or refused. Nothing is removed unless every tip of the story is already on the delivery base."""
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
    if off_base:
        return "unmerged", [], [f"not on {c.base_ref}: {', '.join(off_base)}"] + left
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
        git("branch", "-D", branch)  # -D: its tip is on the base (checked above), which -d can't see from here
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
    state_tool("journal", "--kind", "decision.recorded", "--subject", key, "--summary",
               f"Cleaned up {key} after it merged into {c.base_branch}: " + "; ".join(done + left) + ".", check=False)
    return "cleaned", done, left


def lead_cleanup(args) -> None:
    c = cfg()
    keys = [c.require(k) for k in args.keys] if args.keys else list(c.stories)
    failed = False
    for key in keys:
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
        if not re.search(rf"(merge_queue|render_state)\.py|(?:^|/|\s)(?:{names})\s.*\blead-reconcile\b", command) \
                or int(pid_text) == os.getpid():
            continue
        named = re.search(r"--delivery[ =](\S+)", command)
        if named and named.group(1) != delivery:
            continue
        cwd = run(["lsof", "-a", "-p", pid_text, "-d", "cwd", "-Fn"], check=False).stdout
        here = next((Path(l[1:]) for l in cwd.splitlines() if l.startswith("n")), None)
        homes = [lead.resolve()] + ([main.resolve()] if "lead-reconcile" in command else [])
        if str(state) in command or (here and here.resolve() in homes):
            found.append((int(pid_text), command))
    return found


def reconcile_once() -> list[str]:
    """Take the binding's forge and tracker snapshots and reconcile the record with each: an open PR moves a planned
    story to review, a started ticket to building. Returns one line per snapshot."""
    c = cfg()
    binding = c.root / ".agents/orchestration.json"
    states = json.loads(binding.read_text()).get("state", {}) if binding.exists() else {}
    folder = c.state.parent / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    # First, so a story just added to the delivery is planned work, not a follow-up, when the tracker lists it.
    added = register_stories(c)
    lines = [f"registered {', '.join(added)} as planned work"] if added else []
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
    if plan_declared(c) or plan_log_path(c).exists():
        lines.append(rebuild_plan_quietly(c))
    return lines


def lead_reconcile(args) -> None:
    while True:
        try:
            print(now(), "; ".join(reconcile_once()), flush=True)
        except Stop as error:
            if not args.every:
                raise
            print(now(), f"skipped: {error}", flush=True)
        if not args.every:
            return
        time.sleep(args.every)


def lead_close(args) -> None:
    c = cfg()
    doc = load_state()
    if not any(e.get("kind") == "delivery.closed" for e in doc["journal"]):
        raise Stop("journal delivery.closed first (orchestrate-work's close-out): lead-close runs after it")
    state = doc["state"]
    busy = [q["subject"] for q in state["queue"] if q.get("stage") not in ("merged", "stopped")]
    if busy or (state["meta"].get("checking") or {}).get("now"):
        raise Stop(f"the queue still holds work ({busy or 'a check is running'}); let it finish or stop it first")
    lead = c.lane_checkout(c.delivery)
    done, left = [], []
    for key in c.stories:
        status, d, l = clean_story(key)
        done += [f"{key}: {x}" for x in d]
        left += [f"{key}: {x}" for x in l]
    release = c.release / "skills/orchestrate-work/scripts/render_state.py"
    binding = c.root / ".agents/orchestration.json"
    if binding.exists():
        rendered = run([sys.executable, "-B", str(release), "--dir", str(c.state), "--binding", str(binding)], check=False)
        if rendered.returncode == 0:
            done.append("rendered the views one last time")
        else:
            left.append(f"the final render failed: {(rendered.stderr or rendered.stdout).strip()[-300:]}")
    archive_root = Path(expand((c.flow_raw.get("cleanup") or {}).get(
        "archive", DEFAULT_ARCHIVE), repo=c.main.name, delivery=c.delivery))
    archive = archive_root / datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    archive.mkdir(parents=True)
    for source in (c.state, c.flow_log, c.flow_log.parent / "evidence"):
        if source.exists():
            shutil.copytree(source, archive / source.name)
    done.append(f"archived the record, the lead-side logs and the lanes' evidence in {archive}")
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
        except Stop as held:
            left.append(str(held))
        if port_open(port):
            left.append(f"something still listens on port {port}")
    print(f"{c.delivery}: closed out")
    for line in done:
        print(f"  done: {line}")
    for line in left:
        print(f"  left: {line}")
    print("  yours: archive the lane sessions, align your main checkout with the final base, and remove the lead's "
          f"checkout {lead} when you no longer need it (its record is archived above).")
    if left:
        sys.exit(1)


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
    diff = run(["git", "diff", "--binary", s["toolingCommit"], c.base_ref, "--", *s["paths"]]).stdout  # unstripped
    if not diff:
        raise Stop(f"nothing under {s['paths']} to ship")
    git("worktree", "add", "-q", "--no-track", "-b", branch, str(path), s["from"])
    applied = apply_patch(diff, path)
    files = git("diff", "--cached", "--name-only", cwd=path).splitlines()
    outside = [f for f in files if not any(f == p.rstrip("/") or f.startswith(p.rstrip("/") + "/") for p in s["paths"])]
    print(f"{branch} at {path}, from {s['from']} ({git('rev-parse', '--short', s['from'])})")
    print(f"  {len(files)} file(s) staged; outside {s['paths']}: {outside or 'none'}")
    if applied.returncode != 0:
        unmerged = git("diff", "--name-only", "--diff-filter=U", cwd=path)
        raise Stop("git apply failed" + (f"; resolve the conflicts in {path}: {unmerged}" if unmerged else f": {applied.stderr.strip()}"))
    if outside:
        raise Stop("files outside the ship paths were staged; this must not happen")
    if args.no_gates or not c.gates:
        print("  next: prepare the checkout, run the gates there, then commit as one change. Its PR waits for the user.")
        return
    ship_gates(c, path, s)
    print("  next: commit the staged change as one commit. Its PR waits for the user.")


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
                raise Stop(f"something else is listening on {ia['port']}; stop it first")
            server = subprocess.Popen(expand_argv(ia["serve"]["argv"], dir=str(site), port=str(ia["port"])),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            lease.track(server.pid)
            for _ in range(100):
                if port_open(ia["port"]):
                    break
                time.sleep(0.2)
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
            ("in-app", "in-app adapters", ("config",), self.in_app),
            ("adapters", "in-app adapter contract", ("in-app",), self.adapters),
            ("video", "video evidence tools", ("config",), self.video),
            ("harness", "harness configuration", ("config", "generation"), self.harness),
            ("delivery", "delivery file", ("config", "binding"), self.check_delivery),
            ("plan", "plan chapter", ("delivery",), self.check_plan),
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
        if errors:
            return "wrong", (f"{path.name} doesn't validate: {'; '.join(errors[:3])}"
                             f"{f' (and {len(errors) - 3} more)' if len(errors) > 3 else ''}; fix: correct it against "
                             "orchestration-binding/1")
        self.binding = binding
        return "ok", f"{path.name} validates against orchestration-binding/1"

    def base_ref(self) -> str | None:
        base = self.c.binding_base
        return f"{self.c.remote}/{base}" if base else None

    def committed(self):
        ref = self.base_ref()
        if not ref:
            return "wrong", "no delivery base; fix: set base.ref in the binding"
        if not OFFLINE:  # what lanes will check out; --offline reads the local remote-tracking ref instead
            run(["git", "fetch", "-q", self.c.remote, self.c.binding_base], cwd=self.root, check=False)
        if run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=self.root, check=False).returncode != 0:
            return "wrong", (f"the delivery base {ref} doesn't exist{' locally' if OFFLINE else ''}; fix: push the "
                             "delivery base")
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
        if not pr_review_config():
            return self.not_applicable("no review.pr configured")
        if not forge_answers("reviews"):
            return "wrong", (f"review.pr is configured, but the {self.c.forge.get('adapter')} adapter has no `reviews` "
                             "command, so hand-offs would wait on a review that never comes; fix: remove review.pr, "
                             "or use an adapter that answers `reviews`")
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
        argv = (((self.binding or {}).get("state") or {}).get("trackerSnapshot") or {}).get("argv")
        if not argv:
            return self.not_applicable("the binding declares no tracker snapshot")
        names = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("--token-env", "--user-env")]
        unset = [name for name in names if not os.environ.get(name)]
        if unset:
            return "missing", (f"{', '.join(unset)} {'is' if len(unset) == 1 else 'are'} unset, and the tracker "
                               f"snapshot reads {'it' if len(unset) == 1 else 'them'}; fix: export {', '.join(unset)}")
        return "ok", f"{', '.join(names) or 'no variables'} set"

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
    global DELIVERY_ARG
    parser = argparse.ArgumentParser(prog="run_delivery.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--delivery", help="the delivery key, when the repository has several")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("review-prepare"); p.add_argument("key"); p.add_argument("--round", type=int, required=True)
    p.add_argument("--grace", action="store_true"); p.add_argument("--restart", action="store_true")
    p.set_defaults(fn=review_prepare)
    p = sub.add_parser("review-reduce"); p.add_argument("key"); p.add_argument("--round", type=int, required=True)
    p.set_defaults(fn=review_reduce)
    p = sub.add_parser("gate"); p.add_argument("key"); p.set_defaults(fn=gate)
    p = sub.add_parser("build"); p.add_argument("key"); p.set_defaults(fn=build)
    p = sub.add_parser("app-check"); p.add_argument("key"); p.add_argument("name")
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
    p = sub.add_parser("push"); p.add_argument("key"); p.set_defaults(fn=push)
    p = sub.add_parser("app-video"); p.add_argument("key"); p.add_argument("name")
    p.add_argument("--mode", choices=["hold", "fail", "loaded"], required=True); p.add_argument("--path", required=True)
    p.add_argument("--hold", action="append", default=[]); p.add_argument("--fail", action="append", default=[])
    p.add_argument("--click", action="append", default=[]); p.add_argument("--for", dest="for_")
    p.add_argument("--wait", type=int, default=2500); p.add_argument("--after-for"); p.add_argument("--settle", type=int, default=2500)
    p.add_argument("--caption"); p.add_argument("--after-build", help="a stamped build to record as 'after' (the lead's base build)")
    p.add_argument("--lock-wait", type=int)
    p.add_argument("passthrough", nargs="*", help="after --: extra arguments for the check adapter")
    p.set_defaults(fn=app_video)
    p = sub.add_parser("open-pr"); p.add_argument("key"); p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True); p.set_defaults(fn=open_pr)
    p = sub.add_parser("pr-review"); p.add_argument("key"); p.add_argument("--wait", action="store_true")
    p.set_defaults(fn=pr_review)
    p = sub.add_parser("handoff"); p.add_argument("key"); p.add_argument("--pr", required=True)
    p.add_argument("--pr-review-triage")
    p.add_argument("--findings"); p.add_argument("--blocked", action="append", default=[]); p.set_defaults(fn=handoff)
    p = sub.add_parser("lead-setup"); p.add_argument("key"); p.add_argument("--plan", required=True)
    p.add_argument("--base", help="default: the binding's base.ref"); p.add_argument("--pattern", required=True); p.add_argument("--headline")
    p.add_argument("--tracker-snapshot"); p.add_argument("--ship-from"); p.add_argument("--ship-path", action="append")
    p.add_argument("--context-section", action="append"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=lead_setup)
    p = sub.add_parser("lead-init"); p.set_defaults(fn=lead_init)
    p = sub.add_parser("lead-brief"); p.set_defaults(fn=lead_brief)
    p = sub.add_parser("lead-assign"); p.add_argument("keys", nargs="+"); p.add_argument("--lane", required=True)
    p.add_argument("--title", required=True); p.add_argument("--note"); p.set_defaults(fn=lead_assign)
    p = sub.add_parser("lead-accept"); p.add_argument("key"); p.add_argument("--accept-blocked", action="append")
    p.add_argument("--accept-restarts", action="store_true")
    p.add_argument("--accept-discarded", action="append", type=int, metavar="ROUND"); p.set_defaults(fn=lead_accept)
    p = sub.add_parser("lead-cleanup"); p.add_argument("keys", nargs="*"); p.set_defaults(fn=lead_cleanup)
    p = sub.add_parser("lead-reconcile"); p.add_argument("--every", type=int, default=0)
    p.set_defaults(fn=lead_reconcile)
    p = sub.add_parser("lead-evidence"); p.add_argument("action", choices=["record", "page", "published", "status"])
    p.add_argument("--url", help="published: the page's address")
    p.add_argument("keys", nargs="*"); p.add_argument("--after-build"); p.add_argument("--out", default="evidence-page")
    p.add_argument("--title"); p.add_argument("--intro")
    p.add_argument("--note", action="append", help="KEY=TEXT: what to say for a story without clips")
    p.set_defaults(fn=lead_evidence)
    p = sub.add_parser("lead-plan", help="record a change to the approved plan, or a reason for one")
    plan_commands = p.add_subparsers(dest="action", required=True)
    q = plan_commands.add_parser("change", help="edit and commit the delivery file, recording before and after")
    q.add_argument("--kind", required=True, choices=["added", "removed", "relaxed", "changed", "finish"])
    q.add_argument("--subjects", nargs="*", default=[]); q.add_argument("--criterion"); q.add_argument("--text")
    q.add_argument("--finish", help="finish: the line's id"); q.add_argument("--covers", help="finish: its covers, as JSON")
    q.add_argument("--label", help="finish: a new line's label"); q.add_argument("--why", required=True)
    q.add_argument("--by", default="lead"); q.add_argument("--ref"); q.add_argument("--title")
    q.set_defaults(fn=lead_plan)
    q = plan_commands.add_parser("why", help="give a recorded change its reason")
    q.add_argument("change"); q.add_argument("--why", required=True); q.set_defaults(fn=lead_plan)
    p = sub.add_parser("lead-close"); p.set_defaults(fn=lead_close)
    p = sub.add_parser("ship"); p.add_argument("--no-gates", action="store_true", help="stage only; run the gates yourself")
    p.set_defaults(fn=ship)
    p = sub.add_parser("adapter-test"); p.set_defaults(fn=adapter_test)
    p = sub.add_parser("config"); p.set_defaults(fn=show_config)
    p = sub.add_parser("doctor", help="check this repository's setup: every missing prerequisite and its fix")
    p.add_argument("--delivery", dest="doctor_delivery", help="also check this delivery's file")
    p.add_argument("--offline", action="store_true", help="don't fetch the base or ask the forge; read local refs")
    p.add_argument("--run-adapters", action="store_true", help="also run the in-app adapters on the fixture page")
    p.add_argument("--run-gates", action="store_true", help="also run the gates on the base, in a fresh checkout")
    p.set_defaults(fn=doctor)
    args = parser.parse_args(argv)
    DELIVERY_ARG = args.delivery
    for _signal in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(_signal, _terminate)
    try:
        args.fn(args)
    except Stop as error:
        print(f"run-delivery: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
