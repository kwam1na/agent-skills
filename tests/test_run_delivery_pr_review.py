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
# {"exit": n, "stderr": text}, {"answer": object} or {"pass": true} (answered as if unscripted), taken in order and the
# last one repeated. Every call is logged to FAKE_FORGE_CALLS.
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
    if "answer" in step:
        print(json.dumps(step["answer"]))
        sys.exit(0)
    # {"pass": true}: answered as an unscripted call is
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



# The list: a required reviewer matched by account, and an optional one matched by author and signature.
PRIMARY = {"id": "primary", "account": "{u-primary}", "required": True}
CODEX = {"id": "codex", "author": "Review Bot", "signature": "<!-- codex -->", "required": False}
LISTED = {"waitSeconds": 600, "settleSeconds": 300, "reviewers": [PRIMARY, CODEX]}
AFTER = "2026-01-01T00:00:30"  # a review after the pipeline's one check


def by_primary(body: str = "Primary says: fix x", at: str = AFTER, **extra) -> dict:
    return {"author": "Primary Bot", "account": "{u-primary}", "at": at, "body": body, **extra}


def by_codex(body: str = "<!-- codex -->\nCodex says: fine", at: str = AFTER) -> dict:
    return {"author": "Review Bot", "account": "{u-review}", "at": at, "body": body}


class ConfigurationTests(PrReviewCase):
    """Decision 1: one parser for every reader. Each malformed list is refused by pr-review, pr-review-current,
    handoff, lead-accept and lead-brief, naming the reviewer and the member, and the doctor reports it wrong."""

    def readers(self) -> dict[str, tuple[int, str]]:
        code, out, err = self.gate()
        lead = self.fx.flow("lead-brief", cwd=self.fx.main)
        doctor = self.fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=self.fx.main)
        return {"pr-review": self.flow("pr-review", "FX-2"), "pr-review-current": (code, err),
                "handoff": self.handoff("fixed"), "lead-accept": self.accept(), "lead-brief": lead, "doctor": doctor}

    def test_each_malformed_list_is_refused_by_every_reader_naming_the_reviewer_and_the_member(self) -> None:
        cases = [
            ("an unknown member", {"reviewers": [{**PRIMARY, "colour": "blue"}]}, "reviewer 'primary': `colour`"),
            ("a duplicate id", {"reviewers": [PRIMARY, {**CODEX, "id": "primary"}]}, "reviewer 'primary': `id`"),
            ("an empty list", {"reviewers": []}, "`reviewers`"),
            ("a bad id", {"reviewers": [{**PRIMARY, "id": "Primary_1"}]}, "reviewer 'Primary_1': `id`"),
            ("a negative settle", {"settleSeconds": -1, "reviewers": [PRIMARY]}, "`settleSeconds`"),
            ("an approval gate without an account", {"reviewers": [{"id": "x", "author": "X", "gate": "approval"}]},
             "reviewer 'x': `account`"),
            ("a string settle", {"settleSeconds": "300", "reviewers": [PRIMARY]}, "`settleSeconds`"),
            ("a string wait", {"waitSeconds": "600", "reviewers": [PRIMARY]}, "`waitSeconds`"),
            ("an unknown top-level member", {"reviewers": [PRIMARY], "author": "X"}, "`author` is not a member"),
            ("a reviewer with neither account nor author", {"reviewers": [{"id": "x", "signature": "s"}]},
             "reviewer 'x': `account`"),
            ("an unknown gate", {"reviewers": [{**PRIMARY, "gate": "vote"}]}, "reviewer 'primary': `gate`"),
            ("a required that isn't a boolean", {"reviewers": [{**PRIMARY, "required": "yes"}]},
             "reviewer 'primary': `required`"),
        ]
        for what, pr, named in cases:
            with self.subTest(what):
                self.configure(pr)
                for reader, (code, out) in self.readers().items():
                    with self.subTest(reader=reader):
                        self.assertIn(named, out)
                        self.assertIn("review.pr", out)
                        if reader == "doctor":
                            self.assertRegex(out, r"(?m)^wrong +pipeline PR review: .*review\.pr")
                            self.assertNotEqual(code, 0, out)
                        else:
                            self.assertEqual(code, 2, out)

    def test_a_valid_two_reviewer_list_passes_every_reader(self) -> None:
        self.configure(LISTED)
        self.answer([PASSED], [by_primary(), by_codex()])
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)
        readers = self.readers()
        for reader, (code, out) in readers.items():
            with self.subTest(reader=reader):
                if reader == "handoff":
                    self.assertEqual(code, 2, out)  # a bare text with two current reviews (the triage rows)
                    self.assertIn("--pr-review-triage ID=TEXT", out)
                elif reader == "lead-accept":
                    self.assertEqual(code, 2, out)  # nothing handed off yet: refused after the configuration is read
                    self.assertIn("not in the queue", out)
                elif reader == "doctor":
                    self.assertRegex(out, r"(?m)^ok +pipeline PR review: ")
                else:
                    self.assertEqual(code, 0, out)
        self.assertIn("`primary` (required); `codex` (optional)", (self.fx.main / "docs/lane-brief.md").read_text())

    def test_the_doctor_warns_of_a_reviewer_matched_by_name_alone_and_of_a_shared_name(self) -> None:
        def row() -> tuple[str, str]:
            code, out = self.fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=self.fx.main)
            line = next(l for l in out.splitlines() if " pipeline PR review: " in l)
            return code, line
        cases = [
            ("neither account nor signature", [{"id": "a", "author": "Bot"}], "warn", "a is matched by display name"),
            ("two sharing an author, neither signed", [{"id": "a", "author": "Bot", "account": "1"},
                                                       {"id": "b", "author": "Bot", "account": "2"}],
             "warn", "a and b share the author 'Bot'"),
            ("two sharing an account, neither signed", [{"id": "a", "account": "1"}, {"id": "b", "account": "1"}],
             "warn", "a and b share the account '1'"),
            ("an account", [{"id": "a", "author": "Bot", "account": "1"}], "ok", ""),
            ("a signature", [{"id": "a", "author": "Bot", "signature": "<!-- a -->"}], "ok", ""),
            ("two sharing an author, one signed", [{"id": "a", "author": "Bot", "account": "1"},
                                                   {"id": "b", "author": "Bot", "signature": "s"}], "ok", ""),
        ]
        for what, reviewers, status, said in cases:
            with self.subTest(what):
                self.configure({"reviewers": reviewers})
                code, line = row()
                self.assertTrue(line.startswith(status), line)
                self.assertIn(said, line)
        with self.subTest("a warning doesn't fail the doctor"):
            self.configure({"reviewers": [{"id": "a", "author": "Bot", "account": "1"}]})
            ok_code, _ = row()
            self.configure({"reviewers": [{"id": "a", "author": "Bot"}]})
            warn_code, _ = row()
            self.assertEqual(warn_code, ok_code)

    def test_the_worked_review_gate_outlasts_the_wait_by_two_minutes(self) -> None:
        """Decision 6: the reviewGate's timeoutSeconds fits the wait, the last backoff and one adapter call."""
        guide = Path(__file__).resolve().parents[1] / "skills/run-delivery/references/adopting.md"
        text = guide.read_text(encoding="utf-8")
        section = text[text.index("## 7. A worked adopter"):]
        gate = json.loads(section[section.index('{"argv": ["python3", "-B", ".agent-skills/current/skills/run-delivery/'
                                                'scripts/run_delivery.py",\n                          "pr-review-current"'):
                                  ].split("},", 1)[0] + "}")
        wait = int(section.split('"waitSeconds": ', 1)[1].split("}", 1)[0])
        self.assertGreaterEqual(gate["timeoutSeconds"], wait + 120)
        config = (Path(__file__).resolve().parents[1] / "skills/run-delivery/references/config.md").read_text()
        self.assertIn("exit 75", " ".join(config.split()))
        self.assertIn("at least 120", " ".join(config.split()))


class MatchingTests(PrReviewCase):
    """Decision 2: by account, else by author, and by signature as plain text; the list never matches an empty body;
    the latest matching comment by `at` is the review."""

    def setUp(self) -> None:
        super().setUp()
        self.configure({"reviewers": [PRIMARY]})

    def read(self, *reviews: dict) -> tuple[int, str]:
        self.answer([PASSED], list(reviews))
        return self.flow("pr-review", "FX-2")

    def test_an_account_matches_whatever_the_display_name(self) -> None:
        code, out = self.read(by_primary(author="Primary (renamed)"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record()["reviewers"]["primary"]["author"], "Primary (renamed)")

    def test_another_account_under_the_same_name_doesnt_match(self) -> None:
        code, out = self.read(by_primary(account="{u-impostor}"))
        self.assertEqual(code, 2, out)
        self.assertIn("no current review from primary", out)

    def test_a_signature_is_plain_text_not_a_pattern(self) -> None:
        self.configure({"reviewers": [{"id": "primary", "author": "Primary Bot", "signature": "[primary](v1)?*"}]})
        code, out = self.read(by_primary(body="primary says x"))  # what the pattern would match, without the text
        self.assertEqual(code, 2, out)
        code, out = self.read(by_primary(body="[primary](v1)?* says x"))
        self.assertEqual(code, 0, out)

    def test_an_empty_body_never_matches_in_the_list(self) -> None:
        code, out = self.read(by_primary(body=""))
        self.assertEqual(code, 2, out)
        self.assertIn("no current review from primary", out)

    def test_the_latest_matching_comment_is_the_review_whatever_the_order(self) -> None:
        newer = by_primary(body="newer", at="2026-01-01T00:00:40")
        older = by_primary(body="older", at="2026-01-01T00:00:05")
        middle = by_primary(body="middle", at="2026-01-01T00:00:20")
        for order in ([newer, older], [older, newer], [older, newer, middle], [middle, newer, older]):
            with self.subTest(order=[r["body"] for r in order]):
                code, out = self.read(*order)
                self.assertEqual(code, 0, out)
                self.assertEqual(self.record()["reviewers"]["primary"]["body"], "newer")

    def test_members_the_adapter_doesnt_return(self) -> None:
        with self.subTest("account configured, comments without it: refused, naming the member"):
            plain = by_primary()
            del plain["account"]
            code, out = self.read(plain)
            self.assertEqual(code, 2, out)
            self.assertIn("returned no `account` on each review comment", out)
        with self.subTest("comments with accounts that match nobody: pending"):
            code, out = self.read(by_codex())
            self.assertEqual(code, 2, out)
            self.assertIn("no current review from primary", out)


class WaitTests(PrReviewCase):
    """Decision 4, on the injected clock: each poll classifies every reviewer; the wait ends when none is pending
    and no check is; a failed check refuses at once."""

    def setUp(self) -> None:
        super().setUp()
        self.configure(LISTED)

    def test_one_current_and_one_pending_before_the_settle_keeps_waiting_until_both_are_current(self) -> None:
        self.scripted(reviews=[{"answer": {"checks": [PASSED], "reviews": [by_primary()]}},
                               {"answer": {"checks": [PASSED], "reviews": [by_primary(), by_codex()]}}])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sleeps(), [30])
        self.assertEqual(self.forge_calls().count("reviews"), 2)
        self.assertEqual({k: v["state"] for k, v in self.record()["reviewers"].items()},
                         {"primary": "current", "codex": "current"})

    def test_both_current_ends_the_wait_at_once(self) -> None:
        self.answer([PASSED], [by_primary(), by_codex()])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sleeps(), [])
        self.assertIn("## primary (required): Primary Bot at 2026-01-01T00:00:30\n\nPrimary says: fix x", out)
        self.assertIn("## codex (optional): Review Bot at 2026-01-01T00:00:30\n\n<!-- codex -->\nCodex says: fine", out)

    def test_an_optional_reviewer_with_nothing_after_the_settle_is_absent(self) -> None:
        self.clock(T0_EPOCH + 301)
        self.answer([PASSED], [by_primary()])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record()["reviewers"]["codex"], {"state": "absent", "required": False})
        self.assertIn("## codex (optional): absent, no review within 300s after the pipeline went green", out)
        with self.subTest("at the settle's end it is still pending"):
            self.clock(T0_EPOCH + 300)
            code, out = self.flow("pr-review", "FX-2")
            self.assertEqual(code, 2, out)
            self.assertIn("no current review from codex", out)

    def test_a_pending_check_keeps_the_wait_going_though_every_reviewer_is_current(self) -> None:
        self.scripted(reviews=[{"answer": {"checks": [PENDING], "reviews": [by_primary(), by_codex()]}},
                               {"answer": {"checks": [PASSED], "reviews": [by_primary(), by_codex()]}}])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sleeps(), [30])

    def test_the_settle_starts_at_the_latest_done_check_and_currency_at_the_latest_created(self) -> None:
        """Decision 4: the settle starts at the latest doneAt among the head's checks, whichever was created last, and
        a review is current only from the latest check's `at`."""
        first = {**PASSED, "doneAt": "2026-01-01T00:03:30"}  # created at T0, done at T0+200
        second = {"name": "lint", "state": "passed", "at": "2026-01-01T00:01:00", "doneAt": "2026-01-01T00:01:50"}
        self.answer([first, second], [by_primary(at="2026-01-01T00:01:10")])
        self.clock(T0_EPOCH + 200 + 300)
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertIn("no current review from codex", out)
        self.clock(T0_EPOCH + 200 + 301)
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record()["reviewers"]["codex"]["state"], "absent")
        with self.subTest("a review between the two checks' creation isn't current"):
            self.answer([first, second], [by_primary(at="2026-01-01T00:00:30")])
            code, out = self.flow("pr-review", "FX-2")
            self.assertEqual(code, 3, out)  # past the settle, a required reviewer with no current review is silent
            self.assertIn("but primary posted nothing since", out)

    def test_a_pending_check_with_no_time_yet_keeps_the_wait_going(self) -> None:
        """A queued check may carry no time (GitHub's rollup, before it starts): the wait goes on, never refused."""
        queued = {"name": "lint", "state": "pending", "at": ""}
        self.scripted(reviews=[{"answer": {"checks": [PASSED, queued], "reviews": [by_primary(), by_codex()]}},
                               {"answer": {"checks": [PASSED], "reviews": [by_primary(), by_codex()]}}])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sleeps(), [30])

    def test_a_failed_check_refuses_at_once(self) -> None:
        self.answer([FAILED], [by_primary(), by_codex()])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 2, out)
        self.assertIn(f"the pipeline failed at {self.head[:10]}: pipe failed", out)
        self.assertEqual((self.sleeps(), self.forge_calls().count("reviews")), ([], 1))

    def test_the_record_holds_each_reviewer_and_the_move_waits_for_every_required_one(self) -> None:
        tracker = {"issueUrl": "https://tracker.test/{key}",
                   "transitions": {"review": ["In Review"], "reviewWhen": "pipeline-green"}}
        self.configure(LISTED, tracker=tracker)
        self.answer([PASSED], [by_codex()])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertNotIn("Tracker move", out)
        self.clock(T0_EPOCH + 301)
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 3, out)  # a required reviewer silent after the settle
        self.assertNotIn("Tracker move", out)
        self.assertFalse((self.review / "pr-review.json").exists())
        self.answer([PASSED], [by_primary()])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertIn("Tracker move (your story FX-2)", out)
        self.assertEqual(self.record(), {
            "schema": "pr-review/2", "head": self.head, "url": PR, "reviewers": {
                "primary": {"state": "current", "required": True, "author": "Primary Bot", "account": "{u-primary}",
                           "at": AFTER, "body": "Primary says: fix x"},
                "codex": {"state": "absent", "required": False}}})


class TriageTests(PrReviewCase):
    """Decision 8: ID=TEXT for each current review; a bare TEXT only when exactly one is current."""

    def setUp(self) -> None:
        super().setUp()
        self.configure(LISTED)

    def test_two_current_reviews_each_need_their_own_triage(self) -> None:
        self.answer([PASSED], [by_primary(), by_codex()])
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)
        cases = [
            ("a bare text", ["fixed x"], "a bare --pr-review-triage TEXT is accepted only alone, with exactly one current"),
            ("one of the two", ["primary=fixed x"], "say how codex's review was handled: --pr-review-triage codex=TEXT"),
            ("an unknown id", ["primary=fixed x", "codex=nothing", "gemini=x"], "names gemini, which is no reviewer"),
        ]
        for what, given, said in cases:
            with self.subTest(what):
                code, out = self.handoff(*given)
                self.assertEqual(code, 2, out)
                self.assertIn(said, out)
                if what == "a bare text":  # and each current review is named, with the form it takes
                    self.assertIn("say how primary's review was handled: --pr-review-triage primary=TEXT", out)
                    self.assertIn("say how codex's review was handled: --pr-review-triage codex=TEXT", out)
        code, out = self.handoff("primary=fixed x", "codex=nothing to do")
        self.assertEqual(code, 0, out)
        self.assertIn("- PR review: primary: Primary Bot at 2026-01-01T00:00, triage: fixed x; codex: Review Bot at "
                      "2026-01-01T00:00, triage: nothing to do", out)
        reviewers = self.record()["reviewers"]
        self.assertEqual((reviewers["primary"]["triage"], reviewers["codex"]["triage"]), ("fixed x", "nothing to do"))

    def test_every_current_review_without_a_triage_is_named(self) -> None:
        third = {"id": "third", "author": "Third Bot", "signature": "<!-- third -->", "required": False}
        self.configure({**LISTED, "reviewers": [PRIMARY, CODEX, third]})
        self.answer([PASSED], [by_primary(), by_codex(), {"author": "Third Bot", "account": "{u-third}", "at": AFTER,
                                                          "body": "<!-- third -->\nThird says: fine"}])
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)
        code, out = self.handoff("primary=fixed x", "gemini=x")
        self.assertEqual(code, 2, out)
        for said in ("names gemini, which is no reviewer", "say how codex's review was handled",
                     "say how third's review was handled"):
            self.assertIn(said, out)
        self.assertEqual(self.handoff("primary=fixed x", "codex=fine", "third=fine")[0], 0)

    def test_with_one_current_review_a_bare_text_is_its_and_an_absent_reviewer_takes_none(self) -> None:
        self.clock(T0_EPOCH + 301)
        self.answer([PASSED], [by_primary()])
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)
        code, out = self.handoff("primary=fixed x", "codex=read it")
        self.assertEqual(code, 2, out)
        self.assertIn("names codex, which was absent: an absent reviewer takes no triage", out)
        code, out = self.handoff("fixed x", "primary=fixed x")
        self.assertEqual(code, 2, out)
        self.assertIn("a bare --pr-review-triage TEXT is accepted only alone", out)
        code, out = self.handoff("fixed x")
        self.assertEqual(code, 0, out)
        self.assertIn("- PR review: primary: Primary Bot at 2026-01-01T00:00, triage: fixed x; codex: absent", out)


class AcceptanceTests(PrReviewCase):
    """Decision 7: lead-accept re-checks every reviewer at the handed head, reading both record shapes."""

    def hand_off(self, *triage: str) -> None:
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)
        code, out = self.handoff(*triage)
        self.assertEqual(code, 0, out)

    def test_current_triaged_and_unchanged_is_accepted_and_a_changed_optional_review_is_sent_back(self) -> None:
        self.configure(LISTED)
        self.answer([PASSED], [by_primary(), by_codex()])
        self.hand_off("primary=fixed x", "codex=fine")
        self.answer([PASSED], [by_primary(), by_codex(body="<!-- codex -->\nCodex has more", at="2026-01-01T00:00:50")])
        code, out = self.accept()
        self.assertEqual(code, 1, out)
        self.assertIn("codex's PR review changed after the lane read it (2026-01-01T00:00:30 -> 2026-01-01T00:00:50); "
                      "the lane must read and triage it again", out)
        with self.subTest("its body changed and its time didn't"):
            self.answer([PASSED], [by_primary(), by_codex(body="<!-- codex -->\nCodex, edited")])
            code, out = self.accept()
            self.assertEqual(code, 1, out)
            self.assertIn("codex's PR review changed after the lane read it", out)
        self.answer([PASSED], [by_primary(), by_codex()])
        code, out = self.accept()
        self.assertEqual(code, 0, out)
        self.assertIn("PR review primary by Primary Bot at 2026-01-01T00:00 triaged: fixed x", self.decisions())
        self.assertIn("PR review codex by Review Bot at 2026-01-01T00:00 triaged: fine", self.decisions())

    def test_a_review_no_longer_current_or_never_triaged_is_refused(self) -> None:
        self.configure(LISTED)
        self.answer([PASSED], [by_primary(), by_codex()])
        self.hand_off("primary=fixed x", "codex=fine")
        with self.subTest("the pipeline at the head is running again"):
            self.answer([PENDING], [by_primary(), by_codex()])
            code, out = self.accept()
            self.assertEqual(code, 1, out)
            self.assertIn(f"primary's PR review isn't current at {self.head[:10]} (pending)", out)
        self.answer([PASSED], [by_primary(), by_codex()])
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)  # read again: the record has no triage now
        code, out = self.accept()
        self.assertEqual(code, 1, out)
        self.assertIn("the lane recorded no triage of primary's PR review at the handed head", out)

    def test_an_optional_reviewer_recorded_absent_that_is_now_current_sends_the_lane_back(self) -> None:
        self.configure(LISTED)
        self.clock(T0_EPOCH + 301)
        self.answer([PASSED], [by_primary()])
        self.hand_off("fixed x")
        self.answer([PASSED], [by_primary(), by_codex(at="2026-01-01T00:06:00")])
        code, out = self.accept()
        self.assertEqual(code, 1, out)
        self.assertIn("codex was recorded absent, but its review is now current", out)
        self.assertIn("send the lane back to read and triage it", out)

    def test_an_old_shape_record_is_reviewer_review(self) -> None:
        """A record written by the legacy shape, read under a list whose one reviewer is `review`."""
        self.configure(LEGACY)
        self.answer([PASSED], [comment("Pipeline Bot", AFTER, "Should fix: x")])
        self.hand_off("fixed x")
        self.configure({"reviewers": [{"id": "review", "author": "Pipeline Bot"}]})
        saved = self.review / "pr-review.json"
        old = saved.read_text()
        with self.subTest("at an older head"):
            saved.write_text(json.dumps({**json.loads(old), "head": "0" * 40}))
            code, out = self.accept()
            self.assertEqual(code, 1, out)
            self.assertIn(f"the lane recorded no PR review at the handed head {self.head[:10]}", out)
            saved.write_text(old)
        with self.subTest("its review changed"):
            self.answer([PASSED], [comment("Pipeline Bot", "2026-01-01T00:00:40", "new point")])
            code, out = self.accept()
            self.assertEqual(code, 1, out)
            self.assertIn("review's PR review changed after the lane read it (2026-01-01T00:00:30 -> "
                          "2026-01-01T00:00:40)", out)
        self.answer([PASSED], [comment("Pipeline Bot", AFTER, "Should fix: x")])
        code, out = self.accept()
        self.assertEqual(code, 0, out)
        self.assertIn("PR review review by Pipeline Bot at 2026-01-01T00:00 triaged: fixed x", self.decisions())


class ReviewGateTests(PrReviewCase):
    """pr-review-current under the list: every required reviewer current passes; an optional one never blocks."""

    def setUp(self) -> None:
        super().setUp()
        self.configure(LISTED)

    def test_required_reviewers_decide_the_gate(self) -> None:
        self.answer([PASSED], [by_primary(), by_codex()])
        code, out, err = self.gate()
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(out, f"PR reviews of {self.head[:10]} are current (primary at {AFTER}, codex at {AFTER}): "
                              "pipe passed\n")
        with self.subTest("a required reviewer pending"):
            self.answer([PASSED], [by_codex()])
            code, _, err = self.gate()
            self.assertEqual(code, 2, err)
            self.assertEqual(err.strip(), f"run-delivery: no current review at {self.head[:10]} from primary: pipe passed")
        with self.subTest("an optional reviewer absent"):
            self.clock(T0_EPOCH + 301)
            self.answer([PASSED], [by_primary()])
            code, out, err = self.gate("--wait")
            self.assertEqual((code, err), (0, ""), out)
            self.assertIn("codex absent", out)
            self.assertEqual(self.sleeps(), [])


class RetryTests(PrReviewCase):
    """Decision 6: a transient failure (exit 75) is retried inside a wait's loop, from 5 seconds doubling to 60 and
    never past the deadline; everywhere else it refuses at once."""

    def setUp(self) -> None:
        super().setUp()
        self.configure(LISTED)
        self.answer([PASSED], [by_primary(), by_codex()])

    def transient(self, said: str = "forge: 503 Service Unavailable") -> dict:
        return {"exit": 75, "stderr": said}

    def test_without_a_wait_and_at_hand_off_a_transient_failure_refuses_at_once(self) -> None:
        self.scripted(reviews=[self.transient()])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertIn("forge reviews failed transiently (exit 75): forge: 503 Service Unavailable", out)
        self.assertEqual((self.sleeps(), self.forge_calls().count("reviews")), ([], 1))
        self.scripted(get=[self.transient()])
        code, out = self.handoff("primary=x", "codex=y")
        self.assertEqual(code, 2, out)
        self.assertIn("forge get failed transiently (exit 75)", out)
        self.assertEqual(self.sleeps(), [])

    def test_a_wait_succeeds_after_two_transient_failures(self) -> None:
        self.scripted(reviews=[self.transient(), self.transient(), {"pass": True}])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sleeps(), [5, 10])

    def test_the_review_gate_retries_get_and_reviews_but_not_find(self) -> None:
        self.scripted(get=[self.transient(), {"pass": True}], reviews=[self.transient(), {"pass": True}])
        code, out, err = self.gate("--wait")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(self.sleeps(), [5, 5])
        self.calls.unlink()
        self.clock(T0_EPOCH + 60)
        self.scripted(find=[self.transient()])
        code, _, err = self.gate("--wait")
        self.assertEqual(code, 2, err)
        self.assertIn("forge find failed transiently", err)
        self.assertEqual((self.sleeps(), self.forge_calls()), ([], ["find"]))

    def test_transient_until_the_deadline_refuses_with_the_last_message(self) -> None:
        self.configure({**LISTED, "waitSeconds": 20})
        self.scripted(reviews=[self.transient(f"attempt {n}") for n in (1, 2, 3, 4)])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 2, out)
        self.assertIn("attempt 4", out)
        self.assertIn("retried until the wait's deadline", out)
        self.assertEqual(self.sleeps(), [5, 10, 5])

    def test_the_backoff_starts_at_5_doubles_to_60_and_never_passes_the_deadline(self) -> None:
        self.configure({**LISTED, "waitSeconds": 200})
        self.scripted(reviews=[self.transient()])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 2, out)
        self.assertEqual(self.sleeps(), [5, 10, 20, 40, 60, 60, 5])
        self.assertEqual(self.now(), T0_EPOCH + 60 + 200)

    def test_the_legacy_wait_retries_too(self) -> None:
        self.configure(LEGACY | {"waitSeconds": 100})
        self.answer([PASSED], [comment("Pipeline Bot", AFTER, "fine")])
        self.scripted(reviews=[self.transient(), {"pass": True}])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sleeps(), [5])



def stamp(epoch: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# The silent-reviewer rows' pipeline: done at T, created earlier.
T = T0_EPOCH + 3600
SETTLE = 300
SILENT = "run-delivery: silent reviewer: the pipeline is green at "


def check(created: float, done: float | None, name: str = "pipe", state: str = "passed") -> dict:
    found = {"name": name, "state": state, "at": stamp(created)}
    if done is not None:
        found["doneAt"] = stamp(done)
    return found


class SilentReviewerTests(PrReviewCase):
    """V26-3347, on the injected clock: once every check passed, a required reviewer with no current review after the
    settle stops the wait, exit 3, with a line of its own; the legacy shape never does."""

    def setUp(self) -> None:
        super().setUp()
        self.configure(LISTED)

    def read(self, now: float, *argv: str) -> tuple[int, str]:
        self.clock(now)
        return self.flow("pr-review", "FX-2", *argv)

    def silent(self, code: int, out: str) -> None:
        self.assertEqual(code, 3, out)
        self.assertIn(f"{SILENT}{self.head[:10]} but primary posted nothing since; re-run its step or ask the lead", out)

    def test_the_legacy_shape_never_stops_this_way(self) -> None:
        self.configure(LEGACY | {"waitSeconds": 120})
        self.answer([check(T - 600, T)], [])
        for now, argv in ((T + 301, ()), (T + 301, ("--wait",)), (T + 5000, ())):
            with self.subTest(now=now - T, argv=argv):
                code, out = self.read(now, *argv)
                self.assertEqual(code, 2, out)
                self.assertIn(f"no Pipeline Bot review after the pipeline for {self.head[:10]} yet", out)
                self.assertNotIn("silent reviewer", out)
                if argv:  # with --wait, the whole wait, to its deadline
                    self.assertEqual(self.sleeps(), [30, 30, 30, 30])
        with self.subTest("the same reviewer in a list"):
            self.configure({"reviewers": [{"id": "review", "author": "Pipeline Bot"}]})
            code, out = self.read(T + 301)
            self.assertEqual(code, 3, out)
            self.assertIn(f"{SILENT}{self.head[:10]} but review posted nothing since", out)

    def test_the_settle_starts_when_the_checks_finished_and_falls_back_to_their_creation(self) -> None:
        self.answer([check(T - 600, T)], [])
        code, out = self.read(T + SETTLE + 1)
        self.silent(code, out)
        self.assertIn("codex absent", out)
        self.assertEqual(self.sleeps(), [])
        with self.subTest("without doneAt, the check's at"):
            self.answer([check(T - 600, None)], [])
            self.silent(*self.read(T - 600 + SETTLE + 1))
        with self.subTest("with doneAt, the same read is still inside the settle"):
            self.answer([check(T - 600, T)], [])
            code, out = self.read(T - 600 + SETTLE + 1)
            self.assertEqual(code, 2, out)
            self.assertNotIn("silent reviewer", out)
        with self.subTest("two checks, one without doneAt: only it falls back"):
            self.answer([check(T - 900, T), check(T - 600, None, "lint")], [])
            code, out = self.read(T + SETTLE - 1)
            self.assertEqual(code, 2, out)
            self.silent(*self.read(T + SETTLE + 1))

    def test_with_several_checks_the_latest_done_starts_the_settle(self) -> None:
        self.answer([check(T - 900, T), check(T - 600, T - 400, "lint")], [])
        code, out = self.read(T + SETTLE - 1)
        self.assertEqual(code, 2, out)
        self.assertIn("no current review from primary, codex", out)
        self.silent(*self.read(T + SETTLE + 1))

    def test_the_boundary_belongs_to_the_settle(self) -> None:
        self.answer([check(T - 600, T)], [])
        for now in (T + SETTLE - 1, T + SETTLE):
            with self.subTest(now=now - T):
                code, out = self.read(now)
                self.assertEqual(code, 2, out)
                self.assertIn("no current review from primary, codex", out)
        self.silent(*self.read(T + SETTLE + 1))

    def test_a_pending_check_keeps_every_reviewer_pending_past_the_settle(self) -> None:
        self.answer([check(T - 600, T), check(T - 600, None, "lint", "pending")], [])
        code, out = self.read(T + SETTLE + 1000)
        self.assertEqual(code, 2, out)
        self.assertNotIn("silent reviewer", out)

    def test_a_wait_stops_when_the_settle_passes(self) -> None:
        self.configure({**LISTED, "waitSeconds": 1800})
        self.answer([check(T - 600, T)], [])
        code, out = self.read(T + 200, "--wait")
        self.silent(code, out)
        self.assertEqual(self.sleeps(), [30, 30, 30, 30])  # T+200 -> T+320, the first read past the settle
        self.assertLess(self.now(), T + 1800)

    def test_an_optional_reviewer_is_not_absent_as_soon_as_the_pipeline_goes_green(self) -> None:
        """A Bitbucket status created long before it finished: the settle runs from its doneAt, not its creation."""
        self.answer([check(T - 3600, T)], [by_primary(at=stamp(T + 10))])
        code, out = self.read(T + 60)
        self.assertEqual(code, 2, out)
        self.assertIn("no current review from codex", out)

    def test_the_review_gate_exits_3_on_a_silent_required_reviewer(self) -> None:
        self.answer([check(T - 600, T)], [by_codex(at=stamp(T + 10))])
        self.clock(T + SETTLE + 1)
        code, _, err = self.gate()
        self.assertEqual(code, 3, err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertTrue(err.startswith(f"{SILENT}{self.head[:10]} but primary posted nothing since"), err)
        with self.subTest("an optional one silent never blocks"):
            self.answer([check(T - 600, T)], [by_primary(at=stamp(T + 10))])
            code, out, err = self.gate()
            self.assertEqual((code, err), (0, ""), out)

    def test_the_lane_hands_off_blocked_on_it_and_the_lead_decides(self) -> None:
        self.answer([check(T - 600, T)], [])
        code, out = self.read(T + SETTLE + 1)
        self.silent(code, out)
        line = next(l for l in out.splitlines() if l.startswith(SILENT))[len("run-delivery: "):]
        code, out = self.flow("handoff", "FX-2", "--pr", PR, "--blocked", f"pr-review={line}")
        self.assertEqual(code, 0, out)
        self.assertIn(f"BLOCKED pr-review: {line}", out)
        code, out = self.accept()
        self.assertEqual(code, 1, out)
        self.assertIn("the lane blocked 'pr-review'", out)
        self.assertNotIn("recorded no PR review", out)
        code, out = self.flow("lead-accept", "FX-2", "--accept-blocked", "pr-review", cwd=self.fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("accepted blocked 'pr-review'", self.decisions())

    def test_a_block_on_another_label_still_needs_the_pr_review(self) -> None:
        """Only the `pr-review` label stands in for the PR review, at hand-off and at acceptance."""
        self.answer([check(T - 600, T)], [])
        self.clock(T + SETTLE + 1)  # a silent reviewer, so only the label decides
        code, out = self.flow("handoff", "FX-2", "--pr", PR, "--blocked", "other=reason")
        self.assertEqual(code, 2, out)
        self.assertIn(f"no PR review recorded at {self.head[:10]}", out)
        self.clock(T + 60)
        self.answer([check(T - 600, T)], [by_primary(at=stamp(T + 10)), by_codex(at=stamp(T + 10))])
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)
        code, out = self.flow("handoff", "FX-2", "--pr", PR, "--blocked", "other=reason",
                              "--pr-review-triage", "primary=fixed", "--pr-review-triage", "codex=fine")
        self.assertEqual(code, 0, out)
        self.answer([check(T - 600, T)], [by_primary(at=stamp(T + 20)), by_codex(at=stamp(T + 10))])
        code, out = self.flow("lead-accept", "FX-2", "--accept-blocked", "other", cwd=self.fx.main)
        self.assertEqual(code, 1, out)
        self.assertIn("primary's PR review changed after the lane read it", out)

    def test_several_silent_reviewers_are_each_named(self) -> None:
        second = {"id": "second", "author": "Second Bot", "signature": "<!-- second -->"}
        self.configure({**LISTED, "reviewers": [PRIMARY, CODEX, second]})
        self.answer([check(T - 600, T)], [])
        code, out = self.read(T + SETTLE + 1)
        self.assertEqual(code, 3, out)
        self.assertIn(f"{SILENT}{self.head[:10]} but primary, second posted nothing since", out)

    def test_a_block_on_the_pr_review_needs_a_silent_reviewer(self) -> None:
        self.answer([check(T - 600, T)], [])
        self.clock(T + 60)  # inside the settle: nobody is silent
        code, out = self.flow("handoff", "FX-2", "--pr", PR, "--blocked", "pr-review=impatient")
        self.assertEqual(code, 2, out)
        self.assertIn(f"--blocked pr-review is for a required reviewer silent after the settle, and none is silent at "
                      f"{self.head[:10]}", out)
        with self.subTest("the legacy shape has no such block: its review is required as ever"):
            self.configure(LEGACY)
            code, out = self.flow("handoff", "FX-2", "--pr", PR, "--blocked", "pr-review=impatient")
            self.assertEqual(code, 2, out)
            self.assertIn(f"no PR review recorded at {self.head[:10]}", out)

    def test_a_silent_reviewer_that_posts_before_acceptance_sends_the_lane_back(self) -> None:
        self.answer([check(T - 600, T)], [])
        code, out = self.read(T + SETTLE + 1)
        line = next(l for l in out.splitlines() if l.startswith(SILENT))[len("run-delivery: "):]
        self.assertEqual(self.flow("handoff", "FX-2", "--pr", PR, "--blocked", f"pr-review={line}")[0], 0)
        self.answer([check(T - 600, T)], [by_primary(at=stamp(T + 400))])  # its step re-run: it has posted now
        code, out = self.flow("lead-accept", "FX-2", "--accept-blocked", "pr-review", cwd=self.fx.main)
        self.assertEqual(code, 1, out)
        self.assertIn(f"the lane blocked on a silent PR reviewer, but none is silent at {self.head[:10]} now "
                      "(primary current, codex absent): send the lane back to read and triage its review", out)

    def test_the_brief_tells_the_lane_to_escalate_and_not_wait_again(self) -> None:
        self.assertEqual(self.fx.flow("lead-brief", cwd=self.fx.main)[0], 0)
        brief = " ".join((self.fx.main / "docs/lane-brief.md").read_text().split())
        self.assertIn("exits 3 with a `silent reviewer` line", brief)
        self.assertIn("--blocked pr-review=", brief)
        self.assertIn("don't run the wait again", brief)



class SilentAndGateTests(PrReviewCase):
    """A silent required reviewer and another reviewer's unmet gate are separate refusals: neither hides the other."""

    GATED = {**CODEX, "gate": "verdict"}

    def setUp(self) -> None:
        super().setUp()
        self.configure({**LISTED, "reviewers": [PRIMARY, self.GATED]})
        self.answer([check(T - 600, T)], [by_codex(body="<!-- codex -->\n**Verdict:** Request changes", at=stamp(T + 10))])
        self.unmet = f"PR reviewer codex's verdict gate is unmet at {self.head[:10]}"

    def test_pr_review_and_the_gate_name_both(self) -> None:
        self.clock(T + SETTLE + 1)
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 3, out)
        self.assertIn(f"{SILENT}{self.head[:10]} but primary posted nothing since", out)
        self.assertIn(self.unmet, out)
        code, _, err = self.gate()
        self.assertEqual(code, 3, err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn(self.unmet, err)
        with self.subTest("with the gate met, the silent line alone"):
            self.answer([check(T - 600, T)], [by_codex(body="<!-- codex -->\n**Verdict:** Approve", at=stamp(T + 10))])
            code, out = self.flow("pr-review", "FX-2")
            self.assertEqual(code, 3, out)
            self.assertNotIn("gate is unmet", out)

    def test_a_block_on_the_silent_reviewer_still_refuses_the_unmet_gate(self) -> None:
        self.clock(T + SETTLE + 1)
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 3)  # records primary silent, codex current and unmet
        code, out = self.flow("handoff", "FX-2", "--pr", PR, "--blocked", "pr-review=silent",
                              "--pr-review-triage", "codex=read it")
        self.assertEqual(code, 2, out)
        self.assertIn(self.unmet, out)
        self.answer([check(T - 600, T)], [by_codex(body="<!-- codex -->\n**Verdict:** Approve", at=stamp(T + 10))])
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 3)
        code, out = self.flow("handoff", "FX-2", "--pr", PR, "--blocked", "pr-review=silent",
                              "--pr-review-triage", "codex=read it")
        self.assertEqual(code, 0, out)
        self.answer([check(T - 600, T)], [by_codex(body="<!-- codex -->\n**Verdict:** Request changes",
                                                   at=stamp(T + 20))])
        code, out = self.flow("lead-accept", "FX-2", "--accept-blocked", "pr-review", cwd=self.fx.main)
        self.assertEqual(code, 1, out)
        self.assertIn(self.unmet, out)



class SilentRecordTests(PrReviewCase):
    """Plan change c1: pr-review records a silent required reviewer before it exits 3, a hand-off blocked on it still
    triages every current review, and lead-accept re-checks those as for any hand-off."""

    def setUp(self) -> None:
        super().setUp()
        self.configure(LISTED)
        self.answer([check(T - 600, T)], [by_codex(at=stamp(T + 10))])
        self.clock(T + SETTLE + 1)
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 3, out)
        self.line = next(l for l in out.splitlines() if l.startswith(SILENT))[len("run-delivery: "):]

    def blocked_handoff(self, *triage: str) -> tuple[int, str]:
        return self.flow("handoff", "FX-2", "--pr", PR, "--blocked", f"pr-review={self.line}",
                         *[x for t in triage for x in ("--pr-review-triage", t)])

    def test_the_record_holds_the_silent_reviewer_beside_the_current_one(self) -> None:
        self.assertEqual(self.record(), {
            "schema": "pr-review/2", "head": self.head, "url": PR, "reviewers": {
                "primary": {"state": "silent", "required": True},
                "codex": {"state": "current", "required": False, "author": "Review Bot", "account": "{u-review}",
                          "at": stamp(T + 10), "body": "<!-- codex -->\nCodex says: fine"}}})

    def test_a_blocked_hand_off_triages_every_current_review(self) -> None:
        code, out = self.blocked_handoff()
        self.assertEqual(code, 2, out)
        self.assertIn("say how codex's review was handled: --pr-review-triage codex=TEXT", out)
        code, out = self.blocked_handoff("codex=fine", "primary=nothing to read")
        self.assertEqual(code, 2, out)
        self.assertIn("--pr-review-triage names primary, which was silent: a silent reviewer takes no triage", out)
        code, out = self.blocked_handoff("codex=fine")
        self.assertEqual(code, 0, out)
        self.assertIn("- PR review: primary: silent; codex: Review Bot at", out)
        self.assertIn(f"BLOCKED pr-review: {self.line}", out)
        self.assertEqual(self.record()["reviewers"]["codex"]["triage"], "fine")

    def test_a_recorded_silent_reviewer_needs_the_block(self) -> None:
        code, out = self.flow("handoff", "FX-2", "--pr", PR, "--pr-review-triage", "codex=fine")
        self.assertEqual(code, 2, out)
        self.assertIn(f"primary was silent at {self.head[:10]}: hand off blocked on it, --blocked pr-review=", out)

    def test_lead_accept_rechecks_the_triaged_review_on_a_blocked_hand_off(self) -> None:
        self.assertEqual(self.blocked_handoff("codex=fine")[0], 0)
        self.answer([check(T - 600, T)], [by_codex(body="<!-- codex -->\nCodex has more", at=stamp(T + 20))])
        code, out = self.flow("lead-accept", "FX-2", "--accept-blocked", "pr-review", cwd=self.fx.main)
        self.assertEqual(code, 1, out)
        self.assertIn("codex's PR review changed after the lane read it", out)
        self.answer([check(T - 600, T)], [by_codex(at=stamp(T + 10))])
        code, out = self.flow("lead-accept", "FX-2", "--accept-blocked", "pr-review", cwd=self.fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("PR review codex by Review Bot", self.decisions())
        self.assertIn("accepted blocked 'pr-review'", self.decisions())

    def test_the_legacy_shape_writes_no_record_on_its_refusal_and_takes_no_block(self) -> None:
        (self.review / "pr-review.json").unlink()
        self.configure(LEGACY)
        self.answer([check(T - 600, T)], [])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertFalse((self.review / "pr-review.json").exists())
        code, out = self.flow("handoff", "FX-2", "--pr", PR, "--blocked", f"pr-review={self.line}")
        self.assertEqual(code, 2, out)
        self.assertIn(f"no PR review recorded at {self.head[:10]}", out)


if __name__ == "__main__":
    unittest.main()
