"""run-delivery's plan sync: `plan-sync` composes two authors' plan edits section by section and pushes them, and
`plan-watch` streams each peer push as one line.

Two clones of one bare remote stand in for two engineers' machines. The bare remote records every ref update and allows
forced ones, as a forge does, so a forced push the tool made would land and show in its reflog. Each case works on a
plan branch of its own, so the clones and the remote are built once for the class.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.collab_remotes import assert_fast_forwards_only, bare_remote
from tests.quiet_git import quiet_clone, quieten
from tests.test_run_delivery import POSIX_ONLY, ROOT, clean_environment, sh

TOOL = ROOT / "skills/run-delivery/scripts/run_delivery.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


sys.path.insert(0, str(ROOT / "skills/orchestrate-work/scripts"))
MARKDOWN = load("region_ownership_for_plan_sync", ROOT / "skills/orchestrate-work/scripts/region_ownership.py")
PLAN_SYNC = load("plan_sync_for_tests", ROOT / "skills/run-delivery/scripts/plan_sync.py")


def plan(slug: str, sections: list[tuple[str, str]], matter: str = "title: A plan") -> str:
    """A plan: its front matter and one line under it, then each (heading, body) section in order."""
    text = f"---\n{matter}\nreviewSlug: {slug}\n---\n\nA preamble line.\n"
    for heading, body in sections:
        text += f"\n{heading}\n\n{body}\n"
    return text


BASE = [("# The plan", "Its intro."), ("## Alpha", "alpha body"), ("## Beta", "beta body"),
        ("### Beta detail", "beta detail body"), ("## Gamma", "gamma body"), ("## Delta", "delta body")]


def edited(sections: list[tuple[str, str]], heading: str, body: str) -> list[tuple[str, str]]:
    return [(h, body if h == heading else b) for h, b in sections]


def without(sections: list[tuple[str, str]], heading: str) -> list[tuple[str, str]]:
    return [(h, b) for h, b in sections if h != heading]


def added(sections: list[tuple[str, str]], after: str, heading: str, body: str) -> list[tuple[str, str]]:
    at = [h for h, _ in sections].index(after) + 1
    return [*sections[:at], (heading, body), *sections[at:]]


def git(*args: str, cwd: Path, check: bool = True) -> str:
    return sh("git", *args, cwd=cwd, check=check).stdout.strip()


def seeded(folder: Path) -> Path:
    """A repository to seed the remote's main from."""
    git("init", "-q", "-b", "main", str(folder), cwd=folder.parent)
    quieten(folder)
    return folder


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PlanSyncTests(unittest.TestCase):
    """Ours is clone A, theirs is clone B. B pushes first, then A syncs, so A composes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="plan-sync-"))
        cls.remote = bare_remote(cls.folder / "remote.git")
        seed = seeded(cls.folder / "seed")
        (seed / "README.md").write_text("seed\n")
        git("add", "README.md", cwd=seed)
        git("-c", "user.name=Seed", "-c", "user.email=seed@example.com", "commit", "-qm", "seed", cwd=seed)
        git("push", "-q", str(cls.remote), "main", cwd=seed)
        cls.ours, cls.theirs = cls.folder / "a", cls.folder / "b"
        for clone, who in ((cls.ours, "Ann"), (cls.theirs, "Ben")):
            quiet_clone(cls.remote, clone, settings=f"[user]\n\tname = {who}\n\temail = {who.lower()}@example.com\n")
        cls.heads_before = set(git("ls-remote", "--heads", "--tags", str(cls.remote), cwd=cls.folder).split("\n"))
        cls.fetch_before = git("config", "--get-all", "remote.origin.fetch", cwd=cls.ours)
        cls.count = 0

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(["rm", "-rf", str(cls.folder)], check=False)

    def start(self, sections=BASE, matter: str = "title: A plan") -> tuple[str, str, str]:
        """A fresh plan branch, pushed by A and checked out in B. Returns its slug, branch and plan path."""
        type(self).count += 1
        slug = f"case-{self.count}"
        branch, path = f"plan/{slug}", f"docs/{slug}.md"
        git("switch", "-q", "-c", branch, "main", cwd=self.ours)
        self.write(self.ours, path, plan(slug, sections, matter))
        git("add", path, cwd=self.ours)
        git("commit", "-qm", "the first draft", cwd=self.ours)
        self.sync(self.ours, path, branch)
        git("fetch", "-q", "origin", cwd=self.theirs)
        git("switch", "-q", "-c", branch, f"origin/{branch}", cwd=self.theirs)
        return slug, branch, path

    def write(self, clone: Path, path: str, text: str) -> None:
        (clone / path).parent.mkdir(parents=True, exist_ok=True)
        (clone / path).write_text(text)

    def sync(self, clone: Path, path: str, branch: str, **kw) -> list[str]:
        return PLAN_SYNC.sync(clone, path, branch, "origin", MARKDOWN, **kw)

    def remote_text(self, branch: str, path: str) -> str:
        return git("show", f"refs/heads/{branch}:{path}", cwd=self.remote) + "\n"

    def compose_case(self, ours: str, theirs: str, expected: str, *, theirs_matter: str | None = None,
                     ours_matter: str | None = None) -> None:
        """B pushes `theirs`, then A syncs `ours` uncommitted; both end at `expected`, exactly."""
        slug, branch, path = self.start()
        self.write(self.theirs, path, theirs)
        self.sync(self.theirs, path, branch)
        self.write(self.ours, path, ours)
        self.sync(self.ours, path, branch)
        self.assertEqual((self.ours / path).read_text(), expected)
        self.assertEqual(self.remote_text(branch, path), expected)
        self.assertEqual(git("status", "--porcelain", cwd=self.ours), "")
        self.sync(self.theirs, path, branch)
        self.assertEqual((self.theirs / path).read_text(), expected)
        assert_fast_forwards_only(self.remote, f"refs/heads/{branch}")

    # -- composing --------------------------------------------------------------------------------------------------

    def test_ours_edits_one_section_theirs_another(self) -> None:
        slug = f"case-{self.count + 1}"
        self.compose_case(plan(slug, edited(BASE, "## Beta", "ours beta")),
                          plan(slug, edited(BASE, "## Delta", "theirs delta")),
                          plan(slug, edited(edited(BASE, "## Beta", "ours beta"), "## Delta", "theirs delta")))

    def test_ours_adds_a_section_theirs_edits_another(self) -> None:
        slug = f"case-{self.count + 1}"
        self.compose_case(plan(slug, added(BASE, "## Alpha", "## Ours new", "ours new body")),
                          plan(slug, edited(BASE, "## Delta", "theirs delta")),
                          plan(slug, added(edited(BASE, "## Delta", "theirs delta"), "## Alpha", "## Ours new",
                                           "ours new body")))

    def test_theirs_adds_a_section_ours_edits_another(self) -> None:
        slug = f"case-{self.count + 1}"
        self.compose_case(plan(slug, edited(BASE, "## Delta", "ours delta")),
                          plan(slug, added(BASE, "## Beta", "## Theirs new", "theirs new body")),
                          plan(slug, added(edited(BASE, "## Delta", "ours delta"), "## Beta", "## Theirs new",
                                           "theirs new body")))

    def test_ours_deletes_a_section_theirs_edits_another(self) -> None:
        slug = f"case-{self.count + 1}"
        self.compose_case(plan(slug, without(BASE, "## Gamma")),
                          plan(slug, edited(BASE, "## Alpha", "theirs alpha")),
                          plan(slug, without(edited(BASE, "## Alpha", "theirs alpha"), "## Gamma")))

    def test_theirs_deletes_a_section_ours_left_unchanged(self) -> None:
        slug = f"case-{self.count + 1}"
        self.compose_case(plan(slug, edited(BASE, "## Delta", "ours delta")),
                          plan(slug, without(BASE, "## Alpha")),
                          plan(slug, without(edited(BASE, "## Delta", "ours delta"), "## Alpha")))

    def test_only_one_side_edits_the_front_matter(self) -> None:
        slug = f"case-{self.count + 1}"
        self.compose_case(plan(slug, edited(BASE, "## Gamma", "ours gamma")),
                          plan(slug, BASE, matter="title: Their title"),
                          plan(slug, edited(BASE, "## Gamma", "ours gamma"), matter="title: Their title"))

    def test_an_addition_after_a_section_theirs_deleted_lands_after_the_one_before_it(self) -> None:
        slug = f"case-{self.count + 1}"
        self.compose_case(plan(slug, added(BASE, "## Gamma", "## Ours new", "ours new body")),
                          plan(slug, without(BASE, "## Gamma")),
                          plan(slug, added(without(BASE, "## Gamma"), "### Beta detail", "## Ours new",
                                           "ours new body")))

    def test_an_addition_after_a_deleted_first_section_lands_after_the_preamble(self) -> None:
        slug = f"case-{self.count + 1}"
        first = [("# The plan", "Its intro.")]
        rest = BASE[1:]
        expected = f"---\ntitle: A plan\nreviewSlug: {slug}\n---\n\nA preamble line.\n"
        expected += "".join(f"\n{h}\n\n{b}\n" for h, b in [("## Ours new", "ours new body"), *rest])
        self.compose_case(plan(slug, [*first, ("## Ours new", "ours new body"), *rest]),
                          plan(slug, rest), expected)

    def test_an_edit_ending_one_section_and_one_to_the_next_heading_compose(self) -> None:
        """Git's three-way merge conflicts here: the two edits touch adjacent lines."""
        slug = f"case-{self.count + 1}"
        last, heading = "beta detail body\n\n## Gamma\n", "## Gamma\n"
        ours = plan(slug, BASE).replace(last, "beta detail body\nours last line\n\n## Gamma\n")
        theirs = plan(slug, BASE).replace(heading, "## Gamma, renamed\n")
        expected = plan(slug, BASE).replace(last, "beta detail body\nours last line\n\n## Gamma, renamed\n")
        self.assertNotIn(ours, (theirs, expected))
        self.assertNotEqual(theirs, expected)
        self.compose_case(ours, theirs, expected)

    def test_ours_committed_edits_compose_too(self) -> None:
        slug, branch, path = self.start()
        self.write(self.theirs, path, plan(slug, edited(BASE, "## Alpha", "theirs alpha")))
        self.sync(self.theirs, path, branch)
        self.write(self.ours, path, plan(slug, edited(BASE, "## Gamma", "ours gamma")))
        git("commit", "-qam", "ours, committed", cwd=self.ours)
        self.sync(self.ours, path, branch)
        expected = plan(slug, edited(edited(BASE, "## Alpha", "theirs alpha"), "## Gamma", "ours gamma"))
        self.assertEqual(self.remote_text(branch, path), expected)
        parents = git("log", "-1", "--format=%P", cwd=self.ours).split()
        self.assertEqual(len(parents), 2)
        self.assertIn(git("rev-parse", f"refs/heads/{branch}", cwd=self.theirs), parents)

    # -- refusals ---------------------------------------------------------------------------------------------------

    def surfaces(self, branch: str) -> tuple[str, str, str]:
        return (git("rev-parse", "HEAD", cwd=self.ours), git("rev-parse", f"refs/heads/{branch}", cwd=self.remote),
                git("status", "--porcelain", cwd=self.ours))

    def refused(self, ours: str, theirs: str, section: str, *, ours_line: str, theirs_line: str) -> None:
        slug, branch, path = self.start()
        self.write(self.theirs, path, theirs)
        self.sync(self.theirs, path, branch)
        self.write(self.ours, path, ours)
        before = self.surfaces(branch)
        with self.assertRaises(PLAN_SYNC.Refused) as caught:
            self.sync(self.ours, path, branch)
        self.assertEqual(caught.exception.code, "plan.sections_overlap")
        message = str(caught.exception)
        self.assertIn(section, message)
        self.assertIn(ours_line, message)
        self.assertIn(theirs_line, message)
        self.assertEqual(self.surfaces(branch), before)
        self.assertEqual((self.ours / path).read_text(), ours)
        assert_fast_forwards_only(self.remote, f"refs/heads/{branch}")
        # The allowed case beside it: once ours takes theirs' side, it syncs.
        self.write(self.ours, path, theirs)
        self.sync(self.ours, path, branch)
        self.assertEqual(self.remote_text(branch, path), theirs)

    def test_one_section_edited_on_both_sides_is_refused(self) -> None:
        slug = f"case-{self.count + 1}"
        self.refused(plan(slug, edited(BASE, "## Beta", "ours beta")), plan(slug, edited(BASE, "## Beta", "theirs beta")),
                     "## Beta", ours_line="+ours beta", theirs_line="+theirs beta")

    def test_a_delete_against_an_edit_is_refused(self) -> None:
        slug = f"case-{self.count + 1}"
        self.refused(plan(slug, without(BASE, "## Gamma")), plan(slug, edited(BASE, "## Gamma", "theirs gamma")),
                     "## Gamma", ours_line="-gamma body", theirs_line="+theirs gamma")

    def test_both_sides_editing_the_front_matter_is_refused(self) -> None:
        slug = f"case-{self.count + 1}"
        self.refused(plan(slug, BASE, matter="title: Our title"), plan(slug, BASE, matter="title: Their title"),
                     "(preamble)", ours_line="+title: Our title", theirs_line="+title: Their title")

    def peer_pushes(self, branch: str, path: str, times: int):
        """A before-push hook: on each of A's first `times` attempts, B first pushes one more line ending Delta."""
        def before(attempt: int) -> None:
            if attempt <= times:
                self.write(self.theirs, path, (self.theirs / path).read_text() + f"theirs {attempt}\n")
                self.sync(self.theirs, path, branch)
        return before

    def test_five_rejected_pushes_then_a_success_land(self) -> None:
        slug, branch, path = self.start()
        self.write(self.ours, path, plan(slug, edited(BASE, "## Alpha", "ours alpha")))
        said = self.sync(self.ours, path, branch, before_push=self.peer_pushes(branch, path, 5))
        lines = "".join(f"theirs {n}\n" for n in range(1, 6))
        expected = plan(slug, edited(BASE, "## Alpha", "ours alpha")).replace("delta body\n", f"delta body\n{lines}")
        self.assertEqual(self.remote_text(branch, path), expected)
        self.assertEqual((self.ours / path).read_text(), expected)
        self.assertTrue(any("5" in line and "reject" in line for line in said), said)
        assert_fast_forwards_only(self.remote, f"refs/heads/{branch}")

    def test_a_sixth_rejection_stops_with_the_branch_at_its_last_composed_commit(self) -> None:
        slug, branch, path = self.start()
        self.write(self.ours, path, plan(slug, edited(BASE, "## Alpha", "ours alpha")))
        with self.assertRaises(PLAN_SYNC.Refused) as caught:
            self.sync(self.ours, path, branch, before_push=self.peer_pushes(branch, path, 6))
        self.assertEqual(caught.exception.code, "plan.push_exhausted")
        peer = git("rev-parse", f"refs/heads/{branch}", cwd=self.theirs)
        self.assertEqual(git("rev-parse", f"refs/heads/{branch}", cwd=self.remote), peer)
        lines = "".join(f"theirs {n}\n" for n in range(1, 6))
        expected = plan(slug, edited(BASE, "## Alpha", "ours alpha")).replace("delta body\n", f"delta body\n{lines}")
        self.assertEqual(git("show", f"HEAD:{path}", cwd=self.ours) + "\n", expected)
        self.assertIn(git("rev-parse", f"{peer}~1", cwd=self.theirs), git("log", "-1", "--format=%P", cwd=self.ours))
        self.assertEqual(git("status", "--porcelain", cwd=self.ours), "")
        assert_fast_forwards_only(self.remote, f"refs/heads/{branch}")
        # The allowed case beside it: the next sync composes the sixth line and lands.
        self.sync(self.ours, path, branch)
        self.assertEqual(self.remote_text(branch, path),
                         expected.replace("theirs 5\n", "theirs 5\ntheirs 6\n"))

    # -- what stays untouched -----------------------------------------------------------------------------------------

    def test_only_plan_branches_are_new_and_the_fetch_refspec_is_unchanged(self) -> None:
        slug, branch, path = self.start()
        self.write(self.ours, path, plan(slug, edited(BASE, "## Beta", "ours beta")))
        self.sync(self.ours, path, branch)
        heads = set(git("ls-remote", "--heads", "--tags", str(self.remote), cwd=self.folder).split("\n"))
        new = {line.split("\t")[1] for line in heads - self.heads_before}
        self.assertTrue(new)
        self.assertTrue(all(ref.startswith("refs/heads/plan/case-") for ref in new), new)
        self.assertIn(f"refs/heads/{branch}", new)
        self.assertEqual(git("config", "--get-all", "remote.origin.fetch", cwd=self.ours), self.fetch_before)
        self.assertEqual(git("config", "--get-all", "remote.origin.fetch", cwd=self.theirs), self.fetch_before)

    def test_a_branch_not_checked_out_is_refused_before_anything_changes(self) -> None:
        slug, branch, path = self.start()
        git("switch", "-q", "main", cwd=self.ours)
        try:
            with self.assertRaises(PLAN_SYNC.Refused) as caught:
                self.sync(self.ours, path, branch)
            self.assertIn(f"git switch {branch}", str(caught.exception))
        finally:
            git("switch", "-q", branch, cwd=self.ours)

    # -- watching ---------------------------------------------------------------------------------------------------

    def ticking(self, poll: float, at_tick: dict):
        """A fake clock and sleep for `watch`: each sleep is one tick, and `at_tick[n]` runs during the nth sleep."""
        state = {"now": 0.0, "ticks": 0}

        def clock() -> float:
            return state["now"]

        def sleep(seconds: float) -> None:
            state["ticks"] += 1
            state["now"] += seconds
            if state["ticks"] in at_tick:
                at_tick[state["ticks"]]()
        return clock, sleep, state

    def peer_edit(self, branch: str, path: str, heading: str, body: str):
        def push() -> None:
            text = (self.theirs / path).read_text()
            self.write(self.theirs, path, text.replace("## Delta\n", f"{heading}\n\n{body}\n\n## Delta\n"))
            self.sync(self.theirs, path, branch)
        return push

    def test_each_peer_push_is_one_line_within_two_ticks_and_the_watch_exits_0_at_its_timeout(self) -> None:
        slug, branch, path = self.start()
        lines, seen_at = [], {}
        clock, sleep, state = self.ticking(2, {1: self.peer_edit(branch, path, "## Peer one", "one"),
                                               3: self.peer_edit(branch, path, "## Peer two", "two")})

        def write(line: str) -> None:
            lines.append(line)
            seen_at[len(lines)] = state["ticks"]
        # Ours' own sync is not a peer push: it shows nothing.
        self.write(self.ours, path, plan(slug, edited(BASE, "## Alpha", "ours alpha")))
        self.sync(self.ours, path, branch)
        code = PLAN_SYNC.watch(self.ours, path, branch, "origin", MARKDOWN, poll=2, timeout=12, until=None,
                               write=write, sleep=sleep, clock=clock)
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2, lines)
        self.assertLessEqual(seen_at[1] - 1, 2)
        self.assertLessEqual(seen_at[2] - 3, 2)
        self.assertIn("Ben", lines[0])
        self.assertIn("## Peer one", lines[0])
        self.assertIn("## Peer two", lines[1])
        self.assertTrue(all("\n" not in line for line in lines))
        self.assertGreaterEqual(state["now"], 12)

    def test_until_exits_0_on_the_first_matching_line(self) -> None:
        slug, branch, path = self.start()
        lines = []
        clock, sleep, state = self.ticking(2, {1: self.peer_edit(branch, path, "## Peer one", "one"),
                                               2: self.peer_edit(branch, path, "## Peer two", "two"),
                                               4: self.peer_edit(branch, path, "## Peer three", "three")})
        code = PLAN_SYNC.watch(self.ours, path, branch, "origin", MARKDOWN, poll=2, timeout=60, until="Peer two",
                               write=lines.append, sleep=sleep, clock=clock)
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("## Peer two", lines[-1])
        self.assertLess(state["now"], 60)

    def test_an_until_that_never_matches_exits_3_at_its_timeout(self) -> None:
        slug, branch, path = self.start()
        lines = []
        clock, sleep, state = self.ticking(2, {1: self.peer_edit(branch, path, "## Peer one", "one")})
        code = PLAN_SYNC.watch(self.ours, path, branch, "origin", MARKDOWN, poll=2, timeout=8, until="never seen",
                               write=lines.append, sleep=sleep, clock=clock)
        self.assertEqual(code, 3)
        self.assertEqual(len(lines), 1, lines)
        self.assertGreaterEqual(state["now"], 8)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PlanSyncCommandTests(unittest.TestCase):
    """The commands through run_delivery.py's own command line, in a clone of a bare remote of their own."""

    run_quietly = True  # the watch waits on a real poll of a real git remote

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="plan-sync-command-"))
        cls.remote = bare_remote(cls.folder / "remote.git")
        cls.ours, cls.theirs = cls.folder / "a", cls.folder / "b"
        seed = seeded(cls.folder / "seed")
        slug = "watched"
        (seed / "docs").mkdir()
        (seed / "docs/watched.md").write_text(plan(slug, BASE))
        for other in ("delivered", "streamed", "quiet"):
            (seed / f"docs/{other}.md").write_text(plan(other, BASE))
        (seed / ".agents/deliveries").mkdir(parents=True)
        (seed / ".agents/deliveries/FX-9.json").write_text(json.dumps(
            {"schemaVersion": "delivery-flow-delivery/1", "delivery": "FX-9", "plan": "docs/delivered.md"}, indent=2) + "\n")
        git("add", "-A", cwd=seed)
        git("-c", "user.name=Seed", "-c", "user.email=seed@example.com", "commit", "-qm", "seed", cwd=seed)
        git("push", "-q", str(cls.remote), "main", cwd=seed)
        for clone, who in ((cls.ours, "Ann"), (cls.theirs, "Ben")):
            quiet_clone(cls.remote, clone, settings=f"[user]\n\tname = {who}\n\temail = {who.lower()}@example.com\n")
        cls.config = cls.folder / "run-delivery.json"
        cls.config.write_text(json.dumps({"schemaVersion": "delivery-flow/1", "collab": {"pollSeconds": 2}}) + "\n")

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(["rm", "-rf", str(cls.folder)], check=False)

    def tool(self, *args: str, cwd: Path, config: Path | None = None, **kw):
        env = {**clean_environment(), "RUN_DELIVERY_CONFIG": str(config or self.config)}
        return subprocess.run([sys.executable, "-B", str(TOOL), *args], cwd=cwd, env=env, text=True,
                              capture_output=True, **kw)

    def test_plan_sync_pushes_the_plan_branch_and_a_delivered_plan_pushes_its_lead_branch(self) -> None:
        for clone in (self.ours, self.theirs):
            git("switch", "-q", "-c", "plan/watched", "main", cwd=clone)
        done = self.tool("plan-sync", "--plan", "docs/watched.md", cwd=self.ours)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(git("rev-parse", "refs/heads/plan/watched", cwd=self.remote),
                         git("rev-parse", "HEAD", cwd=self.ours))
        git("switch", "-q", "-c", "lead/FX-9", "main", cwd=self.ours)
        (self.ours / "docs/delivered.md").write_text(plan("delivered", edited(BASE, "## Beta", "lead beta")))
        done = self.tool("plan-sync", "--plan", "docs/delivered.md", cwd=self.ours)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("lead beta", git("show", "refs/heads/lead/FX-9:docs/delivered.md", cwd=self.remote))
        self.assertEqual(git("ls-remote", "--heads", str(self.remote), "plan/delivered", cwd=self.folder), "")
        # A refusal carries its code on the command line.
        git("switch", "-q", "plan/watched", cwd=self.ours)
        (self.theirs / "docs/watched.md").write_text(plan("watched", edited(BASE, "## Gamma", "theirs gamma")))
        self.assertEqual(self.tool("plan-sync", "--plan", "docs/watched.md", cwd=self.theirs).returncode, 0)
        (self.ours / "docs/watched.md").write_text(plan("watched", edited(BASE, "## Gamma", "ours gamma")))
        done = self.tool("plan-sync", "--plan", "docs/watched.md", cwd=self.ours)
        self.assertEqual(done.returncode, 2)
        self.assertIn("plan.sections_overlap", done.stderr)
        self.assertIn("## Gamma", done.stderr)
        (self.ours / "docs/watched.md").write_text(plan("watched", edited(BASE, "## Gamma", "theirs gamma")))
        self.assertEqual(self.tool("plan-sync", "--plan", "docs/watched.md", cwd=self.ours).returncode, 0)
        assert_fast_forwards_only(self.remote, "refs/heads/plan/watched")
        assert_fast_forwards_only(self.remote, "refs/heads/lead/FX-9")

    def test_plan_watch_prints_a_flushed_line_per_peer_push(self) -> None:
        for clone in (self.ours, self.theirs):
            git("switch", "-q", "-c", "plan/streamed", "main", cwd=clone)
        (self.ours / "docs/streamed.md").write_text(plan("streamed", edited(BASE, "## Alpha", "ours alpha")))
        self.assertEqual(self.tool("plan-sync", "--plan", "docs/streamed.md", cwd=self.ours).returncode, 0)
        git("fetch", "-q", "origin", cwd=self.theirs)
        git("reset", "-q", "--hard", "origin/plan/streamed", cwd=self.theirs)
        started = time.monotonic()
        watch = subprocess.Popen([sys.executable, "-B", str(TOOL), "plan-watch", "--plan", "docs/streamed.md",
                                  "--timeout", "30", "--until", "Epsilon"],
                                 cwd=self.ours, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env={**clean_environment(), "RUN_DELIVERY_CONFIG": str(self.config)})
        self.addCleanup(lambda: watch.poll() is None and watch.kill())
        text = (self.theirs / "docs/streamed.md").read_text().replace("## Delta\n", "## Epsilon\n\nnew\n\n## Delta\n")
        (self.theirs / "docs/streamed.md").write_text(text)
        done = self.tool("plan-sync", "--plan", "docs/streamed.md", cwd=self.theirs)
        self.assertEqual(done.returncode, 0, done.stderr)
        pushed = time.monotonic()
        line = watch.stdout.readline()
        arrived = time.monotonic()
        out, err = watch.communicate(timeout=30)
        self.assertEqual(watch.returncode, 0, err)
        self.assertIn("## Epsilon", line)
        self.assertEqual(out, "")
        self.assertLess(arrived - pushed, 2 * 2 + 3, (started, pushed, arrived))

    def test_a_poll_below_two_seconds_is_refused_by_the_settings(self) -> None:
        config = self.folder / "fast.json"
        config.write_text(json.dumps({"schemaVersion": "delivery-flow/1", "collab": {"pollSeconds": 1}}) + "\n")
        done = self.tool("plan-watch", "--plan", "docs/watched.md", "--timeout", "1", cwd=self.ours, config=config)
        self.assertEqual(done.returncode, 2)
        self.assertIn("collab.pollSeconds", done.stderr)
        self.assertEqual(done.stdout, "")
        config.write_text(json.dumps({"schemaVersion": "delivery-flow/1", "collab": {"autoGrant": "yes"}}) + "\n")
        done = self.tool("plan-watch", "--plan", "docs/watched.md", "--timeout", "1", cwd=self.ours, config=config)
        self.assertEqual(done.returncode, 2)
        self.assertIn("collab.autoGrant", done.stderr)
        # The allowed case beside it: two seconds is the floor, and a watch with nothing to see exits 0 at its timeout.
        config.write_text(json.dumps({"schemaVersion": "delivery-flow/1", "collab": {"pollSeconds": 2,
                                                                                     "autoGrant": False}}) + "\n")
        done = self.tool("plan-watch", "--plan", "docs/quiet.md", "--timeout", "1", cwd=self.ours, config=config)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()
