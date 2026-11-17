## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**Authority.** Lanes may push their story branches and open draft pull requests into `integrate/delivery-story`, as the delivery's
grant states. No lane merges, marks a pull request ready, tags, or applies a release. The operator is away; the lead holds the
operator's authority for the calls that land this delivery, so ask the lead, not the operator.

**The base is `integrate/delivery-story`, not `main`.** Branch from it, open your pull request into it, and never merge it or `main`
into your story branch unless the lead authorizes the exact commits. One merge is authorized here: V26-3531 merges the branch
`claude/live-view-ui` at `4e66a057` into its story branch, as its plan section says.

**Files.** No file is shared between lanes. Each lane's files are in the plan's ownership table, and each new file's exact path is
there too. Create no other file outside a folder you own without asking the lead for its path first. Lane 1 gains the other lanes'
files only for V26-3538, when every other story has merged.

**The bridge.** By Decision 25, until V26-3538 a writer you move to facts still passes its sentence as well, and a reader you move
reads the fields when the entry has `data` and the sentence otherwise. Do not remove a sentence or a second path before V26-3538.

**The registry is lane 1's.** If your story needs a kind, a field, a link or a word the registry does not have, or finds the plan's
table wrong, stop and ask the lead; do not work around it. The lead decides and has lane 1 make the change.

**Design.** V26-3531 changes no design: it lands the rework as committed. V26-3537 follows the plan's Design direction: subtle, and
nothing without a reason. When a choice of look or wording is open, make the quieter one and name it in your hand-off.

**Provenance, catalog and profiles.** When you change a file under `skills/`, refresh its digest with
`.venv/bin/python scripts/update-provenance.py` and commit `provenance.lock.json` with the change. A story that adds a shipped file adds
its entry to the lock by hand, then runs the writer. Do not edit `catalog.json`, `profiles/` or `.agents/orchestration.json`: the lead
writes that wording once, at integration.

**Proofs.** A row that asserts new behaviour is proven red against the code before its change. A row that asserts behaviour kept is
proven red against an over-correcting mutant of the new code, then the mutant is reverted. A proof that mutates a file
`provenance.lock.json` covers refreshes that file's digest with the mutation, so the build succeeds and the row's own assertion is what
fails. Name each proof in your hand-off.

**Tests and the suite's budget.** Run your story's test modules with `.venv/bin/python scripts/run-tests.py -p <module>`, and never the
full suite. Add no fixture that starts a process: assert a new field in a case that already produces its entry. A new test class that
keeps to its own folders, ports and processes is `run_quietly`, never `run_serially`. The suite has 38 seconds of headroom in
`tests/suite-budget.json`, and nobody in this delivery may raise a number in it. If your story's tests cost time, make other tests in
your files cheaper in the same story and say so in your hand-off. Name the catalogue ids you ran with `handoff --checks`.

**Review.** Four rounds and one grace round at most. Past that, stop and tell the lead.

**Criteria come from the tracker items**, and the plan's scenario rows bind beside them. Where a tracker item's wording and a plan
Decision differ, the Decision is the newer ruling: say so in your hand-off, naming both.

**Processes.** Stop only processes you started, by the pid you recorded, and never by pattern.

**The installed generation is not your source.** `.agent-skills/current` is `linear-v2.4.0`, which delivers this work and does not
contain it. Edit under `skills/`, never under `.agent-skills/`. This delivery's own record stays in the old shape throughout: every new
behaviour is proven in fixtures.
