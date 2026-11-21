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

    def test_each_block_rule_at_its_edge(self) -> None:
        base = self.repo.commit({
            "b.py": ["x = 0\n", "if x: a = 1\n", "else: a = 2\n", "y = 3\n"],
            "b.txt": ["a:\n", "  b\n", "\n", "c\n"],
            "b.md": ["# H\n", "para one\n", "para two\n", "\n", "after\n"],
            "many.txt": [f"dup {i}\n" for i in range(12)]})
        around = lambda path, anchor: regions.anchored(self.repo.root, base, path, anchor)
        self.assertEqual(around("b.py", "if x: a = 1"), (2, 4))   # the outermost of the statements starting there
        self.assertEqual(around("b.txt", "a:"), (1, 3))           # a trailing blank line is not the block's
        self.assertEqual(around("b.md", "para two"), (2, 4))      # a paragraph stops at the heading above it
        with self.assertRaises(regions.RegionError) as caught:
            around("many.txt", "dup")
        said = str(caught.exception)
        self.assertIn("12 lines of many.txt", said)
        self.assertIn("line 10: 'dup 9'", said)
        self.assertNotIn("line 11:", said)
        self.assertIn("and 2 more", said)

    def test_a_heading_like_line_inside_a_code_fence_is_code_not_a_heading(self) -> None:
        fenced = ["# Top\n", "\n", "## Usage\n", "\n", "```bash\n", "# install it\n", "make\n", "```\n", "\n",
                  "More usage.\n", "\n", "## Next\n", "Text.\n"]
        base = self.repo.commit({"fenced.md": fenced})
        self.assertEqual(regions.anchored(self.repo.root, base, "fenced.md", "## Usage"), (3, 12))
        self.assertEqual(regions.anchored(self.repo.root, base, "fenced.md", "# install it"), (5, 9))
        # A fence closes only on its own marker: a ~~~ line inside a ``` fence is code, and so is the # after it.
        mixed = ["## Usage\n", "\n", "```\n", "~~~\n", "# not a heading\n", "```\n", "\n", "## Next\n"]
        base = self.repo.commit({"mixed.md": mixed})
        self.assertEqual(regions.anchored(self.repo.root, base, "mixed.md", "## Usage"), (1, 8))

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


    def test_an_anchor_pair_names_the_lines_from_its_anchor_through_the_first_line_after_holding_through(self) -> None:
        pair = lambda path, anchor, through: regions.anchor_pair(self.repo.root, self.base, path, anchor, through)
        self.assertEqual(pair("a.py", "def waited_on", "return 0"), (10, 18))
        self.assertEqual(pair("a.py", "return total", "return total"), (16, 17))   # one line, inclusive
        self.assertEqual(pair("dup.txt", "x", "same"), (2, 4))                       # the first at or after it
        with self.assertRaises(regions.RegionError) as caught:
            pair("dup.txt", "same", "x")
        said = str(caught.exception)
        self.assertIn("3 lines of dup.txt at", said)
        for number in (1, 3, 4):
            self.assertIn(f"line {number}: 'same'", said)
        with self.assertRaisesRegex(regions.RegionError, "no line of a.py at .* holds 'nowhere'"):
            pair("a.py", "nowhere", "return 0")
        with self.assertRaisesRegex(regions.RegionError, "no line of a.py at .* from line 21 on holds 'return 1': "
                                                         r"line 5: '    return 1', each before it"):
            pair("a.py", "return 2", "return 1")
        with self.assertRaisesRegex(regions.RegionError, "from line 5 on holds 'nowhere'. Name a --through"):
            pair("a.py", "return 1", "nowhere")


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
        for start, end in ((8, 14), (14, 16)):  # one line short of the call's closing line, and starting on it
            with self.subTest(start=start, end=end), self.assertRaisesRegex(regions.RegionError, "lines 11-14"):
                self.check("a.py", start, end)
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
        # No block holds lines 1-3 alone: the smallest one widened by the line after it does.
        self.assertEqual(regions.anchor_for("a.txt", ["a", "  b", "c", "d"], 1, 4, []), ("a", "after", (1, 4)))
        # On a tie in size, a later line's own block beats an earlier line's widened one.
        self.assertEqual(regions.anchor_for("a.txt", ["a", "b:", "  c", "d"], 2, 3, []), ("b:", None, (2, 4)))
        # An insertion's one line, by --insert: never into a claimed line, so the other side's block is named.
        self.assertEqual(regions.anchor_for("a.txt", ["p", "q", "r"], 2, 3, [], True), ("p", "after", (1, 3)))
        self.assertEqual(regions.anchor_for("a.txt", ["p", "q", "r"], 2, 3, [(2, 3)], True), ("q", "after", (2, 4)))



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
        for argv, said in (
                (("--region", "lib/a.py", "--lane", "lane-1", "--base", base, "--around", "def after", "--start", 1, "--end", 2),
                 "not both"),
                (("--revoke-region", "lib/a.py", "--lane", "lane-1", "--base", base, "--around", "def after"),
                 "a revocation matches the claim by its coordinates"),
                (("--carve", "lib/a.py", "--around", "def after", "--give", "lane-1:19-22"), "in its --give")):
            with self.subTest(argv=argv[0]):
                self.assertIn(said, self.flow("lead-own", *argv, ok=False))
                self.assertEqual(self.claims("lib/a.py"), before)
        self.grant("lib/a.py", "lane-1", base, 19, 22)  # to the end of the file
        self.grant("lib/a2.txt", "lane-1", base, 12, 16)  # no statement check outside Python
        out = self.flow("lead-own", "--region", "lib/a.py", "--lane", "lane-2", "--base", base, "--around", "def waited_on")
        self.assertIn("--around 'def waited_on' names [8, 18)", out)
        self.assertEqual(self.claims("lib/a.py")[-1]["start"], 8)

    def test_an_anchor_pair_grants_its_lines_and_a_carve_gives_the_same(self) -> None:
        base = self.push_base({"lib/n.py": PYTHON.splitlines(True), "lib/m.py": PYTHON.splitlines(True)})
        out = self.flow("lead-own", "--region", "lib/n.py", "--lane", "lane-1", "--base", base,
                        "--anchor", "def waited_on", "--through", "return 0")
        self.assertIn("--anchor 'def waited_on' --through 'return 0' names [10, 18)", out)
        self.assertIn("line 10: 'def waited_on(value):' ... line 17: '    return 0'", out)
        self.assertEqual(self.claims("lib/n.py"), [claim_of("lane-1", base, 10, 18, "lib/n.py")])
        [granted] = self.entries("ownership.granted")[-1:]
        self.assertEqual((granted["data"]["span"], granted["data"]["base"]["head"]), ({"first": 10, "after": 18}, base))
        out = self.flow("lead-own", "--carve", "lib/m.py", "--give", "lane-2:anchor=def waited_on..through=return 0@FX-7")
        self.assertEqual(self.claims("lib/m.py"), [claim_of("lane-2", base, 10, 18, "lib/m.py", "FX-7")])
        self.assertIn("lane-2: --anchor 'def waited_on' --through 'return 0' names [10, 18)", out)
        before = {path: self.claims(path) for path in ("lib/n.py", "lib/m.py")}
        region = ("--region", "lib/n.py", "--lane", "lane-2", "--base", base)
        for argv, said in (
                ((*region, "--anchor", "def first", "--through", "    total", "--start", 1), "not with --start"),
                ((*region, "--anchor", "def first", "--through", "    total", "--around", "def after"), "not with"),
                ((*region, "--anchor", "def first"), "give both"),
                ((*region, "--anchor", "return", "--through", "def after"), "lines of lib/n.py at"),
                ((*region, "--anchor", "def after", "--through", "def first"), "each before it"),
                ((*region, "--anchor", "value,", "--through", "return 0"), "cuts the statement at lines 11-14"),
                ((*region, "--anchor", "def first", "--through", ""), "--through names some text of a line"),
                (("--claim", "lib/n.py", "--anchor", "def first", "--through", "return 1"), "use them with --region"),
                (("--revoke-region", "lib/n.py", "--lane", "lane-1", "--base", base, "--start", 10, "--end", 18,
                  "--anchor", "def first", "--through", "return 1"), "use them with --region"),
                (("--carve", "lib/m.py", "--anchor", "def first", "--give", "lane-1:1-2"), "in its --give"),
                (("--carve", "lib/m.py", "--give", "lane-1:anchor=        value,..through=return 0"),
                 "the carve of lib/m.py changes nothing: the span [12, 18) cuts the statement")):
            with self.subTest(argv=argv):
                self.assertIn(said, self.flow("lead-own", *argv, ok=False))
        self.assertEqual({path: self.claims(path) for path in before}, before)

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
        # A give widens the same way, and never into another give's line or another lane's claim.
        v = numbered(20, "v")
        vbase = self.push_base({"lib/v.txt": v})
        out = self.flow("lead-own", "--carve", "lib/v.txt", "--give", "lane-3+after:4-8")
        self.assertIn("--insert after widens [4, 8) to [4, 9)", out)
        self.assertEqual(self.claims("lib/v.txt"), [claim_of("lane-3", vbase, 4, 9, "lib/v.txt")])
        out = self.flow("lead-own", "--carve", "lib/v.txt", "--give", "lane-1:around=v 12", "--give", "lane-2+before:around=v 13",
                        ok=False)
        self.assertIn("line 12, which [12, 13) already claims", out)
        out = self.flow("lead-own", "--carve", "lib/v.txt", "--give", "lane-1+before:9-11", ok=False)
        self.assertIn("line 8, which [4, 9) already claims", out)
        self.assertEqual(self.claims("lib/v.txt"), [claim_of("lane-3", vbase, 4, 9, "lib/v.txt")])
        # FX-8 inserts after its asked span's last line, at the original edge: inside the widened span. It also
        # inserts just past the widened span, at its new edge.
        self.flow("lead-assign", "FX-8", "--lane", "lane-3", "--title", "lane-3")
        wt = self.fx.lane("FX-8")
        (wt / "lib/w.txt").write_text("".join([*lines[:7], "w inserted\n", lines[7], "w past the edge\n", *lines[8:]]))
        commit(wt, "FX-8: insert at the edge")
        review = wt / ".orchestration/review/FX-8"
        review.mkdir(parents=True)
        (review / "context.md").write_text("- fixture evidence\n")
        # Another lane claims the line past the edge: the insertion there is refused, naming that lane.
        self.grant("lib/w.txt", "lane-2", base, 9, 12, "--story", "FX-7")
        out = self.flow("review-prepare", "FX-8", "--round", "1", cwd=wt, ok=False)
        self.assertIn("writer_not_owned", out)
        self.assertIn("lib/w.txt: insertion before line 9, next to lane-2's claim is outside a single owned span", out)
        self.assertFalse(list(review.glob("brief-*")))
        # No lane claims it: the insertion at the edge is the span's.
        self.flow("lead-own", "--revoke-region", "lib/w.txt", "--lane", "lane-2", "--base", base, "--start", 9, "--end", 12)
        self.flow("review-prepare", "FX-8", "--round", "1", cwd=wt)
        self.assertTrue(list(review.glob("brief-*")))

    def test_claims_check_asks_by_anchor_and_the_brief_says_to(self) -> None:
        from tests.test_run_delivery import commit
        lines = numbered(30, "k")
        j = ["group:\n", "  a\n", "end\n", "other 1\n", "other 2\n"]
        base = self.push_base({"lib/k.txt": lines, "lib/j.txt": j})
        self.grant("lib/k.txt", "lane-2", base, 5, 10)
        self.grant("lib/j.txt", "lane-3", base, 4, 6)
        wt = self.fx.lane("FX-7")
        (wt / "lib/k.txt").write_text("".join([*lines[:9], "k inserted\n", *lines[9:14], "k fifteen\n", *lines[15:]]))
        # Two new lines inside the group's block, not at an edge of a span the lane holds: the block, without --insert.
        (wt / "lib/j.txt").write_text("".join([*j[:1], "  new one\n", "  new two\n", *j[1:]]))
        commit(wt, "FX-7: one at the edge, one outside")
        # Another lane claims the line past lane-2's span: the insertion at its edge needs a claim, naming that lane,
        # and only a carve can give it.
        self.grant("lib/k.txt", "lane-3", base, 10, 12)
        code, out = self.fx.flow("claims-check", "FX-7", cwd=wt)
        self.assertEqual(code, 1, out)
        self.assertIn("lib/k.txt insertion before line 10, next to lane-3's claim: outside lane-2's spans", out)
        [edge] = [line for line in out.splitlines() if "run_delivery.py lead-own " in line and "--around='k 10'" in line]
        self.assertIn("(overlaps lane-3's [10, 12)", edge)
        self.assertNotIn("--insert", edge)
        self.flow("lead-own", "--revoke-region", "lib/k.txt", "--lane", "lane-3", "--base", base, "--start", 10, "--end", 12)
        # No lane claims it: the insertion is held, and what is left is asked by anchor, without --insert.
        code, out = self.fx.flow("claims-check", "FX-7", cwd=wt)
        self.assertEqual(code, 1, out)
        self.assertIn("holds lib/k.txt insertion before line 10: inside lane-2's [5, 10)", out)
        requests = [line for line in out.splitlines() if "run_delivery.py lead-own " in line]
        self.assertEqual(len(requests), 2, out)
        [grouped] = [line for line in requests if "lib/j.txt" in line]
        self.assertIn("--around=group:", grouped)
        for line in requests:
            self.assertIn("--around=", line)
            self.assertNotIn("--start", line)
            self.assertNotIn("--end", line)
            self.assertNotIn("--insert", line)
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
