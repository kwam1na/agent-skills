"""A PR reviewer's gate: its verdict or its approval, met or unmet, and the members it needs from the adapter."""

from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
