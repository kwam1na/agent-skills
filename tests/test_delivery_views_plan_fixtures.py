"""The plan chapter's fixtures: valid plans, records the state tool really wrote, and no first adopter's terms."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from agent_skills.validate import LEAKAGE_PATTERNS
from tests.delivery_views_fixtures import views_common
from tests.test_run_delivery import FIRST_ADOPTER_TERM_DIGESTS


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "delivery-views" / "line-map"
PLAN_CASES = ("plan-in-app", "plan-record-only", "plan-review")
RECORDS = CORPUS / "plan-records"
HEX = re.compile(r"\b[0-9a-f]{40}\b|\b[0-9a-f]{64}\b")
TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
SECONDS = re.compile(r'"(\w*[Ss]econds|durationSeconds)": \d+')


def shape(text: str) -> str:
    """A captured document with its commit ids, digests and times replaced by their order of first appearance."""
    seen: dict[str, str] = {}

    def token(match: re.Match) -> str:
        return seen.setdefault(match.group(0), f"<{len(seen)}>")

    return SECONDS.sub(lambda match: f'"{match.group(1)}": 0', TIME.sub("<time>", HEX.sub(token, text)))


def load_capture():
    spec = importlib.util.spec_from_file_location("plan_records_capture", RECORDS / "capture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlanFixtureTests(unittest.TestCase):
    def test_each_golden_delivery_plan_passes_the_plan_validator(self) -> None:
        for case in PLAN_CASES:
            with self.subTest(case=case):
                plan = json.loads((CORPUS / case / "plan.json").read_text(encoding="utf-8"))
                self.assertIs(views_common.validate_plan(plan), plan)

    def test_the_plan_fixtures_carry_no_first_adopter_term(self) -> None:
        paths = [path for case in PLAN_CASES for path in sorted((CORPUS / case).iterdir())] + sorted(RECORDS.iterdir())
        for path in paths:
            contents = path.read_text(encoding="utf-8")
            for class_id, pattern in LEAKAGE_PATTERNS:
                with self.subTest(path=path.relative_to(CORPUS).as_posix(), literal=class_id):
                    self.assertIsNone(pattern.search(contents))
            # The first adopter's own terms, as the run-delivery scan finds them: by word, against their digests.
            with self.subTest(path=path.relative_to(CORPUS).as_posix(), literal="first-adopter term"):
                words = set(re.findall(r"[a-z0-9]+", contents.lower()))
                self.assertEqual([word for word in words if hashlib.sha256(word.encode()).hexdigest() in FIRST_ADOPTER_TERM_DIGESTS], [])

    def test_the_captured_records_are_what_the_state_tool_and_runner_write_today(self) -> None:
        # The records are `show` output from a real runner sync, never written by hand: a fresh capture has the same
        # shape, entry for entry, with only commit ids, digests, times and durations differing.
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            load_capture().capture(out)
            for name in ("handed", "synced", "merged"):
                with self.subTest(record=name):
                    fresh = (out / f"{name}.json").read_text(encoding="utf-8")
                    committed = (RECORDS / f"{name}.json").read_text(encoding="utf-8")
                    self.assertEqual(shape(committed), shape(fresh))

    def test_the_synced_records_carry_a_runner_synced_head_and_keep_the_reviewed_heads_observation(self) -> None:
        # The runner moves syncedHead; the captured review stays an available observation of the head it reviewed.
        for name in ("handed", "synced", "merged"):
            document = json.loads((RECORDS / f"{name}.json").read_text(encoding="utf-8"))
            with self.subTest(record=name):
                native = next(item for item in document["state"]["queue"] if item["subject"] == "native-sync")
                observation = document["reviewEvidence"]["native-sync"]
                self.assertNotIn("unavailable", observation)
                self.assertEqual(observation["candidateRef"], native["head"])
                for item in document["state"]["queue"]:
                    if name == "handed":
                        self.assertIsNone(item["syncedHead"])
                    else:
                        self.assertNotIn(item["syncedHead"], (None, item["head"]))


if __name__ == "__main__":
    unittest.main()
