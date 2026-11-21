"""A lane waits on its lenses in the foreground: review-prepare's prompts tell each reviewer to write its final JSON to
its result path as its last step, the round brief names that write as the lens's one permitted write, and
`review-wait` returns once every result path holds JSON that parses, or exits 4 naming the lenses still out.

The cases drive the installed release's commands on the characterization fixture, as `tests.test_run_delivery` does.
One prepared round serves every case: each case writes the result files it needs and removes them after.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

from tests.test_run_delivery import (CONTEXT_TODO, POSIX_ONLY, Fixture, bytecode_under, commit, installed_release,
                                     setup_delivery, write_results)

ROOT = Path(__file__).resolve().parents[1]
FLOW = "python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py"


def flat(text: str) -> str:
    return " ".join(text.split())


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class ReviewWaitTests(unittest.TestCase):
    """FX-2's lane with round 1 prepared, its two lenses' results not yet written."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)
        fx = cls.fx = Fixture(cls.generation, cls.receipt)
        cls.addClassCleanup(fx.close)
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        assert code == 0, out
        code, out = fx.flow("lead-brief", cwd=fx.main)
        assert code == 0, out
        wt = cls.wt = fx.lane("FX-2")
        cls.review = (wt / ".orchestration/review/FX-2").resolve()
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)  # seeds the context, and refuses until it is filled
        context = cls.review / "context.md"
        context.write_text(context.read_text().replace(next(line for line in context.read_text().splitlines()
                                                            if line.startswith(CONTEXT_TODO)), "- evidence"))
        code, cls.prepared = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        assert code == 0, cls.prepared
        cls.lenses = fx.lenses(wt)
        cls.results = [cls.review / f"result-r1-{short}.json" for _, (short, _) in cls.lenses]

    def setUp(self) -> None:
        self.assertGreaterEqual(len(self.lenses), 2, "the fixture's story has the mandated pair at least")
        for path in self.results:
            self.addCleanup(path.unlink, missing_ok=True)

    def tearDown(self) -> None:
        self.assertEqual(bytecode_under(self.generation), [], "the case left bytecode in the installed generation")

    def wait(self, *argv: str, env: dict | None = None) -> tuple[int, str]:
        return self.fx.flow("review-wait", "FX-2", "--round", "1", *argv, cwd=self.wt, env=env)

    def test_it_returns_0_once_every_result_parses_and_exits_4_naming_the_lenses_still_out(self) -> None:
        (first, _), (second, _) = self.lenses[0], self.lenses[1]
        code, out = self.wait("--seconds", "0")
        self.assertEqual(code, 4, out)
        self.assertIn(f"FX-2 round 1: still out after 0s: {', '.join(lens for lens, _ in self.lenses)}.", out)
        self.assertIn(f"Wait again: {FLOW} review-wait FX-2 --round 1", out)
        write_results(self.review, 1, "FX-2", self.lenses, only=0)
        code, out = self.wait("--seconds", "0")
        self.assertEqual(code, 4, out)
        self.assertIn(second, out)
        self.assertNotIn(first, out)
        write_results(self.review, 1, "FX-2", self.lenses)
        code, out = self.wait("--seconds", "0")
        self.assertEqual(code, 0, out)
        self.assertIn(f"FX-2 round 1: every lens's result is in. Then: {FLOW} review-reduce FX-2 --round 1", out)

    def test_a_result_holding_truncated_json_is_still_out(self) -> None:
        write_results(self.review, 1, "FX-2", self.lenses)
        whole = self.results[1].read_text()
        self.results[1].write_text(whole[: len(whole) // 2])
        (first, _), (second, _) = self.lenses[0], self.lenses[1]
        code, out = self.wait("--seconds", "1")
        self.assertEqual(code, 4, out)
        self.assertIn(f"still out after 1s: {second}.", out)
        self.assertNotIn(first, out)

    def test_with_no_seconds_its_deadline_is_540_seconds_on_the_injected_clock(self) -> None:
        clock = self.fx.tmp / "clock.json"
        clock.write_text(json.dumps({"now": 1000.0, "poll": 60}))
        code, out = self.wait(env={**self.fx.env, "RUN_DELIVERY_CLOCK": str(clock)})
        self.assertEqual(code, 4, out)
        self.assertIn("still out after 540s", out)
        ticks = json.loads(clock.read_text())
        self.assertEqual((ticks["now"], sum(ticks["sleeps"]), len(ticks["sleeps"])), (1540.0, 540.0, 9))

    def test_a_round_not_prepared_or_negative_seconds_are_refused(self) -> None:
        code, out = self.fx.flow("review-wait", "FX-2", "--round", "2", cwd=self.wt)
        self.assertEqual(code, 2, out)
        self.assertIn(f"round 2 isn't prepared: {FLOW} review-prepare FX-2 --round 2 prepares it", out)
        code, out = self.wait("--seconds", "-1")
        self.assertEqual(code, 2, out)
        self.assertIn("--seconds is -1: give zero or more", out)

    def test_the_rendered_round_brief_names_the_result_path_as_the_lenss_one_permitted_write(self) -> None:
        for (lens, (short, _)), result in zip(self.lenses, self.results):
            with self.subTest(lens=lens):
                brief = flat((self.review / f"brief-r1-{short}.md").read_text())
                self.assertIn(f"Its one permitted write, when its prompt asks for it, is its final JSON to `{result}` "
                              "as its last step, before it replies with the same JSON: an exception to the read-only "
                              "rule and to touching no checkout but its own.", brief)
                self.assertNotIn("{{", brief)

    def test_review_prepare_names_review_wait_and_its_prompts_tell_the_reviewer_to_write_its_result(self) -> None:
        out = flat(self.prepared)
        self.assertIn("Each reviewer writes its final JSON to its result path as its last step. Wait for them in the "
                      f"foreground, inside your turn: {FLOW} review-wait FX-2 --round 1 returns once every result is in",
                      out)
        self.assertIn("A reviewer that returned without writing its result: save its final JSON message, exactly as "
                      "returned, to its result path.", out)
        for (lens, (short, _)), result in zip(self.lenses, self.results):
            with self.subTest(lens=lens):
                self.assertIn(f"- {lens} prompt: Read {self.review / f'brief-r1-{short}.md'} and follow it exactly. "
                              f"As your last step, write your final JSON to {result}, then reply with only that same "
                              f"JSON. result path: {result}", out)
        self.assertIn(f"Then: {FLOW} review-reduce FX-2 --round 1, once {FLOW} review-wait FX-2 --round 1 returns 0", out)

    def test_the_lane_briefs_your_turn_names_review_wait(self) -> None:
        for source, text in (("template", (ROOT / "skills/run-delivery/templates/lane-brief.md").read_text()),
                             ("rendered", (self.fx.main / "docs/lane-brief.md").read_text())):
            with self.subTest(source=source):
                turn = flat(re.search(r"^## Your turn\n(.*?)^## ", text, re.MULTILINE | re.DOTALL).group(1))
                flow = "{{flow}}" if source == "template" else FLOW
                self.assertIn("Never end it just to wait for a reviewer, the gates, an in-app check or the pipeline: "
                              "wait inside the turn.", turn)
                self.assertIn(f"resume your reviewers and wait for their results with `{flow} review-wait <key> "
                              "--round <n>`", turn)
                review = flat(re.search(r"^## Review\n(.*?)^## ", text, re.MULTILINE | re.DOTALL).group(1))
                self.assertIn(f"Wait for them in the foreground with `{flow} review-wait <key> --round <n>`", review)
                self.assertIn("When a reviewer returned without writing its result, save its final JSON message, "
                              "exactly as returned, to that path.", review)


class ReviewWaitDocumentationTests(unittest.TestCase):
    """The command table's row for review-wait."""

    def test_skill_md_documents_review_wait(self) -> None:
        skill = flat((ROOT / "skills/run-delivery/SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("| Lane | `review-wait <key> --round N [--seconds S]` | Waits in the foreground, inside the lane's "
                      "turn, until every lens's result path in the round's meta holds JSON that parses, then exits 0. "
                      "When `S` runs out (default 540, inside a host's ten-minute command limit) it exits 4 naming the "
                      "lenses still out, and the lane runs it again.", skill)
        self.assertIn("`review-prepare`'s prompts tell each reviewer to write its final JSON to its result path as its "
                      "last step; the lane saves a reviewer's final message there only when it returned without writing "
                      "it. |", skill)


if __name__ == "__main__":
    unittest.main()
