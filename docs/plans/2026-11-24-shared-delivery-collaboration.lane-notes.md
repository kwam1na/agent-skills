# V26-3794 lane notes

- Each lane delivers its stories in the plan's order, each handed off before the next starts. Validate on a base
  holding every story your story comes after.
- The plan's "Decisions recorded here" (D1 to D22) bind every story. Read the ones your story names before
  starting. A change that departs from one goes to the lead as a plan question, not into the diff.
- An unshared record behaves exactly as today (D1). Every existing test, golden and fixture stays green, apart
  from the pins your story lists. A pin you find that the plan does not list is a plan question.
- V26-3775 (plan previews) runs on `main` beside this delivery and shares files with it:
  - `run_delivery.py`, where V26-3778 and V26-3779 work;
  - `render_state.py` and the runs page (V26-3777);
  - the line map and `run-summary.js` (V26-3776);
  - the skill pages (V26-3780).

  Claim only your own regions. V26-3798 starts only after V26-3779 (with V26-3776 and V26-3778) has reached this
  integration base, and V26-3801 only after V26-3776 and V26-3777 have. Both reuse that work, never copy it.
- kwam1na/agent-skills#488 (chapters as pages) is an open draft. If it has landed when V26-3801 starts, draw the
  new views as chapters in its model.
- `tests/test_journal_shape.py`'s `INVENTORY` is shared by region. Ask for your story's rows with `lead-own --region`
  when you start. Lane 1 registers every new journal kind and wait kind. Lane 2 never edits the kind registry, the
  wait words or the orchestration guide.
- Multi-machine tests build their own bare remotes and clones through `tests/collab_remotes.py`. Those remotes allow
  forced updates and log every ref update (D18). Peers are observed by reading their `state.json` and
  `journal.jsonl`, never through a command that could ingest.
- No test compares clock times written on different machines (D21). Set one clone's clock behind where the plan
  says to.
- Each new test module is catalogued in `.agents/orchestration.json` in the same diff and named in your
  `handoff --checks`. Python tests stay inside `tests/suite-budget.json`: reuse a class's fixture before building
  another.
- Processes a test starts are stopped by their recorded pid, never by pattern. Free loopback ports only.
- No release is cut and no tag is made by a lane. The release candidate is operator work after close-out, after
  V26-3775's.
