"""The delivery's supervisor: `lead-run`, which does every mechanical lead duty pass after pass.

Each case builds a throwaway repository with a local bare remote, a binding whose queue really merges (git only),
and a fake forge whose pull requests open as drafts. The process cases start the real supervisor and stop every
process they start; the pass cases run `lead-run --once`, one pass at a time, so what each pass does is exact.
"""

from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import sys
import time
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
import json, os, signal
out = os.environ.get("FX_SIGNALS")
if out:
    ignored = [name for name in ("SIGINT", "SIGHUP", "SIGQUIT") if signal.getsignal(getattr(signal, name)) == signal.SIG_IGN]
    with open(out, "a") as log:
        log.write(json.dumps({"cwd": os.getcwd(), "ignored": ignored}) + "\n")
'''

FULL_HEALTH = r'''
import os, pathlib, sys
verdict = pathlib.Path(os.environ["FX_HEALTH"])
with open(str(verdict) + ".runs", "a") as runs:
    runs.write("run\n")
sys.exit(1 if verdict.exists() and verdict.read_text().strip() == "fail" else 0)
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


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


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
        # Started from a parent that ignores SIGINT and SIGHUP, as nohup and a background `&` leave them.
        ignoring = lambda: [signal.signal(number, signal.SIG_IGN) for number in (signal.SIGINT, signal.SIGHUP)]
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
        return passes(fx)[-1]

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
        hand_off(fx, "FX-2", "lane-1")
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        queue_run(fx, "FX-2")
        stopped = kinds(fx, "candidate.stopped")
        self.assertTrue(stopped and "merge_refused" in stopped[-1]["summary"], stopped)
        handed = len(kinds(fx, "candidate.handed"))
        forge = json.loads((fx.tmp / "forge.json").read_text())
        forge["https://forge.test/pr/1"]["draft"] = False  # the lead marks it ready by hand
        (fx.tmp / "forge.json").write_text(json.dumps(forge))
        code, out = fx.flow("lead-accept", "--requeue", "FX-2", cwd=fx.main)
        self.assertEqual(code, 0, out)
        queue_run(fx, "FX-2")
        self.assertTrue(kinds(fx, "candidate.merged"))
        self.assertEqual(len(kinds(fx, "candidate.handed")), handed, "the requeue needed a new hand-off")
        code, out = fx.flow("lead-accept", "--requeue", "FX-2", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("isn't stopped (merged)", out)

    def test_each_finish_line_is_journalled_once_when_its_proofs_are_done(self) -> None:
        fx = self.fixture()
        path = fx.main / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"] = [{"id": "one-works", "text": "One works"}]
        d["stories"]["FX-3"]["criteria"] = [{"id": "two-works", "text": "Two works"}]
        d["finish"] = [{"id": "one", "text": "One is done", "covers": {"criteria": ["one-works"], "proofs": ["merged"]}},
                       {"id": "two", "text": "Two is done", "covers": {"criteria": ["two-works"], "proofs": ["merged"]}}]
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: two finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", "--replace", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.once(fx)
        self.assertEqual(kinds(fx, "finish.met"), [])
        self.merged(fx, "FX-2", "lane-1")
        # The lead journals line two by hand, ahead of its proofs: the supervisor never journals it again.
        state_tool(fx, "journal", "--kind", "finish.met", "--subject", "two", "--summary", "two: by hand")
        for _ in range(2):
            self.once(fx)
        met = kinds(fx, "finish.met")
        self.assertEqual([e["subject"] for e in met], ["two", "one"], met)
        self.assertEqual({line["id"]: line["met"] for line in record(fx)["state"]["finish"]}, {"one": True, "two": False})
        self.merged(fx, "FX-3", "lane-2")
        self.once(fx)
        self.assertEqual([e["subject"] for e in kinds(fx, "finish.met")], ["two", "one"])
        self.assertTrue(all(line["met"] for line in record(fx)["state"]["finish"]))

    def holds(self, fx: Fixture) -> list[str]:
        return self.once(fx)["closeHeldBy"]

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
        # A planned story nobody took holds the close.
        self.assertIn("not handed off: FX-2, FX-3", self.holds(fx))
        wt = self.merged(fx, "FX-2", "lane-1")
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
        code, out = fx.flow("lead-resolve", str(blocker), "--why", "again", cwd=m)
        self.assertEqual(code, 2, out)
        # A failing fullHealth holds it, and isn't run again at the same head until lead-resolve asks.
        (fx.tmp / "health").write_text("fail")
        self.assertEqual(self.holds(fx), [], "something besides fullHealth still holds the close")
        held = self.holds(fx)
        self.assertTrue(any(h.startswith("fullHealth failed at") for h in held), held)
        self.holds(fx)
        runs = lambda: len((fx.tmp / "health.runs").read_text().splitlines())
        self.assertEqual(runs(), 1, "fullHealth ran again at the same head")
        code, out = fx.flow("lead-resolve", "full-health", "--why", "flaky", cwd=m)
        self.assertEqual(code, 0, out)
        (fx.tmp / "health").write_text("pass")
        last = self.once(fx)
        self.assertEqual(runs(), 2)
        # Then it closes: delivery.closed, the archive, the friction note, and the supervisor's stop.
        self.assertEqual(last["closeHeldBy"], [], last)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        closed = json.loads((fx.tmp / "flow-log/FX-1.closed.json").read_text())
        archive = Path(closed["archive"])
        self.assertTrue((archive / "record/journal.jsonl").exists())
        self.assertTrue(Path(str(archive) + ".friction.md").exists(), last)
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertIn(f"supervisor: stopped after closing the delivery; archive {archive}", out)
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
        return passes(fx)[-1]

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

    def test_with_a_host_publisher_the_close_waits_for_the_outbox_ack_and_by_hand_it_doesnt(self) -> None:
        for updates in ("host", "manual"):
            with self.subTest(updates=updates):
                fx, backend = self.fixture(updates=updates)
                self.closable(fx)
                ack = lambda: state_tool(fx, "ack", "--version", str(record(fx)["state"]["version"]), "--through",
                                         str(max(e["id"] for e in record(fx)["journal"])))
                if updates == "host":
                    held = self.once(fx)["closeHeldBy"]
                    self.assertTrue(any(h.startswith("The host outbox holds") for h in held), held)
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


if __name__ == "__main__":
    unittest.main()
