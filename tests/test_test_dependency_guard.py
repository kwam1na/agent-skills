from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE_COMMAND = (
    sys.executable,
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-t",
    ".",
    "-p",
    "test_*.py",
)


class TestDependencyGuardTests(unittest.TestCase):
    def test_missing_test_extra_stops_discovery_with_one_clear_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            blocker = Path(temporary)
            (blocker / "jsonschema.py").write_text(
                "raise ModuleNotFoundError("
                '"No module named \'jsonschema\'", name="jsonschema")\n',
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(blocker), environment.get("PYTHONPATH")))
            )

            result = subprocess.run(
                SUITE_COMMAND,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("Ran 1 test", output)
        self.assertIn("install -e '.[test]'", output)
        self.assertNotIn("ModuleNotFoundError", output)
        self.assertNotIn("ERROR:", output)


if __name__ == "__main__":
    unittest.main()
