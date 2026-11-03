# Amendment 1: how the delivery ran, and what it added

The reviewed plan stays byte for byte as its review evidence bound it. This amendment records where the delivery
departed from it, and overrides it where they differ.

- **Runner.** The delivery ran on `merge_queue.py serve` against a delivery base, with `.agents/orchestration.json` and
  `.agents/delivery-views.json` on that base and a lane brief beside the plan. All three name this machine's paths and
  the delivery base, so the integration change request removes them, and the default branch gains no binding. A
  standing binding is V26-3050's.
- **The CI finish line.** "CI is green on all three operating systems" reads as the default branch's release gate as
  the tag-driven releases delivery left it: the Ubuntu job and `cut-release` gate, macOS informs, and Windows is
  deferred.
- **Added: "Needs you" in the line map.** C1 says "The line map shows the same list", but no unit owned it. It was
  delivered as its own unit: the supervisor writes the derived list after the publishers and before the renderers,
  and the line map's "Now" chapter shows it, holding items first, and says when it is unavailable.
- **The forge adapter's merge.** The Lead supervisor unit's outcome has the guide show a binding whose `merge` argv
  calls the forge adapter. The binding has no change-request placeholder, so both adapters gained
  `merge --source BRANCH --head SHA`, pinned to the head and refusing no pull request, several, or a moved head.
  `merge --url` is unchanged.
- **C5's host drain.** The orchestration guide's host drain skips every `lane.assigned` start that `lane-start`
  posts, rather than only a start `lane-start` has recorded, so the start is posted once in the usual order.
- **Shared catalog text.** Every unit appended a sentence to the same `degradedBehavior` strings in `catalog.json`,
  so each later candidate conflicted there and was brought up to date with the base by its lane before merging.
- **Release.** The changes add to the record's shape (`planned[].trackerStarted`, `bases`, the supervisor's
  `needs-you.json`) and require adopters to act (`lead-reconcile --every` is replaced by `lead-run`, `lead-setup`
  requires the lanes' authority grant, and `lead-init` refuses in new cases). The first release is therefore the
  candidate `v2.0.0-rc.1`, applied in one adopter before `v2.0.0` is tagged on the same commit.
- **Follow-ups.** Findings left at review bounds and gaps found while delivering are filed as their own items, related
  to the epic: the queue's candidate step and base rerun, regenerable-path conflicts, intermittent merge-queue tests,
  review evidence without run-delivery, and the review-bound test and note gaps.
