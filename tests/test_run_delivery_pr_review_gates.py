"""A PR reviewer's gate: its verdict or its approval, met or unmet, and the members it needs from the adapter."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

import agent_skills

# The product under test: this repository, or the extracted release candidate qualification puts first on the path.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
GATES = PRODUCT_ROOT / "skills/run-delivery/scripts/pr_review_gates.py"

HEAD = "0123456789abcdef0123456789abcdef01234567"
OLDER = "fedcba9876543210fedcba9876543210fedcba98"


def load_gates():
    spec = importlib.util.spec_from_file_location("pr_review_gates_under_test", GATES)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


gates = load_gates()


def review(body: str) -> dict:
    return {"author": "Reviewer", "account": "{bot}", "at": "2026-11-11T10:00:00+00:00", "body": body}


class VerdictGateTests(unittest.TestCase):
    """The verdict is the body's first `**Verdict:**` line; its first word says approve or it doesn't."""

    def gate(self, body: str) -> dict:
        return gates.gate({"id": "claude", "gate": "verdict"}, review(body), {}, HEAD)

    def test_an_approving_verdict_is_met(self) -> None:
        for value in ("Approve", "Approved.", "APPROVE", "approve, with two nits", "**Approved**"):
            with self.subTest(value=value):
                verdict = self.gate(f"## Review\n\nLooks fine.\n\n**Verdict:** {value}\n")
                self.assertEqual(verdict["kind"], "verdict")
                self.assertTrue(verdict["met"], verdict)
                self.assertIn(value, verdict["why"])

    def test_any_other_verdict_is_unmet_and_named(self) -> None:
        for value in ("Not approved", "Approval withheld", "Request changes", "Approves", "Disapprove"):
            with self.subTest(value=value):
                verdict = self.gate(f"Findings.\n**Verdict:** {value}\n")
                self.assertEqual(verdict, {"kind": "verdict", "met": False,
                                           "why": f"the verdict is {value!r}, not approve"})

    def test_a_review_without_a_verdict_line_is_unmet_and_says_it_is_missing(self) -> None:
        # Nearly verdict lines: not bold, the colon outside, indented, lowercase, or the value on the next line.
        for body in ("No verdict here.", "Verdict: Approve", "**Verdict**: Approve", "  **Verdict:** Approve",
                     "**verdict:** Approve", "", "**Verdict:**\nApprove"):
            with self.subTest(body=body):
                self.assertEqual(self.gate(body), {"kind": "verdict", "met": False,
                                                   "why": "the review has no **Verdict:** line"})

    def test_an_empty_verdict_is_unmet(self) -> None:
        verdict = self.gate("**Verdict:**   \n**Verdict:** Approve")
        self.assertEqual(verdict, {"kind": "verdict", "met": False, "why": "the review's **Verdict:** line is empty"})

    def test_only_the_first_verdict_line_counts(self) -> None:
        verdict = self.gate("**Verdict:** Request changes\n\nAfter the fix:\n**Verdict:** Approve\n")
        self.assertFalse(verdict["met"])
        self.assertIn("'Request changes'", verdict["why"])
        self.assertTrue(self.gate("**Verdict:** Approve\n**Verdict:** Request changes")["met"])

    def test_a_crlf_body_reads_its_verdict(self) -> None:
        self.assertTrue(self.gate("Summary\r\n**Verdict:** Approved\r\n")["met"])

    def test_a_reviewer_without_a_gate_has_none(self) -> None:
        self.assertIsNone(gates.gate({"id": "claude"}, review("**Verdict:** Request changes"), {}, HEAD))
        self.assertEqual(gates.shown(None), "")


class ApprovalGateTests(unittest.TestCase):
    """An approval counts by account, never by name, and at the head where the forge ties it to a commit."""

    REVIEWER = {"id": "codex", "account": "codex-bot", "gate": "approval"}

    def gate(self, approvals: list) -> dict:
        found = {"approvals": approvals}
        self.assertIsNone(gates.members_refusal([self.REVIEWER], found))
        return gates.gate(self.REVIEWER, review("text"), found, HEAD)

    def test_an_approval_by_the_account_at_the_head_is_met(self) -> None:
        verdict = self.gate([{"account": "codex-bot", "name": "Codex", "at": "2026-11-11T10:00:00Z", "commit": HEAD}])
        self.assertTrue(verdict["met"], verdict)
        self.assertEqual(verdict["kind"], "approval")
        # An abbreviated commit, as a forge may report one, is the head too.
        self.assertTrue(self.gate([{"account": "codex-bot", "name": "Codex", "at": "t", "commit": HEAD[:12]}])["met"])

    def test_github_an_approval_at_an_older_commit_is_unmet(self) -> None:
        verdict = self.gate([{"account": "codex-bot", "name": "Codex", "at": "t", "commit": OLDER}])
        self.assertEqual(verdict, {"kind": "approval", "met": False,
                                   "why": f"codex-bot approved {OLDER[:10]}, not the head {HEAD[:10]}"})

    def test_github_an_approval_at_the_head_by_another_account_with_the_same_name_is_unmet(self) -> None:
        verdict = self.gate([{"account": "impostor", "name": "Codex", "at": "t", "commit": HEAD}])
        self.assertEqual(verdict, {"kind": "approval", "met": False, "why": "no approval by codex-bot"})
        # Nor does a name equal to the account count.
        self.assertFalse(self.gate([{"account": "other", "name": "codex-bot", "at": "t", "commit": HEAD}])["met"])

    def test_bitbucket_an_approval_with_no_commit_still_counts_after_a_new_push(self) -> None:
        verdict = self.gate([{"account": "codex-bot", "name": "Codex", "at": "2026-11-10T09:00:00Z"}])
        self.assertTrue(verdict["met"], verdict)

    def test_an_approval_naming_an_empty_commit_is_not_taken_for_an_untied_one(self) -> None:
        for commit in ("", None, "0123"):
            with self.subTest(commit=commit):
                verdict = self.gate([{"account": "codex-bot", "name": "C", "at": "t", "commit": commit}])
                self.assertFalse(verdict["met"], verdict)
                self.assertIn("not the head", verdict["why"])

    def test_an_older_approval_beside_one_at_the_head_is_met(self) -> None:
        self.assertTrue(self.gate([{"account": "codex-bot", "name": "C", "at": "t1", "commit": OLDER},
                                   {"account": "codex-bot", "name": "C", "at": "t2", "commit": HEAD}])["met"])

    def test_approvals_present_but_empty_is_unmet_not_refused(self) -> None:
        self.assertEqual(self.gate([]), {"kind": "approval", "met": False, "why": "no approval by codex-bot"})

    def test_approvals_absent_from_the_adapter_is_refused_naming_the_member(self) -> None:
        for found in ({"checks": [], "reviews": []}, {"approvals": None}):
            with self.subTest(found=found):
                refusal = gates.members_refusal([self.REVIEWER], found)
                self.assertIn("returns no `approvals`", refusal)
                self.assertIn("codex", refusal)

    def test_malformed_approvals_are_refused(self) -> None:
        for approvals in ("codex-bot", [["codex-bot"]], {"account": "codex-bot"}):
            with self.subTest(approvals=approvals):
                self.assertIn("not a list of objects", gates.members_refusal([self.REVIEWER], {"approvals": approvals}))

    def test_no_approval_gate_needs_no_approvals(self) -> None:
        reviewers = [{"id": "claude", "author": "Claude", "gate": "verdict"}, {"id": "codex", "account": "codex-bot"}]
        self.assertIsNone(gates.members_refusal(reviewers, {"checks": [], "reviews": []}))


class RefusalTextTests(unittest.TestCase):
    def test_an_unmet_gate_names_the_reviewer_and_fix_and_push(self) -> None:
        verdict = {"kind": "verdict", "met": False, "why": "the verdict is 'Request changes', not approve"}
        self.assertEqual(gates.unmet_refusal("claude", verdict, HEAD),
                         "PR reviewer claude's verdict gate is unmet at 0123456789: the verdict is 'Request changes', "
                         "not approve; fix and push, so the reviewer runs again")
        self.assertEqual(gates.shown(verdict), "verdict gate UNMET: the verdict is 'Request changes', not approve")
        self.assertEqual(gates.shown({"kind": "approval", "met": True, "why": "codex-bot approved"}),
                         "approval gate met: codex-bot approved")



class BriefTextTests(unittest.TestCase):
    def test_the_brief_names_each_gated_reviewer_and_says_nothing_without_one(self) -> None:
        text = gates.brief_text([{"id": "primary", "gate": "verdict"}, {"id": "codex", "gate": "approval"},
                                 {"id": "third"}])
        self.assertIn("`primary` (its **Verdict:** line); `codex` (its approval)", text)
        self.assertIn("fix what the reviewer found and push", text)
        self.assertNotIn("third", text)
        self.assertEqual(gates.brief_text([{"id": "third"}]), "")



# A reviewer with `report`, read from its Code Insights report at the head.
T_CHECK1, T_COMMENT, T_CHECK2, T_REPORT = ("2026-11-11T10:00:00+00:00", "2026-11-11T10:02:00+00:00",
                                           "2026-11-11T10:05:00+00:00", "2026-11-11T10:06:00+00:00")
T_EARLIER = "2026-11-11T09:00:00+00:00"
REPORTER = {"id": "primary", "account": "{u-primary}", "required": True, "report": "primary-review"}


def when(stamp: object) -> float:
    import datetime
    return datetime.datetime.fromisoformat(str(stamp)).timestamp()


def checks(*states: str) -> list[dict]:
    return [{"name": f"build-{i}", "state": state, "at": (T_CHECK1, T_CHECK2)[i % 2]} for i, state in enumerate(states)]


def report(result: str, external_id: str = "primary-review", at: str = T_REPORT, data: list | None = None) -> dict:
    return {"id": external_id, "result": result, "at": at, "data": data or []}


def comment(at: str, body: str = "Primary says: fine\n**Verdict:** Approve\n") -> dict:
    return {"author": "Primary", "account": "{u-primary}", "at": at, "body": body}


class ReportReadingTests(unittest.TestCase):
    """A reviewer with `report` is current exactly when its report is PASSED at the head and every check passed; its
    text is its latest comment from the head's pipeline. PENDING or no report waits for the settle; FAILED is at once."""

    def read(self, reports: list, mine: list, the_checks: list, settled: bool = False,
             reviewer: dict = REPORTER) -> dict:
        return gates.report_reading(reviewer, mine, {"reports": reports}, the_checks, settled, when)

    def test_passed_with_a_comment_between_the_earliest_and_latest_check_is_current_with_that_text(self) -> None:
        mine = [comment(T_COMMENT)]
        read = self.read([report("PASSED")], mine, checks("passed", "passed"))
        self.assertEqual(read["state"], "current")
        self.assertIs(read["review"], mine[0])
        self.assertEqual(read["report"]["result"], "PASSED")

    def test_the_latest_comment_of_the_pipeline_is_the_text(self) -> None:
        mine = [comment(T_CHECK2, "later"), comment(T_COMMENT, "earlier"), comment(T_EARLIER, "older head")]
        self.assertEqual(self.read([report("PASSED")], mine, checks("passed", "passed"))["review"]["body"], "later")

    def test_passed_with_a_check_still_pending_waits(self) -> None:
        read = self.read([report("PASSED")], [comment(T_COMMENT)], checks("passed", "pending"))
        self.assertEqual(read["state"], "pending")

    def test_pending_waits_until_the_settle_then_a_required_reviewer_is_silent(self) -> None:
        self.assertEqual(self.read([report("PENDING")], [comment(T_COMMENT)], checks("passed"))["state"], "pending")
        after = self.read([report("PENDING")], [comment(T_COMMENT)], checks("passed"), settled=True)
        self.assertEqual(after["state"], "silent")
        self.assertIn("still PENDING after the settle", after["why"])

    def test_no_report_yet_and_an_empty_list_wait_like_pending(self) -> None:
        for reports in ([], [report("PASSED", "another-report")]):
            with self.subTest(reports=reports):
                self.assertEqual(self.read(reports, [comment(T_COMMENT)], checks("passed"))["state"], "pending")
                after = self.read(reports, [comment(T_COMMENT)], checks("passed"), settled=True)
                self.assertEqual(after["state"], "silent")
                self.assertIn("not at the head", after["why"])

    def test_an_optional_reviewer_is_absent_after_the_settle(self) -> None:
        optional = {**REPORTER, "required": False}
        after = self.read([report("PENDING")], [], checks("passed"), settled=True, reviewer=optional)
        self.assertEqual(after["state"], "absent")

    def test_failed_is_silent_at_once_when_required_and_absent_when_optional(self) -> None:
        for the_checks in (checks("passed"), checks("pending")):
            with self.subTest(checks=the_checks):
                read = self.read([report("FAILED")], [comment(T_COMMENT)], the_checks)
                self.assertEqual(read["state"], "silent")
                self.assertIn("PR reviewer primary errored: its report primary-review is FAILED", read["why"])
                optional = self.read([report("FAILED")], [comment(T_COMMENT)], the_checks,
                                     reviewer={**REPORTER, "required": False})
                self.assertEqual(optional["state"], "absent")

    def test_a_comment_from_before_the_heads_pipeline_never_counts(self) -> None:
        read = self.read([report("PASSED")], [comment(T_EARLIER)], checks("passed", "passed"))
        self.assertEqual(read["state"], "pending")
        refused = self.read([report("PASSED")], [comment(T_EARLIER)], checks("passed", "passed"), settled=True)
        self.assertEqual(set(refused), {"refusal"})
        self.assertIn("PR reviewer primary's report primary-review is PASSED, but names a review that wasn't posted",
                      refused["refusal"])

    def test_a_comment_at_exactly_the_earliest_check_counts(self) -> None:
        read = self.read([report("PASSED")], [comment(T_CHECK1)], checks("passed", "passed"))
        self.assertEqual(read["state"], "current")

    def test_a_comment_posted_before_the_report_turned_passed_counts(self) -> None:
        read = self.read([report("PASSED", at=T_REPORT)], [comment(T_COMMENT)], checks("passed"))
        self.assertEqual(read["state"], "current")

    def test_the_timestamps_of_the_report_do_not_decide_currency(self) -> None:
        read = self.read([report("PASSED", at=T_EARLIER)], [comment(T_COMMENT)], checks("passed", "passed"))
        self.assertEqual(read["state"], "current")

    def test_the_report_listed_twice_is_read_at_its_latest(self) -> None:
        reports = [report("PASSED", at=T_CHECK1), report("FAILED", at=T_REPORT)]
        self.assertEqual(self.read(reports, [comment(T_COMMENT)], checks("passed"))["state"], "silent")


class SilentReasonTests(unittest.TestCase):
    """The silent refusal's reason: a report's own, or none, so the refusal keeps its words for every other reviewer."""

    def test_allow_a_reviewer_without_a_report_keeps_the_silent_text(self) -> None:
        read = {"reviewers": {"primary": {"state": "silent", "review": None}, "codex": {"state": "absent"}}}
        self.assertIsNone(gates.silent_reasons(read))

    def test_deny_an_errored_report_gives_its_reason_and_names_a_quiet_one_beside_it(self) -> None:
        errored = {"state": "silent", "why": "PR reviewer primary errored: its report primary-review is FAILED"}
        self.assertEqual(gates.silent_reasons({"reviewers": {"primary": errored}}), errored["why"])
        both = {"reviewers": {"primary": errored, "codex": {"state": "silent", "review": None}}}
        self.assertEqual(gates.silent_reasons(both),
                         errored["why"] + "; codex posted nothing since the pipeline went green")

    def test_only_a_pending_entry_is_unread_when_stopped(self) -> None:
        self.assertTrue(gates.unread_when_stopped({"state": "pending", "required": True}))
        for state in ("current", "absent", "silent"):
            self.assertFalse(gates.unread_when_stopped({"state": state}))


class ReportVerdictTests(unittest.TestCase):
    """With a report, the verdict is its data item titled Verdict, in any case, and wins over the body."""

    def verdict(self, data: list, body: str, kind: str = "verdict", result: str = "PASSED") -> dict:
        reviewer = {**REPORTER, "gate": kind}
        found = {"reports": [report(result, data=data)], "approvals": []}
        return gates.gate(reviewer, comment(T_COMMENT, body), found, HEAD)

    def test_a_lowercase_verdict_title_is_read(self) -> None:
        verdict = self.verdict([{"title": "verdict", "type": "TEXT", "value": "Approved"}], "no line here")
        self.assertEqual(verdict, {"kind": "verdict", "met": True, "why": "report primary-review's Verdict is 'Approved'"})

    def test_the_report_verdict_wins_over_the_body(self) -> None:
        data = [{"title": "Findings", "type": "NUMBER", "value": 2}, {"title": "VERDICT", "type": "TEXT",
                                                                       "value": "Request changes"}]
        verdict = self.verdict(data, "**Verdict:** Approve\n")
        self.assertFalse(verdict["met"])
        self.assertIn("'Request changes', not approve", verdict["why"])
        self.assertTrue(self.verdict([{"title": "Verdict", "type": "TEXT", "value": "approve"}],
                                     "**Verdict:** Request changes\n")["met"])

    def test_without_a_verdict_item_the_body_is_read(self) -> None:
        self.assertEqual(self.verdict([{"title": "Findings", "type": "NUMBER", "value": 0}], "**Verdict:** Approve\n"),
                         {"kind": "verdict", "met": True, "why": "the verdict is 'Approve'"})

    def test_an_empty_report_verdict_is_unmet(self) -> None:
        self.assertEqual(self.verdict([{"title": "Verdict", "type": "TEXT", "value": None}], "**Verdict:** Approve\n"),
                         {"kind": "verdict", "met": False, "why": "report primary-review's Verdict is empty"})

    def test_an_approval_gate_with_a_report_reads_the_report_not_the_approvals(self) -> None:
        approve = [{"title": "Verdict", "type": "TEXT", "value": "Approve"}]
        met = self.verdict(approve, "", kind="approval")
        self.assertEqual(met, {"kind": "approval", "met": True,
                               "why": "report primary-review is PASSED; report primary-review's Verdict is 'Approve'"})
        unmet = self.verdict([{"title": "Verdict", "type": "TEXT", "value": "Changes"}], "", kind="approval")
        self.assertFalse(unmet["met"])
        self.assertEqual(self.verdict(approve, "", kind="approval", result="PENDING"),
                         {"kind": "approval", "met": False, "why": "report primary-review is PENDING"})


class ReportMembersTests(unittest.TestCase):
    """`reports` absent is refused naming the member, null (GitHub's) naming Bitbucket, present but empty is not."""

    def test_reports_absent_is_refused_naming_the_member(self) -> None:
        refused = gates.reports_refusal([REPORTER], {})
        self.assertIn("returns no `reports`, which the `report` of primary needs", refused)

    def test_reports_null_is_refused_naming_bitbucket(self) -> None:
        refused = gates.reports_refusal([REPORTER], {"reports": None})
        self.assertIn("only the Bitbucket adapter returns reports", refused)
        self.assertIn("PR reviewer primary takes `report`", refused)

    def test_reports_present_but_empty_is_not_refused(self) -> None:
        self.assertIsNone(gates.reports_refusal([REPORTER], {"reports": []}))

    def test_malformed_reports_are_refused(self) -> None:
        for reports in ("x", [1], [{"id": 1, "result": "PASSED"}], [{"id": "a", "result": "SUCCESS"}]):
            with self.subTest(reports=reports):
                self.assertIn("not a list of objects", gates.reports_refusal([REPORTER], {"reports": reports}))

    def test_no_reviewer_with_report_needs_no_reports(self) -> None:
        self.assertIsNone(gates.reports_refusal([{"id": "x", "author": "X"}], {"reports": None}))

    def test_the_doctor_refuses_report_under_the_github_adapter_naming_bitbucket(self) -> None:
        refused = gates.forge_refusal([REPORTER, {"id": "codex", "author": "Codex"}], "github")
        self.assertIn("PR reviewer primary takes `report`", refused)
        self.assertIn("only the Bitbucket adapter returns them", refused)
        self.assertIsNone(gates.forge_refusal([REPORTER], "bitbucket"))
        self.assertIsNone(gates.forge_refusal([{"id": "codex", "author": "Codex"}], "github"))

    def test_the_report_contract_is_documented(self) -> None:
        adopting = " ".join((PRODUCT_ROOT / "skills/run-delivery/references/adopting.md").read_text().split())
        for phrase in ("The report's `external_id` is the reviewer's `report`",
                       "`PASSED` once its review comment is posted, whatever the verdict.",
                       "`FAILED` when the reviewer errored",
                       "A data item titled `Verdict` (matched in any case)",
                       "its adapter prints `\"reports\": null`"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, adopting)

    def test_attach_refuses_reports_null_for_a_reviewer_with_report(self) -> None:
        read = {"status": "done", "reviewers": {"primary": {"state": "current", "review": comment(T_COMMENT)}}}
        conf = {"legacy": False, "reviewers": [REPORTER]}
        self.assertIn("Bitbucket", gates.attach(read, {"reports": None}, conf, HEAD))


# The commands, end to end: the tool from an installed release built from this tree, on lane 1's PR-review fixture.
from tests.test_run_delivery_pr_review import (  # noqa: E402
    AFTER, PASSED, PR, T0_EPOCH, PrReviewCase, by_codex, by_primary,
)

VERDICT_PRIMARY = {"id": "primary", "account": "{u-primary}", "required": True, "gate": "verdict"}
APPROVAL_CODEX = {"id": "codex", "account": "{u-review}", "required": True, "gate": "approval"}
OPTIONAL_CODEX = {"id": "codex", "author": "Review Bot", "signature": "<!-- codex -->", "required": False,
                  "gate": "verdict"}
APPROVE = "Primary says: fine\n**Verdict:** Approve\n"
CHANGES = "Primary says: fix x\n**Verdict:** Request changes\n"


def approved(account: str = "{u-review}", **extra) -> dict:
    return {"account": account, "name": "Review Bot", "at": AFTER, **extra}


class GatesEverywhereTests(PrReviewCase):
    """An unmet gate is shown by pr-review and refused by handoff, lead-accept and the reviewGate, each naming the
    reviewer and "fix and push"; the same reviewer met passes all four."""

    def test_an_unmet_verdict_gate_is_shown_and_refused_and_met_passes(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [VERDICT_PRIMARY]})
        self.answer([PASSED], [by_primary(body=CHANGES)])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertIn("## primary (required): Primary Bot at 2026-01-01T00:00:30\nverdict gate UNMET: the verdict is "
                      "'Request changes', not approve\n", out)
        self.assertEqual(self.record()["reviewers"]["primary"]["gate"],
                         {"kind": "verdict", "met": False, "why": "the verdict is 'Request changes', not approve"})
        refusal = (f"PR reviewer primary's verdict gate is unmet at {self.head[:10]}: the verdict is 'Request "
                   "changes', not approve; fix and push, so the reviewer runs again")
        code, out = self.handoff("fixed x")
        self.assertEqual(code, 2, out)
        self.assertIn(refusal, out)
        code, _, err = self.gate()
        self.assertEqual(code, 2, err)
        self.assertEqual(err.strip(), "run-delivery: " + refusal)
        # Met: the review says approve.
        self.answer([PASSED], [by_primary(body=APPROVE)])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertIn("verdict gate met: the verdict is 'Approve'", out)
        code, out = self.handoff("fine")
        self.assertEqual(code, 0, out)
        code, out, err = self.gate()
        self.assertEqual((code, err), (0, ""), out)
        code, out = self.accept()
        self.assertEqual(code, 0, out)

    def test_lead_accept_rereads_the_gate_at_the_forge(self) -> None:
        """Handed off with the approval; at acceptance the forge no longer shows it."""
        self.configure({"settleSeconds": 300, "reviewers": [APPROVAL_CODEX]})
        self.answer([PASSED], [by_codex()], approvals=[approved(commit=self.head)])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertIn(f"approval gate met: {{u-review}} approved {self.head[:10]}", out)
        self.assertEqual(self.handoff("fine")[0], 0)
        refusal = (f"PR reviewer codex's approval gate is unmet at {self.head[:10]}: no approval by {{u-review}}; "
                   "fix and push, so the reviewer runs again")
        for what, approvals in (("withdrawn", []), ("by another account under the name", [approved("{other}")]),
                                ("of an older commit", [approved(commit="f" * 40)])):
            with self.subTest(what):
                self.answer([PASSED], [by_codex()], approvals=approvals)
                code, out = self.accept()
                self.assertEqual(code, 1, out)
                self.assertIn(refusal if what != "of an older commit" else
                              "codex's approval gate is unmet", out)
                self.assertIn("fix and push", out)
                code, _, err = self.gate()
                self.assertEqual(code, 2, err)
                self.assertIn("PR reviewer codex's approval gate is unmet", err)
        self.answer([PASSED], [by_codex()], approvals=[approved(commit=self.head)])
        code, out = self.accept()
        self.assertEqual(code, 0, out)

    def test_handoff_refuses_a_record_made_before_the_reviewer_took_its_gate(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [{"id": "primary", "account": "{u-primary}"}]})
        self.answer([PASSED], [by_primary(body=APPROVE)])
        self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)
        self.assertNotIn("gate", self.record()["reviewers"]["primary"])
        self.configure({"settleSeconds": 300, "reviewers": [VERDICT_PRIMARY]})
        code, out = self.handoff("fine")
        self.assertEqual(code, 2, out)
        self.assertIn(f"PR reviewer primary's verdict gate isn't in the PR review recorded at {self.head[:10]}: run "
                      "pr-review again", out)
        with self.subTest("recorded under another kind of gate"):
            # Recorded with its verdict met, then the same reviewer takes an approval gate: the verdict doesn't count.
            self.assertEqual(self.flow("pr-review", "FX-2")[0], 0)
            self.assertEqual(self.record()["reviewers"]["primary"]["gate"]["kind"], "verdict")
            self.configure({"settleSeconds": 300, "reviewers": [{**VERDICT_PRIMARY, "gate": "approval"}]})
            code, out = self.handoff("fine")
            self.assertEqual(code, 2, out)
            self.assertIn(f"PR reviewer primary's approval gate isn't in the PR review recorded at {self.head[:10]}: "
                          "run pr-review again", out)

    def test_the_lane_brief_names_the_gated_reviewers(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [VERDICT_PRIMARY, APPROVAL_CODEX]})
        code, out = self.fx.flow("lead-brief", cwd=self.fx.main)
        self.assertEqual(code, 0, out)
        brief = " ".join((self.fx.main / "docs/lane-brief.md").read_text().split())
        self.assertIn("Some reviewers gate the hand-off: `primary` (its **Verdict:** line); `codex` (its approval). "
                      "`pr-review` shows each gate beside its review; with one unmet, `handoff`, the lead's acceptance "
                      "and the merge queue refuse", brief)
        # Without a gate, the paragraph is as it was: no gate sentence.
        self.configure({"settleSeconds": 300, "reviewers": [{"id": "primary", "account": "{u-primary}"}]})
        self.assertEqual(self.fx.flow("lead-brief", cwd=self.fx.main)[0], 0)
        self.assertNotIn("gate the hand-off", (self.fx.main / "docs/lane-brief.md").read_text())


class ApprovalMembersTests(PrReviewCase):
    def setUp(self) -> None:
        super().setUp()
        self.configure({"settleSeconds": 300, "reviewers": [APPROVAL_CODEX]})

    def test_approvals_absent_are_refused_naming_the_member(self) -> None:
        for what, members in (("left out", {}), ("null", {"approvals": None})):
            with self.subTest(what):
                self.answer([PASSED], [by_codex()], **members)
                code, out = self.flow("pr-review", "FX-2")
                self.assertEqual(code, 2, out)
                self.assertIn("`approvals`", out)
                self.assertFalse((self.review / "pr-review.json").exists())

    def test_approvals_present_but_empty_are_an_unmet_gate_not_a_refusal(self) -> None:
        self.answer([PASSED], [by_codex()], approvals=[])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertIn("approval gate UNMET: no approval by {u-review}", out)

    def test_a_bitbucket_approval_with_no_commit_counts_after_a_new_push(self) -> None:
        self.answer([PASSED], [by_codex()], approvals=[approved()])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertIn("approval gate met: {u-review} approved (2026-01-01T00:00:30)", out)


class AbsentReviewerTests(PrReviewCase):
    """An absent optional reviewer's gate is checked nowhere."""

    def test_an_absent_optional_reviewer_whose_gate_would_be_unmet_passes(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [{"id": "primary", "account": "{u-primary}"},
                                                             OPTIONAL_CODEX]})
        self.clock(T0_EPOCH + 301)
        self.answer([PASSED], [by_primary()])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record()["reviewers"]["codex"], {"state": "absent", "required": False})
        code, out = self.handoff("fixed x")
        self.assertEqual(code, 0, out)
        code, out, err = self.gate()
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("codex absent", out)
        code, out = self.accept()
        self.assertEqual(code, 0, out)
        # Current, the same reviewer's unmet verdict does gate: it is checked on every current review.
        self.answer([PASSED], [by_primary(), by_codex(body="<!-- codex -->\n**Verdict:** Request changes")])
        code, _, err = self.gate()
        self.assertEqual(code, 2, err)
        self.assertIn("PR reviewer codex's verdict gate is unmet", err)


# A reviewer read from its Code Insights report, end to end: two checks, the pipeline's earliest at T0.
LINT = {"name": "lint", "state": "passed", "at": "2026-01-01T00:01:00"}
BEFORE_PIPELINE = "2026-01-01T00:00:05"
REPORTING = {"id": "primary", "account": "{u-primary}", "required": True, "report": "primary-review"}
REPORTING_CODEX = {"id": "codex", "account": "{u-review}", "required": False, "report": "codex-review"}


def insight(result: str, external_id: str = "primary-review", verdict: str | None = None,
            title: str = "Verdict") -> dict:
    data = [{"title": title, "type": "TEXT", "value": verdict}] if verdict is not None else []
    return {"id": external_id, "result": result, "at": "2026-01-01T00:02:00", "data": data}


class ReportEndToEndTests(PrReviewCase):
    """pr-review, handoff, lead-accept, the reviewGate and the doctor on a reviewer with `report`."""

    def test_a_passed_report_with_a_comment_from_the_pipeline_is_current_everywhere(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [REPORTING]})
        # The comment came between the earliest and the latest check, before the report turned PASSED.
        self.answer([PASSED, LINT], [by_primary(body="Primary: fine")], reports=[insight("PASSED")])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertIn("## primary (required): Primary Bot at 2026-01-01T00:00:30\n\nPrimary: fine", out)
        entry = self.record()["reviewers"]["primary"]
        self.assertEqual(entry["report"], {"id": "primary-review", "result": "PASSED", "at": "2026-01-01T00:02:00"})
        self.assertEqual(entry["body"], "Primary: fine")
        self.assertEqual(self.handoff("fine")[0], 0)
        code, out, err = self.gate()
        self.assertEqual((code, err), (0, ""), out)
        code, out = self.accept()
        self.assertEqual(code, 0, out)

    def test_a_passed_report_waits_for_a_pending_check_and_a_failed_check_refuses(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [REPORTING]})
        self.answer([PASSED, {**LINT, "state": "pending"}], [by_primary()], reports=[insight("PASSED")])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertIn(f"no current review from primary after the pipeline for {self.head[:10]} yet", out)
        self.answer([PASSED, {**LINT, "state": "failed"}], [by_primary()], reports=[insight("PASSED")])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertIn(f"the pipeline failed at {self.head[:10]}", out)

    def test_deny_a_report_still_pending_after_the_settle_is_silent_with_its_own_reason(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [REPORTING]})
        self.answer([PASSED], [by_primary()], reports=[insight("PENDING")])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertNotIn("silent reviewer", out)
        self.clock(T0_EPOCH + 301)
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 3, out)
        self.assertIn("silent reviewer: PR reviewer primary's report primary-review is still PENDING after the settle",
                      out)
        self.assertEqual(self.record()["reviewers"]["primary"]["state"], "silent")

    def test_deny_a_failed_report_stops_a_required_reviewers_wait_at_once_while_a_check_is_pending(self) -> None:
        self.configure({"settleSeconds": 300, "waitSeconds": 600, "reviewers": [REPORTING]})
        self.answer([PASSED, {**LINT, "state": "pending"}], [], reports=[insight("FAILED")])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 3, out)
        self.assertIn("silent reviewer: PR reviewer primary errored: its report primary-review is FAILED; re-run its "
                      "step or ask the lead", out)
        self.assertNotIn("the pipeline is green", out)
        self.assertEqual(self.sleeps(), [])
        code, _, err = self.gate("--wait")
        self.assertEqual(code, 3, err)
        self.assertIn("PR reviewer primary errored", err)

    def test_allow_an_optional_reviewer_with_a_failed_report_is_absent(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [REPORTING | {"report": "other"}, REPORTING_CODEX]})
        self.answer([PASSED], [by_primary()], reports=[insight("PASSED", "other"), insight("FAILED", "codex-review")])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 0, out)
        self.assertIn("## codex (optional): absent", out)
        self.assertEqual(self.record()["reviewers"]["codex"],
                         {"state": "absent", "required": False,
                          "report": {"id": "codex-review", "result": "FAILED", "at": "2026-01-01T00:02:00"}})

    def test_allow_an_optional_failed_report_does_not_end_the_wait_while_a_check_is_pending(self) -> None:
        """Only a silent required reviewer ends the wait early; an absent optional one never does."""
        self.configure({"settleSeconds": 300, "waitSeconds": 120, "reviewers": [REPORTING, REPORTING_CODEX]})
        self.answer([PASSED, {**LINT, "state": "pending"}], [by_primary()],
                    reports=[insight("PASSED"), insight("FAILED", "codex-review")])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 2, out)
        self.assertNotEqual(self.sleeps(), [])
        self.assertIn("lint pending", out)
        self.clock(T0_EPOCH + 60)
        code, _, err = self.gate("--wait")
        self.assertEqual(code, 2, err)
        self.assertNotEqual(self.sleeps(), [])
        self.assertIn("lint pending", err)

    def test_a_blocked_hand_off_on_an_errored_report_is_the_leads_to_accept_with_another_reviewer_unread(self) -> None:
        """A FAILED report stops the read before another required reviewer is in: that one is recorded pending, and
        the lead accepts the block."""
        self.configure({"settleSeconds": 300, "reviewers": [REPORTING, {"id": "codex", "account": "{u-review}"}]})
        self.answer([PASSED, {**LINT, "state": "pending"}], [], reports=[insight("FAILED")])
        code, out = self.flow("pr-review", "FX-2", "--wait")
        self.assertEqual(code, 3, out)
        self.assertEqual(self.record()["reviewers"]["codex"], {"state": "pending", "required": True})
        line = next(l for l in out.splitlines() if l.startswith("run-delivery: silent reviewer"))[len("run-delivery: "):]
        self.assertIn("PR reviewer primary errored", line)
        code, out = self.flow("handoff", "FX-2", "--pr", PR, "--blocked", f"pr-review={line}")
        self.assertEqual(code, 0, out)
        code, out = self.accept()
        self.assertEqual(code, 1, out)
        self.assertIn("the lane blocked 'pr-review'", out)
        # The unread reviewer has since posted, the report still FAILED: the lane must read and triage it first.
        self.answer([PASSED, LINT], [by_codex(body="Codex says: P1 bug in x", at="2026-01-01T00:03:00")],
                    reports=[insight("FAILED")])
        code, out = self.flow("lead-accept", "FX-2", "--accept-blocked", "pr-review", cwd=self.fx.main)
        self.assertEqual(code, 1, out)
        self.assertIn("codex is required, but the lane recorded it pending", out)
        # Still not in: the lead accepts the block.
        self.answer([PASSED, {**LINT, "state": "pending"}], [], reports=[insight("FAILED")])
        code, out = self.flow("lead-accept", "FX-2", "--accept-blocked", "pr-review", cwd=self.fx.main)
        self.assertEqual(code, 0, out)

    def test_a_passed_report_whose_comment_predates_the_pipeline_waits_then_refuses(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [REPORTING]})
        self.answer([PASSED, LINT], [by_primary(at=BEFORE_PIPELINE)], reports=[insight("PASSED")])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertIn("no current review from primary", out)
        self.clock(T0_EPOCH + 400)
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertIn("PR reviewer primary's report primary-review is PASSED, but names a review that wasn't posted", out)

    def test_the_verdict_gate_and_the_approval_gate_read_the_report(self) -> None:
        for kind, verdict, body, met in (
                ("verdict", "approved", "**Verdict:** Request changes\n", True),
                ("verdict", "Request changes", "**Verdict:** Approve\n", False),
                ("approval", "Approve", "no line", True)):
            with self.subTest(kind=kind, verdict=verdict):
                self.configure({"settleSeconds": 300, "reviewers": [REPORTING | {"gate": kind}]})
                # A lowercase title; the approval gate needs no approvals from the forge here.
                self.answer([PASSED], [by_primary(body=body)], approvals=[],
                            reports=[insight("PASSED", verdict=verdict, title="verdict")])
                code, out = self.flow("pr-review", "FX-2")
                self.assertEqual(code, 0, out)
                self.assertIn(f"{kind} gate {'met' if met else 'UNMET'}: ", out)
                self.assertIn(f"report primary-review's Verdict is {verdict!r}", out)
                code, _, err = self.gate()
                self.assertEqual(code, 0 if met else 2, err)

    def test_reports_absent_null_or_empty(self) -> None:
        self.configure({"settleSeconds": 300, "reviewers": [REPORTING]})
        self.answer([PASSED], [by_primary()])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertIn("returned no `reports` (a reviewer has `report`)", out)
        self.answer([PASSED], [by_primary()], reports=None)
        for reader in ("pr-review", "gate"):
            with self.subTest(reader):
                code, out = self.flow("pr-review", "FX-2") if reader == "pr-review" else self.gate()[::2]
                self.assertEqual(code, 2, out)
                self.assertIn("only the Bitbucket adapter returns reports", out)
        self.answer([PASSED], [by_primary()], reports=[])
        code, out = self.flow("pr-review", "FX-2")
        self.assertEqual(code, 2, out)
        self.assertIn("no current review from primary", out)

    def test_the_doctor_refuses_report_under_the_github_adapter(self) -> None:
        for adapter, status in (("github", "wrong"), ("bitbucket", "ok")):
            with self.subTest(adapter):
                self.configure({"settleSeconds": 300, "reviewers": [REPORTING]})
                conf_path = Path(self.fx.env["RUN_DELIVERY_CONFIG"])
                conf = json.loads(conf_path.read_text())
                conf["forge"] = {"adapter": adapter, "repository": "o/n", "tokenEnv": "FX_TOKEN"}
                conf_path.write_text(json.dumps(conf))
                code, out = self.fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=self.fx.main)
                line = next(l for l in out.splitlines() if " pipeline PR review: " in l)
                self.assertTrue(line.startswith(status), line)
                if status == "wrong":
                    self.assertIn("only the Bitbucket adapter returns them", line)


if __name__ == "__main__":
    unittest.main()
