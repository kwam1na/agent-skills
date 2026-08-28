# Agent Skills Corpus

This repository is the deterministic, metadata-first home for a small portable
agent-skills corpus. It does not install skills or extract new workflow bodies.

`catalog.json` lists every cataloged skill. `profiles/` declares selectable
workflow sets, and `provenance.lock.json` binds every cataloged file to its
approved Athena baseline source, revision, license status, modification status,
and SHA-256 digest.

Validate the checked-in registry with Python 3.11 or newer:

```bash
python3 scripts/validate-registry.py
python3 tests/test_registry.py
```

The default `core` profile is tracker-neutral. Bootstrap guidance is opt-in;
the Linear profile is reserved for U8. Cataloged source-only inventory is not selectable, and
Athena overlays are classification inputs only rather than distributable skills.
