from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_RUNTIME_PATHS = (
    "agent_skills/capabilities.py",
    "agent_skills/fake_tracker.py",
    "agent_skills/router.py",
    "agent_skills/workflows.py",
)
CORE_SKILL_PATHS = (
    "skills/compound-delivery-kernel/SKILL.md",
    "skills/compound-learning/SKILL.md",
    "skills/deliver-work/SKILL.md",
    "skills/deliver-work/references/capability-contract.md",
    "skills/execute-work/SKILL.md",
    "skills/plan-work/SKILL.md",
    "skills/review-work/SKILL.md",
)
FORBIDDEN = re.compile(
    r"\b(?:linear|mcp|https?|sdk|oauth|credentials?)\b", re.IGNORECASE
)


class CoreBoundaryTests(unittest.TestCase):
    def test_execution_handoff_keeps_every_kernel_phase_observable(self) -> None:
        text = (ROOT / "skills/execute-work/SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())

        for required in (
            "selected execution posture",
            "review outcome",
            "who or what produced it",
            "compounding outcome",
            "leaves the delivery `partial`",
            "incidental sensor mechanics",
            "without weakening that behavior",
            "accidental formatting or extraction constraint",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized)

    def test_core_has_no_provider_or_transport_vocabulary(self) -> None:
        for relative in (*CORE_RUNTIME_PATHS, *CORE_SKILL_PATHS):
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIsNone(FORBIDDEN.search(text))

    def test_core_python_imports_only_standard_library_and_core_modules(self) -> None:
        allowed_roots = {"__future__", "dataclasses", "enum", "re", "typing", "unicodedata"}
        for relative in CORE_RUNTIME_PATHS:
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

    def test_portable_workflows_have_no_repository_provider_dependencies(self) -> None:
        forbidden = re.compile(
            r"\b(?:Athena|Compound Engineering|delivery harness|Linear|MCP)\b",
            re.IGNORECASE,
        )
        for relative in (
            "agent_skills/workflows.py",
            "skills/compound-learning/SKILL.md",
            "skills/execute-work/SKILL.md",
            "skills/plan-work/SKILL.md",
            "skills/review-work/SKILL.md",
            "tests/scenarios/core/compound.json",
            "tests/scenarios/core/execute.json",
            "tests/scenarios/core/plan.json",
            "tests/scenarios/core/review.json",
        ):
            with self.subTest(path=relative):
                self.assertIsNone(
                    forbidden.search((ROOT / relative).read_text(encoding="utf-8"))
                )


if __name__ == "__main__":
    unittest.main()
