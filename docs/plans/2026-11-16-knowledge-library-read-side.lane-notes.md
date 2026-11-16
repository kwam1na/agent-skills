## This delivery's own rules

The approved plan's Decisions, and its Delivery section, are binding. These notes add what the plan leaves to the lead.

**Authority.** Lanes may push their story branches and open draft pull requests into `integrate/knowledge-library`, as
the delivery's grant states. No lane merges, marks a pull request ready, tags, or applies a release.

**The base is `integrate/knowledge-library`, not `main`.** Branch from it, open your pull request into it, and never
merge it or `main` into your story branch unless the lead authorizes the exact commits.

**Files.** Edit only the files your lane owns in the plan's Units and order table. Lanes share no file except
`provenance.lock.json`. Create no file outside your lane's paths without asking the lead for its path first.

**Provenance.** When you change a file under `skills/`, refresh its digest with
`.venv/bin/python scripts/update-provenance.py` and commit `provenance.lock.json` with the change. Only V26-3546 adds
listed outputs, the adapter's; no other story adds or removes one. Only lane 2 edits `catalog.json` and `profiles/`.

**Scope moves.** The plan's Delivery section moves the catalog capability and the profile description sentences into
V26-3546, the adapter-as-core-member row into V26-3546, and the "Properties are never guessed" row into V26-3547. Where
a tracker item's wording and the plan's Delivery section differ, the plan is the newer ruling: say so in your hand-off,
naming both.

**Proofs.** A row that asserts new behaviour is proven red before its change. A pin is proven red by mutating its text
with the file's digest refreshed in `provenance.lock.json`, so the build succeeds and the pin's own assertion is what
fails, as AGENTS.md describes. Name each proof in your hand-off.

**Tests and the suite's budget.** Run your story's test modules with `.venv/bin/python scripts/run-tests.py -p
<module>`, and never the full suite. Reuse a fixture your class already builds, and each profile class's existing
archive build, before building another: the suite has a time budget, `tests/suite-budget.json`, and this delivery stays
inside it. A new test class that keeps to its own folders, ports and processes is `run_quietly`, never `run_serially`.
Name the catalogue ids you ran with `handoff --checks`.

**Node.** This machine's default Node is 20. Put Node 22 first on `PATH` before any test or tool run:
`export PATH="$HOME/.nvm/versions/node/v22.23.3/bin:$PATH"`.

**Processes.** Stop only processes you started, by the pid you recorded, and never by pattern.
