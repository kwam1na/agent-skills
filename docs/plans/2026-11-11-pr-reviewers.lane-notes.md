## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**The base is `integrate/pr-reviewers`, not `main`.** Branch from it, open draft pull requests into it, and read the delivery's
settings from it. Never merge `main` in. The lead merges the branch into `main` at the end (Decision 11).

**Authority.** Lanes may push their story branches and open draft pull requests into `integrate/pr-reviewers`, as the delivery's grant
states. No lane merges, marks a pull request ready, tags or applies a release.

**No deferrals.** Nothing found in this delivery is deferred. Every finding is fixed in the story that found it, before hand-off,
whatever its severity, cost or round. This overrides the brief's Triage and Hand-off sections where they say otherwise:
- A P2 or P3 a lens files as deferred is fixed in the lane, the same as a blocking finding.
- A late P1 listed in `lateFindings` is fixed too.
- A note a lens leaves in its evidence (for example "P2, not filed") is a finding, and is fixed.
- `handoff` carries no `--findings`, and no follow-up item is filed for anything this delivery found.

If a fix needs a file or a region outside yours, ask the lead. If a fix is out of reach (it needs an operator decision, or another
epic's change), stop and tell the lead before hand-off. Don't hand off around it.

**The plan's rows are the criteria's evidence.** Each story section lists named rows. Write every one, deny side and allow side, and
name in your hand-off evidence which test proves each row.

## Two lanes in shared files (Decision 13)

Both lanes edit `skills/run-delivery/scripts/run_delivery.py`, both forge adapters, `tests/test_forge_adapters.py`, the run-delivery
references, `SKILL.md`, the lane-brief template, `.agents/orchestration.json` and `tests/test_own_deliveries.py`. The delivery file
gives them to lane 1. The lead moves the ones a lane-2 story touches to lane 2 just before accepting it, and back afterwards, so
`lead-accept`'s ownership check keeps holding. You don't move ownership yourself, and you don't need to ask before editing your own
regions below. Edit nothing outside them.

**Lane 1 (V26-3345, then V26-3347) holds the wait:**
- in `run_delivery.py`: `pr_review_config`, `pr_review_state` and its classification, the settle, the injectable clock, retries,
  `call_adapter`, the record, triage, the PR-review blocks of `handoff`, `lead_accept` and `review_gate`, `pr_review_text`, and the
  doctor's `review_pr`;
- in the adapters: comment, check and error handling, `account`, `doneAt`, and exit 75;
- `tests/test_run_delivery_pr_review.py` (yours alone), and the existing PR-review rows in `tests/test_run_delivery.py`.

**Lane 2 (V26-3346, then V26-3348) holds the gates and the reports:**
- `skills/run-delivery/scripts/pr_review_gates.py` and `tests/test_run_delivery_pr_review_gates.py`, yours alone: verdict parsing,
  approval matching and report reading as pure functions;
- in the adapters: the new `approvals` and `reports` members only;
- in lane 1's functions: call sites only, the lines that call into your module and report what it returns. Nothing else in them.

**Shared, by subsection.** In each doc, add your story's own subsection and leave the others' alone. In
`.agents/orchestration.json` and `tests/test_own_deliveries.py`, add only your module's catalogue entry.

**Rebases.** The brief's "never merge the base" has one exception here, and only on the lead's word: a lane-2 story starts on its own
module and adapter members, and once the lane-1 story it builds on has merged (V26-3345 for V26-3346, V26-3347 for V26-3348), the lead
tells you to rebase it once onto `integrate/pr-reviewers` and wire it in. Do that before the story's first review round, so the rebase
costs no round. Every later sync is the merge queue's.

**Checks for a new test module.** Until its catalogue entry merges, `handoff --checks` can't name the module that your story creates.
Name it and its run in your hand-off evidence instead. The lead accepts V26-3345 and V26-3346 with `--full-suite` (Decision 12).

**Order.**
- Lane 1: V26-3345, then V26-3347.
- Lane 2: V26-3346 at once, on its module and adapter members. Wire it in when the lead says V26-3345 has merged. V26-3348 starts once
  V26-3346 has merged, and is wired in when the lead says V26-3347 has merged.

**Machine notes.** The shell's default Node is 20.19. `tests/test_own_deliveries.py`'s fresh-clone setup check needs Node 22.6 or
newer, so put Node 22's bin first on `PATH` (`$HOME/.nvm/versions/node/v22.23.3/bin`) when you run it. No row reaches a real forge,
opens a port or sleeps for real (Decision 12).
