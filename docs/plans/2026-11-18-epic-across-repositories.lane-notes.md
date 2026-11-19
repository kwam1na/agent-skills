# Lane notes: one epic across repositories

These are notes for the lanes that the plan's review left open. The review aligned at round 4, and its record is
`2026-11-18-epic-across-repositories.review.json`. The lenses raised none of these as findings. Each is worth a test
row in the story it names.

## The integration base

This delivery runs on `integrate/epic-across-repos`, not `main`. Branch from it, merge it before you hand off, and
point every change request at it. Run `run_delivery.py` from the lead checkout at
`.orchestration/lanes/V26-3602`, whose binding names this base. `main`'s binding does not.

## V26-3603

- **A stale sibling snapshot doesn't block tracker and forge.** If the freshness gate refuses a sibling snapshot
  for age, tracker and forge snapshots passed in the same `reconcile` call still apply (Decision 3). Pass a fresh
  forge snapshot alongside a stale sibling snapshot, and assert that the forge snapshot applied and the sibling
  reads `lastOutcome: stale`. A whole-call raise, as a stale tracker snapshot gets today, would leave scenario 9(b)
  green.
- **A terminal cut needs no newer pointer.** Decision 3's terminal test also requires that `current` names no newer
  pointer. Scenario 10 pins that, so keep the case when you build the fixture.

## V26-3072

- **A re-parented sibling.** Decision 10 counts a sibling's last kept read even after its `lastOutcome` turns
  `other-epic`. That needs a sibling delivery to be re-parented mid-delivery, and its stories still draw in that
  sibling's group. Leave it as it is unless a test shows a wrong outcome.

## V26-3604

- **A wait on a key outside the record.** A `sibling` wait's `on` is a key the record doesn't hold. Check that
  `waited_on`, the line map's lookup of the thing a wait names, and the Markdown view don't fail when `on` isn't
  among the record's stories. Draw it as text, with no link, or with the sibling's link when one is known.
