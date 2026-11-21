"""What lead-assign and lead-accept take a lane to own: acceptance judges an insertion at a span's edge as review does."""

from __future__ import annotations

import unittest

from tests.test_region_claims import CommandCase, command_fixture, numbered


class AcceptEdgeTests(CommandCase):
    """Decision 4 at acceptance: an insertion at a span's edge is the span's unless another lane claims the far side."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx, _ = command_fixture(cls, {"FX-7": "lane-2", "FX-8": "lane-3"})

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
