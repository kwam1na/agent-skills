"""The walk of every call that writes a journal entry.

Each of the three scripts that write the journal is read as a syntax tree, and every call that writes an entry is
found in one of three shapes: ``append_journal(...)``, the runner's ``self.journal(...)``, and a ``journal`` command
line, ``state_tool("journal", ..., "--kind", K, ...)``. At each, the kind must be a string the registry lists, or one
of a set of them that the call can only choose between. A call that passes a sentence is refused, and so is a call the
inventory below does not hold. A function that writes with a kind it was given is a helper: each call to it is walked
in its place and counted as a write. The ``journal`` command's own call is exempt, since the state tool checks its kind
when it runs. No test journals a sentence either.
"""

from __future__ import annotations

import ast
import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "skills/orchestrate-work/references/journal-kinds.json"
SCRIPTS = {
    "delivery_state.py": ROOT / "skills/orchestrate-work/scripts/delivery_state.py",
    "merge_queue.py": ROOT / "skills/orchestrate-work/scripts/merge_queue.py",
    "run_delivery.py": ROOT / "skills/run-delivery/scripts/run_delivery.py",
}
# The journal command's own call: the state tool checks the kind it is given when it runs.
EXEMPT = {("delivery_state.py", "main")}
SENTENCE = "summary"
# The state tool's append under its own lock: append_journal makes each entry with it, and a writer that already
# holds the lock builds the entry itself.
LOCKED = "_append_journal_locked"


def _key(function: str, kinds: set[str]) -> tuple[str, str]:
    return function, " or ".join(sorted(kinds))


# Every call that writes an entry, by the function it sits in and the kind it writes, or the kinds it chooses between.
INVENTORY = {
    "delivery_state.py": Counter({
        _key("complete_base_move_locked", {"base.moved"}): 1,
        _key("handoff", {"candidate.handed"}): 1,
        _key("no_change_handoff", {"candidate.handed"}): 1,
        _key("accept_handoff", {"handoff.accepted"}): 1,
        _key("reject_handoff", {"handoff.rejected"}): 1,
        _key("select_checks", {"check.selected", "check.selection.cleared"}): 1,
        _key("select_checks", {"decision.recorded"}): 1,
        _key("capture_review", {"review.recorded"}): 1,
        _key("carry_review", {"review.carried"}): 1,
        _key("_journal_recovery", {"candidate.merged"}): 1,
        _key("_reconcile_work", {"work.cleared"}): 1,
        _key("take_snapshot", {"snapshot.retaken"}): 1,
        _key("take_snapshot", {"snapshot.stale"}): 1,
        _key("_apply_siblings", {"sibling.landed"}): 1,
        _key("_apply_siblings", {"sibling.mismatched"}): 1,
        _key("set_sibling", {"sibling.added"}): 1,
        _key("set_sibling", {"sibling.removed"}): 1,
    }),
    "merge_queue.py": Counter({
        _key("Runner.recover_interrupted", {"candidate.stopped"}): 1,
        _key("Runner.check", {"check.started"}): 1,
        _key("Runner.check_watched", {"check.finished"}): 1,
        _key("Runner.carry", {"check.carried"}): 1,
        _key("Runner.run_checks", {"check.reused"}): 1,
        _key("Runner.run_checks", {"check.retried"}): 1,
        _key("Runner.run_checks", {"check.failed"}): 1,
        _key("Runner.rerun_on_base", {"base.rerun.started"}): 1,
        _key("Runner.rerun_on_base", {"check.retried"}): 1,
        _key("Runner.rerun_on_base", {"base.rerun.finished"}): 1,
        _key("Runner.rerun_on_base", {"base.rerun.stopped"}): 1,
        _key("Runner.rerun_stopped", {"base.rerun.stopped"}): 1,
        _key("Runner.merge", {"candidate.merged"}): 1,
        _key("Runner.merge", {"base.repaired"}): 1,
        _key("Runner.batch_round", {"candidate.stopped"}): 1,
        _key("Runner.batch_round", {"check.started"}): 1,
        _key("Runner.batch_round", {"check.finished"}): 1,
        _key("Runner.batch_round", {"base.broken"}): 1,
        _key("Runner.release", {"release.started"}): 1,
        _key("Runner.release", {"release.finished"}): 2,
        _key("Runner.stopped", {"base.rerun.finished"}): 1,
        _key("Runner.stopped", {"base.broken"}): 1,
        _key("Runner.stopped", {"selection.refused"}): 1,
        _key("Runner.stopped", {"candidate.stopped"}): 1,
        _key("hold_for_setup", {"serving.held"}): 1,
    }),
    "run_delivery.py": Counter({
        _key("ensure_journaled", {"review.round"}): 1,
        _key("review_prepare", {"review.started"}): 1,
        _key("journal_returns", {"review.lens.returned"}): 1,
        _key("lead_evidence", {"evidence.published"}): 1,
        _key("lead_authorize_base_merge", {"base.merge.authorized"}): 1,
        _key("base_merge", {"base.merge.authorized"}): 1,
        _key("rebuild_plan", {"plan.updated"}): 1,
        _key("rebuild_preview", {"plan.updated"}): 1,
        _key("lead_plan", {"story.added"}): 1,
        _key("lead_plan", {"story.approved"}): 1,
        _key("lead_init", {"record.started"}): 1,
        _key("lead_init", {"story.approved"}): 1,
        _key("suite_run", {"integration.suite"}): 2,
        _key("suite_started", {"integration.suite.started"}): 1,
        # A run that steps off the machine's wait with --now --why: claim's and lead-suite's.
        _key("journal_override", {"decision.recorded"}): 1,
        _key("lead_integrate", {"integration.merged"}): 1,
        _key("lead_integrate", {"review.round"}): 1,
        _key("lead_integrate", {"integration.requested"}): 1,
        _key("lead_integrate", {"integration.landed"}): 1,
        _key("_register_stories", {"story.added"}): 1,
        _key("_register_stories", {"story.approved"}): 1,
        _key("lead_push", {"base.published"}): 1,
        _key("lead_push", {"decision.recorded"}): 1,
        _key("lead_flake", {"flake.declared"}): 1,
        _key("change_regions", {"ownership.granted", "ownership.revoked"}): 1,
        _key("change_carve", {"ownership.carved"}): 1,
        _key("settle_landings", {"ownership.released"}): 1,
        _key("settle_landings", {"ownership.carried"}): 1,
        _key("change_carry", {"ownership.carried"}): 1,
        _key("change_ownership", {"ownership.granted", "ownership.revoked"}): 1,
        _key("lead_assign", {"lane.assigned"}): 1,
        _key("change_claims", {"ownership.claimed"}): 1,
        _key("accept_no_change", {"handoff.checked"}): 1,
        _key("accept_story", {"handoff.checked"}): 1,
        _key("accept_story", {"decision.recorded"}): 1,
        _key("clean_story", {"cleanup.done"}): 1,
        _key("journal_finish_met", {"finish.met"}): 1,
        _key("lead_finish", {"finish.met"}): 1,
        _key("lead_compound", {"compounding.recorded"}): 1,
        _key("lead_close", {"compounding.recorded"}): 1,
        _key("lead_may_stop", {"hook.held"}): 1,
        _key("note_posture", {"posture.changed"}): 2,
        _key("record_focused", {"fullhealth.skipped"}): 1,
        _key("record_full_health", {"base.broken", "base.flaky", "base.repaired", "base.verified"}): 1,
        _key("deferral_breaches", {"deferral.breached"}): 1,
        _key("followups_step", {"followup.filed"}): 1,
        _key("note_gap", {"gap.noted"}): 1,
        _key("closed_step", {"delivery.closed"}): 1,
        _key("Supervisor.peer_merges", {"peer.merged"}): 1,
        _key("Supervisor.blockers", {"blocker.recorded"}): 1,
        _key("Supervisor.blockers", {"blocker.resolved"}): 1,
        _key("lead_decide", {"decision.recorded"}): 1,
        _key("lead_block", {"blocker.recorded"}): 1,
        _key("lead_hold", {"story.held"}): 1,
        _key("lead_hold", {"story.unheld"}): 1,
        _key("lead_resolve", {"fullhealth.rerun"}): 1,
        _key("lead_resolve", {"blocker.resolved"}): 1,
        _key("requeue", {"handoff.requeued"}): 1,
    }),
}


class Unresolved(Exception):
    """A kind that is neither a registered literal nor one of a set of them the call chooses between."""


class Walk:
    """The calls that write an entry in one script, each as ``(function, kinds, line)``, and what is wrong with them."""

    def __init__(self, name: str, source: str, kinds: set[str]) -> None:
        self.name, self.kinds = name, kinds
        self.tree = ast.parse(source)
        self.writes: list[tuple[str, frozenset[str], int]] = []
        self.problems: list[str] = []
        self.constants = {target.id: node.value for node in self.tree.body if isinstance(node, ast.Assign)
                          for target in node.targets if isinstance(target, ast.Name)}
        self.functions: dict[str, ast.FunctionDef] = {}
        self.owner: dict[ast.AST, str] = {}
        self.calls: list[ast.Call] = []
        self._index(self.tree, "", None)
        self.walked: set[tuple[int, int]] = set()
        for call, function in self._writes():
            self._walk(call, function, set())
        self.problems = list(dict.fromkeys(self.problems))

    def _index(self, node: ast.AST, prefix: str, owner: str | None) -> None:
        """One pass over the tree: each call, and the function each node sits in. A nested function is its enclosing
        function's; a method is named by its class."""
        for child in ast.iter_child_nodes(node):
            inner = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and owner is None:
                inner = f"{prefix}{child.name}"
                self.functions.setdefault(inner, child)
            if inner is not None:
                self.owner[child] = inner
            if isinstance(child, ast.Call):
                self.calls.append(child)
            self._index(child, f"{prefix}{child.name}." if isinstance(child, ast.ClassDef) and owner is None else prefix, inner)

    def _writes(self) -> list[tuple[ast.Call, str]]:
        """Every call in one of the three shapes, with the function it sits in."""
        return [(node, self.owner.get(node, "<module>")) for node in self.calls if self._shape(node) is not None]

    def _shape(self, call: ast.Call) -> ast.expr | None | bool:
        """The kind a call writes, as an expression; False for a call that is no write."""
        func = call.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name == "append_journal":
            return next((word.value for word in call.keywords if word.arg == "kind"), None)
        if name == LOCKED and self.owner.get(call) != "append_journal":
            # An entry built by hand under the lock: its kind is the one its dict names.
            entry = self._entry(call)
            return entry.values[[key.value for key in entry.keys].index("kind")] if entry is not None else None
        if name == "journal" and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self":
            return call.args[0] if call.args else next((word.value for word in call.keywords if word.arg == "kind"), None)
        for sequence in [call.args, *(node.elts for node in call.args if isinstance(node, (ast.List, ast.Tuple)))]:
            words = [node.value if isinstance(node, ast.Constant) else None for node in sequence]
            if "journal" in words and "--kind" in words[words.index("journal"):]:
                at = words.index("--kind", words.index("journal"))
                return sequence[at + 1] if at + 1 < len(sequence) else None
        return None

    def _entry(self, call: ast.Call) -> ast.Dict | None:
        """The entry a call under the lock appends, as the dict literal its function builds."""
        named = call.args[3] if len(call.args) > 3 else None
        body = self.functions.get(self.owner.get(call, ""))
        if not isinstance(named, ast.Name) or body is None:
            return None
        built = [item.value for item in ast.walk(body) if isinstance(item, ast.Assign) and isinstance(item.value, ast.Dict)
                 for target in item.targets if isinstance(target, ast.Name) and target.id == named.id]
        keys = [[key.value for key in entry.keys if isinstance(key, ast.Constant)] for entry in built]
        return built[0] if len(built) == 1 and "kind" in keys[0] else None

    def _sentence(self, call: ast.Call) -> bool:
        if any(word.arg == SENTENCE for word in call.keywords):
            return True
        entry = self._entry(call) if isinstance(call.func, ast.Name) and call.func.id == LOCKED else None
        if entry is not None and SENTENCE in [key.value for key in entry.keys if isinstance(key, ast.Constant)]:
            return True
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr == "journal" and (
                len(call.args) > 2 or len(call.args) == 2 and _worded(call.args[1])):
            # The runner's helper takes (kind, subject): words where its subject goes are its old sentence.
            return True
        return f"--{SENTENCE}" in _constants(call)

    def _walk(self, call: ast.Call, function: str, seen: set[str], kind: ast.expr | None = None) -> None:
        line = call.lineno
        if (self.name, function) in EXEMPT or (call.lineno, call.col_offset) in self.walked:
            return
        self.walked.add((call.lineno, call.col_offset))
        if self._sentence(call):
            self.problems.append(f"{self.name}:{line}: {function} passes a sentence")
        expression = self._shape(call) if kind is None else kind
        try:
            if expression is None:
                raise Unresolved("no kind")
            kinds = self._resolve(expression, function)
        except _Helper as helper:
            # A kind passed on from a parameter: each call to the helper is a write.
            if function in seen:
                self.problems.append(f"{self.name}:{line}: {function} passes its kind round in a loop")
                return
            self._through(function, helper.index, helper.default, seen | {function})
            return
        except Unresolved as error:
            self.problems.append(f"{self.name}:{line}: {function} writes a kind outside its rules ({error})")
            return
        unknown = sorted(kinds - self.kinds)
        if unknown:
            self.problems.append(f"{self.name}:{line}: {function} writes {', '.join(unknown)}, which the registry does not list")
            return
        self.writes.append((function, frozenset(kinds), line))

    def _through(self, helper: str, index: int, default: ast.expr | None, seen: set[str]) -> None:
        short = helper.rsplit(".", 1)[-1]
        method = "." in helper
        calls = [node for node in self.calls if (
            (isinstance(node.func, ast.Name) and node.func.id == short and not method)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == short and method))]
        parameter = self._parameters(self.functions[helper])[index]
        for call in calls:
            caller = self.owner.get(call, "<module>")
            if caller == helper:
                continue
            position = index - (1 if method else 0)
            given = next((word.value for word in call.keywords if word.arg == parameter.arg), None)
            if given is None and position < len(call.args):
                given = call.args[position]
            given = given if given is not None else default
            if given is None:
                self.problems.append(f"{self.name}:{call.lineno}: {caller} calls {helper} with no kind")
                continue
            self._walk(call, caller, seen, kind=given)

    @staticmethod
    def _parameters(function: ast.FunctionDef) -> list[ast.arg]:
        return [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]

    def _resolve(self, node: ast.expr, function: str) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return self._resolve(node.body, function) | self._resolve(node.orelse, function)
        if isinstance(node, ast.JoinedStr):
            made = {""}
            for part in node.values:
                values = ({part.value} if isinstance(part, ast.Constant)
                          else self._resolve(part.value, function) if isinstance(part, ast.FormattedValue) else None)
                if values is None:
                    raise Unresolved("an f-string part")
                made = {left + right for left in made for right in values}
            return made
        if isinstance(node, ast.Name):
            body = self.functions.get(function)
            if body is not None:
                assigned = [item.value for item in ast.walk(body) if isinstance(item, ast.Assign)
                            for target in item.targets if isinstance(target, ast.Name) and target.id == node.id]
                if assigned:
                    return set().union(*(self._resolve(value, function) for value in assigned))
                parameters = self._parameters(body)
                names = [parameter.arg for parameter in parameters]
                if node.id in names:
                    index = names.index(node.id)
                    defaults = [*body.args.defaults]
                    positional = [*body.args.posonlyargs, *body.args.args]
                    default = None
                    if index < len(positional):
                        offset = index - (len(positional) - len(defaults))
                        default = defaults[offset] if offset >= 0 else None
                    else:
                        default = body.args.kw_defaults[index - len(positional)]
                    raise _Helper(index, default)
            if node.id in self.constants:
                return self._resolve(self.constants[node.id], "<module>")
            raise Unresolved(f"the name {node.id}")
        raise Unresolved(type(node).__name__)

    def counted(self) -> Counter:
        return Counter(_key(function, set(kinds)) for function, kinds, _ in self.writes)


class _Helper(Exception):
    def __init__(self, index: int, default: ast.expr | None) -> None:
        super().__init__(index)
        self.index, self.default = index, default


def registered() -> set[str]:
    return set(json.loads(REGISTRY.read_text(encoding="utf-8"))["kinds"])


def walk(name: str, source: str, kinds: set[str] | None = None) -> Walk:
    return Walk(name, source, registered() if kinds is None else kinds)


def problems(name: str, source: str, inventory: Counter | None = None) -> list[str]:
    """What the walk finds wrong with one script: its calls, and its count against the inventory."""
    found = walk(name, source)
    said = list(found.problems)
    expected = INVENTORY[name] if inventory is None else inventory
    counted = found.counted()
    for key in sorted(set(counted) | set(expected)):
        if counted[key] != expected[key]:
            said.append(f"{name}: {key[0]} writes {key[1]} {counted[key]} times; the inventory holds {expected[key]}")
    return said


def _worded(node: ast.expr) -> bool:
    """Words written out: a string literal or an f-string."""
    return isinstance(node, ast.JoinedStr) or isinstance(node, ast.Constant) and isinstance(node.value, str)


def _constants(call: ast.Call) -> set[str]:
    """Every string a call's arguments hold, however deep: in a list, a starred list or either branch of a choice."""
    return {node.value for argument in call.args for node in ast.walk(argument)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def sentences_in_tests(path: Path, source: str) -> list[str]:
    """A test that journals a sentence: ``summary=`` to append_journal, or ``--summary`` on a journal command line."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name == "append_journal" and any(word.arg == SENTENCE for word in node.keywords):
            found.append(f"{path.name}:{node.lineno}: passes a sentence to append_journal")
        held = _constants(node)
        if "journal" in held and f"--{SENTENCE}" in held:
            found.append(f"{path.name}:{node.lineno}: passes --{SENTENCE} to the journal command")
    return found


class JournalShapeWalkTests(unittest.TestCase):
    """Every write in the three scripts and in tests/ holds to its kind's rules, and the walk can tell when one doesn't."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = {name: path.read_text(encoding="utf-8") for name, path in SCRIPTS.items()}

    def test_every_write_in_the_three_scripts_is_a_registered_kind_with_no_sentence_and_as_the_inventory_holds(self) -> None:
        for name, source in self.sources.items():
            with self.subTest(script=name):
                self.assertEqual(problems(name, source), [])

    def test_no_test_journals_a_sentence(self) -> None:
        found = []
        for path in sorted((ROOT / "tests").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            # Only a file that names a sentence and the journal can pass one; the rest are not parsed.
            if f"--{SENTENCE}" in source or ("append_journal" in source and f"{SENTENCE}=" in source):
                found += sentences_in_tests(path, source)
        self.assertEqual(found, [])

    def planted(self, name: str, anchor: str, line: str) -> list[str]:
        """The walk's problems once ``line`` is planted at the top of the function ``anchor`` begins."""
        source = self.sources[name]
        start = source.index(anchor)
        body = source.index("\n", source.index(":\n", start)) + 1
        indent = " " * (len(source[body:]) - len(source[body:].lstrip(" ")))
        return problems(name, source[:body] + indent + line + "\n" + source[body:])

    def test_a_planted_call_in_each_shape_and_through_a_helper_fails_for_a_sentence_an_unruled_kind_and_an_uncounted_call(self) -> None:
        # Each planted call writes finish.met, which none of these functions writes: it is a call the inventory does
        # not hold, and, given a sentence, a call that passes one.
        shapes = {
            "append_journal": ("delivery_state.py", "def accept_handoff(",
                               "append_journal(directory, kind={kind}, data={{}}{sentence})", ', summary="Said"'),
            "Runner.journal": ("merge_queue.py", "    def carry(", "self.journal({kind}, None, data={{}}{sentence})", ', summary="Said"'),
            "the journal command": ("run_delivery.py", "def lead_flake(",
                                    'state_tool("journal", "--kind", {kind}, "--data", "{{}}"{sentence})', ', "--summary", "Said"'),
            "a helper": ("run_delivery.py", "def note_posture(", "journal_posture(c, {kind}, {{}})", ""),
        }
        for shape, (name, anchor, call, sentence) in shapes.items():
            with self.subTest(shape=shape, fails_for="a sentence, and a call the inventory does not hold"):
                said = self.planted(name, anchor, call.format(kind='"finish.met"', sentence=sentence))
                self.assertTrue(any("finish.met 1 times; the inventory holds 0" in problem for problem in said), said)
                if sentence:
                    self.assertTrue(any("passes a sentence" in problem for problem in said), said)
            with self.subTest(shape=shape, fails_for="a kind outside its rules"):
                kind, expected = (("args.kind", "outside its rules (Attribute)") if shape == "a helper"
                                  else ('"candidate.vanished"', "writes candidate.vanished, which the registry does not list"))
                said = self.planted(name, anchor, call.format(kind=kind, sentence=""))
                self.assertTrue(any(expected in problem for problem in said), (kind, said))
        # The runner's helper given its old sentence where its subject now goes, and a sentence in a starred choice on a
        # command line, each fail too.
        for name, anchor, call in (("merge_queue.py", "    def carry(", 'self.journal("finish.met", "Said", data={})'),
                                   ("merge_queue.py", "    def carry(", 'self.journal("finish.met", f"Said {self.base}", data={})'),
                                   ("run_delivery.py", "def lead_flake(",
                                    'state_tool("journal", "--kind", "finish.met", "--data", "{}", *(["--summary", "Said"] if c else []))')):
            with self.subTest(planted=call):
                said = self.planted(name, anchor, call)
                self.assertTrue(any("passes a sentence" in problem for problem in said), said)
        # A helper that passes on a sentence with the kind it was given fails at its own call.
        helper = self.sources["run_delivery.py"].replace(
            'state_tool("journal", "--kind", kind, "--data", json.dumps(facts))',
            'state_tool("journal", "--kind", kind, "--data", json.dumps(facts), "--summary", "Said")')
        self.assertNotEqual(helper, self.sources["run_delivery.py"])
        said = problems("run_delivery.py", helper)
        self.assertTrue(any("journal_posture passes a sentence" in problem for problem in said), said)

    def test_a_choosing_call_takes_only_kinds_it_can_choose_between(self) -> None:
        # The selection chooses between its two kinds; a third that is not a literal is outside its rules.
        source = self.sources["delivery_state.py"]
        chosen = 'kind="check.selection.cleared" if clearing else "check.selected"'
        self.assertIn(chosen, source)
        said = problems("delivery_state.py", source.replace(chosen, 'kind="check.selection.cleared" if clearing else str(item)'))
        self.assertTrue(any("select_checks writes a kind outside its rules (Call)" in problem for problem in said), said)

    def test_the_exempt_command_and_the_counts_are_what_the_walk_holds(self) -> None:
        # The journal command's own call passes the kind it was given, and only it is exempt.
        source = self.sources["delivery_state.py"]
        self.assertIn("kind=args.kind", source)
        moved = source.replace("def main(", "def main_elsewhere(")
        said = problems("delivery_state.py", moved)
        self.assertTrue(any("main_elsewhere writes a kind outside its rules" in problem for problem in said), said)
        # A write removed is a count the inventory no longer matches.
        gone = source.replace('append_journal(directory, kind="handoff.rejected"', 'print(directory, kind="handoff.rejected"')
        self.assertIn("delivery_state.py: reject_handoff writes handoff.rejected 0 times; the inventory holds 1",
                      problems("delivery_state.py", gone))

    def test_a_test_that_journals_a_sentence_is_found(self) -> None:
        for planted, expected in (
                ('state_tool.append_journal(directory, kind="flake.declared", data={}, summary="Said")', "passes a sentence to append_journal"),
                ('self.tool("journal", "--kind", "flake.declared", "--summary", "Said")', "passes --summary to the journal command"),
                ('subprocess.run([*tool, "journal", "--kind", "flake.declared", "--summary", "Said"])', "passes --summary to the journal command"),
                ('self.tool("journal", "--kind", "flake.declared", *(["--summary", "Said"] if said else []))', "passes --summary to the journal command")):
            with self.subTest(planted=planted):
                found = sentences_in_tests(Path("test_planted.py"), planted)
                self.assertEqual(len(found), 1, found)
                self.assertIn(expected, found[0])
        self.assertEqual(sentences_in_tests(Path("test_planted.py"),
                                            'self.tool("journal", "--kind", "flake.declared", "--data", "{}")'), [])


if __name__ == "__main__":
    unittest.main()
