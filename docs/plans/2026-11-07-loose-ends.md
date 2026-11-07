---
reviewSlug: loose-ends
---

# Finish the delivery workflow's loose ends

Epic V26-3265: ten backlog items left over from V26-3169's stories, its integration review and its flake work, plus one earlier gap at
`lane-start`. Each item's tracker scope binds what it does, apart from the changes under Decisions. This plan decides only what the items can't:
the operator's rulings, order, file ownership, the release in the middle, and how the work reaches `main`.

## Finish line

- Each item is merged to `main` through the merge queue, or closed with its recorded reason where this plan allows that.
- `main`'s CI on the last merged commit passes on the Ubuntu job and `cut-release`, with the run linked.
- `main`'s macOS job passes on that commit too. It informs rather than gates, but V26-3232's fix is judged by it, so a red macOS job at the close
  is a held finish line, not a footnote.
- The full local suite passes on macOS with Python 3.11 at that commit.
- `v1.6.0-rc.3` is tagged with the operator's word, applied here, and `.agents/orchestration.json` declares `regenerable.listings` (V26-3182).
- Every deferral has an open follow-up item, and the epic and its items are closed with this evidence.

## Out of scope

- The other backlog epics: lead hand-work (V26-3250), release safety (V26-3266), posture readers (V26-3267), and the harness runtime (V26-3268).
- Promoting `v1.6.0-rc.3` to `v1.6.0`. That happens after the candidate is applied in an adopter, under the repository's release rule.
- Making tracker refinement moves for V26-3201. Its Scope 5 already rules them out.
- Any change to obtain-review's rule text for V26-3194, beyond the drift test's declared difference. Its rule is the one being adopted.

## Decisions

1. **V26-3194, out-of-delta P1 (operator ruling, 2026-11-07).** Both briefs adopt obtain-review's rule. In a verification round, a new finding
   outside the delta is filed only at P0 in a story's review, and a P1 becomes a deferral with a follow-up item. In a plan review it is filed
   at P0 or P1, with no deferral. The reason: a P1 recorded only as dissent in `evidence` is tracked by nothing afterwards, a story's deferral
   path already exists to track it without blocking convergence, and a plan P1 is cheapest to fix before any lane starts. So run-delivery's
   `STORY_SEVERITIES`, `PLAN_SEVERITIES`, `review-reduce` and `plan-review reduce` change, and obtain-review's text doesn't. The item's
   characterization-first posture still applies: record what each reducer does today before changing either.
2. **V26-3182, the release (operator ruling, 2026-11-07).** `regenerable.listings` is on `main` (#255) but not in `v1.6.0-rc.2`. Once every
   lane story has merged, the lead cuts `v1.6.0-rc.3` at that batch boundary, so the candidate also carries this epic's fixes, and applies it
   here. V26-3182 then lands last, from the lead. The lead asks the operator before pushing the tag. A tag push publishes, and tagging is not
   pre-authorized.
3. **V26-3232 is a fix, not a watch.** Its first flake now recurs on every macOS run on `main` from 41873429274 to 42398660322, the last 15:
   `tests.test_run_delivery` reaches its 1800-second limit inside `test_timeouts_and_interruptions_stop_their_processes`, blocked in
   `communicate()` under `run_group`. The latest run also timed out `tests.test_run_delivery_supervisor`. Its acceptance criterion 1 applies:
   reproduce, fix, and show it with macOS CI. Criterion 2's ten-run watch no longer can. It goes first in its lane, because the
   local macOS suite that gates the rc.3 tag runs the same module.
4. **V26-3164 and V26-3232's page flake are diagnosed together**, as the epic says. Both are the "The plan shown is out of date." message. The
   page lane's lane owns both, so V26-3232's page half moves to V26-3164, and V26-3232 keeps the `test_run_delivery` half.
5. **V26-3201 Scope 4, readiness before assignment (lead's call).** `doctor --delivery` warns, and `lead-assign` refuses, with `--force`
   overriding the refusal. Both name the story, its status and the missing transition, and neither moves anything.

## Units and order

Two lanes. Seven of the ten items change `run_delivery.py`, and no two lanes hold one file at a time, so a third lane would only wait. Lane 1
holds the run-delivery chain throughout. Lane 2 takes the queue and the views, then hands its files to lane 1 for V26-3242, which spans both.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3232, V26-3175, V26-3201, V26-3194, V26-3183, V26-3180 | `skills/run-delivery/` (script, templates, `references/adopting.md`), `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_plan_review.py`, `tests/test_round_brief_template_corpus.py`, `tests/test_review_and_compounding.py` |
| 1 | 2 | V26-3197, V26-3164 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/test_merge_queue.py`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/delivery-views/`, `tests/test_delivery_views_page.py`, `tests/delivery_views_page/` |
| 2 | 1 | V26-3242 | lane 1's files, plus lane 2's once V26-3164 merges |
| 3 | lead | rc.3, then V26-3182 | `.agents/orchestration.json` |

**Moving ownership.** A file passes when the item holding it merges. The lead rewrites `lanes.*.owns` at each hand-over, the same way the
V26-3169 plan did, pushing only while no candidate is synced or checking. `provenance.lock.json` moves to the lane being accepted immediately
before each `lead-accept`. Each item's test-pin updates belong to it, including the `schemas.json` shape pins and the tracker updates table
if a journal kind is added. If lane 2 finishes before lane 1 reaches V26-3242, the lead gives lane 2 whatever unblocked follow-up the record
holds, or rests it and records why, since no unblocked work in this epic needs files lane 1 doesn't own.

**Shared resources.** V26-3175's rows bind real listeners on ephemeral ports the test owns, and touch the in-app lease. Modules that can't share
a machine are already marked `run_serially`. A lane that adds such a test marks it the same way, and no lane runs the in-app check outside its
granted lease.

## Stories

### V26-3232. Fix the macOS `test_run_delivery` timeout

Lane 1, tier 1, not blocked. Characterization-first: reproduce the hang locally on macOS under CI's conditions (a cleared environment keeping
CI's baseline and its workflow `env`, no tty) before changing anything, and capture a sample of the stalled process. The open hypothesis is
the stdin-pipe class fixed in ff710de, or `run_group`'s reads outliving the group kill when a backgrounded child holds the pipes. Fix it in
`run_group` if that's where it lives. If the supervisor module's timeout has the same cause, fix it here too. If not, file it. Files:
`run_delivery.py`, `tests/test_run_delivery.py`. Sensors: the named test, `tests/test_run_delivery.py`, and macOS CI on the change request
with the `full-matrix` label. Compounding: the solution note the item names, if the pipe explains it.

### V26-3175. Pin `lead-close`'s busy-port lines

Lane 1, tier 1, after V26-3232. Test-first, per the item: a held lease reports only the held-lease line, a free lease with a real listener
names its pid and the kill hint, and each row fails when its branch's line is removed. Files: `tests/test_run_delivery.py`, and
`run_delivery.py` only if a row shows a defect. Sensors: `tests/test_run_delivery.py`.

### V26-3201. `lane-start` marks the start, and assignment checks readiness

Lane 1, tier 1, after V26-3175. Test-first, per the item's seven scenarios, with Decision 5 for Scope 4. Files: `run_delivery.py`,
`skills/run-delivery/templates/lane-brief.md`, `tests/test_run_delivery.py`, and the line-map goldens only if a fixture covers a started story
with no snapshot. Sensors: the `lane-start`, `lead-assign` and `doctor` tests, and the brief template tests.

### V26-3194. One out-of-delta P1 rule in both briefs and every reducer

Lane 1, tier 1, after V26-3201. Characterization-first: first, a row per reducer recording where today's out-of-delta P1 ends up. Then apply
Decision 1. Files: `run_delivery.py` (`STORY_SEVERITIES`, `PLAN_SEVERITIES`, `review_reduce`, `plan_review_reduce`),
`skills/run-delivery/templates/round-brief.md` if it restates the rule, and `tests/test_round_brief_template_corpus.py`, whose declared
difference goes. Sensors: the four the item names.

### V26-3183. A ready-made `reviewGate` command

Lane 1, tier 1, after V26-3194. Test-first, per the item. Files: `run_delivery.py`, `references/adopting.md`, `tests/test_run_delivery.py`,
and `catalog.json`, the profiles and the provenance lock only if an output is added. Sensors: `tests/test_run_delivery.py`,
`scripts/validate-registry.py`.

### V26-3180. A run-delivery command for `carry-review`

Lane 1, tier 1, after V26-3183. Test-first, per the item. It reaches `delivery_state.py carry-review` through the state tool and changes nothing
in it. Files: `run_delivery.py`, `references/adopting.md`, `tests/test_run_delivery.py`. Sensors: `tests/test_run_delivery.py`,
`scripts/validate-registry.py`.

### V26-3197. Every merge-queue step keeps its stderr

Lane 2, tier 1, not blocked. Test-first, per the item, with a row per step putting a known secret in its stderr. The guide states the scrub's
limit. Files: `merge_queue.py`, `tests/test_merge_queue.py`, `orchestration-guide.md`. Sensors: `tests/test_merge_queue.py`.

### V26-3164. The page lane's "plan shown is out of date" flake

Lane 2, tier 1, after V26-3197, with V26-3232's page half (Decision 4). Characterization-first: reproduce under load, on Ubuntu if macOS won't,
before changing a test or the page. If neither reproduces within a bounded effort, the item closes with the evidence and the hypothesis
recorded, and V26-3232's page half closes with it. Files: `skills/delivery-views/`, `tests/test_delivery_views_page.py`,
`tests/delivery_views_page/`. Sensors: `tests/test_delivery_views_page.py`, the page lane.

### V26-3242. Three integration-review gaps

Lane 1, tier 2, after V26-3180 and V26-3164. Test-first, per the item: `finishing_command` names `lead-close` for a holding need, the line map
words `base.verified` and `selection.refused`, and a titled `refusalStops` row. Files: `run_delivery.py`, `tests/test_run_delivery_supervisor.py`,
`skills/delivery-views/assets/line-map.js` and its page tests, `tests/test_merge_queue.py`. Sensors: the three the item names.

### V26-3182. Adopt `regenerable.listings` here

Lead, tier 3, after `v1.6.0-rc.3` is applied (Decision 2). Sensor-only, per the item: `doctor`, the binding check, and the queue's own
`source_conflict` row. Files: `.agents/orchestration.json`. Sensors: `scripts/validate-registry.py`, `run_delivery.py doctor`,
`tests/test_own_deliveries.py`.

## Release step

After V26-3242 merges and `main`'s CI is green on Ubuntu and `cut-release` at that commit, the lead clears `skills/` bytecode, runs the full
suite locally on macOS with Python 3.11, and brings the operator the commit and the run for the `v1.6.0-rc.3` tag. On a yes, it pushes the
annotated tag, verifies the published release's digests, applies it at `.agent-skills/current`, and then lands V26-3182.

## Execution posture

Per item, as above: `characterization-first` for V26-3232, V26-3164 and V26-3194, `test-first` for the rest, and `sensor-only` for V26-3182.
Lanes work to the focused lane profile. The queue runs the selected checks on the synced head, and the lead runs the full suite at the
release step and the close.

## Approval handoffs

- The plan itself, before `lead-setup`.
- The `v1.6.0-rc.3` tag push.
- Nothing else is outward-facing beyond the merge queue's own merges, which the binding already authorizes.

## Tracking

The epic and its ten items are in Linear. The lead posts at each milestone with the host's Linear tools and moves items to their final state.
V26-3232 and V26-3164's descriptions get Decisions 3 and 4 recorded as comments when the delivery starts.
