# Compatibility

The registry declares Codex and Claude Code as supported discovery hosts for
each current workflow body. Host metadata under `agents/` is cataloged as a
file in the same provenance record as its skill body; it does not create a
second workflow source.

This v1 corpus qualifies structure offline and can build one authenticated,
core-only release archive. It does not install skills, expose them to either
host, negotiate host features, run a compatibility engine, or claim cross-host
behavioral qualification.

## Provenance migration

The live registry uses `agent-skills-provenance/2` with per-entry origins. Its
schema and validator retain explicit version-1 compatibility for immutable
archives: version 1 keeps the original approved Athena revision and cannot carry
version-2 origin fields. Version 2 accepts different full immutable commits per
entry and explicit independently authored origins. A version change does not
grant source-use or implementation authority.

The migration preserves every existing source revision, path, digest,
classification, and output digest. It adds repository identity and origin kind
to existing entries, while retaining `sourceBaseline` solely as historical
classification evidence. Retained archives and their detached metadata are not
rewritten or relabeled. Their original manifest, payload, schema, and output
digests still verify; extraction uses the corresponding versioned provenance
rules. Existing exact release identities remain distinct from newly built
version-2 candidates, which require fresh qualification evidence.
