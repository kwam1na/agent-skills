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
module that sets `RUN_SERIALLY = True`, runs with nothing beside it.
`--timings <file>` records per-test times, and `--timings-from <file>` starts
the slowest work first:

```bash
.venv/bin/python scripts/run-tests.py --timings .test-timings.json
```

On Windows, use `.venv\Scripts\python.exe` for the sensor commands.

Build and verify the core profile:

```bash
python3 scripts/build-release.py build --archive /tmp/core.zip --metadata /tmp/core.release.json --release-id core-workflows-concurrent-delivery-v1
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
  --release-id linear-workflows-v3 \
  --profile linear
python3 scripts/build-release.py verify \
  --archive /tmp/linear.zip \
  --metadata /tmp/linear.release.json \
  --extract-to /tmp/linear-standalone
```

Build and qualify the bootstrap profile independently. Its qualifier installs
the exact archive in a fresh Git repository, verifies both host exposures and
standalone corpus validation, updates through the same command, removes its
source artifacts, and rolls back through installed bytes:

```bash
python3 scripts/build-release.py build \
  --archive /tmp/bootstrap.zip \
  --metadata /tmp/bootstrap.release.json \
  --release-id bootstrap-workflows-concurrent-delivery-v1 \
  --profile bootstrap
python3 scripts/qualify-bootstrap.py \
  --archive /tmp/bootstrap.zip \
  --metadata /tmp/bootstrap.release.json \
  --record /tmp/bootstrap.qualification.json
```

The current checked qualification record is
`qualifications/bootstrap-workflows-concurrent-delivery-v1.json`.

The detached release metadata authenticates the ZIP before it is parsed. The
manifest then authenticates the complete payload and its single profile group.
Verification is bounded by fixed entry, file-size, expanded-size, path-depth,
compression-ratio, and elapsed-time limits. The archive contains the validator,
catalog, selected profile closure, provenance, schemas, inventory, and selected
skills. Core excludes optional profile bodies; Linear adds its adapter and the two
tracking workflows that call the adapter to the complete core closure.

The default `core` profile contains the portable delivery posture, normalized
router, neutral tracker capability contract, bounded planning and execution
workflows, evidence-first diagnosis, exact caller-selected review acquisition,
bounded multi-lens review convergence, and a caller-owned compounding decision.
Bootstrap guidance is opt-in. The optional Linear profile adds a
host-native MCP adapter plus the decomposition and tracked-execution workflows
that reach the tracker only through it, without changing core behavior when
tracking is unavailable. A cataloged source-only entry is never selectable, and
Athena overlays remain classification inputs: what ships is a narrowed
derivative, never copied repository policy.

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
The current workflow-only record is checked in at
`qualifications/core-workflows-concurrent-delivery-v1.json`.
Historical records retain their original bytes under `qualifications/history/`.
Historical product records such as `linear-product-v1` also remain immutable
under `qualifications/product/`.
The exact runnable product pair and its new qualifications live under
`qualifications/product/`; see [product generations](docs/product-generation.md).

Qualify the optional Linear profile only after an authorized maintainer's
configured host has exercised the live connector with clearly disposable work.
Before any live operation, build and verify both archives, then independently
freeze a canonical collector input. Never derive this expected input from the
attestation it will later validate.

The collector input is `agent-skills-linear-collector-binding/1`:

| Field | Required value |
| --- | --- |
| `schemaVersion` | `agent-skills-linear-collector-binding/1` |
| `releases` | Exactly `core` and `linear`, each with `releaseId`, `profile`, `archiveSha256`, and `metadataSha256` from independent archive verification |
| `graphSha256` | SHA-256 of the exact `workflows/delivery-v1.json` bytes, identical in both extracted archives |
| `subjectRef` | `workflow-subject-ref/1` with a nonempty opaque qualification identity |
| `candidateRef` | `workflow-candidate-ref/1` with a nonempty opaque candidate identity |

Each reference is an object with `schemaVersion` and `opaque`. Use fresh redacted
opaque identities, not ticket identifiers, paths, or host payloads. Serialize the
collector input and attestation with sorted keys, two-space indentation, and a
final newline. The current checked input is
`qualifications/product/linear-concurrent-delivery-v1.collector-binding.json`; it is not
part of either archive.
Qualification also rebuilds the supplied Linear archive's core projection and
requires its archive and metadata bytes to match the separately qualified core.
For product archives, every runtime component must be byte-identical across
profiles; only the descriptor's verified workflow-content binding differs.

The host searches for an exact random operation marker before each create,
retains the redacted live event sequence, and cancels both disposable entities
after create, read, update, search, relation, and reconciliation checks. The
`agent-skills-linear-host-attestation/2` document includes `collectorBinding`
equal to the frozen input, plus the existing collector, audit, entity, event,
session, and receipt fields. `auditReferences` uses the same order as `entities`:
primary first, then dependency. Post its digest and random receipt reference through
the same host-native Linear MCP boundary. Then pass the retained attestation and
the independently frozen input to the offline driver:

```bash
python3 scripts/qualify-linear.py \
  --core-archive qualifications/product/core-concurrent-delivery-v1.zip \
  --core-metadata qualifications/product/core-concurrent-delivery-v1.release.json \
  --core-record qualifications/product/core-concurrent-delivery-v1.json \
  --linear-archive qualifications/product/linear-concurrent-delivery-v1.zip \
  --linear-metadata qualifications/product/linear-concurrent-delivery-v1.release.json \
  --collector-binding qualifications/product/linear-concurrent-delivery-v1.collector-binding.json \
  --attestation qualifications/product/linear-concurrent-delivery-v1.attestation.json \
  --record /tmp/linear.qualification.json
```

The version-2 checked record at `qualifications/product/linear-concurrent-delivery-v1.json` binds the passing core
qualification, both exact archive and metadata identities, canonical extracted
source-tree digests, the frozen collector input and its digest, the exact retained
attestation bytes, and its external receipt reference. Old unbound attestations,
any candidate or graph mismatch, and unavailable protected collection block a
new qualification; none may be copied forward as fresh evidence. The transport
sensor inspects only the delta between
the supplied extracted Linear and core candidates. Its tests prove rejection of
concrete direct endpoint, SDK, Node HTTP(S), and command transport actions while
allowing non-executable boundary terminology and unrelated extension code.
Authentication, unavailable, unsupported,
malformed, timeout, and ambiguous outcomes remain deterministic controlled
boundary checks because intentionally breaking a configured connector is unsafe.
Install and host-discovery checks run inside qualification-owned temporary home,
configuration, repository, Codex, and Claude roots, so they do not depend on or
mutate a runner's existing host installation.

The protected host-native run owns the external MCP provenance. Offline checks
bind the retained attestation bytes to its receipt reference and exact candidate
artifacts and independently frozen input, then deterministically replay the
redacted event causality; they do not independently prove external calls or
collector authorization. The retained attestation and record
contain no host payloads, ticket identifiers, secrets, or local paths.

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
while retaining no local paths or opaque host references. The checked record is
`qualifications/provider-workflows-concurrent-delivery-v1.json`.
Its `workflowGraphSha256` names the actual installed graph, and the installation
scenario compares that graph and both new skill bodies with the verified
candidate. A failed qualifier cannot leave an earlier success record at the
requested output path; missing, duplicate, or unasserted scenarios reject.

This offline proof establishes the behavior of the exact installed provider
bytes and their evidence output. Host and connector provenance remains the
responsibility of the protected external run that supplies host events; the
controlled host-event inputs used here do not independently prove external
calls.

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

Windows keeps its native `taskkill /T /F` tree cleanup for `SIGINT` and
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
