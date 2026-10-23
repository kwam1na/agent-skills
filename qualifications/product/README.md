# Product qualification artifacts

The `*-product-v1` files retain the exact historical core and Linear releases,
offline qualification records, collector binding, and protected host
attestation collected for that release pair. Their recorded identities and
bindings remain independently verifiable together. They do not claim current
source equivalence or qualify a later product.

The `*-adoption-v1` files retain the previously qualified adoption release set,
including its independently bound protected Linear attestation. Their bytes and
identities remain immutable.

The `*-bytecode-v1` files retain the previously qualified bytecode-recovery
release set, including its independently bound protected Linear attestation.
Their bytes and identities remain immutable.

The `*-diagnostics-v1` files retain the previously qualified diagnostics release
set, including its independently bound protected Linear attestation. Their bytes
and identities remain immutable.

The `*-text-coherence-v1` files retain the previously qualified text-coherence
release set, including its independently bound protected Linear attestation.
Their bytes and identities remain immutable.

The `*-concurrent-delivery-v1` and `*-orchestration-v1` files retain the previously qualified
concurrent-delivery and orchestration release sets, each with its independently bound protected Linear
attestation. Their bytes and identities remain immutable.

The `*-delivery-views-v1` files are the current qualified product set. The
bootstrap archive qualifies the minimal first-install profile and full product lifecycle.
The core archive carries its exact current core and provider qualification
records. The Linear qualification binds that exact core and Linear pair to a
new independently frozen collector input and protected host-native attestation.
Every earlier product artifact above remains unchanged and its attestation is
never rebound or relabeled.
