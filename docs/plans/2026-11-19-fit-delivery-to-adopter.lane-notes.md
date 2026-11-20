# V26-3656 lane notes

- Lane 1 delivers V26-3654, then V26-3655, then V26-3653, each handed off before the next starts.
- Both lanes share `run_delivery.py`, `config.md`, `adopting.md`, `SKILL.md` and `.agents/orchestration.json` by
  region. Before review, run `claims-check <key>` and ask the lead for any span it names, by `--around` and a quoted
  line. Never edit outside your claims, docs included.
- Each new test module is catalogued in `.agents/orchestration.json` in the same diff, and named in your
  `handoff --checks`.
- This delivery's own review runs the installed generation, so your rounds run every declared lens whatever your
  story changes.
