"""A fixture adopter runs the orchestration workflow end to end.

The adopter knows nothing of any real repository. Its remote is named
``upstream``, its base branch is ``trunk``, and every path it uses contains
spaces. Each tool is driven through its command line, the way a lead would:
the state tool, the merge-queue runner, and the renderer wrapper.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_skills.validate import LEAKAGE_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/orchestrate-work/scripts"
STATE_TOOL = SCRIPTS / "delivery_state.py"
RUNNER = SCRIPTS / "merge_queue.py"
WRAPPER = SCRIPTS / "render_state.py"
FIXTURE = ROOT / "tests/fixtures/orchestration-adopter"
REMOTE = "upstream"
BASE = "trunk"
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Fixture Lane", "GIT_AUTHOR_EMAIL": "lane@fixture.invalid",
    "GIT_COMMITTER_NAME": "Fixture Lane", "GIT_COMMITTER_EMAIL": "lane@fixture.invalid",
}
# Variables that would carry the machine's own git settings into the fixture.
INHERITED_GIT = ("GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_DIR", "GIT_WORK_TREE",
                 "GIT_INDEX_FILE")


def _load_wrapper():
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("render_state_for_adopter", WRAPPER)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))
        sys.dont_write_bytecode = previous


render_state = _load_wrapper()


def text(value: object) -> str:
    """How the markdown view writes a value."""
    return render_state._markdown_text(value)


class FixtureAdopter:
    """The adopter's remote, the operator's clone, one lane clone and a delivery state."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote = root / "upstream remote.git"
        self.operator = root / "operator checkout"
        self.lane = root / "lane checkout"
        self.state = root / "delivery state"
        self.work_root = root / "queue work"
        self.log = root / "fake log.txt"
        self.snapshots = root / "view snapshots"
        self.log.write_text("", encoding="utf-8")
        # The machine's global and system git settings (signing, ignore rules,
        # line-ending conversion) never reach the fixture or the tools it runs.
        # The runner itself does not yet pin its own commits against them; a
        # tracked follow-up does, and then this isolation can be dropped, or
        # turned into a hostile configuration, to show the runner is immune.
        self.gitconfig = root / "fixture gitconfig"
        self.gitconfig.write_text("", encoding="utf-8")
        # Git also reads a user's default ignore and attributes files from the
        # home directory, so the fixture gets a home of its own.
        home = root / "fixture home"
        home.mkdir()
        self.environment = {
            **{key: value for key, value in os.environ.items()
               if key not in INHERITED_GIT and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))},
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
        }
        self.git("init", "--quiet", "--bare", f"--initial-branch={BASE}", str(self.remote), cwd=root)
        seed = root / "seed checkout"
        self.git("clone", "--quiet", "--origin", REMOTE, str(self.remote), str(seed), cwd=root)
        self.git("checkout", "--quiet", "-b", BASE, cwd=seed)
        shutil.copytree(FIXTURE / "repository", seed, dirs_exist_ok=True)
        (seed / ".agents").mkdir()
        (seed / ".agents/orchestration.json").write_text(json.dumps(self.binding(), indent=2) + "\n", encoding="utf-8")
        self.commit(seed, "The adopter's first commit")
        self.git("push", "--quiet", REMOTE, BASE, cwd=seed)
        for clone in (self.operator, self.lane):
            self.git("clone", "--quiet", "--origin", REMOTE, "--branch", BASE, str(self.remote), str(clone), cwd=root)

    def binding(self) -> dict:
        tokens = {
            "@PYTHON@": sys.executable, "@WORK_ROOT@": str(self.work_root), "@STATE@": str(self.state),
            "@LOG@": str(self.log), "@SNAPSHOTS@": str(self.snapshots),
        }

        def fill(value: object) -> object:
            if isinstance(value, str):
                return tokens.get(value, value)
            if isinstance(value, list):
                return [fill(item) for item in value]
            if isinstance(value, dict):
                return {key: fill(item) for key, item in value.items()}
            return value

        return fill(json.loads((FIXTURE / "binding.json").read_text(encoding="utf-8")))

    # -- git ---------------------------------------------------------------

    def git(self, *args: str, cwd: Path) -> str:
        completed = subprocess.run(
            ["git", "-c", f"core.hooksPath={self.root / 'no hooks'}", *args], cwd=cwd, capture_output=True,
            text=True, encoding="utf-8", env={**self.environment, **GIT_IDENTITY}, check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {completed.stderr}")
        return completed.stdout.strip()

    def commit(self, checkout: Path, message: str) -> str:
        subprocess.run([sys.executable, "-B", "tools/regenerate.py"], cwd=checkout, check=True, env=self.environment)
        self.git("add", "--all", cwd=checkout)
        self.git("commit", "--quiet", "-m", message, cwd=checkout)
        return self.git("rev-parse", "HEAD", cwd=checkout)

    def read(self, revision: str, path: str) -> str:
        """A file as the remote has it at ``revision``."""
        self.git("fetch", "--quiet", REMOTE, cwd=self.lane)
        return self.git("show", f"{revision}:{path}", cwd=self.lane)

    def tip(self, branch: str) -> str:
        return self.git("ls-remote", "--heads", str(self.remote), f"refs/heads/{branch}", cwd=self.root).split()[0]

    def lane_change(self, subject: str, files: dict[str, str], *, fresh: bool = False) -> str:
        """The lane commits a candidate on lane/<subject>, regenerating as it goes."""
        branch = f"lane/{subject}"
        self.git("fetch", "--quiet", REMOTE, cwd=self.lane)
        self.git("checkout", "--quiet", "-B", branch, f"{REMOTE}/{BASE}", cwd=self.lane)
        for relative, contents in files.items():
            (self.lane / relative).write_text(contents, encoding="utf-8")
        head = self.commit(self.lane, f"{'Rework' if fresh else 'Change'} {subject}")
        self.git("push", "--quiet", "--force", REMOTE, f"{branch}:{branch}", cwd=self.lane)
        return head

    def push_to_base(self, files: dict[str, str], message: str, binding: dict | None = None) -> str:
        """A commit lands on the base directly, outside the queue."""
        self.git("fetch", "--quiet", REMOTE, cwd=self.lane)
        self.git("checkout", "--quiet", "-B", "base-work", f"{REMOTE}/{BASE}", cwd=self.lane)
        for relative, contents in files.items():
            (self.lane / relative).write_text(contents, encoding="utf-8")
        if binding is not None:
            (self.lane / ".agents/orchestration.json").write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
        head = self.commit(self.lane, message)
        self.git("push", "--quiet", REMOTE, f"base-work:{BASE}", cwd=self.lane)
        return head

    # -- the tools, through their command lines ------------------------------

    def tool(self, script: Path, *args: str, expect: int | None = 0) -> object:
        completed = subprocess.run(
            [sys.executable, "-B", str(script), *args], capture_output=True, cwd=self.root, check=False,
            env=self.environment,
        )
        if expect is not None and completed.returncode != expect:
            raise AssertionError(
                f"{script.name} {args[:4]} exited {completed.returncode}: "
                f"{completed.stdout.decode('utf-8', 'replace')[-2000:]} {completed.stderr.decode('utf-8', 'replace')[-2000:]}"
            )
        return json.loads(completed.stdout.decode("ascii"))

    def state_tool(self, *args: str) -> dict:
        return self.tool(STATE_TOOL, "--dir", str(self.state), *args)  # type: ignore[return-value]

    def lead_patch(self, changes: dict, element: str | None = None) -> dict:
        version = self.show()["state"]["version"]
        extra = ["--element", element] if element else []
        return self.state_tool("patch", "--writer", "lead", "--version", str(version), *extra, "--patch", json.dumps(changes))

    def journal(self, kind: str, summary: str, *, lane: str | None = None, subject: str | None = None) -> None:
        extra = (["--lane", lane] if lane else []) + (["--subject", subject] if subject else [])
        self.state_tool("journal", "--kind", kind, "--summary", summary, *extra)

    def show(self) -> dict:
        return self.state_tool("show")

    def document(self) -> dict:
        """What the renderers read: ``show`` without its publication status."""
        return {key: value for key, value in self.show().items() if key != "publication"}

    def queue(self, *args: str, expect: int | None = None) -> object:
        return self.tool(
            RUNNER, "--repo", str(self.operator), "--state", str(self.state), "--remote", REMOTE, "--base", BASE,
            *args, expect=expect,
        )

    def item(self, subject: str) -> dict:
        return next(item for item in self.show()["state"]["queue"] if item["subject"] == subject)

    def view(self) -> str:
        return (self.state / "delivery.md").read_text(encoding="utf-8")

    def log_lines(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()


SUBJECTS = ("alpha-copy", "beta-copy", "alpha-rewrite")


class FixtureAdopterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="orchestration adopter ")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.assertIn(" ", str(self.root))

    def assertQueueRow(self, view: str, subject: str, stage: str) -> None:
        # A bullet whose order keeps its escaped marker, so Markdown never
        # renumbers it.
        row = re.search(rf"^- \d+\\\. {re.escape(text(subject))}: .*$", view, re.M)
        self.assertIsNotNone(row, f"{subject} is not in the view")
        self.assertIn(f", {text(stage)} · lane", row.group(0))

    def test_the_whole_workflow_runs_end_to_end(self) -> None:
        adopter = FixtureAdopter(self.root)

        # 1. Init.
        created = adopter.state_tool("init", "--headline", "Fixture epic")
        self.assertEqual(created["state"]["version"], 1)

        # 2. Assign: one lane per candidate, each recorded in the journal.
        since = "2026-10-20T09:00:00Z"
        lanes = [{"id": f"lane-{n}", "title": subject, "focus": subject, "state": "working", "waitingOn": None,
                  "since": since} for n, subject in enumerate(SUBJECTS, start=1)]
        adopter.lead_patch({"lanes": lanes})
        for lane in lanes:
            adopter.journal("lane.assigned", f"Assigned {lane['title']}", lane=lane["id"])

        # Each lane hands its candidate over; the lead records the reviewed head.
        heads = {
            "alpha-copy": adopter.lane_change("alpha-copy", {"src/alpha.txt": "alpha two\n"}),
            # Changes the catalog line next to the clean candidate's: a generated-only conflict once it merges.
            "beta-copy": adopter.lane_change("beta-copy", {"src/beta.txt": "beta two\n"}),
            # Changes the same source line as the clean candidate: a source conflict once it merges.
            "alpha-rewrite": adopter.lane_change("alpha-rewrite", {"src/alpha.txt": "alpha rewritten\n"}),
        }
        # The first lane records its two review rounds as it goes.
        for number, verdict, findings in ((1, "changes", {"P0": 0, "P1": 1, "P2": 0, "P3": 1}),
                                          (2, "approve", {"P0": 0, "P1": 0, "P2": 0, "P3": 1})):
            moment = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            adopter.state_tool("journal", "--kind", "review.round", "--subject", "alpha-copy", "--lane", "lane-1",
                               "--review", json.dumps({"round": number, "startedAt": moment, "finishedAt": moment,
                                                       "verdicts": {"outcome": verdict, "adversarial": verdict},
                                                       "findings": findings}))
        queue = [{
            "order": n, "subject": subject, "title": f"Candidate {subject}", "lane": f"lane-{n}", "stage": "queued",
            "head": heads[subject], "syncedHead": None, "leadInputs": [], "reportedRounds": 1, "runId": None,
            "checks": [],
        } for n, subject in enumerate(SUBJECTS, start=1)]
        adopter.lead_patch({"queue": queue})
        for item in queue:
            adopter.journal("candidate.handed", f"Handed {item['subject']}", lane=item["lane"], subject=item["subject"])
        self.assertFalse((adopter.state / "delivery.md").exists(), "nothing renders until the runner first writes")

        # S5: the second lane pushes again after its head was reviewed and recorded.
        unreviewed = adopter.lane_change("beta-copy", {"src/beta.txt": "beta three\n"}, fresh=True)
        # S3: while the first candidate is checked, another merge lands on the base.
        adopter.work_root.mkdir(exist_ok=True)
        (adopter.work_root / "move-base").write_text("once\n", encoding="utf-8")

        # 3. Run all three.
        results = adopter.queue("run", *SUBJECTS, expect=1)
        self.assertFalse((adopter.work_root / "move-base").exists(), "the base moved during the first check")
        outcomes = [(result["subcommand"], result["candidate"], result["outcome"]) for result in results]
        self.assertEqual(outcomes, [
            ("sync", "alpha-copy", "synced"), ("check", "alpha-copy", "checked"), ("merge", "alpha-copy", "stopped"),
            ("sync", "alpha-copy", "synced"), ("check", "alpha-copy", "checked"), ("merge", "alpha-copy", "merged"),
            ("sync", "beta-copy", "stopped"),
        ])
        self.assertTrue(all(result["hooksBypassed"] for result in results))
        # S3: the merge refused a base its checked head does not contain; the runner re-synced,
        # re-checked and merged at the new base, onto exactly the base it was checked against.
        moved = results[2]
        self.assertEqual(moved["stop"]["reason"], "base_moved")
        self.assertFalse(moved["stop"]["queueStopped"])
        self.assertNotEqual(results[3]["baseHead"], results[0]["baseHead"])
        self.assertEqual(results[3]["baseHead"], moved["baseHead"])
        self.assertEqual(results[5]["mergedHead"], adopter.tip(BASE))
        self.assertEqual(adopter.read(adopter.tip(BASE), "src/gamma.txt"), "gamma two")
        # The provider selected the checks for the first candidate's source change.
        self.assertEqual(results[4]["selection"]["checks"], ["format", "lint"])
        self.assertEqual(results[4]["selection"]["fallbackReasons"], [])
        self.assertFalse(results[4]["selection"]["widened"])
        merged_runs = [(results[5], results[4])]
        # S5: the second candidate moved after review, so nothing of it was synced or merged.
        stopped = results[6]
        self.assertEqual(stopped["stop"]["reason"], "candidate_moved")
        self.assertEqual(stopped["head"], unreviewed)
        self.assertIsNone(stopped["syncedHead"])
        self.assertEqual(adopter.tip("lane/beta-copy"), unreviewed, "the runner pushed nothing to the moved branch")
        view = adopter.view()
        self.assertQueueRow(view, "alpha-copy", "merged")
        self.assertQueueRow(view, "beta-copy", "stopped")
        self.assertQueueRow(view, "alpha-rewrite", "queued")
        self.assertIn(f"candidate{text('_')}moved", view)
        self.assertIn(f"base{text('_')}moved", view)

        # The lead reviews the new head and re-queues the second candidate on it.
        adopter.lead_patch({"head": unreviewed}, "queue[subject=beta-copy]")
        adopter.journal("candidate.handed", "Handed beta-copy at its reviewed new head", lane="lane-2",
                        subject="beta-copy")

        # S2: while this run goes, the provider is down for any change to src/beta.txt,
        # so the second candidate is checked through the fallback.
        (adopter.work_root / "provider-down").write_text("src/beta.txt\n", encoding="utf-8")
        results = adopter.queue("run", "beta-copy", "alpha-rewrite", expect=1)
        (adopter.work_root / "provider-down").unlink()
        outcomes = [(result["subcommand"], result["candidate"], result["outcome"]) for result in results]
        self.assertEqual(outcomes, [
            ("sync", "beta-copy", "synced"), ("check", "beta-copy", "checked"), ("merge", "beta-copy", "merged"),
            ("sync", "alpha-rewrite", "stopped"),
        ])
        merged_runs.append((results[2], results[1]))
        self.assertEqual(results[-1]["stop"]["reason"], "source_conflict")
        self.assertEqual(results[-1]["stop"]["paths"], ["src/alpha.txt"])
        # The second candidate really conflicted with the base, on the generated file only.
        beta_sync = results[0]
        adopter.git("fetch", "--quiet", REMOTE, cwd=adopter.lane)
        conflict = subprocess.run(
            ["git", "merge-tree", "--write-tree", "--name-only", unreviewed, beta_sync["baseHead"]],
            cwd=adopter.lane, capture_output=True, text=True, env=adopter.environment, check=False,
        )
        self.assertEqual((conflict.returncode, conflict.stderr), (1, ""), conflict.stdout)
        self.assertEqual(conflict.stdout.split("\n\n")[0].split("\n")[1:], ["generated/catalog.txt"])
        # The runner took the base side in its merge commit, then regenerated in a commit of its own.
        merge_commit = f"{beta_sync['syncedHead']}^"
        self.assertEqual(adopter.git("log", "-1", "--format=%s", merge_commit, cwd=adopter.lane),
                         f"Merge {BASE} into lane/beta-copy")
        self.assertEqual(adopter.read(merge_commit, "generated/catalog.txt"),
                         adopter.read(beta_sync["baseHead"], "generated/catalog.txt"))
        catalog = adopter.read(adopter.tip(BASE), "generated/catalog.txt")
        self.assertEqual(catalog, "alpha.txt: alpha two\nbeta.txt: beta three\ngamma.txt: gamma two")
        # S2: the provider was down for the second candidate; the runner fell back and says why.
        selection = results[1]["selection"]
        self.assertEqual(selection["providers"][0]["outcome"], "failed")
        self.assertEqual(selection["fallbackReasons"], ["provider_failed"])
        self.assertTrue(selection["widened"])
        self.assertEqual(selection["checks"], ["full"])
        self.assertEqual([(check["id"], check["state"]) for check in results[1]["checks"]], [("full", "passed")])
        # While the fallback ran, the view named the candidate being checked and the fallback check.
        snapshots = sorted(adopter.snapshots.iterdir())
        self.assertEqual(len(snapshots), 1)
        during = snapshots[0].read_text(encoding="utf-8")
        self.assertIn(f"- Now: {text('beta-copy')}", during)
        rows = during.split(f"- 2\\. {text('beta-copy')}: ", 1)[1].split("\n", 3)
        self.assertIn(", checking · lane", rows[0])
        # Its stats, while it is still being checked: the wait is known, the merge not yet.
        self.assertRegex(rows[1], r"^  - stats: .* · wait \d+[smhd](?: \d+[smhd])? · check to merge unknown · total unknown$")
        del rows[1]
        # The runner marks a check running as it starts, so the fallback reads as running.
        self.assertEqual(rows[1], f"  - {text('full')}: running")
        view = adopter.view()
        self.assertQueueRow(view, "alpha-copy", "merged")
        self.assertQueueRow(view, "beta-copy", "merged")
        self.assertQueueRow(view, "alpha-rewrite", "stopped")
        self.assertIn(f"Base head {text(adopter.tip(BASE))}", view)
        self.assertIn("Nothing is being checked.", view)
        self.assertIn(f"source{text('_')}conflict", view)

        # 4. The lane fixes the third candidate on top of the new base, and the lead re-queues it.
        fixed = adopter.lane_change("alpha-rewrite", {"src/alpha.txt": "alpha two, rewritten\n"}, fresh=True)
        adopter.lead_patch({"head": fixed}, "queue[subject=alpha-rewrite]")
        adopter.journal("candidate.handed", "Handed alpha-rewrite again", lane="lane-3", subject="alpha-rewrite")
        # The view is redrawn only when the runner next writes; the state already holds the new head.
        self.assertIn(text(fixed), render_state.render_markdown(adopter.show()))

        # 5. A broken-base episode: a commit outside the queue breaks lint on the base.
        broken_base = adopter.push_to_base({"src/beta.txt": "beta three BROKEN\n"}, "A change that breaks lint")
        results = adopter.queue("run", "alpha-rewrite", expect=1)
        last = results[-1]
        self.assertEqual((last["subcommand"], last["outcome"]), ("check", "stopped"))
        self.assertEqual(last["stop"]["reason"], "base_broken")
        self.assertTrue(last["stop"]["queueStopped"])
        self.assertEqual(last["baseHead"], broken_base)
        self.assertEqual(adopter.item("alpha-rewrite")["stage"], "stopped")
        self.assertQueueRow(adopter.view(), "alpha-rewrite", "stopped")
        self.assertIn(f"base{text('.')}broken", adopter.view())

        # The lead fixes forward and makes lint run on every candidate from now on.
        binding = adopter.binding()
        lint = {"id": "lint", "argv": [sys.executable, "-B", "tools/lint.py", "src"], "cwd": ".",
                "reasons": ["the base broke without it"], "prerequisites": []}
        binding["alwaysRun"] = [lint]
        adopter.push_to_base({"src/beta.txt": "beta three\n"}, "Fix the base forward", binding)
        adopter.journal("base.repaired", "Fixed the base forward")
        adopter.journal("decision.recorded", "lint runs on every candidate")

        # 6. The re-run, with the provider up: it defers lint to alwaysRun, so the
        # check the base broke without runs from the binding, with no fallback.
        results = adopter.queue("run", "alpha-rewrite", expect=0)
        self.assertEqual([result["outcome"] for result in results], ["synced", "checked", "merged"])
        merged_runs.append((results[2], results[1]))
        selection = results[1]["selection"]
        self.assertEqual(selection["providers"], [{"source": "base", "outcome": "selected", "detail": "1 checks"}])
        self.assertEqual(selection["fallbackReasons"], [])
        self.assertFalse(selection["widened"])
        self.assertEqual(selection["checks"], ["format", "lint"])
        self.assertEqual([(check["id"], check["state"]) for check in results[1]["checks"]],
                         [("format", "passed"), ("lint", "passed")])
        self.assertEqual(len(list(adopter.snapshots.iterdir())), 1, "the fallback ran only during the outage")
        after = adopter.view()
        for subject in SUBJECTS:
            self.assertQueueRow(after, subject, "merged")

        # 7. A release: refused without the operator's approval, then run with it.
        released_head = adopter.tip(BASE)
        refused = adopter.queue("release", released_head, expect=1)
        self.assertEqual((refused["outcome"], refused["stop"]["reason"]), ("refused", "approval_required"))
        released = adopter.queue("release", released_head, "--approved-by", "the operator, in chat", expect=0)
        self.assertEqual(released["outcome"], "released")
        self.assertEqual([(target["id"], target["state"], target["approvedBy"]) for target in released["release"]["targets"]],
                         [("package", "passed", None), ("publish", "passed", "the operator, in chat")])
        self.assertIn(f"released head {text(released_head)}", adopter.view())
        adopter.journal("delivery.closed", "Three candidates merged and released")

        # 8. Both built-in views render from the final state.
        for output in ("delivery.md", "delivery.html"):
            (adopter.state / output).unlink()
        rendered = adopter.tool(WRAPPER, "--dir", str(adopter.state), "--binding",
                                str(adopter.operator / ".agents/orchestration.json"))
        self.assertEqual([(entry["id"], entry["ok"]) for entry in rendered["renderers"]],
                         [("markdown", True), ("page", True)])
        self.assertEqual([(entry["id"], entry["kind"], entry["ok"]) for entry in rendered["publishers"]],
                         [("live", "host", True)])
        final = adopter.document()
        page = (adopter.state / "delivery.html").read_text(encoding="utf-8")
        embedded = re.search(r'<script type="application/json" id="delivery-state">(.*?)</script>', page, re.S)
        self.assertEqual(json.loads(embedded.group(1)), final)
        self.assertEqual(adopter.view(), render_state.render_markdown(final))

        # Each stop's stats: the recorded rounds for the first, the lanes' reports for the others.
        stats = final["stats"]
        self.assertEqual((stats["alpha-copy"]["rounds"], stats["alpha-copy"]["roundsSource"],
                          stats["alpha-copy"]["findings"]), (2, "journal", {"P0": 0, "P1": 1, "P2": 0, "P3": 2}))
        self.assertEqual((stats["beta-copy"]["rounds"], stats["beta-copy"]["roundsSource"]), (1, "reported"))
        for subject in SUBJECTS:
            with self.subTest(subject=subject):
                for key in ("buildSeconds", "waitSeconds", "checkToMergeSeconds", "totalSeconds"):
                    if subject != "alpha-copy" and key == "buildSeconds":
                        self.assertIsNone(stats[subject][key], "no recorded round, so no build time")
                    else:
                        self.assertIsInstance(stats[subject][key], int, key)
                self.assertEqual(stats[subject]["outcome"], "merged")
        self.assertIn("2 rounds, findings P0/P1/P2/P3 0/1/0/2", adopter.view())

        # The final state: three merged candidates, the released head, and the journal's story.
        state = final["state"]
        self.assertEqual([(item["subject"], item["stage"]) for item in state["queue"]],
                         [(subject, "merged") for subject in SUBJECTS])
        self.assertEqual(state["meta"]["releasedHead"], released_head)
        self.assertEqual(state["meta"]["baseHead"], released_head)
        self.assertIsNone(state["meta"]["checking"]["now"])
        self.assertEqual([entry["kind"] for entry in final["journal"]], [
            "lane.assigned", "lane.assigned", "lane.assigned",
            "review.round", "review.round",
            "candidate.handed", "candidate.handed", "candidate.handed",
            # S3: checked, stopped at merge by the moved base, then re-checked and merged.
            "check.started", "check.finished", "candidate.stopped",
            "check.started", "check.finished", "candidate.merged",
            # S5: moved after review, stopped, and re-queued on its reviewed new head.
            "candidate.stopped", "candidate.handed",
            "check.started", "check.finished", "candidate.merged",
            "candidate.stopped",
            "candidate.handed",
            "check.started", "base.broken", "candidate.stopped",
            "base.repaired", "decision.recorded",
            "check.started", "check.finished", "candidate.merged",
            "release.started", "release.finished",
            "delivery.closed",
        ])
        # The forge fakes saw exactly the checked heads, each merged onto the base it was checked against.
        merges = [line.split()[1:] for line in adopter.log_lines() if line.startswith("merge ")]
        self.assertEqual(merges, [[f"lane/{result['candidate']}", result["syncedHead"], checked["baseHead"]]
                                  for result, checked in merged_runs])
        self.assertEqual(sum(line.startswith("ready ") for line in adopter.log_lines()), 3)
        self.assertEqual([line for line in adopter.log_lines() if line.startswith("release ")],
                         [f"release package {released_head}", f"release publish {released_head}"])

        # 9. The lead keeps the live view current: snapshots, reconcile, render, publish, acknowledge.
        self.keep_the_live_view_current(adopter)

        # 10. With the repository's tracker command bound, the sync loop takes the snapshot itself.
        self.take_the_tracker_snapshot_with_the_binding(adopter)
        self.assertEqual(adopter.show()["state"]["meta"].get("trackerParent"), "epic-1", "the snapshot step ran")

    def take_the_tracker_snapshot_with_the_binding(self, adopter: FixtureAdopter) -> None:
        data = adopter.root / "tracker data.json"
        data.write_text(json.dumps({"epic-1": [
            {"reference": "theta-docs", "title": "Docs for theta", "stateType": "backlog", "blockedBy": []},
            {"reference": "iota-copy", "title": "Copy for iota", "stateType": "started", "blockedBy": ["theta-docs"]},
        ]}), encoding="utf-8")
        binding = adopter.binding()
        binding["state"]["trackerSnapshot"] = {
            "argv": [sys.executable, "-B", str(adopter.operator / "tools/tracker.py"), "{parent}", str(data)],
            "timeoutSeconds": 30}
        path = adopter.root / "binding with a tracker command.json"
        path.write_text(json.dumps(binding, indent=2), encoding="utf-8")
        adopter.lead_patch({"meta": {"trackerParent": "epic-1"}})
        output = adopter.root / "tracker snapshot.json"

        taken = adopter.state_tool("tracker-snapshot", "--binding", str(path), "--output", str(output))
        self.assertEqual((taken["taken"], taken["items"]), (True, 2))
        reconciled = adopter.state_tool("reconcile", "--snapshot", str(output), "--binding", str(path))
        self.assertEqual(sorted(change["subject"] for change in reconciled["changes"]), ["iota-copy", "theta-docs"])
        state = adopter.show()["state"]
        self.assertIn("theta-docs", [followup["id"] for followup in state["followups"]])
        self.assertIn("iota-copy", [item["subject"] for item in state["planned"]])

        # The tracker fails: the last snapshot stands, one note says why, and reconcile finds no drift.
        data.write_text(json.dumps({}), encoding="utf-8")
        failed = adopter.state_tool("tracker-snapshot", "--binding", str(path), "--output", str(output))
        self.assertEqual((failed["taken"], failed["fallback"]), (False, True))
        self.assertIn("no item epic-1", failed["detail"])
        notes = [entry for entry in adopter.show()["journal"] if entry["summary"].startswith("tracker-snapshot:")]
        self.assertEqual(len(notes), 1)
        self.assertFalse(adopter.state_tool("reconcile", "--snapshot", str(output))["drift"])

    def test_a_batch_of_two_is_checked_once_and_lands_tree_verified(self) -> None:
        adopter = FixtureAdopter(self.root)
        adopter.state_tool("init", "--headline", "Fixture batch")
        subjects = ("alpha-copy", "beta-copy")
        heads = {
            "alpha-copy": adopter.lane_change("alpha-copy", {"src/alpha.txt": "alpha two\n"}),
            # Its catalog line sits next to the first's: a generated-only conflict in the combined tree.
            "beta-copy": adopter.lane_change("beta-copy", {"src/beta.txt": "beta two\n"}),
        }
        queue = [{
            "order": n, "subject": subject, "title": f"Candidate {subject}", "lane": f"lane-{n}", "stage": "queued",
            "head": heads[subject], "syncedHead": None, "leadInputs": [], "reportedRounds": 1, "runId": None,
            "checks": [],
        } for n, subject in enumerate(subjects, start=1)]
        adopter.lead_patch({"queue": queue})
        base_before = adopter.tip(BASE)
        results = adopter.queue("batch", *subjects, expect=0)
        self.assertEqual([(result["subcommand"], result["candidate"], result["outcome"]) for result in results],
                         [("batch", "alpha-copy", "merged"), ("batch", "beta-copy", "merged")])
        # One check run covered both: every member reports the same selection and the same checks.
        self.assertEqual(results[0]["selection"], results[1]["selection"])
        self.assertEqual(results[0]["checks"], results[1]["checks"])
        self.assertEqual([check["state"] for check in results[0]["checks"]], ["passed"] * len(results[0]["checks"]))
        # Each landing is a merge onto the previous one, with exactly the tree the batch recorded.
        first, second = results
        adopter.git("fetch", "--quiet", REMOTE, cwd=adopter.lane)
        for result in results:
            adopter.git("fetch", "--quiet", REMOTE, result["syncedHead"], cwd=adopter.lane)
        self.assertEqual(adopter.git("rev-parse", f"{first['mergedHead']}^1", cwd=adopter.lane), base_before)
        self.assertEqual(adopter.git("rev-parse", f"{second['mergedHead']}^1", cwd=adopter.lane), first["mergedHead"])
        for result in results:
            self.assertEqual(adopter.git("rev-parse", f"{result['mergedHead']}^{{tree}}", cwd=adopter.lane),
                             adopter.git("rev-parse", f"{result['syncedHead']}^{{tree}}", cwd=adopter.lane))
        self.assertEqual(second["mergedHead"], adopter.tip(BASE))
        catalog = adopter.read(adopter.tip(BASE), "generated/catalog.txt")
        self.assertIn("alpha.txt: alpha two", catalog)
        self.assertIn("beta.txt: beta two", catalog)
        view = adopter.view()
        for subject in subjects:
            self.assertQueueRow(view, subject, "merged")
        self.assertIn("Nothing is being checked.", view)
        journal = [(entry["kind"], entry.get("subject")) for entry in adopter.show()["journal"]]
        for subject in subjects:
            self.assertIn(("check.started", subject), journal)
            self.assertIn(("candidate.merged", subject), journal)

    def keep_the_live_view_current(self, adopter: FixtureAdopter) -> None:
        # A fourth candidate is handed over, then merged on the forge by hand: the runner never sees it.
        hand_merged = adopter.lane_change("delta-copy", {"src/delta.txt": "delta one\n"})
        handed = {"order": 4, "subject": "delta-copy", "title": "Candidate delta-copy", "lane": "lane-1",
                  "stage": "queued", "head": hand_merged, "syncedHead": None, "leadInputs": [], "reportedRounds": 1,
                  "runId": None, "checks": []}
        adopter.lead_patch({"queue": adopter.show()["state"]["queue"] + [handed]})
        adopter.git("push", "--quiet", REMOTE, f"lane/delta-copy:{BASE}", cwd=adopter.lane)
        self.assertEqual(adopter.tip(BASE), hand_merged)

        # The lead writes what its tracker and forge say, with its own tools.
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tracker = adopter.root / "tracker snapshot.json"
        tracker.write_text(json.dumps({"takenAt": now, "source": "tracker", "items": [
            {"subject": "gamma-notes", "title": "Notes for gamma", "state": "backlog"},
            {"subject": "epsilon-copy", "title": "Copy for epsilon", "state": "started"},
            {"subject": "alpha-copy", "state": "completed"},
            {"subject": "delta-copy", "state": "completed"},
        ]}, indent=2), encoding="utf-8")
        forge = adopter.root / "forge snapshot.json"
        forge.write_text(json.dumps({"takenAt": now, "source": "forge", "items": [
            {"subject": "epsilon-copy", "state": "open", "changeRequest": "change 5"},
            {"subject": "delta-copy", "state": "merged", "changeRequest": "change 4", "head": hand_merged},
        ]}, indent=2), encoding="utf-8")
        snapshots = ["--snapshot", str(tracker), "--snapshot", str(forge)]

        reconciled = adopter.state_tool("reconcile", *snapshots)
        self.assertTrue(reconciled["drift"])
        self.assertEqual([(change["subject"], change["recovery"]) for change in reconciled["changes"]], [
            ("gamma-notes", False), ("epsilon-copy", False), ("epsilon-copy", False), ("epsilon-copy", False),
            ("delta-copy", False), ("delta-copy", True),
        ])
        self.assertEqual(reconciled["reports"], [])
        state = adopter.show()["state"]
        self.assertEqual(state["followups"][-1], {"id": "gamma-notes", "title": "Notes for gamma",
                                                  "reference": "gamma-notes"})
        self.assertEqual(state["planned"], [{"subject": "epsilon-copy", "title": "Copy for epsilon", "lane": None,
                                             "stage": "review", "changeRequest": "change 5"}])
        recovered = adopter.item("delta-copy")
        self.assertEqual((recovered["stage"], recovered["syncedHead"], recovered["changeRequest"]),
                         ("merged", hand_merged, "change 4"))
        recorded = adopter.show()["journal"][-1]
        self.assertEqual((recorded["kind"], recorded["subject"]), ("candidate.merged", "delta-copy"))
        self.assertIn(hand_merged, recorded["summary"])

        # Nothing has been published yet, so the view lags the record.
        shown = adopter.show()
        self.assertTrue(shown["publication"]["lags"])
        self.assertIsNone(shown["publication"]["publishedVersion"])

        # Render: the host publisher leaves the projection and every unacknowledged entry in the outbox.
        binding = str(adopter.operator / ".agents/orchestration.json")
        adopter.tool(WRAPPER, "--dir", str(adopter.state), "--binding", binding)
        outbox_path = adopter.state / "outbox" / "outbox.json"
        outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
        self.assertEqual(outbox["version"], shown["state"]["version"])
        self.assertEqual(outbox["state"], shown["state"])
        self.assertEqual(outbox["journal"], shown["journal"])
        self.assertFalse(adopter.show()["publication"]["outboxLags"])

        # The lead publishes the outbox with its host's own tool, then acknowledges what it published.
        last = outbox["journalThrough"]
        self.assertEqual(last, outbox["journal"][-1]["id"])
        adopter.state_tool("ack", "--version", str(outbox["version"]), "--through", str(last))
        published = adopter.show()["publication"]
        self.assertEqual((published["publishedVersion"], published["journalThrough"], published["unpublishedEntries"],
                          published["lags"]), (outbox["version"], last, 0, False))

        # The next pass finds no drift and has nothing new to publish.
        again = adopter.state_tool("reconcile", *snapshots)
        self.assertEqual((again["drift"], again["changes"], again["summary"]), (False, [], "no drift"))
        adopter.tool(WRAPPER, "--dir", str(adopter.state), "--binding", binding)
        self.assertEqual(json.loads(outbox_path.read_text(encoding="utf-8"))["journal"], [])

        # A new entry lags the published view, and the next outbox carries exactly that entry.
        adopter.journal("delivery.closed", "The hand-merged candidate is recorded")
        self.assertTrue(adopter.show()["publication"]["lags"])
        adopter.tool(WRAPPER, "--dir", str(adopter.state), "--binding", binding)
        pending = json.loads(outbox_path.read_text(encoding="utf-8"))["journal"]
        self.assertEqual([(entry["id"], entry["kind"]) for entry in pending], [(last + 1, "delivery.closed")])


# Defaults a tool must never assume about its adopter: a remote or branch
# name, a package runner, a forge command line, or skipping a repository's
# hooks by command-line flag. Only quoted string forms and full ref names are
# matched, so ordinary words and identifiers such as __main__ never are.
ADOPTER_DEFAULTS = (
    ("remote name", re.compile(r"""(["'])origin\1""")),
    ("branch name", re.compile(r"""(["'])(?:main|master)\1""")),
    ("branch ref", re.compile(r"refs/heads/(?:main|master)\b")),
    ("remote-tracking ref", re.compile(r"""["'](?:origin|upstream)/(?:main|master)\b""")),
    ("short hook skip", re.compile(r"""(["'])commit\1,\s*(["'])-n\2|\bgit commit\b[^"'\n]*\s-n\b""")),
    ("package runner", re.compile(r"""(["'])(?:npm|npx|pnpm|yarn|bun|bunx|uv|uvx|pipx)\1""")),
    ("hook skip", re.compile(r"--no-verify\b")),
    ("forge command line", re.compile(r"""(["'])(?:gh|glab|hub)\1|["'](?:gh|glab|hub)\s""")),
)


class AdopterNeutralityTests(unittest.TestCase):
    def tool_sources(self) -> list[Path]:
        return [*sorted(SCRIPTS.glob("*.py")), ROOT / "skills/orchestrate-work/references/schemas.json"]

    def fixture_files(self) -> list[Path]:
        return sorted(path for path in FIXTURE.rglob("*") if path.is_file())

    def test_the_tools_assume_no_adopter_default(self) -> None:
        self.assertEqual([path.name for path in self.tool_sources()],
                         ["delivery_state.py", "merge_queue.py", "render_state.py", "schemas.json"])
        for path in self.tool_sources():
            contents = path.read_text(encoding="utf-8")
            for name, pattern in ADOPTER_DEFAULTS:
                with self.subTest(path=path.name, literal=name):
                    self.assertIsNone(pattern.search(contents))

    def test_the_scan_finds_each_default_it_looks_for(self) -> None:
        for planted in ('remote = "origin"', "branch = 'main'", "refs/heads/main", '["npx", "x"]',
                        "git push --no-verify", 'run(["gh", "pr"])', 'base = "origin/main"',
                        '["git", "commit", "-n", "-m", "x"]', "git commit -q -n -m x", 'command = "gh pr merge"'):
            with self.subTest(planted=planted):
                self.assertTrue(any(pattern.search(planted) for _, pattern in ADOPTER_DEFAULTS))
        for innocent in ('if __name__ == "__main__":', "the main checkout", "origin_url = remote",
                         "core.hooksPath", "highlight", '"-n"', 'git log -n 1', '"ghost"'):
            with self.subTest(innocent=innocent):
                self.assertFalse(any(pattern.search(innocent) for _, pattern in ADOPTER_DEFAULTS))

    def test_the_fixture_names_no_first_adopter_and_no_adopter_default(self) -> None:
        files = self.fixture_files()
        self.assertTrue(files)
        for path in files:
            contents = path.read_text(encoding="utf-8")
            for class_id, pattern in LEAKAGE_PATTERNS:
                with self.subTest(path=path.relative_to(FIXTURE).as_posix(), pattern=class_id):
                    self.assertIsNone(pattern.search(contents))
            for name, pattern in ADOPTER_DEFAULTS:
                with self.subTest(path=path.relative_to(FIXTURE).as_posix(), literal=name):
                    self.assertIsNone(pattern.search(contents))


if __name__ == "__main__":
    unittest.main()
