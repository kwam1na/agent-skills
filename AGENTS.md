# Agent Skills Corpus Guide

Keep this repository metadata-first while the workflow bodies are being
classified. The release surface is the deterministic, core-only archive plus
its transactional repository lifecycle and fixed Codex and Claude Code
projections. Do not add more hosts, compatibility engines, or
repository-specific overlays without a separately approved unit.

Use outcome names in durable docs and metadata. Keep ticket and plan-unit
identifiers in delivery records rather than standing source language.

A `candidateRef` naming a review candidate resolves, in this repository, to the
commit whose tree carries the reviewed text. The executor supplies that
resolution beside the exact reference text, and a lens matches the pair by exact
text rather than by parsing the reference.

Cut releases at batch boundaries, not per ruling. Operator rulings
accumulate as merged text on the default branch, and one release carries
them.

When a releasable skill changes, update its catalog entry, every selected
profile, and the corresponding provenance output digest in the same change.
Keep JSON serialized as two-space-indented UTF-8 with a final newline, then run:

```bash
python3 scripts/validate-registry.py
python3 tests/test_registry.py
python3 -m unittest discover -s tests -p 'test_*.py'
```

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
