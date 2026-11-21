# V26-3670 lane notes

- Lane 1 delivers V26-3666, then V26-3669. Lane 2 delivers V26-3667, then V26-3668. Each story is handed off before
  the lane's next starts.
- V26-3666 is characterization-first for the runtime (0.8.6). Any emission the runtime refuses goes to the lead,
  who takes it to the operator before V26-3666 continues (Decision 10). Never change the runtime or its grammar.
- V26-3668 starts once V26-3666 and V26-3667 have landed on the base: it reads V26-3666's captured run output and is
  driven by V26-3667's `event: runs` push. Merge the base before you start it.
- V26-3669 starts only once the operator has called a release candidate holding V26-3666 to V26-3668 and it is
  applied here. The lead tells lane 1 when.
- `.agents/orchestration.json` is lane 1's. A new test module in lane 2 is catalogued there in the same diff: run
  `claims-check <key>` and ask the lead for the span by `--around` and a quoted line. Name every new module in your
  `handoff --checks`.
- `provenance.lock.json` is regenerable: refresh it with `scripts/update-provenance.py`, never by hand. A
  `catalog.json` or profile edit needs a claim from the lead first.
- Runtime tests run the stored runtime under Node 22.6 or newer in a temporary git repository, never in the
  checkout, and fail naming the need rather than skip. Live-server tests bind `--port 0`, stop only what they started
  by recorded PID, and are marked `run_quietly`. No number in `tests/suite-budget.json` moves.
