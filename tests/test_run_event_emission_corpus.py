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

# Ownership above is single, so naming a kind is also denied where the skill
# does not own it: an executor reading any shipped skill learns each kind's one
# emit point rather than a second, competing one. `execute-work` is the sole
# skill that names kinds it does not own, and it names them only to point at
# their owners, so its references are enumerated here instead of inferred.
REFERENCED_KINDS: dict[str, tuple[str, ...]] = {
    "skills/execute-work/SKILL.md": (
        "`lens.selected`",
        "`review.round.opened`",
        "`review.round.closed`",
        "`compounding.recorded`",
    ),
}

OWNED_KINDS: frozenset[str] = frozenset(
    kind for kinds in EMITTED_KINDS.values() for kind in kinds
)

# Kinds no skill owns because nothing in the corpus emits them. The run-event
# command writes this one, so a shipped skill naming it sends an executor to an
# emit point no skill has.
UNOWNED_KINDS: tuple[str, ...] = ("`command.completed`",)

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
# does not compare, a `run.ended` cost that is not the handoff's own cost makes
# the journal disagree with the report, a gate timed after the fact costs
# another run, a skipped sensor has nowhere to go, and an executor reading only
# the emit-point list never learns about the entries other skills own. Each
# entry pins the clause carrying the rule an executor acts on rather than the
# prose justifying it, so rewording a justification does not redden the corpus
# and a reworded rule does.
REQUIRED_PHRASES: dict[str, tuple[str, ...]] = {
    "skills/obtain-review/SKILL.md": (
        "naming the round, the declared bound that round counts against, the"
        " candidate it binds, and the lenses it carries",
        "A reopened round is emitted under the number it already carries, with a"
        " member naming why it reopened",
        "Where the round belongs to a loop that declares no bound of its own, the"
        " emission names the round and states that the loop is unbounded",
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
        "`$obtain-review` emits `lens.selected` and `review.round.opened`,"
        " `$compound-learning` emits `compounding.recorded`, and this workflow,"
        " as `$review-work`'s caller, emits `review.round.closed` once that"
        " reducer has closed a round",
        "The two `$obtain-review` and `$compound-learning` entries are owned by"
        " the skill named beside them, and this workflow neither restates their"
        " rules nor emits them in that skill's place",
        # One pair: the `run.ended` cost and the handoff's cost are the same
        # cost, so both halves are pinned together and removing either one
        # leaves the other reading as a cost of its own.
        "`run.ended` last, carrying the delivery's result category and its"
        " self-reported cost, the same cost the handoff reports",
        "compounding outcome, the delivery's self-reported cost, unresolved"
        " blockers",
        "Every cost this delivery reports names its unit and that unit's"
        " coverage",
        "Spell the unit in kebab-case and identically for every run of the same"
        " repository",
        "where the host meters the tokens of dispatched subagents, that unit is"
        " `subagent-tokens`",
        "Where the host exposes no accounting for the executor's own session,"
        " report the part of the cost the executor can see and say that it is a"
        " part",
        "Time a repository gate while it runs and retain that wall time beside"
        " its outcome, because `gate.reported` needs how long the gate ran",
        "A sensor considered and deliberately not run is a judgement call rather"
        " than an absence: record it with `decision.recorded` naming the sensor"
        " and why it does not apply",
        "Reading the root instruction file for the run-event declaration is"
        " capability discovery, not the authority reading this point orders: it"
        " precedes `run.started`",
    ),
}


# The paragraph that qualifies the emit-point list reads off its own position:
# `This list` and `every point above` name the list it follows, so a copy of it
# anywhere else in the file says nothing. Phrase membership is position-blind,
# so the paragraph's opening is pinned to the last bullet of the list rather
# than to the file.
EMIT_POINT_LIST_CLOSE: tuple[str, str] = (
    "skills/execute-work/SKILL.md",
    "the same cost the handoff reports."
    " This list is not the whole journal.",
)


class RunEventEmissionCorpusTests(unittest.TestCase):
    """Pin the emit points in the skill text a release actually ships."""

    archived: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        archived: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            # Every profile, because a skill only one of them ships names its
            # emit points for that profile's adopters and nobody else's.
            for profile_id in ("core", "linear"):
                archive = directory / f"{profile_id}.zip"
                build_release(
                    ROOT,
                    archive,
                    directory / f"{profile_id}.release.json",
                    release_id=f"{profile_id}-v1",
                    profile_id=profile_id,
                )
                with zipfile.ZipFile(archive) as release:
                    for relative in release.namelist():
                        if not relative.startswith("skills/"):
                            continue
                        if not relative.endswith(".md"):
                            continue
                        if relative in archived:
                            continue
                        archived[relative] = " ".join(
                            release.read(relative).decode("utf-8").split()
                        )
        cls.archived = archived

    def test_each_skill_names_the_kinds_it_emits(self) -> None:
        for relative, kinds in EMITTED_KINDS.items():
            for kind in kinds:
                with self.subTest(path=relative, kind=kind):
                    self.assertIn(kind, self.archived[relative])

    def test_each_kind_has_exactly_one_owner(self) -> None:
        owner: dict[str, str] = {}
        for relative, kinds in EMITTED_KINDS.items():
            for kind in kinds:
                with self.subTest(kind=kind):
                    self.assertNotIn(kind, owner)
                owner[kind] = relative

    def test_no_skill_names_a_kind_it_does_not_own(self) -> None:
        for relative, text in sorted(self.archived.items()):
            allowed = set(EMITTED_KINDS.get(relative, ())) | set(
                REFERENCED_KINDS.get(relative, ())
            )
            denied = sorted(OWNED_KINDS - allowed) + list(UNOWNED_KINDS)
            for kind in denied:
                with self.subTest(path=relative, kind=kind):
                    self.assertNotIn(kind, text)

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

    def test_the_qualifying_paragraph_follows_the_emit_point_list(self) -> None:
        relative, phrase = EMIT_POINT_LIST_CLOSE
        self.assertIn(phrase, self.archived[relative])


if __name__ == "__main__":
    unittest.main()
