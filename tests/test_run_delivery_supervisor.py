"""The delivery's supervisor: `lead-run`, which does every mechanical lead duty pass after pass.

Each case builds a throwaway repository with a local bare remote, a binding whose queue really merges (git only),
and a fake forge whose pull requests open as drafts. The process cases start the real supervisor and stop every
process they start; the pass cases run `lead-run --once`, one pass at a time, so what each pass does is exact.
"""

from __future__ import annotations

import datetime
import fcntl
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.request
from pathlib import Path

from tests.test_run_delivery import (POSIX_ONLY, Fixture, commit, doctor_binding, installed_release, setup_delivery,
                                     sh, stop_supervisors, write_results)
from tests.test_run_delivery_tracker import FileBackend, JiraBackend

# Supervisors, their children and a shared fake forge state: one case at a time.
RUN_SERIALLY = True

FORGE = r'''
import argparse, json, os, subprocess, sys
state = os.environ["FAKE_FORGE_STATE"]
prs = json.load(open(state)) if os.path.exists(state) else {}
p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="c")
a = sub.add_parser("find"); a.add_argument("--source")
a = sub.add_parser("open"); a.add_argument("--source"); a.add_argument("--destination"); a.add_argument("--title"); a.add_argument("--body-file")
a = sub.add_parser("get"); a.add_argument("--url")
a = sub.add_parser("ready"); a.add_argument("--url")
args = p.parse_args()
def head(branch):
    line = subprocess.run(["git", "ls-remote", "origin", "refs/heads/" + branch], capture_output=True, text=True).stdout
    return line.split("\t")[0][:12]
if args.c == "find":
    url = next((u for u, pr in prs.items() if pr["source"] == args.source and pr["state"] == "open"), None)
    print(json.dumps({"url": url} if url else {}))
elif args.c == "open":
    url = "https://forge.test/pr/%d" % (len(prs) + 1)
    prs[url] = {"source": args.source, "destination": args.destination, "state": "open", "draft": True, "title": args.title}
    json.dump(prs, open(state, "w")); print(json.dumps({"url": url}))
elif args.c == "ready":
    if os.environ.get("FAKE_FORGE_READY") == "refuse":
        sys.exit("ready refused: the token may not edit pull requests")
    prs[args.url]["draft"] = False
    json.dump(prs, open(state, "w")); print(json.dumps({"url": args.url, "draft": False}))
else:
    pr = prs.get(args.url)
    if not pr:
        sys.exit("no such PR " + args.url)
    print(json.dumps({**pr, "url": args.url, "head": head(pr["source"])}))
'''

CANDIDATE = r'''
import json, subprocess, sys
branch = "feature/" + sys.argv[1]
tip = subprocess.run(["git", "ls-remote", "--heads", "origin", "refs/heads/" + branch],
                     capture_output=True, text=True, check=True).stdout.split()
if not tip:
    sys.exit(3)
print(json.dumps({"branch": branch, "head": tip[0]}))
'''

# The merge step refuses a draft, as some adopters' do; otherwise the checked head lands on the base.
MERGE = r'''
import json, os, subprocess, sys
base, branch, expected, base_head = sys.argv[1:5]
state = os.environ["FAKE_FORGE_STATE"]
prs = json.load(open(state))
pr = next((p for p in prs.values() if p["source"] == branch and p["state"] == "open"), None)
if pr is None or pr.get("draft", True):
    sys.exit("merge refused: " + branch + "'s pull request is a draft")
def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
tip = git("ls-remote", "--heads", "origin", "refs/heads/" + branch).split()
if not tip or tip[0] != expected:
    sys.exit(branch + " moved after its check")
git("fetch", "--quiet", "origin", "+refs/heads/" + branch + ":refs/merge-queue/candidate")
tree = git("rev-parse", expected + "^{tree}")
commit = git("commit-tree", tree, "-p", base_head, "-p", expected, "-m", "Merge " + branch)
git("push", "--quiet", "--force-with-lease=refs/heads/" + base + ":" + base_head, "origin", commit + ":refs/heads/" + base)
pr["state"] = "merged"
json.dump(prs, open(state, "w"))
'''

# The check every candidate runs records the signal dispositions it started with, and where it ran.
CHECK = r'''
import json, os, signal, time
time.sleep(float(os.environ.get("FX_CHECK_SECONDS", "0")))
out = os.environ.get("FX_SIGNALS")
if out:
    ignored = [name for name in ("SIGINT", "SIGHUP", "SIGQUIT") if signal.getsignal(getattr(signal, name)) == signal.SIG_IGN]
    with open(out, "a") as log:
        log.write(json.dumps({"cwd": os.getcwd(), "ignored": ignored}) + "\n")
'''

FULL_HEALTH = r'''
import os, pathlib, sys, time
verdict = pathlib.Path(os.environ["FX_HEALTH"])
with open(str(verdict) + ".runs", "a") as runs:
    runs.write("run\n")
said = verdict.read_text().strip() if verdict.exists() else ""
if said == "slow":
    time.sleep(float(os.environ.get("FX_HEALTH_SECONDS", "30")))
if said == "lane-done":
    # The lane's last word lands while the pass waits on fullHealth: after its cleanup, before it reads the close.
    import subprocess
    subprocess.run([sys.executable, "-B", os.environ["FX_STATE_TOOL"], "--dir", os.environ["FX_RECORD"], "focus",
                    "--lane", "lane-1", "--text", "done", "--state", "done"], check=True, capture_output=True)
if said == "fail":
    print("fullHealth: test_parser failed")
sys.exit(1 if said == "fail" else 0)
'''

# Holds a lock as a supervisor would, recording what it is given.
HOLDER = r'''
import fcntl, sys, time
lock = open(sys.argv[1], "a+")
fcntl.flock(lock, fcntl.LOCK_EX)
lock.truncate(0)
lock.write(sys.argv[2])
lock.flush()
print("holding", flush=True)
time.sleep(120)
'''

SNAPSHOT = r'''
import datetime, json
print(json.dumps({"takenAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "source": "forge", "items": []}))
'''


def supervised(fx: Fixture, *, grant_ready: bool = True, merge_needs_ready: bool = True) -> Path:
    """The fixture delivery, with a binding whose queue merges for real and a merge step that refuses drafts."""
    path = setup_delivery(fx)
    m = fx.main
    tools = {"fake_forge.py": FORGE, "candidate.py": CANDIDATE, "merge.py": MERGE, "check.py": CHECK,
             "full_health.py": FULL_HEALTH, "snapshot.py": SNAPSHOT}
    for name, text in tools.items():
        (m / "tools" / name).write_text(text)
    py = lambda name: [sys.executable, "-B", f"tools/{name}"]
    binding = doctor_binding()
    binding.update(candidate=[*py("candidate.py"), "{candidate}"], merge=[*py("merge.py"), "{base}", "{branch}",
                   "{expectedHead}", "{baseHead}"], fullHealth=py("full_health.py"))
    binding["fallback"] = [{"id": "check", "argv": py("check.py"), "cwd": ".", "reasons": ["every candidate"],
                            "prerequisites": []}]
    binding["state"] = {"directory": ".orchestration/state", "publishers": [], "renderers": [],
                        "forgeSnapshot": {"argv": py("snapshot.py")}, "refreshSeconds": 0}
    (m / ".agents/orchestration.json").write_text(json.dumps(binding, indent=2))
    conf_path = m / ".agents/run-delivery.json"
    conf = json.loads(conf_path.read_text())
    for name in ("inApp", "gates"):
        conf.pop(name, None)
    conf["forge"] = {**conf["forge"], **({"mergeNeedsReady": True} if merge_needs_ready else {})}
    conf["supervisor"] = {"passSeconds": 1}
    conf["cleanup"] = {"archive": str(fx.tmp / "archive/{delivery}")}
    conf_path.write_text(json.dumps(conf, indent=2))
    d = json.loads(path.read_text())
    d["stories"]["FX-3"].pop("inApp", None)
    if grant_ready:
        d["authority"]["readyPullRequests"] = True
    path.write_text(json.dumps(d, indent=2))
    (m / ".gitignore").write_text(".orchestration/\nout/\nlanes/\n")
    commit(m, "FX-1: a binding whose queue merges")
    sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
    fx.env.update(FX_SIGNALS=str(fx.tmp / "signals.jsonl"), FX_HEALTH=str(fx.tmp / "health"))
    return path


def running(fx: Fixture) -> dict[str, str]:
    """The fixture's environment with the supervisor allowed to start."""
    return {key: value for key, value in fx.env.items() if key != "RUN_DELIVERY_SUPERVISOR"}


def hand_off(fx: Fixture, key: str, lane: str, assign: bool = True, deferred: bool = False) -> Path:
    """A lane's whole flow for one story: its change, its gate, one approved review round (with one finding deferred
    to a follow-up, when asked), its draft PR, its hand-off."""
    m = fx.main
    if assign:
        code, out = fx.flow("lead-assign", key, "--lane", lane, "--title", lane, cwd=m)
        assert code == 0, out
    wt = fx.lane(key)
    name = {"lane-1": "one", "lane-2": "two"}[lane]
    (wt / f"src/{name}.txt").write_text(f"{name}\n")
    commit(wt, f"{key}: {name}")
    review = wt / ".orchestration/review" / key
    review.mkdir(parents=True, exist_ok=True)
    (review / "context.md").write_text("- fixture evidence\n")
    for argv in (("gate", key), ("review-prepare", key, "--round", "1")):
        code, out = fx.flow(*argv, cwd=wt)
        assert code == 0, out
    lenses = fx.lenses(wt)
    write_results(review, 1, key, lenses)
    if deferred:
        short = lenses[0][1][0]
        path = review / f"result-r1-{short}.json"
        result = json.loads(path.read_text())
        result["findings"] = [{"id": f"{short}-r1-1", "severity": "P3", "summary": "later", "state": "deferred",
                               "deferredIssue": "Tidy the one copy"}]
        path.write_text(json.dumps(result))
    code, out = fx.flow("review-reduce", key, "--round", "1", cwd=wt)
    assert code == 0, out
    sh("git", "push", "-q", "-u", "origin", f"HEAD:feature/{key}", cwd=wt)
    body = fx.tmp / f"{key}.md"
    body.write_text(f"{key}\n")
    code, out = fx.flow("open-pr", key, "--title", f"{key}: {name}", "--body-file", body, cwd=wt)
    assert code == 0, out
    url = next(u for u, pr in json.loads((fx.tmp / "forge.json").read_text()).items() if pr["source"] == f"feature/{key}")
    code, out = fx.flow("handoff", key, "--pr", url, cwd=wt)
    assert code == 0, out
    return wt


def record(fx: Fixture) -> dict:
    return json.loads(sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py",
                         "--dir", fx.tmp / "record", "show").stdout)


def state_tool(fx: Fixture, *argv) -> None:
    sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir",
       fx.tmp / "record", *argv, cwd=fx.main)


def kinds(fx: Fixture, kind: str) -> list[dict]:
    return [e for e in record(fx)["journal"] if e["kind"] == kind]


def passes(fx: Fixture) -> list[dict]:
    log = fx.tmp / "flow-log/FX-1.supervisor.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def listed(case: unittest.TestCase, last: dict) -> dict:
    """A pass, after checking that every "Needs you" item it found says whether it holds, where it comes from and
    what clears it: the line map refuses a whole list over one item that doesn't."""
    for item in last.get("needsYou", []):
        case.assertIs(type(item.get("holding")), bool, item)
        for name in ("source", "text", "clears"):
            case.assertTrue(isinstance(item.get(name), str) and item[name].strip(), (name, item))
    return last


def queue_run(fx: Fixture, key: str) -> None:
    """The merge queue, run once by hand, as a lead does with the supervisor stopped."""
    sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/merge_queue.py", "--repo", fx.main,
       "--state", fx.tmp / "record", "--remote", "origin", "--base", "feature/FX-1", "run", key, cwd=fx.main,
       env=fx.env, check=False)


def wait_for(condition, what: str, seconds: float = 120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError(f"{what} didn't happen within {seconds}s")


def lock_of(fx: Fixture) -> dict:
    path = fx.tmp / "flow-log/FX-1.supervisor.lock"
    return json.loads(path.read_text() or "{}") if path.exists() else {}


def settled_lock(fx: Fixture, seconds: float = 10) -> dict:
    """The supervisor's lock as written: `write_lock` truncates and then writes on every restart, so a read can land
    between the two and find it empty. Retries within the bound, and fails naming the file past it."""
    deadline = time.monotonic() + seconds
    while True:
        lock = lock_of(fx)
        if lock:
            return lock
        if time.monotonic() >= deadline:
            raise AssertionError(f"{fx.tmp / 'flow-log/FX-1.supervisor.lock'} stayed empty for {seconds}s")
        time.sleep(0.02)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ProofStateTests(unittest.TestCase):
    def test_a_merged_item_is_past_waiting_as_the_line_map_reads_it(self) -> None:
        path = Path(__file__).resolve().parents[1] / "skills" / "run-delivery" / "scripts" / "run_delivery.py"
        spec = importlib.util.spec_from_file_location("run_delivery_for_proofs", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        def handed(item: dict) -> str:
            return module.proof_state(None, {"proofKinds": [{"id": "handed"}]}, {"state": {"queue": [{"subject": "A", **item}]}}, {"subject": "A"}, "handed")
        self.assertEqual(handed({"stage": "merged", "pending": True}), "done")
        self.assertEqual(handed({"stage": "queued", "pending": True}), "open")
        self.assertEqual(handed({"stage": "stopped"}), "open")
        self.assertEqual(handed({"stage": "queued"}), "done")


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class SupervisorProcessTests(unittest.TestCase):
    """The real supervisor: detached, holding its lock, keeping serve and the live view running."""

    run_serially = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        self.addCleanup(stop_supervisors, fx.tmp)
        return fx

    def test_one_supervisor_per_record_and_its_first_line_is_the_live_view(self) -> None:
        fx = self.fixture()
        supervised(fx)
        m = fx.main
        code, out = fx.flow("lead-init", cwd=m, env=running(fx))
        self.assertEqual(code, 0, out)
        first = lock_of(fx)
        self.assertIn(f"supervisor: running; live view {first['address']}", out)
        self.assertTrue(first["address"].startswith("http://127.0.0.1:"))
        log = fx.tmp / "flow-log/FX-1.supervisor.log"
        self.assertEqual(log.read_text().splitlines()[0], first["address"], "the first line isn't the live view")
        with urllib.request.urlopen(first["address"], timeout=10) as page:
            self.assertEqual(page.status, 200)
        self.assertEqual(sorted(child["name"] for child in first["children"]), ["live", "serve"])
        # Each pass reconciles, renders and copies the record to `latest`.
        done = wait_for(lambda: [p for p in passes(fx) if p.get("render") == "rendered"], "a rendered pass")[0]
        self.assertTrue(any(line.startswith("forgeSnapshot:") for line in done["reconcile"]), done)
        latest = fx.tmp / "archive/FX-1/latest"
        self.assertTrue((latest / "record/state.json").exists() and (latest / "flow-log").is_dir(), done)
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertIn(f"supervisor: running (pid {first['pid']}); live view {first['address']}", out)
        code, out = fx.flow("lead-close", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("journal delivery.closed first", out)
        self.assertTrue(alive(first["pid"]) and lock_of(fx)["pid"] == first["pid"], "a refused lead-close stopped it")
        # A second supervisor and a second lead-init are refused; --replace swaps the first for one other.
        code, out = fx.flow("lead-run", cwd=m, env=running(fx))
        self.assertEqual(code, 2, out)
        self.assertIn(f"a supervisor already runs (pid {first['pid']})", out)
        code, out = fx.flow("lead-init", cwd=m, env=running(fx))
        self.assertEqual(code, 2, out)
        code, out = fx.flow("lead-init", "--replace", cwd=m, env=running(fx))
        self.assertEqual(code, 0, out)
        second = lock_of(fx)
        self.assertNotEqual(second["pid"], first["pid"])
        for pid in [first["pid"], *(child["pid"] for child in first["children"])]:
            wait_for(lambda: not alive(pid), f"the first supervisor's process {pid} to stop", 30)
        self.assertEqual(len(list(fx.tmp.rglob("*.supervisor.lock"))), 2, "the archive keeps the first lock")

    def test_children_start_with_default_signals_and_an_accepted_draft_is_made_ready_and_merged(self) -> None:
        fx = self.fixture()
        supervised(fx)
        m = fx.main
        # Started from a parent that ignores SIGINT, SIGHUP and SIGQUIT, as nohup and a background `&` leave them.
        ignoring = lambda: [signal.signal(number, signal.SIG_IGN)
                            for number in (signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)]
        done = subprocess.run([sys.executable, "-B", str(fx.tool), "lead-init"], cwd=m, env=running(fx), text=True,
                              capture_output=True, preexec_fn=ignoring, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        hand_off(fx, "FX-2", "lane-1")
        code, out = fx.flow("lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertFalse(json.loads((fx.tmp / "forge.json").read_text())["https://forge.test/pr/1"]["draft"],
                         "lead-accept didn't mark the draft ready")
        wait_for(lambda: kinds(fx, "candidate.merged"), "serve merging FX-2")
        self.assertEqual([e for e in kinds(fx, "candidate.stopped")], [], "the merge step refused the candidate")
        runs = [json.loads(line) for line in (fx.tmp / "signals.jsonl").read_text().splitlines()]
        under_serve = [r for r in runs if "/.orchestration/work" in r["cwd"]]
        self.assertTrue(under_serve, runs)
        self.assertEqual([r["ignored"] for r in under_serve], [[]] * len(under_serve))

    def test_doctor_names_a_child_that_ignores_a_signal_where_the_host_shows_it(self) -> None:
        fx = self.fixture()
        supervised(fx)
        m = fx.main
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        ignorer = subprocess.Popen(["sleep", "60"], start_new_session=True,
                                   preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
        self.addCleanup(ignorer.wait)
        self.addCleanup(ignorer.kill)
        lock = fx.tmp / "flow-log/FX-1.supervisor.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen([sys.executable, "-B", "-c", HOLDER, str(lock), json.dumps(
            {"pid": os.getpid(), "startedAt": "2026-01-01T00:00:00Z", "address": "http://127.0.0.1:1/",
             "children": [{"name": "serve", "pid": ignorer.pid}]})], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "holding")
        holder.stdout.close()
        code, out = fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=m)
        line = next(l for l in out.splitlines() if "delivery supervisor:" in l)
        if Path("/proc/self/status").exists():
            self.assertIn(f"serve (pid {ignorer.pid}) ignores SIGINT", line)
            self.assertTrue(line.startswith("wrong"), line)
        else:
            self.assertIn("can't be read on this host", line)
            self.assertTrue(line.startswith("ok"), line)

    def test_a_killed_supervisor_is_reported_and_resume_stops_its_orphans_and_repeats_nothing(self) -> None:
        fx = self.fixture()
        supervised(fx)
        m = fx.main
        code, out = fx.flow("lead-init", cwd=m, env=running(fx))
        self.assertEqual(code, 0, out)
        wait_for(lambda: passes(fx), "a first pass")
        first = lock_of(fx)
        os.kill(first["pid"], signal.SIGKILL)
        wait_for(lambda: not alive(first["pid"]), "the supervisor to die")
        orphans = [child["pid"] for child in first["children"]]
        self.assertTrue(all(alive(pid) for pid in orphans), "its children died with it")
        code, out = fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=m)
        self.assertIn("the supervisor is down; fix:", out)
        self.assertIn("lead-run --resume", out)
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertIn("supervisor: down; restart it with", out)
        code, out = fx.flow("lead-run", cwd=m, env=running(fx))
        self.assertEqual(code, 2, out)
        self.assertIn("left children running", out)
        decisions = len(kinds(fx, "decision.recorded"))
        code, out = fx.flow("lead-run", "--resume", cwd=m, env=running(fx))
        self.assertEqual(code, 0, out)
        for pid in orphans:
            self.assertIn(f"(pid {pid}), left by the earlier supervisor (pid {first['pid']})", out)
            wait_for(lambda: not alive(pid), f"orphan {pid} to stop", 30)
        second = lock_of(fx)
        self.assertNotEqual(second["pid"], first["pid"])
        count = len(passes(fx))
        wait_for(lambda: len(passes(fx)) > count + 1, "two more passes")
        self.assertEqual(len(kinds(fx, "decision.recorded")), decisions, "a resumed supervisor journalled again")

    def test_a_running_supervisor_restarts_its_children_and_reports_one_that_wont_stay_up(self) -> None:
        fx = self.fixture()
        supervised(fx)
        m = fx.main
        path = m / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["finish"] = []
        path.write_text(json.dumps(d, indent=2))
        conf_path = m / ".agents/run-delivery.json"
        conf_path.write_text(json.dumps({**json.loads(conf_path.read_text()), "supervisor": {"passSeconds": 4}}))
        commit(m, "FX-1: no finish lines, a pass every four seconds")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", cwd=m, env=running(fx))
        self.assertEqual(code, 0, out)
        wait_for(lambda: passes(fx), "a first pass")
        serve = lambda: next(child["pid"] for child in settled_lock(fx)["children"] if child["name"] == "serve")
        for _ in range(3):
            before = serve()
            os.kill(before, signal.SIGKILL)
            wait_for(lambda: serve() != before and alive(serve()), "serve to be restarted", 30)
        # Three exits in the window: a fourth rests until a pass's interval has gone by since the last restart.
        before = serve()
        os.kill(before, signal.SIGKILL)
        time.sleep(1.5)
        self.assertEqual(serve(), before, "a child that won't stay up was restarted at once")
        wait_for(lambda: serve() != before and alive(serve()), "serve to be restarted after its rest", 30)
        seen = len(passes(fx))
        flapping = wait_for(lambda: [p for p in passes(fx)[seen:] if any("serve won't stay up" in item["text"]
                                                                         for item in p.get("needsYou", []))],
                            "a pass reporting serve won't stay up", 60)
        self.assertTrue(flapping)
        self.assertTrue(alive(settled_lock(fx)["pid"]), "a child that won't stay up ended the supervisor")
        # A restart that fails is logged, and the supervisor carries on.
        binding_path = m / ".agents/orchestration.json"
        good = binding_path.read_text()
        binding = json.loads(good)
        binding["state"]["renderers"] = "not a list"
        binding_path.write_text(json.dumps(binding))
        live = next(child["pid"] for child in settled_lock(fx)["children"] if child["name"] == "live")
        os.kill(live, signal.SIGKILL)
        log = fx.tmp / "flow-log/FX-1.supervisor.live.log"
        wait_for(lambda: "restart failed" in log.read_text(), "the failed restart to be logged", 60)
        self.assertTrue(alive(settled_lock(fx)["pid"]), "a failed restart ended the supervisor")
        binding_path.write_text(good)

    def test_lead_close_stops_a_running_supervisor_after_its_pass(self) -> None:
        fx = self.fixture()
        supervised(fx)
        m = fx.main
        code, out = fx.flow("lead-init", cwd=m, env=running(fx))
        self.assertEqual(code, 0, out)
        wait_for(lambda: passes(fx), "a first pass")
        first = lock_of(fx)
        state_tool(fx, "journal", "--kind", "delivery.closed", "--summary", "closed early")
        code, out = fx.flow("lead-close", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn(f"stopped the supervisor (pid {first['pid']}) after its pass", out)
        for pid in [first["pid"], *(child["pid"] for child in first["children"])]:
            wait_for(lambda: not alive(pid), f"process {pid} to stop", 30)
        self.assertIn("supervisor: stopped; the delivery is closed. Archive", out)
        code, out = fx.flow("lead-run", "--resume", cwd=m, env=running(fx))
        self.assertEqual(code, 2, out)
        self.assertIn("the delivery is closed: lead-close closed it", out)

    def test_a_supervisor_that_closes_the_delivery_stops(self) -> None:
        fx = self.fixture()
        supervised(fx)
        m = fx.main
        path = m / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["finish"] = []
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: no finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", cwd=m, env=running(fx))
        self.assertEqual(code, 0, out)
        hand_off(fx, "FX-2", "lane-1")
        code, out = fx.flow("lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 0, out)
        wait_for(lambda: kinds(fx, "candidate.merged"), "serve merging FX-2")
        first = lock_of(fx)
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "done", "--state", "done")
        # While it runs, every lead command and doctor say what the close waits on now: the latest pass, not the first.
        latest = lambda p: p.get("closeHeldBy") == ["not handed off: FX-3"]
        wait_for(lambda: passes(fx) and latest(passes(fx)[-1]), "a pass held only by FX-3")
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertIn(f"supervisor: running (pid {first['pid']}); live view {first['address']}. The close waits on: "
                      "not handed off: FX-3", out)
        code, out = fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=m)
        row = next(line for line in out.splitlines() if "delivery supervisor:" in line)
        self.assertIn("The close waits on: not handed off: FX-3", row)
        with open(fx.tmp / "flow-log/plan-changes.jsonl", "a") as changes:
            changes.write(json.dumps({"type": "change", "id": "c1", "kind": "removed", "subjects": ["FX-3"], "by": "lead",
                                      "why": "Out of scope.", "at": "2026-01-01T00:00:00Z"}) + "\n")
        wait_for(lambda: (fx.tmp / "flow-log/FX-1.closed.json").exists(), "the supervisor to close the delivery")
        for pid in [first["pid"], *(child["pid"] for child in first["children"])]:
            wait_for(lambda: not alive(pid), f"process {pid} to stop once the delivery closed", 30)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1)


class SettledLockTests(unittest.TestCase):
    """A read during a restart finds the truncated file: the test's reads retry, within their bound."""

    def fixture(self) -> tuple[types.SimpleNamespace, Path]:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        lock = Path(folder.name) / "flow-log/FX-1.supervisor.lock"
        lock.parent.mkdir()
        lock.write_text("")
        return types.SimpleNamespace(tmp=Path(folder.name)), lock

    def test_a_read_during_a_restart_waits_for_the_write(self) -> None:
        fx, lock = self.fixture()
        writer = threading.Timer(0.2, lock.write_text, ['{"pid": 7, "children": []}'])
        writer.start()
        self.addCleanup(writer.join)
        self.assertEqual(lock_of(fx), {}, "the plain read this guards against no longer finds the race")
        self.assertEqual(settled_lock(fx)["children"], [])

    def test_a_lock_that_stays_empty_fails_within_the_bound(self) -> None:
        fx, lock = self.fixture()
        started = time.monotonic()
        with self.assertRaisesRegex(AssertionError, "stayed empty for 0.2s"):
            settled_lock(fx, seconds=0.2)
        self.assertLess(time.monotonic() - started, 5)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class SupervisorPassTests(unittest.TestCase):
    """One pass at a time, with `lead-run --once`: acceptance, the finish lines, "Needs you" and the close-out."""

    run_serially = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self, **settings) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        self.addCleanup(stop_supervisors, fx.tmp)
        supervised(fx, **settings)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return fx

    def once(self, fx: Fixture) -> dict:
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return listed(self, passes(fx)[-1])

    def merged(self, fx: Fixture, key: str, lane: str) -> Path:
        wt = hand_off(fx, key, lane)
        code, out = fx.flow("lead-accept", key, cwd=fx.main)
        self.assertEqual(code, 0, out)
        queue_run(fx, key)
        self.assertTrue([e for e in kinds(fx, "candidate.merged") if e.get("subject") == key], record(fx)["journal"][-3:])
        return wt

    def test_a_failed_ready_leaves_the_hand_off_pending_and_names_the_adapter(self) -> None:
        fx = self.fixture()
        hand_off(fx, "FX-2", "lane-1")
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main, env={**fx.env, "FAKE_FORGE_READY": "refuse"})
        self.assertEqual(code, 1, out)
        self.assertIn("couldn't be marked ready, so the hand-off stays pending", out)
        self.assertIn("tools/fake_forge.py", out)
        item = next(q for q in record(fx)["state"]["queue"] if q["subject"] == "FX-2")
        self.assertTrue(item.get("pending"), item)
        # An acceptance refused for another reason never marks the draft ready.
        wt = fx.main / "lanes/FX-2"
        (wt / "src/one.txt").write_text("one, moved on\n")
        commit(wt, "FX-2: moved after its hand-off")
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-2", cwd=wt)
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 1, out)
        self.assertIn("is not at the handed head", out)
        self.assertTrue(json.loads((fx.tmp / "forge.json").read_text())["https://forge.test/pr/1"]["draft"],
                        "a refused acceptance marked the draft ready")

    def merged_by_hand(self, fx: Fixture, key: str, lane: str) -> Path:
        """A story handed off and waiting for the lead, then merged outside the queue with its head landing on the base."""
        wt = hand_off(fx, key, lane)
        head = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        sh("git", "push", "-q", "origin", "HEAD:refs/heads/feature/FX-1", cwd=wt)
        forge = json.loads((fx.tmp / "forge.json").read_text())
        url = next(u for u, pr in forge.items() if pr["source"] == f"feature/{key}")
        forge[url].update(state="merged", head=head)
        (fx.tmp / "forge.json").write_text(json.dumps(forge))
        (fx.main / "tools/snapshot.py").write_text(
            "import datetime, json\n"
            "print(json.dumps({'takenAt': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), "
            f"'source': 'forge', 'items': [{{'subject': {key!r}, 'state': 'merged', 'head': {head!r}, "
            f"'changeRequest': {url!r}}}]}}))\n")
        return wt

    def test_a_merge_before_the_lead_accepted_keeps_its_checkout_and_says_so_until_accepted(self) -> None:
        fx = self.fixture()
        wt = self.merged_by_hand(fx, "FX-2", "lane-1")
        last = self.once(fx)
        self.assertTrue(wt.exists(), "the pass removed the checkout lead-accept reads")
        self.assertIn("FX-2: waits for the lead's decision on its merge", last["cleanup"])
        self.assertTrue(any("before the lead accepted it" in line for line in last["reconcile"]), last["reconcile"])
        waiting = [item for item in last["needsYou"] if "merged before you accepted" in item["text"]]
        self.assertEqual([item["holding"] for item in waiting], [True], last["needsYou"])
        self.assertIn("(candidate.merged)", waiting[0]["source"])
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        last = self.once(fx)
        self.assertEqual([item for item in last["needsYou"] if "merged before you accepted" in item["text"]], [])
        self.assertFalse(wt.exists(), "the checkout outlived the lead's decision")

    def test_a_journaled_decision_also_ends_the_wait_on_a_merge_before_the_lead_accepted(self) -> None:
        fx = self.fixture()
        wt = self.merged_by_hand(fx, "FX-2", "lane-1")
        self.once(fx)
        self.assertTrue(wt.exists())
        state_tool(fx, "journal", "--kind", "decision.recorded", "--subject", "FX-3", "--summary", "About other work")
        last = self.once(fx)
        self.assertTrue(wt.exists(), "a decision on other work released this checkout")
        self.assertTrue([item for item in last["needsYou"] if "merged before you accepted" in item["text"]])
        state_tool(fx, "journal", "--kind", "decision.recorded", "--subject", "FX-2", "--summary", "Merged as it stands")
        last = self.once(fx)
        self.assertEqual([item for item in last["needsYou"] if "merged before you accepted" in item["text"]], [])
        self.assertFalse(wt.exists())

    def test_lead_cleanup_leaves_the_checkout_a_decision_still_needs(self) -> None:
        fx = self.fixture()
        wt = self.merged_by_hand(fx, "FX-2", "lane-1")
        self.once(fx)
        code, out = fx.flow("lead-cleanup", "FX-2", cwd=fx.main)
        self.assertEqual(code, 1, out)
        self.assertIn("waits for the lead's decision", out)
        code, out = fx.flow("lead-cleanup", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("FX-2: waits for the lead's decision", out)
        self.assertIn("FX-3:", out, "the sweep stopped at the story it held")
        self.assertTrue(wt.exists(), "lead-cleanup removed the checkout lead-accept reads")
        self.assertTrue([item for item in self.once(fx)["needsYou"] if "merged before you accepted" in item["text"]],
                        "cleaning up released the hold with no decision")
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-cleanup", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertFalse(wt.exists())

    def test_what_counts_as_a_merge_still_awaiting_the_lead(self) -> None:
        fx = self.fixture()
        skipped = "Recorded from a forge snapshot: merged at x, not recorded by the runner before the lead accepted it"
        merge = lambda id, summary=skipped, subject="S": {"id": id, "kind": "candidate.merged", "subject": subject, "summary": summary}
        decision = lambda id, subject="S": {"id": id, "kind": "decision.recorded", "subject": subject, "summary": "d"}
        item = {"subject": "S", "stage": "merged", "head": "h1"}
        log = fx.tmp / "flow-log/S.acceptance.jsonl"
        for name, journal, accepted, queued, awaits in (
                ("a merge that skipped the lead", [merge(5)], None, item, 5),
                ("a merge through the queue, unaccepted", [merge(5, "Merged at x")], None, item, None),
                ("no merge entry yet", [], None, item, None),
                ("another story's skipped merge", [merge(5, subject="T")], None, item, None),
                ("a decision follows it", [merge(5), decision(6)], None, item, None),
                ("a decision before it", [decision(4), merge(5)], None, item, 5),
                ("a decision on another story", [merge(5), decision(6, "T")], None, item, 5),
                ("a later entry of another kind", [merge(5), {"id": 6, "kind": "review.round", "subject": "S", "summary": "r"}],
                 None, item, 5),
                ("the latest merge is what counts, skipped", [merge(4, "Merged at x"), merge(5)], None, item, 5),
                ("the latest merge is what counts, not skipped", [merge(4), merge(5, "Merged at x")], None, item, None),
                ("an acceptance at its head", [merge(5)], "h1", item, None),
                ("an acceptance at another head", [merge(5)], "h0", item, 5),
                ("put back in the queue since", [merge(5)], None, {**item, "stage": "queued"}, None)):
            with self.subTest(name):
                log.unlink(missing_ok=True)
                if accepted:
                    log.write_text(json.dumps({"head": accepted}) + "\n")
                found = fx.call("unaccepted_merge", {"state": {"queue": []}, "journal": journal}, queued, cwd=fx.main,
                                delivery="FX-1")
                self.assertEqual(found and found["id"], awaits)

    def test_a_reconciles_reports_are_read_from_its_output_and_anything_else_reports_nothing(self) -> None:
        fx = self.fixture()
        for output, lines in ((json.dumps({"reports": [{"subject": "S", "note": "n"}, {"subject": None, "note": "m"}]}),
                               ["S: n", "the record: m"]),
                              (json.dumps({"ok": False, "code": "x"}), []), ("", []), (None, []), ("not json", []), ("[1]", [])):
            with self.subTest(output=output):
                self.assertEqual(fx.call("reconcile_report_lines", output, cwd=fx.main), lines)

    def test_a_merge_through_the_queue_needs_no_decision_and_cleans_up(self) -> None:
        fx = self.fixture()
        wt = self.merged(fx, "FX-2", "lane-1")
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "done", "--state", "done")
        last = self.once(fx)
        self.assertEqual([item for item in last["needsYou"] if "merged before you accepted" in item["text"]], [])
        self.assertFalse(wt.exists())

    def test_a_leftover_wait_on_a_merged_item_does_not_open_its_handed_proof(self) -> None:
        fx = self.fixture()
        doc = {"state": {"queue": [{"subject": subject, "stage": stage, **({"pending": True} if waits else {})}
                                   for subject, stage, waits in (("left", "merged", True), ("done", "merged", False),
                                                                 ("waits", "queued", True), ("taken", "queued", False),
                                                                 ("stopped", "stopped", False))]}, "journal": []}
        states = {subject: fx.call("proof_state", None, {}, doc, {"subject": subject}, "handed", cwd=fx.main)
                  for subject in ("left", "done", "waits", "taken", "stopped", "absent")}
        self.assertEqual(states, {"left": "done", "done": "done", "waits": "open", "taken": "done", "stopped": "open",
                                  "absent": "open"})

    def closable(self, fx: Fixture) -> None:
        """No finish lines, FX-2 merged, its lane done, FX-3 removed: nothing holds the close."""
        path = fx.main / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["finish"] = []
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: no finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", "--replace", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.merged(fx, "FX-2", "lane-1")
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "done", "--state", "done")
        with open(fx.tmp / "flow-log/plan-changes.jsonl", "a") as changes:
            changes.write(json.dumps({"type": "change", "id": "c1", "kind": "removed", "subjects": ["FX-3"], "by": "lead",
                                      "why": "Out of scope.", "at": "2026-01-01T00:00:00Z"}) + "\n")

    def holds_alone(self, fx: Fixture, words: str) -> None:
        """One pass, held by exactly what the case opened, and the delivery isn't closed."""
        held = self.once(fx)["closeHeldBy"]
        self.assertTrue(any(words in h for h in held), held)
        self.assertEqual(kinds(fx, "delivery.closed"), [], "the delivery closed while something held it")

    def test_each_condition_holds_the_close_on_its_own(self) -> None:
        fx = self.fixture()
        self.closable(fx)
        lane = lambda *argv: state_tool(fx, "focus", "--lane", "lane-1", "--text", "x", *argv)
        lane("--state", "working")
        self.holds_alone(fx, "lanes not idle or done: lane-1")
        lane("--state", "blocked")
        self.holds_alone(fx, "lane-1 is blocked")
        lane("--state", "done", "--waiting-on", "lead")
        self.holds_alone(fx, "lane-1 waits on you")
        lane("--state", "done", "--waiting-on", "")
        doc = record(fx)
        state_tool(fx, "patch", "--writer", "lead", "--version", str(doc["state"]["version"]), "--patch",
                   json.dumps({"finish": [{"id": "late", "text": "A late line", "met": False}]}))
        self.holds_alone(fx, "finish lines not met: late")
        doc = record(fx)
        state_tool(fx, "patch", "--writer", "lead", "--version", str(doc["state"]["version"]), "--patch",
                   json.dumps({"finish": []}))
        # A handed-off story that isn't merged: FX-3 back in the plan, handed off and accepted.
        log = fx.tmp / "flow-log/plan-changes.jsonl"
        log.write_text("".join(line + "\n" for line in log.read_text().splitlines() if '"removed"' not in line))
        hand_off(fx, "FX-3", "lane-2")
        code, out = fx.flow("lead-accept", "FX-3", cwd=fx.main)
        self.assertEqual(code, 0, out)
        state_tool(fx, "focus", "--lane", "lane-2", "--text", "done", "--state", "done")
        self.holds_alone(fx, "not merged: FX-3")
        queue_run(fx, "FX-3")
        self.once(fx)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, passes(fx)[-1])

    def test_what_the_close_waits_on_is_cut_to_five_and_a_pass_still_writing_is_passed_over(self) -> None:
        fx = self.fixture()
        log = fx.tmp / "flow-log/FX-1.supervisor.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        held = [f"hold {n}" for n in range(1, 8)]
        log.write_text(json.dumps({"pass": 1, "closeHeldBy": held}) + "\n")
        code, out = fx.flow("lead-brief", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("The close waits on: hold 1; hold 2; hold 3; hold 4; hold 5; and 2 more", out)
        self.assertNotIn("hold 6", out)
        # A pass still writing its line: the line is passed over, never a crash.
        with open(log, "a") as partial:
            partial.write('{"pass": 2, "closeHeldBy": ["hold')
        code, out = fx.flow("lead-brief", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertNotIn("The close waits on", out)

    def test_accepted_drafts_hold_the_close_when_the_merge_step_refuses_them(self) -> None:
        fx = self.fixture(grant_ready=False, merge_needs_ready=False)
        hand_off(fx, "FX-2", "lane-1")
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        conf_path = fx.main / ".agents/run-delivery.json"
        conf = json.loads(conf_path.read_text())
        conf["forge"]["mergeNeedsReady"] = True
        conf_path.write_text(json.dumps(conf, indent=2))
        commit(fx.main, "FX-1: the merge step refuses drafts now")
        self.holds_alone(fx, "Accepted as drafts while the merge step refuses drafts: FX-2")

    def test_the_runner_s_blocker_clears_with_its_hold_or_with_base_repaired(self) -> None:
        fx = self.fixture()
        self.closable(fx)
        # A lane still working keeps the delivery open throughout, so each pass shows what else holds.
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "working", "--state", "working")
        held = lambda reason: state_tool(fx, "patch", "--writer", "runner", "--version",
                                         str(record(fx)["state"]["version"]), "--set",
                                         json.dumps({"/meta/hold": {"reason": reason, "since": "2026-01-01T00:00:00Z"}}))
        # Cleared by the hold clearing, with no base.repaired.
        held("the setup broke")
        state_tool(fx, "journal", "--kind", "blocker.recorded", "--summary", "Serving is held: the setup broke.")
        self.holds_alone(fx, "Serving is held: the setup broke")
        runner = [item for item in passes(fx)[-1]["needsYou"] if "Serving is held" in item["text"]]
        self.assertEqual(len(runner), 1, passes(fx)[-1]["needsYou"])
        self.assertTrue(runner[0]["clears"].startswith("the runner's hold clearing, journalling base.repaired, or "), runner)
        state_tool(fx, "patch", "--writer", "lead", "--version", str(record(fx)["state"]["version"]), "--set",
                   json.dumps({"/meta/hold": None}))
        self.assertFalse(any("Serving is held" in h for h in self.once(fx)["closeHeldBy"]))
        # Cleared by base.repaired, with the hold still set.
        held("the base broke")
        state_tool(fx, "journal", "--kind", "blocker.recorded", "--summary", "Serving is held: the base broke.")
        self.holds_alone(fx, "Serving is held: the base broke")
        state_tool(fx, "journal", "--kind", "base.repaired", "--summary", "repaired")
        self.assertFalse(any("Serving is held" in h for h in self.once(fx)["closeHeldBy"]))

    def test_a_merge_step_that_refuses_drafts_needs_the_grant(self) -> None:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        supervised(fx, grant_ready=False)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("forge.mergeNeedsReady says the merge step refuses a draft", out)
        self.assertIn('add "readyPullRequests": true to `authority`', out)
        code, out = fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=fx.main)
        self.assertIn("wrong    pull requests marked ready: forge.mergeNeedsReady says", out)

    def test_a_stopped_candidate_merges_after_requeue_without_a_new_hand_off(self) -> None:
        fx = self.fixture(grant_ready=False, merge_needs_ready=False)
        wt = hand_off(fx, "FX-2", "lane-1")
        code, out = fx.flow("lead-accept", "--requeue", "FX-2", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("isn't stopped (queued)", out)
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        # The base moves first, so the queue syncs the candidate and pushes it before its merge step refuses it.
        (fx.main / "docs/elsewhere.md").write_text("moved\n")
        commit(fx.main, "FX-1: the base moves on")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        queue_run(fx, "FX-2")
        item = next(q for q in record(fx)["state"]["queue"] if q["subject"] == "FX-2")
        self.assertTrue(item.get("syncedHead") and item["syncedHead"] != item["head"], item)
        stopped = kinds(fx, "candidate.stopped")
        self.assertTrue(stopped and "merge_refused" in stopped[-1]["summary"], stopped)
        handed = len(kinds(fx, "candidate.handed"))
        forge = json.loads((fx.tmp / "forge.json").read_text())
        forge["https://forge.test/pr/1"]["draft"] = False  # the lead marks it ready by hand
        (fx.tmp / "forge.json").write_text(json.dumps(forge))
        # A branch that moved on from both the handed and the synced head is the lane's to hand off again.
        synced = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        sh("git", "fetch", "-q", "origin", "feature/FX-2", cwd=wt)
        sh("git", "reset", "-q", "--hard", "origin/feature/FX-2", cwd=wt)
        (wt / "src/one.txt").write_text("one, again\n")
        commit(wt, "FX-2: moved on")
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-2", cwd=wt)
        code, out = fx.flow("lead-accept", "--requeue", "FX-2", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("the lane hands it off again", out)
        sh("git", "push", "-q", "-f", "origin", f"{item['syncedHead']}:refs/heads/feature/FX-2", cwd=wt)
        self.assertNotEqual(synced, "")
        code, out = fx.flow("lead-accept", "--requeue", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        queue_run(fx, "FX-2")
        self.assertTrue(kinds(fx, "candidate.merged"))
        self.assertEqual(len(kinds(fx, "candidate.handed")), handed, "the requeue needed a new hand-off")
        code, out = fx.flow("lead-accept", "--requeue", "FX-2", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("isn't stopped (merged)", out)
        # A stopped candidate whose new hand-off waits for acceptance is accepted, never requeued.
        record_now = record(fx)
        state_tool(fx, "patch", "--writer", "runner", "--version", str(record_now["state"]["version"]), "--element",
                   "queue[subject=FX-2]", "--set", json.dumps({"/stage": "stopped"}))
        record_now = record(fx)
        state_tool(fx, "patch", "--writer", "lead", "--version", str(record_now["state"]["version"]), "--element",
                   "queue[subject=FX-2]", "--patch", json.dumps({"pending": True}))
        code, out = fx.flow("lead-accept", "--requeue", "FX-2", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("still waits for acceptance", out)

    def test_each_finish_line_is_journalled_once_when_its_proofs_are_done(self) -> None:
        fx = self.fixture()
        path = fx.main / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"] = [{"id": "one-works", "text": "One works"}]
        d["stories"]["FX-3"]["criteria"] = [{"id": "two-works", "text": "Two works"}]
        d["finish"] = [{"id": "one", "text": "One is done", "covers": {"criteria": ["one-works"], "proofs": ["merged"]}},
                       {"id": "two", "text": "Two is done", "covers": {"criteria": ["two-works"], "proofs": ["merged"]}},
                       {"id": "three", "text": "Two ships", "covers": {"criteria": ["two-works"], "proofs": ["merged"]}}]
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: two finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", "--replace", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.once(fx)
        self.assertEqual(kinds(fx, "finish.met"), [])
        self.merged(fx, "FX-2", "lane-1")
        # The lead journals lines two and three by hand, ahead of their proofs, one by its subject and one by its
        # summary: the supervisor journals neither again.
        state_tool(fx, "journal", "--kind", "finish.met", "--subject", "two", "--summary", "By hand, ahead of time.")
        state_tool(fx, "journal", "--kind", "finish.met", "--summary", "Finish line three met by hand.")
        for _ in range(2):
            self.once(fx)
        met = kinds(fx, "finish.met")
        self.assertEqual([e.get("subject") for e in met], ["two", None, "one"], met)
        self.assertEqual({line["id"]: line["met"] for line in record(fx)["state"]["finish"]},
                         {"one": True, "two": False, "three": False})
        self.merged(fx, "FX-3", "lane-2")
        self.once(fx)
        self.assertEqual([e.get("subject") for e in kinds(fx, "finish.met")], ["two", None, "one"])
        self.assertTrue(all(line["met"] for line in record(fx)["state"]["finish"]))

    def holds(self, fx: Fixture) -> list[str]:
        return self.once(fx)["closeHeldBy"]

    def test_a_lane_that_goes_quiet_after_its_hand_off_is_cleaned_up_when_its_story_merges(self) -> None:
        fx = self.fixture()
        code, out = fx.flow("lead-init", "--replace", cwd=fx.main)
        self.assertEqual(code, 0, out)
        # A pass saw the lane at work before it handed off; its hand-off is its last word, and no pass runs after it.
        code, out = fx.flow("lead-assign", "FX-2", "--lane", "lane-1", "--title", "lane-1", cwd=fx.main)
        self.assertEqual(code, 0, out)
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "building FX-2", "--state", "working")
        self.once(fx)
        wt = hand_off(fx, "FX-2", "lane-1", assign=False)
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        queue_run(fx, "FX-2")
        held = self.holds(fx)
        self.assertFalse(wt.exists(), "a lane quiet since its hand-off kept its checkout after the merge")
        self.assertEqual(len([e for e in kinds(fx, "decision.recorded") if e["summary"].startswith("Cleaned up FX-2")]), 1)
        self.assertFalse(any("lane-1" in h for h in held), held)

    def test_a_lane_that_says_it_is_done_after_its_hand_off_is_cleaned_up_when_its_story_merges(self) -> None:
        fx = self.fixture()
        code, out = fx.flow("lead-init", "--replace", cwd=fx.main)
        self.assertEqual(code, 0, out)
        # The lane's own last word, since its hand-off and seen by a pass before the merge, is that it is done.
        wt = hand_off(fx, "FX-2", "lane-1")
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "finished", "--state", "done")
        self.once(fx)
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        queue_run(fx, "FX-2")
        self.once(fx)
        self.assertFalse(wt.exists(), "a lane done since its hand-off kept its checkout after the merge")
        self.assertEqual(len([e for e in kinds(fx, "decision.recorded") if e["summary"].startswith("Cleaned up FX-2")]), 1)

    def test_the_close_waits_for_its_condition_and_each_failure_path_holds_it(self) -> None:
        fx = self.fixture()
        m = fx.main
        path = m / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["finish"] = []
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: no finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", "--replace", cwd=m)
        self.assertEqual(code, 0, out)
        # A planned story nobody took holds the close, and every lead command says what the close waits on.
        self.assertIn("not handed off: FX-2, FX-3", self.holds(fx))
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertIn("The close waits on: not handed off: FX-2, FX-3", out)
        # The lane is still at work on its story after handing it off, and a pass sees it say so before the merge.
        wt = hand_off(fx, "FX-2", "lane-1")
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "fixing FX-2's docs", "--state", "working")
        self.once(fx)
        code, out = fx.flow("lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 0, out)
        queue_run(fx, "FX-2")
        # The queue rests the lane's row once FX-2 merges; that is the queue's word, not the lane moving off.
        row = next(lane for lane in record(fx)["state"]["lanes"] if lane["id"] == "lane-1")
        self.assertEqual((row["state"], row.get("focusBy")), ("idle", "runner"), row)
        held = self.holds(fx)
        self.assertIn("not handed off: FX-3", held)
        # Cleanup waits until the lane moves off its merged story.
        self.assertTrue(wt.exists())
        self.assertIn("FX-2: waits for lane-1 to move off it", passes(fx)[-1]["cleanup"])
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "done", "--state", "done")
        self.once(fx)
        self.assertFalse(wt.exists(), "the merged story wasn't cleaned up once its lane moved off it")
        cleaned = [e for e in kinds(fx, "decision.recorded") if e["summary"].startswith("Cleaned up FX-2")]
        self.once(fx)
        self.assertEqual(len([e for e in kinds(fx, "decision.recorded") if e["summary"].startswith("Cleaned up FX-2")]),
                         len(cleaned), "a story already cleaned up was cleaned up again")
        # A story removed from the plan doesn't hold it.
        code, out = fx.flow("lead-plan", "change", "--kind", "removed", "--subjects", "FX-3", "--why", "Out of scope.", cwd=m)
        if code != 0:  # the plan chapter is off without criteria: the removal is recorded in the changes log by hand
            log = fx.tmp / "flow-log/plan-changes.jsonl"
            with open(log, "a") as changes:
                changes.write(json.dumps({"type": "change", "id": "c1", "kind": "removed", "subjects": ["FX-3"],
                                          "by": "lead", "why": "Out of scope.", "at": "2026-01-01T00:00:00Z"}) + "\n")
        # A lane's blocker holds it until lead-resolve resolves it.
        state_tool(fx, "journal", "--kind", "blocker.recorded", "--summary", "the API is down")
        blocker = kinds(fx, "blocker.recorded")[-1]["id"]
        held = self.holds(fx)
        self.assertTrue(any(h.startswith(f"Blocker {blocker}") for h in held), held)
        # Only the runner's own blocker clears with its hold or base.repaired; this one clears with lead-resolve alone.
        mine = [item for item in passes(fx)[-1]["needsYou"] if item["text"].startswith(f"Blocker {blocker}")]
        self.assertEqual(len(mine), 1, passes(fx)[-1]["needsYou"])
        self.assertTrue(mine[0]["clears"].endswith(f"lead-resolve {blocker} --why TEXT"), mine)
        self.assertNotIn("runner's hold", mine[0]["clears"])
        self.assertFalse(any(h.startswith("not handed off") for h in held), held)
        # A broken base holds it until it's repaired; the runner's own blocker clears with base.repaired.
        state_tool(fx, "journal", "--kind", "base.broken", "--summary", "the base fails its check")
        state_tool(fx, "journal", "--kind", "blocker.recorded", "--summary", "Serving is held: the base broke.")
        held = self.holds(fx)
        self.assertTrue(any(h.startswith("The delivery base is broken") for h in held), held)
        state_tool(fx, "journal", "--kind", "base.repaired", "--summary", "repaired")
        held = self.holds(fx)
        self.assertFalse(any("broken" in h or "Serving is held" in h for h in held), held)
        code, out = fx.flow("lead-resolve", str(blocker), "--why", "the API is back", cwd=m)
        self.assertEqual(code, 0, out)
        # Marked, so the tracker publisher posts the resolution rather than skipping it.
        resolution = kinds(fx, "decision.recorded")[-1]
        self.assertTrue(resolution["summary"].startswith(f"Blocker {blocker} resolved"), resolution)
        marks = [json.loads(line) for line in (fx.tmp / "flow-log/FX-1.tracker-marks.jsonl").read_text().splitlines()]
        self.assertIn(resolution["id"], [mark["journal"] for mark in marks])
        code, out = fx.flow("lead-resolve", str(blocker), "--why", "again", cwd=m)
        self.assertEqual(code, 2, out)
        # FX-2's merge already ran fullHealth at its head, so a pass there finds nothing more to run.
        runs = lambda: len((fx.tmp / "health.runs").read_text().splitlines())
        self.assertEqual(runs(), 1, "fullHealth didn't run after FX-2's merge")
        # A commit landed on the base is a new head: the post-merge tier runs there, fails and holds the close, and
        # isn't run again at that head until lead-resolve asks.
        (fx.tmp / "health").write_text("fail")
        declare(fx, "landed by hand\n", path="src/landed.txt")
        held = self.holds(fx)
        self.assertTrue(any(h.startswith("fullHealth failed at") for h in held), held)
        self.assertTrue(all(h.startswith(("fullHealth failed at", "The delivery base is broken")) for h in held), held)
        self.holds(fx)
        self.assertEqual(runs(), 2, "fullHealth ran again at the same head")
        code, out = fx.flow("lead-resolve", "full-health", "--why", "flaky", cwd=m)
        self.assertEqual(code, 0, out)
        rerun = kinds(fx, "decision.recorded")[-1]
        self.assertIn("runs again on the supervisor's next pass: flaky", rerun["summary"])
        marks = [json.loads(line) for line in (fx.tmp / "flow-log/FX-1.tracker-marks.jsonl").read_text().splitlines()]
        self.assertIn(rerun["id"], [mark["journal"] for mark in marks], "the rerun's decision isn't marked for the tracker")
        code, out = fx.flow("lead-resolve", "full-health", "--why", "again", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("already asked for", out)
        self.holds(fx)
        self.assertEqual(runs(), 3)
        self.holds(fx)
        self.assertEqual(runs(), 3, "a rerun that failed again ran a third time")
        code, out = fx.flow("lead-resolve", "full-health", "--why", "flaky twice", cwd=m)
        self.assertEqual(code, 0, out)
        (fx.tmp / "health").write_text("pass")
        last = self.once(fx)
        self.assertEqual(runs(), 4)
        self.assertEqual(kinds(fx, "base.repaired")[-1]["summary"][:20], "fullHealth passed at")
        # Then it closes: delivery.closed, the archive, the friction note, and the supervisor's stop.
        self.assertEqual(last["closeHeldBy"], [], last)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        closed = json.loads((fx.tmp / "flow-log/FX-1.closed.json").read_text())
        archive = Path(closed["archive"])
        self.assertTrue((archive / "record/journal.jsonl").exists())
        self.assertTrue(Path(str(archive) + ".friction.md").exists(), last)
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertIn(f"supervisor: stopped; the delivery is closed. Archive {archive}; write up the friction note "
                      f"{archive}.friction.md", out)
        # A further pass closes nothing twice.
        self.once(fx)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1)
        self.assertEqual(json.loads((fx.tmp / "flow-log/FX-1.closed.json").read_text())["archive"], str(archive))


def tracked(fx: Fixture, backend_class, updates: str | None = None):
    """The supervised fixture's tracker: a fake one the release's tracker publisher posts to, or a host outbox."""
    m = fx.main
    backend = backend_class(fx.tmp)
    with backend.edit() as store:
        store["projects"] = ["FX"]
        for key, status in (("FX-1", "In Progress"), ("FX-2", "Backlog"), ("FX-3", "Backlog")):
            store["issues"][key] = {"status": status, "comments": [], "markers": [], "labels": [],
                                    "parent": None if key == "FX-1" else "FX-1", "summary": key}
    conf_path = m / ".agents/run-delivery.json"
    conf = json.loads(conf_path.read_text())
    conf["tracker"] = {**conf["tracker"], **backend.adapter(), **({"updates": updates} if updates else {})}
    conf_path.write_text(json.dumps(conf, indent=2))
    binding_path = m / ".agents/orchestration.json"
    binding = json.loads(binding_path.read_text())
    publisher = fx.release / "skills/run-delivery/scripts/tracker_publisher.py"
    binding["state"]["publishers"] = ([{"id": "tracker", "kind": "host"}] if updates == "host" else
                                      [{"id": "tracker", "argv": [sys.executable, "-B", str(publisher), "--state-dir",
                                                                  "{stateDirectory}"], "timeoutSeconds": 120}])
    binding_path.write_text(json.dumps(binding, indent=2))
    commit(m, "FX-1: a tracker the publisher posts to")
    sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
    fx.env.update(backend.env())
    return backend


def epic_comments(backend) -> list[str]:
    return [comment["text"] for comment in backend.read()["issues"]["FX-1"]["comments"]]


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class SupervisorTrackerTests(unittest.TestCase):
    """What the supervisor reads of the tracker publisher: its "Needs you", its follow-up filings, and its ledger's
    cover of delivery.closed."""

    run_serially = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self, backend_class=FileBackend, updates: str | None = None) -> tuple[Fixture, object]:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        self.addCleanup(stop_supervisors, fx.tmp)
        supervised(fx)
        backend = tracked(fx, backend_class, updates)
        self.addCleanup(backend.close)
        path = fx.main / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["finish"] = []
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: no finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return fx, backend

    def once(self, fx: Fixture) -> dict:
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return listed(self, passes(fx)[-1])

    def closable(self, fx: Fixture, deferred: bool = False) -> None:
        """FX-2 merged, its lane done, and FX-3 removed: nothing but the tracker can hold the close."""
        wt = hand_off(fx, "FX-2", "lane-1", deferred=deferred)
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        queue_run(fx, "FX-2")
        self.assertTrue(kinds(fx, "candidate.merged"))
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "done", "--state", "done")
        with open(fx.tmp / "flow-log/plan-changes.jsonl", "a") as changes:
            changes.write(json.dumps({"type": "change", "id": "c1", "kind": "removed", "subjects": ["FX-3"], "by": "lead",
                                      "why": "Out of scope.", "at": "2026-01-01T00:00:00Z"}) + "\n")
        return wt

    def test_a_failing_publisher_holds_the_close_and_the_fake_jira_gets_delivery_closed_once_it_clears(self) -> None:
        fx, backend = self.fixture(JiraBackend)
        with backend.edit() as store:
            store["token"] = "revoked"
        self.closable(fx)
        held = self.once(fx)["closeHeldBy"]
        self.assertTrue(any(h.startswith("The tracker publisher hasn't posted journal entries") for h in held), held)
        self.assertEqual(kinds(fx, "delivery.closed"), [])
        with backend.edit() as store:
            store["token"] = "good"
        for _ in range(3):
            last = self.once(fx)
            if (fx.tmp / "flow-log/FX-1.closed.json").exists():
                break
        self.assertTrue((fx.tmp / "flow-log/FX-1.closed.json").exists(), last)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1)
        self.assertEqual(len([text for text in epic_comments(backend) if "Delivery closed" in text]), 1,
                         epic_comments(backend))

    def drawn(self, fx: Fixture) -> None:
        """The binding also renders the line map into the record, as an adopter's live view does."""
        m = fx.main
        (m / ".agents/delivery-views.json").write_text(json.dumps({"spec": "delivery-views-config/1"}))
        binding_path = m / ".agents/orchestration.json"
        binding = json.loads(binding_path.read_text())
        binding["state"]["renderers"] = [{"id": "line-map", "output": "line-map.html", "timeoutSeconds": 60, "argv": [
            sys.executable, "-B", str(fx.release / "skills/delivery-views/scripts/render.py"), "line-map", "--config",
            ".agents/delivery-views.json", "--binding", ".agents/orchestration.json", "--state-dir", "{stateDirectory}"]}]
        binding_path.write_text(json.dumps(binding, indent=2))
        commit(m, "FX-1: the line map in the record")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)

    def embedded_needs(self, page: str) -> dict:
        found = re.search(r'<script type="application/json" id="delivery-needs">([^<]*)</script>', page)
        self.assertIsNotNone(found, "the line map embeds no Needs you list")
        return json.loads(found.group(1))

    def test_the_line_map_shows_each_pass_s_needs_you_and_none_once_the_supervisor_stops(self) -> None:
        fx, backend = self.fixture(JiraBackend)
        self.drawn(fx)
        self.closable(fx)
        (fx.tmp / "health").write_text("fail")
        code, out = fx.flow("lead-run", "--resume", cwd=fx.main, env=running(fx))
        self.assertEqual(code, 0, out)
        pid = wait_for(lambda: lock_of(fx).get("pid"), "a running supervisor")
        self.addCleanup(lambda: alive(pid) and os.kill(pid, signal.SIGTERM))
        # FX-2's merge runs fullHealth on the pass that sees it, before any close-out.
        wait_for(lambda: any(str(p.get("fullHealth", "")).startswith("fullHealth failed") for p in passes(fx)),
                 "a failed fullHealth")
        with backend.edit() as store:
            store["token"] = "revoked"
        state_tool(fx, "journal", "--kind", "release.finished", "--summary", "Released for the tracker to hear about")
        page = fx.tmp / "record/line-map.html"
        holding = lambda drawn: sorted("base.broken" if item["source"].endswith("(base.broken)") else item["source"]
                                       for item in drawn["items"] if item["holding"])
        # Each holds the close, and the line map the pass draws lists each, from the same list the close read.
        drawn = wait_for(lambda: (lambda d: d if holding(d) == ["base.broken", "fullHealth", "tracker publisher"] else None)(
            self.embedded_needs(page.read_text())), "each holding in the line map")
        self.assertTrue(drawn["running"], drawn)
        # Stale once a pass is overdue: the interval (1s here) and once more the longer of it and the pass's own time.
        moment = lambda text: datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
        window = (moment(drawn["staleAfter"]) - moment(drawn["at"])).total_seconds()
        self.assertTrue(2 <= window <= 61, window)
        self.assertTrue(all(item["clears"] for item in drawn["items"]), drawn)
        last = passes(fx)[-1]
        for item in last["needsYou"]:
            if item["holding"]:
                self.assertIn(item["text"], last["closeHeldBy"])
        self.assertEqual(kinds(fx, "delivery.closed"), [])
        # Clearing one removes it on a following pass; the other still holds. The rerun passes, so it repairs the base.
        (fx.tmp / "health").write_text("pass")
        code, out = fx.flow("lead-resolve", "full-health", "--why", "flaky", cwd=fx.main)
        self.assertEqual(code, 0, out)
        wait_for(lambda: holding(self.embedded_needs(page.read_text())) == ["tracker publisher"], "fullHealth cleared")
        self.assertEqual(kinds(fx, "delivery.closed"), [])
        # Stopped, it says so: the list, the line map and `latest` all say it isn't running, and keep no items.
        os.kill(pid, signal.SIGTERM)
        wait_for(lambda: not alive(pid), "the supervisor to stop")
        written = json.loads((fx.tmp / "record/needs-you.json").read_text())
        self.assertEqual((written["running"], written["items"]), (False, []), written)
        self.assertFalse(self.embedded_needs(page.read_text())["running"])
        latest = fx.tmp / "archive/FX-1/latest/record"
        self.assertFalse(json.loads((latest / "needs-you.json").read_text())["running"])
        self.assertFalse(self.embedded_needs((latest / "line-map.html").read_text())["running"])

    def test_a_follow_up_whose_filing_fails_once_holds_the_close_and_is_filed_once(self) -> None:
        fx, backend = self.fixture()
        self.closable(fx, deferred=True)
        with backend.edit() as store:
            store["failures"].append({"op": "create", "ticket": None, "status": 409, "times": 1})
        last = self.once(fx)
        self.assertTrue(any(line.startswith("follow-ups: waiting for the tracker publisher") for line in
                            last.get("closeOut", [])), last)
        self.assertEqual(kinds(fx, "delivery.closed"), [])
        for _ in range(3):
            last = self.once(fx)
            if (fx.tmp / "flow-log/FX-1.closed.json").exists():
                break
        self.assertTrue((fx.tmp / "flow-log/FX-1.closed.json").exists(), last)
        created = [call for call in backend.read()["calls"] if call[0] == "create"]
        self.assertEqual(len(created), 1, created)
        followups = [f["id"] for f in record(fx)["state"]["followups"]]
        self.assertEqual(len(followups), 1, followups)
        self.assertTrue(followups[0].startswith("FX-2-"), followups)

    def test_a_follow_up_refused_for_good_is_shown_not_retried_and_doesnt_hold_the_close(self) -> None:
        fx, backend = self.fixture()
        self.closable(fx, deferred=True)
        with backend.edit() as store:
            store["failures"].append({"op": "create", "ticket": None, "status": 400, "times": -1})
        for _ in range(4):
            last = self.once(fx)
            if (fx.tmp / "flow-log/FX-1.closed.json").exists():
                break
        self.assertTrue((fx.tmp / "flow-log/FX-1.closed.json").exists(), last)
        self.assertEqual([call for call in backend.read()["calls"] if call[0] == "create"], [])
        archive = Path(json.loads((fx.tmp / "flow-log/FX-1.closed.json").read_text())["archive"])
        friction = Path(str(archive) + ".friction.md").read_text()
        self.assertIn("was refused", friction)

    def test_the_close_waits_until_the_publisher_has_posted_delivery_closed(self) -> None:
        fx, backend = self.fixture()
        self.closable(fx)
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "still here", "--state", "working")
        self.once(fx)  # every entry so far is posted, while the lane keeps the close open
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "done", "--state", "done")
        with backend.edit() as store:
            store["failures"].append({"op": "comment", "ticket": "FX-1", "status": 409, "times": 1})
        last = self.once(fx)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        self.assertTrue(any(line.startswith("waiting for the tracker publisher to cover journal entries")
                            for line in last.get("closeOut", [])), last)
        self.assertFalse((fx.tmp / "flow-log/FX-1.closed.json").exists(), "archived before the tracker had it")
        last = self.once(fx)
        self.assertTrue((fx.tmp / "flow-log/FX-1.closed.json").exists(), last)
        self.assertEqual(len([text for text in epic_comments(backend) if "Delivery closed" in text]), 1)

    def test_with_a_host_publisher_the_close_waits_for_the_outbox_ack_and_by_hand_it_doesnt(self) -> None:
        for updates in ("host", "manual"):
            with self.subTest(updates=updates):
                fx, backend = self.fixture(updates=updates)
                self.closable(fx)
                ack = lambda: state_tool(fx, "ack", "--version", str(record(fx)["state"]["version"]), "--through",
                                         str(max(e["id"] for e in record(fx)["journal"])))
                if updates == "host":
                    first = self.once(fx)
                    held = first["closeHeldBy"]
                    self.assertTrue(any(h.startswith("The host outbox holds") for h in held), held)
                    self.assertIn(("host outbox", "posting the updates with your tracker tools, then ack"),
                                  [(i["source"], i["clears"]) for i in first["needsYou"] if i["holding"]])
                    ack()
                    last = self.once(fx)
                    self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
                    self.assertFalse((fx.tmp / "flow-log/FX-1.closed.json").exists(), "closed before the outbox was acked")
                    ack()
                last = self.once(fx)
                self.assertTrue((fx.tmp / "flow-log/FX-1.closed.json").exists(), last)

    def test_a_running_supervisor_posts_a_command_s_entry_within_one_pass_and_once_across_a_resume(self) -> None:
        fx, backend = self.fixture()
        m = fx.main
        code, out = fx.flow("lead-run", "--resume", cwd=m, env=running(fx))
        self.assertEqual(code, 0, out)
        wait_for(lambda: passes(fx), "a first pass")
        state_tool(fx, "journal", "--kind", "blocker.recorded", "--summary", "the API is down")
        seen = len(passes(fx))
        wait_for(lambda: len(passes(fx)) > seen + 1, "a whole pass after the entry")
        self.assertEqual(len([t for t in epic_comments(backend) if "the API is down" in t]), 1, epic_comments(backend))
        first = lock_of(fx)
        os.kill(first["pid"], signal.SIGKILL)
        wait_for(lambda: not alive(first["pid"]), "the supervisor to die")
        code, out = fx.flow("lead-run", "--resume", cwd=m, env=running(fx))
        self.assertEqual(code, 0, out)
        seen = len(passes(fx))
        wait_for(lambda: len(passes(fx)) > seen + 1, "two passes after the resume")
        self.assertEqual(len([t for t in epic_comments(backend) if "the API is down" in t]), 1, "posted twice")


FOCUSED_REASON = "The gate is still being optimized."
PASS_WORDING = re.compile(r"\bpass(ed|es|ing)?\b", re.IGNORECASE)


def declaration(posture: str = "focused", reason: str = FOCUSED_REASON) -> str:
    return json.dumps({"schemaVersion": "delivery-posture/1", "posture": posture, "reason": reason}) + "\n"


def declare(fx: Fixture, text: str, branch: str = "feature/FX-1", path: str = ".agents/delivery-posture.json") -> str:
    """Push a declaration to the remote's branch from a clone of its own, so the lead's checkout and tracking ref don't
    move: what the supervisor reads of it, it reads by fetching. Returns the new tip."""
    other = fx.tmp / "declaring"
    if not other.exists():
        origin = sh("git", "remote", "get-url", "origin", cwd=fx.main).stdout.strip()
        sh("git", "clone", "-q", origin, other)
    sh("git", "fetch", "-q", "origin", cwd=other)
    sh("git", "checkout", "-q", "-B", branch, f"origin/{branch}", cwd=other)
    target = other / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    commit(other, "the posture")
    sh("git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=other)
    return sh("git", "rev-parse", "HEAD", cwd=other).stdout.strip()


def health_runs(fx: Fixture) -> int:
    runs = fx.tmp / "health.runs"
    return len(runs.read_text().splitlines()) if runs.exists() else 0


def decisions(fx: Fixture, words: str) -> list[dict]:
    return [e for e in kinds(fx, "decision.recorded") if words in e["summary"]]


def plan_of(fx: Fixture) -> dict:
    return json.loads((fx.tmp / "record/plan.json").read_text())


def closed_of(fx: Fixture) -> dict:
    return json.loads((fx.tmp / "flow-log/FX-1.closed.json").read_text())


def fix_criteria(path: Path) -> None:
    """Criteria turn the plan chapter on; no finish lines leave the close to what a case opens."""
    d = json.loads(path.read_text())
    d["stories"]["FX-2"]["criteria"] = [{"id": "one-works", "text": "One works"}]
    d["stories"]["FX-3"]["criteria"] = [{"id": "two-works", "text": "Two works"}]
    d["finish"] = []
    path.write_text(json.dumps(d, indent=2))


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class SupervisorPostureTests(unittest.TestCase):
    """`focused` on the delivery's base: the close-out runs no fullHealth, says so everywhere, and never reads as a pass."""

    run_serially = True
    setUpClass = classmethod(SupervisorPassTests.setUpClass.__func__)
    merged = SupervisorPassTests.merged
    once = SupervisorPassTests.once

    def fixture(self, closable: bool = True) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        self.addCleanup(stop_supervisors, fx.tmp)
        path = supervised(fx)
        fix_criteria(path)
        commit(fx.main, "FX-1: criteria, no finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        if closable:
            self.closable(fx)
        return fx

    def closable(self, fx: Fixture) -> None:
        self.merged(fx, "FX-2", "lane-1")
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "done", "--state", "done")
        with open(fx.tmp / "flow-log/plan-changes.jsonl", "a") as changes:
            changes.write(json.dumps({"type": "change", "id": "c1", "kind": "removed", "subjects": ["FX-3"], "by": "lead",
                                      "why": "Out of scope.", "at": "2026-01-01T00:00:00Z"}) + "\n")

    def lead_close(self, fx: Fixture) -> str:
        """It exits 1 for what it leaves to the operator, and the fixture keeps its remote branches."""
        code, out = fx.flow("lead-close", cwd=fx.main)
        self.assertIn(code, (0, 1), out)
        self.assertIn("FX-1: closed out", out)
        self.assertNotIn("posture wasn't recorded", out)
        return out

    def assert_says_skipped(self, fx: Fixture, text: str) -> None:
        for words in ("skipped", "focused", "origin/feature/FX-1", FOCUSED_REASON):
            self.assertIn(words, text)
        self.assertIsNone(PASS_WORDING.search(text), text)

    def assert_skip_recorded(self, fx: Fixture) -> None:
        said = decisions(fx, "fullHealth skipped")
        self.assertEqual(len(said), 1, said)
        self.assert_says_skipped(fx, said[0]["summary"])
        self.assertEqual(plan_of(fx)["closeOut"], {"fullHealth": "skipped", "posture": "focused",
                                                   "source": "origin/feature/FX-1", "reason": FOCUSED_REASON})
        self.assert_says_skipped(fx, json.dumps(closed_of(fx)["fullHealth"]))
        self.assertEqual(health_runs(fx), 0, "a run started under focused")

    def test_focused_on_the_base_closes_with_no_run_and_every_record_says_skipped(self) -> None:
        fx = self.fixture()
        declare(fx, declaration())
        last = self.once(fx)
        self.assertEqual(last["closeHeldBy"], [], last)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        self.assert_skip_recorded(fx)
        # The supervisor's close-out summary, in its pass log and in delivery.closed.
        self.assert_says_skipped(fx, last["closeOut"][0])
        self.assert_says_skipped(fx, kinds(fx, "delivery.closed")[0]["summary"])
        # No entry the log calls a result, so nothing can read it as a pass.
        entries = [json.loads(line) for line in (fx.tmp / "flow-log/FX-1.full-health.jsonl").read_text().splitlines()]
        self.assertTrue(entries)
        self.assertFalse([e for e in entries if "result" in e], entries)
        # Further passes write nothing more.
        self.once(fx)
        self.once(fx)
        self.assert_skip_recorded(fx)
        self.assertEqual(len(decisions(fx, "posture changed")), 0)

    def test_gate_or_nothing_declared_runs_and_holds_as_it_always_did(self) -> None:
        for name, text in (("undeclared", None), ("gate", declaration("gate", "The gate is on."))):
            with self.subTest(declared=name):
                fx = self.fixture()
                if text:
                    declare(fx, text)
                (fx.tmp / "health").write_text("fail")
                self.once(fx)
                self.assertEqual(health_runs(fx), 1)
                held = self.once(fx)["closeHeldBy"]
                self.assertTrue(any(h.startswith("fullHealth failed at") for h in held), held)
                self.assertEqual(health_runs(fx), 1)
                self.assertEqual(kinds(fx, "delivery.closed"), [])
                (fx.tmp / "health").write_text("pass")
                code, out = fx.flow("lead-resolve", "full-health", "--why", "flaky", cwd=fx.main)
                self.assertEqual(code, 0, out)
                last = self.once(fx)
                self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
                self.assertTrue(last["closeOut"][0].startswith("fullHealth passed at"), last)
                self.assertNotIn("closeOut", plan_of(fx))
                self.assertEqual(decisions(fx, "skipped") + decisions(fx, "posture"), [])
                self.assertNotIn("fullHealth", closed_of(fx))
                self.assertNotIn("skipped", json.dumps(closed_of(fx)))

    def test_the_base_moving_to_focused_after_a_failed_run_frees_the_close_and_is_recorded_once(self) -> None:
        fx = self.fixture()
        (fx.tmp / "health").write_text("fail")
        self.once(fx)
        self.assertTrue(any(h.startswith("fullHealth failed at") for h in self.once(fx)["closeHeldBy"]))
        self.assertEqual(health_runs(fx), 1)
        declare(fx, declaration())
        last = self.once(fx)
        self.assertEqual(last["closeHeldBy"], [], last)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        self.once(fx)
        self.assertEqual(health_runs(fx), 1, "a run started once the base said focused")
        changed = decisions(fx, "posture changed")
        self.assertEqual(len(changed), 1, changed)
        self.assertIn("from gate to focused", changed[0]["summary"])
        self.assertEqual(len(decisions(fx, "fullHealth skipped")), 1)
        self.assertEqual(plan_of(fx)["closeOut"]["fullHealth"], "skipped")

    def test_a_failed_run_at_exactly_the_tip_that_says_focused_holds_nothing(self) -> None:
        fx = self.fixture()
        tip = declare(fx, declaration())
        with open(fx.tmp / "flow-log/FX-1.full-health.jsonl", "a") as log:
            log.write(json.dumps({"head": tip, "result": "failed", "output": "boom", "at": "2026-01-01T00:00:00.000000Z"}) + "\n")
        last = self.once(fx)
        self.assertEqual(last["closeHeldBy"], [], last)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        self.assertEqual(health_runs(fx), 0)
        self.assertEqual(plan_of(fx)["closeOut"]["fullHealth"], "skipped")
        # A lead-resolve rerun entry isn't a result: the skip stands while the base says focused.
        code, out = fx.flow("lead-resolve", "full-health", "--why", "one more", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("if the base returns to gate", out)
        self.assertNotIn("next pass", kinds(fx, "decision.recorded")[-1]["summary"])
        self.assertEqual(plan_of(fx)["closeOut"]["fullHealth"], "skipped")
        self.once(fx)
        self.assertEqual(plan_of(fx)["closeOut"]["fullHealth"], "skipped")
        self.assertEqual(health_runs(fx), 0)

    def test_a_long_multi_line_reason_still_reaches_the_plan_and_every_record(self) -> None:
        fx = self.fixture()
        declare(fx, declaration("focused", "first line\nsecond " + "x" * 700))
        last = self.once(fx)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        shown = plan_of(fx)["closeOut"]["reason"]
        self.assertTrue(shown.startswith("first line second "), shown)
        self.assertLessEqual(len(shown), 600)
        self.assertEqual(closed_of(fx)["fullHealth"]["reason"], shown)
        self.assertEqual(len(decisions(fx, "fullHealth skipped")), 1)

    def test_focused_anywhere_but_the_tip_of_the_remote_base_gets_nothing(self) -> None:
        fx = self.fixture()
        declare(fx, declaration("gate", "The gate is on."))
        # A candidate's branch, the lead's working tree, and a local commit nobody pushed.
        sh("git", "checkout", "-q", "-b", "feature/candidate", "origin/feature/FX-1", cwd=fx.tmp / "declaring")
        (fx.tmp / "declaring/.agents/delivery-posture.json").write_text(declaration())
        commit(fx.tmp / "declaring", "a candidate says focused")
        sh("git", "push", "-q", "origin", "feature/candidate", cwd=fx.tmp / "declaring")
        (fx.main / ".agents/delivery-posture.json").write_text(declaration())
        commit(fx.main, "a local commit nobody pushed")
        (fx.main / ".agents/delivery-posture.json").write_text(declaration("focused", "And the working tree."))
        self.once(fx)
        self.assertEqual(health_runs(fx), 1, "fullHealth didn't run under the base's gate")
        self.assertEqual(decisions(fx, "fullHealth skipped"), [])

    def test_a_malformed_declaration_runs_with_the_reason_named_once(self) -> None:
        fx = self.fixture()
        declare(fx, '{"schemaVersion": "delivery-posture/1", "posture": "focused", "reason": "   "}\n')
        (fx.tmp / "health").write_text("fail")
        last = self.once(fx)
        self.assertEqual(health_runs(fx), 1)
        # The post-merge run after FX-2's merge is the one that ran, and its line names the declaration that didn't take.
        self.assertIn("posture_malformed", last["fullHealth"])
        self.assertIn(".agents/delivery-posture.json", last["fullHealth"])
        self.once(fx)
        named = decisions(fx, "posture_malformed")
        self.assertEqual(len(named), 1, named)
        self.assertIn("origin/feature/FX-1", named[0]["summary"])
        self.assertNotIn("closeOut", plan_of(fx))

    def test_the_delivery_s_base_decides_when_the_binding_names_another(self) -> None:
        for delivery_says, binding_says, runs in (("gate", "focused", 1), ("focused", "gate", 0)):
            with self.subTest(delivery=delivery_says, binding=binding_says):
                fx = Fixture(self.generation, self.receipt)
                self.addCleanup(fx.close)
                self.addCleanup(stop_supervisors, fx.tmp)
                path = supervised(fx)
                fix_criteria(path)
                d = json.loads(path.read_text())
                d["baseBranch"] = "feature/FX-1"
                path.write_text(json.dumps(d, indent=2))
                binding_path = fx.main / ".agents/orchestration.json"
                binding = json.loads(binding_path.read_text())
                binding["base"]["ref"] = "main"
                binding["bases"] = ["feature/FX-1"]
                binding_path.write_text(json.dumps(binding, indent=2))
                commit(fx.main, "FX-1: a binding based elsewhere")
                sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
                fx.env["RUN_DELIVERY_BASE"] = "feature/FX-1"
                code, out = fx.flow("lead-init", cwd=fx.main)
                self.assertEqual(code, 0, out)
                self.closable(fx)
                declare(fx, declaration(delivery_says, "Said on the delivery's base."))
                declare(fx, declaration(binding_says, "Said on the binding's base."), branch="main")
                self.once(fx)
                self.assertEqual(health_runs(fx), runs)

    def test_lead_close_with_the_supervisor_down_records_the_skip_the_same_way(self) -> None:
        fx = self.fixture()
        declare(fx, declaration())
        state_tool(fx, "journal", "--kind", "delivery.closed", "--summary", "FX-1 closed by hand.")
        out = self.lead_close(fx)
        self.assert_skip_recorded(fx)
        self.assert_says_skipped(fx, next(line for line in out.splitlines() if "fullHealth skipped" in line))
        # Run again, it records nothing new.
        self.lead_close(fx)
        self.assert_skip_recorded(fx)

    def test_two_archives_in_the_same_second_each_get_a_folder(self) -> None:
        fx = self.fixture(closable=False)
        script = ("import sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\nimport run_delivery as flow\n"
                  "flow.CFG = c = flow.Config(Path.cwd(), 'FX-1')\n"
                  "print(flow.archive_delivery(c)[0])\nprint(flow.archive_delivery(c)[0])\n")
        done = subprocess.run([sys.executable, "-B", "-c", script, str(fx.tool.parent)], cwd=fx.main, env=fx.env,
                              text=True, capture_output=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        first, second = done.stdout.split()
        self.assertNotEqual(first, second)
        self.assertTrue((Path(first) / "record/journal.jsonl").exists())
        self.assertTrue((Path(second) / "record/journal.jsonl").exists())

    def test_lead_close_under_gate_does_what_it_did(self) -> None:
        fx = self.fixture()
        declare(fx, declaration("gate", "The gate is on."))
        state_tool(fx, "journal", "--kind", "delivery.closed", "--summary", "FX-1 closed by hand.")
        out = self.lead_close(fx)
        self.assertEqual(health_runs(fx), 0)
        self.assertNotIn("skipped", out)
        self.assertNotIn("fullHealth", closed_of(fx))
        self.assertFalse((fx.tmp / "flow-log/FX-1.full-health.jsonl").exists())
        self.assertNotIn("closeOut", plan_of(fx))

    def test_one_running_supervisor_honours_the_new_tip_with_no_help(self) -> None:
        fx = self.fixture()
        (fx.tmp / "health").write_text("fail")
        code, out = fx.flow("lead-run", "--resume", cwd=fx.main, env=running(fx))
        self.assertEqual(code, 0, out)
        wait_for(lambda: any(h.startswith("fullHealth failed at") for p in passes(fx) for h in p.get("closeHeldBy", [])),
                 "a failed fullHealth holding the close")
        pid = settled_lock(fx)["pid"]
        tracking = sh("git", "rev-parse", "origin/feature/FX-1", cwd=fx.main).stdout.strip()
        declare(fx, declaration())
        self.assertEqual(sh("git", "rev-parse", "origin/feature/FX-1", cwd=fx.main).stdout.strip(), tracking,
                         "the fixture moved the tracking ref")
        wait_for(lambda: kinds(fx, "delivery.closed"), "the same supervisor to close on the new tip", 60)
        self.assertEqual(health_runs(fx), 1)
        self.assertEqual(len(decisions(fx, "posture changed")), 1)
        wait_for(lambda: not alive(pid), "the supervisor to stop", 60)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class SupervisorPostureRestoredTests(unittest.TestCase):
    """A skip, then `gate`, with the tracker holding the close open between the two passes: a real run starts, and once
    it passes nothing says skipped."""

    run_serially = True
    setUpClass = classmethod(SupervisorTrackerTests.setUpClass.__func__)
    once = SupervisorTrackerTests.once
    closable = SupervisorTrackerTests.closable

    def fixture(self) -> tuple[Fixture, object]:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        self.addCleanup(stop_supervisors, fx.tmp)
        path = supervised(fx)
        backend = tracked(fx, FileBackend)
        self.addCleanup(backend.close)
        fix_criteria(path)
        commit(fx.main, "FX-1: criteria, no finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return fx, backend

    def skipped_and_held_open(self) -> Fixture:
        fx, backend = self.fixture()
        self.closable(fx, deferred=True)
        declare(fx, declaration())
        with backend.edit() as store:
            store["failures"].append({"op": "create", "ticket": None, "status": 409, "times": 1})
        last = self.once(fx)
        self.assertTrue(last["closeOut"][0].startswith("fullHealth skipped"), last)
        self.assertTrue(any(line.startswith("follow-ups: waiting") for line in last["closeOut"]), last)
        self.assertEqual(kinds(fx, "delivery.closed"), [])
        self.assertEqual(plan_of(fx)["closeOut"]["fullHealth"], "skipped")
        self.assertEqual(health_runs(fx), 0)
        return fx

    def assert_closed_on_a_pass(self, fx: Fixture) -> None:
        last = self.once(fx)
        for _ in range(3):
            if (fx.tmp / "flow-log/FX-1.closed.json").exists():
                break
            last = self.once(fx)
        self.assertTrue((fx.tmp / "flow-log/FX-1.closed.json").exists(), last)
        self.assertEqual(health_runs(fx), 1)
        self.assertNotIn("closeOut", plan_of(fx))
        self.assertTrue(last["closeOut"][0].startswith("fullHealth passed at"), last)
        self.assertNotIn("fullHealth", closed_of(fx))
        self.assertNotIn("skipped", json.dumps(closed_of(fx)))
        self.assertNotIn("skipped", kinds(fx, "delivery.closed")[0]["summary"])

    def test_gate_at_a_later_head_starts_a_real_run_and_clears_the_skip(self) -> None:
        fx = self.skipped_and_held_open()
        added = len(decisions(fx, "changed closeOut"))
        declare(fx, declaration("gate", "The gate is back."))
        self.assert_closed_on_a_pass(fx)
        changed = decisions(fx, "posture changed")
        self.assertEqual(len(changed), 1, changed)
        self.assertIn("from focused to gate", changed[0]["summary"])
        self.assertEqual(len(decisions(fx, "changed closeOut")), added + 1, "the rebuild didn't name the member it removed")

    def test_focused_at_a_later_head_with_a_new_reason_replaces_the_skip_and_journals_nothing_new(self) -> None:
        fx = self.skipped_and_held_open()
        tip = declare(fx, declaration("focused", "A newer reason."))
        last = self.once(fx)
        for _ in range(3):
            if (fx.tmp / "flow-log/FX-1.closed.json").exists():
                break
            last = self.once(fx)
        self.assertTrue((fx.tmp / "flow-log/FX-1.closed.json").exists(), last)
        self.assertEqual(plan_of(fx)["closeOut"]["reason"], "A newer reason.")
        self.assertEqual((closed_of(fx)["fullHealth"]["reason"], closed_of(fx)["fullHealth"]["head"]), ("A newer reason.", tip))
        self.assertEqual(len(decisions(fx, "fullHealth skipped")), 1, "each skip after the first was journalled")
        self.assertEqual(decisions(fx, "posture changed"), [])
        self.assertEqual(health_runs(fx), 0)

    def test_focused_at_a_later_head_with_the_same_reason_moves_the_skip_to_that_head(self) -> None:
        fx = self.skipped_and_held_open()
        said = json.loads(declaration())
        tip = declare(fx, json.dumps({**said, "since": "later"}) + "\n")
        last = self.once(fx)
        for _ in range(3):
            if (fx.tmp / "flow-log/FX-1.closed.json").exists():
                break
            last = self.once(fx)
        self.assertTrue((fx.tmp / "flow-log/FX-1.closed.json").exists(), last)
        self.assertEqual(closed_of(fx)["fullHealth"]["head"], tip)
        self.assertEqual(len(decisions(fx, "fullHealth skipped")), 1)

    def test_lead_close_under_gate_after_a_skip_records_the_change_and_stops_saying_skipped(self) -> None:
        fx = self.skipped_and_held_open()
        declare(fx, declaration("gate", "The gate is back."))
        state_tool(fx, "journal", "--kind", "delivery.closed", "--summary", "FX-1 closed by hand.")
        code, out = fx.flow("lead-close", cwd=fx.main)
        self.assertIn(code, (0, 1), out)
        self.assertNotIn("posture wasn't recorded", out)
        self.assertEqual(len(decisions(fx, "posture changed")), 1)
        self.assertNotIn("closeOut", plan_of(fx))
        self.assertNotIn("fullHealth", closed_of(fx))
        self.assertEqual(health_runs(fx), 0)

    def test_gate_at_the_same_head_starts_a_real_run_and_clears_the_skip(self) -> None:
        fx = self.skipped_and_held_open()
        # The tip can't be read once, so the same head resolves to gate with the code that says why.
        real = sh("sh", "-c", "command -v git").stdout.strip()
        stubs = fx.tmp / "stubs"
        stubs.mkdir()
        (stubs / "git").write_text(f'#!/bin/sh\nif [ "$1" = ls-tree ]; then exit 1; fi\nexec {real} "$@"\n')
        (stubs / "git").chmod(0o755)
        path = fx.env["PATH"]
        fx.env["PATH"] = f"{stubs}{os.pathsep}{path}"
        last = self.once(fx)
        fx.env["PATH"] = path
        self.assertEqual(health_runs(fx), 1, last)
        self.assertIn("posture_unreadable", last["closeOut"][0])
        self.assertEqual(len(decisions(fx, "posture_unreadable")), 1)
        self.assert_closed_on_a_pass(fx)



@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class SupervisorPostMergeTests(unittest.TestCase):
    """The post-merge tier: under `gate`, the binding's fullHealth runs after each merge, not only at close-out."""

    run_serially = True
    setUpClass = classmethod(SupervisorPassTests.setUpClass.__func__)
    merged = SupervisorPassTests.merged
    once = SupervisorPassTests.once

    def fixture(self, **binding) -> Fixture:
        """The supervised delivery with no finish lines, so what holds the close is what each case leaves open."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        self.addCleanup(stop_supervisors, fx.tmp)
        path = supervised(fx)
        d = json.loads(path.read_text())
        d["finish"] = []
        path.write_text(json.dumps(d, indent=2))
        if binding:
            bound = fx.main / ".agents/orchestration.json"
            bound.write_text(json.dumps({**json.loads(bound.read_text()), **binding}, indent=2))
        commit(fx.main, "FX-1: no finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return fx

    def done(self, fx: Fixture, *lanes: str) -> None:
        for lane in lanes:
            state_tool(fx, "focus", "--lane", lane, "--text", "done", "--state", "done")

    def base_events(self, fx: Fixture) -> list[dict]:
        return [e for e in record(fx)["journal"] if e["kind"] in ("base.verified", "base.broken", "base.repaired")]

    def results(self, fx: Fixture) -> list[dict]:
        log = fx.tmp / "flow-log/FX-1.full-health.jsonl"
        return [e for e in map(json.loads, log.read_text().splitlines()) if e.get("result")] if log.exists() else []

    def base_tip(self, fx: Fixture) -> str:
        return sh("git", "ls-remote", "origin", "refs/heads/feature/FX-1", cwd=fx.main).stdout.split()[0]

    def test_a_merge_runs_full_health_with_the_close_still_held_and_once_at_its_head(self) -> None:
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        for _ in range(3):
            self.assertTrue(self.once(fx)["closeHeldBy"])
        self.assertEqual(health_runs(fx), 1, "fullHealth didn't run after the merge, or ran again at its head")
        head = self.base_tip(fx)
        [result] = self.results(fx)
        self.assertEqual((result["head"], result["result"], result["merge"]["subject"]), (head, "passed", "FX-2"))
        self.assertEqual(passes(fx)[-1]["fullHealth"], f"fullHealth passed at {head[:10]}", passes(fx)[-1])

    def test_two_merges_a_pass_names_the_first_and_a_break_names_the_second(self) -> None:
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        self.once(fx)
        [verified] = self.base_events(fx)
        self.assertEqual(verified["kind"], "base.verified", verified)
        self.assertIn("after FX-2's merge", verified["summary"])
        (fx.tmp / "health").write_text("fail")
        self.merged(fx, "FX-3", "lane-2")
        self.done(fx, "lane-1", "lane-2")
        held = self.once(fx)["closeHeldBy"]
        self.assertEqual(health_runs(fx), 2)
        broken = self.base_events(fx)[-1]
        self.assertEqual(broken["kind"], "base.broken", broken)
        self.assertIn("after FX-3's merge", broken["summary"])
        self.assertNotIn("FX-2", broken["summary"])
        self.assertIn("test_parser failed", broken["summary"])
        self.assertTrue(any(h.startswith("fullHealth failed at") for h in held), held)
        self.assertTrue(any(h.startswith("The delivery base is broken: fullHealth failed") for h in held), held)
        needs = [item for item in passes(fx)[-1]["needsYou"] if "(base.broken)" in item["source"]]
        self.assertTrue(needs[0]["clears"].startswith("a fullHealth pass at a later base head"), needs)
        # Nothing else holds the close, and no pass runs it again at B's head: no third run until another merge.
        for _ in range(2):
            held = self.once(fx)["closeHeldBy"]
            self.assertEqual(len(held), 2, held)
        self.assertEqual(health_runs(fx), 2, "fullHealth ran again at the broken head")
        self.assertEqual(kinds(fx, "delivery.closed"), [])
        # The break stops serving, as any base.broken does.
        self.assertTrue(self.serving_waits(fx))

    def serving_waits(self, fx: Fixture) -> bool:
        """Whether serve waits for base.repaired, as the installed queue reads the journal."""
        said = sh(sys.executable, "-B", "-c", "import sys, pathlib; sys.path.insert(0, sys.argv[1]); import delivery_state; "
                  "print(delivery_state.base_awaits_repair(delivery_state.read_journal(pathlib.Path(sys.argv[2]))))",
                  fx.release / "skills/orchestrate-work/scripts", fx.tmp / "record").stdout.strip()
        return said == "True"

    def test_a_hand_repair_to_let_the_fix_through_is_followed_by_the_run_s_own_repair(self) -> None:
        fx = self.fixture()
        (fx.tmp / "health").write_text("fail")
        self.merged(fx, "FX-2", "lane-1")
        self.once(fx)
        # The lead reopens the queue by hand so the fix, C, can go through it.
        state_tool(fx, "journal", "--kind", "base.repaired", "--summary", "reopened to let the fix through")
        self.assertFalse(self.serving_waits(fx))
        (fx.tmp / "health").write_text("pass")
        self.merged(fx, "FX-3", "lane-2")
        self.done(fx, "lane-1", "lane-2")
        last = self.once(fx)
        events = self.base_events(fx)
        self.assertEqual([e["kind"] for e in events], ["base.broken", "base.repaired", "base.repaired"])
        self.assertIn("after FX-3's merge", events[-1]["summary"])
        self.assertNotIn("FX-2", events[-1]["summary"])
        # The second repair reopens nothing that was closed, holds nothing, and the run isn't counted twice.
        self.assertFalse(self.serving_waits(fx))
        self.assertFalse([item for item in last["needsYou"] if "base.broken" in item["source"]], last["needsYou"])
        self.assertEqual(health_runs(fx), 2)
        self.assertEqual([e["result"] for e in self.results(fx)], ["failed", "passed"])
        self.assertEqual(last["closeHeldBy"], [], last)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1)
        self.once(fx)
        self.assertEqual(len(self.base_events(fx)), 3, "a later pass journalled the base again")

    def test_the_first_pass_after_a_break_repairs_it_and_close_out_runs_nothing_more(self) -> None:
        fx = self.fixture()
        (fx.tmp / "health").write_text("fail")
        self.merged(fx, "FX-2", "lane-1")
        self.once(fx)
        self.assertEqual([e["kind"] for e in self.base_events(fx)], ["base.broken"])
        (fx.tmp / "health").write_text("pass")
        # The fix, C, lands through the queue run by hand, as the lead fixes forward on a broken base.
        self.merged(fx, "FX-3", "lane-2")
        self.done(fx, "lane-1", "lane-2")
        last = self.once(fx)
        events = self.base_events(fx)
        self.assertEqual([e["kind"] for e in events], ["base.broken", "base.repaired"])
        self.assertIn("after FX-3's merge", events[-1]["summary"])
        self.assertNotIn("FX-2", events[-1]["summary"])
        self.assertEqual(health_runs(fx), 2, "close-out ran fullHealth again at the repaired head")
        head = self.base_tip(fx)
        self.assertEqual(last["closeHeldBy"], [], last)
        self.assertEqual(last["closeOut"][0], f"fullHealth passed at {head[:10]}", last)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1)
        self.assertEqual(self.results(fx)[-1]["merge"]["subject"], "FX-3")

    def test_a_pass_after_a_pass_verifies_and_never_repairs_a_break_the_queue_journalled(self) -> None:
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        self.once(fx)
        state_tool(fx, "journal", "--kind", "base.broken", "--summary", "base_broken: lint also fails on the base")
        self.merged(fx, "FX-3", "lane-2")
        self.once(fx)
        self.assertEqual([e["kind"] for e in self.base_events(fx)], ["base.verified", "base.broken", "base.verified"])
        needs = [item for item in passes(fx)[-1]["needsYou"] if "(base.broken)" in item["source"]]
        self.assertEqual(needs[0]["clears"], "journalling base.repaired", needs)

    def test_a_pass_never_repairs_a_break_the_queue_found_after_a_hand_repair(self) -> None:
        fx = self.fixture()
        (fx.tmp / "health").write_text("fail")
        self.merged(fx, "FX-2", "lane-1")
        self.once(fx)
        state_tool(fx, "journal", "--kind", "base.repaired", "--summary", "reopened to let the fix through")
        # The queue then finds a break of its own, and a fix lands by hand.
        state_tool(fx, "journal", "--kind", "base.broken", "--summary", "base_broken: lint also fails on the base")
        (fx.tmp / "health").write_text("pass")
        declare(fx, "landed by hand\n", path="src/landed.txt")
        last = self.once(fx)
        self.assertEqual([e["kind"] for e in self.base_events(fx)],
                         ["base.broken", "base.repaired", "base.broken", "base.verified"])
        self.assertTrue(self.serving_waits(fx), "a fullHealth pass reopened serve over the queue's break")
        needs = [item for item in last["needsYou"] if "(base.broken)" in item["source"]]
        self.assertEqual((needs[0]["holding"], needs[0]["clears"]), (True, "journalling base.repaired"), needs)

    def test_a_pass_never_repairs_a_queue_break_that_fullHealth_s_own_break_followed(self) -> None:
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        self.once(fx)
        # The queue finds a break; a fix landed by hand fails fullHealth on top of it; the next fix passes.
        state_tool(fx, "journal", "--kind", "base.broken", "--summary", "base_broken: lint also fails on the base")
        (fx.tmp / "health").write_text("fail")
        declare(fx, "first fix\n", path="src/landed.txt")
        held = self.once(fx)
        needs = [item for item in held["needsYou"] if "(base.broken)" in item["source"]]
        self.assertEqual(needs[0]["clears"], "journalling base.repaired", needs)
        (fx.tmp / "health").write_text("pass")
        declare(fx, "second fix\n", path="src/landed.txt")
        self.once(fx)
        self.assertEqual([e["kind"] for e in self.base_events(fx)],
                         ["base.verified", "base.broken", "base.broken", "base.verified"])
        self.assertTrue(self.serving_waits(fx), "a fullHealth pass confirmed the queue's break")

    def test_a_queue_break_the_lead_confirmed_doesnt_keep_a_later_fullHealth_break_from_its_repair(self) -> None:
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        self.once(fx)
        # The queue's break, confirmed by the lead; then a merge that fails fullHealth, and a fix that passes it.
        state_tool(fx, "journal", "--kind", "base.broken", "--summary", "base_broken: lint also fails on the base")
        state_tool(fx, "journal", "--kind", "base.repaired", "--summary", "lint fixed on the base")
        (fx.tmp / "health").write_text("fail")
        self.merged(fx, "FX-3", "lane-2")
        self.once(fx)
        (fx.tmp / "health").write_text("pass")
        declare(fx, "the fix\n", path="src/landed.txt")
        self.once(fx)
        self.assertEqual([e["kind"] for e in self.base_events(fx)],
                         ["base.verified", "base.broken", "base.repaired", "base.broken", "base.repaired"])
        self.assertFalse(self.serving_waits(fx), "a fullHealth break fullHealth itself repaired left serve waiting")

    def test_a_break_yields_the_close_under_focused_stays_listed_and_holds_again_under_gate(self) -> None:
        fx = self.fixture()
        (fx.tmp / "health").write_text("fail")
        self.merged(fx, "FX-2", "lane-1")
        self.once(fx)
        declare(fx, declaration())
        last = self.once(fx)
        [item] = [item for item in last["needsYou"] if "(base.broken)" in item["source"]]
        self.assertFalse(item["holding"], item)
        for words in ("focused posture on origin/feature/FX-1", FOCUSED_REASON[:40], "serve still waits for base.repaired"):
            self.assertIn(words, item["text"])
        self.assertFalse([h for h in last["closeHeldBy"] if "broken" in h or "fullHealth" in h], last["closeHeldBy"])
        self.assertTrue(self.serving_waits(fx), "serve stopped waiting under focused")
        self.assertEqual(health_runs(fx), 1)
        # Back to gate with the break unrepaired: it holds again, and the run at the new head decides.
        declare(fx, declaration("gate", "The gate is back."))
        last = self.once(fx)
        self.assertEqual(health_runs(fx), 2)
        [item] = [item for item in last["needsYou"] if "(base.broken)" in item["source"]]
        self.assertTrue(item["holding"], item)
        self.assertTrue(any(h.startswith("The delivery base is broken: fullHealth failed") for h in last["closeHeldBy"]))
        (fx.tmp / "health").write_text("pass")
        code, out = fx.flow("lead-resolve", "full-health", "--why", "fixed", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.once(fx)
        self.assertEqual(self.base_events(fx)[-1]["kind"], "base.repaired")
        self.assertFalse(self.serving_waits(fx))

    def test_a_batch_names_every_merge_and_a_head_past_the_queue_says_so(self) -> None:
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        self.merged(fx, "FX-3", "lane-2")
        self.once(fx)
        [verified] = self.base_events(fx)
        merges = {e["subject"]: e["id"] for e in kinds(fx, "candidate.merged")}
        self.assertIn(f"after FX-3's merge (journal entry {merges['FX-3']}), which landed with FX-2", verified["summary"])
        self.assertEqual(verified.get("subject"), "FX-3")
        tip = declare(fx, "landed by hand\n", path="src/landed.txt")
        self.once(fx)
        latest = self.base_events(fx)[-1]
        self.assertIn(f"after FX-3's merge (journal entry {merges['FX-3']}), and {tip[:10]}, which landed outside the queue",
                      latest["summary"])
        self.assertNotIn("landed with", latest["summary"])

    def test_a_lane_that_says_done_during_the_pass_is_cleaned_up_before_the_close(self) -> None:
        fx = self.fixture()
        (fx.tmp / "flow-log").mkdir(parents=True, exist_ok=True)
        with open(fx.tmp / "flow-log/plan-changes.jsonl", "a") as changes:
            changes.write(json.dumps({"type": "change", "id": "c1", "kind": "removed", "subjects": ["FX-3"], "by": "lead",
                                      "why": "Out of scope.", "at": "2026-01-01T00:00:00Z"}) + "\n")
        # The lane is still at work after its hand-off, so the pass's first cleanup leaves its checkout.
        wt = hand_off(fx, "FX-2", "lane-1")
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "fixing the docs", "--state", "working")
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        queue_run(fx, "FX-2")
        (fx.tmp / "health").write_text("lane-done")
        fx.env.update(FX_STATE_TOOL=str(fx.release / "skills/orchestrate-work/scripts/delivery_state.py"),
                      FX_RECORD=str(fx.tmp / "record"))
        last = self.once(fx)
        self.assertIn("FX-2: waits for lane-1 to move off it", last["cleanup"])
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        self.assertTrue((fx.tmp / "flow-log/FX-1.closed.json").exists())
        self.assertFalse(wt.exists(), "the delivery closed with the lane's checkout left behind")
        cleaned = [e for e in kinds(fx, "decision.recorded") if e["summary"].startswith("Cleaned up FX-2")]
        self.assertEqual(len(cleaned), 1)
        self.assertLess(cleaned[0]["id"], kinds(fx, "delivery.closed")[0]["id"])

    def test_its_own_timeout_bounds_it_and_not_the_queue_s_checks(self) -> None:
        fx = self.fixture(fullHealthTimeoutSeconds=1)
        # The queue's check takes longer than fullHealth may, and still passes under checkTimeoutSeconds.
        fx.env["FX_CHECK_SECONDS"] = "2"
        self.merged(fx, "FX-2", "lane-1")
        (fx.tmp / "health").write_text("slow")
        started = time.monotonic()
        self.once(fx)
        self.assertLess(time.monotonic() - started, 25, "fullHealth wasn't stopped at its own timeout")
        [result] = self.results(fx)
        self.assertEqual(result["result"], "failed")
        self.assertIn("took longer than 1s", result["output"])
        self.assertEqual(self.base_events(fx)[-1]["kind"], "base.broken")
        binding = {"checkTimeoutSeconds": 600, "checkTimeoutCeiling": 1200, "fullHealth": ["true"]}
        timeout = lambda **more: fx.call("full_health_timeout", {**binding, **more}, cwd=fx.main)
        self.assertEqual((timeout(), timeout(fullHealthTimeoutSeconds=60), timeout(fullHealthTimeoutSeconds=5400)),
                         (600, 60, 5400))

    def test_a_delivery_that_merged_nothing_runs_it_once_at_close_out(self) -> None:
        fx = self.fixture()
        (fx.tmp / "flow-log").mkdir(parents=True, exist_ok=True)
        with open(fx.tmp / "flow-log/plan-changes.jsonl", "a") as changes:
            changes.write(json.dumps({"type": "change", "id": "c1", "kind": "removed", "subjects": ["FX-2", "FX-3"],
                                      "by": "lead", "why": "Out of scope.", "at": "2026-01-01T00:00:00Z"}) + "\n")
        last = self.once(fx)
        self.assertEqual(last["fullHealth"], "fullHealth: nothing merged yet", last)
        self.assertEqual(health_runs(fx), 1)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        [event] = self.base_events(fx)
        self.assertEqual(event["kind"], "base.verified")
        self.assertIn("with nothing merged", event["summary"])
        self.once(fx)
        self.assertEqual(health_runs(fx), 1)

    def test_nothing_runs_after_a_merge_under_focused(self) -> None:
        fx = self.fixture()
        declare(fx, declaration())
        self.merged(fx, "FX-2", "lane-1")
        last = self.once(fx)
        self.assertEqual(last["fullHealth"], "fullHealth: not run after merges under the focused posture", last)
        self.assertEqual(health_runs(fx), 0)
        self.assertEqual(self.base_events(fx), [])

@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class SupervisorBackgroundHealthTests(unittest.TestCase):
    """A running supervisor starts the post-merge fullHealth as a child of its own and keeps passing while it runs."""

    run_serially = True
    setUpClass = classmethod(SupervisorPassTests.setUpClass.__func__)
    merged = SupervisorPassTests.merged
    fixture = SupervisorPostMergeTests.fixture
    base_events = SupervisorPostMergeTests.base_events
    results = SupervisorPostMergeTests.results

    def run_lock(self, fx: Fixture) -> dict:
        """What the fullHealth run's lock records, and whether a process holds it."""
        lock = fx.tmp / "flow-log/FX-1.full-health.lock"
        if not lock.exists():
            return {"held": False}
        with open(lock, "a+") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe, fcntl.LOCK_UN)
                held = False
            except BlockingIOError:
                held = True
        try:
            recorded = json.loads(lock.read_text() or "{}")
        except ValueError:
            recorded = {}
        return {**recorded, "held": held}

    def slow_run_under_a_supervisor(self, seconds: str = "60") -> Fixture:
        """FX-2 merged by hand, then a running supervisor whose first pass starts a slow fullHealth after it."""
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        (fx.tmp / "health").write_text("slow")
        fx.env["FX_HEALTH_SECONDS"] = seconds
        code, out = fx.flow("lead-run", "--resume", cwd=fx.main, env=running(fx))
        self.assertEqual(code, 0, out)
        wait_for(lambda: self.run_lock(fx)["held"], "the fullHealth run to start", 60)
        return fx

    def test_a_supervisor_keeps_passing_restarts_serve_and_keeps_needs_you_fresh_while_it_runs(self) -> None:
        fx = self.slow_run_under_a_supervisor()
        run = self.run_lock(fx)
        seen = len(passes(fx))
        wait_for(lambda: len(passes(fx)) >= seen + 2, "two passes while fullHealth runs", 30)
        self.assertTrue(self.run_lock(fx)["held"], "the run ended before the passes it should run beside")
        running_pass = passes(fx)[-1]
        self.assertTrue(running_pass["fullHealth"].startswith("fullHealth running at "), running_pass)
        self.assertTrue(any(item["source"] == "fullHealth" and item["holding"] and "running" in item["text"]
                            for item in running_pass["needsYou"]), running_pass["needsYou"])
        needs = json.loads((fx.tmp / "record/needs-you.json").read_text())
        stale = datetime.datetime.strptime(needs["staleAfter"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
        self.assertGreater(stale, datetime.datetime.now(datetime.timezone.utc), needs)
        serve = lambda: next(child["pid"] for child in settled_lock(fx)["children"] if child["name"] == "serve")
        before = serve()
        os.kill(before, signal.SIGKILL)
        wait_for(lambda: serve() != before and alive(serve()), "serve to be restarted while fullHealth runs", 30)
        self.assertEqual(self.run_lock(fx).get("pid"), run["pid"], "the run was started again")
        self.assertEqual(health_runs(fx), 1)

    def test_passes_beside_a_run_and_a_second_start_never_run_it_twice(self) -> None:
        fx = self.slow_run_under_a_supervisor("8")
        head = self.run_lock(fx)["head"]
        # A second start for the same head, as a racing pass would make, leaves the first run alone and runs nothing.
        code, out = fx.flow("full-health-run", "--head", head, "--record", self.identity(fx), cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("already runs", out)
        wait_for(lambda: self.results(fx), "the run's result", 60)
        seen = len(passes(fx))
        wait_for(lambda: len(passes(fx)) >= seen + 2, "two more passes", 30)
        self.assertEqual(health_runs(fx), 1, "fullHealth ran twice for one merge")
        self.assertEqual([e["kind"] for e in self.base_events(fx)], ["base.verified"])
        # A start that takes the lock just after a run let it go finds the result standing, and runs nothing.
        code, out = fx.flow("full-health-run", "--head", head, "--record", self.identity(fx), cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("already; this start runs nothing", out)
        self.assertEqual(health_runs(fx), 1)
        # One started for a record that was replaced since runs nothing either.
        self.assertTrue(self.serving_lock_free(fx))
        code, out = fx.flow("full-health-run", "--head", "f" * 40, "--record", "2000-01-01T00:00:00Z", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("the record was replaced since this run was started", out)
        self.assertEqual(health_runs(fx), 1)

    def test_a_supervisor_killed_mid_run_leaves_a_run_the_next_one_adopts(self) -> None:
        fx = self.slow_run_under_a_supervisor("10")
        run = self.run_lock(fx)
        os.killpg(settled_lock(fx)["pid"], signal.SIGKILL)
        self.assertTrue(alive(run["pid"]), "the run died with its supervisor")
        code, out = fx.flow("lead-run", "--resume", cwd=fx.main, env=running(fx))
        self.assertEqual(code, 0, out)
        wait_for(lambda: self.results(fx), "the adopted run's result", 60)
        seen = len(passes(fx))
        wait_for(lambda: len(passes(fx)) >= seen + 2, "two passes after it", 30)
        self.assertEqual(health_runs(fx), 1, "the resumed supervisor started a second run")
        self.assertEqual([e["kind"] for e in self.base_events(fx)], ["base.verified"])

    def test_a_run_killed_with_its_supervisor_is_started_again_once(self) -> None:
        fx = self.slow_run_under_a_supervisor("300")
        wait_for(lambda: health_runs(fx) == 1 and self.run_lock(fx).get("command"), "the run's command to start", 30)
        run = self.run_lock(fx)
        self.assertTrue(Path(run["checkout"]).exists())
        os.killpg(settled_lock(fx)["pid"], signal.SIGKILL)
        os.killpg(run["pid"], signal.SIGKILL)
        wait_for(lambda: not self.run_lock(fx)["held"], "the killed run to let its lock go", 15)
        # Its command, in a group of its own, outlived it; the restarted run stops it before running its own.
        self.assertTrue(alive(run["command"]))
        fx.env["FX_HEALTH_SECONDS"] = "2"
        code, out = fx.flow("lead-run", "--resume", cwd=fx.main, env=running(fx))
        self.assertEqual(code, 0, out)
        wait_for(lambda: not alive(run["command"]), "the killed run's command to be stopped", 30)
        self.assertIn(f"stopped the command (pid {run['command']})", (fx.tmp / "flow-log/FX-1.full-health.log").read_text())
        wait_for(lambda: not Path(run["checkout"]).exists(), "the killed run's checkout to be removed", 30)
        wait_for(lambda: self.results(fx), "the restarted run's result", 60)
        self.assertNotIn(run["checkout"], sh("git", "worktree", "list", cwd=fx.main).stdout)
        seen = len(passes(fx))
        wait_for(lambda: len(passes(fx)) >= seen + 2, "two passes after it", 30)
        self.assertEqual(health_runs(fx), 2)
        self.assertEqual(len(self.results(fx)), 1)
        self.assertEqual([e["kind"] for e in self.base_events(fx)], ["base.verified"])

    def identity(self, fx: Fixture) -> str:
        return record(fx)["journal"][0]["at"]

    def serving_lock_free(self, fx: Fixture) -> bool:
        return not self.run_lock(fx)["held"]

    def test_the_close_waits_for_a_run_and_closes_on_its_result(self) -> None:
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "done", "--state", "done")
        with open(fx.tmp / "flow-log/plan-changes.jsonl", "a") as changes:
            changes.write(json.dumps({"type": "change", "id": "c1", "kind": "removed", "subjects": ["FX-3"], "by": "lead",
                                      "why": "Out of scope.", "at": "2026-01-01T00:00:00Z"}) + "\n")
        (fx.tmp / "health").write_text("slow")
        fx.env["FX_HEALTH_SECONDS"] = "10"
        code, out = fx.flow("lead-run", "--resume", cwd=fx.main, env=running(fx))
        self.assertEqual(code, 0, out)
        wait_for(lambda: self.run_lock(fx)["held"], "the fullHealth run to start", 60)
        seen = len(passes(fx))
        wait_for(lambda: len(passes(fx)) >= seen + 2, "two passes while it runs", 30)
        while_running = [p for p in passes(fx)[seen:] if self.run_lock(fx)["held"]]
        self.assertTrue(while_running)
        for held in (p["closeHeldBy"] for p in while_running):
            self.assertTrue(any(h.startswith("fullHealth running at ") for h in held), held)
        self.assertEqual(kinds(fx, "delivery.closed"), [], "the delivery closed while fullHealth ran")
        wait_for(lambda: kinds(fx, "delivery.closed"), "the close once the run passed", 90)
        self.assertEqual(health_runs(fx), 1)
        self.assertEqual([e["kind"] for e in self.base_events(fx)], ["base.verified"])

    def test_a_merge_during_a_run_is_checked_by_the_next_run_at_its_head(self) -> None:
        fx = self.slow_run_under_a_supervisor("15")
        first = self.run_lock(fx)["head"]
        hand_off(fx, "FX-3", "lane-2")
        code, out = fx.flow("lead-accept", "FX-3", cwd=fx.main)
        self.assertEqual(code, 0, out)
        wait_for(lambda: [e for e in kinds(fx, "candidate.merged") if e.get("subject") == "FX-3"], "serve merging FX-3", 90)
        self.assertTrue(self.run_lock(fx)["held"], "the first run ended before FX-3 landed")
        (fx.tmp / "health").write_text("pass")
        wait_for(lambda: len(self.results(fx)) == 2, "a run at FX-3's head", 120)
        second = self.results(fx)[-1]
        self.assertNotEqual(second["head"], first)
        self.assertEqual(second["merge"]["subject"], "FX-3")
        self.assertEqual(health_runs(fx), 2)
        # The run writes its result, then journals what it means for the base.
        wait_for(lambda: len(self.base_events(fx)) == 2, "the second run's base entry", 30)
        self.assertIn("after FX-3's merge", self.base_events(fx)[-1]["summary"])

    def test_lead_close_stops_a_run_a_once_pass_waits_on(self) -> None:
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        (fx.tmp / "health").write_text("slow")
        fx.env["FX_HEALTH_SECONDS"] = "300"
        once = subprocess.Popen([sys.executable, "-B", str(fx.tool), "lead-run", "--once"], cwd=fx.main, env=fx.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: once.poll() is None and os.killpg(once.pid, signal.SIGKILL))
        wait_for(lambda: self.run_lock(fx).get("command"), "the --once pass's run to start", 60)
        run = self.run_lock(fx)
        state_tool(fx, "journal", "--kind", "delivery.closed", "--summary", "FX-1 closed by hand.")
        started = time.monotonic()
        code, out = fx.flow("lead-close", cwd=fx.main)
        self.assertIn(code, (0, 1), out)
        self.assertLess(time.monotonic() - started, 120, "lead-close waited out the --once pass's run")
        self.assertIn(f"stopped the fullHealth run at {run['head'][:10]} (pid {run['pid']})", out)
        self.assertEqual(once.wait(timeout=60), 0)
        self.assertEqual(self.results(fx), [])

    def test_a_recorded_command_whose_start_time_differs_is_left_alone(self) -> None:
        fx = self.fixture()
        # A live group leader that reuses the recorded pid, as far as the lock can tell: its start time differs.
        other = subprocess.Popen(["sleep", "120"], start_new_session=True)
        self.addCleanup(lambda: other.poll() is None and os.killpg(other.pid, signal.SIGKILL))
        lock = fx.tmp / "flow-log/FX-1.full-health.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"pid": 99999, "head": "e" * 40, "command": other.pid,
                                    "commandStarted": "Thu Jan  1 00:00:00 1970"}))
        code, out = fx.flow("full-health-run", "--head", "f" * 40, "--record", "2000-01-01T00:00:00Z", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertNotIn("stopped the command", out)
        self.assertIsNone(other.poll(), "a process that only shares the recorded pid was killed")

    def test_lead_close_stops_a_run_and_records_no_result(self) -> None:
        fx = self.slow_run_under_a_supervisor()
        wait_for(lambda: self.run_lock(fx).get("command"), "the run's command to start", 30)
        run = self.run_lock(fx)
        state_tool(fx, "journal", "--kind", "delivery.closed", "--summary", "FX-1 closed by hand.")
        code, out = fx.flow("lead-close", cwd=fx.main)
        self.assertIn(code, (0, 1), out)
        self.assertIn(f"stopped the fullHealth run at {run['head'][:10]} (pid {run['pid']}); it recorded no result", out)
        self.assertFalse(self.run_lock(fx)["held"])
        wait_for(lambda: not alive(run["pid"]), "the run to be gone", 15)
        self.assertEqual(self.results(fx), [])
        self.assertEqual(self.base_events(fx), [])
        # Stopped by SIGTERM, it stopped its command and removed its checkout itself.
        self.assertFalse(alive(run["command"]))
        self.assertFalse(Path(run["checkout"]).exists(), "the stopped run's checkout was left behind")
        self.assertNotIn("run-delivery-full-health-", sh("git", "worktree", "list", cwd=fx.main).stdout)


if __name__ == "__main__":
    unittest.main()
