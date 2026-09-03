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


# The vocabulary the harness and Athena dogfood runs found missing: an emitted
# round is unreadable without its bound, a cost without its unit and coverage
# does not compare, a gate timed after the fact costs another run, a skipped
# sensor has nowhere to go, and an executor reading only the emit-point list
# never learns about the entries other skills own.
REQUIRED_PHRASES: dict[str, tuple[str, ...]] = {
    "skills/obtain-review/SKILL.md": (
        "naming the round, the declared bound that round counts against, the"
        " candidate it binds, and the lenses it carries",
        "Without the bound beside the round a reader cannot tell round 1 of 4"
        " from round 1 of 1",
        "the `review.loop-bound-reached` blocker is legible only against a bound"
        " the journal states",
        "A reopened round is emitted under the number it already carries, with a"
        " member naming why it reopened",
        "Where the round belongs to a loop that declares no bound of its own, the"
        " emission names the round and states that the loop is unbounded, which is"
        " what naming no bound looks like where none may be invented",
        "The caller also supplies the round bound it declared for the delivery,"
        " the same bound `$review-work` reduces against; the bound is not part"
        " of the harness's comparison context and is never invented for an"
        " emission",
    ),
    "skills/review-work/SKILL.md": (
        "naming the round, the candidate it bound, the reduced outcome, its"
        " findings by severity, the count of late findings in that round, and"
        " the round's self-reported cost",
    ),
    "skills/execute-work/SKILL.md": (
        "This list is not the whole journal",
        "`$obtain-review` emits `lens.selected` and `review.round.opened`,"
        " `$compound-learning` emits `compounding.recorded`, and this workflow,"
        " as `$review-work`'s caller, emits `review.round.closed` once that"
        " reducer has closed a round",
        "A delivery that obtains review or compounds and emits none of those has"
        " an incomplete journal even though every point above was honoured",
        "The two `$obtain-review` and `$compound-learning` entries are owned by"
        " the skill named beside them, and this workflow neither restates their"
        " rules nor emits them in that skill's place",
        "Every cost this delivery reports names its unit and that unit's"
        " coverage",
        "Spell the unit in kebab-case and identically for every run of the same"
        " repository, so two runs compare rather than silently failing to",
        "where the host meters the tokens of dispatched subagents, that unit is"
        " `subagent-tokens`",
        "Where the host exposes no accounting for the executor's own session,"
        " report the part of the cost the executor can see and say that it is a"
        " part, so the total is never read as the whole",
        "Time a repository gate while it runs and retain that wall time beside"
        " its outcome, because `gate.reported` needs how long the gate ran and a"
        " finished run's duration cannot be recovered afterward except by"
        " running the gate again",
        "A sensor considered and deliberately not run is a judgement call rather"
        " than an absence: record it with `decision.recorded` naming the sensor"
        " and why it does not apply",
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
                    for relative in {*CAPABILITY_PHRASES, *REQUIRED_PHRASES}
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

    def test_each_skill_states_the_emit_point_vocabulary(self) -> None:
        for relative, phrases in REQUIRED_PHRASES.items():
            for phrase in phrases:
                with self.subTest(path=relative, phrase=phrase):
                    self.assertIn(phrase, self.archived[relative])


if __name__ == "__main__":
    unittest.main()
