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

The `*-delivery-views-v1` files retain the previously qualified delivery-views
release set, including its independently bound protected Linear attestation.
Their bytes and identities remain immutable.

The `*-review-evidence-v1` files retain the previously qualified review-evidence
release set, including its independently bound protected Linear attestation.
Their bytes and identities remain immutable.

The `*-run-delivery-v1` files retain the previously qualified run-delivery
release set, including its independently bound protected Linear attestation.
Their bytes and identities remain immutable.

The `*-jira-tracker-v1` files retain the previously qualified jira-tracker
release set, including its independently bound protected Linear attestation.
Their bytes and identities remain immutable.

The `*-plan-review-v1` files retain the previously qualified plan-review
release set, including its independently bound protected Linear attestation.
Their bytes and identities remain immutable.

The `*-capture-observations-v1` files are the current product set. The bootstrap archive
qualifies the minimal first-install profile and full product lifecycle. The core
archive carries its exact current core and provider qualification records. The
Linear qualification binds that exact core and Linear pair to a new
independently frozen collector input and protected host-native attestation. The `run-delivery` archive is the
product an adopter installs to run deliveries, and the `jira` archive is the
product a Jira adopter installs; like the delivery-views profile before them,
neither carries a qualification record of its own. Every earlier product
artifact above remains unchanged and its attestation is never rebound or
relabeled.
