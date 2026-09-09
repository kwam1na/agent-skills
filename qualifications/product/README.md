# Product qualification artifacts

The `*-product-v1` files retain the exact historical core and Linear releases,
offline qualification records, collector binding, and protected host
attestation collected for that release pair. Their recorded identities and
bindings remain independently verifiable together. They do not claim current
source equivalence or qualify a later product.

The `*-product-contract-alignment-v1` files are the current local product set.
The core archive carries its exact current core and provider qualification
records. The Linear archive is locally release-verified and checked against the
current source projection, but has no newly collected protected host
attestation. A fresh protected collection must bind that exact core and Linear
release pair before the current Linear product can claim external MCP
qualification. The historical attestation is never rebound or relabeled.
