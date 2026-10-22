from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from agent_skills.validate import LEAKAGE_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
CORE_RUNTIME_PATHS = (
    "agent_skills/capabilities.py",
    "agent_skills/fake_tracker.py",
    "agent_skills/router.py",
    "agent_skills/workflows.py",
    "agent_skills/workflow_graph.py",
    "agent_skills/diagnosis.py",
    "agent_skills/review_orchestration.py",
)
CORE_SKILL_PATHS = (
    "skills/compound-delivery-kernel/SKILL.md",
    "skills/compound-learning/SKILL.md",
    "skills/deliver-work/SKILL.md",
    "skills/deliver-work/references/capability-contract.md",
    "skills/diagnose-work/SKILL.md",
    "skills/execute-work/SKILL.md",
    "skills/execute-work/references/run-observation-contract.md",
    "skills/execute-work/scripts/report_observation.py",
    "skills/obtain-review/SKILL.md",
    "skills/obtain-review/references/round-brief-template.md",
    "skills/orchestrate-work/SKILL.md",
    "skills/orchestrate-work/references/orchestration-guide.md",
    "skills/orchestrate-work/references/schemas.json",
    "skills/orchestrate-work/scripts/delivery_state.py",
    "skills/orchestrate-work/scripts/merge_queue.py",
    "skills/orchestrate-work/scripts/render_state.py",
    "skills/plan-work/SKILL.md",
    "skills/review-work/SKILL.md",
)
# The same rule the corpus validator applies, so the two never drift apart.
FORBIDDEN = dict(LEAKAGE_PATTERNS)["provider-boundary"]


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
        allowed_roots = {"__future__", "copy", "dataclasses", "enum", "hashlib", "json", "pathlib", "re", "typing", "unicodedata"}
        for relative in CORE_RUNTIME_PATHS:
            with self.subTest(path=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.level:
                        self.assertEqual(node.level, 1)
                        self.assertIn(node.module, {"workflow_graph", "workflows"})
                imported = {
                    node.module.split(".")[0]
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module and not node.level
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
            "agent_skills/workflow_graph.py",
            "agent_skills/diagnosis.py",
            "agent_skills/review_orchestration.py",
            "skills/compound-learning/SKILL.md",
            "skills/diagnose-work/SKILL.md",
            "skills/execute-work/SKILL.md",
            "skills/obtain-review/SKILL.md",
            "skills/orchestrate-work/SKILL.md",
            "skills/plan-work/SKILL.md",
            "skills/review-work/SKILL.md",
            "tests/scenarios/core/compound.json",
            "tests/scenarios/core/execute.json",
            "tests/scenarios/core/diagnose.json",
            "tests/scenarios/core/obtain-review.json",
            "tests/scenarios/core/plan.json",
            "tests/scenarios/core/review.json",
        ):
            with self.subTest(path=relative):
                self.assertIsNone(
                    forbidden.search((ROOT / relative).read_text(encoding="utf-8"))
                )


    def test_orchestration_text_names_no_host_forge_tracker_or_plan_unit(self) -> None:
        # Names in any case or spelling; identifiers exactly as they are written.
        names = re.compile(
            r"\b(?:Athena|Linear|Jira|GitHub|GitLab|Gitea|Bitbucket|Claude|Codex|Cursor|"
            r"Slack|MCP|gh|pull requests?|merge requests?|PRs?)\b",
            re.IGNORECASE,
        )
        identifiers = re.compile(r"\b(?:V26-\d+|U\d+|A\d+|C\d+)\b")
        for relative in (
            "skills/orchestrate-work/SKILL.md",
            "skills/orchestrate-work/references/orchestration-guide.md",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIsNone(names.search(text))
                self.assertIsNone(identifiers.search(text))

class ProviderBoundaryRuleTests(unittest.TestCase):
    """The provider rule allows exactly three forms of http, and nothing else."""

    def test_the_three_allowed_forms_pass(self) -> None:
        for text in ("import http.server", "class Server(http.server.ThreadingHTTPServer):",
                     'protocol_version = "HTTP/1.1"', "open http://127.0.0.1:8080/ in a browser",
                     "http://localhost", "http://localhost:3000/events?x=1", "http://[::1]:9/"):
            with self.subTest(text=text):
                self.assertIsNone(FORBIDDEN.search(text))

    def test_everything_else_still_matches(self) -> None:
        for text in ("http://example.com", "http://localhost.example.com/", "http://127.0.0.1.example/",
                     "http://10.0.0.1:80/", "an http request", "http/2", "HTTP/1.0",
                     "https://localhost", "https://127.0.0.1:8080/", "https://example.com",
                     "http://localhost@evil.com/", "http://127.0.0.1@example.com", "http://localhost:80@evil.com",
                     "http://[::1]@evil.com", "http://localhost#@evil.com",
                     "import http.client", "from http import cookies", "http.cookies",
                     "linear", "mcp", "sdk", "oauth", "credentials", "credential"):
            with self.subTest(text=text):
                self.assertIsNotNone(FORBIDDEN.search(text))


if __name__ == "__main__":
    unittest.main()
