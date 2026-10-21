from __future__ import annotations

import re
import unittest
from pathlib import Path

from agent_skills.lifecycle import Lifecycle

from tests.lifecycle_support import LifecycleFixture


ROOT = Path(__file__).resolve().parents[1]


class DocumentedInstallSurfaceTests(LifecycleFixture, unittest.TestCase):
    def test_consumer_install_declaration_matches_real_status(self) -> None:
        guidance = " ".join(
            (ROOT / "docs/host-adapters.md").read_text(encoding="utf-8").split()
        )
        declaration = re.search(
            r"Its sensors are the lifecycle status sensor — `([^`:]+): ([^`]+)`,"
            r" empty `([^`]+)`",
            guidance,
        )
        self.assertIsNotNone(declaration, "consumer-install status declaration is missing")
        assert declaration is not None
        lifecycle_key, expected_state, blockers_key = declaration.groups()
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        status = lifecycle.status().as_dict()
        self.assertEqual(status[lifecycle_key], expected_state)
        self.assertEqual(status[blockers_key], [])



class OutsideAdopterDocumentationTests(unittest.TestCase):
    def test_workflow_only_commands_and_prerequisites_are_explicit(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Install core skills without a harness", readme)
        section = readme.split("## Install core skills without a harness", 1)[1].split("\n## ", 1)[0]
        normalized = " ".join(section.split())
        for phrase in ("Python 3.11 or newer", "Git", "No Node", "CLAUDE.md", "local-only"):
            self.assertIn(phrase, normalized)
