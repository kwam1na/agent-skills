# Preserve source time during merge recovery

A queue can say merged while its append-only journal has no merge event. This
can happen when an external merge is recorded as a state patch, or a crash
separates state and journal writes. Reconciliation must inspect both records.

Recovery observes an old event now. Keep the journal's `at` as append time and
carry the verified forge `mergedAt` as `occurredAt`. Explicit null means that
recovery has no source time; absence retains the old journal and normal runner
semantics. Rewriting append time would hide when recovery actually happened.

An idempotent recovery uses the queue lock and durable recovery intent, preserves
review events, and checks for a merge after the latest hand-off before appending.
Retain the recovered head and change-request identity too: hand-off state and
its journal append can straddle recovery, so journal order alone cannot tell a
delayed same-attempt hand-off from new work. Reject conflicting retained heads
and future source occurrences before writing an intent or changing state.
Display landing times and compute durations from occurrence. If a hand-off was
recorded after the actual merge, its wait and check duration are unknown, not
zero or the delay until recovery. Sort the last known landing by occurrence,
since an older merge can be observed after a newer one.

A snapshot limited to one repository cannot discover a merge in another.
Supply a fresh, verified source snapshot for that merge through reconciliation;
do not invent run history or edit the live journal. Source aggregation remains
separate from timestamp recovery.

A later pull request for the same subject must not relabel a merged candidate.
Reconciliation reports a different retained change request before linking or
recovering it; the lead settles candidate identity through supported state operations.
