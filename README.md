# Agent Skills Corpus

This repository is the deterministic, metadata-first home for a small portable
agent-skills corpus. Its lifecycle installs only authenticated release archives;
it does not extract new workflow bodies or implement provider-specific service
adapters. One installed generation is exposed to Codex and Claude Code through
stable repository-relative links and one shared active-generation link, so
neither host owns another editable workflow copy or switches independently.

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

Build and verify the one supported core-only release format:

```bash
python3 scripts/build-release.py build --archive /tmp/core.zip --metadata /tmp/core.release.json --release-id core-v1
python3 scripts/build-release.py verify \
  --archive /tmp/core.zip \
  --metadata /tmp/core.release.json \
  --extract-to /tmp/core-standalone
```

The detached release metadata authenticates the ZIP before it is parsed. The
manifest then authenticates the complete payload and its single `core` group.
Verification is bounded by fixed entry, file-size, expanded-size, path-depth,
compression-ratio, and elapsed-time limits. The archive contains the validator,
catalog, core profile, provenance, schemas, inventory, and selected core skills;
it excludes source-only and optional profile bodies.

The default `core` profile contains the portable delivery posture, normalized
router, neutral tracker capability contract, bounded planning and execution
workflows, bounded multi-lens review convergence, and a caller-owned compounding
decision. Bootstrap guidance is opt-in; the Linear profile remains empty until
the optional Linear adapter is extracted. Cataloged source-only inventory is not
selectable, and Athena overlays are classification inputs only rather than
distributable skills.

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
it executes. It retains only named assertions and deterministic checkpoint
summaries: no environment values or local paths. The gate rejects a missing,
duplicated, or unasserted required scenario so an empty run cannot pass. The
rollback checkpoint derives a disposable prior fixture; the candidate archive
named in the record remains the single release under qualification. That prior
fixture is built only from the verified candidate extraction, never from the
runner checkout corpus.
The current record is checked in at `qualifications/core-v1.json`.

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
python -m agent_skills.cli --root /path/to/repository rollback --maintenance
```

The tracked `.agent-skills/active.json` file is both the ownership receipt and
the active-generation marker. It records the fixed `.agents/skills` and
`.claude/skills` projections separately from workflow behavior. Content-addressed
generations retain the current and immediately prior verified releases. A
private journal under Git metadata makes interruption recoverable without
touching unrelated tracked or untracked bytes. Rollback uses the retained prior
generation and never fetches its source.

See `docs/host-adapters.md` for the projection contract and conflict behavior.
