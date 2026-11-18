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
- Without `--lenses` and without any default set, every declared lens runs, as today.
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
4. **The set is fixed at round 1.** Round 1's meta records `lenses` and `lensesReason`; the `review.started` entry
   carries both. A later round without `--lenses` keeps round 1's set. A later round with `--lenses` naming a different
   set is refused, naming round 1's set; the same set is accepted. `--restart` of round 1 may change the set and
   reason, and records the old ones in the restart's entry. Every story-review loop that iterates `c.lenses` today
   iterates the story's set instead: the restart's discards, the briefs, the reduction's results and refusals, the
   checkout removal, and `journal_returns`.
5. **The defaults, in order.** With no `--lenses` at round 1, the set is the story's `reviewLenses` in the delivery
   file, else the delivery file's `storyReview.lenses`, else every declared story lens. A default names its source as
   its reason, such as `the delivery file's storyReview.lenses`. A default is validated as `--lenses` is, and an invalid
   one is refused naming the file and field. A plan-only lens is never in a default.
6. **The checks follow the story's set.** `review-reduce` refuses a meta whose set isn't the story's fixed set.
   `handoff` refuses a last round that doesn't carry every lens of the story's set. `lead-accept` refuses a last
   journaled round whose verdicts aren't the story's set, read from round 1's `review.started` in the journal. A round
   carried from before this change, whose meta has no `lensesReason`, counts as the full declared set, so in-flight
   stories are unchanged.
7. **The set is shown.** `review-prepare` prints the set and its reason. `handoff`'s message names the set and the
   reason. `lead-accept` prints them before its decisions, so the lead can refuse an acceptance whose set is too thin.
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

In `tests/test_run_delivery.py`, with a fixture declaring one story lens, one plan-only lens through `review.lenses`'s
`use`, and one both-use lens:

- `review-prepare K --round 1 --lenses lens.x,lens.adversarial-testing,lens.outcome-correctness --lenses-reason R`
  writes briefs for those three only, and the meta and the `review.started` entry carry them in declaration order with
  `R`. `review-reduce` with those three results aligns, and `handoff` accepts the round; its message names the set and
  `R`.
- `--lenses lens.outcome-correctness,lens.x`, leaving out a mandated lens, is refused naming the mandated pair and the
  declared story lenses; nothing is written. A repeated id is refused.
- `--lenses lens.nowhere` is refused naming the declared story lenses; nothing is written. The plan-only lens is
  refused the same way.
- `--lenses` without `--lenses-reason`, with a blank one, or with one over 600 characters, and `--lenses-reason`
  alone, are each refused.
- Round 2 without `--lenses` keeps round 1's set. Round 2 with a different `--lenses` is refused naming round 1's set.
  `--restart` of round 1 with a different set is accepted and the restart entry records the old set.
- No `--lenses` and no defaults: every declared story lens runs, and the plan-only lens doesn't. With no `use`
  anywhere, every declared lens runs, as today.
- The delivery file's `storyReview.lenses`, and a story's `reviewLenses` over it, each set the round-1 set and its
  reason names the source. An invalid default is refused naming the file and field.
- A `use` that is empty, names an unknown use, names an undeclared lens or narrows a mandated lens is a config
  conflict.
- `review-reduce` refuses a meta whose set isn't round 1's. `handoff` refuses a last round missing a lens of the set.
- A round prepared before this change (a meta with the full set and no `lensesReason`) reduces and hands off as today.
- `doctor` prints one line per declared lens with its use; `config`'s row shows the use.
- `lead-brief` lists each story lens with its charter's purpose and leaves out the plan-only lens.

In `tests/test_run_delivery_supervisor.py`:

- `lead-accept` accepts a hand-off whose last round carries the story's chosen set, and prints the set and reason.
- `lead-accept` refuses one whose last journaled round lacks a lens of that set.

In `tests/test_run_delivery_plan_review.py`:

- `plan-review prepare --lenses` accepts the plan-only lens and refuses a story-only one, naming the plan lenses.

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
