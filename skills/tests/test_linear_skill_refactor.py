import pathlib
import unittest


SKILLS_ROOT = pathlib.Path("/Users/kwamina/.codex/skills")


def read(relative_path: str) -> str:
    return (SKILLS_ROOT / relative_path).read_text()


class LinearSkillRefactorTests(unittest.TestCase):
    def test_create_linear_ticket_skill_prefers_cwd_inference_before_asking(self) -> None:
        path = SKILLS_ROOT / "create-linear-ticket" / "SKILL.md"
        self.assertTrue(path.exists(), f"missing {path}")
        content = read("create-linear-ticket/SKILL.md")
        self.assertIn("name: create-linear-ticket", content)
        self.assertIn("If a Linear issue ID is present, call `get_issue` first", content)
        self.assertIn("If no issue id is present, look for explicit project/team names", content)
        self.assertIn("inspect the current working directory the skill was invoked from", content)
        self.assertIn("Build candidate project names from the current directory basename", content)
        self.assertIn("If exactly one confident project match exists, use it automatically", content)
        self.assertIn("If the issue exists but has no project, stop and ask instead of silently falling back", content)
        self.assertIn("If the named or derived project does not exist in Linear, stop and surface the mismatch clearly", content)
        self.assertIn("Apply `references/linear-project-resolution.md`", content)
        self.assertIn("If a feature materially changes what the repo does", content)
        self.assertIn("create a ticket to refresh the repo docs and agent docs", content)
        self.assertIn("keep the repo honest about its standing behavior", content)
        self.assertNotIn("Apply `../references/linear-project-resolution.md`", content)

    def test_execute_linear_ticket_skill_prefers_cwd_inference_and_has_no_retrospective_hook(self) -> None:
        path = SKILLS_ROOT / "execute-linear-ticket" / "SKILL.md"
        self.assertTrue(path.exists(), f"missing {path}")
        content = read("execute-linear-ticket/SKILL.md")
        self.assertIn("name: execute-linear-ticket", content)
        self.assertIn("inspect the current working directory the skill was invoked from", content)
        self.assertIn("If exactly one confident project match exists, use it automatically", content)
        self.assertIn("Default to completing the ticket autonomously end-to-end", content)
        self.assertIn("Only treat something as a blocker when it genuinely requires user input", content)
        self.assertIn("Delivery means the PR is merged into `main`", content)
        self.assertIn("already marked `Done` in Linear by merge automation", content)
        self.assertIn("leave the local repo tidy and back on `main`", content)
        self.assertIn("align the local `main` branch with `origin/main`", content)
        self.assertIn("Clean up the working tree and any temporary worktree or branch", content)
        self.assertIn("invoke `$designing-frontends`", content)
        self.assertIn("invoke `$frontend-skill`", content)
        self.assertIn("Prefer sub-agents when the work can be split into parallelizable chunks", content)
        self.assertIn("Apply `references/linear-project-resolution.md`", content)
        self.assertNotIn("Apply `../references/linear-project-resolution.md`", content)
        self.assertNotIn("ticket-retrospective", content)
        self.assertNotIn("athena-ticket-retrospective", content)
        self.assertNotIn("execute-athena-linear-ticket", content)
        self.assertNotIn("Mandatory handoff video", content)

    def test_ticket_retrospective_skill_is_generic_and_manual(self) -> None:
        path = SKILLS_ROOT / "ticket-retrospective" / "SKILL.md"
        self.assertTrue(path.exists(), f"missing {path}")
        content = read("ticket-retrospective/SKILL.md")
        self.assertIn("name: ticket-retrospective", content)
        self.assertIn("Run this skill manually after ticket handoff", content)
        self.assertNotIn("Athena", content)
        self.assertNotIn("execute-linear-ticket", content)

    def test_generic_agent_metadata_exists(self) -> None:
        create_agent = read("create-linear-ticket/agents/openai.yaml")
        execute_agent = read("execute-linear-ticket/agents/openai.yaml")
        retrospective_agent = read("ticket-retrospective/agents/openai.yaml")
        self.assertIn('display_name: "Create Linear Ticket"', create_agent)
        self.assertIn('display_name: "Execute Linear Ticket"', execute_agent)
        self.assertIn('display_name: "Ticket Retrospective"', retrospective_agent)

    def test_linear_project_resolution_reference_is_local_and_consistent(self) -> None:
        shared = read("references/linear-project-resolution.md")
        create_local = read("create-linear-ticket/references/linear-project-resolution.md")
        execute_local = read("execute-linear-ticket/references/linear-project-resolution.md")

        self.assertEqual(create_local, shared)
        self.assertEqual(execute_local, shared)

        notes = read("create-linear-ticket/references/linear-execution-notes.md")
        self.assertNotIn("../../references/linear-project-resolution.md", notes)


if __name__ == "__main__":
    unittest.main()
