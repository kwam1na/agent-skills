from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


TESTS = Path(__file__).resolve().parent

try:
    importlib.import_module("jsonschema")
except ModuleNotFoundError as error:
    if error.name != "jsonschema":
        raise
    TEST_EXTRA_MISSING = True
else:
    TEST_EXTRA_MISSING = False


# Preserve the existing discovery command's top-level helper imports while
# the package-aware command imports each test as `tests.test_*`.
tests_path = str(TESTS)
if tests_path not in sys.path:
    sys.path.insert(0, tests_path)


if TEST_EXTRA_MISSING:
    class TestExtraDependency(unittest.TestCase):
        def test_test_extra_is_installed(self) -> None:
            self.fail(
                "The repository test extra is missing; run "
                "python3 -m pip install -e '.[test]' before running the suite."
            )

    # Intercept only the missing-dependency path. With dependencies installed,
    # unittest discovers the suite normally; there is no success-path loader.
    def load_tests(
        loader: unittest.TestLoader,
        standard_tests: unittest.TestSuite,
        pattern: str | None,
    ) -> unittest.TestSuite:
        return standard_tests
