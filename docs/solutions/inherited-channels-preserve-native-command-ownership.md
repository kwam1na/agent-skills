# Borrow explicit channels without changing native command ownership

A repository may keep pure analysis in its own child while each product command
still starts a fresh native runtime. Passing a result-file path would create a
new authority boundary; retaining the native CLI would retain imported config
state. Neither is necessary for a directly owned analysis process.

The launcher's `harness --capabilities` response advertises POSIX descriptor
forwarding. `harness --pass-fd N` borrows an open pipe or socket above stderr. It
rejects stdio, regular files, duplicates, invalid descriptors, and more than eight
channels. Windows reports unsupported. Capability discovery performs no config
evaluation or admission.

Every actual command still takes the lifecycle shared lock, verifies the current
runtime, reconciles policy, and starts the ordinary Node entry point. The
launcher does not read the extra descriptors or interpret their data. It closes
only its own cancellation lifeline; the caller retains ownership of borrowed
channels. Native stdio and process-group cancellation retain their existing
meaning.

Focused tests exercise real repeated child launches, nonzero exit propagation,
explicit inheritance, refusal of file/stdin channels, fresh readiness checks, and
cancellation that reaps the native child while leaving the caller's channel open.
This is a transport capability, not permission to treat an imported plan as
evidence or to reuse a previous admission.
