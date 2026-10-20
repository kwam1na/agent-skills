from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_skills.release import ReleaseError, build_release


ROOT = Path(__file__).resolve().parents[1]

# Mechanics an executor would otherwise re-derive per delivery: how a candidate
# reference resolves, the typed blocker vocabulary and its coining rule, the
# convention for realizing a lens where the harness supplies no realization, the
# install convention an adopting repository and its executors follow, the
# release cadence, how a red proof of a pin on a provenance-covered file must
# move the digests, and what a corpus pin asserts about a phrase it matches.
REQUIRED_PHRASES: dict[str, tuple[str, ...]] = {
    "skills/plan-work/SKILL.md": (
        "search the existing tests for literal identifiers, arrays, and counts"
        " that will become stale",
        "Preserve any stricter boundary the caller explicitly imposed",
        "For parallel units and review lenses, list shared ports, browser"
        " sessions, databases, and caches alongside file ownership",
        "Record sensors whose assertions compare to the merge base",
    ),
    "skills/linear-tracker-adapter/SKILL.md": (
        "Apart from `schemaVersion`, which identifies the document format, it"
        " declares only properties an operation consumes",
    ),
    "skills/deliver-work/SKILL.md": (
        "call its read-only `resume` command before reusing evidence on continuation",
    ),
    "skills/review-work/SKILL.md": (
        "When the host reports no cost, record `coverage: unreported`; do not invent zero",
    ),
    "AGENTS.md": (
        "resolves, in this repository, to the commit whose tree carries the"
        " reviewed text",
        "For a verification round the executor supplies that resolution beside"
        " the exact reference text",
        "a lens matches the pair by exact text rather than by parsing the"
        " reference",
        "Cut releases at batch boundaries, not per ruling. Operator rulings"
        " accumulate as merged text on the default branch, and one release"
        " carries them",
        "by mutating a file `provenance.lock.json` covers takes the digests"
        " with it",
        "mutating the pinned text alone raises"
        " `release.source_validation: source corpus is invalid` across the"
        " suite before those assertions run",
        "Mutate the text and refresh that file's output digest together, so the"
        " build succeeds and the pin's own assertion is what fails, then restore"
        " both",
        "A corpus pin asserts that its phrase is present in the"
        " whitespace-normalized file, not that the phrase sits under any"
        " particular heading",
        "Relocating a pinned phrase under an unrelated heading therefore leaves"
        " its pin green, and that is the accepted contract",
        "Where a phrase reads off its own position, pin what it depends on, its"
        " neighbouring text carried with it as one phrase or both the expected"
        " heading and the forbidden deeper heading, rather than anchoring every"
        " pin in the corpus to a heading",
        "qualification records re-earned against the candidate's release, not"
        " restamped",
        "Refresh output digests with the provenance writer rather than by hand",
        "When a rebase or merge conflicts only in `provenance.lock.json`, take"
        " either side of the lock, run the writer, then validate the registry"
        " before continuing",
    ),
    "README.md": (
        "`auditReferences` uses the same order as `entities`: primary first,"
        " then dependency",
    ),
    "docs/host-adapters.md": (
        "An adopting repository's overlay names what that repository adds: its"
        " gate, its sensors, its worktree and branch rules, how it realizes a"
        " reviewer, and its protected paths",
        "For every rule the installed workflow already carries, the overlay"
        " references the installed workflow rather than restating it; it does"
        " not restate the review round bound, the grace round, the deferral"
        " rules, or the rules for resolving a finding",
        "A release install in a consumer is delivered `sensor-only`, with"
        " `active.json` as the declared review scope",
        "Its sensors are the lifecycle status sensor \u2014 `lifecycle: current`,"
        " empty `blockers`, and the new generation active \u2014 together with any"
        " policy recompilation that consumer performs",
        "Everything under the installed generation's directory is generated"
        " release payload, outside review scope by declaration",
    ),
    "skills/execute-work/SKILL.md": (
        "If related test-pin updates are allowed, make the smallest correction"
        " and record the scoped exception with its proving sensor",
        "A stricter explicit caller boundary still governs",
        "Worktree isolation alone does not isolate those resources",
        "Retain the original `review-context --json` document before dispatching reviewers",
        "Never reconstruct a receipt, invent a passed check, or infer evidence reuse from journal events",
        "Record `action.intent` before an authorized external action",
        "| Typed blocker | Raised when |",
        "`review.loop-bound-reached` | The bound is reached with dissent open",
        "`review.deferral-untracked` | The delivery would report done while a"
        " finding some lens recorded as a deferral has no tracked follow-up item",
        "A new token takes a namespace the raising skill owns, is kebab-case in"
        " both its namespace and its name, and is recorded on the work item the"
        " first time it is used",
    ),
    "skills/obtain-review/SKILL.md": (
        "Native subagent dispatch and the aggregation below are sufficient;"
        " Python and `agent_skills.review_orchestration` are not prerequisites",
        "The Python helpers `prepare_review` and `obtain_review` are optional"
        " implementations of this same contract when the caller has them",
        "Retain one entry for every selected lens in the caller's order,"
        " including missing, failed, and indeterminate acquisitions",
        "Copy the trusted binding tuple into the envelope and every entry;"
        " compare any binding returned by a reviewer against that tuple and"
        " reject a mismatch",
        "Project each obtained result to `review-work` with its lens id,"
        " outcome, findings, evidence, and optional late findings",
        "One subagent per lens",
        "The template is the shape of every lens brief, whether the realization"
        " is harness-supplied or follows the host convention",
        "Its `Charter` member contains the selected charter verbatim inside the"
        " brief",
        "Every selected lens runs in its own clean checkout of the prepared"
        " candidate tree, separate from the executor and every other lens",
        "verify that the checkout's tree SHA equals the prepared candidate tree"
        " and that its status is clean immediately before the lens reads it",
        "Repeat both checks after the lens finishes",
        "The lens names the reviewed tree SHA and both successful identity checks"
        " in its evidence",
        "The evidence names the model class the lens ran under",
        "records its realization basis as `host-convention`, naming this section,"
        " rather than leaving the basis of the realization unrecorded",
        "The acquisition entry's typed `independence` is unchanged by the"
        " convention and stays `{\"status\": \"unverified\"}`",
        "it builds no realization runtime, and this workflow launches no"
        " subordinate runtime",
    ),
}


def corpus_texts(root: Path) -> dict[str, str]:
    return {
        relative: " ".join((root / relative).read_text(encoding="utf-8").split())
        for relative in REQUIRED_PHRASES
    }


def missing_phrases(texts: dict[str, str]) -> set[tuple[str, str]]:
    return {
        (relative, phrase)
        for relative, phrases in REQUIRED_PHRASES.items()
        for phrase in phrases
        if phrase not in texts[relative]
    }


def markdown_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


class DeclaredMechanicsCorpusTests(unittest.TestCase):
    """Pin the declared mechanics in the repository's durable text."""

    def test_declared_mechanics_are_present(self) -> None:
        self.assertEqual(missing_phrases(corpus_texts(ROOT)), set())

    def test_relocating_a_phrase_within_its_file_preserves_membership(self) -> None:
        texts = corpus_texts(ROOT)
        relative = "AGENTS.md"
        phrase = REQUIRED_PHRASES[relative][0]
        moved = dict(texts)
        moved[relative] = texts[relative].replace(phrase, "", 1) + " ## Unrelated " + phrase
        self.assertEqual(missing_phrases(moved), missing_phrases(texts))

    def test_host_convention_is_not_named_as_independence(self) -> None:
        text = (ROOT / "skills/obtain-review/SKILL.md").read_text(encoding="utf-8")
        section = " ".join(markdown_section(text, "Realize a lens by convention").split())
        sentences = re.split(r"(?<=[.!?])\s+", section)
        ambiguous = [
            sentence
            for sentence in sentences
            if "independence" in sentence.casefold()
            and (
                "host-convention" in sentence
                or "evidence-side" in sentence.casefold()
            )
        ]
        self.assertEqual(ambiguous, [])

    def test_documented_source_validation_token_is_raised(self) -> None:
        instructions = " ".join(
            (ROOT / "AGENTS.md").read_text(encoding="utf-8").split()
        )
        match = re.search(
            r"`(release\.source_validation: source corpus is invalid)`",
            instructions,
        )
        self.assertIsNotNone(match)
        documented = match.group(1)

        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "corpus"
            shutil.copytree(
                ROOT,
                copied,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            skill = copied / "skills/execute-work/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8") + "\nInvalidates provenance.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ReleaseError, f"^{re.escape(documented)}$"):
                build_release(
                    copied,
                    copied / "invalid.zip",
                    copied / "invalid.release.json",
                    release_id="invalid-source-v1",
                    profile_id="core",
                )

    def test_each_declaration_is_red_for_exactly_itself(self) -> None:
        texts = corpus_texts(ROOT)
        for relative, phrases in REQUIRED_PHRASES.items():
            for phrase in phrases:
                with self.subTest(path=relative, phrase=phrase):
                    deleted = {
                        **texts,
                        relative: texts[relative].replace(phrase, "", 1),
                    }
                    self.assertEqual(
                        missing_phrases(deleted), {(relative, phrase)}
                    )


if __name__ == "__main__":
    unittest.main()
