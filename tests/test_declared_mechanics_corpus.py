from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from agent_skills.release import ReleaseError, build_release
from corpus_copy import copy_corpus


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
        "Save the plan in the planning folder the repository's instructions"
        " declare, otherwise at `docs/plans/<key>.md`, where `<key>` names the"
        " work, and report the path",
    ),
    "skills/linear-tracker-adapter/SKILL.md": (
        "Apart from `schemaVersion`, which identifies the document format, it"
        " declares only properties an operation consumes",
    ),
    "skills/deliver-work/SKILL.md": (
        "call its read-only `resume` command before reusing evidence on continuation",
    ),
    "skills/obtain-review/SKILL.md": (
        "A lens stops only processes it started, by the pid it recorded, never by pattern with `pkill` or `killall`",
    ),
    "skills/obtain-review/references/round-brief-template.md": (
        "It stops only processes it started, by the pid it recorded, never by pattern with `pkill` or `killall`",
    ),
    "skills/run-delivery/templates/round-brief.md": (
        "It stops only processes it started, by the pid it recorded, never by pattern with `pkill` or `killall`",
    ),
    "skills/run-delivery/references/adopting.md": (
        "keep installing its own tracker's release, because a generation holds one profile",
    ),
    "skills/plan-work/SKILL.md": (
        "Where the repository tracks plans, commit the saved plan under its rules before plan review or removing the"
        " checkout",
        "Whether plans are tracked stays the repository's call",
    ),
    "README.md": (
        "On Windows, which is not validated (see below), use `.venv\\Scripts\\python.exe`",
        "Both tracker profiles build on `run-delivery`, so an adopter on one gets the delivery commands too",
        "--run-delivery-archive /tmp/run-delivery.zip",
        "the supplied extracted Linear and `run-delivery` candidates",
        "an adopter on a tracker gets the same commands from their tracker's archive and keeps installing that one",
        "It never replaces a release that already exists, and it reads no CI result",
        "A release rests on whoever tags",
        "It blocks neither a merge nor a release",
        "Windows, which is not validated (see above), keeps its native `taskkill /T /F` tree cleanup",
        "A record nobody shares is unchanged. See [two engineers, one delivery](docs/collaboration.md)",
    ),
    # The adopter guide to a shared delivery: what a seat is and isn't, how each engineer starts, joins and follows,
    # and what each machine needs and how each hold clears.
    "docs/collaboration.md": (
        "A record nobody shares behaves exactly as it always has",
        "A seat is one writer of a shared record: in practice one clone on one machine",
        "no environment variable sets or overrides it",
        "**A seat is not a person.** One person on two machines holds two seats",
        "**A label is only shown.**",
        "**Identity is trust-based.** Anyone who can push to the base remote can write any seat's chain",
        "Nothing else pushes or ingests a chain",
        "Until a seat is admitted, its only write is its own request",
        "Neither pushes or ingests",
        "Each seat changes only the plan sections it holds, and a plan section nobody was given is the `delivery`"
        " holder's",
        "Only the holder of that section settles it, once",
        "Only the `delivery` holder runs it",
        "`lead-setup` and `lead-init` take the planning record over in place",
        "In between, no machine serves the queue, and the line map says \"queue moving to <label>\"",
        "so the public link is unchanged and no tracker update is posted twice",
        "A machine that may hold `delivery` needs the repository's hosted publishing token and its tracker credentials",
        "Nothing secret travels in a seat's chain",
        "It is ingested once the lagging machine runs its own `lead-upgrade-writer`. No machine upgrades another",
        "refuses every later write with `collab.seat_split`",
    ),
    "docs/host-adapters.md": (
        "On Windows, which is not validated (see the README), directory links use",
    ),
    "docs/hosted-deliveries.md": (
        "the publish command reads no CI result, and the local suite before a tag does not attest service CI",
        # Choose a host: the three paths, the bundle an adopter checks and the rules a portable host keeps.
        "Any service that conforms to it is a valid origin, whoever runs it and wherever.",
        "whatever terminates TLS in front of the service must have its access logging off or path-free",
        "shasum -a 256 -c hosted-delivery-service-<version>.zip.sha256",
        "docker build -f hosted/delivery-viewer/Dockerfile -t hosted-delivery-service:<version> .",
        "Use the bundle from the same release as the generation you applied",
        "docker run --rm --read-only hosted-delivery-service:<version> provision-token REPOSITORY_ID",
        "Network filesystems are unsupported: AWS EFS, Azure Files, Cloud Run volume mounts, GCS FUSE and NFS.",
        "Like Node's own HTTP server, it closes a half-closed connection whose response is still pending. A client"
        " must read its reply before it sends FIN.",
        "No local build of the image is recorded: the image is first built and run in the service CI's container job.",
        "An origin that passes both tiers is a valid origin.",
        "SQLite's exclusive lock and the blob store's `link()` need local block-device semantics. Give the service a"
        " disk that one machine mounts:",
        "AWS: an EBS volume attached to the EC2 instance; - GCP: a Persistent Disk or Hyperdisk attached to the Compute"
        " Engine VM; - Azure: a managed disk attached to the VM;",
        "edit `publishers.json` in place, or run `docker compose restart deliveries` after replacing it",
        "node hosted/conformance/cli.mjs --origin https://deliveries.example.com --credentials credentials.json"
        " node hosted/conformance/cli.mjs --origin https://deliveries.example.com --credentials credentials.json --slow",
        "`hosted.browser`, `hosted.conformance`, `hosted.node-test` and `hosted.conformance-node` for those readers",
    ),
    "docs/product-generation.md": (
        "Tag a commit merged to `main`; a merge commit is the usual one",
        "The publish command reads no CI result and runs no suite",
        "it does not block a release, because the publish command does not read it",
        "A run that passes every test and goes over the suite's time budget does not hold the release",
    ),
    "skills/review-work/SKILL.md": (
        "When the host reports no cost, record `coverage: unreported`; do not invent zero",
    ),
    "skills/run-delivery/templates/lane-brief.md": (
        "Stop only processes you started, by the pid you recorded, never by pattern",
    ),
    "skills/orchestrate-work/references/orchestration-guide.md": (
        "Stop only processes you started, by the pid you recorded, never by pattern",
        # The lane brief's review step.
        "Ask each lens for its result in review-lens-result/1, on top of the shape `$obtain-review`'s round brief gives",
        "Save every lens's result for every round, one outcome file per round, as the lens returned it, outside your"
        " checkout, and keep the files until close-out",
        "Right after your hand-off, which gives the record your reviewed head, capture every round in order",
        "A round's outcome is `aligned` only when every lens aligned and no finding is open",
        "`state` is one of `closed`, `fixed`, `withdrawn`, `dropped`, `deferred` or `open`, and a `deferred` one also"
        " carries `deferredIssue`, naming its follow-up item.",
        "capture-review --subject <subject> --candidate <reviewed head> --outcome <round-1.json> --outcome <round-2.json>",
        # The hand-off check.
        "review evidence was captured for the reviewed head, or the lane said why not.",
        "`delivery_state.py review-evidence --subject <subject>` says which",
        # The lead checklist.
        "A brief written from the template below keeps its review step; a delivery run through `run-delivery` gets the"
        " same from `review-reduce` instead.",
        "`meta.planFile` names the approved plan, so a line map with no plan chapter says so",
        "`plan.json` in the state directory is the plan chapter's one input",
        "names it in `liveHome`, and the live view starts with `--binding`",
    ),
    "skills/orchestrate-work/SKILL.md": (
        "the live view shows the built-in page and says so",
        "`review-evidence --subject <subject>` reports `captured`, `not-captured`, `incomplete`, `unavailable` or `run-named`",
    ),
    "docs/own-deliveries.md": (
        "The binding declares no affected-checks provider, on purpose.",
        "the corpus's own CI jobs inform and hold neither",
        "Nothing here reads a `.env`, this repository's or another's.",
    ),
    "AGENTS.md": (
        "carrying its verified product archives, release files and records, and the hosted delivery service bundle"
        " with its `.sha256`",
        "`docs/solutions/` holds what past deliveries learned, one note per learning at"
        " `docs/solutions/<category>/<slug>-<YYYY-MM-DD>.md`",
        "On Windows, which is not validated (see above), use `.venv\\Scripts\\python.exe`",
        "on a commit merged to the default branch, usually a merge commit",
        "CI does not block a release either: the publish command reads no CI result",
        "CI runs only `cut-release`, on every change request and on each push to the default branch",
        "Stop only processes you started, by the pid you recorded, and never by"
        " pattern with `pkill` or `killall`",
        "resolves, in this repository, to the commit whose tree carries the"
        " reviewed text",
        "For a verification round the executor supplies that resolution beside"
        " the exact reference text",
        "a lens matches the pair by exact text rather than by parsing the"
        " reference",
        "Cut releases at batch boundaries, not per ruling. Operator rulings"
        " accumulate as merged text on the default branch, and one release"
        " carries them",
        "Tag each release with an annotated version tag",
        "strictly greater than every existing version tag",
        "Never move or reuse a release tag",
        "A version tag that never published may be deleted to clear that guard",
        "that release is the channel adopters download from",
        "promoted by tagging the final version on the same commit",
        "its digests differ from the candidate's because its release id does",
        "The default branch is unprotected, and CI on it informs rather than blocks",
        "it falls to whoever merges to read the result and fix what it shows",
        "Before tagging, run the full suite locally on macOS with Python 3.11. A release rests on that run passing",
        "`runtimeVersion` does not identify runtime bytes",
        "A green local run does not prove another operating system",
        "clear the local environment's variables first",
        "Never point `TMPDIR` under a checkout",
        "`scripts/cut-release.py` prints nothing on success",
        "An instruction that allows a fallback gets read as permission to use it",
        "A change that adds tests keeps the suite inside the budget",
        "It never raises a number in the budget; only the operator does",
        "is marked `run_quietly`, never `run_serially`",
        "A run over its budget does not hold the release: the tag's annotation says the run went over",
        "A flaky test does not hold a release: it is one the release doesn't change, and it passes when rerun on its"
        " own at the tagged commit",
        "A lens's follow-up does not outrank a binding ruling",
        "must include the case where nothing observes the clear",
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
        "`scripts/cut-release.py` earns a release's qualification records"
        " against that release's exact archives",
        "Refresh output digests with the provenance writer rather than by hand",
        "When a rebase or merge conflicts only in `provenance.lock.json`, take"
        " either side of the lock, run the writer, then validate the registry"
        " before continuing",
        "The writer never adds or removes an output, so when one side added or"
        " removed a listed output, take that side",
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
        "A shared delivery record, written by two engineers' clones and carried through git, adds no host projection",
        "Neither host discovers anything new for a shared record",
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

# Publishing once required CI's success on the tagged commit, waited for it within a bound, and waited for the
# suite to be back inside its time budget. That is withdrawn: a release rests on the local run before the tag.
# Each pattern is one way of saying a withdrawn claim, matched in any case against the whitespace-normalized
# file, so a synonym that comes back is caught wherever it lands.
WITHDRAWN_RELEASE_GATE_FILES = (
    "AGENTS.md",
    "README.md",
    "docs/product-generation.md",
    "docs/own-deliveries.md",
    "docs/hosted-deliveries.md",
    "docs/linear-connector-check.md",
    ".github/workflows/ci.yml",
    "scripts/release_channel.py",
)
WITHDRAWN_RELEASE_GATE_CLAIMS = (
    # Publishing requires CI.
    r"publish\w* only on CI",
    r"requires? CI\b",
    r"requires? the Ubuntu job",
    r"CI's success",
    r"CI has (not )?passed",
    r"(on|once|after|until|when) CI (is green|passes|passed|succeeds|succeeded)",
    r"gating (job|CI)",
    r"gates? (publishing|releases?|a release|the release)",
    r"the gate is the Ubuntu job",
    r"corpus gate",
    r"alongside (corpus )?Ubuntu",
    r"Ubuntu\W+cut-release\W+service CI",
    r"blocks the next release",
    # Which commit a tag may name.
    r"commit CI (ran|passed) on",
    r"no CI run to read",
    # Waiting for CI.
    r"wait\w* (for|on) CI\b",
    r"bounded (wait|time)",
    r"waits its bound",
    r"within the bound",
    r"neither allows nor blocks",
    r"absent while it (still )?runs",
    r"is waited for",
    r"two and a half hours",
    # Waiting for the time budget.
    r"release waits until",
    r"until the suite is back inside",
    r"(holds?|blocks?|delays?) (a|the|the next) release (until|while) the suite",
)


def withdrawn_release_gate_claims(texts: dict[str, str]) -> set[tuple[str, str]]:
    return {
        (relative, pattern)
        for relative, text in texts.items()
        for pattern in WITHDRAWN_RELEASE_GATE_CLAIMS
        if re.search(pattern, text, re.IGNORECASE)
    }


def release_gate_texts(root: Path) -> dict[str, str]:
    return {
        relative: " ".join((root / relative).read_text(encoding="utf-8").split())
        for relative in WITHDRAWN_RELEASE_GATE_FILES
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

    def test_no_standing_text_says_a_release_waits_on_ci_or_on_the_budget(self) -> None:
        self.assertEqual(withdrawn_release_gate_claims(release_gate_texts(ROOT)), set())

    def test_each_withdrawn_release_gate_claim_is_caught_when_it_comes_back(self) -> None:
        texts = release_gate_texts(ROOT)
        returned = {
            r"publish\w* only on CI": "It publishes only on CI's success for that exact commit.",
            r"gating (job|CI)": "a red gating job blocks the next release",
            r"blocks the next release": "a red Ubuntu job on `main` blocks the next release",
            r"commit CI (ran|passed) on": "on a commit CI ran on as the tip of a push to the default branch",
            r"no CI run to read": "Any other commit has no CI run to read, so it publishes nothing.",
            r"neither allows nor blocks": "its result, or its absence, neither allows nor blocks a release",
            r"is waited for": "a gating job absent while it runs is waited for, within the bound, and then refused",
            r"release waits until": "a release waits until the suite is back inside it",
            r"the gate is the Ubuntu job": "The gate is the Ubuntu job and `cut-release` on the push to `main`.",
            r"wait\w* (for|on) CI\b": "The guard waits for CI on the tagged commit.",
        }
        for pattern, sentence in returned.items():
            with self.subTest(sentence=sentence):
                self.assertIn(pattern, WITHDRAWN_RELEASE_GATE_CLAIMS)
                found = withdrawn_release_gate_claims({**texts, "AGENTS.md": texts["AGENTS.md"] + " " + sentence})
                self.assertIn(("AGENTS.md", pattern), found)
                self.assertEqual({relative for relative, _ in found}, {"AGENTS.md"})

    def test_relocating_a_phrase_within_its_file_preserves_membership(self) -> None:
        texts = corpus_texts(ROOT)
        relative = "AGENTS.md"
        phrase = REQUIRED_PHRASES[relative][0]
        moved = dict(texts)
        moved[relative] = texts[relative].replace(phrase, "", 1) + " ## Unrelated " + phrase
        self.assertEqual(missing_phrases(moved), missing_phrases(texts))

    def test_where_a_plan_is_saved_sits_in_the_handoff(self) -> None:
        text = (ROOT / "skills/plan-work/SKILL.md").read_text(encoding="utf-8")
        section = " ".join(markdown_section(text, "Handoff").split())
        self.assertIn("Save the plan in the planning folder", section)

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
            copy_corpus(copied)
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

    def test_the_hosting_guide_names_exactly_the_bindings_hosted_checks(self) -> None:
        import json

        catalogue = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))["selection"]["catalogue"]
        bound = {row["id"] for row in catalogue if row["id"].startswith("hosted.")}
        named = set(re.findall(r"`(hosted\.[a-z-]+)`", (ROOT / "docs/hosted-deliveries.md").read_text(encoding="utf-8")))
        self.assertEqual(named, bound)

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
