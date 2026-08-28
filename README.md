# Agent Skills Corpus

This repository is the deterministic, metadata-first home for a small portable
agent-skills corpus. It does not install skills or extract new workflow bodies.

`catalog.json` lists every shipped skill. `profiles/` declares selectable
workflow sets, and `provenance.lock.json` binds every shipped file to its
approved Athena baseline source, revision, license status, modification status,
and SHA-256 digest.

Validate the checked-in registry with Python 3.11 or newer:

```bash
python3 scripts/validate-registry.py
python3 tests/test_registry.py
```

The default `core` profile is tracker-neutral. Linear and bootstrap guidance
are opt-in profiles; Athena overlays are classification inputs only and are not
shipped by this corpus.
