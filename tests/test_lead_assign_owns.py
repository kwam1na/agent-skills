"""What lead-assign and lead-accept take a lane to own: co-changed files claimed at assignment, a pin outside the lane
taken at acceptance with a reason, and an insertion at a span's edge judged at acceptance as review judges it."""

from __future__ import annotations

import json
import unittest

from tests.test_region_claims import CommandCase, command_fixture, numbered


class AssignAcceptOwnsTests(CommandCase, unittest.TestCase):
    """Decisions 6 and 7 through lead-assign and lead-accept, and Decision 4 at acceptance: an insertion at a span's
    edge is the span's unless another lane claims the far side."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx, cls.delivery = command_fixture(cls, {"FX-5": "lane-1", "FX-6": "lane-1", "FX-7": "lane-2", "FX-8": "lane-3",
                                                     "FX-9": "lane-2"})

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

    def git(self, *argv: str) -> str:
        from tests.test_run_delivery import sh
        return sh("git", *argv, cwd=self.fx.main).stdout.strip()

    def test_own_co_changed_claims_each_warned_file_in_one_commit_and_names_another_lane_s_move(self) -> None:
        self.flow("lead-own", "--grant", "lib/held5.txt", "--lane", "lane-2")
        self.names("FX-5", "lib/c5.txt")
        self.co_change("lib/c5.txt", "lib/p5.txt", "lib/held5.txt")
        before, since = self.git("rev-parse", "HEAD"), self.last_id()
        out = self.flow("lead-assign", "FX-5", "--lane", "lane-1", "--title", "lane-1", "--own-co-changed")
        owns = self.record()["state"]["laneOwnership"]
        self.assertIn("lib/p5.txt", owns["lane-1"])
        self.assertNotIn("lib/held5.txt", owns["lane-1"])
        self.assertIn("lib/held5.txt", owns["lane-2"])
        self.assertIn("lead-own --lane lane-1 --move lib/held5.txt --from lane-2", out)
        # One commit, of the delivery file alone, holding the same claim; nothing left for a hand edit.
        [made] = self.git("rev-list", f"{before}..HEAD").split()
        relative = self.delivery.relative_to(self.fx.main).as_posix()
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", made).split(), [relative])
        self.assertIn("lib/p5.txt", json.loads(self.delivery.read_text())["lanes"]["lane-1"]["owns"])
        self.assertNotIn("lib/held5.txt", json.loads(self.delivery.read_text())["lanes"]["lane-1"]["owns"])
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

    def test_take_accepts_a_pin_outside_the_lane_with_its_reason_and_refuses_what_it_can_t_take(self) -> None:
        from tests.test_run_delivery import commit, sh
        from tests.test_run_delivery_supervisor import hand_off
        m = self.fx.main
        # A declared shared output, which no lane takes.
        binding_path = m / ".agents/orchestration.json"
        binding = json.loads(binding_path.read_text())
        binding["regenerable"] = {"paths": ["gen/shared9.json"], "regenerators": [["true"]],
                                  "listings": [{"path": "gen/shared9.json", "argv": ["true"]}]}
        binding_path.write_text(json.dumps(binding, indent=2))
        commit(m, "FX-1: a shared output")
        self.push_base({"lib/pin1.txt": ["one\n"], "lib/pin2.txt": ["two\n"], "lib/held9.txt": ["held\n"],
                        "lib/untouched9.txt": ["untouched\n"], "gen/shared9.json": ["{}\n"]})
        self.flow("lead-own", "--grant", "lib/held9.txt", "--lane", "lane-1")
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
