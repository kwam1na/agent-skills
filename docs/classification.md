# Classification boundary

The approved portable-workflow baseline is the source of classification evidence,
not a corpus to copy wholesale. `provenance.lock.json` records the source path,
immutable revision, output classification, modification status, output file
digest, licensing status, and owner for every cataloged file.

Classification and distribution status live only in `provenance.lock.json`.
The default profile contains only releasable portable-core entries. Bootstrap
is optional guidance. The Linear profile adds only the narrow host-native
tracker adapter to the complete core profile. Athena-derived source-only
inventory remains cataloged for traceability and is not selected by that
adapter profile. Athena-specific merge gates,
telemetry, deployment, reporting, generated-artifact policy, and domain rules
are deliberately absent from all selectable profile closures.
