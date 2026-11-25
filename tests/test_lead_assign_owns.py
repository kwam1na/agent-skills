"""What lead-assign and lead-accept take a lane to own: co-changed files claimed at assignment, a pin outside the lane
taken at acceptance with a reason, and an insertion at a span's edge judged at acceptance as review judges it."""

from __future__ import annotations

import json
import unittest

from agent_skills.validate import sentence_text
from tests.test_region_claims import CommandCase, command_fixture, numbered


class AssignAcceptOwnsTests(CommandCase, unittest.TestCase):
    """Decisions 6 and 7 through lead-assign and lead-accept, and Decision 4 at acceptance: an insertion at a span's
    edge is the span's unless another lane claims the far side."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx, cls.delivery = command_fixture(cls, {"FX-5": "lane-1", "FX-6": "lane-1", "FX-7": "lane-2", "FX-8": "lane-3",
                                                     "FX-9": "lane-2", "FX-10": "lane-1", "FX-11": "lane-3",
                                                     "FX-12": "lane-2"})

    def names(self, key: str, path: str) -> None:
        """The story's plan section names ``path``, so lead-assign reads it as one of the story's files."""
        plan = self.fx.main / "docs/plan.md"
        section = f"### S9 ({key}). Thing {key}\n\n- Scenarios: it works.\n"
        self.push_base({"docs/plan.md": plan.read_text().replace(section, section + f"- Files: `{path}`.\n")
                        .splitlines(True)}, f"FX-1: {key} names {path}")

    def co_change(self, named: str, *partners: str) -> None:
        """``named`` and each partner change together four times on the base: a co-change lead-assign warns of."""
        for k in range(4):
            self.push_base({named: [f"{k}\n"], **{path: [f"{path} {k}\n"] for path in partners}}, f"FX-1: change {k}")

    def share(self, path: str) -> None:
        """``path`` becomes a declared shared output of the binding, on the base."""
        from tests.test_run_delivery import commit
        binding_path = self.fx.main / ".agents/orchestration.json"
        binding = json.loads(binding_path.read_text())
        regenerable = binding.get("regenerable") or {}
        binding["regenerable"] = {"paths": [*regenerable.get("paths", []), path], "regenerators": [["true"]],
                                  "listings": [*regenerable.get("listings", []), {"path": path, "argv": ["true"]}]}
        binding_path.write_text(json.dumps(binding, indent=2))
        commit(self.fx.main, f"FX-1: {path} is a shared output")
        self.git("push", "-q", "origin", "HEAD:feature/FX-1")

    def git(self, *argv: str) -> str:
        from tests.test_run_delivery import sh
        return sh("git", *argv, cwd=self.fx.main).stdout.strip()

    def test_own_co_changed_claims_each_warned_file_in_one_commit_and_names_another_lane_s_move(self) -> None:
        self.flow("lead-own", "--grant", "lib/held5.txt", "--lane", "lane-2")
        self.names("FX-5", "lib/c5.txt")
        self.co_change("lib/c5.txt", "lib/p5.txt", "lib/held5.txt", "lib/span5.txt", "gen/shared5.json")
        self.grant("lib/span5.txt", "lane-3", self.git("rev-parse", "HEAD"), 1, 2)
        self.share("gen/shared5.json")
        before, since = self.git("rev-parse", "HEAD"), self.last_id()
        out = self.flow("lead-assign", "FX-5", "--lane", "lane-1", "--title", "lane-1", "--own-co-changed")
        owns = self.record()["state"]["laneOwnership"]
        self.assertIn("lib/p5.txt", owns["lane-1"])
        self.assertNotIn("lib/held5.txt", owns["lane-1"])
        self.assertIn("lib/held5.txt", owns["lane-2"])
        self.assertIn("lead-own --lane lane-1 --move lib/held5.txt --from lane-2", out)
        self.assertIn("lib/span5.txt is shared by lines, so it isn't added", out)
        self.assertNotIn("gen/shared5.json", out)  # every lane may change a declared shared output: no warning names it
        for left in ("lib/span5.txt", "gen/shared5.json"):
            self.assertNotIn(left, owns["lane-1"])
        # One commit, of the delivery file alone, holding the same claim; nothing left for a hand edit.
        [made] = self.git("rev-list", f"{before}..HEAD").split()
        relative = self.delivery.relative_to(self.fx.main).as_posix()
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", made).split(), [relative])
        self.assertIn("lib/p5.txt", json.loads(self.delivery.read_text())["lanes"]["lane-1"]["owns"])
        for left in ("lib/held5.txt", "lib/span5.txt", "gen/shared5.json"):
            self.assertNotIn(left, json.loads(self.delivery.read_text())["lanes"]["lane-1"]["owns"])
        self.assertEqual(self.git("status", "--porcelain"), "")
        granted = [(entry["lane"], entry["data"]["path"]) for entry in self.entries("ownership.granted", since)]
        self.assertEqual(granted, [("lane-1", "lib/p5.txt")])
        self.assertNotIn("Move each to", out)
        self.git("push", "-q", "origin", "HEAD:feature/FX-1")

    def test_without_the_flag_the_warning_ends_by_naming_it_and_claims_nothing(self) -> None:
        self.names("FX-6", "lib/c6.txt")
        self.co_change("lib/c6.txt", "lib/p6.txt")
        before, since = self.git("rev-parse", "HEAD"), self.last_id()
        out = self.flow("lead-assign", "FX-6", "--lane", "lane-1", "--title", "lane-1")
        warning = out.split("Files outside lane-1's owns", 1)[1].split("\n\n", 1)[0]
        self.assertIn("lib/p6.txt (no lane's; with lib/c6.txt in 4 of 4 changes)", warning)
        self.assertTrue(warning.rstrip().endswith("--own-co-changed."), warning)
        self.assertNotIn("Move each to", out)
        self.assertNotIn("lib/p6.txt", self.record()["state"]["laneOwnership"]["lane-1"])
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.entries("ownership.granted", since), [])

    def test_own_co_changed_refuses_a_dirty_checkout_and_commits_nothing_when_every_file_is_another_lane_s(self) -> None:
        self.flow("lead-own", "--grant", "lib/held10.txt", "--lane", "lane-2")
        self.names("FX-10", "lib/c10.txt")
        self.co_change("lib/c10.txt", "lib/held10.txt")
        before, since = self.git("rev-parse", "HEAD"), self.last_id()
        owns = self.record()["state"]["laneOwnership"]
        stray = self.fx.main / "stray.txt"
        stray.write_text("uncommitted\n")
        try:
            out = self.flow("lead-assign", "FX-10", "--lane", "lane-1", "--title", "lane-1", "--own-co-changed", ok=False)
        finally:
            stray.unlink()
        self.assertIn("has uncommitted changes", out)
        self.assertEqual((self.record()["state"]["laneOwnership"], self.git("rev-parse", "HEAD"), self.last_id()),
                         (owns, before, since))
        out = self.flow("lead-assign", "FX-10", "--lane", "lane-1", "--title", "lane-1", "--own-co-changed")
        self.assertIn("lead-own --lane lane-1 --move lib/held10.txt --from lane-2", out)
        self.assertNotIn("committed the delivery file", out)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.entries("ownership.granted", since), [])
        self.assertEqual(self.record()["state"]["laneOwnership"], owns)

    def test_a_warning_of_named_files_alone_keeps_its_last_line(self) -> None:
        self.names("FX-11", "lib/c11.txt")
        out = self.flow("lead-assign", "FX-11", "--lane", "lane-3", "--title", "lane-3")
        self.assertIn("FX-11 names lib/c11.txt (no lane's)\n"
                      "Move each to lane-3's owns now, or plan the story on the lane that owns it.\n", out)
        self.assertNotIn("--own-co-changed", out)
        # A no-change hand-off changes no file, so --take has nothing to take, and is refused writing nothing.
        wt = self.fx.lane("FX-11")
        self.flow("handoff", "FX-11", "--no-change", "--evidence", "fx-11-works: it works at the base", cwd=wt)
        state, length = self.record()["state"], len(self.record()["journal"])
        out = self.flow("lead-accept", "FX-11", "--take", "lib/c11.txt", "--why", "a pin", ok=False)
        self.assertIn("FX-11 has a no-change hand-off, which changes no file, so there is nothing to take", out)
        self.assertEqual((self.record()["state"]["laneOwnership"], self.record()["state"]["planned"],
                          len(self.record()["journal"])), (state["laneOwnership"], state["planned"], length))

    def test_take_accepts_a_pin_outside_the_lane_with_its_reason_and_refuses_what_it_can_t_take(self) -> None:
        from tests.test_run_delivery import commit, sh
        from tests.test_run_delivery_supervisor import hand_off
        m = self.fx.main
        self.share("gen/shared9.json")  # a declared shared output, which no lane takes
        base = self.push_base({"lib/pin1.txt": ["one\n"], "lib/pin2.txt": ["two\n"], "lib/held9.txt": ["held\n"],
                               "lib/untouched9.txt": ["untouched\n"], "gen/shared9.json": ["{}\n"],
                               "lib/span9.txt": numbered(6, "s")})
        self.flow("lead-own", "--grant", "lib/held9.txt", "--lane", "lane-1")
        self.grant("lib/span9.txt", "lane-1", base, 1, 3)
        # The lane held both pins when it handed off; the lead narrowed its claims since.
        for path in ("lib/pin1.txt", "lib/pin2.txt"):
            self.flow("lead-own", "--grant", path, "--lane", "lane-2")
        hand_off(self.fx, "FX-9", "lane-2", files={"lib/pin1.txt": "one pinned\n", "lib/pin2.txt": "two pinned\n"})
        for path in ("lib/pin1.txt", "lib/pin2.txt"):
            self.flow("lead-own", "--revoke", path, "--lane", "lane-2")
        refused = self.flow("lead-accept", "FX-9", ok=False)
        self.assertIn("changes files outside lane-2's", refused)
        state, length = self.record()["state"], len(self.record()["journal"])
        for argv, said in ((("--take", "lib/held9.txt", "--why", "a pin"), "lib/held9.txt is lane-1's"),
                           (("--take", "gen/shared9.json", "--why", "a pin"), "gen/shared9.json is a declared shared output"),
                           (("--take", "lib/untouched9.txt", "--why", "a pin"), "FX-9 doesn't change lib/untouched9.txt"),
                           (("--take", "src/two.txt", "--why", "a pin"), "lane-2 holds src/two.txt already"),
                           (("--take", "lib/span9.txt", "--why", "a pin"), "lanes share lib/span9.txt by lines"),
                           (("--take", "/lib/pin1.txt", "--why", "a pin"), "name a repository-relative file"),
                           (("--take", "lib/pin1.txt"), "--take needs --why")):
            with self.subTest(argv=argv):
                self.assertIn(said, self.flow("lead-accept", "FX-9", *argv, ok=False))
                record = self.record()
                self.assertEqual((record["state"]["laneOwnership"], record["state"]["queue"], len(record["journal"])),
                                 (state["laneOwnership"], state["queue"], length))
        since = self.last_id()
        out = self.flow("lead-accept", "FX-9", "--take", "lib/pin1.txt", "--take", "lib/pin2.txt", "--why",
                        "one-line pins the story needs in files no lane holds")
        self.assertIn("accepted FX-9", out)
        owns = self.record()["state"]["laneOwnership"]["lane-2"]
        self.assertTrue({"lib/pin1.txt", "lib/pin2.txt"} <= set(owns), owns)
        granted = [(entry["lane"], entry["data"]["path"]) for entry in self.entries("ownership.granted", since)]
        self.assertEqual(granted, [("lane-2", "lib/pin1.txt"), ("lane-2", "lib/pin2.txt")])
        [decision] = self.entries("decision.recorded", since)
        self.assertEqual(decision["subject"], "FX-9")
        self.assertIn("lib/pin1.txt, lib/pin2.txt", decision["data"]["text"])
        self.assertEqual(decision["data"]["why"], "one-line pins the story needs in files no lane holds")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)

    def test_handoff_names_the_grant_or_move_that_clears_an_out_of_lane_file_before_review(self) -> None:
        from tests.test_run_delivery_supervisor import hand_off
        base = self.push_base({"lib/out12.txt": ["out\n"], "lib/g12/other.txt": ["other\n"],
                               "lib/span12.txt": numbered(6, "s")})
        for pattern in ("lib/out12.txt", "lib/g12/*"):
            self.flow("lead-own", "--grant", pattern, "--lane", "lane-2")
        self.grant("lib/span12.txt", "lane-2", base, 1, 3)
        self.grant("lib/span12.txt", "lane-3", base, 4, 6)
        refusals = []

        def narrowed(wt, url) -> None:
            # Narrowed after review: a file no lane holds, a file another lane holds by a glob, and a file lanes
            # share by lines whose changed span no lane holds.
            self.flow("lead-own", "--revoke", "lib/out12.txt", "--lane", "lane-2")
            self.flow("lead-own", "--move", "lib/g12/*", "--lane", "lane-3", "--from", "lane-2")
            self.flow("lead-own", "--revoke-region", "lib/span12.txt", "--lane", "lane-2", "--base", base,
                      "--start", "1", "--end", "3")
            refused = self.flow("handoff", "FX-12", "--pr", url, cwd=wt, ok=False)
            refusals.append(refused)
            self.assertIn("writer_not_owned: FX-12 changes files outside lane-2's", refused)
            self.assertIn("the lead clears each whole file before hand-off with:", refused)
            self.assertIn("lead-own --grant lib/out12.txt --lane lane-2\n", refused)
            # The other lane's own pattern, which lead-own --move takes, not the file's path.
            self.assertIn("lead-own --move 'lib/g12/*' --lane lane-2 --from lane-3", refused)
            self.assertIn("lib/span12.txt: ", refused)
            for wrong in ("--take", "lib/g12/other.txt --lane", "--grant lib/span12.txt", "--grant 'lib/span12.txt"):
                self.assertNotIn(wrong, refused)
            # The named commands, and a span for the shared file, are what clear it: the hand-off that follows succeeds.
            self.flow("lead-own", "--grant", "lib/out12.txt", "--lane", "lane-2")
            self.flow("lead-own", "--move", "lib/g12/*", "--lane", "lane-2", "--from", "lane-3")
            self.grant("lib/span12.txt", "lane-2", base, 1, 3)

        hand_off(self.fx, "FX-12", "lane-2", before=narrowed,
                 files={"lib/out12.txt": "out changed\n", "lib/g12/other.txt": "other changed\n",
                        "lib/span12.txt": "".join(["s 1 changed\n", *numbered(6, "s")[1:]])})
        self.assertEqual(len(refusals), 1)

    def test_take_is_described_as_for_a_file_narrowed_out_after_hand_off(self) -> None:
        from tests.test_run_delivery import ROOT
        flat = " ".join((ROOT / "skills/run-delivery/SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("`--take PATH --why T`, repeatable with one `--why`, is for a file the lead narrowed out of the "
                      "lane's claims after the lane handed off, and accepts its change: review-prepare, review-reduce "
                      "and handoff already refuse a change to a file outside the lane, and the `lead-own --grant` that "
                      "refusal names, or `--move` for a file another lane holds, clears it before review.", flat)
        help_text = " ".join(self.flow("lead-accept", "--help").split())
        self.assertIn("for a file the lead narrowed out of the lane's claims after it handed off: grant the lane a "
                      "whole-file claim on it, then accept; repeatable, and needs --why. Before hand-off, "
                      "review-prepare and handoff refuse an out-of-lane file, and lead-own --grant clears it", help_text)
        sentence = ("lead-accept --take is for a file the lead narrowed out of the lane's claims after the lane handed "
                    "off.")
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        texts = {"catalog.json": next(skill["degradedBehavior"] for skill in catalog["skills"]
                                      if skill["id"] == "run-delivery")}
        for name in ("jira", "linear", "run-delivery"):
            texts[f"profiles/{name}.json"] = json.loads((ROOT / f"profiles/{name}.json").read_text(encoding="utf-8"))[
                "description"]
        for name, sentences in texts.items():
            with self.subTest(file=name):
                self.assertIn(sentence, sentence_text(sentences))

    def test_acceptance_refuses_an_edge_insertion_next_to_another_lane_and_takes_it_otherwise(self) -> None:
        from tests.test_run_delivery_supervisor import hand_off
        lines = numbered(20, "a")
        base = self.push_base({"lib/a.txt": lines})
        self.grant("lib/a.txt", "lane-2", base, 5, 10)
        # lane-2's last line, replaced by itself and a new line: an insertion just past its span.
        hand_off(self.fx, "FX-7", "lane-2", files={"lib/a.txt": "".join([*lines[:9], "a 10 more\n", *lines[9:]])})
        self.grant("lib/a.txt", "lane-3", base, 10, 12, "--story", "FX-8")
        out = self.flow("lead-accept", "FX-7", ok=False)
        self.assertIn("changes files outside lane-2's", out)
        self.assertIn("lib/a.txt: insertion before line 10, next to lane-3's claim is outside a single owned span", out)
        self.flow("lead-own", "--revoke-region", "lib/a.txt", "--lane", "lane-3", "--base", base, "--start", 10, "--end", 12)
        self.flow("lead-accept", "FX-7")


if __name__ == "__main__":
    unittest.main()
