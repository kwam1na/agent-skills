---
reviewSlug: story-review-lens-selection
---

# Let a lane pick its story review's lenses instead of running every declared lens

A story's review rounds always run every lens the repository declares: the workflow's two, every
`additionalReviewLenses` entry in `harness.config.ts`, and every `review.lenses` entry in `.agents/run-delivery.json`.
`review-prepare <key> --round N` takes no lens argument, and `review-reduce` refuses a round whose lens set differs
from the full declared list. That contradicts execute-work's contract, where the two workflow lenses are mandated and
every other lens is the executor's own selection, declared before the first round and held for the delivery. It also
couples plan review to story review: a lens an adopter wants only for plan review has to be declared, and once
declared it reviews every round of every story's code too.

An adopter declares five extra lenses: three for plan review (feasibility, scope, adversarial document), one for plan
and code (project standards) and one for code that changes stored data (data integrity). Every story round in every
lane gets seven reviewers, three of them asking plan questions of a code diff, and dropping the plan lenses from the
declaration removes them from plan review as well.

Checked against `main` at `bb54fe4` on 2026-11-18:

- `Config.__init__` in `run_delivery.py` builds `self.lenses`, an ordered dict of lens id to `(short, charter)`, from
  the two workflow lenses, `harness.config.ts`'s `additionalReviewLenses` and the own config's `review.lenses`.
- `review_prepare` iterates `c.lenses` for the discarded results on restart, the briefs, the `review.started` journal
  entry and the round's meta. `review_reduce` refuses `meta["lenses"] != list(c.lenses)`, then iterates `c.lenses` for
  the results, the validator refusal and the checkout removal. `journal_returns` iterates `c.lenses` but skips any
  lens the round's start doesn't name.
- `handoff` refuses a last round whose entries aren't `set(c.lenses)`. `lead-accept`'s check refuses a last journaled
  round whose verdicts aren't `set(c.lenses)`.
- `plan_review_lenses` accepts any declared lens. Plan review already fixes its set at round 1 and refuses a different
  one later.
- The runtime's `readAdditionalReviewLenses` in `product-runtime/kernel.mjs` closes each `additionalReviewLenses` entry
  to `lensId`, `reviewerId` and `charterPath`, so `harness.config.ts` cannot carry a new member without a runtime
  change.
- `lead-brief` fills the lane brief's `{{lenses}}` with every declared lens id. `doctor` names no lens.

## Finish line

- Both plan lenses align.
- A story round can run the mandated pair plus a lane-chosen subset of the declared story lenses, fixed at round 1 with
  its reason, shown at hand-off and at `lead-accept`.
- Without `--lenses` and with no `use` declared, every declared lens runs, as today.
- The behaviour is proven by fixture tests in the run-delivery test modules, which run this checkout's
  `run_delivery.py`. This delivery's own review runs the installed generation, which has no `--lenses`, so its own
  rounds run every declared lens.
- The story has landed on `main` through the merge queue. The full local suite has passed on macOS with Python 3.11
  at the base head after it landed, run with `--workers 4 --budget tests/suite-budget.json`, inside the budget.
- Tagging a release is not this delivery's. The change is additive, a minor version, and goes out at the next batch
  boundary.

## Decisions

1. **Declared means available.** The mandated lenses are `lens.outcome-correctness` and `lens.adversarial-testing`.
   Every other declared lens is available to a story round when its use includes `story`, and to plan review when its
   use includes `plan`. The mandated pair's use is both and cannot be changed.
2. **Where a lens's use is declared.** An own-config `review.lenses` entry may carry `"use": ["plan"]`, `["story"]` or
   both; omitted means both. A lens from `harness.config.ts` takes its use from the own config's
   `review.use`, a map of lens id to that list, because the runtime closes its entries and this change does not touch
   the runtime. A `use` that is empty, names anything else, names an undeclared lens or names a mandated lens as other
   than both is a config conflict, which `doctor` and every command already report.
3. **`review-prepare --lenses`.** `review-prepare <key> --round 1 --lenses a,b --lenses-reason TEXT` declares the
   story's set. It must name both mandated lenses: leaving one out is refused, naming the mandated pair and the
   declared story lenses. The set is stored in declaration order, whatever order `--lenses` gives, so the same choice
   always gives the same list. An id that isn't declared, isn't a story lens, or is repeated is refused, naming the
   declared story lenses. `--lenses` without `--lenses-reason`, an empty or blank reason, or a
   reason over 600 characters is refused. `--lenses-reason` without `--lenses` is refused.
4. **The set is fixed at round 1, in the round metas.** Every round's meta records `lenses` and `lensesReason`, and
   rounds after the first copy both from round 1's meta. A later round without `--lenses` keeps round 1's set. A later
   round with `--lenses` naming the same set is accepted, and one naming a different set is refused, naming round 1's
   set. A later round of a story prepared before this change, whose round 1 meta has no `lensesReason`, records every
   declared lens, plan-only lenses included, and no `lensesReason`. `--restart` of round 1 may change the set and
   reason, and the restart's entry in the meta's `restarts` records the old ones. A restart discards the results of
   the lenses the previous meta named, so a result left from an earlier set is never reduced. The journal is unchanged: `review.started` already carries `lenses`, which now holds the story's set,
   and no new field is journaled, because `journal-kinds.json` closes that entry's data and changing it is a
   record-shape change. Every story-review loop that iterates `c.lenses` today iterates the story's set instead: the
   restart's discards, the briefs, the reduction's results and refusals, the checkout removal, and `journal_returns`.
5. **The default is every story lens.** With no `--lenses` at round 1, the set is every declared lens whose use
   includes `story`, and the reason recorded is `every declared story lens`. A plan-only lens is never in it. With no
   `use` declared anywhere, that is every declared lens, as today. The delivery file gets no default set: nothing
   writes one and `lead-setup`'s redraft would drop it, so the lane brief is where the lead asks for a set.
6. **The checks follow the story's set.** A story whose round 1 meta has a `lensesReason` has that meta's set as its
   set. A story whose round 1 meta has none was prepared before this change, and its set is every declared lens, so
   in-flight stories are checked as today. `review-reduce` refuses a meta whose `lenses` aren't the story's set.
   `handoff` refuses a last round whose entries aren't the story's set. `lead-accept` takes the story's set from the
   latest `review.started` for round 1 in the journal. When round 1's meta, read as in Decision 7, has no
   `lensesReason`, or there is no such entry, it uses every declared lens. It refuses a last journaled round whose
   verdicts aren't that set.
7. **The set is shown.** `review-prepare` prints the set and its reason. `handoff`'s message names the set and the
   reason. `lead-accept` prints the set and the reason before its decisions, so the lead can refuse an acceptance
   whose set is too thin. It reads the reason from round 1's meta in the story's review folder, where the hand-off
   left it (the lane's checkout, else the kept lane evidence), and prints `reason not found` when neither holds it.
8. **Plan review takes plan lenses.** `plan_review_lenses` refuses a lens whose use excludes `plan`, naming the
   declared plan lenses. A story-only lens is refused the same way.
9. **`doctor` lists each declared lens** on one `ok` line per lens, with its use (`plan`, `story` or `plan, story`)
   and where it was declared. `config`'s `review.lenses` row shows the use too.
10. **The lane brief lists the story lenses.** `lead-brief` fills `{{lenses}}` with the mandated pair and, for each
    other story lens, its id and its charter's purpose: the first line of the charter after its title that isn't
    blank or a heading, cut to 160 characters. The template says how to choose with `--lenses` and `--lenses-reason`,
    that the set is fixed at round 1, and that the lead sees it.

## Out of scope

- A `use` member in `harness.config.ts` itself. That needs a runtime change, which goes out as a candidate first.
- Changing the round brief template or the review validator. A round's `requestedLenses` already names its own set.
- Choosing lenses per round. The set is held for the story.
- Plan review's own rules beyond Decision 8.
- A default story set in the delivery file (`storyReview.lenses`, or a story's own set), which the ticket proposed:
  no command writes either and `lead-setup`'s redraft drops them. A lead asks for a set in the lane brief.
- Journaling the reason or a restart's old set. `review.started`'s data is closed, so either is a record-shape change.

## Approval Handoffs

- The operator approves this plan before `lead-setup`.
- The operator grants lanes `push` and `draftPullRequests`, and the lead `readyPullRequests`.

## Execution posture and sensors

Test-first. Sensors: `tests/test_run_delivery.py` for `review-prepare`, `review-reduce`, `handoff`, `doctor`, `config`
and `lead-brief`; `tests/test_run_delivery_supervisor.py` for `lead-accept`; `tests/test_run_delivery_plan_review.py`
for plan review; `scripts/validate-registry.py` and `scripts/update-provenance.py --check` for the catalog, profiles
and provenance. The candidate's checks: `tests.test_run_delivery`, `tests.test_run_delivery_supervisor`,
`tests.test_run_delivery_plan_review`, plus the guards.

## Scenarios

In `tests/test_run_delivery.py`, with a fixture declaring, besides the mandated pair: `lens.x`, a story-only lens
through `review.lenses`'s `use`; `lens.p`, a plan-only lens through `review.lenses`'s `use`; `lens.b`, a
`review.lenses` lens with no `use`; and `lens.h`, a `harness.config.ts` lens made plan-only through `review.use`:

- `review-prepare K --round 1 --lenses lens.x,lens.adversarial-testing,lens.outcome-correctness --lenses-reason R`
  writes briefs for those three only, and the meta and the `review.started` entry carry them in declaration order,
  the meta with `R`. It prints the set and `R`. `review-reduce` with those three results aligns, and `handoff` accepts
  the round; its message names the set and `R`.
- `--lenses lens.outcome-correctness,lens.x`, leaving out a mandated lens, is refused naming the mandated pair and the
  declared story lenses; nothing is written. A repeated id is refused. `--lenses lens.nowhere` is refused naming the
  declared story lenses. `--lenses` naming `lens.p`, and naming `lens.h`, are each refused the same way.
- `--lenses` without `--lenses-reason`, with a blank one, or with one of 601 characters, and `--lenses-reason` alone,
  are each refused. A reason of exactly 600 characters is accepted.
- Round 2 of the chosen-set story: without `--lenses` it keeps round 1's set, and its meta copies the set and `R`;
  with the same `--lenses` it is accepted; with a different `--lenses` it is refused naming round 1's set. That round
  2 reduces and the story hands off with three lenses.
- `--restart` of round 1 with a different set is accepted, the restart entry records the old set and reason, and the
  round reduces with the new set.
- No `--lenses`: the set is the mandated pair, `lens.x` and `lens.b`, without `lens.p` or `lens.h`, and the reason is
  `every declared story lens`. A fixture with no `use` and no `review.use` runs every declared lens, as today.
- A `use` that is empty or names an unknown use, a `review.use` that names an undeclared lens, and a `use` or
  `review.use` that narrows a mandated lens are each a config conflict.
- `review-reduce` refuses a meta whose set isn't round 1's. `handoff` refuses a last round missing a lens of the set.
- A story prepared before this change (a round 1 meta with every declared lens and no `lensesReason`) reduces and
  hands off as today, and one whose legacy meta lists fewer lenses is refused, as today. From the same legacy round 1,
  round 2 prepared with no `--lenses` succeeds, writes briefs for every declared lens, `lens.p` and `lens.h`
  included, and its meta carries no `lensesReason`; that round 2 reduces and hands off.
- Round 1 prepared with the pair and `lens.x`, restarted with the pair and `lens.b`, then restarted again with the
  pair and `lens.x`: the first attempt's `lens.x` result was discarded at the first restart and is not reduced.
- `doctor` prints one line per declared lens with its use and where it was declared: `lens.h` as `plan`, from
  `harness.config.ts`; `lens.b` as `plan, story`, from the own config. `config`'s row shows the use.
- `lead-brief` lists `lens.x` and `lens.b`, each with its charter's purpose, and leaves out `lens.p` and `lens.h`. One
  charter's first line after its title is a heading and another's is over 160 characters: the purpose skips the
  heading and is cut to 160.

In `tests/test_run_delivery_supervisor.py`, with a fixture declaring two extra story lenses, `lens.x` and `lens.y`:

- A story chooses the mandated pair and `lens.x`. `lead-accept` accepts its hand-off and prints the set and reason.
- `lead-accept` refuses a hand-off whose last journaled round carries the pair and `lens.y` instead of `lens.x`.
- Round 1 prepared with the pair and `lens.x`, then restarted with the pair and `lens.y`: `lead-accept` accepts
  against the restart's set.
- A pre-change story, whose round 1 `review.started` names every declared lens and whose meta has no `lensesReason`,
  is accepted as today, and `lead-accept` prints `reason not found` rather than failing.
- A hand-off whose story has no round-1 `review.started` in the journal is checked against every declared lens: one
  carrying every declared lens is accepted, and one missing a declared lens is refused.
- A pre-change story whose round-1 `review.started` names a lens no longer declared, with round 2 run on the current
  declared set, is accepted.
- The reason is printed when only the kept lane evidence holds round 1's meta, the lane checkout having gone.

In `tests/test_run_delivery_plan_review.py`, with the same plan-only, story-only, both-use and harness lenses:

- `plan-review prepare --lenses` accepts `lens.p`, `lens.h` and `lens.b`, the last declared with no `use`, and refuses
  `lens.x`, naming the declared plan lenses.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3606 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/config.md`, `skills/run-delivery/references/adopting.md`, `skills/run-delivery/templates/lane-brief.md`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_plan_review.py`, `catalog.json`, `profiles/*` |

`provenance.lock.json` is regenerable. The lane runs the writer, as `AGENTS.md` describes.

## Stories

### V26-3606. Let a lane pick its story review's lenses instead of running every declared lens

Lane 1, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/config.md`, `skills/run-delivery/references/adopting.md`,
`skills/run-delivery/templates/lane-brief.md`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`,
`tests/test_run_delivery_plan_review.py`, `catalog.json`, `profiles/run-delivery.json`, `profiles/linear.json` and
`profiles/jira.json`.

Implement Decisions 1 to 10. Describe the selection in run-delivery's `SKILL.md` command table for `review-prepare`,
in the config reference and the adoption guide, and in the lane brief template. Update the catalog entry, every
selected profile and provenance.

Acceptance criteria:

- `c1` A story round can run the mandated pair plus a lane-chosen subset of the declared lenses. The set is fixed at
  round 1, recorded with its reason, and shown at hand-off and `lead-accept`.
- `c2` Leaving out a mandated lens, or adding an undeclared one, is refused with the declared list. A different set in
  a later round is refused.
- `c3` A lens declared plan-only is accepted by `plan-review` and refused, and never defaulted, in a story round.
- `c4` Without `--lenses` and without any default set, behaviour is unchanged: every declared lens runs.
- `c5` `doctor` lists each declared lens with its use (plan, story, or both).
- `c6` The config reference, the adoption guide and the lane brief describe the selection.

### V26-3618. Wake the lead when the supervisor holds the finish, and clear a stale suite checkout before serve refuses on it

Lane 1, tier 1, after V26-3606. Added mid-delivery: The operator added it to this delivery after the delivery's own merge queue was refused for about 12 minutes on another delivery's stale lead-suite checkout, and no wake reached the lead.

### V26-3620. Make the delivery's name the live view's headline, with the count under it and a one-line kicker

Lane 1, tier 1, after V26-3618. Added mid-delivery: The operator chose this design for the live view's opening from mockups during this delivery and asked for it to join this delivery's work, after V26-3618 on the one lane.
