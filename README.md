# Agent Skills Corpus

This repository is the deterministic, metadata-first home for a small portable
agent-skills corpus. Its lifecycle installs only authenticated profile-bound
release archives; it does not extract new workflow bodies or implement direct
provider service clients. One installed generation is exposed to Codex and
Claude Code through stable repository-relative links and one shared
active-generation link, so neither host owns another editable workflow copy or
switches independently.

`catalog.json` lists every cataloged skill. `profiles/` declares selectable
workflow sets, and `provenance.lock.json` binds every cataloged file to its
approved Athena baseline source, revision, license status, modification status,
and SHA-256 digest.

Validate the checked-in registry and hostile fixture suite with Python 3.11 or
newer:

```bash
python3 scripts/validate-registry.py
python3 tests/test_registry.py
python3 -m unittest discover -s tests -p 'test_*.py'
```

Build and verify the core profile:

```bash
python3 scripts/build-release.py build --archive /tmp/core.zip --metadata /tmp/core.release.json --release-id core-v1
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
  --release-id linear-v1 \
  --profile linear
python3 scripts/build-release.py verify \
  --archive /tmp/linear.zip \
  --metadata /tmp/linear.release.json \
  --extract-to /tmp/linear-standalone
```

The detached release metadata authenticates the ZIP before it is parsed. The
manifest then authenticates the complete payload and its single profile group.
Verification is bounded by fixed entry, file-size, expanded-size, path-depth,
compression-ratio, and elapsed-time limits. The archive contains the validator,
catalog, selected profile closure, provenance, schemas, inventory, and selected
skills. Core excludes optional profile bodies; Linear adds only its adapter to
the complete core closure.

The default `core` profile contains the portable delivery posture, normalized
router, neutral tracker capability contract, bounded planning and execution
workflows, bounded multi-lens review convergence, and a caller-owned compounding
decision. Bootstrap guidance is opt-in. The optional Linear profile adds a
host-native MCP adapter without changing core behavior when tracking is
unavailable. Cataloged source-only inventory is not selectable, and Athena
overlays are classification inputs only rather than distributable skills.

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
the archive. It retains only named assertions and deterministic checkpoint
summaries: no environment values or local paths. The gate rejects a missing,
duplicated, or unasserted required scenario so an empty run cannot pass. The
rollback checkpoint derives a disposable prior fixture; the candidate archive
named in the record remains the single release under qualification. That prior
fixture is built only from the verified candidate extraction, never from the
runner checkout corpus.
The current record is checked in at `qualifications/core-v1.json`.

Qualify the optional Linear profile only after a configured host has exercised
the live connector with clearly disposable work. The host must search for an
exact randomly generated operation marker before each create, retain one
canonical redacted host attestation, and cancel every disposable entity after
the create, read, update, search, relation, and reconciliation checks. Post the
attestation digest and its random receipt reference through the same host-native
Linear MCP boundary, then feed the retained attestation to the trusted driver
together with the exact core and Linear releases:

```bash
python3 scripts/qualify-linear.py \
  --core-archive /tmp/core.zip \
  --core-metadata /tmp/core.release.json \
  --core-record qualifications/core-v1.json \
  --linear-archive /tmp/linear.zip \
  --linear-metadata /tmp/linear.release.json \
  --attestation qualifications/linear-v1.attestation.json \
  --record /tmp/linear.qualification.json
```

The checked record at `qualifications/linear-v1.json` binds the passing core
qualification, both exact archive and metadata identities, canonical extracted
source-tree digests, the exact retained attestation bytes, and its retained
external receipt reference. The transport sensor inspects only the delta between
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
artifacts, then deterministically replay the redacted event causality; they do
not independently prove external calls. The retained attestation and record
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
`qualifications/provider-v1.json`.

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

Mutation is an explicit repository-maintenance action:

```bash
python -m agent_skills.cli --root /path/to/repository install \
  --archive /path/to/core.zip \
  --metadata /path/to/core.release.json \
  --maintenance
```

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
