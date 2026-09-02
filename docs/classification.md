# Classification boundary

The approved portable-workflow baseline is the source of classification evidence,
not a corpus to copy wholesale. `provenance.lock.json` records the source path,
immutable revision, output classification, modification status, output file
digest, licensing status, and owner for every cataloged file.

Provenance version 2 records an origin independently for each entry. A
`repository-source` names a repository, full immutable commit, relative source
path, and source SHA-256. Existing `baselineSourceId` values remain historical
cross-references; the unchanged `sourceBaseline` does not constrain new origins
or mean all entries derive from that revision. A directory source path in a
legacy entry retains its original meaning: its source digest identifies the
workflow body, not a newly invented directory-tree digest.

An `independent-origin` records the rationale and an `ownerApproval` with the
same owner as the entry plus an evidence reference. It uses modification status
`independently-authored` and cannot carry repository-source fields. Every origin
kind retains exact path/SHA-256 output records. Approval metadata is a declaration
of accountable ownership, not an authenticated approval system: the delivery
owner must retain the actual delegated or maintainer authority behind the
reference. Registry validation verifies shape and consistency, not remote Git
objects, licensing permission, or external approval authenticity.

New origins must be approved independently. A new repository revision or
independently authored workflow never rewrites another entry's origin. Source
inventory and approval evidence belong in the delivery record, not in the
portable skill body. `source-only` remains a distribution classification for
retained inventory, not a third way to bypass origin validation.

Classification and distribution status live only in `provenance.lock.json`.
The default profile contains only releasable portable-core entries. Bootstrap
is optional guidance. The Linear profile adds the narrow host-native tracker
adapter, plus the decomposition and tracked-execution workflows that reach the
tracker only through that adapter, to the complete core profile. A
`source-only` entry, whenever one is retained, stays cataloged for
traceability and is never selected by a profile. Athena-specific merge gates,
telemetry, deployment, reporting, generated-artifact policy, and domain rules
are deliberately absent from all selectable profile closures.
