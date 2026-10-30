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
them. Tag each release with an annotated tag named exactly its set name, such
as `run-delivery-v1`, on the merge commit that carries its archives and
records. Never move or reuse a release tag.

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

The full suite may run through `scripts/run-tests.py` instead of
`unittest discover`; it gives the same verdict across worker processes, and a
merge check may use it the same way:

```bash
.venv/bin/python scripts/run-tests.py
```

It exits 0 when every test passed, 1 when any failed, errored, passed
unexpectedly, timed out or its worker crashed, 2 on a usage error, 5 when no
test ran, and 130 when interrupted, after stopping every worker and what it
started. A unit that hangs is stopped after `--unit-timeout` seconds and named.
Without the option the limit is five times the slowest unit an earlier
`--timings-from` file records, at least 300 seconds, or 1800 seconds with no
usable file; `0` sets no limit.

Mark a test class `run_serially = True`, or a module `RUN_SERIALLY = True`,
when it cannot share the machine: a fixed port, a fixed path outside its own
temporary folder, or process-wide state.

Refresh output digests with the provenance writer rather than by hand. It
recomputes the sha256 of every output `provenance.lock.json` lists, changes
nothing else, leaves a current lock byte for byte, and restores a lock in any
other layout to canonical form; `--skill <id>` limits it
to that catalog skill's entry, and `--check` lists stale outputs and exits 1
without writing. A refusal exits 2 and writes nothing: a malformed lock, or a
listed output that is missing or outside the corpus, so adding or removing an
output stays a deliberate edit to the lock:

```bash
.venv/bin/python scripts/update-provenance.py
```

When a rebase or merge conflicts only in `provenance.lock.json`, take either
side of the lock, run the writer, then validate the registry before continuing.
The writer never adds or removes an output, so when one side added or removed a
listed output, take that side; validating the registry reports any output the
lock no longer lists. When both sides changed which outputs are listed, merge
those lists by hand first, then run the writer.

The `-t .` option enters the test package's dependency guard. Without the test
extra, that guard reports one installation failure before importing the suite.

The page-behaviour lane runs the delivery views' page scripts under Node's
built-in test runner with `jsdom`, through `tests/test_delivery_views_page.py`.
It is development-only: bootstrap needs Node.js 20 or newer with npm and runs
`npm ci --ignore-scripts` against the committed `package-lock.json`, installing
into the checkout's own `node_modules/`. No release archive carries the lane's
package files.

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
