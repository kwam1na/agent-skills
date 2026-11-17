"""The full suite's time budget, and the units allowed to run alone.

The budget in ``tests/suite-budget.json`` is what ``scripts/run-tests.py
--budget`` checks a whole run against. These rows keep it from drifting: a
limit may come down in the file alone, a unit that runs alone has to be listed
there with its reason, and the deliveries' own full-suite command runs at the
worker count the budget is measured with.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET = ROOT / "tests" / "suite-budget.json"
RUNNER = ROOT / "scripts" / "run-tests.py"
# The most each limit may be, in seconds. Raising one is the operator's ruling, made here and in the budget in one
# change; a limit comes down in the budget alone.
CEILINGS = {"wallSeconds": 1740, "serialSeconds": 90, "unitSeconds": 450}
WORKERS = 4


class SuiteBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.budget = json.loads(BUDGET.read_text(encoding="utf-8"))

    def test_no_limit_is_above_its_ceiling_and_the_measure_is_named(self) -> None:
        self.assertEqual(self.budget["schemaVersion"], "suite-budget/1")
        self.assertEqual(self.budget["workers"], WORKERS)
        self.assertRegex(self.budget["reference"], r"\S")
        for limit, ceiling in CEILINGS.items():
            with self.subTest(limit):
                value = self.budget[limit]
                self.assertNotIsInstance(value, bool)
                self.assertIsInstance(value, (int, float))
                self.assertGreater(value, 0)
                self.assertLessEqual(value, ceiling, f"{limit} was raised past its ceiling")

    def test_every_unit_that_runs_alone_is_listed_with_its_reason_and_nothing_else_is(self) -> None:
        listed = subprocess.run([sys.executable, "-B", str(RUNNER), "--list"], cwd=ROOT, capture_output=True, text=True,
                                timeout=300, check=True).stdout
        alone = sorted({re.sub(r"\[\d+/\d+\]$", "", line.split()[0]) for line in listed.splitlines()
                        if "(serial)" in line})
        self.assertTrue(alone, listed[-2000:])
        self.assertEqual(alone, sorted(self.budget["serial"]),
                         "a unit runs alone without its reason in tests/suite-budget.json, or one listed there no longer "
                         "does: a class that keeps to its own folders, ports and processes is run_quietly")
        for unit, reason in self.budget["serial"].items():
            with self.subTest(unit):
                self.assertIsInstance(reason, str)
                self.assertRegex(reason, r"\S+\s+\S+", "a reason says what the unit cannot share")

    def test_the_deliveries_full_suite_runs_at_the_budgets_worker_count(self) -> None:
        binding = json.loads((ROOT / ".agents" / "orchestration.json").read_text(encoding="utf-8"))
        commands = {"fullHealth": binding["fullHealth"], "fallback": binding["fallback"][0]["argv"]}
        for name, argv in commands.items():
            with self.subTest(name):
                self.assertIn("scripts/run-tests.py", argv)
                self.assertEqual(argv[argv.index("--workers") + 1], str(self.budget["workers"]))

    def test_the_run_before_a_tag_is_checked_against_the_budget(self) -> None:
        command = f".venv/bin/python scripts/run-tests.py --workers {WORKERS} --budget tests/suite-budget.json"
        for name in ("AGENTS.md", "README.md"):
            with self.subTest(name):
                text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
                self.assertIn(command, text)
                # Going over is reported with the tag and holds nothing; only a failing run does.
                self.assertRegex(text, r"does not hold the release: the tag's annotation says the run went over")
                self.assertNotRegex(text, r"(?i)release waits|back inside")
        self.assertIn("A change that adds tests keeps the suite inside the budget",
                      " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split()))


if __name__ == "__main__":
    unittest.main()
