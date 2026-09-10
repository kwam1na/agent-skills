from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

try:
    importlib.import_module("jsonschema")
except ModuleNotFoundError as error:
    if error.name != "jsonschema":
        raise
    TEST_EXTRA_MISSING = True
else:
    TEST_EXTRA_MISSING = False


class TestExtraDependency(unittest.TestCase):
    def test_test_extra_is_installed(self) -> None:
        # This is a failure sentinel. Successful discovery replaces it with
        # the full suite, so dispatching only this test can never report green.
        self.fail(
            "The full test suite was not discovered; run "
            "python3 -m pip install -e '.[test]' to install its test dependencies."
        )


def load_tests(
    loader: unittest.TestLoader,
    standard_tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    if TEST_EXTRA_MISSING:
        return standard_tests
    # Preserve the existing discovery command's top-level helper imports while
    # the package-aware command imports each test as `tests.test_*`.
    tests_path = str(TESTS)
    if tests_path not in sys.path:
        sys.path.insert(0, tests_path)
    return loader.discover(
        start_dir=str(TESTS),
        pattern=pattern or "test*.py",
        top_level_dir=str(ROOT),
    )
