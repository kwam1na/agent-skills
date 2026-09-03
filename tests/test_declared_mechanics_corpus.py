from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Mechanics an executor would otherwise re-derive per delivery: how a candidate
# reference resolves, the typed blocker vocabulary and its coining rule, the
# convention for realizing a lens where the harness supplies no realization, the
# install convention an adopting repository and its executors follow, the
# release cadence, and how a red proof of a pin on a provenance-covered file
# must move the digests.
REQUIRED_PHRASES: dict[str, tuple[str, ...]] = {
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
        "| Typed blocker | Raised when |",
        "`review.loop-bound-reached` | The bound is reached with dissent open",
        "`review.deferral-untracked` | The delivery would report done while a"
        " finding some lens recorded as a deferral has no tracked follow-up item",
        "A new token takes a namespace the raising skill owns, is kebab-case in"
        " both its namespace and its name, and is recorded on the work item the"
        " first time it is used",
    ),
    "skills/obtain-review/SKILL.md": (
        "One subagent per lens",
        "The charter is supplied to it verbatim beside the round brief, and the"
        " executor composes nothing about the charter itself",
        "A lens is read-only. No two lenses in a round share context",
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


class DeclaredMechanicsCorpusTests(unittest.TestCase):
    """Pin the declared mechanics in the repository's durable text."""

    def test_declared_mechanics_are_present(self) -> None:
        self.assertEqual(missing_phrases(corpus_texts(ROOT)), set())

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
