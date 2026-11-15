## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**Authority.** Lanes may push their story branches and open draft pull requests into `integrate/shared-base`, as the delivery's grant
states. No lane merges, marks a pull request ready, tags, or applies a release.

**The base is `integrate/shared-base`, not `main`.** Branch from it, open your pull request into it, and never merge it or `main` into
your story branch unless the lead authorizes the exact commits.

**Shared files.** Three text files are shared by region: `skills/run-delivery/scripts/run_delivery.py`,
`tests/test_run_delivery.py` and `skills/orchestrate-work/references/orchestration-guide.md`. Your assignment names your spans. Edit
inside them only: an insertion must be strictly inside a span, and a deletion or replacement must take lines from one span. If your
story needs a line at a span's edge or outside your spans, stop and ask the lead before you write it; do not work around it in another
file. The plan names three edits that cross lanes: a new journal kind's rows in lane 1's schema, publisher, line map and tests,
`lead-own --claim` in the argument parser, and the supervisor's reading of peers. Ask the lead for each when you reach it. After
another lane's merge edits a shared file, the lead regrants the regions, and tells you when yours has moved.

**Files you create.** Each new file's exact path is in the plan's ownership table. Create no other file outside a folder you own without
asking the lead for its path first.

**Provenance, catalog and profiles.** When you change a file under `skills/`, refresh its digest with
`.venv/bin/python scripts/update-provenance.py` and commit `provenance.lock.json` with the change. Add or remove no listed output. Do not
edit `catalog.json`, `profiles/` or `.agents/orchestration.json`: the lead writes that wording once, at integration.

**Proofs.** A row that asserts new behaviour is proven red against the code before its change. A row that asserts behaviour kept is
proven red against an over-correcting mutant of the new code, then the mutant is reverted. The plan names the mutants. A proof that
mutates a file `provenance.lock.json` covers refreshes that file's digest with the mutation, so the build succeeds and the row's own
assertion is what fails. Name each proof in your hand-off.

**Tests and the suite's budget.** Run your story's test modules with `.venv/bin/python scripts/run-tests.py -p <module>`, and never the
full suite. A new test class that keeps to its own folders, ports and processes is `run_quietly`, never `run_serially`. Reuse a fixture
your class already builds before building another: the suite has a time budget, `tests/suite-budget.json`, and this delivery stays
inside it. Name the catalogue ids you ran with `handoff --checks`.

**Criteria come from the tracker items**, and the plan's scenario rows bind beside them. Where a tracker item's wording and a plan
Decision differ, the Decision is the newer ruling: say so in your hand-off, naming both.

**Processes.** Stop only processes you started, by the pid you recorded, and never by pattern.

**The installed generation is not your source.** `.agent-skills/current` is `linear-v2.3.0`, which delivers this work and does not
contain it. Edit under `skills/`, never under `.agent-skills/`. This repository's binding declares none of the members these stories
add, so every new behaviour is proven in fixtures.
