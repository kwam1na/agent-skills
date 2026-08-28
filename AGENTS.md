# Agent Skills Corpus Guide

Keep this repository metadata-first while the workflow bodies are being
classified. Do not add installers, generators, release archives, compatibility
engines, or repository-specific overlays without a separately approved unit.

When a releasable skill changes, update its catalog entry, every selected
profile, and the corresponding provenance output digest in the same change.
Keep JSON serialized as two-space-indented UTF-8 with a final newline, then run:

```bash
python3 scripts/validate-registry.py
python3 tests/test_registry.py
```

The approved source baseline is Athena commit
`342850073464a2c9b6198d32d11475f5fa5cbe53`. It is evidence for classification
and source provenance, not permission to copy Athena policy into this corpus.
