"""The one projection from review outcomes to `roundDetail`, as a plan review uses it, and C4a's fitting.

The native review cards' use of the same projection is held by the existing native-review tests and goldens, which pass
unchanged across the move. Here: a plan review's fixed closures carry the commit of the round that reports them closed,
and whatever the review's length, the fitted `planReview` passes the plan validator within 256 KiB.
"""

from __future__ import annotations

import unittest

from tests.delivery_views_fixtures import views_common
import review_projection  # noqa: E402  (beside views_common, on the path the fixtures put first)


A, B = "lens.outcome-correctness", "lens.adversarial-testing"
COMMITS = {1: "1" * 40, 2: "2" * 40, 3: "3" * 40}


def outcome(number: int, results: dict, *, candidate: str | None = None, aligned: bool = False) -> dict:
    """A plan-review round as `plan-review reduce` stores it: one obtained entry per lens."""
    entries = []
    for lens, (verdict, findings, closures) in results.items():
        ref = candidate or COMMITS[number]
        binding = {"subjectRef": "plan", "candidateRef": ref, "round": number}
        entries.append({"lens": lens, "state": "obtained", "binding": binding, "result": {
            "schemaVersion": "review-lens-result/1", "lensId": lens, **binding, "outcome": verdict,
            "findings": findings, "resolvedFindings": closures, "evidence": ["the plan"]}})
    return {"round": number, "outcome": "aligned" if aligned else "unresolved", "finishedAt": f"2026-10-30T1{number}:00:00Z",
            "entries": entries}


def finding(ident: str, severity: str = "P1", summary: str = "A finding.") -> dict:
    return {"id": ident, "severity": severity, "summary": summary, "state": "open"}


def closure(ident: str, state: str = "fixed", resolution: str = "Fixed in the plan.") -> dict:
    return {"id": ident, "state": state, "resolution": resolution}


def plan(review: dict) -> dict:
    """The smallest valid plan holding the review."""
    return {"schemaVersion": "delivery-plan/1", "updated": "2026-10-30T12:00:00Z",
            "outcome": {"summary": "s", "text": "t"}, "requiredLenses": [], "proofKinds": [], "units": [],
            "finish": [], "approved": {"at": "2026-10-30T12:00:00Z", "snapshot": {"units": [], "finish": []}},
            "changes": [], "evidence": {}, "planReview": review}


class PlanReviewProjectionTests(unittest.TestCase):
    def test_each_fixed_closure_carries_the_commit_of_the_round_that_reports_it_closed(self) -> None:
        outcomes = [
            outcome(1, {A: ("changes-requested", [finding("oc-r1-1"), finding("oc-r1-2", "P2", "Line one\nline two")], []),
                        B: ("aligned", [], [])}),
            outcome(2, {A: ("changes-requested", [finding("oc-r2-1")],
                            [closure("oc-r1-1"), closure("oc-r1-2", "withdrawn", "Not a problem.")]),
                        B: ("aligned", [], [])}),
            outcome(3, {A: ("aligned", [], [closure("oc-r2-1", "closed", "Round three confirms it.")]),
                        B: ("aligned", [], [])}, aligned=True),
        ]
        rounds = review_projection.round_detail(outcomes, COMMITS)
        self.assertEqual(rounds, [
            {"round": 1, "candidateRef": "1" * 40, "at": "2026-10-30T11:00:00Z", "outcome": "changes",
             "findings": {"P0": 0, "P1": 1, "P2": 1, "P3": 0},
             "verdicts": {A: "changes", B: "approve"},
             "raised": [{"id": "oc-r1-1", "lens": A, "severity": "P1", "summary": "A finding.", "state": "resolved"},
                        {"id": "oc-r1-2", "lens": A, "severity": "P2", "summary": "Line one line two",
                         "state": "resolved"}],
             "settled": []},
            {"round": 2, "candidateRef": "2" * 40, "at": "2026-10-30T12:00:00Z", "outcome": "changes",
             "findings": {"P0": 0, "P1": 1, "P2": 0, "P3": 0},
             "verdicts": {A: "changes", B: "approve"},
             "raised": [{"id": "oc-r2-1", "lens": A, "severity": "P1", "summary": "A finding.", "state": "resolved"}],
             "settled": [{"id": "oc-r1-1", "lens": A, "state": "fixed", "fixCommit": "2" * 40,
                          "resolution": "Fixed in the plan."},
                         {"id": "oc-r1-2", "lens": A, "state": "dropped", "fixCommit": None,
                          "resolution": "Not a problem."}]},
            {"round": 3, "candidateRef": "3" * 40, "at": "2026-10-30T13:00:00Z", "outcome": "approve",
             "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
             "verdicts": {A: "approve", B: "approve"},
             "raised": [],
             "settled": [{"id": "oc-r2-1", "lens": A, "state": "fixed", "fixCommit": "3" * 40,
                          "resolution": "Round three confirms it."}]},
        ])
        review = review_projection.fit_plan_review(rounds, [A, B], "aligned")
        self.assertEqual(review, {"lenses": [A, B], "state": "aligned", "rounds": rounds})
        views_common.validate_plan(plan(review))

    def test_a_round_over_thirty_findings_lists_the_worst_thirty_and_counts_the_rest(self) -> None:
        findings = [finding(f"oc-r1-{i}", "P3" if i < 10 else "P1") for i in range(35)]
        rounds = review_projection.round_detail([
            outcome(1, {A: ("changes-requested", findings, []), B: ("aligned", [], [])}),
        ], COMMITS)
        rounds[0]["settled"] = [{"id": f"x-{i}", "lens": A, "state": "fixed" if i == 30 else "open",
                                 "fixCommit": "1" * 40 if i == 30 else None, "resolution": "r"} for i in range(31)]
        review = review_projection.fit_plan_review(rounds, [A, B], "open")
        entry = review["rounds"][0]
        self.assertEqual(len(entry["raised"]), 30)
        self.assertEqual([note["severity"] for note in entry["raised"]], ["P1"] * 25 + ["P3"] * 5)
        self.assertEqual(entry["clipped"], {"findings": 5, "closures": 1, "texts": False})
        # The fixed closure is listed first and keeps its commit; an open one is the one left out.
        self.assertEqual(entry["settled"][0], {"id": "x-30", "lens": A, "state": "fixed", "fixCommit": "1" * 40,
                                               "resolution": "r"})
        self.assertEqual(entry["findings"], {"P0": 0, "P1": 25, "P2": 0, "P3": 10})
        views_common.validate_plan(plan(review))

    def test_a_fold_over_rounds_holding_over_thirty_closures_counts_them_all(self) -> None:
        outcomes, commits = [], {}
        for number in range(1, 35):
            commits[number] = f"{number:040x}"
            raised = [finding(f"oc-r{number}-{i}", "P2") for i in range(12)]
            carried = [closure(f"oc-r{number - 1}-{i}") for i in range(12)] if number > 1 else []
            outcomes.append(outcome(number, {A: ("changes-requested", raised, carried), B: ("aligned", [], [])},
                                    candidate=commits[number]))
        rounds = review_projection.round_detail(outcomes, commits)
        review = review_projection.fit_plan_review(rounds, [A, B], "open")
        entries = review["rounds"]
        self.assertEqual(len(entries), 32)
        self.assertEqual([entries[0]["round"], entries[1].get("fromRound"), entries[1]["round"], entries[2]["round"],
                          entries[-1]["round"]], [1, 2, 4, 5, 34])
        self.assertEqual(entries[1]["clipped"], {"findings": 36, "closures": 36, "texts": False})
        self.assertEqual(entries[1]["findings"], {"P0": 0, "P1": 0, "P2": 36, "P3": 0})
        self.assertEqual((entries[1]["raised"], entries[1]["settled"]), ([], []))
        self.assertEqual(entries[1]["verdicts"], {A: "changes", B: "approve"})
        self.assertEqual(entries[1]["candidateRef"], f"{4:040x}")
        views_common.validate_plan(plan(review))

    def test_the_worst_case_yields_32_entries_within_256_kib_keeping_every_listed_fix_commit(self) -> None:
        outcomes, commits = [], {}
        ident = lambda n, i: f"lens-r{n}-{i}-".ljust(64, "x")  # noqa: E731
        for number in range(1, 41):
            commits[number] = f"{number:040x}"
            raised = [finding(ident(number, i), "P1", "s" * 280) for i in range(32)]
            carried = [closure(ident(number - 1, i), "fixed" if i % 2 else "open", "r" * 600)
                       for i in range(32)] if number > 1 else []
            outcomes.append(outcome(number, {A: ("changes-requested", raised, carried), B: ("aligned", [], [])},
                                    candidate="c" * 2048))
            for entry in outcomes[-1]["entries"]:
                entry["binding"]["candidateRef"] = "c" * 2048
        rounds = review_projection.round_detail(outcomes, commits)
        review = review_projection.fit_plan_review(rounds, [A, B], "open")
        entries = review["rounds"]
        self.assertEqual(len(entries), 32)
        self.assertEqual((entries[1]["fromRound"], entries[1]["round"]), (2, 10))
        self.assertEqual([entry["round"] for entry in entries[2:]], list(range(11, 41)))
        self.assertLessEqual(views_common.plan_review_size(review), views_common.PLAN_REVIEW_BYTES)
        views_common.validate_plan(plan(review))
        # Every entry keeps its verdicts and counts; every closure it lists keeps its round's commit, fixed ones first.
        for entry in entries:
            self.assertEqual(entry["verdicts"], {A: "changes", B: "approve"})
            self.assertEqual(entry["findings"]["P1"], 32 * (9 if entry.get("fromRound") else 1))
            states = [note["state"] for note in entry["settled"]]
            self.assertEqual(states, sorted(states, key=lambda state: state != "fixed"))
            for note in entry["settled"]:
                self.assertEqual(note["fixCommit"], commits[entry["round"]] if note["state"] == "fixed" else None)
        # The newest entry lists what it can; whatever an entry doesn't list, it counts.
        self.assertTrue(entries[-1]["raised"])
        for entry in entries[2:]:
            listed = len(entry["raised"]) + entry.get("clipped", {}).get("findings", 0)
            self.assertEqual(listed, 32)
            closed = len(entry["settled"]) + entry.get("clipped", {}).get("closures", 0)
            self.assertEqual(closed, 32)

    def test_a_native_review_projects_as_it_did_before_the_move(self) -> None:
        # Without commits, the projection is the native cards': deferred issues, details flags and the reported commit.
        rounds = review_projection.round_detail([
            {"round": 1, "outcome": "unresolved", "entries": [
                {"lens": A, "state": "obtained", "binding": {"candidateRef": "c1"}, "result": {
                    "outcome": "changes-requested", "findings": [
                        {"id": "F1", "severity": "P2", "summary": "s", "state": "deferred", "deferredIssue": "FX-9"}],
                    "resolvedFindings": []}},
                {"lens": B, "state": "missing", "binding": {"candidateRef": "c1"}}]}])
        self.assertEqual(rounds, [{"round": 1, "candidateRef": "c1", "at": None, "outcome": "changes",
                                   "findings": {"P0": 0, "P1": 0, "P2": 1, "P3": 0},
                                   "verdicts": {A: "changes", B: "unavailable"},
                                   "raised": [{"id": "F1", "lens": A, "severity": "P2", "summary": "s",
                                               "state": "deferred", "deferredIssue": "FX-9"}],
                                   "settled": [], "detailsUnavailable": True}])


if __name__ == "__main__":
    unittest.main()
