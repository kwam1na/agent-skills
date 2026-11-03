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
them. Tag each release with an annotated version tag, `vMAJOR.MINOR.PATCH` with
an optional `-rc.N` and no leading zeros, on a commit CI ran on as the tip of a
push to the default branch, such as a merge commit. Major means an adopter must act, minor is additive, and patch changes no
contract. Release ids are `<profile>-<version>`. A tag's version must be strictly
greater than every existing version tag, with a candidate below its final, and its
commit must equal or descend from the highest one's; a backport to an older line is
unsupported. Never move or reuse a release tag. A version tag that never published
may be deleted to clear that guard, which is neither. Pushing the tag publishes a
GitHub release of the same name from the tagged tree, carrying its verified product
archives, release files and records with their build provenance; that release is
the channel adopters download from. A release edits no file in the repository and
needs no live tracker run. Older set-named tags remain, frozen in place with their
checked-in bytes, and do not match the publish trigger.

A runtime or record-shape change goes out first as a candidate, is applied in one
adopter, and is then promoted by tagging the final version on the same commit. The
final is built again, so its digests differ from the candidate's because its release
id does; an adopter verifies the final's own. See `docs/product-generation.md`.

The default branch is unprotected, and CI on it informs rather than blocks. A merge
can land on red, and it falls to whoever merges to read the result, because a red
gating job blocks the next release: the release workflow publishes only on CI's
success for the exact tagged commit. That gate is the Ubuntu job and `cut-release` on
the push to the default branch. CI's macOS job runs there, and with a `full-matrix`
label on a change request, to inform: its result, or its absence, neither allows nor
blocks a release, and nothing waits for it.

Before tagging, run the full suite locally on macOS with Python 3.11. The release job
cannot verify this, so it rests on whoever tags. A newer Python can hide what the
declared minimum shows, so a run on one doesn't count. The separate environment
leaves `.venv` alone:

```bash
uv python install 3.11
uv run --no-project --python 3.11 python scripts/bootstrap-test-environment.py --environment .venv-3.11
.venv-3.11/bin/python scripts/run-tests.py
```

Windows is not validated. Its code fixes and guards stay, unproven, and CI does not
run it.

`runtimeVersion` does not identify runtime bytes: two different runtimes can declare
the same version, so compare digests. `scripts/cut-release.py` prints nothing on
success, so read its exit code and its output directory.

A green local run does not prove the declared minimum Python or another operating
system. Reproducing a CI failure has to clear the local environment's variables
first, keeping the runner's baseline, such as `PATH`, `HOME` and the git identity CI
configures, and setting what the workflow's own `env` block sets, because a variable
left in the session's shell can hide the failure. Never point `TMPDIR` under a checkout: tests that copy the
repository root then recurse into their own copy without bound. A local suite run
can leave bytecode under `skills/`, and `scripts/cut-release.py` then fails with
`release.source_validation`. Clear it, then cut again:

```bash
find skills -type d -name __pycache__ -prune -exec rm -rf {} +
```

An instruction that allows a fallback gets read as permission to use it, so a ruling
that withdraws one needs a search by meaning across every skill in its profile, with
absence pins on the synonyms. A lens's follow-up does not outrank a binding ruling:
check it against the rulings before adopting it. A test for "clears and returns"
must include the case where nothing observes the clear.

When a releasable skill changes, update its catalog entry, every selected
profile, and the corresponding provenance output digest in the same change.
`scripts/cut-release.py` earns a release's qualification records against that
release's exact archives, so no record is restamped or checked in. Retained
historical qualification records keep their original bytes.
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

Stop only processes you started, by the pid you recorded, and never by pattern with
`pkill` or `killall`. BSD `pkill` stops reading options at the first pattern, so one
such command, meant for a test's children, signalled other applications across the
machine.

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
