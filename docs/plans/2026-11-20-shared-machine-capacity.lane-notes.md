# V26-3689 lane notes

- Lane 1 delivers V26-3680, then V26-3681, each handed off before the next starts.
- Lanes 2 and 3 start V26-3682 and V26-3683 only once V26-3680 has merged on the base. Until then the lead holds
  them back. V26-3683 also waits for V26-3573 to be on the base, since it changes the lead-wait fixture tests.
- `catalog.json` is shared by region: each lane edits only its own skill's lines, as Decision 16 says, under the
  span the lead grants.
- `provenance.lock.json` is shared. Refresh it with `scripts/update-provenance.py`, never by hand; a conflict in it
  alone is resolved as `AGENTS.md` says. Only V26-3680 adds a listed output (`machine.py`).
- Tests are test-first, in-process with an injected clock and sleep where they can be, and every test uses its own
  machine directory with capacity set explicitly. New cases reuse the fixtures their classes already build, and the
  suite stays inside `tests/suite-budget.json`.
- Before review, run `claims-check <key>`, and ask the lead for any claim it names by `--around` and a quoted line.
  Never edit outside your claims, docs included.
- Name the checks you ran in `handoff --checks` by their catalogue ids.
