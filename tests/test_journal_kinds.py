from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

import agent_skills


ROOT = Path(__file__).resolve().parents[1]
# The product under test: this repository, or the extracted release candidate that
# qualification puts first on the path, so its shipped scripts are the ones exercised.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
SKILL = PRODUCT_ROOT / "skills/orchestrate-work"
SCRIPTS = SKILL / "scripts"
REGISTRY = SKILL / "references/journal-kinds.json"
SCHEMA_FILE = SKILL / "references/schemas.json"
VECTORS = ROOT / "tests/fixtures/journal-kinds/vectors.json"
HEAD = "4e66a0571b2c3d4e5f60718293a4b5c6d7e8f901"
BASE_HEAD = "9f3c2b1a0e9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b"
ROUND = {"round": 1, "startedAt": "2026-11-18T10:00:00Z", "finishedAt": "2026-11-18T10:20:00Z",
         "verdicts": {"lens.outcome-correctness": "approve"}, "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}}


def _load(name: str):
    # Importing the skill's modules here must not leave bytecode beside them.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


state_tool = _load("delivery_state")
words_tool = state_tool.journal_words


# The plan's table of kinds, as written there: altitude, tone, data (prose in *stars*, optional with ?),
# links (cause: the registry requires one; answers: the kinds it may answer; opens: a thread, or unless a
# flag), and tracker (where: heading, or - for nowhere).
TABLE = {
    "record.started": ("H", "note", "base", "", "epic: Delivery started"),
    "story.added": ("H", "decision", "*title* *why*", "", "epic: Story added"),
    "story.approved": ("S", "note", "*title* plan review?", "", "ticket: Plan approved"),
    "lane.assigned": ("S", "note", "*title* *note?*", "", "ticket: Started"),
    "review.round": ("S", "note", "round head? startedAt finishedAt verdicts findings refused? restarts?", "answers:review.started", "-"),
    "review.started": ("S", "note", "round head lenses restart moved? *why?*", "", "-"),
    "review.lens.returned": ("B", "note", "round lens verdict findings", "", "-"),
    "review.recorded": ("B", "note", "digest candidateRef", "", "-"),
    "review.carried": ("S", "decision", "from to digest baseCommits? *why*", "", "ticket: Review carried"),
    "candidate.handed": ("S", "ready", "head changeRequest? rounds? checks evidence? noChange? carry?", "answers:candidate.stopped",
                         "ticket: Handed off and queued"),
    "handoff.checked": ("S", "decision", "head rounds afterMerge noChange *notes*", "cause", "-"),
    "handoff.accepted": ("S", "decision", "settled", "", "ticket: Accepted"),
    "handoff.rejected": ("H", "problem", "*why*", "cause", "ticket: Hand-off rejected"),
    "handoff.requeued": ("S", "decision", "head", "answers:candidate.stopped", "ticket: Back in the queue"),
    "check.selected": ("S", "note", "head checks *why* added", "", "-"),
    "check.selection.cleared": ("S", "note", "", "", "-"),
    "selection.refused": ("H", "problem", "*detail*", "", "-"),
    "check.started": ("S", "note", "batch?", "", "-"),
    "check.finished": ("S", "note", "batch?", "cause", "-"),
    "check.failed": ("S", "problem", "check timedOut failing more log", "cause", "-"),
    "check.superseded": ("S", "decision", "base head by phase", "cause", "-"),
    "check.retried": ("S", "note", "check failing flake onBase", "", "ticket-or-epic: Check retried"),
    "check.reused": ("S", "note", "check head from", "", "-"),
    "check.carried": ("S", "note", "checks ranAgain base commits", "", "-"),
    "candidate.merged": ("H", "landed", "head recovery? acceptanceSkipped?", "", "ticket: Merged"),
    "candidate.stopped": ("H", "problem", "reason *detail* requeued? stage? leftOnBranch?", "opens-unless:requeued", "ticket: Stopped"),
    "base.rerun.started": ("S", "note", "checks base", "cause", "-"),
    "base.rerun.finished": ("S", "note", "base seconds verdict results", "cause", "-"),
    "base.rerun.stopped": ("S", "problem", "base head seconds *why*", "cause", "-"),
    "base.broken": ("H", "problem", "source reason? *detail?* head? after? check? test? *why?*", "opens",
                    "epic: The delivery base is broken; the queue is stopped"),
    "base.repaired": ("H", "landed", "source head repair? *why?*", "answers:base.broken", "epic: The delivery base is repaired"),
    "base.verified": ("S", "note", "source head after?", "", "-"),
    "base.flaky": ("S", "problem", "source head after? failing", "", "-"),
    "base.moved": ("S", "note", "from to merged *why*", "", "epic: Delivery base updated"),
    "integration.merged": ("S", "note", "target targetHead head conflicts", "", "-"),
    "integration.suite": ("S", "note", "head result seconds budgetSeconds? stopped?", "", "-"),
    "integration.suite.started": ("S", "note", "head startedAt expectedSeconds? budgetSeconds?", "", "-"),
    "integration.requested": ("S", "ready", "changeRequest head", "", "-"),
    "integration.landed": ("S", "landed", "target head", "", "-"),
    "base.published": ("B", "note", "base from", "", "-"),
    "base.merge.authorized": ("S", "decision", "reviewed baseCommits token by?", "", "-"),
    "ownership.granted": ("B", "note", "path span? base? movedFrom?", "", "-"),
    "ownership.revoked": ("B", "note", "path span? base? movedFrom?", "", "-"),
    "ownership.released": ("B", "note", "path span base merge", "", "-"),
    "ownership.carried": ("B", "note", "path from to was base story? inFlight? by?", "", "-"),
    "ownership.carved": ("B", "note", "path base removed added", "", "-"),
    "ownership.claimed": ("B", "note", "path base", "", "-"),
    "plan.updated": ("B", "note", "updated changed", "", "-"),
    "cleanup.done": ("B", "note", "base changeRequest? head?", "", "-"),
    "followup.filed": ("B", "note", "finding followup *title*", "", "-"),
    "work.cleared": ("B", "note", "from *why*", "", "-"),
    "snapshot.stale": ("B", "note", "source cause? exitCode?", "", "-"),
    "snapshot.retaken": ("B", "note", "source", "answers:snapshot.stale", "-"),
    "sibling.added": ("B", "decision", "sibling link", "", "-"),
    "sibling.removed": ("B", "decision", "sibling", "", "-"),
    "sibling.mismatched": ("B", "note", "sibling parent?", "", "-"),
    "sibling.landed": ("S", "landed", "sibling subject firstSeenAt", "", "-"),
    "evidence.published": ("S", "note", "url clips", "", "epic: Evidence published"),
    "flake.declared": ("S", "decision", "flake check head", "", "epic: Flake declared"),
    "fullhealth.skipped": ("H", "decision", "head posture source *why*", "", "epic: Full run skipped"),
    "fullhealth.rerun": ("S", "decision", "head *why*", "", "-"),
    "posture.changed": ("H", "decision", "from to source *why*", "", "epic: Posture changed"),
    "serving.held": ("H", "problem", "*why*", "", "epic: Serving is held"),
    "deferral.breached": ("H", "problem", "finding *title*", "", "ticket: Deferred note still open"),
    "blocker.recorded": ("H", "problem", "*text*", "opens", "ticket-or-epic: Blocked"),
    "gap.noted": ("S", "note", "class *text* *why* by release? generation? command? evidence? occurrenceOf?", "", "-"),
    "blocker.resolved": ("H", "landed", "*why*", "answers:blocker.recorded,deferral.breached",
                         "ticket-or-epic: Blocker resolved"),
    "decision.recorded": ("H", "decision", "*text* *why*", "", "ticket-or-epic: Decision"),
    "lease.granted": ("S", "note", "lease holder", "", "ticket-or-epic: Lease granted"),
    "lease.released": ("S", "note", "lease holder", "answers:lease.granted", "ticket-or-epic: Lease released"),
    "story.held": ("S", "decision", "*text*", "", "ticket: Waiting"),
    "story.unheld": ("S", "note", "", "answers:story.held", "ticket: Waiting"),
    "release.started": ("H", "note", "head", "", "-"),
    "release.finished": ("H", "landed", "head failedAt?", "cause", "epic: Released"),
    "finish.met": ("H", "landed", "finish evidence? by?", "", "epic: Finish line met"),
    "compounding.recorded": ("H", "decision", "outcome reference? *why?*", "", "epic: Compounding decided"),
    "hook.held": ("S", "note", "hook verdict lanes? pieces?", "", "-"),
    "peer.merged": ("S", "note", "peer head files", "", "-"),
    "seat.requested": ("B", "note", "seat label", "", "-"),
    "seat.admitted": ("B", "decision", "seat label approver", "answers:seat.requested", "-"),
    "seat.labeled": ("B", "note", "seat label", "", "-"),
    "partition.held": ("S", "problem", "seat reason", "", "-"),
    "partition.resumed": ("S", "landed", "seat", "answers:partition.held", "-"),
    "scope.transferred": ("S", "decision", "scope from to", "", "-"),
    "scope.requested": ("S", "note", "scope reason", "", "-"),
    "scope.declined": ("S", "decision", "scope", "answers:scope.requested", "-"),
    "plan.endorsed": ("H", "decision", "commit", "", "-"),
    "plan.seen": ("B", "note", "commit", "", "-"),
    "plan.rekeyed": ("S", "decision", "from to map", "", "-"),
    "deferral.held": ("B", "note", "finding *title*", "", "-"),
    "feedback.raised": ("S", "note", "anchor *text* blocking", "", "-"),
    "feedback.resolved": ("S", "decision", "outcome reason", "answers:feedback.raised", "-"),
    "queue.stopped": ("S", "note", "seat", "", "-"),
    "delivery.closed": ("H", "landed", "skipped?", "", "epic: Delivery closed"),
}
ALTITUDES = {"H": "headline", "S": "story", "B": "bookkeeping"}
PROSE = ["text", "why", "detail", "note", "notes", "title"]


def registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def vectors() -> list[dict]:
    return json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]


def unreached(described: dict, cases: list[dict]) -> list[str]:
    """What of the registry's words no vector reaches: a bracket kept or left out, a variant, or a form's edge."""
    reached: dict[str, set] = {}
    for case in cases:
        reached.setdefault(case["kind"], set()).update(words_tool.traced(case, described)[1])
    missing = []
    for name, kind in described["kinds"].items():
        seen = reached.get(name, set())
        for part in ("head", "line"):
            template = kind["words"][part]
            for key in words_tool.variants(template):
                if (part, "variant", key) not in seen:
                    missing.append(f"{name} {part}: no vector chooses {key or 'otherwise'}")
            for text in words_tool.templates(template):
                for index, _ in enumerate(words_tool.brackets(text)):
                    for kept in (True, False):
                        if (part, "bracket", text, index, kept) not in seen:
                            missing.append(f"{name} {part}: no vector {'keeps' if kept else 'leaves out'} [{index}] of {text!r}")
    edges = {(item[2], item[3]) for found in reached.values() for item in found if item[1] == "form"}
    for form, edge in [("count", 0), ("count", 1), ("count", 2), ("list", 0), ("list", 1), ("list", 2), ("list", 3),
                       ("duration", "under a minute"), ("duration", "over an hour"),
                       ("commit", None), ("lane", None), ("ticket", None)]:
        if (form, edge) not in edges:
            missing.append(f"no vector shows {form} at {edge}")
    return missing


class RegistryTests(unittest.TestCase):
    """The registry lists every kind in the plan's table, with each column's value, and its own rules hold."""

    def test_the_registry_has_every_kind_of_the_plans_table_with_each_columns_value(self) -> None:
        kinds = registry()["kinds"]
        self.assertEqual(sorted(kinds), sorted(TABLE))
        for name, (altitude, tone, data, links, tracker) in TABLE.items():
            kind = kinds[name]
            with self.subTest(kind=name):
                self.assertEqual(kind["altitude"], ALTITUDES[altitude])
                self.assertEqual(kind["tone"], tone)
                fields = {}
                for field in data.split():
                    prose = field.startswith("*")
                    field = field.strip("*")
                    fields[field.rstrip("?")] = (prose, field.endswith("?"))
                self.assertEqual({field: (spec["type"] in ("prose", "proseList"), spec.get("optional", False))
                                  for field, spec in kind["data"].items()}, fields)
                self.assertEqual(kind["because"], {"required": links == "cause"})
                answers = links.split(":", 1)[1].split(",") if links.startswith("answers:") else []
                self.assertEqual(kind["answers"], answers)
                opens = {"opens": True, "opens-unless:requeued": {"unless": "requeued"}}.get(links, False)
                self.assertEqual(kind["opensThread"], opens)
                expected = None if tracker == "-" else dict(zip(("posts", "heading"), tracker.split(": ", 1)))
                self.assertEqual(kind["tracker"], expected)
                self.assertTrue(kind["label"].strip())

    def test_the_registry_is_a_journal_kinds_document_in_canonical_form(self) -> None:
        text = REGISTRY.read_text(encoding="utf-8")
        described = json.loads(text)
        self.assertEqual(text, json.dumps(described, indent=2, ensure_ascii=False) + "\n")
        schema = state_tool.load_schema("journal-kinds/1")
        self.assertEqual(state_tool.validate(described, schema), [])
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(described)), [])
        self.assertEqual(described["prose"], PROSE)

    def test_no_kind_is_reserved_and_no_bookkeeping_kind_is_a_problem_or_posts(self) -> None:
        for name, kind in registry()["kinds"].items():
            with self.subTest(kind=name):
                self.assertFalse(name.startswith("x."))
                self.assertRegex(name, r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$")
                if kind["altitude"] == "bookkeeping":
                    self.assertNotEqual(kind["tone"], "problem")
                    self.assertIsNone(kind["tracker"])

    def test_every_field_is_typed_or_prose_by_its_name_and_every_type_is_a_schema(self) -> None:
        described = registry()
        for name, node in described["types"].items():
            with self.subTest(type=name):
                state_tool._check_schema(node, name, {"$defs": described["types"]})
        for name, kind in described["kinds"].items():
            for field, spec in kind["data"].items():
                with self.subTest(kind=name, field=field):
                    self.assertIn(spec["type"], described["types"])
                    # A field is prose exactly when it has a prose name.
                    self.assertEqual(spec["type"] in ("prose", "proseList"), field in described["prose"])

    def test_typed_fields_hold_their_closed_values(self) -> None:
        described, schemas = registry(), json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        reasons = schemas["merge-queue-result/1"]["$defs"]["reason"]["enum"]
        self.assertIn("interrupted", reasons)
        self.assertEqual(described["types"]["reason"]["enum"], reasons)
        self.assertEqual(described["types"]["stage"]["enum"],
                         schemas["delivery-state/1"]["properties"]["queue"]["items"]["properties"]["stage"]["enum"])
        self.assertEqual(described["types"]["commit"]["pattern"], "^[0-9a-f]{40}([0-9a-f]{24})?$")

    def test_links_name_kinds_the_registry_lists_and_only_the_three_open_threads(self) -> None:
        kinds = registry()["kinds"]
        for name, kind in kinds.items():
            for target in kind["answers"]:
                with self.subTest(kind=name, answers=target):
                    self.assertIn(target, kinds)
        self.assertEqual(sorted(name for name, kind in kinds.items() if kind["opensThread"]),
                         ["base.broken", "blocker.recorded", "candidate.stopped"])
        self.assertIn("requeued", kinds["candidate.stopped"]["data"])

    def test_every_template_uses_only_its_kinds_fields_in_the_closed_forms(self) -> None:
        for name, kind in registry()["kinds"].items():
            data = kind["data"]
            for part in ("head", "line"):
                template = kind["words"][part]
                if isinstance(template, dict):
                    with self.subTest(kind=name, part=part):
                        self.assertIn(template["by"], data)
                        self.assertTrue(template["cases"])
                for text in words_tool.templates(template):
                    outside = re.sub(r"\[[^\]]*\]", "", text)
                    for field, form, argument in words_tool.references(text):
                        with self.subTest(kind=name, part=part, field=field):
                            top = field.split(".")[0]
                            self.assertIn(top, [*data, "lane", "subject"])
                            self.assertIn(form, (None, *words_tool.FORMS))
                            # A count names its noun; a list may name its items' form; nothing else takes one.
                            if form == "count":
                                self.assertIsNotNone(argument)
                            elif form == "list":
                                self.assertIn(argument, (None, *words_tool.FORMS))
                            else:
                                self.assertIsNone(argument)
                    for field, _, _ in words_tool.references(outside):
                        top = field.split(".")[0]
                        # Outside brackets only what is always there: a required field, or the entry's lane or subject.
                        if top in data:
                            with self.subTest(kind=name, part=part, required=field):
                                self.assertFalse(data[top].get("optional"))

    def test_every_kind_has_a_tracker_instruction_that_agrees_with_where_it_posts_and_never_gates(self) -> None:
        for name, kind in registry()["kinds"].items():
            with self.subTest(kind=name):
                instruction = kind["instruction"]
                # It says why the kind posts nothing, or what its update holds where it posts.
                self.assertEqual(instruction.startswith("None: "), kind["tracker"] is None)
                if kind["tracker"] is not None:
                    self.assertTrue(instruction.startswith("On the "), instruction)
                self.assertTrue(instruction.endswith("."), instruction)
                # An update is never a gate: no instruction waits on, blocks or orders anything after itself.
                self.assertIsNone(re.search(r"\b(?:wait(?:s|ing|ed)?|block(?:s|ing)?|until|till|before|after|gate[sd]?|hold(?:s|ing)?)\b",
                                            instruction), instruction)

    def test_every_explanation_is_short_and_plain(self) -> None:
        for name, kind in registry()["kinds"].items():
            with self.subTest(kind=name):
                explain = kind["explain"]
                self.assertTrue(explain.endswith("."))
                self.assertLessEqual(len(re.findall(r"[.!?](?:\s|$)", explain)), 2)
                self.assertLessEqual(len(explain), 160)


class WordsTests(unittest.TestCase):
    """journal_words gives every vector's words, and the vectors reach every part of every kind's words."""

    def test_every_vector_is_an_entry_of_its_kind_and_journal_words_gives_its_words(self) -> None:
        described = registry()
        for index, case in enumerate(vectors()):
            with self.subTest(vector=index, kind=case["kind"]):
                kind = described["kinds"][case["kind"]]
                self.assertEqual(state_tool._errors(case["data"], state_tool.data_schema(kind), {"$defs": described["types"]}, ()), [])
                self.assertEqual(words_tool.words(case, described), {"head": case["head"], "line": case["line"]})
                self.assertTrue(case["head"].strip())

    def test_the_vectors_reach_every_bracket_both_ways_every_variant_and_each_forms_edges(self) -> None:
        self.assertEqual(unreached(registry(), vectors()), [])

    def test_a_bracket_or_variant_no_vector_reaches_is_named(self) -> None:
        planted = registry()
        planted["kinds"]["finish.met"]["words"]["line"] = "{finish}[ at {finish:ticket}]"
        planted["kinds"]["finish.met"]["words"]["head"] = {
            "by": "finish", "cases": {"acs": "A finish line was met"}, "otherwise": "Another finish line was met"}
        missing = unreached(planted, vectors())
        self.assertIn("finish.met line: no vector leaves out [0] of '{finish}[ at {finish:ticket}]'", missing)
        self.assertIn("finish.met head: no vector chooses otherwise", missing)

    def test_each_form_reads_at_its_edges(self) -> None:
        show = words_tool.show
        self.assertEqual([show(n, "count", "review round") for n in (0, 1, 2)],
                         ["0 review rounds", "1 review round", "2 review rounds"])
        self.assertEqual(show(["a", "b"], "count", "check"), "2 checks")
        self.assertEqual(show(3, "count", "fix|fixes"), "3 fixes")
        self.assertEqual([show(items, "list") for items in ([], ["a"], ["a", "b"], ["a", "b", "c"])],
                         ["none", "a", "a and b", "a, b and c"])
        self.assertEqual(show([HEAD, BASE_HEAD], "list", "commit"), "4e66a05 and 9f3c2b1")
        self.assertEqual([show(seconds, "duration") for seconds in (0, 59, 60, 250, 600, 3600, 6180, 172800)],
                         ["0s", "59s", "1m", "4m 10s", "10m", "1h", "1h 43m", "2d"])
        self.assertEqual([show(lane, "lane") for lane in ("lane-1", "lead", "lane-backend", "review")],
                         ["Lane 1", "Lead", "Backend lane", "Review lane"])
        self.assertEqual(show(HEAD, "commit"), "4e66a05")
        self.assertEqual([show(ticket, "ticket") for ticket in
                          ("APP-12", "https://linear.app/team/issue/APP-40/name-it", "https://example.org/x")],
                         ["APP-12", "APP-40", "https://example.org/x"])
        for value, form in ((True, None), ({"a": 1}, None), ("2", "count"), (-1, "duration"), ("a", "list"), ("x", "shout")):
            with self.subTest(value=value, form=form):
                with self.assertRaises(words_tool.WordsError):
                    show(value, form, "noun")

    def test_a_bracket_is_left_out_when_a_field_it_uses_is_absent_or_an_empty_list(self) -> None:
        described = registry()
        handed = {"kind": "candidate.handed", "lane": "lane-1", "subject": "APP-12",
                  "data": {"head": HEAD, "changeRequest": "https://example.org/pr/1", "rounds": 2, "checks": []}}
        self.assertEqual(words_tool.words(handed, described)["line"], "Ready after 2 review rounds")
        handed["data"]["checks"] = ["unit"]
        self.assertEqual(words_tool.words(handed, described)["line"], "Ready after 2 review rounds, with unit run by the lane")
        handed["data"]["rounds"] = 0
        self.assertEqual(words_tool.words(handed, described)["line"], "Ready, with unit run by the lane")
        merged = {"kind": "candidate.merged", "lane": None, "subject": "APP-12", "data": {"head": HEAD}}
        self.assertEqual(words_tool.words(merged, described), {"head": "APP-12 landed", "line": "At 4e66a05"})

    def test_words_are_refused_for_an_unknown_kind_or_a_required_field_the_entry_lacks(self) -> None:
        with self.assertRaises(words_tool.WordsError):
            words_tool.words({"kind": "candidate.vanished", "lane": None, "subject": None, "data": {}})
        with self.assertRaises(words_tool.WordsError):
            words_tool.words({"kind": "candidate.merged", "lane": None, "subject": "APP-12", "data": {}})


class PlantedRegistryTests(unittest.TestCase):
    """Every reader of a kind's description reads it through the one path, so a planted registry is what they see."""

    def test_the_state_tool_appends_a_planted_kind_and_journal_words_gives_a_planted_head(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        planted = registry()
        planted["kinds"]["piece.weighed"] = {**copy.deepcopy(planted["kinds"]["finish.met"]),
                                             "words": {"head": "{subject} was weighed", "line": "{finish}"}}
        planted["kinds"]["candidate.merged"]["words"]["head"] = "{subject} reached the main line"
        path = Path(temporary.name) / "journal-kinds.json"
        path.write_text(json.dumps(planted), encoding="utf-8")
        directory = Path(temporary.name) / "state"
        state_tool.init(directory, "Planted")
        weighed = {"kind": "piece.weighed", "subject": "APP-12", "data": {"finish": "acs"}}
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.append_journal(directory, **weighed)
        self.assertIn("no kind named piece.weighed", str(refused.exception))
        merged = {"kind": "candidate.merged", "lane": None, "subject": "APP-12", "data": {"head": HEAD}}
        with mock.patch.object(words_tool, "REGISTRY_FILE", path):
            entry = state_tool.append_journal(directory, **weighed)
            self.assertEqual(words_tool.words(entry), {"head": "APP-12 was weighed", "line": "acs"})
            self.assertEqual(words_tool.words(merged)["head"], "APP-12 reached the main line")
            self.assertEqual([item["kind"] for item in state_tool.read_journal(directory)], ["piece.weighed"])
        self.assertEqual(words_tool.words(merged)["head"], "APP-12 landed")


    def test_a_planted_registry_with_a_type_that_is_not_a_schema_is_refused(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        planted = registry()
        planted["types"]["flag"] = {"type": "strng"}
        path = Path(temporary.name) / "journal-kinds.json"
        path.write_text(json.dumps(planted), encoding="utf-8")
        directory = Path(temporary.name) / "state"
        state_tool.init(directory, "Planted")
        with mock.patch.object(words_tool, "REGISTRY_FILE", path):
            with self.assertRaises(state_tool.StateError) as refused:
                state_tool.append_journal(directory, kind="finish.met", data={"finish": "acs"})
        self.assertEqual(refused.exception.code, "registry_invalid")
        self.assertIn("unsupported type 'strng'", str(refused.exception))
        self.assertEqual(state_tool.read_journal(directory), [])


class JournalFactsTests(unittest.TestCase):
    """The record's scenarios: each refusal appends nothing and names what is wrong, beside its accepted form."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "state"
        state_tool.init(self.directory, "Facts")

    def append(self, kind: str, data: dict | None = None, **fields: object) -> dict:
        return state_tool.append_journal(self.directory, kind=kind, data=data, **fields)

    def refused(self, kind: str, data: dict | None, expected: str, code: str = "entry_invalid", **fields: object) -> None:
        before = state_tool.read_journal(self.directory)
        with self.assertRaises(state_tool.StateError) as refusal:
            self.append(kind, data, **fields)
        self.assertEqual(refusal.exception.code, code)
        self.assertIn(expected, str(refusal.exception))
        self.assertEqual(state_tool.read_journal(self.directory), before)

    def test_an_entry_whose_data_fits_its_kind_is_appended_and_one_that_does_not_is_refused(self) -> None:
        merged = {"head": HEAD}
        self.refused("candidate.merged", {}, "/data: missing head")
        self.refused("candidate.merged", {**merged, "landed": True}, "/data/landed: no value is allowed here")
        self.refused("candidate.vanished", merged, "no kind named candidate.vanished")
        entry = self.append("candidate.merged", merged, subject="APP-12")
        self.assertEqual({key: entry[key] for key in ("id", "kind", "lane", "subject", "data")},
                         {"id": 1, "kind": "candidate.merged", "lane": None, "subject": "APP-12", "data": merged})
        self.assertNotIn("summary", entry)

    def test_a_plan_approval_names_its_plan(self) -> None:
        approved = {"title": "Keep facts in the journal", "plan": "docs/plans/facts.md"}
        self.refused("story.approved", {"title": approved["title"]}, "/data: missing plan", subject="APP-12")
        self.refused("story.approved", {**approved, "review": ""}, "/data/review", subject="APP-12")
        entry = self.append("story.approved", {**approved, "review": "docs/plans/facts.review.json"}, subject="APP-12")
        self.assertEqual(entry["data"]["review"], "docs/plans/facts.review.json")

    def test_a_reserved_kind_is_refused_with_the_reservations_own_error(self) -> None:
        self.refused("x.weighed", {"finish": "acs"}, "x.weighed is reserved", code="kind_reserved")
        # Read back from a journal, the entry is named as reserved, not as unknown.
        entry = {"id": 1, "at": "2026-11-16T10:00:00Z", "kind": "x.weighed", "lane": None, "subject": None, "data": {}}
        self.assertEqual(state_tool.journal_errors([entry]), [
            "entry 1/kind: x.weighed is reserved: kinds beginning x. are an adopter's own, and this registry has none"])
        self.refused("x.weighed", {}, "kinds beginning x. are an adopter's own", code="kind_reserved", lane="lane-1")

    def test_typed_fields_are_refused_out_of_shape_and_accepted_in_it(self) -> None:
        cases = [
            ("candidate.merged", {"head": HEAD[:10]}, "/data/head: does not match", {"head": HEAD}),
            ("record.started", {"base": {"head": BASE_HEAD}}, "/data/base: missing ref",
             {"base": {"ref": "integrate/delivery-story", "head": BASE_HEAD}}),
            ("record.started", {"base": {"ref": "integrate/delivery-story"}}, "/data/base: missing head",
             {"base": {"ref": "integrate/delivery-story", "head": BASE_HEAD}}),
            ("candidate.stopped", {"reason": "gave_up", "detail": "It stopped."}, "/data/reason: not one of the allowed values",
             {"reason": "interrupted", "detail": "A run ended while APP-12 was checking."}),
            ("candidate.handed", {"head": HEAD, "changeRequest": "", "rounds": 1, "checks": []},
             "/data/changeRequest: shorter than 1",
             {"head": HEAD, "changeRequest": "pr-12", "rounds": 1, "checks": []}),
            # An integration review round names the candidate it read by its full commit, or no head at all.
            ("review.round", {**ROUND, "head": HEAD[:10]}, "/data/head: does not match", {**ROUND, "head": HEAD}),
            ("integration.suite", {"head": HEAD, "result": "timed-out", "seconds": 3}, "/data/result: not one of the allowed values",
             {"head": HEAD, "result": "over-budget", "seconds": 3, "budgetSeconds": 2}),
        ]
        for kind, wrong, expected, right in cases:
            with self.subTest(kind=kind, wrong=wrong):
                self.refused(kind, wrong, expected)
                self.assertEqual(self.append(kind, right, subject="APP-12")["data"], right)
        started = self.append("check.started", {}, subject="APP-12")
        checked = {"head": HEAD, "rounds": 1, "afterMerge": False, "noChange": False}
        self.refused("handoff.checked", {**checked, "notes": ["  "]}, "/data/notes/0: does not match", because=[started["id"]])
        self.assertEqual(self.append("handoff.checked", {**checked, "notes": ["Checked."]}, because=[started["id"]])["data"]["notes"],
                         ["Checked."])
        failed = {"check": "unit", "timedOut": False, "failing": [], "more": 0}
        for log in ("/tmp/unit.log", "unit.log", "logs/"):
            with self.subTest(log=log):
                self.refused("check.failed", {**failed, "log": log}, "/data/log: does not match", because=[started["id"]])
        log = "logs/20261116T101500Z-003-a1b2c3d4-unit.log"
        self.assertEqual(self.append("check.failed", {**failed, "log": log}, because=[started["id"]])["data"]["log"], log)

    def test_links_name_earlier_entries_the_registry_requires_or_pairs(self) -> None:
        started = self.append("check.started", {}, subject="APP-12")
        self.refused("check.finished", {}, "/because: a check.finished names what caused it")
        self.refused("check.finished", {}, "/because: entry 2 is not an earlier entry", because=[2])
        self.refused("check.finished", {}, "/because: entry 9 is not an earlier entry", because=[9])
        self.refused("check.finished", {}, "/because: names an entry twice", because=[1, 1])
        finished = self.append("check.finished", {}, because=[started["id"]])
        self.assertEqual(finished["because"], [1])
        handed = {"head": HEAD, "changeRequest": "https://github.com/example/app/pull/12", "rounds": 1, "checks": []}
        self.refused("candidate.handed", handed, "/answers: a candidate.handed does not answer a check.started", answers=[1])
        stopped = self.append("candidate.stopped", {"reason": "check_failed", "detail": "unit failed."}, because=[1])
        self.assertEqual(self.append("candidate.handed", handed, answers=[stopped["id"]])["answers"], [stopped["id"]])
        # A cause may be given on any entry.
        self.assertEqual(self.append("finish.met", {"finish": "acs"}, because=[2])["because"], [2])

    def test_a_decision_or_a_blocker_needs_its_words_and_a_decision_its_reason(self) -> None:
        for data, expected in (({"text": "Carry it"}, "/data: missing why"),
                               ({"text": "", "why": "It holds."}, "/data/text: does not match"),
                               ({"text": "Carry it", "why": "   "}, "/data/why: does not match"),
                               ({"text": " \n", "why": "It holds."}, "/data/text: does not match")):
            with self.subTest(data=data):
                self.refused("decision.recorded", data, expected)
        self.refused("blocker.recorded", {"text": "  "}, "/data/text: does not match")
        self.assertEqual(self.append("decision.recorded", {"text": "Carry it", "why": "It holds."})["data"]["why"], "It holds.")
        self.assertEqual(self.append("blocker.recorded", {"text": "The lead must choose."})["data"]["text"], "The lead must choose.")

    def test_what_the_registry_cannot_say_of_a_story_s_entries_is_refused_each_by_its_own_rule(self) -> None:
        def entry(kind: str, data: dict, **fields: object) -> dict:
            return {"id": 1, "at": "2026-11-16T10:00:00Z", "kind": kind, "lane": "lane-1", "subject": "APP-12", "data": data, **fields}
        authorized = {"reviewed": HEAD, "baseCommits": [BASE_HEAD], "token": "0" * 32}
        carry = {"from": HEAD, "to": BASE_HEAD, "digest": "a" * 64, "baseCommits": [BASE_HEAD]}
        handed = {"head": BASE_HEAD, "checks": [], "carry": carry}
        recovered = {"head": HEAD, "recovery": {"head": HEAD, "changeRequest": None}}
        # Each in its right form is accepted.
        for kind, data, fields in (("base.merge.authorized", authorized, {}), ("candidate.handed", handed, {}),
                                   ("candidate.merged", recovered, {"occurredAt": None}),
                                   ("review.recorded", {"digest": "a" * 64, "candidateRef": HEAD}, {})):
            with self.subTest(kind=kind):
                self.assertEqual(state_tool.journal_errors([entry(kind, data, **fields)]), [])
        for name, wrong, expected in (
                ("an authorization without its lane", entry("base.merge.authorized", authorized, lane=None),
                 "a base merge authorization names a lane and base commits"),
                ("an authorization with no base commits", entry("base.merge.authorized", {**authorized, "baseCommits": []}),
                 "a base merge authorization names a lane and base commits"),
                ("an authorization about no story", entry("base.merge.authorized", authorized, subject=None),
                 "a base.merge.authorized names its story"),
                ("a capture about no story", entry("review.recorded", {"digest": "a" * 64, "candidateRef": HEAD}, subject=None),
                 "a review.recorded names its story"),
                ("a hand-off's carry with no base commits", entry("candidate.handed", {**handed, "carry": {**carry, "baseCommits": []}}),
                 "a base merge carry names its base commits"),
                ("a recovery never recovered", entry("candidate.merged", recovered),
                 "only a recovered merge carries source identity")):
            with self.subTest(name):
                self.assertEqual(state_tool.journal_errors([wrong]), [f"entry 1: {expected}"])

    def test_an_entry_may_say_when_it_happened_but_not_after_it_was_journaled(self) -> None:
        started = {"id": 1, "at": "2026-11-16T10:00:00Z", "kind": "check.started", "lane": None, "subject": "APP-12", "data": {}}
        finished = {"id": 2, "at": "2026-11-16T10:05:00Z", "kind": "check.finished", "lane": None, "subject": "APP-12",
                    "occurredAt": "2026-11-16T10:04:00Z"}
        self.assertEqual(state_tool.journal_errors([started, {**finished, "data": {}, "because": [1]}]), [])
        self.assertEqual(state_tool.journal_errors([started, {**finished, "data": {}, "because": [1],
                                                              "occurredAt": "2026-11-16T10:06:00Z"}]),
                         ["entry 2: occurrence follows observation"])

    def test_the_state_tools_own_readers_take_entries_that_carry_only_facts(self) -> None:
        broken = self.append("base.broken", {"source": "lead", "detail": "unit failed on the main line."})
        repaired = self.append("base.repaired", {"source": "lead", "head": HEAD}, answers=[broken["id"]])
        self.assertEqual(repaired["answers"], [broken["id"]])
        stopped = self.append("candidate.stopped", {"reason": "base_broken", "detail": "The main line broke."}, subject="APP-12")
        self.assertNotIn("summary", stopped)
        rounds = {"round": 1, "startedAt": "2026-11-16T10:00:00Z", "finishedAt": "2026-11-16T10:30:00Z",
                  "verdicts": {"outcome-correctness": "approve"}, "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}}
        self.append("review.round", rounds, subject="APP-12", lane="lane-1")
        document = state_tool.show(self.directory)
        self.assertEqual([entry["kind"] for entry in document["journal"]],
                         ["base.broken", "base.repaired", "candidate.stopped", "review.round"])
        # A piece's stats count a round that carries only its facts.
        item = {"subject": "APP-12", "lane": "lane-1", "stage": "queued", "head": HEAD, "checks": [], "syncedHead": None}
        stats = state_tool._candidate_stats(item, [entry for entry in document["journal"] if entry["kind"] == "review.round"], [])
        self.assertEqual((stats["rounds"], stats["findings"]), (1, rounds["findings"]))

    def test_an_entry_in_the_older_shape_is_refused_naming_the_release_that_reads_it(self) -> None:
        older = "in the older journal shape, with a sentence or without data, which a 2.x release reads"
        self.refused("candidate.merged", None, "an entry carries its facts in data")
        for name in ("summary", "review", "recovery", "reviewArtifact", "reviewCarry", "baseMergeAuthorization"):
            with self.subTest(member=name), self.assertRaises(TypeError):
                self.append("candidate.merged", {"head": HEAD}, **{name: "Merged"})
        merged = {"id": 1, "at": "2026-11-16T10:00:00Z", "kind": "candidate.merged", "lane": None, "subject": "APP-12"}
        for entry in ({**merged, "summary": "Merged"}, {**merged, "summary": "Merged", "data": {"head": HEAD}},
                      {**merged, "kind": "review.evidence", "summary": "Captured",
                       "reviewArtifact": {"digest": "a" * 64, "candidateRef": HEAD}}):
            with self.subTest(entry=entry):
                errors = state_tool.journal_errors([entry])
                self.assertEqual(len(errors), 1)
                self.assertIn(older, errors[0])
        # A member this shape doesn't have is refused beside data, and the old capture's kind is no kind.
        self.assertIn("/review: no value is allowed here", state_tool.journal_errors([{**merged, "data": {"head": HEAD}, "review": {}}])[0])
        self.assertIn("no kind named review.evidence",
                      state_tool.journal_errors([{**merged, "kind": "review.evidence", "data": {"digest": "a" * 64, "candidateRef": HEAD}}])[0])
        # A record in the older shape is refused where it is read, and one whose journal is empty is read in either shape.
        self.assertEqual(state_tool.show(self.directory)["journal"], [])
        (self.directory / state_tool.JOURNAL_FILE).write_text(json.dumps({**merged, "summary": "Merged"}) + "\n")
        with self.assertRaises(state_tool.StateError) as refusal:
            state_tool.show(self.directory)
        self.assertEqual(refusal.exception.code, "journal_invalid")
        self.assertIn(older, str(refusal.exception))

    def test_the_journal_command_takes_facts_links_and_a_persons_words(self) -> None:
        def run(*argv: str) -> dict:
            out = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
            with contextlib.redirect_stdout(out):
                code = state_tool.main(["--dir", str(self.directory), *argv])
            out.flush()
            result = json.loads(out.buffer.getvalue().decode("ascii"))
            self.assertEqual(code == 0, result["ok"], result)
            return result
        started = run("journal", "--kind", "check.started", "--subject", "APP-12", "--data", "{}")["entry"]
        self.assertNotIn("summary", started)
        finished = run("journal", "--kind", "check.finished", "--data", '{"batch": ["APP-12"]}', "--because", "1")["entry"]
        self.assertEqual((finished["data"], finished["because"]), ({"batch": ["APP-12"]}, [1]))
        decided = run("journal", "--kind", "decision.recorded", "--text", "Carry the review", "--why", "It still holds.")["entry"]
        self.assertEqual((decided["data"], "summary" in decided), ({"text": "Carry the review", "why": "It still holds."}, False))
        # The command takes no sentence, no old review member and no old authorization member.
        for flag in ("--summary", "--review", "--base-merge-authorization"):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as refusal:
                state_tool.main(["--dir", str(self.directory), "journal", "--kind", "decision.recorded", "--text", "a",
                                 "--why", "b", flag, "Carried"])
            self.assertEqual(refusal.exception.code, 2)
        review = {"round": 2, "startedAt": "2026-11-16T10:00:00Z", "finishedAt": "2026-11-16T10:30:00Z",
                  "verdicts": {"adversarial-testing": "approve"}, "findings": {"P0": 0, "P1": 0, "P2": 1, "P3": 0}}
        rounded = run("journal", "--kind", "review.round", "--subject", "APP-12", "--lane", "lane-1",
                      "--data", json.dumps(review))["entry"]
        self.assertEqual((rounded["data"], "summary" in rounded, "review" in rounded), (review, False, False))
        unnamed = run("journal", "--kind", "review.round", "--data", json.dumps(review))
        self.assertEqual(unnamed["code"], "entry_invalid")
        self.assertIn("a review.round entry names the candidate it reviewed", unnamed["error"])
        for argv, expected in (
                (["--kind", "decision.recorded", "--text", "Carry the review"], "missing why"),
                (["--kind", "finish.met", "--text", "acs"], "--text and --why are for decision.recorded and blocker.recorded"),
                (["--kind", "decision.recorded", "--data", '{"why": "a"}', "--why", "b", "--text", "c"], "give why once"),
                (["--kind", "check.finished", "--data", "{}", "--because", "one"], "--because is entry ids"),
                (["--kind", "decision.recorded", "--data", "[1]", "--text", "a", "--why", "b"], "--data is a JSON object"),
                (["--kind", "candidate.merged"], "give the entry's facts with --data, or a person's words with --text and --why")):
            with self.subTest(argv=argv):
                result = run("journal", *argv)
                self.assertEqual(result["code"], "entry_invalid")
                self.assertIn(expected, result["error"])
        self.assertEqual(len(state_tool.read_journal(self.directory)), 4)


if __name__ == "__main__":
    unittest.main()
