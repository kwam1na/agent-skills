# V26-3694 lane notes

- Each lane delivers its stories in the plan's order, each handed off before the next starts.
- Lanes 2, 3 and 4 share `run_delivery.py` and `SKILL.md` by region, and lanes 1 and 2 share `journal-kinds.json`.
  Before review, run `claims-check <key>` and ask the lead for any span it names, by `--around` and a quoted line.
  Never edit outside your claims, docs included.
- A change that moves an existing assertion in `tests/test_run_delivery.py` asks the lead for a region over it and
  fixes it in the same change. No assertion is left red or deleted.
- Each new test module is catalogued in `.agents/orchestration.json` in the same diff, and named in your
  `handoff --checks`.
- Refresh `catalog.json`, `profiles/*` and `provenance.lock.json` with `scripts/update-provenance.py`, never by hand.
- No release is cut for this delivery. Don't touch version tags or release files.
- V26-3628's c4: the state tool's changes file records only `{at, change, journal, version}`, so show the one patch
  by one `patch` changes line, a one-step version move, and both values in the resulting state. Don't change the
  changes file's format, which the live view reads.
- V26-3578's c2: assert the doctor row's level, `warn` for a colliding key whose record folder is ignored, beside
  the `wrong` row for an unignored one.
