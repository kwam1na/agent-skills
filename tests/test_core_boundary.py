from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_PATHS = (
    "agent_skills/capabilities.py",
    "agent_skills/fake_tracker.py",
    "agent_skills/router.py",
    "skills/compound-delivery-kernel/SKILL.md",
    "skills/deliver-work/SKILL.md",
    "skills/deliver-work/references/capability-contract.md",
)
FORBIDDEN = re.compile(
    r"\b(?:linear|mcp|https?|sdk|oauth|credentials?)\b", re.IGNORECASE
)


class CoreBoundaryTests(unittest.TestCase):
    def test_core_has_no_provider_or_transport_vocabulary(self) -> None:
        for relative in CORE_PATHS:
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIsNone(FORBIDDEN.search(text))

    def test_core_python_imports_only_standard_library_and_core_modules(self) -> None:
        allowed_roots = {"__future__", "dataclasses", "enum", "re", "typing", "unicodedata"}
        for relative in CORE_PATHS[:3]:
            with self.subTest(path=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
                imported = {
                    node.module.split(".")[0]
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module
                }
                imported.update(
                    alias.name.split(".")[0]
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                )
                self.assertLessEqual(imported, allowed_roots | {"agent_skills"})


if __name__ == "__main__":
    unittest.main()
