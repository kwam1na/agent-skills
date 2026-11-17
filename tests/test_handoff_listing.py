"""`handoff` asks the merge queue's own listing check before it records anything.

A lane that adds or removes an output a regenerable listing names, in a listing its lane doesn't own, was refused only
by the merge queue, after the lead had accepted it. These cases hand off from the fixture delivery under a binding that
declares one listing, `build/listing.json`, and check that such a hand-off is refused, naming the file, the outputs and the
grant to ask for, with nothing recorded; that a lane owning the listing hands off; and that a digest-only refresh hands
off from any lane.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from tests.test_run_delivery import Fixture, commit, installed_release, setup_delivery, sh, write_results

RUN_QUIETLY = True

LISTING = "build/listing.json"
# The listing's command, as the queue runs it: the file's bytes on stdin, the outputs it lists as a JSON array.
# A file without them is refused in words, as a listing command says what it can't read. The queue gives a listing
# its step's values, and this one takes `{head}`: run without the commit, it refuses.
LIST = ("import json, re, sys; d = json.load(sys.stdin); "
        "re.fullmatch('[0-9a-f]{40}', sys.argv[1]) or sys.exit('no head given'); "
        "print(json.dumps(sorted(d['outputs']))) if 'outputs' in d else sys.exit('it lists no outputs')")


class HandoffListingTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def delivery(self) -> Fixture:
        """The fixture delivery with one regenerable listing on the base, which no lane owns, and no in-app checks."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        m = fx.main
        path = setup_delivery(fx)
        d = json.loads(path.read_text())
        for key in ("FX-2", "FX-3"):
            d["stories"][key]["inApp"] = "none"
        path.write_text(json.dumps(d, indent=2))
        (m / ".agents/orchestration.json").write_text(json.dumps({
            "base": {"remote": "origin", "ref": "feature/FX-1"}, "checkTimeoutSeconds": 600,
            "state": {"forgeSnapshot": {"argv": ["true"]}},
            "regenerable": {"paths": [LISTING], "regenerators": [["true"]],
                            "listings": [{"path": LISTING, "argv": [sys.executable, "-c", LIST, "{head}"]}]}}, indent=2))
        self.listing(m, ["a"], "1")
        commit(m, "FX-1: a regenerable listing")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        return fx

    def listing(self, checkout: Path, outputs: list[str], digest: str) -> None:
        (checkout / "build").mkdir(exist_ok=True)
        (checkout / LISTING).write_text(json.dumps({"outputs": outputs, "digest": digest}) + "\n")

    def candidate(self, fx: Fixture, key: str, lane: str, own: str, outputs: list[str], digest: str) -> tuple[Path, str]:
        """The story assigned, changed in its own file and the listing, reviewed once and its draft PR open."""
        code, out = fx.flow("lead-assign", key, "--lane", lane, "--title", lane, cwd=fx.main)
        self.assertEqual(code, 0, out)
        wt = fx.lane(key)
        (wt / own).write_text(f"{key}\n")
        self.listing(wt, outputs, digest)
        commit(wt, f"{key}: its change")
        (wt / f".orchestration/review/{key}").mkdir(parents=True)
        (wt / f".orchestration/review/{key}/context.md").write_text("- evidence")
        self.reviewed(fx, key, wt, 1)
        body = fx.tmp / f"{key}.md"
        body.write_text("x\n")
        code, out = fx.flow("open-pr", key, "--title", f"{key}: change", "--body-file", body, cwd=wt)
        self.assertEqual(code, 0, out)
        return wt, out.strip().splitlines()[-1]

    def reviewed(self, fx: Fixture, key: str, wt: Path, n: int) -> None:
        """Gates passed, one aligned round at HEAD, and the branch pushed."""
        for argv in (("gate", key), ("review-prepare", key, "--round", n)):
            code, out = fx.flow(*argv, cwd=wt)
            self.assertEqual(code, 0, out)
        write_results(wt / f".orchestration/review/{key}", n, key, fx.lenses(wt))
        code, out = fx.flow("review-reduce", key, "--round", n, cwd=wt)
        self.assertEqual(code, 0, out)
        sh("git", "push", "-q", "origin", f"HEAD:feature/{key}", cwd=wt)

    def recorded(self, fx: Fixture, wt: Path, key: str) -> tuple:
        """What a hand-off writes: the record's journal and state, and the review folder's hand-off files."""
        read = lambda path: path.read_bytes() if path.exists() else None
        review = wt / f".orchestration/review/{key}"
        return (read(fx.tmp / "record/journal.jsonl"), read(fx.tmp / "record/state.json"), read(review / "blocked.json"),
                sorted(p.name for p in review.iterdir()))

    def handoff(self, fx: Fixture, key: str, wt: Path, url: str, *words: str) -> None:
        """A hand-off refused with each of `words` in its output, and nothing recorded."""
        before = self.recorded(fx, wt, key)
        code, out = fx.flow("handoff", key, "--pr", url, cwd=wt)
        self.assertEqual(code, 2, out)
        for said in words:
            self.assertIn(said, out)
        self.assertEqual(self.recorded(fx, wt, key), before, "a refused hand-off recorded something")

    def test_a_listing_change_is_refused_until_the_lane_owns_the_listing_alone(self) -> None:
        fx = self.delivery()
        m = fx.main
        own = lambda *argv: self.assertEqual(fx.flow("lead-own", *argv, cwd=m)[0], 0)
        wt, url = self.candidate(fx, "FX-2", "lane-1", "src/one.txt", ["b"], "2")
        # No lane holds the listing: the lead grants it.
        self.handoff(fx, "FX-2", wt, url,
                     "FX-2 changes the outputs a listing names, in files lane-1 doesn't own alone, and the merge queue "
                     "would stop it after acceptance:",
                     f"{LISTING} (added b; removed a): ask the lead to grant it, with ",
                     f"lead-own --lane lane-1 --grant {LISTING}",
                     "then hand off again. Refreshing a listing's digests alone needs no grant.")
        # Another lane holds it: the lead moves it, by that lane's claim.
        own("--lane", "lane-2", "--grant", "build/*")
        self.handoff(fx, "FX-2", wt, url, f"{LISTING} (added b; removed a): it is held by lane-2, so ask the lead to make "
                     f"it lane-1's alone, with ", "lead-own --lane lane-1 --move build/* --from lane-2")
        # Both hold it: still lane-1's alone is what the queue takes, so the same move.
        own("--lane", "lane-1", "--grant", LISTING)
        self.handoff(fx, "FX-2", wt, url, f"{LISTING} (added b; removed a): it is shared with lane-2, so ask the lead to "
                     "make it lane-1's alone, with ", "lead-own --lane lane-1 --move build/* --from lane-2")
        self.assertNotIn("--grant", fx.flow("handoff", "FX-2", "--pr", url, cwd=wt)[1])
        # Moved, the listing is lane-1's alone, and the same candidate hands off.
        own("--lane", "lane-1", "--move", "build/*", "--from", "lane-2")
        code, out = fx.flow("handoff", "FX-2", "--pr", url, cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertIn("**Hand-off: FX-2**", out)

    def test_a_removed_output_or_a_listing_that_cant_be_read_is_refused_and_a_digest_refresh_hands_off(self) -> None:
        fx = self.delivery()
        wt, url = self.candidate(fx, "FX-3", "lane-2", "src/two.txt", [], "2")
        self.handoff(fx, "FX-3", wt, url, f"{LISTING} (removed a): ask the lead to grant it, with ",
                     f"lead-own --lane lane-2 --grant {LISTING}")
        # A listing its command can't read is the queue's refusal too, said as the hand-off's, and records nothing.
        (wt / LISTING).write_text("{}\n")
        commit(wt, "FX-3: a listing with no outputs")
        self.reviewed(fx, "FX-3", wt, 2)
        self.handoff(fx, "FX-3", wt, url, "run-delivery: the merge queue's listing check can't run at ",
                     f"the listing of {LISTING} at the candidate exited 1: it lists no outputs")
        # The output put back, only the digest differs from the base's: the listing is unchanged, so no grant is needed.
        self.listing(wt, ["a"], "3")
        commit(wt, "FX-3: only the digest moves")
        self.reviewed(fx, "FX-3", wt, 3)
        code, out = fx.flow("handoff", "FX-3", "--pr", url, cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertIn("**Hand-off: FX-3**", out)

    def test_the_brief_names_each_listing_the_binding_declares_and_says_nothing_without_one(self) -> None:
        fx = self.delivery()
        m = fx.main
        rule = (f" Adding or removing an output `{LISTING}` lists needs that file's owner: ask the lead to grant it before "
                "you hand off. Refreshing its digests alone needs no grant.")
        code, out = fx.flow("lead-brief", cwd=m)
        self.assertEqual(code, 0, out)
        brief = (m / "docs/lane-brief.md").read_text()
        self.assertIn("The lead refuses a hand-off that changes anything else." + rule + "\n", brief)
        self.assertEqual(brief.count("needs that file's owner"), 1, "the rule is stated once")
        binding = m / ".agents/orchestration.json"
        declared = json.loads(binding.read_text())

        def brief_with(regenerable: dict) -> str:
            binding.write_text(json.dumps({**declared, "regenerable": regenerable}, indent=2))
            commit(m, "FX-1: the listings move")
            code, out = fx.flow("lead-brief", cwd=m)
            self.assertEqual(code, 0, out)
            return (m / "docs/lane-brief.md").read_text()
        # Two listings are named in one sentence.
        two = {**declared["regenerable"], "paths": [LISTING, "build/other.json"],
               "listings": [*declared["regenerable"]["listings"], {"path": "build/other.json", "argv": ["true"]}]}
        self.assertIn(f" Adding or removing an output `{LISTING}` or `build/other.json` lists needs that file's owner",
                      brief_with(two))
        # With no regenerators, neither the queue nor the hand-off checks a listing, so the brief names none; with no
        # listings declared it says nothing of them either, and the sentence before reads as it always did.
        for regenerable in ({**declared["regenerable"], "regenerators": []}, {**declared["regenerable"], "listings": []}):
            brief = brief_with(regenerable)
            self.assertIn("The lead refuses a hand-off that changes anything else.\n", brief)
            self.assertNotIn("needs that file's owner", brief)
            self.assertNotIn("{{listings}}", brief)


if __name__ == "__main__":
    unittest.main()
