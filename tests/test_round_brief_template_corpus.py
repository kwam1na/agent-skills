from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SKILL = "skills/obtain-review/SKILL.md"
TEMPLATE = "skills/obtain-review/references/round-brief-template.md"
# run-delivery's filled copy of the same contract, which it renders for each lens.
RUN_DELIVERY_BRIEF = "skills/run-delivery/templates/round-brief.md"

MEMBER_SENTENCE = re.compile(r"its members are (.+?)\.\s")
QUOTED_MEMBER = re.compile(r"`([^`]+)`")
TEMPLATE_MEMBER = re.compile(r"^## (.+)$", re.MULTILINE)
ROUND_STATEMENT = re.compile(
    r"^- `([^`]+)`: (.*?)(?=\n- `|\n\n)", re.MULTILINE | re.DOTALL
)

REQUIRED_SKILL_PHRASES = (
    "The brief is [the round brief template]"
    "(references/round-brief-template.md) filled in, not composed",
    "An executor realizing a lens fills the template's members rather than"
    " composing the brief's shape from this text",
)

SKILL_MEMBER_PHRASES = {
    "Binding tuple": "Obtain the trusted comparison context from the harness",
    "Round and bound": "The brief uses exactly one of these round statements",
    "Round kind and scope": (
        "The brief also states the round kind and its resulting scope"
    ),
    "Fewest-rounds mandate": "The brief carries the mandate",
    "Carry-forward entries": (
        "For a verification round the caller supplies each lens"
    ),
    "Candidate reference resolutions": (
        "The caller also supplies, for each of the two `candidateRef` values"
    ),
    "Charter": (
        "Its `Charter` member contains the selected charter verbatim inside the brief"
    ),
    "Result shape": (
        "`review-lens-result/1`: `outcome`, `findings`, `evidence`"
    ),
    "Observation binding and selected output": (
        "Every selected lens runs in its own clean checkout of the prepared"
        " candidate tree"
    ),    "Checks this lens runs": (
        "The brief's `Checks this lens runs` member carries this rule"
    ),
}

REQUIRED_MEMBERS = tuple(SKILL_MEMBER_PHRASES)
ROUND_STATEMENTS = ("ordinary counted", "grace", "reopened", "unbounded")

REQUIRED_TEMPLATE_PHRASES = (
    "Fill every member below",
    "`review-lens-result/1`",
    "`lateFindings`",
    "round k of N",
    "round N+1 with declared bound N",
    "reopened round k",
    "unbounded round k",
    "supplied verbatim",
)

MEMBER_BODY_PHRASES = {
    "Binding tuple": (
        "prepared candidate tree SHA",
        "Opaque references are reproduced exactly",
    ),
    "Round and bound": (
        "Use exactly one of these statements",
        "round N+1 with declared bound N",
    ),
    "Round kind and scope": (
        "assessment round",
        "verification round",
    ),
    "Fewest-rounds mandate": (
        "conscious effort to find everything the lens can in this round",
        "Round N, the last counted round",
    ),
    "Carry-forward entries": (
        "every prior round in which this lens was selected",
        "result-less state where the acquisition failed",
        "most recent result-carrying entry",
    ),
    "Candidate reference resolutions": (
        "For each of the two `candidateRef` values the delta spans",
        "matches each pair by exact text",
    ),
    "Charter": (
        "The selected lens's charter, supplied verbatim inside this brief",
    ),
    "Result shape": (
        "reviewed tree SHA and the successful start and end identity checks",
        "When this lens was realized by the host convention",
    ),
    "Observation binding and selected output": (
        "own clean checkout of the prepared candidate tree",
        "Repeat both checks after the review",
    ),    "Checks this lens runs": (
        "never the repository's full suite",
        "scoped sensor commands",
        "test files in scope for this round",
    ),
}


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


def _round_statements(text: str, heading: str) -> dict[str, str]:
    return {
        label: " ".join(body.split())
        for label, body in ROUND_STATEMENT.findall(_section(text, heading))
    }


# Where the two briefs say the same thing differently, sentence by sentence, in order: for each section both carry
# as prose, the sentences only obtain-review's template has, then the sentences only run-delivery's brief has. Every
# other sentence of the section is in both. A section run-delivery fills whole from one placeholder is rendered by
# run_delivery.py and isn't compared. A sentence added to one brief only, or a shared one removed from either, makes
# a brief differ where nothing declares it: add the sentence to the other brief too, or declare the difference here.
DIVERGENCES = {
    'Binding tuple': (
        (
            'The trusted comparison context, obtained from the harness and never from a reviewer result:',
            (
                '`releaseId` `<value>`, `profile` `<value>`, `archiveSha256` `<value>`, `metadataSha256` '
                '`<value>`'
            ),
            '`<value>`',
            '`<exact opaque text>`',
            '`<exact opaque text>`',
            '`<tree SHA from the preparation receipt>`',
            '`<positive integer>`',
            '`<lens identifier>`',
        ),
        (
            (
                '`releaseId` `{{releaseId}}`, `profile` `{{profile}}`, `archiveSha256` `{{archiveSha256}}`, '
                '`metadataSha256` `{{metadataSha256}}`'
            ),
            '`{{graph}}` (SHA-256 of workflows/delivery-v1.json in the installed release)',
            '`{{key}}`',
            '`{{candidate}}`',
            '`{{tree}}`',
            '`{{round}}`',
            '`{{lens}}`',
        ),
    ),
    'Fewest-rounds mandate': (
        (
        ),
        (
        ),
    ),
    'Charter': (
        (
            "The selected lens's charter, supplied verbatim inside this brief.",
            'The executor composes nothing about the charter itself.',
        ),
        (
        ),
    ),
    'Result shape': (
        (
            (
                'Return `review-lens-result/1` with `outcome`, `findings`, `evidence`, and the optional '
                '`lateFindings`.'
            ),
            'Only `changes-requested` carries nonempty findings; both carry evidence.',
            "`findings`: only P0 and P1 findings inside the round's scope are actionable.",
            (
                'In a verification round, for delivery review, a new finding outside the delta is filed only at '
                'P0, and a P1 becomes a deferral with a follow-up item; for plan review (`unbounded`), it is '
                'filed at P0 or P1, with no deferral.'
            ),
            (
                '`evidence`: the reviewed tree SHA and the successful start and end identity checks; the '
                're-check of every carried finding of this lens, deferrals included, each reported closed, open, '
                'raised with its new severity, withdrawn with its reason, or still deferred; for delivery '
                "review, the deferrable findings, each with what its follow-up item must say, that item's "
                "outcome, the deferral's identifier, and this lens; and the model class the lens ran under."
            ),
            (
                'When this lens was realized by the host convention, also record the realization basis '
                '`host-convention`; otherwise record the harness-supplied basis.'
            ),
            (
                'End `evidence` with the approval conditions: for each finding this result raises, deferrals '
                'included, whether the change that finding proposes, adopted as written, resolves it.'
            ),
            (
                'Approval conditions are report text inside `evidence`; the result shape does not change, and '
                'nothing enforces them.'
            ),
            "`lateFindings`: each names one of this same result's own `findings` that the lens raised late.",
            'A lens carries none on its assessment round.',
            (
                'A late finding the out-of-delta rule keeps out of `findings` is marked late in the deferral '
                'that records it instead.'
            ),
        ),
        (
            'Return `review-lens-result/1` as one JSON object, and nothing else, as your final message:',
            'Only `changes-requested` carries open P0 or P1 findings; both carry evidence.',
            (
                '`findings`: every finding, with a unique id starting `{{short}}-r{{round}}-` and one severity, '
                'P0 to P3. {{severities}}'
            ),
            (
                '`evidence` (strings, required): the reviewed tree SHA `{{tree}}` and the successful start and '
                'end identity checks; the re-check of every carried finding; the model class you ran under; the '
                'realization basis `host-convention`.'
            ),
            (
                'End `evidence` with the approval conditions: for each finding you raise, whether its proposed '
                'change, adopted as written, resolves it. {{later}}'
            ),
        ),
    ),
    'Observation binding and selected output': (
        (
            'Use the shared `$execute-work/references/run-observation-contract.md`.',
            (
                'Run this lens in its own clean checkout of the prepared candidate tree, separate from the '
                'executor and every other lens.'
            ),
            (
                'Before the lens reads the checkout, verify that its tree SHA equals the prepared candidate tree '
                'SHA and its status is clean.'
            ),
            (
                "Temporary probes are permitted only in this private checkout, never in the executor's checkout "
                'or another shared checkout, and must be removed before the ending checks.'
            ),
            'Repeat both checks after the review.',
            (
                'A failed check fails the acquisition; do not return a lens result from a checkout that moved or '
                'stayed dirty.'
            ),
            (
                'Supply actual run ID, candidate tree from prepared context, activity ID, attempt ID, '
                'roundId/round/lensId, stable event IDs, and selected bounded output paths.'
            ),
            'Name a superseded attempt only for an actual replacement.',
            'Record the host handle and any progress/permission signal limitation; do not parse transcripts.',
            'Capture actual review, dissent, clarification and available partial-output bytes before cleanup.',
            'Return stable artifact/report references or an explicit unavailable reason.',
            (
                'Permission observations never grant authority; reviewer verdict and acquisition completion '
                'remain distinct.'
            ),
        ),
        (
            'Your private checkout:',
            (
                "`{{checkout}}` (detached at `{{candidate}}`, prepared with the repository's own prepare "
                'commands, so its tests can run there).'
            ),
            'Run every command there, never in any other checkout.',
            (
                'Before reading: verify `git rev-parse HEAD^{tree}` equals `{{tree}}` and `git status '
                '--porcelain` is empty.'
            ),
            'Repeat both after the review.',
            'If either fails, report that instead of a result.',
            (
                'Temporary probes (e.g. mutants) are permitted only in this private checkout and must be '
                'reverted (`git checkout -- .` and removing any file you added) before the ending checks.'
            ),
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-{{key}}`, '
                'attemptId `1`, roundId `{{key}}-r{{round}}`, round `{{round}}`, lensId `{{lens}}`.'
            ),
            (
                'Host: a native subagent; no progress or permission signals are exposed to you; record that '
                'limitation in evidence.'
            ),
            'Do NOT spawn subagents.',
            'Do not push, commit, or touch any checkout other than yours.',
        ),
    ),
    'Checks this lens runs': (
        (
            (
                "The lens runs the adopting repository's scoped check set in its own checkout, never the "
                "repository's full suite: the full suite runs once, outside the round, at the delivery's tail."
            ),
            (
                'A lens needing evidence beyond that set names each additional test file, one path at a time, '
                'with what it is evidence for, and never widens a run to a directory.'
            ),
            (
                "A lens that mutates or injects faults limits both to the candidate's changed lines and the code "
                'that calls them, and says so in its evidence.'
            ),
            'The executor fills both, per round:',
            'scoped sensor commands:',
            '`<commands>`',
            'test files in scope for this round:',
            '`<paths>`',
        ),
        (
            (
                "Run the scoped check set below in your checkout, never the repository's full suite: the full "
                "suite runs once, outside the round, at the delivery's tail."
            ),
            (
                'Evidence beyond that set names each additional test file, one path at a time, with what it is '
                'evidence for; never widen a run to a directory.'
            ),
            (
                "Mutation and fault injection stay on the candidate's changed lines and the code that calls "
                'them, and your `evidence` says so.'
            ),
            '{{checks}}',
        ),
    ),
}


def _brief_sections(text: str) -> dict[str, str]:
    parts = re.split(r"(?m)^## (.+)$", text)
    return dict(zip(parts[1::2], parts[2::2]))


def _sentences(body: str) -> list[str]:
    """A section's prose as sentences, whitespace-normalized: list items and paragraphs split at sentence ends."""
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    found = []
    for item in re.split(r"(?m)^\s*-\s+|\n\s*\n", body):
        item = " ".join(item.split())
        if item:
            found += [part for part in re.split(r"(?<=[.;:])\s+(?=[A-Z`(])", item) if part]
    return found


def _divergence(template: str, brief: str) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """For each section both briefs carry as prose, the sentences only the template has and only the brief has."""
    ours, theirs = _brief_sections(template), _brief_sections(brief)
    found = {}
    for heading, body in ours.items():
        filled = _sentences(theirs.get(heading, ""))
        if len(filled) == 1 and re.fullmatch(r"\{\{\w+\}\}", filled[0]):
            continue
        mine = _sentences(body)
        found[heading] = (tuple(s for s in mine if s not in filled), tuple(s for s in filled if s not in mine))
    return found


class RoundBriefTemplateCorpusTests(unittest.TestCase):
    """Pin the shipped round brief template against the skill text that names it."""

    def test_template_ships_beside_the_skill(self) -> None:
        self.assertTrue((ROOT / TEMPLATE).is_file(), TEMPLATE)

    def test_skill_text_points_at_the_template(self) -> None:
        normalized = " ".join(_read(SKILL).split())
        for phrase in REQUIRED_SKILL_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, normalized)

    def test_template_carries_its_declared_phrases(self) -> None:
        normalized = " ".join(_read(TEMPLATE).split())
        for phrase in REQUIRED_TEMPLATE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, normalized)

    def test_member_sets_match_in_both_directions(self) -> None:
        normalized_skill = " ".join(_read(SKILL).split())
        sentence = MEMBER_SENTENCE.search(normalized_skill + " ")
        self.assertIsNotNone(sentence, "the skill text names no template members")
        named = QUOTED_MEMBER.findall(sentence.group(1))
        self.assertTrue(named, "the member sentence quotes no member")
        self.assertEqual(len(named), len(set(named)), named)
        self.assertEqual(set(named), set(REQUIRED_MEMBERS))

        shipped = TEMPLATE_MEMBER.findall(_read(TEMPLATE))
        self.assertTrue(shipped, "the template declares no member heading")
        self.assertEqual(len(shipped), len(set(shipped)), shipped)
        self.assertEqual(set(shipped), set(REQUIRED_MEMBERS))

        self.assertEqual(
            set(named) - set(shipped),
            set(),
            "the skill text names a member the template omits",
        )
        self.assertEqual(
            set(shipped) - set(named),
            set(),
            "the template ships a member the skill text does not name",
        )

        for member, phrase in SKILL_MEMBER_PHRASES.items():
            with self.subTest(member=member, phrase=phrase):
                self.assertIn(phrase, normalized_skill)

    def test_round_statement_sets_match_in_both_directions(self) -> None:
        skill_statements = _round_statements(_read(SKILL), "The round brief")
        template_statements = _round_statements(_read(TEMPLATE), "Round and bound")
        self.assertEqual(tuple(skill_statements), ROUND_STATEMENTS)
        self.assertEqual(tuple(template_statements), ROUND_STATEMENTS)
        self.assertEqual(skill_statements, template_statements)

    def test_each_required_member_carries_its_normative_body(self) -> None:
        template = _read(TEMPLATE)
        matches = list(TEMPLATE_MEMBER.finditer(template))
        bodies: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(template)
            )
            bodies[match.group(1)] = " ".join(template[match.end() : end].split())

        self.assertEqual(set(MEMBER_BODY_PHRASES), set(REQUIRED_MEMBERS))
        for member, phrases in MEMBER_BODY_PHRASES.items():
            for phrase in phrases:
                with self.subTest(member=member, phrase=phrase):
                    self.assertIn(phrase, bodies[member])


class RoundBriefDriftTests(unittest.TestCase):
    """obtain-review's template and run-delivery's brief are two copies of one contract: neither drifts alone."""

    def test_the_briefs_differ_only_where_declared(self) -> None:
        found = _divergence(_read(TEMPLATE), _read(RUN_DELIVERY_BRIEF))
        self.assertEqual(set(found), set(DIVERGENCES), "a compared section appeared or went")
        for heading, (only_template, only_brief) in DIVERGENCES.items():
            with self.subTest(section=heading):
                self.assertEqual(found[heading][0], only_template,
                                 "obtain-review's template differs from run-delivery's brief where nothing declares it")
                self.assertEqual(found[heading][1], only_brief,
                                 "run-delivery's brief differs from obtain-review's template where nothing declares it")

    def test_the_rules_both_carry_are_compared(self) -> None:
        # The stopping rule nearly shipped in one brief only: it is a shared sentence, so it is compared.
        template, brief = _read(TEMPLATE), _read(RUN_DELIVERY_BRIEF)
        shared = {heading: [s for s in _sentences(body) if s in _sentences(_brief_sections(brief)[heading])]
                  for heading, body in _brief_sections(template).items() if heading in DIVERGENCES}
        self.assertIn("A lens is read-only, files no tracked item itself, and cannot return `failed`.",
                      shared["Result shape"])
        self.assertTrue(any(s.startswith("It stops only processes it started") for s in shared["Result shape"]))
        mandate = _sentences(_brief_sections(template)["Fewest-rounds mandate"])
        self.assertEqual(shared["Fewest-rounds mandate"], mandate)

    def test_removing_any_shared_sentence_from_either_brief_is_seen(self) -> None:
        template, brief = _read(TEMPLATE), _read(RUN_DELIVERY_BRIEF)
        baseline = _divergence(template, brief)
        checked = 0
        for heading in DIVERGENCES:
            ours, theirs = _brief_sections(template)[heading], _brief_sections(brief)[heading]
            for sentence in [s for s in _sentences(ours) if s in _sentences(theirs)]:
                for side in ("template", "brief"):
                    with self.subTest(section=heading, side=side, sentence=sentence[:60]):
                        body = ours if side == "template" else theirs
                        cut = " ".join(body.split()).replace(sentence, "", 1)
                        if side == "template":
                            changed = _divergence(template.replace(ours, "\n" + cut + "\n"), brief)
                        else:
                            changed = _divergence(template, brief.replace(theirs, "\n" + cut + "\n"))
                        self.assertNotEqual(changed, baseline)
                        checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
