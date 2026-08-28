# Agent Skills Corpus

This repository is the deterministic, metadata-first home for a small portable
agent-skills corpus. It does not install skills or implement provider-specific
adapters.

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
router, and neutral tracker capability contract. Bootstrap guidance is opt-in;
the Linear profile remains empty until the optional Linear adapter is extracted.
Cataloged source-only inventory is not selectable, and Athena overlays are
classification inputs only rather than distributable skills.
