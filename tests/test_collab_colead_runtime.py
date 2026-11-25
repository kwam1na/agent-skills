"""The co-lead runtime on a shared record: which machine runs what, the lead commands' scopes, the handovers of
delivery and of a lane, the tracker and hosted publishers across seats, and the sync loop's timing.

The tables are the skill page's "Which machine runs what": every supervisor pass step has a row, and so does every
machine-local name the lead's commands and the tracker publisher read, and a step or name planted without one turns
each table's test red. The shared cases use the partitions tests' fixture, two clones of one bare remote; each machine
keeps its own record, tracker ledger and hosted sidecars, and the two share only a fake tracker and a fake hosted
origin, in this process. A peer is observed by reading its files, never by running a command there that could ingest.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import test_collab_partitions as partitions
from tests import test_collab_colead as colead
from tests.test_collab_colead import lane_row, planned
from tests.test_delivery_views_hosted import CFG as VIEWS, FakeAPI, VALID, hosted, views_common

state_tool, collab, KEY, REMOTE = partitions.state_tool, partitions.collab, partitions.KEY, partitions.REMOTE
ROOT = partitions.PRODUCT_ROOT
RUN_DELIVERY = ROOT / "skills/run-delivery/scripts/run_delivery.py"
PUBLISHER = ROOT / "skills/run-delivery/scripts/tracker_publisher.py"
HOSTED = ROOT / "skills/delivery-views/scripts/hosted.py"
SKILL_PAGE = ROOT / "skills/orchestrate-work/SKILL.md"
HEAD = "4e66a0571b2c3d4e5f60718293a4b5c6d7e8f901"


def load(path: Path, name: str):
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


rd = load(RUN_DELIVERY, "run_delivery_for_colead_runtime")
tp = rd.tracker_publisher


# -- the tables ------------------------------------------------------------------------------------------------------

def page_table(heading: str) -> list[tuple[list[str], str]]:
    """The skill page's table under "Which machine runs what" whose header starts `heading`: each row's names, in
    backticks, and its rule."""
    text = SKILL_PAGE.read_text(encoding="utf-8")
    section = text.split("### Which machine runs what", 1)[1]
    lines = section.splitlines()
    start = next(number for number, line in enumerate(lines) if line.startswith(f"| {heading} |"))
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        first, rule = line.strip("|").split("|", 1)
        rows.append((re.findall(r"`([^`]+)`", first), rule.strip()))
    return rows


RULE_WORDS = {"delivery": "holds `delivery`", "queue": "holds `queue`", "lane": "the lanes its seat holds"}


def table_steps() -> dict[str, str]:
    """The step table's rule for each step, in SHARED_STEPS's words."""
    rules = {}
    for names, words in page_table("Step"):
        rule = next((rule for rule, said in RULE_WORDS.items() if said in words), "every")
        rules.update({name: rule for name in names})
    return rules


def pass_steps(source: str) -> list[str]:
    """The steps `Supervisor.one_pass` runs, as its source names them: each of its `steps` tuple's names, and each pass
    entry the close-out after them sets from a call of its own."""
    tree = ast.parse(source)
    supervisor = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Supervisor")
    method = next(node for node in supervisor.body if isinstance(node, ast.FunctionDef) and node.name == "one_pass")
    names: list[str] = []

    def own(node: ast.AST):
        # The method's own statements: a step defined inside it is reached through the steps tuple.
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.Lambda)):
                yield child
                yield from own(child)

    for node in own(method):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "steps"
                                                for target in node.targets) and isinstance(node.value, ast.Tuple):
            names += [item.elts[0].value for item in node.value.elts
                      if isinstance(item, ast.Tuple) and isinstance(item.elts[0], ast.Constant)]
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                and node.value.func.id != "now":
            targets = [element for target in node.targets
                       for element in (target.elts if isinstance(target, ast.Tuple) else [target])]
            names += [target.slice.value for target in targets if isinstance(target, ast.Subscript)
                      and isinstance(target.value, ast.Name) and target.value.id == "entry"
                      and isinstance(target.slice, ast.Constant)]
    return list(dict.fromkeys(names))


# Each reader of a machine-local name, and which of its arguments names it.
READERS = {"read_log": 0, "append_log": 0, "flow_log": 0, "plan_flow_log": 1, "lead_file": 1, "read_flow": 2,
           "append_flow": 2}
# What the readers themselves are built from: a reader's body passes its own parameter on.
BUILDERS = set(READERS) | {"flow_file"}
# The modules whose attributes name files: their module-level string constants.
NAMING_MODULES = {"tracker_publisher": PUBLISHER, "hosted": HOSTED,
                  "delivery_state": ROOT / "skills/orchestrate-work/scripts/delivery_state.py"}


def string_constants(tree: ast.Module) -> dict[str, str]:
    """A module's string constants by name, one assignment or a tuple of them."""
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            pairs = zip(target.elts, node.value.elts) if isinstance(target, ast.Tuple) and isinstance(
                node.value, ast.Tuple) else [(target, node.value)]
            for name, value in pairs:
                if isinstance(name, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found[name.id] = value.value
    return found


def machine_names(sources: dict[str, str], others: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Every machine-local name the sources read, each with where: a name passed to a reader, a module constant passed
    to one, an inline path from `c.flow_log`, `c.state` or `c.state.parent` with a literal, f-string or constant name,
    and another module's attribute resolved to its value. An f-string's parts are kept as `{...}` patterns, and a flow
    log's path is named by its log, without the delivery key before it or `.jsonl` after it."""
    others = others if others is not None else {name: path.read_text(encoding="utf-8")
                                                for name, path in NAMING_MODULES.items()}
    attributes = {}
    for text in others.values():
        attributes.update(string_constants(ast.parse(text)))
    found: dict[str, list[str]] = {}
    for where, source in sources.items():
        tree = ast.parse(source)
        own = string_constants(tree)
        loops: dict[int, dict[str, list[ast.AST]]] = {}

        def resolve(node: ast.AST, scope: dict[str, list[ast.AST]]) -> list[str]:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return [node.value]
            if isinstance(node, ast.JoinedStr):
                # A part naming a constant is its value; any other part is a pattern's placeholder.
                return ["".join(part.value if isinstance(part, ast.Constant) else (resolve(part.value, scope) or [
                    "{" + ast.unparse(part.value) + "}"])[0] for part in node.values)]
            if isinstance(node, ast.Name) and node.id in scope:
                return [name for value in scope[node.id] for name in resolve(value, {})]
            if isinstance(node, ast.Name) and node.id in own:
                return [own[node.id]]
            if isinstance(node, ast.Attribute) and node.attr in attributes:
                return [attributes[node.attr]]
            return []

        def visit(node: ast.AST, scope: dict[str, list[ast.AST]], inside: str | None) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inside = node.name
            if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name) \
                    and isinstance(node.iter, (ast.Tuple, ast.List)):
                scope = {**scope, node.target.id: list(node.iter.elts)}
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                for generator in node.generators:
                    visit(generator, scope, inside)
                    if isinstance(generator.target, ast.Name) and isinstance(generator.iter, (ast.Tuple, ast.List)):
                        scope = {**scope, generator.target.id: list(generator.iter.elts)}
            if isinstance(node, ast.Call) and inside not in BUILDERS:
                function = node.func.id if isinstance(node.func, ast.Name) else \
                    node.func.attr if isinstance(node.func, ast.Attribute) else None
                if function in READERS:
                    index = READERS[function]
                    argument = node.args[index] if len(node.args) > index else next(
                        (keyword.value for keyword in node.keywords if keyword.arg == "name"), None)
                    for name in resolve(argument, scope) if argument is not None else []:
                        found.setdefault(name, []).append(f"{where}:{node.lineno}")
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and inside not in BUILDERS:
                left = ast.unparse(node.left)
                if re.search(r"(^|\.)(flow_log|state|state\.parent)$", left):
                    for name in resolve(node.right, scope):
                        if left.endswith("flow_log"):
                            name = re.sub(r"\.jsonl$", "", re.sub(r"^\{[^}]*\}\.", "", name))
                        found.setdefault(name, []).append(f"{where}:{node.lineno}")
            for child in ast.iter_child_nodes(node):
                if not isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)) or \
                        not isinstance(child, ast.comprehension):
                    visit(child, scope, inside)

        visit(tree, {}, None)
    return found


def table_names() -> set[str]:
    return {name for names, _ in page_table("Input") for name in names}


def pattern(name: str) -> str:
    """A name with each placeholder as `{}`, so `supervisor.{name}.log` matches whatever names its part."""
    return re.sub(r"\{[^}]*\}", "{}", name)


def unlisted(found: dict[str, list[str]]) -> dict[str, list[str]]:
    listed = {pattern(name) for name in table_names()}
    return {name: where for name, where in found.items() if pattern(name) not in listed}


def corpus_sources() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in (RUN_DELIVERY, PUBLISHER)}


class StepTableTests(unittest.TestCase):
    def test_every_pass_step_has_the_rule_the_table_gives_it_and_the_table_names_no_other(self) -> None:
        steps = pass_steps(RUN_DELIVERY.read_text(encoding="utf-8"))
        self.assertIn("reconcile", steps)
        self.assertIn("closeOut", steps)
        table = table_steps()
        self.assertEqual(sorted(set(steps) - set(table)), [])
        self.assertEqual(sorted(set(table) - set(steps)), [])
        self.assertEqual({step: rd.SHARED_STEPS[step] for step in steps}, {step: table[step] for step in steps})

    def test_a_step_planted_in_the_pass_or_its_close_out_without_a_row_fails(self) -> None:
        source = RUN_DELIVERY.read_text(encoding="utf-8")
        planted = source.replace('("latest", lambda: str(copy_latest(c))))',
                                 '("latest", lambda: str(copy_latest(c))), ("planted", lambda: "x"))', 1)
        self.assertNotEqual(planted, source)
        self.assertEqual(sorted(set(pass_steps(planted)) - set(table_steps())), ["planted"])
        closing = source.replace('entry["closeOut"], closed = close_out(c, self.flapping(), self.once)',
                                 'entry["closeOut"], closed = close_out(c, self.flapping(), self.once)\n'
                                 '                entry["plantedClose"] = render_views(c)', 1)
        self.assertNotEqual(closing, source)
        self.assertEqual(sorted(set(pass_steps(closing)) - set(table_steps())), ["plantedClose"])


class InputTableTests(unittest.TestCase):
    def test_every_machine_local_name_the_commands_and_the_tracker_publisher_read_has_a_row(self) -> None:
        found = machine_names(corpus_sources())
        # Each kind of reader is found at all: a flow log, a lead file, an inline record file, a hosted sidecar.
        for name in ("acceptance", "supervisor.{name}.log", "state.json", "hosted-identity.json", "tracker-marks",
                     "queue.lock", "live.json"):
            self.assertIn(name, {pattern(found_name).replace("{}", "{name}") if "{" in found_name else found_name
                                 for found_name in found} | set(found), name)
        self.assertEqual(unlisted(found), {})

    def test_a_name_planted_through_each_reader_path_fails(self) -> None:
        planted = '''
PLANTED_CONSTANT = "planted-constant"

def plants(c, module, part):
    read_log("planted-read-log", c.delivery)
    append_log("planted-append-log", c.delivery, {})
    flow_log("planted-flow-log", c.delivery)
    plan_flow_log(c, "planted-plan-flow-log", c.delivery)
    lead_file(c, "planted-lead-file")
    tracker_publisher.read_flow(c.flow_log, c.delivery, "planted-read-flow")
    tracker_publisher.append_flow(c.flow_log, c.delivery, "planted-append-flow", {})
    read_log(PLANTED_CONSTANT, c.delivery)
    (c.flow_log / f"{c.delivery}.planted-inline-flow.jsonl").exists()
    (c.state / "planted-inline-state").exists()
    (c.state / f"planted-{part}.json").exists()
    (c.state.parent / "planted-inline-parent").exists()
    (c.state / module.PLANTED_SIDECAR).exists()
    for name in ("planted-looped",):
        (c.state / name).exists()
'''
        sources = corpus_sources()
        sources["run_delivery.py"] += planted
        others = {name: path.read_text(encoding="utf-8") for name, path in NAMING_MODULES.items()}
        others["hosted"] += '\nPLANTED_SIDECAR = "planted-sidecar.json"\n'
        found = unlisted(machine_names(sources, others))
        self.assertEqual(sorted(found), sorted([
            "planted-read-log", "planted-append-log", "planted-flow-log", "planted-plan-flow-log", "planted-lead-file",
            "planted-read-flow", "planted-append-flow", "planted-constant", "planted-inline-flow",
            "planted-inline-state", "planted-{part}.json", "planted-inline-parent", "planted-sidecar.json",
            "planted-looped"]))
        # And the tracker publisher's own readers are read as well.
        publisher = corpus_sources()
        publisher["tracker_publisher.py"] += '\n\ndef plant(flow_log, delivery):\n    read_flow(flow_log, delivery, "planted-publisher")\n'
        self.assertEqual(sorted(unlisted(machine_names(publisher))), ["planted-publisher"])


# -- two seats -------------------------------------------------------------------------------------------------------

class Sentinel(Exception):
    """A command got past its scope check: the step after it raised this instead of running."""


class FakeTracker:
    """One tracker both machines post to, in this process: each ticket's markers and every comment posted."""

    limits: dict[str, int] = {}

    def __init__(self) -> None:
        self.marks: dict[str, set[str]] = {}
        self.posted: list[tuple[str, str]] = []

    def markers(self, ticket: str) -> set[str]:
        return set(self.marks.get(ticket, set()))

    def status(self, ticket: str) -> dict:
        return {}

    def call(self, operation: str, *args: str) -> dict:
        ticket, stamp = args[args.index("--ticket") + 1], args[args.index("--marker") + 1]
        self.marks.setdefault(ticket, set()).add(stamp)
        self.posted.append((ticket, stamp))
        return {}


class CoLeadFixture(partitions.SharedFixture):
    """a opens the record and admits b; b holds lane-1, and a keeps lane-2, the queue and delivery."""

    def setUp(self) -> None:
        super().setUp()
        self.first = self.open()
        self.second = self.join(self.b)
        self.a.patch({"lanes": [lane_row("lane-1"), lane_row("lane-2")],
                      "planned": [planned("APP-1", "lane-1"), planned("APP-2", "lane-2")]})
        self.give("lane:lane-1", self.b)
        # Neither machine runs a hosted publisher process here: what each command asks of one is recorded.
        self.hosted_calls: list[tuple[str, str]] = []
        self.hosted_options: list[dict] = []

        def hosted_call(c, operation, folder, key, **options) -> dict:
            self.hosted_calls.append((operation, str(folder)))
            self.hosted_options.append(options)
            return {"state": "disabled"}
        patcher = mock.patch.object(rd, "hosted_call", side_effect=hosted_call)
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, machine, **extra) -> types.SimpleNamespace:
        """The few settings the runtime reads, for ``machine``'s record."""
        lanes = {"APP-1": "lane-1", "APP-2": "lane-2"}
        return types.SimpleNamespace(
            state=machine.directory, root=machine.repo, main=machine.repo, release=ROOT, remote=REMOTE,
            base_branch="main", delivery=KEY, flow_raw={}, flow_log=machine.repo / "flow-log",
            shelf=types.SimpleNamespace(json=lambda name: {"state": {}} if "delivery-views" not in name else None),
            lane_checkout=lambda key: machine.repo / "lanes" / key, stories={key: {"lane": lane} for key, lane in lanes.items()},
            lane_of=lambda key, state=None: lanes[key], require=lambda key: key, no_deferrals=True, work=Path("work"),
            **extra)

    def labels(self) -> tuple[str, str]:
        seats = state_tool.derived_seats(self.a.journal())
        return seats[self.first]["label"], seats[self.second]["label"]


def accept(args) -> None:
    """lead-accept's acceptance, past the record's lock, as lead-accept runs it."""
    rd.accept_story(rd.cfg(), args)


class LeadCommandScopeTests(CoLeadFixture):
    """lead-assign, lead-own and lead-add-lane need delivery, and lead-accept the story's lane: refused elsewhere
    before anything is written, naming the holder."""

    def run_command(self, machine, command, args, after: str) -> str | None:
        """Run ``command`` on ``machine``; the refusal's words, or None once it passed its scope check and reached
        ``after``, which stops it there."""
        c = self.config(machine)
        before = machine.surfaces(self.remote)
        with mock.patch.object(rd, "cfg", return_value=c), mock.patch.object(rd, "require_lead_checkout"), \
                mock.patch.object(rd, after, side_effect=Sentinel):
            try:
                command(args)
            except rd.Stop as refused:
                self.assertEqual(machine.surfaces(self.remote), before)
                return str(refused)
            except Sentinel:
                return None
        self.fail(f"{command.__name__} neither refused nor reached {after}")

    def test_b_assigning_a_lane_owning_files_or_adding_a_lane_while_a_holds_delivery_is_refused_naming_a(self) -> None:
        first_label, _ = self.labels()
        cases = ((rd.lead_assign, types.SimpleNamespace(keys=["APP-1"], lane="lane-1", title="Lane 1", force=False,
                                                        own_co_changed=False, note=None), "plan_problems"),
                 (rd.lead_own, types.SimpleNamespace(), "ownership_lock"),
                 (rd.lead_add_lane, types.SimpleNamespace(lane="lane-3", grant=[]), "ownership_lock"))
        for command, args, after in cases:
            with self.subTest(command.__name__):
                said = self.run_command(self.b, command, args, after)
                self.assertIsNotNone(said)
                self.assertTrue(said.startswith("scope.not_held: only the holder of delivery, seat "
                                                f"{self.first} ({first_label})"), said)
                # The holder's own command gets past the check.
                self.assertIsNone(self.run_command(self.a, command, args, after))

    def accept_args(self, key: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(requeue=None, key=key, checks=None, full_suite=False, reuse=None, why=None,
                                     take=None)

    def no_change(self) -> None:
        """Each lane's story waits for its no-change hand-off to be accepted, as each lane's own write leaves it."""
        for machine, key in ((self.b, "APP-1"), (self.a, "APP-2")):
            entries = machine.state()["planned"]
            machine.patch({"planned": [{**entry, "noChange": {"baseHead": HEAD, "evidence": "Nothing to change.",
                                                    "handedAt": "2026-11-24T00:00:00Z"}}
                                       if entry["subject"] == key else entry for entry in entries]})
            machine.push()
        self.exchange(self.a, self.b)

    def test_b_accepting_its_own_lanes_queued_hand_off_passes_and_as_lane_is_refused_on_b(self) -> None:
        # Each lane's story handed off with a change and queued, as the lead accepts most hand-offs.
        def doc(machine) -> dict:
            state = machine.state()
            queued = [{"subject": key, "lane": lane, "stage": "queued", "pending": True, "head": HEAD}
                      for key, lane in (("APP-1", "lane-1"), ("APP-2", "lane-2"))]
            return {"state": {**state, "queue": queued, "planned": []}, "journal": machine.journal()}
        with mock.patch.object(rd, "pr_review_config", return_value={}):
            for machine, own, other, holder in ((self.b, "APP-1", "APP-2", self.first),
                                                (self.a, "APP-2", "APP-1", self.second)):
                with self.subTest(seat=machine.repo.name), \
                        mock.patch.object(rd, "load_state", side_effect=lambda: doc(machine)), \
                        mock.patch.object(rd._validator(ROOT), "handed_item", lambda state, journal, key: None), \
                        mock.patch.object(rd, "lead_selects", return_value=True):
                    # Past the check, the acceptance reads what the lane handed: stopped there.
                    self.assertIsNone(self.run_command(machine, accept, self.accept_args(own), "handed_additions"))
                    said = self.run_command(machine, accept, self.accept_args(other), "handed_additions")
                    self.assertTrue(said.startswith(f"scope.not_held: only the holder of lane:"), said)
                    self.assertIn(f"seat {holder}", said)

    def test_b_accepting_its_own_lanes_hand_off_passes_and_as_lane_is_refused_on_b(self) -> None:
        self.no_change()
        doc = lambda machine: {"state": machine.state(), "journal": machine.journal()}
        with mock.patch.object(rd, "pr_review_config", return_value={}):
            with mock.patch.object(rd, "load_state", side_effect=lambda: doc(self.b)):
                self.assertIsNone(self.run_command(self.b, accept, self.accept_args("APP-1"),
                                                   "accept_no_change"))
                said = self.run_command(self.b, accept, self.accept_args("APP-2"), "accept_no_change")
            self.assertTrue(said.startswith(f"scope.not_held: only the holder of lane:lane-2, seat {self.first}"), said)
            with mock.patch.object(rd, "load_state", side_effect=lambda: doc(self.a)):
                said = self.run_command(self.a, accept, self.accept_args("APP-1"), "accept_no_change")
                self.assertTrue(said.startswith(f"scope.not_held: only the holder of lane:lane-1, seat {self.second}"))
                self.assertIsNone(self.run_command(self.a, accept, self.accept_args("APP-2"),
                                                   "accept_no_change"))

    def test_the_assignment_message_names_the_seat_the_lane_reports_to(self) -> None:
        first_label, second_label = self.labels()
        self.assertIn(f"You report to {second_label} (seat {self.second}), which holds lane-1",
                      rd.lane_seat_words(self.config(self.a), "lane-1"))
        self.assertIn(f"You report to {first_label} (seat {self.first}), which holds lane-2",
                      rd.lane_seat_words(self.config(self.b), "lane-2"))
        unshared = Path(tempfile.mkdtemp(prefix="unshared-"))
        self.addCleanup(shutil.rmtree, unshared, True)
        state_tool.init(unshared, "Unshared")
        self.assertIsNone(rd.lane_seat_words(types.SimpleNamespace(state=unshared), "lane-1"))


class SupervisorRoleTests(CoLeadFixture):
    """Each seat's supervisor serves the sync loop, the queue only where it holds it, and runs each pass step by the
    step table."""

    def supervisor(self, machine) -> "rd.Supervisor":
        supervisor = rd.Supervisor(self.config(machine), once=True)
        supervisor.write_lock = lambda: None
        return supervisor

    def setUp(self) -> None:
        super().setUp()
        # These records are shared deliveries, past planning.
        patcher = mock.patch.object(rd, "planning_phase", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_seat_serves_the_sync_loop_and_only_the_queue_holder_serves_the_queue(self) -> None:
        self.assertEqual(self.supervisor(self.a).wanted_children(), ["collab", "serve"])
        self.assertEqual(self.supervisor(self.b).wanted_children(), ["collab"])
        self.give("queue", self.b)
        self.assertEqual(self.supervisor(self.a).wanted_children(), ["collab"])
        self.assertEqual(self.supervisor(self.b).wanted_children(), ["collab", "serve"])
        # The pass after b ingests the transfer starts its queue.
        started = []
        supervisor = self.supervisor(self.b)
        supervisor.children = {"collab": mock.Mock(poll=lambda: None)}
        with mock.patch.object(rd.Supervisor, "start", lambda this, name: started.append(name)):
            supervisor.align_children()
        self.assertEqual(started, ["serve"])

    def test_a_queue_child_that_exits_once_the_queue_is_handed_on_is_dropped_never_restarted_and_no_blocker(self) -> None:
        exited = mock.Mock(poll=lambda: 1)
        restarted: list[str] = []
        with mock.patch.object(rd.Supervisor, "start", lambda this, name: restarted.append(name)):
            # While a holds the queue, its exited serve is restarted, as before.
            supervisor = self.supervisor(self.a)
            supervisor.children = {"serve": exited}
            supervisor.keep_alive()
            self.assertEqual(restarted, ["serve"])
            restarted.clear()
            self.give("queue", self.b)
            supervisor = self.supervisor(self.a)
            supervisor.children = {"serve": exited, "collab": mock.Mock(poll=lambda: None)}
            for _ in range(rd.CHILD_FLAPS + 2):
                supervisor.keep_alive()
        self.assertEqual(restarted, [])
        self.assertEqual(list(supervisor.children), ["collab"])
        self.assertEqual(supervisor.flapping(), ())

    def one_pass(self, machine) -> tuple[list[str], dict]:
        """One supervisor pass on ``machine``, every step recorded instead of run: the steps it ran, and its entry."""
        ran: list[str] = []
        record = lambda name, value=None: (lambda *args, **kwargs: ran.append(name) or value)
        c = self.config(machine, load_delivery=lambda key: None)
        supervisor = rd.Supervisor(c, once=True)
        doc = {"state": machine.state(), "journal": machine.journal()}
        logged: list[dict] = []
        patches = [mock.patch.object(rd, name, record(step, value)) for name, step, value in (
            ("reconcile_once", "reconcile", []), ("settle_landings", "regions", ""), ("cleanup_merged", "cleanup", []),
            ("post_merge_step", "fullHealth", ""), ("journal_lens_returns", "lenses", []),
            ("journal_finish_met", "finish", []), ("deferral_breaches", "deferrals", []),
            ("write_peer_waits", "peerWaits", ""), ("write_lane_changes", "laneChanges", ""),
            ("copy_latest", "latest", ""), ("write_needs", "needs", None), ("close_out", "closeOut", ([], False)),
            ("write_friction_stub", "friction", ""))]
        patches += [mock.patch.object(rd, "render_views", lambda c, only=None: ran.append(
                        "publish" if only == "publishers" else "render") or ""),
                    mock.patch.object(rd, "needs_you", return_value=[]), mock.patch.object(rd, "close_holds",
                                                                                         return_value=[]),
                    mock.patch.object(rd, "load_state", return_value=doc),
                    mock.patch.object(rd, "read_log", return_value=[]),
                    mock.patch.object(rd, "append_log", lambda name, key, entry: logged.append(entry)),
                    mock.patch.object(rd.Supervisor, "peer_merges", lambda this, entry: ran.append("peers") or ""),
                    mock.patch.object(rd.Supervisor, "blockers", lambda this: ran.append("blockers") or ""),
                    mock.patch.object(rd.Supervisor, "lose", lambda this: False)]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            supervisor.one_pass(1)
        return ran, logged[-1]

    def test_each_pass_step_runs_on_the_machine_the_table_names(self) -> None:
        everywhere = ["cleanup", "lenses", "deferrals", "peerWaits", "laneChanges", "blockers", "needs", "render",
                      "latest"]
        ran, entry = self.one_pass(self.a)
        # With nothing holding it, a's close cleans up once more before it closes, and draws the views after.
        self.assertEqual(sorted(ran), sorted(["reconcile", "regions", "fullHealth", "peers", "finish", "publish",
                                              "cleanup", "closeOut", "render", *everywhere]))
        ran, entry = self.one_pass(self.b)
        self.assertEqual(sorted(ran), sorted(everywhere))
        self.assertEqual(entry["publish"], "held by another seat: delivery")
        self.assertEqual(entry["close"], "closed by the delivery holder's machine")
        # Given the queue, b runs full health; a, keeping delivery, no longer does.
        self.give("queue", self.b)
        self.assertIn("fullHealth", self.one_pass(self.b)[0])
        self.assertNotIn("fullHealth", self.one_pass(self.a)[0])

    def test_the_first_pass_of_the_seat_given_delivery_takes_what_was_carried_and_runs_its_steps(self) -> None:
        rd.give_delivery(self.config(self.a), KEY, self.a.directory, self.second, 1.0)
        self.a.push()
        self.b.take()
        ran, entry = self.one_pass(self.b)
        self.assertTrue(entry["delivery"].startswith(f"took delivery from seat {self.first}"), entry)
        self.assertTrue(collab.delivery_taken(self.b.directory))
        self.assertIn("publish", ran)
        self.assertIn(("start", str(self.b.directory)), self.hosted_calls)
        # Taken once: the next pass takes nothing.
        ran, entry = self.one_pass(self.b)
        self.assertNotIn("delivery", entry)
        self.assertNotIn("publish", self.one_pass(self.a)[0])

    def test_cleanup_lens_returns_and_deferrals_take_only_the_stories_of_the_lanes_this_seat_holds(self) -> None:
        doc = lambda machine: {"state": {**machine.state(), "queue": [
            {"subject": "APP-1", "lane": "lane-1", "stage": "merged"},
            {"subject": "APP-2", "lane": "lane-2", "stage": "merged"}], "planned": [
            {"subject": "APP-3", "lane": "lane-1", "stage": "settled"},
            {"subject": "APP-4", "lane": "lane-2", "stage": "settled"}]},
            "journal": [{"kind": "review.started", "subject": key} for key in ("APP-1", "APP-2")]}
        for machine, mine, settled in ((self.a, "APP-2", "APP-4"), (self.b, "APP-1", "APP-3")):
            with self.subTest(seat=machine.repo.name):
                c = self.config(machine)
                c.stories.update({"APP-3": {"lane": "lane-1"}, "APP-4": {"lane": "lane-2"}})
                cleaned, read, breached = [], [], []
                (machine.repo / "lanes" / "APP-1" / "work/review/APP-1").mkdir(parents=True, exist_ok=True)
                (machine.repo / "lanes" / "APP-2" / "work/review/APP-2").mkdir(parents=True, exist_ok=True)
                with mock.patch.object(rd, "lane_moved_off", return_value=True), \
                        mock.patch.object(rd, "unaccepted_merge", return_value=False), \
                        mock.patch.object(rd, "clean_story", lambda key: cleaned.append(key) or ("removed", True, [])), \
                        mock.patch.object(rd, "load_state", return_value=doc(machine)), \
                        mock.patch.object(rd, "journal_returns", lambda key, folder, d: read.append(key) or []), \
                        mock.patch.object(rd, "deferred_findings", return_value=[
                            {"story": key, "finding": "f-1", "title": "Later"} for key in ("APP-1", "APP-2")]), \
                        mock.patch.object(rd, "breach_of", return_value=False), \
                        mock.patch.object(rd, "state_tool", lambda *args: breached.append(args[args.index("--subject") + 1])):
                    rd.cleanup_merged(c, doc(machine))
                    rd.journal_lens_returns(c)
                    rd.deferral_breaches(c, doc(machine))
                self.assertEqual((cleaned, read, breached), ([mine, settled], [mine], [mine]))


class PublisherFixture(CoLeadFixture):
    """Both machines' publishers, and a fake tracker and hosted origin they share."""

    CONF = {"mode": "publisher", "problems": [], "transitions": {}}

    def setUp(self) -> None:
        super().setUp()
        self.tracker = FakeTracker()
        self.api = FakeAPI()
        self.hosted = views_common.validate_config(VIEWS)["hosted"]

    def publish(self, machine) -> None:
        """The tracker publisher's run on ``machine``, as the render wrapper starts it after a write there."""
        doc = json.dumps({"state": machine.state(), "journal": machine.journal()}).encode("utf-8")
        with mock.patch.object(tp, "settings", return_value=self.CONF), \
                mock.patch.object(tp, "Adapter", lambda conf, root: self.tracker), \
                mock.patch.object(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(doc))):
            self.assertEqual(tp.main(["--state-dir", str(machine.directory), "--delivery", KEY,
                                      "--repo", str(machine.repo)]), 0)

    def decide(self, machine, text: str) -> dict:
        return state_tool.append_journal(machine.directory, kind="decision.recorded", subject="APP-1",
                                         data={"text": text, "why": "A fixture."})

    def stamp(self, machine, entry: dict) -> str:
        origin = next(found["origin"] for found in machine.journal() if found["id"] == entry["id"])
        init = tp.init_id(machine.journal())
        return tp.marker(KEY, init, f"{origin['seat']}.{origin['seq']}", "comment")

    def give_command(self, wait: float) -> str:
        """`collab give --scope delivery --to b` on a, through the command the skill pages name: its printed line."""
        args = types.SimpleNamespace(collab_action="give", scope="delivery", to=self.second, wait=wait, key=KEY)
        out = io.StringIO()
        with mock.patch.object(rd, "collab_config", return_value=(self.config(self.a), KEY, self.a.directory)), \
                mock.patch.object(rd, "cfg", side_effect=AssertionError("the global config was read")), \
                contextlib.redirect_stdout(out):
            rd.collab_scope(args)
        return out.getvalue()

    def hand_over(self, wait: float = 5.0) -> dict:
        said = self.give_command(wait)
        self.assertTrue(said.startswith("scope.transferred (entry "), said)
        self.a.push()
        self.b.take()
        return next(entry for entry in reversed(self.a.journal()) if entry["kind"] == "scope.transferred")



class HandoverTests(PublisherFixture):
    """Handing delivery over keeps the public link and posts nothing twice; handing it over, or a lane, while this
    machine still has something of it in flight is refused with scope.handover_busy, naming it."""

    def test_delivery_moves_to_b_keeping_the_public_link_and_each_update_is_posted_once_by_its_holder(self) -> None:
        x = self.decide(self.a, "X")
        self.exchange(self.a, self.b)
        # Before delivery moves: a posts X, and b, holding a lane only, posts nothing.
        self.publish(self.b)
        self.assertEqual(self.tracker.posted, [])
        self.publish(self.a)
        self.assertEqual(self.tracker.posted, [("APP-1", self.stamp(self.a, x))])
        published = hosted.Publisher(self.a.directory, self.hosted, KEY, self.api)
        published.step(lambda: json.loads(json.dumps(VALID)))
        link = published.identity["publicId"]
        # Z, journalled after a's last post, is a's not yet posted when delivery moves.
        z = self.decide(self.a, "Z")
        seq = lambda machine, entry: next(e["origin"]["seq"] for e in machine.journal() if e["id"] == entry["id"])
        # Before the move, b publishes no hosted view: its start is refused without a call.
        self.assertEqual(rd.start_hosted(self.config(self.b)), {"state": "held_elsewhere"})
        self.assertEqual(self.hosted_calls, [])
        self.hand_over()
        self.assertIn(("stop", str(self.a.directory)), self.hosted_calls)
        self.assertEqual(rd.start_hosted(self.config(self.a)), {"state": "held_elsewhere"})
        self.assertNotIn(("start", str(self.a.directory)), self.hosted_calls)
        carried = collab.read_partition(self.b.directory, collab.peer_ref(KEY, self.first))[0]["carried"]
        self.assertEqual(carried["hostedIdentity"]["publicId"], link)
        # The position a carried covers X and stops below Z, which it never posted.
        self.assertGreaterEqual(carried["trackerThrough"][self.first], seq(self.a, x))
        self.assertLess(carried["trackerThrough"][self.first], seq(self.a, z))
        # Moved, before b takes it: neither machine posts, and b starts no hosted view.
        self.publish(self.a)
        self.publish(self.b)
        self.assertEqual(len(self.tracker.posted), 1)
        self.assertEqual(rd.start_hosted(self.config(self.b)), {"state": "held_elsewhere"})
        # b takes it: a's identity is b's own, its own stale status and retry files go, its session is resumed, and X
        # is covered by the position a carried, posting nothing.
        for name in (hosted.STATUS, hosted.RETRY):
            (self.b.directory / name).write_text("{}\n")
        taken = rd.take_delivery(self.config(self.b))
        self.assertIn(f"took delivery from seat {self.first}", taken)
        self.assertIsNone(rd.take_delivery(self.config(self.b)))
        starts = [options for (operation, folder), options in zip(self.hosted_calls, self.hosted_options)
                  if (operation, folder) == ("start", str(self.b.directory))]
        self.assertEqual([options.get("resume") for options in starts], [True])
        self.assertEqual(json.loads((self.b.directory / hosted.IDENTITY).read_text())["publicId"], link)
        self.assertFalse((self.b.directory / hosted.STATUS).exists() or (self.b.directory / hosted.RETRY).exists())
        on_b = {e["data"]["text"]: e for e in self.b.journal() if e["kind"] == "decision.recorded"}
        reasons = lambda entry: [line.get("reason") for line in tp.read_ledger(self.b.directory)
                                 if line["journal"] == entry["id"]]
        self.assertEqual((reasons(on_b["X"]), reasons(on_b["Z"])), ([tp.TAKEN], []))
        # b's next update, written on a clock an hour behind a's, is posted once by b, with its origin's marker.
        behind = "2026-11-23T23:00:00.000000Z"
        with mock.patch.object(state_tool, "now", return_value=behind):
            y = self.decide(self.b, "Y")
        self.publish(self.b)
        self.publish(self.b)
        self.publish(self.a)
        # Z once, by b, under a's origin; Y once, under b's; X never again.
        self.assertEqual(self.tracker.posted, [("APP-1", self.stamp(self.a, x)), ("APP-1", self.stamp(self.a, z)),
                                               ("APP-1", self.stamp(self.b, y))])
        self.assertIn(f":{self.first}.{seq(self.a, z)}:comment", self.tracker.posted[1][1])
        self.assertIn(f":{self.second}.", self.tracker.posted[2][1])
        # The hosted session resumes on b with the same identity, so the link stays, and a is fenced off it.
        self.api.session_value["leaseUntil"] = 1
        moved = hosted.Publisher(self.b.directory, self.hosted, KEY, self.api)
        moved.step(lambda: json.loads(json.dumps(VALID)), resume=True)
        self.assertEqual((moved.identity["publicId"], moved.identity["epoch"]), (link, 2))
        with self.assertRaisesRegex(hosted.PublishError, "writer_fenced"):
            hosted.Publisher(self.a.directory, self.hosted, KEY, self.api).step(lambda: json.loads(json.dumps(VALID)))

    def test_a_handover_with_a_tracker_post_in_flight_or_a_hosted_publication_pending_is_busy_then_passes(self) -> None:
        before = self.a.surfaces(self.remote)
        held, release = threading.Event(), threading.Event()

        def posting() -> None:
            with tp.ledger_lock(self.a.directory, wait=True):
                held.set()
                release.wait(10)
        thread = threading.Thread(target=posting)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.set)
        held.wait(5)
        with self.assertRaisesRegex(rd.Stop, "^scope.handover_busy: .*a tracker post is in flight"):
            self.give_command(0.3)
        self.assertEqual(self.a.surfaces(self.remote), before)
        release.set()
        thread.join()
        identity = hosted.Publisher(self.a.directory, self.hosted, KEY, self.api).identity
        identity.update(epoch=1, pending={"epoch": 1, "sequence": 2, "digest": "d" * 64, "sourceFingerprint": "e" * 64})
        (self.a.directory / hosted.IDENTITY).write_text(json.dumps(identity))
        with self.assertRaisesRegex(rd.Stop, "^scope.handover_busy: .*hosted snapshot 2 is pending"):
            rd.give_delivery(self.config(self.a), KEY, self.a.directory, self.second, 0.3)
        self.assertEqual(self.a.surfaces(self.remote), before)
        self.assertNotIn("stop", [operation for operation, _ in self.hosted_calls])
        identity["pending"] = None
        (self.a.directory / hosted.IDENTITY).write_text(json.dumps(identity))
        # A publication the stop catches mid-way leaves it pending: the publisher is started again to finish it, and
        # the handover keeps waiting.
        caught = {**identity, "pending": {"epoch": 1, "sequence": 3, "digest": "d" * 64, "sourceFingerprint": "e" * 64}}

        archives = []

        def stopped_mid_publication(c, operation, folder, key, **options) -> dict:
            self.hosted_calls.append((operation, str(folder)))
            archives.append(options.get("archive", True))
            if operation == "stop":
                (self.a.directory / hosted.IDENTITY).write_text(json.dumps(caught))
            return {"state": "stopped"}
        with mock.patch.object(rd, "hosted_call", side_effect=stopped_mid_publication):
            with self.assertRaisesRegex(rd.Stop, "^scope.handover_busy: .*hosted snapshot 3 is pending"):
                rd.give_delivery(self.config(self.a), KEY, self.a.directory, self.second, 0.3)
        self.assertEqual([operation for operation, _ in self.hosted_calls][:2], ["stop", "start"])
        # The handover's config names no delivery to archive under: neither call takes an archive root.
        self.assertEqual(archives[:2], [False, False])
        self.assertEqual(self.a.surfaces(self.remote), before)
        (self.a.directory / hosted.IDENTITY).write_text(json.dumps(identity))
        self.assertEqual(self.hand_over(0.3)["data"], {"scope": "delivery", "from": self.first, "to": self.second})
        self.assertTrue(state_tool.holds(self.b.journal(), self.second, "delivery"))

    def test_a_lane_with_a_merged_storys_checkout_left_on_this_machine_is_busy_until_it_is_cleaned_up(self) -> None:
        entries = self.b.state()["planned"]
        self.b.patch({"planned": [{**entry, "stage": "settled"} if entry["subject"] == "APP-1" else entry
                                  for entry in entries]})
        checkout = self.b.repo / "lanes" / "APP-1"
        checkout.mkdir(parents=True)
        args = types.SimpleNamespace(collab_action="give", scope="lane:lane-1", to=self.first, wait=0.1, key=KEY)
        c = self.config(self.b)
        before = self.b.surfaces(self.remote)
        # collab_config's own config is the one it acts with: nothing reads the global one, whatever the checkout.
        with mock.patch.object(rd, "collab_config", return_value=(c, KEY, self.b.directory)), \
                mock.patch.object(rd, "cfg", side_effect=AssertionError("the global config was read")), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(rd.Stop, "^scope.handover_busy: lane:lane-1 still has the checkout of APP-1"):
                rd.collab_scope(args)
            self.assertEqual(self.b.surfaces(self.remote), before)
            shutil.rmtree(checkout)
            rd.collab_scope(args)
        self.assertTrue(state_tool.holds(self.b.journal(), self.first, "lane:lane-1"))


class SharedMarkerTests(unittest.TestCase):
    def test_a_shared_records_init_id_is_its_record_started_whatever_each_seat_ingested_first(self) -> None:
        opener = {"seat": "s-aaaaaaaaaaaa", "seq": 1}
        started = {"id": 1, "kind": "record.started", "at": "2026-11-24T00:00:00.000000Z", "origin": opener}
        earlier = {"id": 1, "kind": "seat.requested", "at": "2026-11-23T00:00:00.000000Z",
                   "origin": {"seat": "s-bbbbbbbbbbbb", "seq": 1}}
        self.assertEqual(tp.init_id([started]), tp.init_id([earlier, {**started, "id": 2}]))
        self.assertEqual(tp.init_id([started]), "20261124T000000000000Z")
        # An unshared record's is still its first entry's, as before.
        self.assertEqual(tp.init_id([{k: v for k, v in earlier.items() if k != "origin"}]), "20261123T000000000000Z")
        self.assertEqual(tp.entry_ref({"id": 7, "origin": {"seat": "s-aaaaaaaaaaaa", "seq": 3}}), "s-aaaaaaaaaaaa.3")
        self.assertEqual(tp.entry_ref({"id": 7}), 7)


class SyncTimingTests(partitions.SharedFixture):
    """With each seat's sync loop running, a write on one machine reaches the other's record within two polls and one
    push, with no sync command run; a write stamped an hour ahead of the peer's clock arrives the same way."""

    run_quietly = True  # it waits on two real sync loops, each a git process every poll
    POLL = 2.0

    def serve(self, machine) -> subprocess.Popen:
        stop = machine.repo / "serve.stop"
        process = subprocess.Popen([sys.executable, "-B", str(collab.__file__), "serve", "--dir", str(machine.directory),
                                    "--remote", REMOTE, "--poll", str(self.POLL), "--stop", str(stop)],
                                   cwd=machine.repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stopped() -> None:
            stop.write_text("stop\n")
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.addCleanup(stopped)
        return process

    def arrival(self, write, reader) -> float:
        """Seconds from ``write`` on one machine until ``reader``'s record holds its entry."""
        started = time.monotonic()
        entry = write()
        origin = next(found["origin"] for found in json.loads(
            "[" + ",".join((self.a.directory / "journal.jsonl").read_text().splitlines()) + "]") if found["id"] == entry["id"])
        while time.monotonic() - started < 6 * self.POLL + 30:
            if any(found.get("origin", {}).get("seat") == origin["seat"] and found["origin"]["seq"] == origin["seq"]
                   for found in reader.journal()):
                return time.monotonic() - started
            time.sleep(0.05)
        self.fail("the write never reached the peer")

    def test_a_write_reaches_the_running_peer_within_two_polls_and_one_push_whatever_its_clock(self) -> None:
        self.open()
        self.join(self.b)
        # One push of a write, measured: what the bound allows beyond the two polls.
        self.a.decide("Before the loops run.")
        started = time.monotonic()
        self.a.push()
        push = time.monotonic() - started
        self.b.take()
        self.serve(self.a)
        self.serve(self.b)
        time.sleep(self.POLL)  # both loops have run their first poll
        bound = 2 * self.POLL + push + 1.0  # and a second for this test's own reads of the peer's record
        self.assertLess(self.arrival(lambda: self.a.decide("Now."), self.b), bound)
        with mock.patch.object(state_tool, "now", return_value="2026-11-25T01:00:00.000000Z"):
            self.assertLess(self.arrival(lambda: self.a.decide("An hour ahead."), self.b), bound)


class ServeSettlesUnderTheLockTests(colead.mq.MergeQueueTestCase):
    """What serve writes after a turn, such as the stop of a refused candidate, is written under the turn's queue lock:
    a handover tried then waits for it, and the write lands."""

    def setUp(self) -> None:
        """a serves the queue of the merge-queue tests' adopter; b, a clone of its remote, holds lane-1."""
        super().setUp()
        cleared = mock.patch.dict(colead.os.environ, partitions.own_git_environment(), clear=True)
        cleared.start()
        self.addCleanup(cleared.stop)
        self.adopter = colead.QueueAcrossSeatsTests.adopter_for_seats(self)
        self.a = partitions.Machine(self.adopter.operator)
        self.b = partitions.Machine(partitions.clone(self.adopter.remote, self.directory / "b", name="B"))
        collab.open_record(self.a.directory, key=KEY, headline="Shared",
                           base={"ref": "main", "head": partitions.git(self.a.repo, "rev-parse", "HEAD")})
        self.a.push()
        self.b.take()
        collab.request_seat(self.b.directory)
        self.b.push()
        self.a.take()
        collab.admit_seat(self.a.directory, self.b.seat())
        self.second = self.b.seat()
        self.a.patch({"lanes": [lane_row("lane-1")], "planned": [planned("feature", "lane-1")],
                      "meta": {"handoffsNeedAcceptance": True}})
        collab.give_scope(self.a.directory, "lane:lane-1", self.second)
        self.a.push()
        self.b.take()

    def item(self, machine, subject: str = "feature") -> dict:
        return next(entry for entry in machine.state()["queue"] if entry["subject"] == subject)

    def test_a_handover_tried_while_serve_stops_a_refused_candidate_waits_and_the_stop_lands(self) -> None:
        queue = colead.mq.queue
        head = self.adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        state_tool.handoff(self.b.directory, lane="lane-1", subject="feature", head=head, change_request="cr-1")
        state_tool.accept_handoff(self.b.directory, "feature")
        self.b.push()
        self.a.take()
        tried, refused_stop = [], queue._refused_stop

        def stopping(directory, subject, head, reason):
            build = refused_stop(directory, subject, head, reason)

            def handed_over_first(current: dict):
                try:
                    collab.give_scope(self.a.directory, "queue", self.second, wait=0.05, poll=0.01)
                except state_tool.StateError as error:
                    tried.append(error.code)
                return build(current)
            return handed_over_first

        # The turn refuses the candidate, as one whose branch is gone is refused.
        with mock.patch.object(queue, "_refused_stop", stopping), mock.patch.object(
                queue.Runner, "resolve", side_effect=queue.Stop("candidate_unknown", "feature's branch is gone")):
            results = queue.serve(self.adopter.operator, self.a.directory, remote="origin", base="main",
                                  until_idle=True, poll=0)
        self.assertEqual([result.outcome for result in results], ["refused"], [result.stop for result in results])
        self.assertEqual(tried, ["scope.handover_busy"])
        self.assertEqual(self.item(self.a)["stage"], "stopped")
        # Once the turn is over, the queue is idle and passes on.
        self.assertEqual(collab.give_scope(self.a.directory, "queue", self.second, wait=1)["kind"], "scope.transferred")


class TakingDeliveryTests(PublisherFixture):
    """The seat given delivery takes what was carried once, retries a partition it can't read yet, and starts its
    hosted publisher either way; a publisher run that began before the handover posts nothing after it."""

    def test_delivery_passed_with_no_hosted_identity_still_starts_the_takers_publisher(self) -> None:
        self.hand_over()
        rd.take_delivery(self.config(self.b))
        self.assertIn(("start", str(self.b.directory)), self.hosted_calls)
        self.assertFalse((self.b.directory / hosted.IDENTITY).exists())

    def test_a_givers_partition_that_cant_be_read_is_taken_on_a_later_pass(self) -> None:
        x = self.decide(self.a, "X")
        self.exchange(self.a, self.b)
        self.publish(self.a)
        self.hand_over()
        tool = rd._validator(ROOT)  # the state tool run-delivery reads with, which loads collab afresh each time
        loaded = tool._collab

        def unreadable():
            module = loaded()
            module.peer_carried = lambda directory, seat: None
            return module
        with mock.patch.object(tool, "_collab", unreadable):
            self.assertIsNone(rd.take_delivery(self.config(self.b)))
        self.assertFalse(collab.delivery_taken(self.b.directory))
        self.assertEqual(tp.read_ledger(self.b.directory), [])
        self.assertIn("tracker update(s) it had posted", rd.take_delivery(self.config(self.b)))
        self.assertTrue(collab.delivery_taken(self.b.directory))
        self.assertEqual(self.tracker.posted, [("APP-1", self.stamp(self.a, x))])

    def test_a_publisher_run_that_passed_before_the_handover_takes_the_lock_after_it_and_posts_nothing(self) -> None:
        x = self.decide(self.a, "X")
        handed, real = [], tp.ledger_lock

        @contextlib.contextmanager
        def handed_over_first(state_dir, wait):
            # The publisher's run on a reaches the ledger's lock just after a handover took and released it.
            if not handed and Path(state_dir) == self.a.directory and not wait:
                handed.append(True)
                with mock.patch.object(tp, "ledger_lock", real):
                    rd.give_delivery(self.config(self.a), KEY, self.a.directory, self.second, 1.0)
            with real(state_dir, wait) as taken:
                yield taken

        with mock.patch.object(tp, "ledger_lock", handed_over_first):
            self.publish(self.a)
        self.assertEqual(handed, [True])
        self.assertEqual(self.tracker.posted, [])
        self.a.push()
        self.b.take()
        rd.take_delivery(self.config(self.b))
        self.publish(self.b)
        self.assertEqual(self.tracker.posted, [("APP-1", self.stamp(self.b, next(
            e for e in self.b.journal() if e["kind"] == "decision.recorded")))])
        self.assertIn(f":{self.first}.", self.tracker.posted[0][1])
