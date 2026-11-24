"""A live address a workflow hands the operator is opened for them, and every lane starts with the delivery."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_DELIVERY = "skills/run-delivery/SKILL.md"
KERNEL = "skills/compound-delivery-kernel/SKILL.md"
ORCHESTRATE = "skills/orchestrate-work/SKILL.md"

# run-delivery's Host behaviour: which addresses, where each opens, and that no command opens anything.
OPEN_RULE = (
    "Each live view or hosted address a command prints is opened for the operator in the same turn, unasked: "
    "`lead-init`'s and `lead-run`'s live view, the hosted URL `doctor` and `view` show, the live view a status line "
    "names, and `plan-view`'s `plan preview` links.",
    "Where the host has an in-app browser pane, the agent opens the address there, and also presents it as a card "
    "that opens it in that pane where the host can show one.",
    "Where the host has no such pane, the agent opens the address in the operator's own browser, and only prints it "
    "when it can open it in neither.",
    "Opening is the agent's step, through whatever the host offers; no command opens anything.",
)
# The same rule in one sentence, in each other skill that hands the operator a live address.
ONE_SENTENCE = {
    KERNEL: (
        "Giving the operator an address means opening it in the same turn: in the host's in-app browser pane, with a "
        "card that opens it there where the host can show one; without such a pane, in the operator's own browser; "
        "and printing it only when it can be opened in neither."
    ),
    ORCHESTRATE: (
        "Giving it means opening it in the same turn: in the host's in-app browser pane, with a card that opens it "
        "there where the host can show one; without such a pane, in the operator's own browser; and printing it only "
        "when it can be opened in neither:"
    ),
}
# run-delivery's Running lanes: every lane starts with the delivery, and stories reach lanes by message.
LANES_UP_FRONT = (
    "In the same turn that starts the delivery, the lead starts one session for every lane of the delivery file, "
    "including a lane whose first story is blocked, which it tells to wait.",
    "A blocked lane stays idle until the lead messages it its story, and every later story reaches a lane as a "
    "message to that lane's session, never as a new session.",
    "Some hosts let the lead start a session only in a turn the operator began, not in one a lane's report or a wake "
    "began, so a lane started later can sit waiting on the operator.",
)
# Names a host-specific instruction would reach for: hosts, products and their tools.
HOST_NAMES = re.compile(
    r"\bclaude\b|\bcodex\b|\bcursor\b|\bchrome\b|\bsafari\b|\bvs ?code\b|preview_start|mcp__|start_session|"
    r"sendmessage|\bdesktop app\b|\bbrowser pane tool\b",
    re.IGNORECASE,
)


def _document(relative: str) -> str:
    return re.sub(r"\s+", " ", (ROOT / relative).read_text(encoding="utf-8"))


class LiveAddressRuleTests(unittest.TestCase):
    def test_run_delivery_opens_every_address_a_command_prints(self) -> None:
        text = _document(RUN_DELIVERY)
        section = text[text.index("## Host behaviour"):text.index("## Where lanes read the delivery")]
        for phrase in OPEN_RULE:
            with self.subTest(phrase=phrase[:50]):
                self.assertIn(phrase, section)

    def test_each_other_skill_that_hands_an_address_says_the_same(self) -> None:
        for relative, sentence in ONE_SENTENCE.items():
            with self.subTest(skill=relative):
                self.assertIn(sentence, _document(relative))

    def test_every_lane_starts_with_the_delivery(self) -> None:
        text = _document(RUN_DELIVERY)
        section = text[text.index("## Running lanes"):text.index("## Host behaviour")]
        for phrase in LANES_UP_FRONT:
            with self.subTest(phrase=phrase[:50]):
                self.assertIn(phrase, section)

    def test_the_rules_name_no_host_or_host_tool(self) -> None:
        passages = [*OPEN_RULE, *ONE_SENTENCE.values(), *LANES_UP_FRONT]
        for passage in passages:
            with self.subTest(passage=passage[:50]):
                self.assertIsNone(HOST_NAMES.search(passage))

    def test_the_host_name_scan_is_not_vacuous(self) -> None:
        for sample in ("in Claude Code", "the Codex app", "call preview_start", "an mcp__browser tool", "start_session",
                       "SendMessage to it", "open Chrome", "in Safari", "VS Code's pane", "in the desktop app",
                       "Cursor's browser"):
            with self.subTest(sample=sample):
                self.assertIsNotNone(HOST_NAMES.search(f"open it {sample} now"))
        self.assertIsNone(HOST_NAMES.search("the host's in-app browser pane, or the operator's own browser"))


if __name__ == "__main__":
    unittest.main()
