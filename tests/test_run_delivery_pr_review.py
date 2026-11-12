"""The pipeline PR review: its configuration, the wait, the record, triage, and the checks before a merge.

`review.pr` is either the legacy `{author, waitSeconds}`, one reviewer, or `{waitSeconds?, settleSeconds?, reviewers:
[...]}`, several, each required or optional. The legacy shape is characterized first, as it behaved before the list
existed, and those rows stay as they are. The rest drive the list.

Every row runs the tool from an installed `run-delivery` release built from this tree, on the fixture repository
`tests/test_run_delivery.py` builds, against a scripted fake forge adapter. Time, sleep and the poll interval are the
tool's injected clock (`RUN_DELIVERY_CLOCK`), so no row sleeps for real or reaches a real forge.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_run_delivery import (
    CONTEXT_TODO, POSIX_ONLY, Fixture, bytecode_under, commit, installed_release, setup_delivery, sh, write_results,
)


# A forge adapter that answers find, open and get from FAKE_FORGE_STATE as the fixture's own does, and reviews from
# FAKE_FORGE_REVIEWS. FAKE_FORGE_SCRIPT, when set, scripts any command: {command: [step, ...]}, each step
# {"exit": n, "stderr": text} or {"answer": object}, taken in order and the last one repeated. Every call is logged to
# FAKE_FORGE_CALLS.
PR_FORGE = r'''
import argparse, json, os, subprocess, sys
state = os.environ["FAKE_FORGE_STATE"]
prs = json.load(open(state)) if os.path.exists(state) else {}
p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="c")
a = sub.add_parser("find"); a.add_argument("--source")
a = sub.add_parser("open"); a.add_argument("--source"); a.add_argument("--destination"); a.add_argument("--title"); a.add_argument("--body-file")
a = sub.add_parser("get"); a.add_argument("--url")
a = sub.add_parser("reviews"); a.add_argument("--url"); a.add_argument("--head")
args = p.parse_args()
if os.environ.get("FAKE_FORGE_CALLS"):
    open(os.environ["FAKE_FORGE_CALLS"], "a").write(args.c + "\n")
script_path = os.environ.get("FAKE_FORGE_SCRIPT", "")
script = json.load(open(script_path)) if script_path and os.path.exists(script_path) else {}
if script.get(args.c):
    taken = script.setdefault("taken", {})
    index = taken.get(args.c, 0)
    steps = script[args.c]
    step = steps[min(index, len(steps) - 1)]
    taken[args.c] = index + 1
    json.dump(script, open(script_path, "w"))
    if "exit" in step:
        sys.stderr.write(step.get("stderr", "") + "\n")
        sys.exit(step["exit"])
    print(json.dumps(step["answer"]))
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

PR = "https://forge.test/pr/1"
# The fixture's pipeline: one check created at T0 and passed, the clock's epoch for T0, and the reviews' times after it.
T0 = "2026-01-01T00:00:10"
T0_EPOCH = 1767225610.0
PASSED = {"name": "pipe", "state": "passed", "at": T0}
PENDING = {**PASSED, "state": "pending"}
FAILED = {**PASSED, "state": "failed"}
LEGACY = {"author": "Pipeline Bot", "waitSeconds": 1}


def comment(author: str, at: str, body: str, account: str | None = None) -> dict:
    found = {"author": author, "at": at, "body": body}
    if account is not None:
        found["account"] = account
    return found


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PrReviewCase(unittest.TestCase):
    """A story ready to hand off: its gates passed, one aligned review round, and its draft PR open. Each case then
    configures review.pr and scripts the forge's answers."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def tearDown(self) -> None:
        self.assertEqual(bytecode_under(self.generation), [], "the case left bytecode in the installed generation")

    def setUp(self) -> None:
        fx = self.fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        m = fx.main
        path = setup_delivery(fx)
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["inApp"] = "none"  # no in-app checks: what this module drives is the PR review
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: FX-2 needs no in-app checks")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        self.ok("lead-init", cwd=m)
        self.ok("lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane 1", cwd=m)
        wt = self.wt = fx.lane("FX-2")
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        self.head = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        sh("git", "push", "-q", "-u", "origin", "HEAD:feature/FX-2", cwd=wt)
        self.ok("gate", "FX-2", cwd=wt)
        review = wt / ".orchestration/review/FX-2"
        review.mkdir(parents=True, exist_ok=True)
        fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)  # seeds the context
        context = review / "context.md"
        context.write_text(context.read_text().replace(CONTEXT_TODO, "- fixture evidence"))
        self.ok("review-prepare", "FX-2", "--round", "1", cwd=wt)
        write_results(review, 1, "FX-2", fx.lenses(wt))
        self.ok("review-reduce", "FX-2", "--round", "1", cwd=wt)
        body = fx.tmp / "body.md"
        body.write_text("Summary\n")
        self.ok("open-pr", "FX-2", "--title", "FX-2: one", "--body-file", body, cwd=wt)
        self.review = review
        self.forge_script = fx.tmp / "pr_forge.py"
        self.forge_script.write_text(PR_FORGE)
        self.reviews = fx.tmp / "reviews.json"
        self.script = fx.tmp / "forge-script.json"
        self.calls = fx.tmp / "forge-calls.log"
        self.clock_file = fx.tmp / "clock.json"
        fx.env.update(FAKE_FORGE_REVIEWS=str(self.reviews), FAKE_FORGE_SCRIPT=str(self.script),
                      FAKE_FORGE_CALLS=str(self.calls), RUN_DELIVERY_CLOCK=str(self.clock_file))
        self.clock(T0_EPOCH + 60)

    # -- helpers -------------------------------------------------------------------------------------------------

    def ok(self, *argv, cwd: Path) -> str:
        code, out = self.fx.flow(*argv, cwd=cwd)
        self.assertEqual(code, 0, out)
        return out

    def configure(self, pr: dict | None, **extra) -> None:
        """review.pr (None leaves it out), with the scripted forge, through RUN_DELIVERY_CONFIG."""
        conf = json.loads((self.wt / ".agents/run-delivery.json").read_text())
        conf["forge"] = {"argv": [sys.executable, "-B", str(self.forge_script)]}
        if pr is None:
            conf.get("review", {}).pop("pr", None)
        else:
            conf["review"]["pr"] = pr
        conf.update(extra)
        path = self.fx.tmp / "flow-pr-review.json"
        path.write_text(json.dumps(conf))
        self.fx.env["RUN_DELIVERY_CONFIG"] = str(path)

    def answer(self, checks: list[dict], reviews: list[dict], **members) -> None:
        self.reviews.write_text(json.dumps({"checks": checks, "reviews": reviews, **members}))

    def scripted(self, **commands) -> None:
        self.script.write_text(json.dumps(commands))

    def clock(self, now: float) -> None:
        self.clock_file.write_text(json.dumps({"now": now, "sleeps": []}))

    def sleeps(self) -> list[float]:
        return json.loads(self.clock_file.read_text()).get("sleeps", [])

    def now(self) -> float:
        return json.loads(self.clock_file.read_text())["now"]

    def forge_calls(self) -> list[str]:
        return self.calls.read_text().split() if self.calls.exists() else []

    def flow(self, *argv, cwd: Path | None = None) -> tuple[int, str]:
        return self.fx.flow(*argv, cwd=cwd or self.wt)

    def gate(self, *extra: str, head: str | None = None) -> tuple[int, str, str]:
        """pr-review-current, the queue's reviewGate, as the queue runs it: stdout and stderr apart."""
        done = subprocess.run([sys.executable, "-B", str(self.fx.tool), "pr-review-current", "--branch", "feature/FX-2",
                               "--head", head or self.head, *extra], cwd=self.fx.main, env=self.fx.env, text=True,
                              capture_output=True, timeout=300)
        self.assertNotIn("Traceback", done.stderr)
        return done.returncode, done.stdout, done.stderr

    def record(self) -> dict:
        return json.loads((self.review / "pr-review.json").read_text())

    def handoff(self, *triage: str) -> tuple[int, str]:
        return self.flow("handoff", "FX-2", "--pr", PR, *[x for t in triage for x in ("--pr-review-triage", t)])

    def accept(self) -> tuple[int, str]:
        return self.flow("lead-accept", "FX-2", cwd=self.fx.main)

    def decisions(self) -> str:
        """The journaled decisions' summaries, which carry what lead-accept checked."""
        journal = self.fx.tmp / "record/journal.jsonl"
        entries = [json.loads(line) for line in journal.read_text().splitlines()]
        return "\n".join(e.get("summary", "") for e in entries if e.get("kind") == "decision.recorded")


class LegacyCharacterizationTests(PrReviewCase):
    """The legacy `{author, waitSeconds}` configuration, pinned as it behaved before the reviewer list: its printed
    text, refusals, record and exit codes, for pr-review, handoff, lead-accept and pr-review-current."""

    def setUp(self) -> None:
        super().setUp()
        self.configure(LEGACY)

    def test_pr_review_prints_saves_and_refuses_as_before(self) -> None:
        with self.subTest("a pending pipeline"):
            self.answer([PENDING], [comment("Pipeline Bot", "2026-01-01T00:00:05", "old")])
            code, out = self.flow("pr-review", "FX-2")
            self.assertEqual(code, 2, out)
            self.assertIn(f"run-delivery: no Pipeline Bot review after the pipeline for {self.head[:10]} yet; run with "
                          "--wait: pipe pending", out)
        with self.subTest("a failed pipeline"):
            self.answer([FAILED], [])
            code, out = self.flow("pr-review", "FX-2", "--wait")
            self.assertEqual(code, 2, out)
            self.assertIn(f"run-delivery: the pipeline failed at {self.head[:10]}: pipe failed", out)
        with self.subTest("a review from before the pipeline, and someone else's"):
            self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:05", "stale"),
                                   comment("Someone", "2026-01-01T00:00:20", "not the bot")])
            code, out = self.flow("pr-review", "FX-2")
            self.assertEqual(code, 2, out)
            self.assertIn(f"no Pipeline Bot review after the pipeline for {self.head[:10]} yet; run with --wait: "
                          "pipe passed", out)
        with self.subTest("no checks"):
            self.answer([], [])
            code, out = self.flow("pr-review", "FX-2", "--wait")
            self.assertEqual(code, 2, out)
            self.assertIn(f"no Pipeline Bot review after the pipeline for {self.head[:10]} yet: no checks", out)
        self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:30", "Should fix: x")])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertIn(f"Pipeline Bot review of {self.head[:10]} (2026-01-01T00:00:30), saved for the hand-off:\n\n"
                      "Should fix: x\n\nTriage every finding like a lens finding: fix what blocks in the lane (then "
                      "gates, a lens round and this review again at the new head), and fix or defer the rest. Hand off "
                      "with --pr-review-triage saying which.", out)
        self.assertEqual(self.record(), {"head": self.head, "url": PR, "author": "Pipeline Bot",
                                         "at": "2026-01-01T00:00:30", "body": "Should fix: x"})

    def test_an_empty_bodied_review_still_counts_and_extra_members_and_a_string_wait_are_read_as_before(self) -> None:
        self.configure({"author": "Pipeline Bot", "waitSeconds": "1", "note": "ignored"})
        self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:30", "")])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record()["body"], "")

    def test_a_legacy_configuration_with_an_adapter_that_returns_no_new_member_is_unchanged(self) -> None:
        """Decision 5: the legacy shape reads none of account, approvals or reports."""
        self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:30", "fine")])
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)
        self.assertEqual(self.gate()[0], 0)

    def test_a_missing_author_is_refused_by_name(self) -> None:
        self.configure({"waitSeconds": 1})
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertIn("review.pr needs `author`: the name the pipeline's review comment is posted as", out)

    def test_handoff_needs_the_review_at_the_head_and_its_triage(self) -> None:
        code, out = self.handoff()
        self.assertEqual(code, 2, out)
        self.assertIn(f"no PR review recorded at {self.head[:10]}: run ", out)
        self.assertIn("pr-review FX-2 --wait", out)
        self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:30", "Should fix: x")])
        self.flow("pr-review", "FX-2")
        code, out = self.handoff()
        self.assertEqual(code, 2, out)
        self.assertIn("say how the PR review's findings were handled: --pr-review-triage TEXT", out)
        code, out = self.handoff("fixed x")
        self.assertEqual(code, 0, out)
        self.assertIn("- PR review: Pipeline Bot at 2026-01-01T00:00; triage: fixed x", out)
        self.assertEqual(self.record()["triage"], "fixed x")

    def test_lead_accept_rechecks_the_review_at_the_head(self) -> None:
        self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:30", "Should fix: x")])
        self.flow("pr-review", "FX-2")
        self.assertEqual(self.handoff("fixed x")[0], 0)
        with self.subTest("changed after the lane read it"):
            self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:40", "new point")])
            code, out = self.accept()
            self.assertEqual(code, 1, out)
            self.assertIn("the PR review changed after the lane read it (2026-01-01T00:00:30 -> 2026-01-01T00:00:40); "
                          "the lane must read and triage it again", out)
        with self.subTest("no longer current"):
            self.answer([PENDING], [comment("Pipeline Bot", "2026-01-01T00:00:30", "Should fix: x")])
            code, out = self.accept()
            self.assertEqual(code, 1, out)
            self.assertIn(f"the PR's pipeline review isn't current at {self.head[:10]} (pending)", out)
        self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:30", "Should fix: x")])
        code, out = self.accept()
        self.assertEqual(code, 0, out)
        self.assertIn("PR review by Pipeline Bot at 2026-01-01T00:00 triaged: fixed x", self.decisions())

    def test_pr_review_current_passes_only_a_current_review_with_one_line_refusals(self) -> None:
        cases = [
            ("no checks and no review", [], [], f"no current Pipeline Bot review at {self.head[:10]}: no checks"),
            ("a pending pipeline", [PENDING], [comment("Pipeline Bot", "2026-01-01T00:00:30", "x")], "pipe pending"),
            ("a failed pipeline", [FAILED], [], f"the pipeline failed at {self.head[:10]}: pipe failed"),
        ]
        for what, checks, reviews, reason in cases:
            with self.subTest(what):
                self.answer(checks, reviews)
                code, out, err = self.gate()
                self.assertEqual(code, 2, err)
                self.assertEqual(len(err.strip().splitlines()), 1, err)
                self.assertIn(reason, err)
        with self.subTest("waited"):
            self.answer([PENDING], [])
            code, _, err = self.gate("--wait")
            self.assertEqual(code, 2, err)
            self.assertIn(f"no current Pipeline Bot review at {self.head[:10]} (waited 1s): pipe pending", err)
        self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:30", "fine")])
        code, out, err = self.gate()
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(out, f"Pipeline Bot review of {self.head[:10]} at 2026-01-01T00:00:30 is current: pipe passed\n")


if __name__ == "__main__":
    unittest.main()
