"""A pass reads each lane's pushed branch, says when a lane changes a file it doesn't hold, and feeds the files view.

Each case builds a small origin and a lead clone with `tests/quiet_git.py`, and runs the supervisor's branch read and
its "Needs you" items in process, with the record handed to them and the release's region helper from this tree.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.quiet_git import quiet_clone, quieten

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/orchestrate-work/scripts"


def _load(name: str, path: Path):
    # Importing the skill's modules here must not leave bytecode beside them.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))
        sys.dont_write_bytecode = previous


region_ownership = _load("region_ownership", SCRIPTS / "region_ownership.py")
run_delivery = _load("run_delivery", ROOT / "skills/run-delivery/scripts/run_delivery.py")

SHARED = "src/shared.py"
SHARED_LINES = "".join([f"def first_{i}():\n    return {i}\n\n\n" for i in range(4)]
                       + ["def held():\n"] + [f"    value_{i} = {i}\n" for i in range(6)] + ["    return value_0\n\n\n"]
                       + [f"def after_{i}():\n    return {i}\n\n\n" for i in range(4)]).splitlines(keepends=True)


def git(cwd: Path, *argv: str) -> str:
    done = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *argv], cwd=cwd, capture_output=True, text=True)
    if done.returncode:
        raise AssertionError(f"git {' '.join(argv)} failed: {done.stderr}")
    return done.stdout.strip()


class Delivery:
    """An origin whose `main` is the delivery base, a lead clone that reads it, and each story's branch worktree."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.origin = folder / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        quieten(self.origin)
        seed = folder / "seed"
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
        quieten(seed)
        files = {"src/one.py": "def one():\n    return 1\n", "src/two.js": "function two() {\n  return 2;\n}\n",
                 "docs/guide.md": "# Guide\n\n## Setup\n\nRun it.\n", SHARED: "".join(SHARED_LINES),
                 "gen/listing.json": "{}\n"}
        self.write(seed, files)
        git(seed, "add", "-A")
        git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
        git(seed, "push", "-q", str(self.origin), "main")
        self.lead = folder / "lead"
        quiet_clone(self.origin, self.lead)
        self.base = git(self.lead, "rev-parse", "HEAD")
        self.record = folder / "record"
        self.record.mkdir()
        self.state = {"lanes": [{"id": "lane-1", "state": "working"}, {"id": "lane-2", "state": "working"}],
                      "planned": [], "queue": [],
                      "laneOwnership": {"lane-1": ["src/one*", "docs/*"], "lane-2": ["src/two*"]}}

    @staticmethod
    def write(checkout: Path, files: dict) -> None:
        for name, text in files.items():
            (checkout / name).parent.mkdir(parents=True, exist_ok=True)
            (checkout / name).write_text(text)

    def story(self, key: str, lane: str) -> Path:
        self.state["planned"].append({"subject": key, "lane": lane, "stage": "building"})
        checkout = self.folder / key
        git(self.lead, "worktree", "add", "-q", "-b", f"claude/{key}", str(checkout), "origin/main")
        return checkout

    def push(self, checkout: Path, files: dict, message: str = "change") -> str:
        self.write(checkout, files)
        git(checkout, "add", "-A")
        git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
        git(checkout, "push", "-q", "origin", "HEAD:refs/heads/" + git(checkout, "rev-parse", "--abbrev-ref", "HEAD"))
        return git(checkout, "rev-parse", "HEAD")

    def land(self, files: dict) -> None:
        """Another lane's change landing on the base."""
        checkout = self.folder / "landing"
        if not checkout.exists():
            git(self.lead, "worktree", "add", "-q", "--detach", str(checkout), "origin/main")
        git(checkout, "fetch", "-q", "origin")
        git(checkout, "checkout", "-q", "--detach", "origin/main")
        self.write(checkout, files)
        git(checkout, "add", "-A")
        git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "landed")
        git(checkout, "push", "-q", "origin", "HEAD:refs/heads/main")

    def config(self, shared: tuple[str, ...] = ()) -> SimpleNamespace:
        binding = {"regenerable": {"regenerators": [{"id": "r"}], "listings": [{"path": p} for p in shared]}} if shared else {}
        return SimpleNamespace(root=self.lead, remote="origin", base_ref="origin/main", state=self.record,
                               binding=binding, branch=lambda key: f"claude/{key}",
                               effective_ownership=lambda state: state.get("laneOwnership", {}))

    def one_pass(self, c: SimpleNamespace | None = None) -> tuple[dict, list[dict]]:
        """A pass's branch read, after its fetch, then the "Needs you" items it raises."""
        c = c or self.config()
        git(self.lead, "fetch", "-q", "origin")
        doc = {"state": json.loads(json.dumps(self.state)), "journal": []}
        with mock.patch.object(run_delivery, "load_state", return_value=doc), \
                mock.patch.object(run_delivery, "_region_ownership", return_value=region_ownership):
            run_delivery.write_lane_changes(c)
            return (json.loads((self.record / "lane-changes.json").read_text()),
                    run_delivery.lane_change_needs(c, doc["state"]))


class LaneChangesTests(unittest.TestCase):

    def delivery(self) -> Delivery:
        folder = Path(tempfile.mkdtemp(prefix="lane-changes-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(folder)])
        here = os.getcwd()
        # Acceptance's whole-file check reads the diff in the working directory, which a pass runs in the lead's.
        os.chdir(folder)
        self.addCleanup(os.chdir, here)
        made = Delivery(folder)
        os.chdir(made.lead)
        return made

    def texts(self, items: list[dict]) -> list[str]:
        return [item["text"] for item in items]

    def test_a_change_to_a_file_the_lane_doesnt_hold_is_named_until_reverted_or_granted(self) -> None:
        d = self.delivery()
        checkout = d.story("S-1", "lane-1")
        d.push(checkout, {"src/one.py": "def one():\n    return 11\n"})
        changes, items = d.one_pass()
        self.assertEqual(items, [])
        self.assertEqual([(f["path"], f["holds"]) for f in changes["lanes"]["lane-1"]["files"]], [("src/one.py", "file")])
        d.push(checkout, {"src/two.js": "function two() {\n  return 22;\n}\n"})
        changes, items = d.one_pass()
        self.assertEqual(self.texts(items), ["Lane 1 changed `src/two.js`, which it doesn't hold."])
        [item] = items
        self.assertEqual((item["holding"], item["kind"], item["lane"], item["subject"], item["who"]),
                         (False, "laneOutside", "lane-1", "S-1", "lead"))
        held = {f["path"]: f["holds"] for f in changes["lanes"]["lane-1"]["files"]}
        self.assertEqual(held, {"src/one.py": "file", "src/two.js": "none"})
        with self.subTest("the lead grants the file"):
            d.state["laneOwnership"] = {"lane-1": ["src/one*", "docs/*", "src/two.js"], "lane-2": []}
            _, items = d.one_pass()
            self.assertEqual(items, [])
            d.state["laneOwnership"] = {"lane-1": ["src/one*", "docs/*"], "lane-2": ["src/two*"]}
            _, items = d.one_pass()
            self.assertEqual(len(items), 1)
        with self.subTest("the branch reverts the change"):
            d.push(checkout, {"src/two.js": "function two() {\n  return 2;\n}\n"})
            changes, items = d.one_pass()
            self.assertEqual(items, [])
            self.assertEqual([f["path"] for f in changes["lanes"]["lane-1"]["files"]], ["src/one.py"])

    def test_another_lanes_landed_change_after_the_branch_began_raises_nothing(self) -> None:
        d = self.delivery()
        checkout = d.story("S-1", "lane-1")
        d.push(checkout, {"src/one.py": "def one():\n    return 11\n"})
        d.land({"src/two.js": "function two() {\n  return 3;\n}\n"})
        changes, items = d.one_pass()
        self.assertEqual(items, [])
        self.assertEqual([f["path"] for f in changes["lanes"]["lane-1"]["files"]], ["src/one.py"])
        self.assertEqual(changes["base"], git(d.lead, "rev-parse", "origin/main"))
        self.assertNotEqual(changes["lanes"]["lane-1"]["mergeBase"], changes["base"])

    def test_a_change_inside_a_span_raises_nothing_and_one_outside_names_its_lines(self) -> None:
        d = self.delivery()
        held = SHARED_LINES.index("def held():\n") + 1
        d.state["laneRegions"] = [{"lane": "lane-1", "path": SHARED, "baseHead": d.base, "start": held, "end": held + 9}]
        checkout = d.story("S-1", "lane-1")
        lines = list(SHARED_LINES)
        lines[held + 2] = "    value_2 = 22\n"  # inside the span
        lines[1] = "    return 100\n"  # outside it, in first_0
        lines[len(lines) - 3:len(lines) - 3] = ["    extra = 1\n", "    extra = 2\n"]  # outside it, in after_3
        gone = held + 16  # two lines of after_1, outside the span, deleted
        del lines[gone - 1:gone + 1]
        d.push(checkout, {SHARED: "".join(lines)})
        changes, items = d.one_pass()
        [entry] = changes["lanes"]["lane-1"]["files"]
        self.assertEqual(entry["holds"], "span")
        self.assertEqual([(c["old"], c["added"], c["removed"], c["name"], c["inside"]) for c in entry["changes"]],
                         [(2, 1, 1, "first_0", False), (held + 3, 1, 1, "held", True),
                          (gone, 0, 2, "after_1", False), (len(SHARED_LINES) - 2, 2, 0, "after_3", False)])
        # Named by the merge base's lines, which the span check judged and a grant names, not the branch's.
        self.assertEqual(self.texts(items), [
            f"Lane 1 changed line 2 of `{SHARED}`, outside its span.",
            f"Lane 1 changed lines {gone}-{gone + 1} of `{SHARED}`, outside its span.",
            f"Lane 1 added 2 lines before line {len(SHARED_LINES) - 2} of `{SHARED}`, outside its span."])
        self.assertEqual({item["fields"]["path"] for item in items}, {SHARED})
        self.assertEqual(changes["files"][0]["path"], SHARED)
        self.assertEqual(changes["files"][0]["spans"], [{"lane": "lane-1", "start": held, "end": held + 9}])
        self.assertEqual(changes["files"][0]["lines"], len(SHARED_LINES))
        self.assertEqual({f["path"]: f["lanes"] for f in changes["files"][1:]},
                         {"docs/guide.md": ["lane-1"], "src/one.py": ["lane-1"], "src/two.js": ["lane-2"]})
        with self.subTest("a deletion, which no span can hold"):
            (checkout / SHARED).unlink()
            d.push(checkout, {})
            changes, items = d.one_pass()
            self.assertEqual(changes["lanes"]["lane-1"]["files"][0]["holds"], "none")
            self.assertEqual(self.texts(items), [f"Lane 1 changed `{SHARED}`, which it doesn't hold."])

    def test_a_lane_with_no_span_on_a_file_other_lanes_share_by_span_doesnt_hold_it(self) -> None:
        d = self.delivery()
        d.state["laneRegions"] = [{"lane": "lane-2", "path": SHARED, "baseHead": d.base, "start": 1, "end": 3}]
        checkout = d.story("S-1", "lane-1")
        d.push(checkout, {SHARED: "".join(SHARED_LINES[:-1])})
        _, items = d.one_pass()
        self.assertEqual(self.texts(items), [f"Lane 1 changed `{SHARED}`, which it doesn't hold."])

    def test_a_binary_or_non_utf8_file_on_the_branch_leaves_it_readable(self) -> None:
        d = self.delivery()
        checkout = d.story("S-1", "lane-1")
        (checkout / "docs").mkdir(exist_ok=True)
        (checkout / "docs/logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\xff")
        (checkout / "docs/latin.md").write_bytes("# Caf\xe9\n".encode("latin-1"))
        d.push(checkout, {"src/two.js": "function two() {\n  return 22;\n}\n"})
        changes, items = d.one_pass()
        entry = changes["lanes"]["lane-1"]
        self.assertNotIn("unreadable", entry)
        self.assertEqual({f["path"]: f["holds"] for f in entry["files"]},
                         {"docs/latin.md": "file", "docs/logo.png": "file", "src/two.js": "none"})
        self.assertEqual(self.texts(items), ["Lane 1 changed `src/two.js`, which it doesn't hold."])

    def test_lines_outside_a_span_are_named_at_the_merge_base_after_an_insertion_inside_it(self) -> None:
        d = self.delivery()
        held = SHARED_LINES.index("def held():\n") + 1
        d.state["laneRegions"] = [{"lane": "lane-1", "path": SHARED, "baseHead": d.base, "start": held, "end": held + 9}]
        checkout = d.story("S-1", "lane-1")
        lines = list(SHARED_LINES)
        after = held + 12  # a line of after_0, outside the span
        lines[after - 1] = "    return 1000\n"
        lines[held:held] = [f"    inserted_{i} = {i}\n" for i in range(5)]
        d.push(checkout, {SHARED: "".join(lines)})
        _, items = d.one_pass()
        self.assertEqual(self.texts(items), [f"Lane 1 changed line {after} of `{SHARED}`, outside its span."])

    def test_after_the_base_moves_spans_are_mapped_and_a_claim_that_no_longer_maps_holds_nothing(self) -> None:
        d = self.delivery()
        held = SHARED_LINES.index("def held():\n") + 1
        d.state["laneRegions"] = [{"lane": "lane-1", "path": SHARED, "baseHead": d.base, "start": held, "end": held + 9},
                                  {"lane": "lane-2", "path": SHARED, "baseHead": d.base, "start": 1, "end": 3}]
        lines = list(SHARED_LINES)
        lines[1] = "    return -1\n"  # inside lane 2's span: it no longer maps
        lines[4:4] = ["# above\n", "# above\n"]  # above lane 1's span: it moves down two lines
        d.land({SHARED: "".join(lines)})
        checkout = d.story("S-2", "lane-2")
        lines[2] = "    # lane 2 was here\n"
        d.push(checkout, {SHARED: "".join(lines)})
        changes, items = d.one_pass()
        self.assertEqual(changes["files"][0]["spans"], [
            {"lane": "lane-2", "start": 1, "end": 3, "regrant": True},
            {"lane": "lane-1", "start": held + 2, "end": held + 11}])
        self.assertEqual(changes["lanes"]["lane-2"]["files"][0]["holds"], "none")
        self.assertEqual(self.texts(items), [f"Lane 2 changed `{SHARED}`, which it doesn't hold."])

    def test_an_insertion_at_a_spans_edge_and_a_mode_change_are_outside_it(self) -> None:
        d = self.delivery()
        held = SHARED_LINES.index("def held():\n") + 1
        d.state["laneRegions"] = [{"lane": "lane-1", "path": SHARED, "baseHead": d.base, "start": held, "end": held + 9}]
        checkout = d.story("S-1", "lane-1")
        lines = list(SHARED_LINES)
        lines[held - 1:held - 1] = ["# at the edge\n"]
        d.push(checkout, {SHARED: "".join(lines)})
        # No other lane claims the line above the span: the insertion at its edge is the span's.
        changes, items = d.one_pass()
        self.assertEqual(items, [])
        self.assertEqual(changes["lanes"]["lane-1"]["files"][0]["holds"], "span")
        # Another lane claims the line above it: the insertion is outside, as acceptance refuses it.
        d.state["laneRegions"].append({"lane": "lane-2", "path": SHARED, "baseHead": d.base, "start": held - 4,
                                       "end": held})
        changes, items = d.one_pass()
        self.assertEqual(self.texts(items), [f"Lane 1 added 1 line before line {held} of `{SHARED}`, outside its span."])
        self.assertEqual(changes["lanes"]["lane-1"]["files"][0]["holds"], "span")
        d.state["laneRegions"].pop()
        # A mode change with a hunk outside the span beside it: the mode is what no span holds.
        git(checkout, "checkout", "-q", "origin/main", "--", SHARED)
        (checkout / SHARED).chmod(0o755)
        lines = list(SHARED_LINES)
        lines[1] = "    return 100\n"
        d.push(checkout, {SHARED: "".join(lines)})
        changes, items = d.one_pass()
        self.assertEqual(changes["lanes"]["lane-1"]["files"][0]["holds"], "none")
        self.assertEqual(self.texts(items), [f"Lane 1 changed `{SHARED}`, which it doesn't hold."])
        with self.subTest("a holdings check that stops is written as an unreadable branch"):
            with mock.patch.object(run_delivery, "candidate_ownership", side_effect=run_delivery.Stop("git diff failed")):
                changes, items = d.one_pass()
            self.assertEqual(changes["lanes"]["lane-1"]["unreadable"], "git diff failed")
            self.assertEqual([item["kind"] for item in items], ["laneUnreadable"])
        with self.subTest("a lane no longer in the record raises nothing"):
            d.state["lanes"] = [lane for lane in d.state["lanes"] if lane["id"] != "lane-1"]
            self.assertEqual(run_delivery.lane_change_needs(d.config(), d.state), [])

    def test_a_declared_shared_output_raises_nothing(self) -> None:
        d = self.delivery()
        checkout = d.story("S-1", "lane-1")
        d.push(checkout, {"gen/listing.json": "{\"a\": 1}\n"})
        changes, items = d.one_pass(d.config(shared=("gen/listing.json",)))
        self.assertEqual(items, [])
        self.assertEqual([(f["path"], f["holds"]) for f in changes["lanes"]["lane-1"]["files"]],
                         [("gen/listing.json", "shared")])
        _, items = d.one_pass(d.config())
        self.assertEqual(self.texts(items), ["Lane 1 changed `gen/listing.json`, which it doesn't hold."])

    def test_a_story_with_no_pushed_branch_has_no_entry(self) -> None:
        d = self.delivery()
        d.state["planned"].append({"subject": "S-2", "lane": "lane-2", "stage": "building"})
        checkout = d.story("S-1", "lane-1")
        d.push(checkout, {"src/one.py": "def one():\n    return 11\n"})
        changes, _ = d.one_pass()
        self.assertEqual(set(changes["lanes"]), {"lane-1"})
        self.assertNotIn("files", changes)  # no region claims, so no files view

    def test_enclosing_names_by_language(self) -> None:
        name = run_delivery.enclosing_name
        python = ["class Outer:\n", "    def inner(self):\n", "        def nested():\n", "            pass\n",
                  "        return 1\n", "\n", "def top():\n", "    x = 1\n"]
        self.assertEqual(name("a.py", python, 4), "nested")
        self.assertEqual(name("a.py", python, 5), "inner")  # past the nested function, in the method
        self.assertEqual(name("a.py", python, 8), "top")
        self.assertEqual(name("a.py", python, 1), "Outer")
        script = ["export const LIMIT = 4;\n", "function draw(x) {\n", "  return x;\n", "}\n",
                  "const helper = () => {\n", "  return 1;\n", "};\n"]
        self.assertEqual(name("a.js", script, 3), "draw")
        self.assertEqual(name("a.js", script, 6), "helper")
        self.assertEqual(name("a.mjs", script, 1), "LIMIT")
        markdown = ["# Guide\n", "\n", "## Setup ##\n", "\n", "Run it.\n"]
        self.assertEqual(name("a.md", markdown, 5), "Setup")
        self.assertEqual(name("a.md", markdown, 1), "Guide")
        self.assertIsNone(name("a.txt", ["x\n"], 1))
        with self.subTest("through a branch read"):
            d = self.delivery()
            checkout = d.story("S-1", "lane-1")
            d.push(checkout, {"docs/guide.md": "# Guide\n\n## Setup\n\nRun it twice.\n"})
            changes, _ = d.one_pass()
            [entry] = changes["lanes"]["lane-1"]["files"]
            self.assertEqual(entry["changes"], [{"old": 5, "removed": 1, "start": 5, "added": 1, "name": "Setup",
                                                 "inside": True}])

    def test_a_branch_that_cant_be_read_says_so_in_place_of_its_items(self) -> None:
        d = self.delivery()
        checkout = d.story("S-1", "lane-1")
        git(checkout, "checkout", "-q", "--orphan", "unrelated")
        d.write(checkout, {"src/two.js": "unrelated\n"})
        git(checkout, "add", "-A")
        git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "unrelated")
        git(checkout, "push", "-q", "origin", "HEAD:refs/heads/claude/S-1")
        changes, items = d.one_pass()
        entry = changes["lanes"]["lane-1"]
        self.assertIn("unreadable", entry)
        self.assertNotIn("files", entry)
        self.assertEqual((entry["story"], entry["branch"]), ("S-1", "claude/S-1"))
        [item] = items
        self.assertEqual((item["kind"], item["lane"], item["holding"]), ("laneUnreadable", "lane-1", False))
        self.assertTrue(item["text"].startswith("Lane 1's branch `claude/S-1` can't be read: "), item["text"])

    def test_an_unchanged_branch_keeps_its_read_and_a_moved_one_is_read_again(self) -> None:
        d = self.delivery()
        checkout = d.story("S-1", "lane-1")
        head = d.push(checkout, {"src/one.py": "def one():\n    value = 1\n    return value\n"})
        first, _ = d.one_pass()
        entry = first["lanes"]["lane-1"]
        self.assertEqual((entry["story"], entry["branch"], entry["head"]), ("S-1", "claude/S-1", head))
        self.assertEqual(entry["files"], [{"path": "src/one.py", "holds": "file", "changes": [
            {"old": 2, "removed": 1, "start": 2, "added": 2, "name": "one", "inside": True}]}])
        with mock.patch.object(run_delivery, "read_lane_branch", side_effect=AssertionError("read again")):
            again, _ = d.one_pass()
        self.assertEqual(again["lanes"]["lane-1"], entry)
        d.push(checkout, {"src/one.py": "def one():\n    return 2\n"})
        moved, _ = d.one_pass()
        self.assertNotEqual(moved["lanes"]["lane-1"]["readAt"], entry["readAt"])
        with self.subTest("the lane moves to another story while the first one's branch stands still"):
            d.state["planned"][0]["stage"] = "settled"
            nxt = d.story("S-3", "lane-1")
            d.push(nxt, {"docs/guide.md": "# Guide\n"})
            later, _ = d.one_pass()
            self.assertEqual((later["lanes"]["lane-1"]["story"], later["lanes"]["lane-1"]["branch"]), ("S-3", "claude/S-3"))
            self.assertEqual([f["path"] for f in later["lanes"]["lane-1"]["files"]], ["docs/guide.md"])

    def test_needs_you_says_the_branch_reads_items_in_the_tables_words_before_the_records_own(self) -> None:
        d = self.delivery()
        d.push(d.story("S-1", "lane-1"), {"src/two.js": "function two() {\n  return 3;\n}\n"})
        d.push(d.story("S-2", "lane-2"), {"src/two.js": "function two() {\n  return 4;\n}\n"})
        d.one_pass()
        # A stopped story in the queue, which the record's own items name.
        d.state["queue"].append({"order": 1, "subject": "S-9", "title": "Stop", "lane": "lane-2", "stage": "stopped",
                                 "head": "b" * 40, "syncedHead": None, "leadInputs": [], "reportedRounds": 1,
                                 "runId": None, "checks": []})
        doc = {"state": json.loads(json.dumps(d.state)), "journal": []}
        c = d.config()
        c.release, c.flow_log, c.delivery, c.forge = ROOT, Path("/nonexistent"), "APP", {}
        with mock.patch.multiple(run_delivery, tracker_conf=lambda c: {}, base_head=lambda c: None,
                                 full_health_running=lambda c: None, read_log=lambda *a, **k: [],
                                 lead_listener=lambda *a, **k: {"pid": 1}, only_full_health_breaks=lambda journal: False), \
                mock.patch.object(run_delivery.tracker_publisher, "needs_you", lambda *a, **k: []):
            items = run_delivery.needs_you(c, doc)
        kinds = [item["kind"] for item in items]
        self.assertEqual(kinds[:1], ["laneOutside"], items)
        self.assertIn("stopped", kinds)
        self.assertLess(kinds.index("laneOutside"), kinds.index("stopped"))
        [outside] = [item for item in items if item["kind"] == "laneOutside"]
        self.assertEqual((outside["lane"], outside["subject"], outside["holding"]), ("lane-1", "S-1", False))
        self.assertEqual(outside["said"]["headline"], "Lane 1 changed src/two.js outside what it holds")
        self.assertEqual(outside["said"]["why"], "It doesn't hold the file.")
        self.assertEqual(outside["said"]["clears"], "Clears when the branch no longer changes it, or the lead grants it.")
        self.assertEqual(outside["said"]["who"], "The lead")

if __name__ == "__main__":
    unittest.main()
