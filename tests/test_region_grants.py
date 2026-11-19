"""Region grants show and resolve their lines: printed spans, anchors, the statement check and edge widening."""

from __future__ import annotations

import importlib.util
import json
import shlex
import sys
import unittest
from pathlib import Path

from tests.test_region_claims import CommandCase, Repo, command_fixture, numbered

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(f"region_grants_{name}",
                                                      ROOT / "skills/orchestrate-work/scripts" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


regions = _load("region_ownership")

PYTHON = """import functools


def first():
    return 1


@functools.cache
@functools.wraps(first)
def waited_on(value):
    total = compute(
        value,
        2,
    )
    if total:
        return total
    return 0


def after():
    return 2
"""

MARKDOWN = """# Title

Intro paragraph.

## Section one

First line of a paragraph,
its second line.

### Deeper

Deep text.

## Section two

Other text.
"""

JSON = """{
  "before": 1,
  "block": {
    "inner": 2,
    "more": [
      3
    ]
  },
  "sibling": 4
}
"""


class AnchorTests(unittest.TestCase):
    """Decision 10: an anchor's one line, and the block it names, exactly."""

    def setUp(self) -> None:
        self.repo = Repo(self)
        self.base = self.repo.commit({"a.py": PYTHON.splitlines(True), "a.md": MARKDOWN.splitlines(True),
                                      "a.json": JSON.splitlines(True), "dup.txt": ["same\n", "x\n", "same\n", "same\n"]})

    def around(self, path: str, anchor: str) -> tuple[int, int]:
        return regions.anchored(self.repo.root, self.base, path, anchor)

    def test_a_python_anchor_names_its_statement_with_its_decorators(self) -> None:
        self.assertEqual(self.around("a.py", "def waited_on"), (8, 18))      # its decorators, not the next function
        self.assertEqual(self.around("a.py", "@functools.cache"), (8, 18))   # a decorator's line names the same
        self.assertEqual(self.around("a.py", "        2,"), (11, 15))        # inside a call: the smallest statement
        self.assertEqual(self.around("a.py", "return total"), (16, 17))
        self.assertEqual(self.around("a.py", "if total:"), (15, 17))

    def test_a_markdown_anchor_names_a_section_or_a_paragraph(self) -> None:
        self.assertEqual(self.around("a.md", "## Section one"), (5, 14))   # its deeper heading in it, to the next ##
        self.assertEqual(self.around("a.md", "its second line"), (7, 9))
        self.assertEqual(self.around("a.md", "### Deeper"), (10, 14))

    def test_a_json_key_names_its_block_with_its_closing_line(self) -> None:
        self.assertEqual(self.around("a.json", '"block": {'), (3, 9))
        self.assertEqual(self.around("a.json", '"more"'), (5, 8))
        self.assertEqual(self.around("a.json", '"before"'), (2, 3))

    def test_an_anchor_names_one_line_or_is_refused_listing_each_it_matches(self) -> None:
        with self.assertRaisesRegex(regions.RegionError, "no line of dup.txt"):
            self.around("dup.txt", "absent")
        with self.assertRaises(regions.RegionError) as caught:
            self.around("dup.txt", "same")
        said = str(caught.exception)
        self.assertIn("3 lines of dup.txt", said)
        for number in (1, 3, 4):
            self.assertIn(f"line {number}: 'same'", said)
        self.assertEqual(self.around("dup.txt", "x"), (2, 3))


class StatementTests(unittest.TestCase):
    """Decisions 11 and 12 on the helpers: a Python span may not cut a statement, and --insert widens by one line."""

    def setUp(self) -> None:
        self.repo = Repo(self)
        self.base = self.repo.commit({"a.py": PYTHON.splitlines(True), "a.txt": PYTHON.splitlines(True)})

    def check(self, path: str, start: int, end: int) -> None:
        regions.check_statements(self.repo.root, self.base, path, start, end)

    def test_a_span_starting_or_ending_inside_a_call_is_refused_and_the_file_s_end_is_an_edge(self) -> None:
        with self.assertRaisesRegex(regions.RegionError, r"cuts the statement at lines 11-14 of a.py .*'total = compute\('"):
            self.check("a.py", 12, 16)
        with self.assertRaisesRegex(regions.RegionError, "cuts the statement at lines 11-14"):
            self.check("a.py", 8, 13)
        self.check("a.py", 11, 15)
        self.check("a.py", 19, 22)   # to the end of the file
        self.check("a.txt", 12, 16)  # no statement check outside Python

    def test_insert_widens_one_unclaimed_line_past_the_edge(self) -> None:
        self.assertEqual(regions.widened(4, 8, "after", 21, []), (4, 9))
        self.assertEqual(regions.widened(4, 8, "before", 21, []), (3, 8))
        with self.assertRaisesRegex(regions.RegionError, r"line 8, which \[8, 12\) already claims"):
            regions.widened(4, 8, "after", 21, [(8, 12)])
        with self.assertRaisesRegex(regions.RegionError, "past the end of the file"):
            regions.widened(18, 22, "after", 21, [])
        with self.assertRaisesRegex(regions.RegionError, "before the file"):
            regions.widened(1, 3, "before", 21, [])

    def test_a_span_s_lines_are_shown_cut_to_a_hundred_characters(self) -> None:
        long = "x" * 150 + "\n"
        base = self.repo.commit({"long.txt": ["short\n", long, "end\n"]})
        said = regions.span_lines(self.repo.root, base, "long.txt", 1, 3)
        self.assertEqual(said, f"line 1: 'short' ... line 2: {'x' * 100!r}")
        self.assertEqual(regions.span_lines(self.repo.root, base, "long.txt", 3, 4), "line 3: 'end'")

    def test_a_request_names_its_lines_by_a_unique_line_s_block(self) -> None:
        text = PYTHON.splitlines()
        self.assertEqual(regions.anchor_for("a.py", text, 16, 17, []), ("        return total", None, (16, 17)))
        self.assertEqual(regions.anchor_for("a.py", text, 11, 15, []), ("    total = compute(", None, (11, 15)))
        # An insertion after a held block: the block named, widened by the line after it.
        words, insert, span = regions.anchor_for("a.txt", ["a\n", "  b\n", "c\n", "d\n"], 1, 4, [])
        self.assertLessEqual(span[0], 1)
        self.assertGreaterEqual(span[1], 4)



class GrantLinesTests(CommandCase):
    """Decisions 9 to 13 through lead-own, the pass, claims-check and the brief, on one delivery."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx, _ = command_fixture(cls, {"FX-5": "lane-1", "FX-7": "lane-2", "FX-8": "lane-3"})

    def test_a_grant_a_carve_and_a_carry_print_each_span_s_first_and_last_line(self) -> None:
        long = "y" * 130 + "\n"
        lines = [*numbered(5, "p"), long, *numbered(10, "q")]
        base = self.push_base({"lib/p.txt": lines})
        out = self.grant("lib/p.txt", "lane-1", base, 2, 7, "--story", "FX-5")
        self.assertIn(f"line 2: 'p 2' ... line 6: {'y' * 100!r}", out)
        self.assertNotIn("y" * 101, out)
        out = self.flow("lead-own", "--carve", "lib/p.txt", "--give", "lane-3:around=q 3@FX-8", "--give", "lane-2:12-14")
        self.assertIn("lane-3's for FX-8 [9, 10) at", out)
        self.assertIn("line 9: 'q 3'", out)
        self.assertIn("line 12: 'q 6' ... line 13: 'q 7'", out)
        self.assertIn(claim_of("lane-3", base, 9, 10, "lib/p.txt", "FX-8"), self.claims("lib/p.txt"))
        # A carry, printed by the pass that settles it.
        self.grant("lib/p.txt", "lane-1", base, 14, 17)
        self.one_pass()
        self.land("FX-5", {"lib/p.txt": [*lines[:14], "q 9 changed\n", *lines[15:]]})
        last = self.one_pass()
        self.assertIn("carried lane-1's [14, 17) at", last["regions"])
        self.assertIn("line 14: 'q 8' ... line 16: 'q 10'", last["regions"])

    def test_a_python_span_may_not_cut_a_statement(self) -> None:
        base = self.push_base({"lib/a.py": PYTHON.splitlines(True), "lib/a2.txt": PYTHON.splitlines(True)})
        before = self.claims("lib/a.py")
        out = self.grant("lib/a.py", "lane-1", base, 12, 16, ok=False)
        self.assertIn("cuts the statement at lines 11-14 of lib/a.py ('total = compute(')", out)
        out = self.grant("lib/a.py", "lane-1", base, 8, 13, ok=False)
        self.assertIn("cuts the statement at lines 11-14", out)
        out = self.flow("lead-own", "--carve", "lib/a.py", "--give", "lane-2:12-16", ok=False)
        self.assertIn("the carve of lib/a.py changes nothing: the span [12, 16) cuts the statement", out)
        self.assertEqual(self.claims("lib/a.py"), before)
        self.grant("lib/a.py", "lane-1", base, 19, 22)  # to the end of the file
        self.grant("lib/a2.txt", "lane-1", base, 12, 16)  # no statement check outside Python
        out = self.flow("lead-own", "--region", "lib/a.py", "--lane", "lane-2", "--base", base, "--around", "def waited_on")
        self.assertIn("--around 'def waited_on' names [8, 18)", out)
        self.assertEqual(self.claims("lib/a.py")[-1]["start"], 8)

    def test_insert_widens_a_grant_so_an_insertion_at_its_edge_passes_review_preparation(self) -> None:
        from tests.test_run_delivery import commit
        lines = numbered(20, "w")
        base = self.push_base({"lib/w.txt": lines})
        self.grant("lib/w.txt", "lane-2", base, 12, 16)
        out = self.grant("lib/w.txt", "lane-3", base, 4, 8, "--insert", "after", "--story", "FX-8")
        self.assertIn("--insert after widens [4, 8) to [4, 9)", out)
        self.assertEqual(self.claims("lib/w.txt")[-1]["end"], 9)
        out = self.grant("lib/w.txt", "lane-1", base, 18, 20, "--insert", "before")
        self.assertIn("--insert before widens [18, 20) to [17, 20)", out)
        before = self.claims("lib/w.txt")
        out = self.grant("lib/w.txt", "lane-3", base, 9, 12, "--insert", "after", ok=False)
        self.assertIn("line 12, which [12, 16) already claims", out)
        out = self.grant("lib/w.txt", "lane-3", base, 20, 21, "--insert", "after", ok=False)
        self.assertIn("past the end of the file", out)
        self.assertEqual(self.claims("lib/w.txt"), before)
        # FX-8 inserts after its asked span's last line, at the original edge: inside the widened span.
        self.flow("lead-assign", "FX-8", "--lane", "lane-3", "--title", "lane-3")
        wt = self.fx.lane("FX-8")
        (wt / "lib/w.txt").write_text("".join([*lines[:7], "w inserted\n", *lines[7:]]))
        commit(wt, "FX-8: insert at the edge")
        review = wt / ".orchestration/review/FX-8"
        review.mkdir(parents=True)
        (review / "context.md").write_text("- fixture evidence\n")
        self.flow("review-prepare", "FX-8", "--round", "1", cwd=wt)
        self.assertTrue(list(review.glob("brief-*")))

    def test_claims_check_asks_by_anchor_and_the_brief_says_to(self) -> None:
        from tests.test_run_delivery import commit
        lines = numbered(30, "k")
        base = self.push_base({"lib/k.txt": lines})
        self.grant("lib/k.txt", "lane-2", base, 5, 10)
        wt = self.fx.lane("FX-7")
        (wt / "lib/k.txt").write_text("".join([*lines[:9], "k inserted\n", *lines[9:14], "k fifteen\n", *lines[15:]]))
        commit(wt, "FX-7: one at the edge, one outside")
        code, out = self.fx.flow("claims-check", "FX-7", cwd=wt)
        self.assertEqual(code, 1, out)
        requests = [line for line in out.splitlines() if "run_delivery.py lead-own " in line]
        self.assertEqual(len(requests), 2, out)
        for line in requests:
            self.assertIn("--around ", line)
            self.assertNotIn("--start", line)
            self.assertNotIn("--end", line)
        self.assertTrue(any("--insert" in line for line in requests), out)
        for line in requests:
            self.flow(*shlex.split(line.split("run_delivery.py ", 1)[1].split("   (", 1)[0]))
        self.assertIn("nothing more to claim", self.flow("claims-check", "FX-7", cwd=wt))
        out = self.flow("lead-brief")
        brief = Path(out.split("wrote ", 1)[1].splitlines()[0]).read_text()
        flat = " ".join(brief.split())
        self.assertIn("claims-check <key>` in your checkout", flat)
        self.assertIn("by anchor (`--around TEXT`) or a quoted line, with `--insert before` or `--insert after`", flat)
        self.assertIn("never by bare line numbers", flat)


def claim_of(lane: str, base: str, start: int, end: int, path: str, story: str | None = None) -> dict:
    made = {"lane": lane, "path": path, "baseHead": base, "start": start, "end": end}
    return {**made, "story": story} if story else made


if __name__ == "__main__":
    unittest.main()
