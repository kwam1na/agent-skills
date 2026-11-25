"""Lanes share the generated files the binding's own generators reproduce.

A binding declares shared outputs with `regenerable.shared`. A lane changes one with no claim: review-prepare,
review-reduce, handoff, lead-accept and the merge queue each verify the candidate's bytes against the base binding's
generators, run on the candidate's own commit in a temporary worktree. These cases run the binding's rules as
`binding_errors` judges them, the queue's sync on its own fixture repository, and the lane and lead commands on the
fixture delivery, whose regenerator builds `gen/graph.json` from `src/`.
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

from tests.test_merge_queue import PY, Adopter, MergeQueueTestCase, journaled, queue, run, state_tool
from tests.test_run_delivery import Fixture, commit, installed_release, setup_delivery, sh, write_results

RUN_QUIETLY = True

GRAPH = "gen/graph.json"

# The fixture delivery's generators. The regenerator needs the file only `prepare` makes, and logs where it ran and
# on which commit to the file SHARED_LOG names, outside every checkout. postSync rewrites the graph's formatting, and
# the lead input, which names no lease, logs that it ran.
TOOLS = {
    "tools/prep.py": "import pathlib\npathlib.Path('.prepared').write_text('ok\\n')\n",
    "tools/graph.py": (
        "import json, os, pathlib, subprocess, sys\n"
        "pathlib.Path('.prepared').exists() or sys.exit('not prepared')\n"
        "head = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()\n"
        "with open(os.environ['SHARED_LOG'], 'a') as log:\n"
        "    log.write(json.dumps({'ran': 'graph', 'cwd': os.getcwd(), 'head': head}) + '\\n')\n"
        "nodes = {p.name: p.read_text() for p in sorted(pathlib.Path('src').glob('*.txt'))}\n"
        "pathlib.Path('gen').mkdir(exist_ok=True)\n"
        "pathlib.Path('gen/graph.json').write_text(json.dumps({'nodes': nodes}))\n"),
    "tools/format.py": (
        "import json, pathlib\n"
        "path = pathlib.Path('gen/graph.json')\n"
        "path.write_text(json.dumps(json.loads(path.read_text()), indent=2) + '\\n')\n"),
    "tools/input.py": (
        "import json, os\n"
        "with open(os.environ['SHARED_LOG'], 'a') as log:\n"
        "    log.write(json.dumps({'ran': 'input', 'cwd': os.getcwd()}) + '\\n')\n"),
}


def graph(checkout: Path) -> str:
    """The graph the generators make of ``checkout``'s sources, formatted as postSync leaves it."""
    nodes = {path.name: path.read_text() for path in sorted((checkout / "src").glob("*.txt"))}
    return json.dumps({"nodes": nodes}, indent=2) + "\n"


class SharedBindingRuleTests(unittest.TestCase):
    """Decision 6: binding_errors alone says what `shared` may name, each refusal in its own words."""

    def binding(self, **regenerable) -> dict:
        made = Adopter.default_binding(types.SimpleNamespace(work=Path("/w"), state=Path("/s"), remote=Path("/r"),
                                                             log=Path("/l")))
        made["leadInputs"] = []
        made["regenerable"] = {"paths": ["gen/**"], "regenerators": [[PY, "tools/regen.py"]], **regenerable}
        return made

    def test_each_rule_refuses_with_its_own_message_and_a_contained_pattern_passes(self) -> None:
        leased = self.binding(shared=["gen/*.json"])
        leased["leadInputs"] = [{"id": "api", "argv": [PY, "tools/leadinput.py"], "paths": ["gen/**"],
                                 "lease": "backend"}]
        cases = {
            "no regenerator": (self.binding(regenerators=[], shared=["gen/*.json"]),
                               "/regenerable/shared: a shared output needs a regenerator to reproduce it"),
            "a leased input": (leased, "/regenerable/shared: lead input api names a lease"),
            "no regenerable path": (self.binding(shared=["docs/*.json"]),
                                    "/regenerable/shared/0: docs/*.json matches no regenerable path"),
            "outside the paths": (self.binding(shared=["**/*.json"]),
                                  "/regenerable/shared/0: **/*.json can match a path outside regenerable.paths"),
            "the binding file": (self.binding(paths=["gen/**", ".agents/orchestration.json"],
                                              shared=[".agents/orchestration.json"]),
                                 "/regenerable/shared/0: .agents/orchestration.json matches the binding file"),
            "a generator": (self.binding(regenerators=[[PY, "gen/build.py"]], shared=["gen/build.py"]),
                            "/regenerable/shared/0: gen/build.py matches gen/build.py, which a regenerator runs"),
        }
        for name, (binding, said) in cases.items():
            with self.subTest(case=name):
                errors = queue.binding_errors(binding)
                self.assertEqual(len(errors), 1, errors)
                self.assertTrue(errors[0].startswith(said), errors)
        self.assertEqual(queue.binding_errors(self.binding(shared=["gen/*.json"])), [])
        self.assertEqual(queue.binding_errors(self.binding()), [])

    def test_containment_is_judged_by_the_binding_s_own_pattern_rule(self) -> None:
        for pattern, within in (("gen/*.json", True), ("gen/a/**/x.json", True), ("gen/graph.json", True),
                                ("**/*.json", False), ("g?n/*", False), ("*", False), ("**", False)):
            with self.subTest(pattern=pattern):
                self.assertEqual(queue.pattern_within(pattern, ["gen/**"]), within)
        self.assertFalse(queue.pattern_meets("docs/*", ["gen/**"]))
        self.assertTrue(queue.pattern_meets("**/*.json", ["gen/**"]))


class QueueSharedOutputTests(MergeQueueTestCase):
    """Decision 12, on the queue's fixture repository: its regenerator builds gen/generated.txt from src/, shared."""

    run_quietly = True

    def shared(self, **regenerable) -> Adopter:
        return self.adopter({"leadInputs": [], "regenerable": {
            "paths": ["gen/**"], "regenerators": [[PY, "tools/regen.py"]], "shared": ["gen/*.txt"], **regenerable}})

    def worktrees(self, adopter: Adopter) -> str:
        return run("worktree", "list", "--porcelain", cwd=adopter.operator)

    def test_two_lanes_changing_the_shared_output_both_merge_and_a_hand_edit_stops(self) -> None:
        adopter = self.shared()
        before = self.worktrees(adopter)
        # Two unrelated sources, each regenerating the same output on its own tree.
        adopter.enqueue("one", adopter.lane_commit("one", {"src/app.txt": "app two\n",
                                                          "gen/generated.txt": "app two\nlib one\n"}))
        adopter.enqueue("two", adopter.lane_commit("two", {"src/lib.txt": "lib two\n",
                                                          "gen/generated.txt": "app one\nlib two\n"}))
        results = self.run_queue(adopter, "one", "two")
        self.assertEqual([result.document()["outcome"] for result in results],
                         ["synced", "checked", "merged"] * 2, [result.document().get("stop") for result in results])
        # The second synced on the moved base: verified on its own tree, then regenerated after the merge.
        self.assertEqual(adopter.show("origin/main", "gen/generated.txt"), "app two\nlib two")
        self.assertEqual(self.worktrees(adopter), before)
        # A hand edit the generator doesn't produce stops before the base comes in, and nothing is pushed.
        tip = adopter.tip("main")
        adopter.enqueue("edit", adopter.lane_commit("edit", {"src/app.txt": "app three\n",
                                                            "gen/generated.txt": "hand edited\n"}))
        stopped = self.assertStopped(self.run_queue(adopter, "edit"), "shared_output_irreproducible")
        self.assertIn("gen/generated.txt (regeneration writes other bytes)", stopped["stop"]["detail"])
        self.assertEqual(stopped["stop"]["paths"], ["gen/generated.txt"])
        self.assertEqual(adopter.tip("main"), tip)
        self.assertEqual(self.worktrees(adopter), before)
        # The stop's journal entry is one the state tool's journal validation accepted.
        [entry] = journaled(adopter, "candidate.stopped", "edit")
        self.assertEqual(entry["data"]["reason"], "shared_output_irreproducible")
        state_tool.read_journal(adopter.state)

    def test_a_failing_regenerator_stops_with_its_argv_and_exit(self) -> None:
        adopter = self.shared(regenerators=[[PY, "-c", "import sys; sys.stderr.write('graph broke'); sys.exit(3)"]])
        before = self.worktrees(adopter)
        adopter.enqueue("one", adopter.lane_commit("one", {"src/app.txt": "app two\n",
                                                          "gen/generated.txt": "app two\nlib one\n"}))
        stopped = self.assertStopped(self.run_queue(adopter, "one"), "shared_output_generator_failed")
        self.assertIn("regenerator", stopped["stop"]["detail"])
        self.assertIn("exited 3: graph broke", stopped["stop"]["detail"])
        self.assertEqual(self.worktrees(adopter), before)

    def test_a_shared_listing_still_needs_its_owner_for_an_added_output_and_a_digest_refresh_merges(self) -> None:
        # The listing lists one output per line of the generated file: a new source adds one, an edited one doesn't.
        listing = [PY, "-c", "import json, sys; print(json.dumps([f'line{i}' for i, _ in enumerate(sys.stdin)]))"]
        adopter = self.shared(listings=[{"path": "gen/generated.txt", "argv": listing}])
        state = state_tool.read_state(adopter.state)
        state_tool.patch(adopter.state, writer="lead", version=state["version"],
                         changes={"laneOwnership": {"lane-1": ["src/**"], "lane-2": ["gen/**"]}})
        adopter.enqueue("added", adopter.lane_commit("added", {
            "src/new.txt": "new one\n", "gen/generated.txt": "app one\nlib one\nnew one\n"}))
        stopped = self.assertStopped(self.run_queue(adopter, "added"), "source_conflict")
        self.assertIn("the candidate changed the outputs gen/generated.txt lists (added line2)", stopped["stop"]["detail"])
        adopter.enqueue("refresh", adopter.lane_commit("refresh", {"src/app.txt": "app two\n",
                                                                  "gen/generated.txt": "app two\nlib one\n"}))
        self.assertEqual(self.run_queue(adopter, "refresh")[-1].document()["outcome"], "merged")


class LaneSharedOutputTests(unittest.TestCase):
    """Decisions 7 to 11 through the lane and lead commands, on the fixture delivery."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def delivery(self, shared: bool = True) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        m = fx.main
        path = setup_delivery(fx)
        d = json.loads(path.read_text())
        for key in ("FX-2", "FX-3"):
            d["stories"][key]["inApp"] = "none"
        path.write_text(json.dumps(d, indent=2))
        for relative, text in TOOLS.items():
            (m / relative).parent.mkdir(parents=True, exist_ok=True)
            (m / relative).write_text(text)
        with (m / ".gitignore").open("a") as ignored:
            ignored.write(".prepared\n")
        for name in ("one", "two"):
            (m / f"src/{name}.txt").parent.mkdir(exist_ok=True)
            (m / f"src/{name}.txt").write_text(f"{name}\n")
        (m / "gen").mkdir(exist_ok=True)
        (m / GRAPH).write_text(graph(m))
        self.log = fx.tmp / "shared.log"
        self.log.write_text("")
        self.bind(fx, shared=shared, message="FX-1: a shared generated graph")
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        return fx

    def binding(self, shared: bool = True, **changes) -> dict:
        made = {"base": {"remote": "origin", "ref": "feature/FX-1"}, "checkTimeoutSeconds": 600,
                "state": {"forgeSnapshot": {"argv": ["true"]}}, "environment": {"SHARED_LOG": str(self.log)},
                "prepare": [[sys.executable, "tools/prep.py"]],
                "regenerable": {"paths": ["gen/**"], "regenerators": [[sys.executable, "tools/graph.py"]],
                                **({"shared": ["gen/*.json"]} if shared else {})},
                "postSync": [{"argv": [sys.executable, "tools/format.py"], "paths": ["gen/**"]}],
                "leadInputs": [{"id": "inputs", "argv": [sys.executable, "tools/input.py"], "paths": ["gen/**"]}]}
        for key, value in changes.items():
            if key == "regenerators":
                made["regenerable"]["regenerators"] = value
            else:
                made[key] = value
        return made

    def bind(self, fx: Fixture, message: str = "FX-1: the binding changes", **settings) -> None:
        """The lead commits and pushes a base binding, so every lane reads it from the remote base."""
        (fx.main / ".agents/orchestration.json").write_text(json.dumps(self.binding(**settings), indent=2))
        commit(fx.main, message)
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)

    def flow(self, fx: Fixture, *argv, cwd: Path | None = None, ok: bool = True) -> str:
        code, out = fx.flow(*argv, cwd=cwd or fx.main)
        (self.assertEqual if ok else self.assertNotEqual)(code, 0, out)
        return out

    def runs(self, head: str, ran: str = "graph") -> list[dict]:
        return [entry for entry in map(json.loads, self.log.read_text().splitlines())
                if entry["ran"] == ran and entry.get("head", head) == head]

    def worktrees(self, fx: Fixture) -> list[str]:
        listed = sh("git", "worktree", "list", "--porcelain", cwd=fx.main).stdout.splitlines()
        return [line for line in listed if line.startswith("worktree ")]

    def change(self, fx: Fixture, key: str, wt: Path, files: dict[str, str | None], message: str) -> str:
        for relative, text in files.items():
            if text is None:
                (wt / relative).unlink()
            else:
                (wt / relative).parent.mkdir(parents=True, exist_ok=True)
                (wt / relative).write_text(text)
        commit(wt, f"{key}: {message}")
        return sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()

    def started(self, fx: Fixture, key: str, lane: str) -> Path:
        self.flow(fx, "lead-assign", key, "--lane", lane, "--title", lane)
        wt = fx.lane(key)
        (wt / f".orchestration/review/{key}").mkdir(parents=True, exist_ok=True)
        (wt / f".orchestration/review/{key}/context.md").write_text("- evidence")
        return wt

    def reviewed(self, fx: Fixture, key: str, wt: Path, n: int) -> None:
        """Gates passed, one aligned round at HEAD, and the branch pushed."""
        self.flow(fx, "gate", key, cwd=wt)
        self.flow(fx, "review-prepare", key, "--round", n, cwd=wt)
        write_results(wt / f".orchestration/review/{key}", n, key, fx.lenses(wt))
        self.flow(fx, "review-reduce", key, "--round", n, cwd=wt)
        sh("git", "push", "-q", "-f", "origin", f"HEAD:feature/{key}", cwd=wt)

    def opened(self, fx: Fixture, key: str, wt: Path) -> str:
        body = fx.tmp / f"{key}.md"
        body.write_text("x\n")
        return self.flow(fx, "open-pr", key, "--title", f"{key}: change", "--body-file", body, cwd=wt).strip().splitlines()[-1]

    def record(self, fx: Fixture) -> tuple[bytes, bytes]:
        return (fx.tmp / "record/journal.jsonl").read_bytes(), (fx.tmp / "record/state.json").read_bytes()

    def test_two_lanes_hand_off_the_same_graph_with_no_claim_verified_once_a_head_and_the_lead_accepts(self) -> None:
        fx = self.delivery()
        heads, urls, trees = {}, {}, {}
        for key, lane, name in (("FX-2", "lane-1", "one"), ("FX-3", "lane-2", "two")):
            wt = trees[key] = self.started(fx, key, lane)
            (wt / f"src/{name}.txt").write_text(f"{name} changed\n")
            heads[key] = self.change(fx, key, wt, {GRAPH: graph(wt)}, "its source and the graph")
            self.reviewed(fx, key, wt, 1)
            urls[key] = self.opened(fx, key, wt)
            # review-prepare verified it once; review-reduce reused that, at the same head and base binding.
            self.assertEqual(len(self.runs(heads[key])), 1, self.log.read_text())
        before = self.worktrees(fx)  # the lead's checkout and the two lanes'
        owns = json.loads((fx.tmp / "record/state.json").read_text())["laneOwnership"]
        self.assertFalse([pattern for patterns in owns.values() for pattern in patterns if pattern.startswith("gen")])
        self.flow(fx, "handoff", "FX-2", "--pr", urls["FX-2"], cwd=trees["FX-2"])
        self.assertEqual(len(self.runs(heads["FX-2"])), 1)
        # Every run was on the candidate's commit, in a folder that is neither the lane's checkout nor still there.
        for entry in self.runs(heads["FX-2"]) + self.runs(heads["FX-3"]):
            self.assertNotIn(str(trees["FX-2"]), entry["cwd"])
            self.assertNotIn(str(trees["FX-3"]), entry["cwd"])
            self.assertFalse(Path(entry["cwd"]).exists(), entry)
        self.assertTrue(self.runs("", "input"), "the lead input that names no lease ran in the verification")
        self.assertEqual(self.worktrees(fx), before)
        # A generator that can't run stops the hand-off with its argv and exit, and leaves no worktree.
        python = sys.executable
        for settings, said in (
                ({"prepare": [[python, "-c", "import sys; sys.exit(7)"]]}, f"prepare {python} -c import sys; sys.exit(7) exited 7"),
                ({"regenerators": [[python, "-c", "import sys; sys.stderr.write('graph broke'); sys.exit(3)"]]},
                 "exited 3: graph broke"),
                ({"regenerators": [[python, "-c", "import time; time.sleep(5)"]], "checkTimeoutSeconds": 1},
                 "ran past its 1-second timeout")):
            with self.subTest(settings=settings):
                self.bind(fx, **settings)
                out = self.flow(fx, "handoff", "FX-3", "--pr", urls["FX-3"], cwd=trees["FX-3"], ok=False)
                self.assertIn("shared_output_generator_failed: ", out)
                self.assertIn(said, out)
                self.assertEqual(self.worktrees(fx), before)
        # A base binding change since the review, to a member the verification runs, runs it again, once.
        self.bind(fx, leadInputs=[{"id": "inputs", "argv": [python, "tools/input.py"], "paths": ["gen/**"],
                                   "timeoutSeconds": 60}])
        self.flow(fx, "handoff", "FX-3", "--pr", urls["FX-3"], cwd=trees["FX-3"])
        self.assertEqual(len(self.runs(heads["FX-3"])), 2)
        # The lead's working tree drops `shared`, but the base declares it: the base's binding decides.
        binding = fx.main / ".agents/orchestration.json"
        declared = binding.read_text()
        binding.write_text(json.dumps(self.binding(shared=False), indent=2))
        try:
            self.assertIn("accepted FX-2", self.flow(fx, "lead-accept", "FX-2"))
        finally:
            binding.write_text(declared)
        # A newer head with a hand edit, pushed after the hand-off: verified again, refused, and nothing accepted.
        self.change(fx, "FX-3", trees["FX-3"], {GRAPH: "{}\n"}, "a hand edit")
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-3", cwd=trees["FX-3"])
        written = self.record(fx)
        out = self.flow(fx, "lead-accept", "FX-3", ok=False)
        self.assertIn(f"shared_output_irreproducible: the binding's generators, run on the candidate's own tree, don't "
                      f"reproduce {GRAPH} (regeneration writes other bytes)", out)
        self.assertEqual(self.record(fx), written)
        out = self.flow(fx, "lead-accept", "FX-3", "--take", GRAPH, "--why", "a pin", ok=False)
        self.assertIn(f"{GRAPH} is a declared shared output, which no lane takes", out)
        self.assertEqual(self.record(fx), written)

    def test_an_irreproducible_output_or_a_self_declared_one_is_refused_and_the_base_decides_at_acceptance(self) -> None:
        fx = self.delivery(shared=False)
        own = lambda *argv: self.flow(fx, "lead-own", *argv)
        # A lane that holds the binding file declares the graph shared in its own binding: it gains nothing.
        wt = self.started(fx, "FX-3", "lane-2")
        own("--grant", ".agents/orchestration.json", "--lane", "lane-2")
        own("--grant", GRAPH, "--lane", "lane-2")
        (wt / "src/two.txt").write_text("two changed\n")
        self.change(fx, "FX-3", wt, {GRAPH: graph(wt), "lib/extra3.txt": "extra\n",
                                     ".agents/orchestration.json": json.dumps(self.binding(), indent=2)},
                    "its source, the graph and a binding that declares it shared")
        own("--grant", "lib/extra3.txt", "--lane", "lane-2")
        self.reviewed(fx, "FX-3", wt, 1)
        url = self.opened(fx, "FX-3", wt)
        own("--revoke", GRAPH, "--lane", "lane-2")
        out = self.flow(fx, "handoff", "FX-3", "--pr", url, cwd=wt, ok=False)
        self.assertIn(f"writer_not_owned: FX-3 changes files outside lane-2's: ['{GRAPH}']", out)
        own("--grant", GRAPH, "--lane", "lane-2")
        self.flow(fx, "handoff", "FX-3", "--pr", url, cwd=wt)
        # The lead's working tree declares it shared, the base's doesn't: acceptance judges by the base.
        own("--revoke", GRAPH, "--lane", "lane-2")
        (fx.main / ".agents/orchestration.json").write_text(json.dumps(self.binding(), indent=2))
        try:
            out = self.flow(fx, "lead-accept", "FX-3", ok=False)
        finally:
            sh("git", "checkout", "--", ".agents/orchestration.json", cwd=fx.main)
        self.assertIn(f"the branch changes files outside lane-2's: ['{GRAPH}']", out)
        # Declared on the base now, the graph is verified, and the one file left outside the lane is named alone.
        self.bind(fx)
        own("--revoke", "lib/extra3.txt", "--lane", "lane-2")
        out = self.flow(fx, "lead-accept", "FX-3", ok=False)
        self.assertIn("the branch changes files outside lane-2's: ['lib/extra3.txt']", out)
        # Each irreproducible candidate, reviewed while the base declared nothing shared, stops its hand-off once it does.
        wt = self.started(fx, "FX-2", "lane-1")
        own("--grant", "gen/*", "--lane", "lane-1")
        before = self.worktrees(fx)
        # A hand edit stops review-prepare, before any round is prepared.
        self.change(fx, "FX-2", wt, {GRAPH: "{}\n"}, "a hand edit")
        out = self.flow(fx, "review-prepare", "FX-2", "--round", 1, cwd=wt, ok=False)
        self.assertIn(f"shared_output_irreproducible: the binding's generators, run on the candidate's own tree, don't "
                      f"reproduce {GRAPH} (regeneration writes other bytes)", out)
        self.assertFalse((wt / ".orchestration/review/FX-2/round-1.meta.json").exists())
        self.assertEqual(self.worktrees(fx), before)
        (wt / "src/one.txt").write_text("one changed\n")
        cases = (
            ("a hand-written output the generator never writes", {GRAPH: graph(wt), "gen/notes.json": "{}\n"},
             "gen/notes.json (regeneration does not write it)", None),
            ("a deleted output", {GRAPH: None, "gen/notes.json": None},
             f"{GRAPH} (the candidate deletes it, and regeneration writes it)", None),
            ("an output matching a source edit left uncommitted",
             {GRAPH: json.dumps({"nodes": {"one.txt": "one three\n", "two.txt": "two\n"}}, indent=2) + "\n"},
             f"{GRAPH} (regeneration writes other bytes)", "one three\n"))
        for n, (name, files, said, uncommitted) in enumerate(cases, start=1):
            with self.subTest(case=name):
                self.bind(fx, shared=False)
                self.change(fx, "FX-2", wt, files, name)
                self.reviewed(fx, "FX-2", wt, n)
                url = url if n > 1 else self.opened(fx, "FX-2", wt)
                if uncommitted:
                    (wt / "src/one.txt").write_text(uncommitted)
                self.bind(fx)
                out = self.flow(fx, "handoff", "FX-2", "--pr", url, cwd=wt, ok=False)
                self.assertIn("shared_output_irreproducible: ", out)
                self.assertIn(said, out)
                self.assertEqual(self.worktrees(fx), before)


if __name__ == "__main__":
    unittest.main()
