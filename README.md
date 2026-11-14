# Agent Skills Corpus

This repository is the deterministic, metadata-first home for a small portable
agent-skills corpus. Its lifecycle installs only authenticated profile-bound
release archives; it does not extract new workflow bodies or implement direct
provider service clients. One installed generation is exposed to Codex and
Claude Code through stable repository-relative links and one shared
active-generation link, so neither host owns another editable workflow copy or
switches independently.

`catalog.json` lists every cataloged skill. `profiles/` declares selectable
workflow sets, and `provenance.lock.json` binds every cataloged file to its own
immutable repository source or explicitly approved independent origin, license
status, modification status, and SHA-256 digest. Historical source baselines are
not silently advanced when a new skill is added.

Validate the checked-in registry and hostile fixture suite with Python 3.11 or
newer. Bootstrap the worktree-private `.venv` before running the sensors;
production remains standard-library-only. A shared editable install records one
source checkout in the interpreter's site-packages and lets concurrent worktrees
replace each other's source path. Qualification also uses isolated Python
processes, so the pinned schema engine must be installed in the same private
interpreter, not available only through a user-site or search-path override:

```bash
python3 scripts/bootstrap-test-environment.py
.venv/bin/python scripts/validate-registry.py
.venv/bin/python tests/test_registry.py
.venv/bin/python -m unittest discover -s tests -t . -p 'test_*.py'
```

`scripts/run-tests.py` runs the same suite across worker processes, half the
cores by default, and gives the same verdict as `unittest discover` in a
fraction of the time. A test class that sets `run_serially = True`, or a
module that sets `RUN_SERIALLY = True`, runs with nothing beside it. While one
that sets `run_quietly = True`, or `RUN_QUIETLY = True`, runs, at most
`--quiet-workers` units run in all, two fifths of the cores by default. On
macOS the workers call git's own programs rather than `/usr/bin/git`, which
looks them up again on every call.
`--timings <file>` records per-test times, and `--timings-from <file>` starts
the slowest work first. A unit that runs past `--unit-timeout` seconds, by
default five times the slowest unit in the earlier timings and at least 300,
or 1800 without them, is stopped with everything it started and reported as
timed out, while the rest of the run carries on:

```bash
.venv/bin/python scripts/run-tests.py --timings .test-timings.json
```

`scripts/measure-test-writes.py` runs the same suite and reports, per test module,
the files it wrote to temp folders and what it left there. It counts file events,
so it measures on macOS; `--compare` shows two runs side by side anywhere:

```bash
.venv/bin/python scripts/measure-test-writes.py --workers 4 --output after.json
.venv/bin/python scripts/measure-test-writes.py --compare before.json after.json
```

On Windows, which is not validated (see below), use `.venv\Scripts\python.exe` for
the sensor commands.

Build and verify the core profile:

```bash
python3 scripts/build-release.py build --archive /tmp/core.zip --metadata /tmp/core.release.json --release-id core-v1.2.3
python3 scripts/build-release.py verify \
  --archive /tmp/core.zip \
  --metadata /tmp/core.release.json \
  --extract-to /tmp/core-standalone
```

Build the optional Linear profile with the same format and lifecycle:

```bash
python3 scripts/build-release.py build \
  --archive /tmp/linear.zip \
  --metadata /tmp/linear.release.json \
  --release-id linear-v1.2.3 \
  --profile linear
python3 scripts/build-release.py verify \
  --archive /tmp/linear.zip \
  --metadata /tmp/linear.release.json \
  --extract-to /tmp/linear-standalone
```

Both tracker profiles build on `run-delivery`, so an adopter on one gets the delivery
commands too. Those run on POSIX hosts only, which doesn't stop a tracker profile
installing on Windows, though Windows is not validated (see below).
The optional Jira profile builds the same way with `--profile jira`. It reaches
Jira only through the host's Atlassian MCP connector, configured by the adopting
repository's `.agents/tracker-properties.json`, whose `jira` block names the
site, issue types, dependency link type, and any summary prefix, components,
labels and canceled resolutions.

Build and qualify the bootstrap profile independently. Its qualifier installs
the exact archive in a fresh Git repository, verifies both host exposures and
standalone corpus validation, updates through the same command, removes its
source artifacts, and rolls back through installed bytes:

```bash
python3 scripts/build-release.py build \
  --archive /tmp/bootstrap.zip \
  --metadata /tmp/bootstrap.release.json \
  --release-id bootstrap-v1.2.3 \
  --profile bootstrap
python3 scripts/qualify-bootstrap.py \
  --archive /tmp/bootstrap.zip \
  --metadata /tmp/bootstrap.release.json \
  --record /tmp/bootstrap.qualification.json
```

The detached release metadata authenticates the ZIP before it is parsed. The
manifest then authenticates the complete payload and its single profile group.
Verification is bounded by fixed entry, file-size, expanded-size, path-depth,
compression-ratio, and elapsed-time limits. The archive contains the validator,
catalog, selected profile closure, provenance, schemas, inventory, and selected
skills. Core excludes optional profile bodies; Linear and Jira each add their
adapter and the two tracking workflows that call the adapter to the complete
`run-delivery` closure.

An installed product verifies an incoming release against its own limits, so
raising a limit takes a stepping-stone release. The release that raises a limit
must itself fit the old limit, and adopters install it before any release that
exceeds the old limit.

The default `core` profile contains the portable delivery posture, normalized
router, neutral tracker capability contract, bounded planning and execution
workflows, evidence-first diagnosis, exact caller-selected review acquisition,
bounded multi-lens review convergence, and a compounding decision with a default home for solution notes.
Bootstrap guidance is opt-in. The optional Linear profile adds a
host-native MCP adapter plus the decomposition and tracked-execution workflows
that reach the tracker only through it, without changing core behavior when
tracking is unavailable. The optional Jira profile does the same through the
host's Atlassian MCP connector. A cataloged source-only entry is never selectable, and
Athena overlays remain classification inputs: what ships is a narrowed
derivative, never copied repository policy.

Two further optional profiles layer on core. `delivery-views` adds ready-made
views of an orchestrated delivery, configured rather than coded: a live line map
(with a plan chapter when the delivery supplies a `delivery-plan/1` input), a
history page, and tracker and forge snapshots. `run-delivery` adds
`delivery-views` and the commands that carry out an orchestrated delivery: lens
round briefs, gate runs, in-app checks under a lease, draft change requests,
lane hand-offs, and the lead's setup, acceptance, evidence, ship and close-out.
Its product archive is the one an adopter with no supported tracker installs to run
deliveries, and an adopter on a tracker gets the same commands from their tracker's
archive and keeps installing that one, because a generation holds one profile. The
adoption guide is `skills/run-delivery/references/adopting.md`, and
`run_delivery.py doctor` checks a repository's setup. Build either profile like
the others:

```bash
python3 scripts/build-release.py build \
  --archive /tmp/run-delivery.zip \
  --metadata /tmp/run-delivery.release.json \
  --release-id run-delivery-v1.2.3 \
  --profile run-delivery
```

A build like these is for working on the corpus. A release comes from a version tag; see
[Getting a release](#getting-a-release) and
[product generations](docs/product-generation.md#releases).

The [portable workflow graph](docs/workflow-graph-v1.md) declares ten canonical
stages and closed, exactly bound stage results. `diagnose-work` returns causal
evidence without granting fix authority. `obtain-review` retains one ordered,
candidate-bound acquisition round and projects it into unchanged `review-work`
inputs; only that reducer decides convergence. Publication and feedback are
upstream-owned evidence handoffs, not operations supplied by this corpus.
The active host and adopting harness still own realization, permissions, durable
checkpoints, candidate capture, policy, and finish-line authority.
Routing keeps `entry_point` fixed at `deliver-work`. The additive
`RouteDecision.workflow_skill` property identifies the released downstream skill
(`diagnose-work` for diagnosis), or returns `None` for clarification or a blocked
handoff. Repository workflow selection still takes precedence over host hints.

From a trusted checkout, qualify one exact archive in standalone environments
with only the bundled fake capability adapter:

```bash
python3 scripts/qualify-core.py \
  --archive /tmp/core.zip \
  --metadata /tmp/core.release.json \
  --record /tmp/core.qualification.json
```

The trusted driver verifies and extracts the supplied archive, then runs each
fixed scenario in a fresh process whose core imports resolve from that extracted
candidate. The fail-closed record is keyed to the archive, metadata, and a
driver digest covering the launcher plus the fixed trusted sensors and fixtures
it executes and the explicit Python module closure used to verify and extract
the archive. New workflow sensors and their independent schema consumer execute
from the extracted candidate with trusted-byte checks. It retains only named
assertions and deterministic checkpoint
summaries: no environment values or local paths. The gate rejects a missing,
duplicated, or unasserted required scenario so an empty run cannot pass. The
rollback checkpoint derives a disposable prior fixture; the candidate archive
named in the record remains the single release under qualification. That prior
fixture is built only from the verified candidate extraction, never from the
runner checkout corpus.
A release carries its records beside its archives, and nothing about a release is
checked in. Historical records retain their original bytes under
`qualifications/history/`, and historical product sets remain immutable under
`qualifications/product/`; see [product generations](docs/product-generation.md).

Qualify the optional Linear profile offline, from the archives alone. It reaches no
tracker and needs no credentials:

```bash
python3 scripts/qualify-linear.py \
  --core-archive /tmp/core.zip \
  --core-metadata /tmp/core.release.json \
  --core-record /tmp/core.qualification.json \
  --linear-archive /tmp/linear.zip \
  --linear-metadata /tmp/linear.release.json \
  --run-delivery-archive /tmp/run-delivery.zip \
  --run-delivery-metadata /tmp/run-delivery.release.json \
  --record /tmp/linear.qualification.json
```

The record binds the passing core qualification and the exact core, Linear and
`run-delivery` archive and metadata identities. Its scenarios are the core binding, Linear install and host discovery,
controlled degraded outcomes, and forbidden alternate transport.
Qualification also rebuilds the supplied Linear archive's core projection and
requires its archive and metadata bytes to match the separately qualified core, and
does the same for its `run-delivery` projection.
For product archives, every runtime component must be byte-identical across
profiles; only the descriptor's verified workflow-content binding differs.

The transport sensor inspects only the delta between
the supplied extracted Linear and `run-delivery` candidates, so it covers what the
Linear profile adds and not the forge adapters and views it builds on. Its tests prove rejection of
concrete direct endpoint, SDK, Node HTTP(S), and command transport actions while
allowing non-executable boundary terminology and unrelated extension code.
Authentication, unavailable, unsupported,
malformed, timeout, and ambiguous outcomes remain deterministic controlled
boundary checks because intentionally breaking a configured connector is unsafe.
Install and host-discovery checks run inside qualification-owned temporary home,
configuration, repository, Codex, and Claude roots, so they do not depend on or
mutate a runner's existing host installation.

These checks do not prove that a live connector works, and no release needs a live
tracker run. When the connector or Linear-facing skill text changes, run the
[on-demand connector check](docs/linear-connector-check.md), which gates nothing
and retains no attestation.

Qualify the installed delivery provider separately from any adopting
repository's delivery runner:

```bash
python3 scripts/qualify-provider.py \
  --archive /tmp/core.zip \
  --metadata /tmp/core.release.json \
  --record /tmp/provider.qualification.json
```

The qualifier verifies and freshly installs the supplied release into
qualification-owned repository and host roots, then starts the provider from
the active generation with `python -B -m agent_skills.provider`. It drives the
exact protocol through line-delimited JSON and independently inspects the
written manifest and reviewer artifacts. Its deterministic record covers
success, blocked, failed, cancelled, interrupted, duplicate, unsupported
version, reconciliation, anti-vacuity, completeness, and redaction behavior
while retaining no local paths or opaque host references.
Its `workflowGraphSha256` names the actual installed graph, and the installation
scenario compares that graph and both new skill bodies with the verified
candidate. A failed qualifier cannot leave an earlier success record at the
requested output path; missing, duplicate, or unasserted scenarios reject.

This offline proof establishes the behavior of the exact installed provider
bytes and their evidence output. Host and connector provenance remains the
responsibility of the protected external run that supplies host events; the
controlled host-event inputs used here do not independently prove external
calls.

## Getting a release

Each release is a GitHub release named by its version tag. It carries five product
archives with their `.release.json` files and four qualification records, and its
notes list each archive's sha256. With no tag named, `gh release download` takes the
latest release. Download one profile's pair into an empty directory:

```bash
gh release download --repo kwam1na/agent-skills --pattern 'run-delivery-*'
```

A Jira adopter downloads the `jira-*` pair the same way. Then verify each file's
build provenance, pinned to the release workflow as signer:

```bash
gh attestation verify run-delivery-*.zip --repo kwam1na/agent-skills --signer-workflow kwam1na/agent-skills/.github/workflows/release.yml
```

```bash
gh attestation verify run-delivery-*.release.json --repo kwam1na/agent-skills --signer-workflow kwam1na/agent-skills/.github/workflows/release.yml
```

Install the pair with `apply` below, which verifies the archive against its
`.release.json` again before it writes anything.

A repository can declare on its base that its deliveries run without the gate, in
`.agents/delivery-posture.json`. A runtime from 0.7.8 on honours it, and a repository
declaring nothing behaves as before.
[The product generation guide](docs/product-generation.md#declaring-a-delivery-posture) has
the file, what each case resolves to, and how to move off a local version.

Releases before the first version tag carry no attestation, so that check finds
nothing for them. Releases before the first version tag, which is what latest
resolves to until then, carry a 0.7.7 runtime without the installed-data protocol,
so an adopter whose installed runtime has `installed-data-proof/1` must not apply
them. A candidate is never latest, so latest stays on those releases until the
first final is published, even once a candidate exists. The version number cannot
tell these apart, because a later runtime can declare 0.7.7 too. Look in the
installed bytes instead, from the repository root:

```bash
grep -c 'installed-data-proof/1' .agent-skills/current/runtime/cli.mjs
```

Pushing an annotated version tag publishes a release from the tagged tree. A tag is
`vMAJOR.MINOR.PATCH`, with `-rc.N` for a candidate. The release workflow refuses a
tag that is not annotated, not merged to `main`, or not above every existing version
tag. It publishes only on CI's success for that exact commit and never replaces a
release that already exists. If anything fails, nothing is published. A candidate is
never latest, so download one by naming its tag. See
[product generations](docs/product-generation.md#releases).

The gate is the Ubuntu job and `cut-release` on the push to `main`. CI's macOS job
runs there too, to inform, and neither allows nor blocks a release. Before tagging,
whoever tags runs the full suite locally on macOS with Python 3.11, which the release
job cannot verify:

```bash
uv python install 3.11
python3 scripts/bootstrap-test-environment.py
.venv/bin/python scripts/run-tests.py
```

Windows is not validated: CI does not run it, and the fixes made for it are unproven.

## Repository lifecycle

Run the lifecycle from an extracted release archive. Read-only commands emit
canonical JSON:

```bash
python -m agent_skills.cli --root /path/to/repository status
python -m agent_skills.cli --root /path/to/repository plan \
  --archive /path/to/core.zip \
  --metadata /path/to/core.release.json
```

The installed launcher owns bounded descendant cleanup for catchable terminal
signals. Its exit status remains `128 + signal` after the launched process group
drains:

| Signal | POSIX launcher behavior |
| --- | --- |
| `SIGINT` | Forward to the process group, escalate to `SIGKILL` after the bounded drain, and return 130. |
| `SIGTERM` | Forward to the process group, escalate to `SIGKILL` after the bounded drain, and return 143. |
| `SIGHUP` | Forward and return 129 when normally inherited. Preserve an inherited `SIG_IGN` disposition for deliberate `nohup` execution. |
| `SIGQUIT` | Forward to the process group, escalate when needed, and return 131. |
| `SIGKILL` | Uncatchable; the launcher cannot run cleanup when the launcher itself receives it. |

Windows, which is not validated (see above), keeps its native `taskkill /T /F` tree
cleanup for `SIGINT` and
`SIGTERM`. If bounded POSIX or Windows cleanup cannot establish that the tree
drained, the CLI returns `product.cancellation_incomplete`. Its diagnostic lists
only PIDs still running through the direct child handle; when tree liveness
cannot be observed, it reports `unknown` rather than inferring survivors from a
cleanup exception.

On POSIX the launcher also stops descendants that left the process group, which
it knows by pid and start time. It finds them by ancestry, or by a per-launch
`AGENT_SKILLS_LAUNCH_<key>` variable in their environment. On macOS, which
discloses no environment for Apple system binaries such as `/bin/sh` and
`/bin/sleep`, it also uses three more signals:

- a fork watcher that sees each child as it is forked;
- an inherited lifeline pipe;
- the process group of a descendant that has exited.

None of these macOS signals applies on Linux. Cleanup never stops pid 0 or 1,
the launcher or any of its ancestors, another user's process on its own, or a
process started before the run. A candidate that breaks that rule is never
signalled, and the cancellation reports
`product.cancellation_incomplete` with `cleanup=escaped-descendants-refused`.

Accepted limits:

- The fork watcher is best effort. A descendant can escape and be orphaned
  between two observations, with no readable marker and no inherited lifeline,
  before the watcher sees its fork. Such a descendant is not found.
- On macOS the launcher tells processes apart by the kernel's unique id,
  which is never reused, and finds a descendant by its parent's unique id even
  after it is reparented. A pid reused by an unrelated process is therefore
  never taken for one of the run's. One fallback still relies on no pid being
  reused within the moment between a descendant's exit and the watcher seeing
  it: a job found only through the group of a descendant that has exited,
  because the process that forked the job was never seen. Where the unique id
  cannot be read, start times stand in, with an hour of slack for a clock
  stepped back, and the preview of cleanup targets reports
  `identity: start-time` instead of `unique-id`.

Mutation is an explicit repository-maintenance action. `apply` is the product
installer entry point: it installs into an absent repository, updates a current
generation, and returns a no-op for the same exact generation without requiring
`AGENT_SKILLS_CHECKOUT` or another producer checkout:

```bash
python3 -B /path/to/product.zip --root /path/to/repository --product apply \
  --archive /path/to/core.zip \
  --metadata /path/to/core.release.json \
  --maintenance
```

`install` and `update` remain compatible explicit lifecycle operations. New
adopter wiring should delegate to `apply` instead of selecting between them.

After installation or adoption completes, start a fresh agent session so Codex
or Claude Code discovers the new repository-relative skill links. An already
running session is not expected to refresh its discovered skills dynamically.

Rollback and removal have different finish lines. Rollback restores a retained
prior generation (or an adopted predecessor). A first installation has no prior
generation, so rollback fails with guidance to use removal instead:

```bash
python -m agent_skills.cli --root /path/to/repository rollback --maintenance
```

To return a first installation to `absent`, run removal. The installed
generation contains the lifecycle code, so this command does not require the
original checkout, release archive, or network access when run from the
repository root:

```bash
PYTHONPATH=.agent-skills/current python3 -B -m agent_skills.cli \
  --root . remove --maintenance
```

In PowerShell, run the same installed lifecycle with:

```powershell
$env:PYTHONPATH = ".agent-skills/current"
python -B -m agent_skills.cli --root . remove --maintenance
```

Removal deletes only managed projections and lifecycle-created empty discovery
directories. It preserves discovery directories that predated installation and
all unrelated tracked or untracked bytes.

An absent managed installation may adopt one existing Codex workflow without
exposing the rest of the profile. The maintainer supplies the expected SHA-256
of its current `SKILL.md`; Claude Code must have no same-name workflow or the
same exact bytes:

```bash
python -m agent_skills.cli --root /path/to/repository adopt \
  --archive /path/to/core.zip \
  --metadata /path/to/core.release.json \
  --skill compound-delivery-kernel \
  --expected-prior-sha256 <digest> \
  --maintenance
```

Adoption still authenticates and retains the complete exact release. Its
receipt narrows discovery to the named workflow and owns a verified predecessor
copy. Immediate offline rollback restores each pre-existing host body and
removes a host projection that adoption created. This first-adoption seam does
not add partial profile installation, batch selection, or a second lifecycle.
The selected workflow must declare no required or routing dependencies; the
command rejects dependency-bearing workflows rather than exposing an incomplete
selection or expanding it automatically.

The tracked `.agent-skills/active.json` file is both the ownership receipt and
the active-generation marker. It records the fixed `.agents/skills` and
`.claude/skills` projections separately from workflow behavior. Content-addressed
generations retain the current and immediately prior verified releases. A
private journal under Git metadata makes interruption recoverable without
touching unrelated tracked or untracked bytes. Rollback uses the retained prior
generation and never fetches its source.

See `docs/host-adapters.md` for the projection contract and conflict behavior.

## Delivery observations

The portable execution and review workflows share a
[run observation contract](skills/execute-work/references/run-observation-contract.md).
It covers actual activity, known waits and selected structured report capture
through a compatible harness runtime. Both hosts use the same contract and
preserve caller-supplied review accounting. Missing capabilities, signals or
output remain explicit; observations do not grant delivery authority. See
[host projections](docs/host-adapters.md) and
[product generations](docs/product-generation.md) for installed use.
