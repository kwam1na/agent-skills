from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Mechanics an executor would otherwise re-derive per delivery: how a candidate
# reference resolves, the typed blocker vocabulary and its coining rule, and the
# convention for realizing a lens where the harness supplies no realization.
REQUIRED_PHRASES: dict[str, tuple[str, ...]] = {
    "AGENTS.md": (
        "resolves, in this repository, to the commit whose tree carries the"
        " reviewed text",
        "a lens matches the pair by exact text rather than by parsing the"
        " reference",
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
        "apart from the restore-on-exit allowance the adversarial charter already"
        " grants itself",
        "No two lenses in a round share context",
        "The evidence names the model class the lens ran under",
        "records independence as `host-convention`, naming this section",
        "it builds no realization runtime, and this workflow launches no"
        " subordinate runtime",
    ),
}


def normalized(root: Path, relative: str) -> str:
    return " ".join((root / relative).read_text(encoding="utf-8").split())


def missing_phrases(root: Path) -> set[tuple[str, str]]:
    absent: set[tuple[str, str]] = set()
    for relative, phrases in REQUIRED_PHRASES.items():
        contents = normalized(root, relative)
        absent.update(
            (relative, phrase) for phrase in phrases if phrase not in contents
        )
    return absent


class DeclaredMechanicsCorpusTests(unittest.TestCase):
    """Pin the declared mechanics in the repository's durable text."""

    def copied_corpus(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        copied = Path(temporary.name) / "corpus"
        shutil.copytree(
            ROOT, copied, ignore=shutil.ignore_patterns(".git", "__pycache__")
        )
        return temporary, copied

    def test_declared_mechanics_are_present(self) -> None:
        self.assertEqual(missing_phrases(ROOT), set())

    def test_each_declaration_is_red_for_exactly_itself(self) -> None:
        for relative, phrases in REQUIRED_PHRASES.items():
            for phrase in phrases:
                with self.subTest(path=relative, phrase=phrase):
                    temporary, root = self.copied_corpus()
                    self.addCleanup(temporary.cleanup)
                    target = root / relative
                    contents = normalized(root, relative)
                    self.assertIn(phrase, contents)
                    target.write_text(
                        contents.replace(phrase, "", 1) + "\n", encoding="utf-8"
                    )
                    self.assertEqual(
                        missing_phrases(root), {(relative, phrase)}
                    )


if __name__ == "__main__":
    unittest.main()
