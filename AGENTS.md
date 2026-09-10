# Agent Skills Corpus Guide

Keep this repository metadata-first while the workflow bodies are being
classified. The release surface is deterministic, profile-bound archives plus
their transactional repository lifecycle and fixed Codex and Claude Code
projections. Product generations can include a paired harness runtime. Do not add more hosts, compatibility engines, or
repository-specific overlays without a separately approved unit.

Use outcome names in durable docs and metadata. Keep ticket and plan-unit
identifiers in delivery records rather than standing source language.

A `candidateRef` naming a review candidate resolves, in this repository, to the
commit whose tree carries the reviewed text. For a verification round the
executor supplies that resolution beside the exact reference text, and a lens
matches the pair by exact text rather than by parsing the reference.

Cut releases at batch boundaries, not per ruling. Operator rulings
accumulate as merged text on the default branch, and one release carries
them.

When a releasable skill changes, update its catalog entry, every selected
profile, and the corresponding provenance output digest in the same change,
with qualification records re-earned against the candidate's release, not
restamped. Retained historical qualification records keep their original bytes.
Keep JSON serialized as two-space-indented UTF-8 with a final newline. Bootstrap
the worktree-private test environment before running the sensors; a shared
editable install stores one checkout path in site-packages, so concurrent
worktrees would otherwise replace each other's imported source:

```bash
python3 scripts/bootstrap-test-environment.py
.venv/bin/python scripts/validate-registry.py
.venv/bin/python tests/test_registry.py
.venv/bin/python -m unittest discover -s tests -t . -p 'test_*.py'
```

The `-t .` option enters the test package's dependency guard. Without the test
extra, that guard reports one installation failure before importing the suite.
On Windows, use `.venv\Scripts\python.exe` for the sensor commands.

A corpus pin asserts that its phrase is present in the whitespace-normalized
file, not that the phrase sits under any particular heading. Relocating a pinned
phrase under an unrelated heading therefore leaves its pin green, and that is the
accepted contract: the pins guard the repository's durable wording against
deletion and silent rewording, while where a rule sits is a review judgement.
Where a phrase reads off its own position, pin what it depends on, its
neighbouring text carried with it as one phrase or both the expected heading and
the forbidden deeper heading, rather than anchoring every pin in the corpus to
a heading.

Proving a corpus pin red by mutating a file `provenance.lock.json` covers takes
the digests with it. Tests build a release in `setUpClass`, in `setUp`, and in
test bodies, and every such build validates the whole source corpus first, so
mutating the pinned text alone raises `release.source_validation: source corpus
is invalid` across the suite before those assertions run. Mutate the text and
refresh that file's output digest together, so the build succeeds and the pin's
own assertion is what fails, then restore both; a red run carrying
`release.source_validation` errors is a failed proof, not a passed one.

The approved source baseline is Athena commit
`342850073464a2c9b6198d32d11475f5fa5cbe53`. It is evidence for classification
and source provenance, not permission to copy Athena policy into this corpus.
