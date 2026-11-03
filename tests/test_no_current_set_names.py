"""No test depends on which release set came last, so a new release changes no test."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
LAST_SET = b"draft-building-v1"
# These read history on purpose: the frozen digest tables and the pins on the sets they cover.
READING_HISTORY = frozenset({"test_historical_qualifications.py", "test_linear_qualification.py"})
THIS_FILE = Path(__file__).name


def files_naming_the_last_set(tests: Path) -> list[str]:
    named = []
    for path in sorted(tests.rglob("*")):
        relative = path.relative_to(tests)
        if not path.is_file() or "__pycache__" in relative.parts:
            continue
        if relative.as_posix() == THIS_FILE or relative.as_posix() in READING_HISTORY:
            continue
        if LAST_SET in path.read_bytes():
            named.append(relative.as_posix())
    return named


class NoCurrentSetNamesTests(unittest.TestCase):
    def test_no_test_names_the_last_set_outside_the_history_readers(self) -> None:
        self.assertEqual(files_naming_the_last_set(TESTS), [])

    def test_the_history_readers_still_exist(self) -> None:
        for name in READING_HISTORY:
            with self.subTest(name=name):
                self.assertTrue((TESTS / name).is_file())

    def test_the_scan_finds_a_name_in_a_test_and_in_a_nested_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tests = Path(temporary)
            (tests / "fixtures" / "deep").mkdir(parents=True)
            (tests / "__pycache__").mkdir()
            (tests / "test_new.py").write_bytes(b"NAME = 'core-" + LAST_SET + b".zip'\n")
            (tests / "fixtures" / "deep" / "record.bin").write_bytes(b"\xff\x00" + LAST_SET)
            (tests / "__pycache__" / "test_new.pyc").write_bytes(LAST_SET)
            (tests / "test_clean.py").write_bytes(b"NAME = 'core-v1.2.3.zip'\n")
            for name in (THIS_FILE, *READING_HISTORY):
                (tests / name).write_bytes(LAST_SET)
            self.assertEqual(files_naming_the_last_set(tests), ["fixtures/deep/record.bin", "test_new.py"])


if __name__ == "__main__":
    unittest.main()
