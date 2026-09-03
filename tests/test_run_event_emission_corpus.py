from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_skills.release import build_release


ROOT = Path(__file__).resolve().parents[1]

# The event kinds each shipped workflow is responsible for emitting. A skill
# that names the capability without naming its own kinds tells an executor
# nothing about where to emit.
EMITTED_KINDS: dict[str, tuple[str, ...]] = {
    "skills/execute-work/SKILL.md": (
        "`run.started`",
        "`ticket.read`",
        "`posture.declared`",
        "`decision.recorded`",
        "`gate.reported`",
        "`pr.opened`",
        "`blocker.recorded`",
        "`run.ended`",
    ),
    "skills/obtain-review/SKILL.md": (
        "`lens.selected`",
        "`review.round.opened`",
    ),
    "skills/review-work/SKILL.md": ("`review.round.closed`",),
    "skills/compound-learning/SKILL.md": ("`compounding.recorded`",),
}

# The capability is optional and repository-declared, and an absent one is
# silent rather than a handoff or a blocker.
CAPABILITY_PHRASES: dict[str, tuple[str, ...]] = {
    **{
        relative: (
            "the run-event command the repository's root instruction file"
            " declares, when it declares one",
            "with no handoff and no blocker",
        )
        for relative in EMITTED_KINDS
    },
    "skills/deliver-work/SKILL.md": (
        "declared by the repository's root instruction file naming a run-event"
        " command",
        "every workflow that would emit proceeds silently, with no handoff and"
        " no blocker",
        "The run identifier is host mechanics: it is allocated when the run"
        " starts and resolved from the run store for the invoking worktree,"
        " never read from work-item content and never invented for an emission",
    ),
}


class RunEventEmissionCorpusTests(unittest.TestCase):
    """Pin the emit points in the skill text a release actually ships."""

    archived: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "core.zip"
            build_release(
                ROOT,
                archive,
                directory / "core.release.json",
                release_id="core-v1",
                profile_id="core",
            )
            with zipfile.ZipFile(archive) as release:
                cls.archived = {
                    relative: " ".join(
                        release.read(relative).decode("utf-8").split()
                    )
                    for relative in CAPABILITY_PHRASES
                }

    def test_each_skill_names_the_kinds_it_emits(self) -> None:
        for relative, kinds in EMITTED_KINDS.items():
            for kind in kinds:
                with self.subTest(path=relative, kind=kind):
                    self.assertIn(kind, self.archived[relative])

    def test_each_skill_states_the_optional_capability_rule(self) -> None:
        for relative, phrases in CAPABILITY_PHRASES.items():
            for phrase in phrases:
                with self.subTest(path=relative, phrase=phrase):
                    self.assertIn(phrase, self.archived[relative])


if __name__ == "__main__":
    unittest.main()
