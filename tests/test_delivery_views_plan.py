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


LENSES = ["lens.outcome-correctness", "lens.adversarial-testing"]
FIX = "b" * 40


def review_entry(number: int, verdicts: dict, *, raised: list | None = None, settled: list | None = None,
                 findings: dict | None = None, **extra) -> dict:
    """One `planReview.rounds` entry, as the projection writes it."""
    return {"round": number, "candidateRef": f"{number:040x}", "at": "2026-10-30T10:00:00Z",
            "outcome": "approve" if all(v == "approve" for v in verdicts.values()) else "changes",
            "verdicts": verdicts, "findings": findings or {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
            "raised": raised or [], "settled": settled or [], **extra}


def plan_review() -> dict:
    """Three rounds to alignment: round 1 raises two findings, round 2 fixes one and raises one, round 3 closes both."""
    changes = {"lens.outcome-correctness": "changes", "lens.adversarial-testing": "approve"}
    approve = {lens: "approve" for lens in LENSES}
    return {
        "lenses": list(LENSES),
        "state": "aligned",
        "rounds": [
            review_entry(1, changes, findings={"P0": 0, "P1": 2, "P2": 0, "P3": 0}, raised=[
                {"id": "oc-r1-1", "lens": "lens.outcome-correctness", "severity": "P1",
                 "summary": "The fold has no shape.", "state": "resolved"},
                {"id": "oc-r1-2", "lens": "lens.outcome-correctness", "severity": "P1",
                 "summary": "The cap conflicts.", "state": "resolved"}]),
            review_entry(2, changes, findings={"P0": 0, "P1": 1, "P2": 0, "P3": 0}, raised=[
                {"id": "oc-r2-1", "lens": "lens.outcome-correctness", "severity": "P1",
                 "summary": "A round is off by one.", "state": "resolved"}], settled=[
                {"id": "oc-r1-1", "lens": "lens.outcome-correctness", "state": "fixed", "fixCommit": FIX,
                 "resolution": "C2 gives the fold its shape."},
                {"id": "oc-r1-2", "lens": "lens.outcome-correctness", "state": "dropped", "fixCommit": None,
                 "resolution": "Not a conflict after all."}]),
            review_entry(3, approve, settled=[
                {"id": "oc-r2-1", "lens": "lens.outcome-correctness", "state": "fixed", "fixCommit": "c" * 40,
                 "resolution": "Scenario 6 reads rounds 2 to 10."}],
                clipped={"findings": 0, "closures": 0, "texts": False}),
        ],
    }


def folded_review() -> dict:
    """Round 1, one fold over rounds 2 to 4, then round 5: the shape C4a's first step writes."""
    changes = {"lens.outcome-correctness": "changes", "lens.adversarial-testing": "approve"}
    review = plan_review()
    review["state"] = "open"
    review["rounds"] = [
        review_entry(1, changes, findings={"P0": 0, "P1": 1, "P2": 0, "P3": 0}),
        review_entry(4, changes, fromRound=2, findings={"P0": 0, "P1": 3, "P2": 1, "P3": 0},
                     clipped={"findings": 4, "closures": 3, "texts": False}),
        review_entry(5, changes, findings={"P0": 0, "P1": 1, "P2": 0, "P3": 0}),
    ]
    return review


def with_review(review: dict) -> dict:
    document = plan()
    document["planReview"] = review
    return document


class PlanReviewContractTests(unittest.TestCase):
    """C2 and C3: the optional `planReview`, and `id` and `blocking` on approvals."""

    def test_a_plan_review_and_marked_approvals_are_accepted_whole(self) -> None:
        for review in (plan_review(), folded_review()):
            document = with_review(review)
            document["approvals"] = [{"id": "approval-1", "text": "The release", "state": "waiting", "blocking": True},
                                     {"id": "approval-2", "text": "The plan", "state": "done", "blocking": False},
                                     {"text": "By hand", "state": "waiting"}]
            self.assertEqual(views.validate_plan(copy.deepcopy(document)), document)
        ended = plan_review()
        ended["state"], ended["endedReason"] = "ended", "The operator stopped it: the findings stopped converging."
        views.validate_plan(with_review(ended))
        # A review still open after an amendment: the last round has a lens asking for changes.
        reopened = plan_review()
        reopened["state"] = "open"
        reopened["rounds"].pop()
        views.validate_plan(with_review(reopened))
        # An unavailable verdict keeps a review open; `at` and `outcome` may be null.
        unavailable = plan_review()
        unavailable["state"] = "open"
        unavailable["rounds"][-1].update(at=None, outcome=None)
        unavailable["rounds"][-1]["verdicts"]["lens.adversarial-testing"] = "unavailable"
        views.validate_plan(with_review(unavailable))

    def test_each_refusal_names_its_key_path_and_echoes_no_value(self) -> None:
        def change(edit):
            review = plan_review()
            edit(review)
            return with_review(review)

        def gap(review):
            review["rounds"][2]["round"] = 4

        def overlap(review):
            review["rounds"][1].update(round=3, fromRound=1)

        def fold_with_entries(review):
            review["rounds"][1]["fromRound"] = 2
            review["rounds"][1]["round"] = 3
            review["rounds"][2]["round"] = 4

        def fold_last(review):
            review["rounds"][2]["fromRound"] = 3
            review["rounds"][2]["round"] = 4

        def only_fold(review):
            review["rounds"] = [review_entry(3, {lens: "approve" for lens in LENSES}, fromRound=1)]

        cases = {
            "an unknown key": (lambda r: r.update({SECRET: 1}), "planReview: holds an unknown key"),
            "an unknown entry key": (lambda r: r["rounds"][0].update({SECRET: 1}), "planReview.rounds[0]: holds an unknown"),
            "an unknown state": (lambda r: r.update(state=SECRET), "planReview.state"),
            "aligned with a lens asking for changes": (lambda r: r["rounds"][2]["verdicts"].update(
                {"lens.adversarial-testing": "changes"}), "planReview.state"),
            "open with every lens approving": (lambda r: r.update(state="open"), "planReview.state"),
            "ended without a reason": (lambda r: r.update(state="ended"), "planReview.endedReason"),
            "a reason on an aligned review": (lambda r: r.update(endedReason="Stopped."), "planReview.endedReason"),
            "an overlong reason": (lambda r: r.update(state="ended", endedReason=SECRET + "x" * 600),
                                   "planReview.endedReason"),
            "no lens": (lambda r: r.update(lenses=[]), "planReview.lenses"),
            "thirteen lenses": (lambda r: r.update(lenses=[f"lens.{i}" for i in range(13)]), "planReview.lenses"),
            "a repeated lens": (lambda r: r.update(lenses=LENSES + LENSES[:1]), "planReview.lenses[2]"),
            "a malformed lens id": (lambda r: r["lenses"].append(SECRET + " x"), "planReview.lenses[2]"),
            "no round": (lambda r: r.update(rounds=[]), "planReview.rounds"),
            "a verdict for no lens": (lambda r: r["rounds"][0]["verdicts"].update({SECRET: "approve"}),
                                      "planReview.rounds[0].verdicts"),
            "a missing verdict": (lambda r: r["rounds"][0]["verdicts"].pop("lens.adversarial-testing"),
                                  "planReview.rounds[0].verdicts"),
            "an unknown verdict": (lambda r: r["rounds"][0]["verdicts"].update({"lens.adversarial-testing": SECRET}),
                                   "planReview.rounds[0].verdicts.lens.adversarial-testing"),
            "an unknown outcome": (lambda r: r["rounds"][0].update(outcome=SECRET), "planReview.rounds[0].outcome"),
            "a malformed time": (lambda r: r["rounds"][0].update(at=SECRET), "planReview.rounds[0].at"),
            "an overlong candidate": (lambda r: r["rounds"][0].update(candidateRef="x" * 2049),
                                      "planReview.rounds[0].candidateRef"),
            "a negative count": (lambda r: r["rounds"][0]["findings"].update(P1=-1), "planReview.rounds[0].findings"),
            "a count that isn't whole": (lambda r: r["rounds"][0]["findings"].update(P1=True),
                                         "planReview.rounds[0].findings"),
            "a missing severity": (lambda r: r["rounds"][0]["findings"].pop("P3"), "planReview.rounds[0].findings"),
            "a deferred finding": (lambda r: r["rounds"][0]["raised"][0].update(state="deferred"),
                                   "planReview.rounds[0].raised[0].state"),
            "a deferred issue": (lambda r: r["rounds"][0]["raised"][0].update(deferredIssue=SECRET),
                                 "planReview.rounds[0].raised[0]: holds an unknown"),
            "an unknown severity": (lambda r: r["rounds"][0]["raised"][0].update(severity=SECRET),
                                    "planReview.rounds[0].raised[0].severity"),
            "an overlong summary": (lambda r: r["rounds"][0]["raised"][0].update(summary=SECRET + "x" * 280),
                                    "planReview.rounds[0].raised[0].summary"),
            "a finding of no lens": (lambda r: r["rounds"][0]["raised"][0].update(lens="lens.other"),
                                     "planReview.rounds[0].raised[0].lens"),
            "a malformed finding id": (lambda r: r["rounds"][0]["raised"][0].update(id=SECRET + "/1"),
                                       "planReview.rounds[0].raised[0].id"),
            "a fixed closure without its commit": (lambda r: r["rounds"][1]["settled"][0].update(fixCommit=None),
                                                   "planReview.rounds[1].settled[0].fixCommit"),
            "a short fix commit": (lambda r: r["rounds"][1]["settled"][0].update(fixCommit=SECRET),
                                   "planReview.rounds[1].settled[0].fixCommit"),
            "a fix commit on a dropped closure": (lambda r: r["rounds"][1]["settled"][1].update(fixCommit=FIX),
                                                  "planReview.rounds[1].settled[1].fixCommit"),
            "a deferred closure": (lambda r: r["rounds"][1]["settled"][1].update(state="deferred"),
                                   "planReview.rounds[1].settled[1].state"),
            "an overlong resolution": (lambda r: r["rounds"][1]["settled"][1].update(resolution=SECRET + "x" * 600),
                                       "planReview.rounds[1].settled[1].resolution"),
            "a malformed clipped note": (lambda r: r["rounds"][2]["clipped"].update(findings=-1),
                                         "planReview.rounds[2].clipped"),
            "a gap": (gap, "planReview.rounds[2].round"),
            "an overlap": (overlap, "planReview.rounds[1]"),
            "a fold with listed entries": (fold_with_entries, "planReview.rounds[1]"),
            "a fold over one round": (lambda r: r["rounds"][1].update(fromRound=2), "planReview.rounds[1].fromRound"),
            "a fold as the last entry": (fold_last, "planReview.rounds[2]"),
            "a review that is one fold": (only_fold, "planReview.rounds[0]"),
            "a first entry past round 1": (lambda r: [e.update(round=e["round"] + 1) for e in r["rounds"]],
                                           "planReview.rounds[0].round"),
        }
        for name, (edit, path) in cases.items():
            with self.subTest(case=name):
                message = refusal(change(edit))
                self.assertTrue(message.startswith(path), message)
                self.assertNotIn(SECRET, message)

    def test_approval_ids_are_ids_and_unique_and_blocking_is_a_boolean(self) -> None:
        for edit, path in (
                (lambda a: a[0].update(id=SECRET + " x"), "approvals[0].id"),
                (lambda a: (a[0].update(id="approval-1"), a[1].update(id="approval-1")), "approvals[1].id"),
                (lambda a: a[0].update(blocking=SECRET), "approvals[0].blocking"),
                (lambda a: a[0].update({SECRET: True}), "approvals[0]: holds an unknown key")):
            document = plan()
            edit(document["approvals"])
            message = refusal(document)
            self.assertTrue(message.startswith(path), message)
            self.assertNotIn(SECRET, message)

    def test_each_cap_holds_at_its_limit_and_refuses_one_past_it(self) -> None:
        self.assertEqual((views.PLAN_REVIEW_LENSES, views.PLAN_REVIEW_ROUNDS, views.PLAN_REVIEW_NOTES,
                          views.PLAN_REVIEW_SUMMARY, views.PLAN_REVIEW_CANDIDATE, views.PLAN_REVIEW_BYTES),
                         (12, 32, 30, 280, 2048, 256 * 1024))
        lens = "lens.outcome-correctness"
        changes = {lens: "changes", "lens.adversarial-testing": "approve"}

        def full(count: int) -> dict:
            review = plan_review()
            review["state"] = "open"
            review["rounds"] = [review_entry(n, changes) for n in range(1, count + 1)]
            return review

        views.validate_plan(with_review(full(32)))
        self.assertTrue(refusal(with_review(full(33))).startswith("planReview.rounds:"))
        twelve = plan_review()
        twelve["lenses"] = [f"lens.{i}" for i in range(12)]
        for entry in twelve["rounds"]:
            entry["verdicts"] = {name: "approve" for name in twelve["lenses"]}
            entry["raised"], entry["settled"] = [], []
        views.validate_plan(with_review(twelve))
        for key, make in (("raised", lambda i: {"id": f"f{i}", "lens": lens, "severity": "P1", "summary": "s",
                                                "state": "unresolved"}),
                          ("settled", lambda i: {"id": f"f{i}", "lens": lens, "state": "open", "fixCommit": None,
                                                 "resolution": "r"})):
            review = full(2)
            review["rounds"][0][key] = [make(i) for i in range(30)]
            views.validate_plan(with_review(review))
            review["rounds"][0][key].append(make(30))
            self.assertTrue(refusal(with_review(review)).startswith(f"planReview.rounds[0].{key}:"))
        texts = plan_review()
        texts["rounds"][0]["raised"][0]["summary"] = "x" * 280
        texts["rounds"][1]["settled"][0]["resolution"] = "x" * 600
        texts["rounds"][0]["candidateRef"] = "x" * 2048
        views.validate_plan(with_review(texts))

    def test_the_serialized_review_is_held_to_256_kib(self) -> None:
        lens = "lens.outcome-correctness"
        review = plan_review()
        review["state"] = "open"
        changes = {lens: "changes", "lens.adversarial-testing": "approve"}
        review["rounds"] = [review_entry(n, changes, settled=[
            {"id": f"f{n}-{i}", "lens": lens, "state": "open", "fixCommit": None, "resolution": "x" * 600}
            for i in range(30)]) for n in range(1, 14)]
        size = views.plan_review_size(review)
        self.assertGreater(size, views.PLAN_REVIEW_BYTES)
        # Trim the last resolution until the review sits exactly at the cap, then one byte over it.
        last = review["rounds"][-1]["settled"]
        while views.plan_review_size(review) > views.PLAN_REVIEW_BYTES:
            excess = views.plan_review_size(review) - views.PLAN_REVIEW_BYTES
            entry = next(e for e in reversed(last) if len(e["resolution"]) > 1)
            entry["resolution"] = entry["resolution"][:max(1, len(entry["resolution"]) - excess)]
        self.assertEqual(views.plan_review_size(review), views.PLAN_REVIEW_BYTES)
        views.validate_plan(with_review(review))
        entry = next(e for e in last if len(e["resolution"]) < 600)
        entry["resolution"] += "x"
        self.assertEqual(views.plan_review_size(review), views.PLAN_REVIEW_BYTES + 1)
        self.assertTrue(refusal(with_review(review)).startswith("planReview: holds"))

    def test_todays_fixtures_and_a_plan_without_a_review_stay_valid(self) -> None:
        views.validate_plan(plan())
        for case in ("plan-in-app", "plan-record-only"):
            path = PRODUCT_ROOT / "tests/fixtures/delivery-views/line-map" / case / "plan.json"
            views.validate_plan(json.loads(path.read_text(encoding="utf-8")))

    def test_the_schema_holds_the_review_and_refuses_what_it_can_express(self) -> None:
        import jsonschema

        validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
        for review in (plan_review(), folded_review()):
            document = with_review(review)
            document["approvals"][0].update(id="approval-1", blocking=True)
            self.assertEqual(list(validator.iter_errors(document)), [])
        refused = {
            "an unknown review key": lambda r: r.update(extra=1),
            "an unknown state": lambda r: r.update(state="paused"),
            "thirteen lenses": lambda r: r.update(lenses=[f"lens.{i}" for i in range(13)]),
            "33 rounds": lambda r: r.update(rounds=r["rounds"] * 11),
            "a deferred finding": lambda r: r["rounds"][0]["raised"][0].update(state="deferred"),
            "a short fix commit": lambda r: r["rounds"][1]["settled"][0].update(fixCommit="abc"),
            "an overlong summary": lambda r: r["rounds"][0]["raised"][0].update(summary="x" * 281),
            "an unknown verdict": lambda r: r["rounds"][0]["verdicts"].update({LENSES[0]: "maybe"}),
        }
        for name, edit in refused.items():
            with self.subTest(case=name):
                review = plan_review()
                edit(review)
                self.assertTrue(list(validator.iter_errors(with_review(review))), name)
                self.assertRaises(views.ConfigError, views.validate_plan, with_review(review))
        document = plan()
        document["approvals"][0]["blocking"] = "yes"
        self.assertTrue(list(validator.iter_errors(document)))


SKILL = PRODUCT_ROOT / "skills/delivery-views/SKILL.md"
LINE_MAP = PRODUCT_ROOT / "skills/delivery-views/assets/line-map.js"
# C7: what needs the engineer, in the order the chapter shows it, each with the words that make it the rule it is.
FLAGS = [
    ("The plan couldn't be read", ()),
    ("The base is broken", ("the last base event in the record is `base.broken`",
                            "While it holds, the title can't read \"Every criterion is proven\"")),
    ("Approvals are blocking work", ("an `approvals` entry is `blocking` and `waiting`", "The flag names each")),
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
