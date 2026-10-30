"""The plan chapter's input, `delivery-plan/1`: its schema, its validator and `check-plan`.

Any producer writes `<state directory>/plan.json`; the view reads nothing else about the plan. The validator refuses a
file it can't trust, naming the key path and never echoing a value from the file.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import agent_skills


# The product under test: this repository, or the extracted release candidate that qualification puts first.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
COMMON = PRODUCT_ROOT / "skills/delivery-views/scripts/views_common.py"
SCHEMA = PRODUCT_ROOT / "skills/delivery-views/references/delivery-plan.schema.json"

_spec = importlib.util.spec_from_file_location("views_common", COMMON)
views = importlib.util.module_from_spec(_spec)
sys.modules["views_common"] = views
_spec.loader.exec_module(views)

HEAD = "a" * 40
# A value no message may echo: each refusal case plants it where the file is wrong.
SECRET = "SENTINEL-not-to-be-echoed"


def plan() -> dict:
    """A valid plan: two units, one removed, one record kind and one producer kind, every optional part present."""
    units = [
        {"subject": "FX-2", "label": "S1", "title": "Show the greeting",
         "criteria": [{"id": "AC1", "text": "The page shows the greeting."},
                      {"id": "AC2", "text": "The greeting survives a reload."}],
         "proofs": ["review", "merged", "in-app"]},
        {"subject": "FX-3", "title": "Drop the banner", "removed": True,
         "criteria": [{"id": "AC3", "text": "No banner."}], "proofs": ["review"]},
    ]
    finish = [{"id": "acs", "label": "Criteria", "text": "Every criterion is proven", "at": "2026-10-30T10:00:00Z",
               "covers": {"criteria": "all", "proofs": ["review", "merged"]}},
              {"id": "evidence", "label": "Evidence", "text": "The in-app checks pass", "at": "2026-10-30T10:00:00Z",
               "covers": {"criteria": [], "proofs": ["in-app"]}}]
    return {
        "schemaVersion": "delivery-plan/1",
        "updated": "2026-10-30T12:00:00.123Z",
        "outcome": {"summary": "A greeting on the front page.", "text": "The front page greets every visitor."},
        "requiredLenses": ["lens.outcome-correctness", "lens.adversarial-testing"],
        "proofKinds": [{"id": "review", "label": "Review", "source": "record"},
                       {"id": "merged", "label": "Merged", "source": "record"},
                       {"id": "in-app", "label": "In-app", "source": "producer"}],
        "units": units,
        "finish": finish,
        "approved": {"at": "2026-10-30T09:00:00Z", "review": "docs/plans/fx.review.json",
                     "snapshot": {"units": copy.deepcopy(units[:1]), "finish": copy.deepcopy(finish[:1])}},
        "changes": [
            {"id": "c1", "at": "2026-10-30T11:00:00Z", "kind": "relaxed", "subjects": ["FX-2"], "criteria": ["AC2"],
             "by": "the operator", "why": "A reload is enough.", "before": "The greeting survives a restart.",
             "after": "The greeting survives a reload.", "ref": "https://tracker.test/FX-2"},
            {"id": "c2", "at": "2026-10-30T11:30:00Z", "kind": "finish", "subjects": [], "by": "unrecorded",
             "title": "Evidence line", "source": "plan-changes.jsonl",
             "before": {"criteria": "all", "proofs": []}, "after": {"criteria": [], "proofs": ["in-app"]}},
        ],
        "evidence": {"FX-2": {"in-app": {"state": "waived", "head": HEAD, "at": "2026-10-30T11:45:00Z",
                                         "reason": "No data in the fixture.", "items": ["needs-loaded"]}}},
        "approvals": [{"text": "The release", "state": "waiting", "who": "the operator"},
                      {"text": "The plan", "state": "done"}],
        "outOfScope": ["Editing the plan from the page."],
    }


def refusal(value: object) -> str:
    try:
        views.validate_plan(value)
    except views.ConfigError as error:
        return str(error)
    raise AssertionError("the plan was accepted")


class PlanContractTests(unittest.TestCase):
    def test_a_valid_plan_is_accepted_whole(self) -> None:
        document = plan()
        self.assertEqual(views.validate_plan(copy.deepcopy(document)), document)
        # The optional parts may be left out.
        for key in ("approvals", "outOfScope"):
            del document[key]
        del document["units"][0]["label"]
        self.assertEqual(views.validate_plan(copy.deepcopy(document)), document)

    def test_each_refusal_names_its_key_path_and_echoes_no_value(self) -> None:
        def change(edit):
            document = plan()
            edit(document)
            return document

        def overlong(document):
            document["units"][0]["criteria"][1]["text"] = SECRET + "x" * 600

        def done_without_head(document):
            entry = document["evidence"]["FX-2"]["in-app"]
            entry["state"] = "done"
            del entry["head"], entry["reason"]

        def over_count(document):
            document["changes"] = [{**document["changes"][0], "id": f"c{i}", "why": SECRET} for i in range(501)]

        cases = {
            "an unknown key": (lambda d: d["units"][0].update({SECRET: 1}), "units[0]: holds an unknown key"),
            "an unknown top-level key": (lambda d: d.update({SECRET: 1}), "(top level): holds an unknown key"),
            "an overlong text": (overlong, "units[0].criteria[1].text"),
            "an overlong outcome": (lambda d: d["outcome"].update(text=SECRET * 100), "outcome.text"),
            "an over-count list": (over_count, "changes"),
            "a duplicate criterion id": (lambda d: d["units"][1]["criteria"][0].update(id="AC1"),
                                         "units[1].criteria[0].id"),
            "an unknown record kind": (lambda d: d["proofKinds"].append(
                {"id": "deployed", "label": SECRET, "source": "record"}), "proofKinds[3].id"),
            "an unknown source": (lambda d: d["proofKinds"][2].update(source=SECRET), "proofKinds[2].source"),
            "a covers naming an unknown criterion": (lambda d: d["finish"][1]["covers"].update(
                criteria=["AC1", "AC9"]), "finish[1].covers.criteria[1]"),
            "a done entry without head": (done_without_head, "evidence.FX-2.in-app.head"),
            "a waived entry without reason": (lambda d: d["evidence"]["FX-2"]["in-app"].pop("reason"),
                                              "evidence.FX-2.in-app.reason"),
            "an evidence kind no unit owes": (lambda d: d["evidence"]["FX-2"].update(
                {"review": {"state": "done", "head": HEAD}}), "evidence.FX-2: names a kind"),
            "a proof owed of an undeclared kind": (lambda d: d["units"][0]["proofs"].append("clip"),
                                                   "units[0].proofs[3]"),
            "an owed proof that isn't text": (lambda d: d["units"][0]["proofs"].append({SECRET: 1}),
                                              "units[0].proofs[3]"),
            "a covered proof that isn't text": (lambda d: d["finish"][0]["covers"]["proofs"].append([SECRET]),
                                                "finish[0].covers.proofs[2]"),
            "a snapshot proof that isn't text": (lambda d: d["approved"]["snapshot"]["units"][0]["proofs"].append(
                [SECRET]), "approved.snapshot.units[0].proofs[3]"),
            "a missing required key": (lambda d: d["units"][0].pop("proofs"), "units[0].proofs: is required"),
            "a waived entry without head": (lambda d: d["evidence"]["FX-2"]["in-app"].pop("head"),
                                            "evidence.FX-2.in-app.head"),
            "a producer kind the unit doesn't owe": (lambda d: d["evidence"].update(
                {"FX-3": {"in-app": {"state": "todo"}}}), "evidence.FX-3: names a kind"),
            "a covered proof of an undeclared kind": (lambda d: d["finish"][0]["covers"].update(proofs=["clip"]),
                                                      "finish[0].covers.proofs[0]"),
            "a duplicate unit subject": (lambda d: d["units"][1].update(subject="FX-2"), "units[1].subject"),
            "a duplicate proof kind": (lambda d: d["proofKinds"].append(dict(d["proofKinds"][2])), "proofKinds[3].id"),
            "a duplicate change id": (lambda d: d["changes"][1].update(id="c1"), "changes[1].id"),
            "a duplicate owed proof": (lambda d: d["units"][0]["proofs"].append("review"), "units[0].proofs:"),
            "a malformed id": (lambda d: d["units"][0]["criteria"][0].update(id="AC 1"), "units[0].criteria[0].id"),
            "an unknown change kind": (lambda d: d["changes"][0].update(kind=SECRET), "changes[0].kind"),
            "a malformed time": (lambda d: d.update(updated=SECRET), "updated"),
            "a head that isn't a commit": (lambda d: d["evidence"]["FX-2"]["in-app"].update(head=SECRET),
                                           "evidence.FX-2.in-app.head"),
            "a control character": (lambda d: d["outOfScope"].append(SECRET + "\u0007"), "outOfScope[1]"),
            "a duplicate finish line": (lambda d: d["finish"][1].update(id="acs"), "finish[1].id"),
            "an evidence subject that isn't a unit": (lambda d: d["evidence"].update(
                {SECRET: d["evidence"]["FX-2"]}), "evidence: names a subject"),
            "a wrong schema version": (lambda d: d.update(schemaVersion=SECRET), "schemaVersion"),
            "a snapshot covers naming an unknown criterion": (lambda d: d["approved"]["snapshot"]["finish"][0][
                "covers"].update(criteria=["AC3"]), "approved.snapshot.finish[0].covers.criteria[0]"),
        }
        for name, (edit, path) in cases.items():
            with self.subTest(case=name):
                message = refusal(change(edit))
                self.assertTrue(message.startswith(path), message)
                self.assertNotIn(SECRET, message)
                self.assertNotIn("SENTINEL", message)

    def test_a_file_over_one_mebibyte_is_refused_unread(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan.json"
            path.write_text(json.dumps(plan()))
            self.assertEqual(views.read_plan(path), plan())
            exact = json.dumps(plan()).encode()
            path.write_bytes(exact + b" " * (views.PLAN_LIMIT_BYTES - len(exact)))
            self.assertEqual(path.stat().st_size, views.PLAN_LIMIT_BYTES)
            self.assertEqual(views.read_plan(path), plan())
            path.write_bytes(b"{" + b" " * views.PLAN_LIMIT_BYTES + b"}")
            with self.assertRaises(views.ConfigError) as refused:
                views.read_plan(path)
            self.assertIn("over the 1 MiB", str(refused.exception))
            # Refused by its size, before anything is parsed.
            path.write_bytes(b"x" * (views.PLAN_LIMIT_BYTES + 1))
            with self.assertRaises(views.ConfigError) as refused:
                views.read_plan(path)
            self.assertIn("over the 1 MiB", str(refused.exception))
            path.write_text("[" * 200_000)
            with self.assertRaises(views.ConfigError) as refused:
                views.read_plan(path)
            self.assertIn("nests too deeply", str(refused.exception))
            with self.assertRaises(views.ConfigError) as refused:
                views.read_plan(Path(folder))
            self.assertIn("couldn't be read", str(refused.exception))
            # A path under a regular file fails at stat, before anything is read.
            with self.assertRaises(views.ConfigError) as refused:
                views.read_plan(path / "plan.json")
            self.assertIn("couldn't be read", str(refused.exception))
            path.write_text("{not json " + SECRET)
            with self.assertRaises(views.ConfigError) as refused:
                views.read_plan(path)
            self.assertNotIn(SECRET, str(refused.exception))
        self.assertEqual(views.PLAN_LIMIT_BYTES, 1024 * 1024)

    def test_the_caps_are_the_contracts(self) -> None:
        self.assertEqual((views.PLAN_OUTCOME_TEXT, views.PLAN_TEXT, views.PLAN_LABEL), (2000, 600, 200))
        self.assertEqual((views.PLAN_UNITS, views.PLAN_CRITERIA, views.PLAN_CHANGES, views.PLAN_FINISH),
                         (200, 1000, 500, 30))
        self.assertEqual(views.PLAN_RECORD_KINDS, ("handed", "merged", "review"))
        # Each cap holds at its limit and refuses one past it.
        document = plan()
        document["outcome"]["text"] = "x" * 2000
        document["units"][0]["criteria"][0]["text"] = "x" * 600
        document["units"][0]["label"] = "x" * 200
        views.validate_plan(document)
        many = plan()
        many["units"] = [{"subject": f"S-{i}", "title": "t", "criteria": [{"id": f"C{i}-{j}", "text": "t"}
                                                                          for j in range(5)], "proofs": ["review"]}
                         for i in range(200)]
        many["evidence"] = {}
        views.validate_plan(many)
        many["units"].append({"subject": "S-x", "title": "t", "criteria": [], "proofs": []})
        self.assertTrue(refusal(many).startswith("units:"))
        for edit, path in (
                (lambda d: d.update(finish=[{**d["finish"][0], "id": f"f{i}"} for i in range(31)]), "finish:"),
                (lambda d: d["units"][0].update(label="x" * 201), "units[0].label"),
                (lambda d: d["units"][0]["criteria"][0].update(text="x" * 601), "units[0].criteria[0].text"),
                (lambda d: d["outcome"].update(text="x" * 2001), "outcome.text")):
            document = plan()
            edit(document)
            self.assertTrue(refusal(document).startswith(path), refusal(document))
        criteria = plan()
        criteria["units"] = [{"subject": f"S-{i}", "title": "t", "criteria": [{"id": f"C{i}-{j}", "text": "t"}
                                                                              for j in range(6)], "proofs": []}
                             for i in range(167)]
        criteria["evidence"] = {}
        self.assertIn("criteria", refusal(criteria))

    def test_the_schema_names_the_validators_fields(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "https://agent-skills.dev/schemas/delivery-plan/1")
        self.assertEqual(schema["properties"]["schemaVersion"], {"const": "delivery-plan/1"})
        self.assertEqual(set(schema["properties"]), set(views.PLAN_KEYS))
        self.assertEqual(set(schema["required"]), set(views.PLAN_REQUIRED))
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["$defs"]["proofKind"]["properties"]["source"]["enum"], ["record", "producer"])
        self.assertEqual(schema["$defs"]["change"]["properties"]["kind"]["enum"],
                         ["added", "removed", "relaxed", "changed", "finish"])
        self.assertEqual(schema["$defs"]["evidenceEntry"]["properties"]["state"]["enum"], ["done", "todo", "waived"])

    def test_the_schema_accepts_the_plan_and_refuses_what_it_can_express(self) -> None:
        import jsonschema

        validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
        jsonschema.Draft202012Validator.check_schema(validator.schema)
        self.assertEqual(list(validator.iter_errors(plan())), [])
        refused = {
            "an unknown nested key": lambda d: d["outcome"].update(extra=1),
            "an unknown top-level key": lambda d: d.update(extra=1),
            "an overlong summary": lambda d: d["outcome"].update(summary="x" * 601),
            "an overlong outcome": lambda d: d["outcome"].update(text="x" * 2001),
            "an overlong label": lambda d: d["units"][0].update(label="x" * 201),
            "31 finish lines": lambda d: d.update(finish=[{**d["finish"][0], "id": f"f{i}"} for i in range(31)]),
            "101 required lenses": lambda d: d.update(requiredLenses=[f"lens.{i}" for i in range(101)]),
            "an unknown change kind": lambda d: d["changes"][0].update(kind="moved"),
            "an unknown evidence state": lambda d: d["evidence"]["FX-2"]["in-app"].update(state="maybe"),
            "a malformed id": lambda d: d["units"][0]["criteria"][0].update(id="AC 1"),
        }
        for name, edit in refused.items():
            with self.subTest(case=name):
                document = plan()
                edit(document)
                self.assertTrue(list(validator.iter_errors(document)), name)
                self.assertRaises(views.ConfigError, views.validate_plan, document)

    def test_check_plan_passes_a_valid_file_and_fails_with_the_same_messages(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan.json"
            path.write_text(json.dumps(plan()))
            done = subprocess.run([sys.executable, "-B", str(COMMON), "check-plan", str(path)], text=True,
                                  capture_output=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout), {"ok": True, "plan": str(path), "units": 2, "criteria": 3})
            broken = plan()
            broken["finish"][1]["covers"]["criteria"] = ["AC9"]
            path.write_text(json.dumps(broken))
            done = subprocess.run([sys.executable, "-B", str(COMMON), "check-plan", str(path)], text=True,
                                  capture_output=True)
            self.assertEqual(done.returncode, 2)
            self.assertEqual(done.stdout, "")
            self.assertEqual(done.stderr, f"delivery-views: {refusal(broken)}\n")
            done = subprocess.run([sys.executable, "-B", str(COMMON), "check-plan", str(Path(folder) / "none.json")],
                                  text=True, capture_output=True)
            self.assertEqual(done.returncode, 2)
            self.assertIn("doesn't exist", done.stderr)
            # A file the validator can't even walk is still a message, never a traceback.
            for content in (json.dumps({**plan(), "units": [{**plan()["units"][0], "proofs": [{}]}]}), "[" * 200_000):
                path.write_text(content)
                done = subprocess.run([sys.executable, "-B", str(COMMON), "check-plan", str(path)], text=True,
                                      capture_output=True)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertNotIn("Traceback", done.stderr)


SKILL = PRODUCT_ROOT / "skills/delivery-views/SKILL.md"
LINE_MAP = PRODUCT_ROOT / "skills/delivery-views/assets/line-map.js"
# C7: what needs the engineer, in the order the chapter shows it, each with the words that make it the rule it is.
FLAGS = [
    ("The plan couldn't be read", ()),
    ("The base is broken", ("the last base event in the record is `base.broken`",
                            "While it holds, the title can't read \"Every criterion is proven\"")),
    ("Work in the record isn't in the plan", ("a queue or planned subject has no unit", "The flag names the subjects")),
    ("The plan and the record disagree on finish lines", ("a finish line in the plan that the record lacks, or the reverse",)),
    ("A finish line no longer covers the plan", ("its `covers.criteria` is an explicit, non-empty list that omits a criterion"
                                                 " added or changed after its `at`",
                                                 "Lines covering `\"all\"` or `[]` never raise it")),
    ("A change touches open work", ("a `relaxed`, `changed` or `removed` change names a unit that is neither proven nor removed",
                                    "It clears once that unit's review approves at a time after the change")),
    ("A change has no reason", ("its `why` is absent",)),
    ("A waived proof on a unit not yet proven", ()),
]


def section(text: str, heading: str) -> str:
    """One `###` section of the skill text, whitespace-normalized."""
    start = text.index(heading)
    end = text.find("\n#", start + len(heading))
    return " ".join(text[start:end if end >= 0 else None].split())


class PlanSkillTextTests(unittest.TestCase):
    """The skill text states the input's fields and the chapter's flags as the code has them."""

    def test_the_field_list_is_the_validators_and_the_schemas(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        table = text[text.index("### The input: `delivery-plan/1`"):text.index("### The chapter")]
        fields = [row.split("`")[1] for row in table.splitlines() if row.startswith("| `")]
        self.assertEqual(fields, list(views.PLAN_KEYS))
        self.assertEqual(fields, list(json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"]))
        optional = [key for key in views.PLAN_KEYS if key not in views.PLAN_REQUIRED]
        self.assertEqual([row.split("`")[1] for row in table.splitlines() if row.startswith("| `") and "| Optional" in row],
                         optional)
        normalized = " ".join(table.split())
        self.assertIn(f"Texts are capped at {views.PLAN_TEXT} characters, `outcome.text` at {views.PLAN_OUTCOME_TEXT:,}"
                      f" and labels at {views.PLAN_LABEL}; lists at {views.PLAN_UNITS} units, {views.PLAN_CRITERIA:,} criteria,"
                      f" {views.PLAN_CHANGES} changes and {views.PLAN_FINISH} finish lines.", normalized)
        self.assertIn("one JSON object, at most 1 MiB, refused unread if larger", normalized)
        self.assertEqual(views.PLAN_LIMIT_BYTES, 1024 * 1024)
        self.assertIn("`kind` one of " + ", ".join(f"`{kind}`" for kind in views.PLAN_CHANGE_KINDS[:-1])
                      + f" or `{views.PLAN_CHANGE_KINDS[-1]}`", normalized)
        self.assertIn("a record kind other than " + ", ".join(f"`{kind}`" for kind in views.PLAN_RECORD_KINDS[:-1])
                      + f" and `{views.PLAN_RECORD_KINDS[-1]}`", normalized)

    def test_the_flags_are_the_pages_in_the_contracts_order(self) -> None:
        text = section(SKILL.read_text(encoding="utf-8"), "### What needs the engineer")
        # Each numbered item, from its bold title to the next one: a rule is pinned under the flag it defines.
        items = text.split(". **")[1:]
        self.assertEqual([item.split("**")[0].rstrip(".") for item in items], [title for title, _ in FLAGS])
        self.assertIn("in this order, and with none it reads \"Nothing in the plan needs you right now.\"", text)
        for item, (title, rules) in zip(items, FLAGS):
            with self.subTest(flag=title):
                for rule in rules:
                    self.assertIn(rule, item)
        # The page draws exactly these titles, and the calm line, as the skill text names them.
        page = LINE_MAP.read_text(encoding="utf-8")
        for title, _ in FLAGS:
            with self.subTest(drawn=title):
                self.assertIn(f'"{title}"', page)
        self.assertIn('"Nothing in the plan needs you right now."', page)


if __name__ == "__main__":
    unittest.main()
