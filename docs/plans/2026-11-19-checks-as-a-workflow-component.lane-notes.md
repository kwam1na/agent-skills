# V26-3645 lane notes

- Lane 1 delivers V26-3660, then V26-3661, then V26-3663, each handed off before the next starts.
- Lane 2 starts V26-3662 only once V26-3660 has merged on the base, and V26-3664 once V26-3661 and V26-3662 have
  merged. Until then the lead holds the story back.
- `provenance.lock.json` is shared. Refresh it with `scripts/update-provenance.py`, never by hand; a conflict in it
  alone is resolved as `AGENTS.md` says.
- New cases go into the existing test modules and reuse the fixtures their classes already build. No catalogue entry
  is added, and the suite stays inside `tests/suite-budget.json`.
- Lane 1 edits the orchestration guide and skill page, so `tests.test_orchestration_state` runs with its own sensors.
- Before review, run `claims-check <key>`, and ask the lead for any claim it names by `--around` and a quoted line.
  Never edit outside your claims, docs included.
- Name the checks you ran in `handoff --checks` by their catalogue ids.
