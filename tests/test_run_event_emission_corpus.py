from __future__ import annotations

import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_skills.release import SUPPORTED_PROFILES, build_release


ROOT = Path(__file__).resolve().parents[1]

# The event kinds each shipped workflow is responsible for emitting. This
# explicit vocabulary is the corpus boundary: other dotted tokens are not
# guessed to be event kinds. A skill that names the capability without naming
# its own kinds tells an executor nothing about where to emit.
EMITTED_KINDS: dict[str, tuple[str, ...]] = {
    # The run's lifecycle belongs to the kernel, which every delivery workflow
    # applies, so one rule decides who starts, joins and ends a run.
    "skills/compound-delivery-kernel/SKILL.md": (
        "`run.started`",
        "`decision.recorded`",
        "`blocker.recorded`",
        "`run.ended`",
    ),
    "skills/execute-work/SKILL.md": (
        "`ticket.read`",
        "`posture.declared`",
        "`gate.reported`",
        "`pr.opened`",
        "`review.round.closed`",
    ),
    "skills/obtain-review/SKILL.md": (
        "`lens.selected`",
        "`review.round.opened`",
    ),
    "skills/compound-learning/SKILL.md": ("`compounding.recorded`",),
}

# Ownership above stays single because a duplicate silently widens the kinds
# the deny permits for both owners. Naming a kind is denied where the skill does
# not own or explicitly reference it. Reference permission is deliberately
# name-level: whether allowed prose makes a competing emit claim remains review
# work rather than a semantic-text sensor.
REFERENCED_KINDS: dict[str, tuple[str, ...]] = {
    "skills/execute-work/references/run-observation-contract.md": (
        "`review.round.opened`",
        "`review.round.closed`",
    ),
    "skills/review-work/SKILL.md": ("`review.round.closed`",),
    # The delivery journal's own kinds share these three names. The guide's
    # tracker-updates table, lead-authority rules and close-out name them as
    # journal entries the state tool writes, never as run events to emit.
    "skills/orchestrate-work/references/orchestration-guide.md": (
        "`decision.recorded`",
        "`blocker.recorded`",
        "`compounding.recorded`",
    ),
    # The lead-compound row names the journal entry it writes for the
    # delivery's compound-learning decision, never a run event to emit.
    "skills/run-delivery/SKILL.md": ("`compounding.recorded`",),
    # The skill's carry-review section names the journal entry the state tool
    # writes for a carry, never a run event to emit.
    "skills/orchestrate-work/SKILL.md": ("`decision.recorded`",),
    # The tracker publisher's configuration names the journal's decision kind, which it posts when marked; the
    # supervisor's names the blocker kind, which lead-resolve resolves.
    "skills/run-delivery/references/config.md": ("`decision.recorded`", "`blocker.recorded`"),
    # The workflow's own forks and blockers are emitted under the kernel's
    # rule, which owns both kinds; the list names them where they arise.
    "skills/execute-work/SKILL.md": (
        "`lens.selected`",
        "`review.round.opened`",
        "`compounding.recorded`",
        "`decision.recorded`",
        "`blocker.recorded`",
    ),
}

OWNED_KINDS: frozenset[str] = frozenset(
    kind for kinds in EMITTED_KINDS.values() for kind in kinds
)

# Kinds no skill owns because nothing in the corpus emits them. The run-event
# command writes this one, so a shipped skill naming it sends an executor to an
# emit point no skill has.
UNOWNED_KINDS: tuple[str, ...] = ("`command.completed`",)
KNOWN_EVENT_KINDS: frozenset[str] = OWNED_KINDS | frozenset(UNOWNED_KINDS)

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
    **{
        relative: (
            "the run-event command the repository's root instruction file"
            " declares, when it declares one",
            "with no handoff and no blocker",
        )
        for relative in ("skills/plan-work/SKILL.md", "skills/diagnose-work/SKILL.md")
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
# and a reworded rule does. These whole-file phrase pins guard deletion and
# rewording, not arbitrary contradiction or displacement. Add a local
# structural pin only where the phrase reads off its position.
REQUIRED_PHRASES: dict[str, tuple[str, ...]] = {
    "skills/obtain-review/SKILL.md": (
        "naming the round, the declared bound that round counts against, the"
        " candidate it binds, and the lenses it carries",
        "A reopened round is emitted under the number it already carries, with a"
        " member naming why it reopened",
        "Where the round belongs to a loop that declares no bound of its own, the"
        " emission names the round and states that the loop is unbounded",
        # Anchored to the tail of the emission rule it qualifies: "that same
        # declared bound" reads off its position, so the same sentence pasted
        # anywhere else in the file names no bound at all. This pins the
        # actionable emission rule independently of review-work's exception;
        # cross-file semantic agreement and explanatory rationale stay review
        # responsibilities.
        "looks like where none may be invented. A grace verification round"
        " obtained at the bound names that same declared bound and states that"
        " it is the grace round obtained outside the count",
        "The caller also supplies the delivery-review round bound it declared,"
        " the same bound `$review-work` reduces against. A plan-review loop"
        " declares no bound, so none is supplied or invented for it. Bound"
        " accounting is not part of the harness's comparison context",
    ),
    "skills/review-work/SKILL.md": (
        "naming the round, the candidate it bound, the reduced outcome, its"
        " findings by severity, and the round's self-reported cost",
        "retains late-finding counts in captured reduction output when the"
        " runtime's closed event schema has no such member",
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
        # The enumeration is the one place a reader counts the forks that emit,
        # so it is pinned whole: the skipped sensor the finish-line paragraph
        # sends to `decision.recorded` is a member of it, and a member dropped
        # from either passage leaves the two disagreeing.
        "Named forks: the branch name, whether to obtain a review round under"
        " unchanged deliverable identity, the checkout allocated to each lens,"
        " a skill read rather than invoked, a sensor considered and deliberately"
        " not run, and any value supplied by hand that should have been"
        " discoverable",
        # The reference that replaces the unconditional start and end. Each
        # clause is the part of the kernel's rule this workflow acts on.
        "This workflow follows `$compound-delivery-kernel`'s run lifecycle"
        " rule. Under the kernel's rule, invoked by the operator it starts the run, and"
        " invoked by another workflow in the same session it joins that"
        " workflow's run and starts none",
        "Under the kernel's rule, an epic lane's `$execute-work` starts its own"
        " run and binds it to its piece with the delivery's `bind-run`, and does"
        " not start the live view, which the lead runs for the delivery",
        "It ends the run only where it started it, as the kernel's rule says",
        "It marks its stage `execute` at entry and exit, and `review` around"
        " each review round it obtains, nested inside `execute`",
    ),
    "skills/compound-delivery-kernel/SKILL.md": (
        # Decision 4: the capability stays optional and silent.
        "What is emitted is observability, not evidence. No gate, approval, or"
        " finish-line decision reads it, and a refused or failed emission never"
        " changes a command's outcome or the delivery's result",
        "Reading the root instruction file for that declaration is capability"
        " discovery: it comes before anything is emitted, and everything else"
        " that file governs is read after the run starts",
        # The 0.8.6 runtime's version limit, characterized by
        # tests/test_run_event_acceptance.py.
        "Start the run with `--version 2`, because without it the runtime"
        " starts a version 1 run, and give every emission on a version 2 run"
        " its own `--event-id`, the same one for a retry of the same emission",
        # Decision 2's rule, clause by clause.
        "A workflow the operator invoked starts the run with `run.started`,"
        " naming the host and the installed workflow release it runs under, and"
        " the work item where there is one. So does an epic lane's"
        " `$execute-work`, which binds its run to its piece with the delivery's"
        " `bind-run`",
        "A workflow another workflow invoked in the same session joins the"
        " current run and starts none. `$deliver-work` starts its run at"
        " `route`, and the workflows it routes to join it",
        "The run stays open across every approval or decision the request waits"
        " on, each marked by `wait.started` and `wait.resolved` with"
        " `waitingOn: human` where the operator is needed, so a request routed"
        " through `plan` and `execute` is one run",
        "Only the workflow that started a run ends it, with `run.ended`, when its"
        " request finishes (`complete`), stops on a blocker (`blocked`), or is"
        " stopped by the operator with work left (`partial`)",
        "`run.ended` carries the result category and the delivery's"
        " self-reported cost, the same cost the handoff reports, and nothing is"
        " emitted to the run after it",
        "A request abandoned without an end leaves its run open. A start that"
        " the runtime refuses because the worktree has an open current run is"
        " made again with `--force`, which records the open run as"
        " `displacedRunId`. A start the runtime still refuses is silent, and the"
        " workflow goes on without a run",
        "`decision.recorded` at every fork resolved by judgement rather than by"
        " a discovered rule, naming the fork and the choice",
        "`blocker.recorded` for each typed blocker raised, naming its token",
        # Decision 3's stage marker identity and names, with the re-entry
        # ruling the runtime's refusal forced.
        "At each workflow's entry and exit, emit `activity.observed` as its"
        " stage marker: `activityId: stage-<name>`, `phase: <name>`, `state:"
        " running` at entry and `completed`, `failed` or `interrupted` at exit,"
        " a new `attemptId` for each entry, and `owner` the host",
        "`<name>` is one of `route`, `plan`, `diagnose`, `execute`, `review` and"
        " `compound`: `$deliver-work` marks `route`, `$plan-work` `plan`,"
        " `$diagnose-work` `diagnose`, `$execute-work` `execute` and `review`"
        " around each review round it obtains, and compounding `compound`. A"
        " workflow that joins a run marks its own stage in it",
        "A stage entered again keeps its `activityId`, takes a new `attemptId`,"
        " and names the stage's previous attempt as `supersedesAttemptId`;"
        " every later state of that attempt repeats it",
        "Where no candidate exists yet, stage markers and waits carry the"
        " worktree's `HEAD^{tree}` as `candidateTreeSha`",
        "Lens attempts, executor verification and the runtime's own command"
        " phases are not stage markers and never take a `stage-` activity id",
        "Mark every stop for an approval or a decision with `wait.started` and"
        " `wait.resolved`, with `waitingOn: human` where the operator is needed",
        # Decision 9's start-only sentence.
        "Where the repository declares a live view command, a workflow that"
        " starts a run as the operator's invocation runs it with `--if-absent`"
        " in the background and gives the operator the address. A workflow that"
        " joins a run, and an epic lane, does neither",
    ),
    "skills/deliver-work/SKILL.md": (
        "Under `$compound-delivery-kernel`'s run lifecycle rule, this workflow"
        " starts the request's run at `route` and marks its `route` stage, and"
        " every workflow it routes to joins that run and starts none",
        "The run stays open while the request waits on the operator between"
        " workflows, and this workflow ends it when the request finishes, stops"
        " on a blocker, or is stopped with work left",
    ),
    "skills/plan-work/SKILL.md": (
        "Apply `$compound-delivery-kernel`'s run lifecycle rule: invoked by the"
        " operator this workflow starts the run, invoked by another workflow it"
        " joins that run, and it marks its stage `plan`",
        "The approval the plan waits on is a wait within the request, under the"
        " kernel's rule, not the run's end",
    ),
    "skills/diagnose-work/SKILL.md": (
        "Apply `$compound-delivery-kernel`'s run lifecycle rule: invoked by the"
        " operator this workflow starts the run, invoked by another workflow it"
        " joins that run, and it marks its stage `diagnose`",
        "A diagnosis that a later stage runs as a subflow joins its caller's"
        " run",
    ),
}


# The paragraph that qualifies the emit-point list reads off its own position:
# `This list`, `those`, and `above` name the list they follow, so the complete
# paragraph is pinned to the last bullet rather than to the file.
EMIT_POINT_LIST_CLOSE: tuple[str, str] = (
    "skills/execute-work/SKILL.md",
    "for each typed blocker this workflow raises, naming its token."
    " This list is not the whole journal. `$obtain-review` emits `lens.selected`"
    " and `review.round.opened`, `$compound-learning` emits"
    " `compounding.recorded`, and this workflow, as `$review-work`'s caller,"
    " emits `review.round.closed` once that reducer has closed a round. The two"
    " `$obtain-review` and `$compound-learning` entries are owned by the skill"
    " named beside them, and this workflow neither restates their rules nor"
    " emits them in that skill's place.",
)


# The emit points are a section in their own right, not a subsection. Nested
# under a section that opens "Before claiming `complete`", their timing reads as
# bounded by the end of the delivery, while the first point fires `run.started`
# at the earliest moment of the run. Archived text is whitespace-normalized, so
# a deeper heading contains the shallower one as a substring: the pin is the
# pair, and the negative half is the load-bearing one.
UNNESTED_SECTIONS: dict[str, tuple[str, ...]] = {
    "skills/execute-work/SKILL.md": ("Emit the Run's Events",),
}


# The unconditional start and end are withdrawn: a workflow starts, joins or
# ends a run only under the kernel's rule. Every sentence of these skills that
# speaks of starting or ending a run, in any of these wordings, names the kernel
# it defers to. The synonyms are lower-case and matched case-insensitively.
WITHDRAWN_LIFECYCLE_SKILLS: tuple[str, ...] = (
    "skills/execute-work/SKILL.md",
    "skills/execute-work/references/run-observation-contract.md",
    "skills/plan-work/SKILL.md",
    "skills/diagnose-work/SKILL.md",
    "skills/deliver-work/SKILL.md",
    # The other workflows that apply the kernel or run inside a delivery. A
    # skill that speaks of a check run rather than a delivery's run, such as
    # run-delivery's configuration reference, is left out.
    "skills/execute-linear-ticket/SKILL.md",
    "skills/create-linear-ticket/SKILL.md",
    "skills/execute-jira-ticket/SKILL.md",
    "skills/create-jira-ticket/SKILL.md",
    "skills/review-work/SKILL.md",
    "skills/obtain-review/SKILL.md",
    "skills/compound-learning/SKILL.md",
    "skills/orchestrate-work/SKILL.md",
)
WITHDRAWN_LIFECYCLE_SYNONYMS: tuple[str, ...] = (
    "start the run",
    "starts the run",
    "start a run",
    "start a linked successor",
    "starts a run",
    "starts its run",
    "starts its own run",
    "start its own run",
    "begins its run",
    "begin a run",
    "begin the run",
    "begins the run",
    "open a run",
    "opens a run",
    "end the run",
    "ends the run",
    "end its run",
    "ends its run",
    "close the run",
    "closes the run",
    "emit `run.started` first",
    "`run.started` first",
    "`run.started` before",
    "`run.ended` last",
)
# A sentence defers to the kernel only by naming the skill or its rule; a
# sentence that merely mentions "the kernel" can still contradict the rule.
KERNEL_REFERENCES: tuple[str, ...] = (
    "`$compound-delivery-kernel`",
    "the kernel's rule",
    "kernel's run lifecycle rule",
)


def uses(sentence: str, synonym: str) -> bool:
    """Whether ``sentence`` uses ``synonym`` as words: "starts the runner" does not use "starts the run"."""
    return re.search(rf"(?<![\w-]){re.escape(synonym)}(?![\w-])", sentence.lower()) is not None


def defers_to_the_kernel(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(reference.lower() in lowered for reference in KERNEL_REFERENCES)


def sentences(text: str) -> list[str]:
    """``text``, already whitespace-normalized, split after each full stop.

    A colon or semicolon does not end the sentence: "Apply the kernel's rule:
    invoked by the operator it starts the run" defers to the kernel as one
    sentence."""
    return [part for part in re.split(r"(?<=\.)\s+", text) if part]


class RunEventEmissionCorpusTests(unittest.TestCase):
    """Pin the emit points in the skill text a release actually ships."""

    archived: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        archived: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            # Every releasable profile, because a skill only one of them ships
            # names its emit points for that profile's adopters and nobody else.
            for profile_id in sorted(SUPPORTED_PROFILES):
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
                        # This is a prose sensor over shipped skill Markdown.
                        # Agent metadata and executable or supporting formats
                        # remain outside its remit.
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
            denied = sorted(KNOWN_EVENT_KINDS - allowed)
            for kind in denied:
                with self.subTest(path=relative, kind=kind):
                    self.assertNotIn(kind, text)

    def test_unrelated_dotted_vocabulary_is_not_an_event_kind(self) -> None:
        unrelated = {"`review.acquire`", "`pyproject.toml`"}

        self.assertTrue(unrelated.isdisjoint(KNOWN_EVENT_KINDS))

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

    def test_no_workflow_starts_or_ends_a_run_outside_the_kernels_rule(self) -> None:
        for relative in WITHDRAWN_LIFECYCLE_SKILLS:
            for sentence in sentences(self.archived[relative]):
                for synonym in WITHDRAWN_LIFECYCLE_SYNONYMS:
                    if uses(sentence, synonym):
                        with self.subTest(path=relative, synonym=synonym):
                            self.assertTrue(defers_to_the_kernel(sentence), sentence)

    def test_the_withdrawn_sensor_catches_an_unconditional_start(self) -> None:
        # The absence check above is only as good as its synonyms and its
        # sentence split: an unconditional sentence pasted into a skill fails.
        for synonym in WITHDRAWN_LIFECYCLE_SYNONYMS:
            pasted = (f"Before reading repository authority, {synonym} for the delivery. Apply the kernel's rule."
                      f" When the plan is approved, {synonym}, as the kernel says.")
            with self.subTest(synonym=synonym):
                offending = [s for s in sentences(pasted) if uses(s, synonym)]
                self.assertEqual(len(offending), 2)
                # Neither names the skill or its rule; the second only
                # mentions the kernel while contradicting it.
                self.assertFalse(any(defers_to_the_kernel(s) for s in offending))
        self.assertTrue(defers_to_the_kernel("Under the kernel's rule, it ends the run only where it started it."))
        self.assertFalse(uses("Only the lead starts the runner.", "starts the run"))

    def test_each_emit_section_stands_unnested(self) -> None:
        for relative, headings in UNNESTED_SECTIONS.items():
            for heading in headings:
                with self.subTest(path=relative, heading=heading):
                    self.assertIn(f"## {heading}", self.archived[relative])
                    self.assertNotIn(f"### {heading}", self.archived[relative])


if __name__ == "__main__":
    unittest.main()
