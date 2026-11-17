---
reviewSlug: checks-a-story-adds
---

# Let a story's checks include a catalogue entry it adds itself

When a story adds a test module, it also adds that module's entry to the binding's check catalogue. Until that entry
reaches the base, nobody can name the new check. The lane's `handoff --checks` refuses it, and so does the lead's
`lead-accept --checks`, because both read the catalogue from the base. The lane writes the id into its hand-off
evidence instead, and the lead picks the check by hand. V26-3344 and V26-3512 each lost time this way. V26-3512 took
`tests.test_run_delivery_peers` out of the catalogue and put it back twice.

The reverse gap is open too. When a test module is never catalogued, nothing fails, and the lead can't select it.

Checked against `main` at `588cc24` on 2026-11-17:

- `check_ids` in `run_delivery.py` validates `--checks` against `catalogue_ids(c)`. That reads `selection.catalogue`
  from the binding on the shelf, which is the delivery base, so an id only the candidate holds is refused.
- `lead-accept` passes the ids to the state tool's `select`, along with the lead checkout's binding. `select` already
  takes a JSON list whose objects are full check entries, and it resolves each one beside the catalogue ids. Only a
  catalogue id that is defined differently is refused.
- `proposed_checks` proposes the catalogue ids of the test modules the diff changes, keeping only ids the base's
  catalogue holds. A module the story creates is dropped.
- `tests/test_own_deliveries.py` pins `CATALOGUE_MODULES` against the binding and asserts every catalogued module
  exists. Nothing asserts that every `tests/test_*.py` module is catalogued. `tests.test_suite_budget` is
  uncatalogued today.

## Finish line

- Both plan lenses align.
- A lane hands off naming, in `--checks`, a check its own diff adds to the catalogue, and `lead-accept --checks`
  selects it at the handed head. The queue runs it before the entry is on the base.
- An uncatalogued `tests/test_*.py` module fails a named test in this repository.
- The second bullet is proven by fixture tests in `tests/test_run_delivery.py` and
  `tests/test_run_delivery_supervisor.py`, which run this checkout's `run_delivery.py`. This delivery's own hand-offs
  run the installed generation, which still refuses an added id, so they can't show it.
- The story has landed on `main` through the merge queue. The full local suite has passed on macOS with Python 3.11
  at the base head after it landed, run with `--workers 4 --budget tests/suite-budget.json`, inside the budget.
- Tagging a release is not this delivery's. The change is additive, a minor version, and goes out at the next batch
  boundary.

## Decisions

1. **The candidate's catalogue is the base's plus what the candidate adds.** A check id is accepted when the base's
   catalogue holds it. It is also accepted when the binding at the candidate's head holds it in
   `selection.catalogue`, the base's catalogue has no entry with that id, and the base names no binding check by it.
   Such an entry is an *added entry*. An id in neither catalogue is still refused, and the refusal lists both. For
   an id the base already holds, the base's definition is used, even if the candidate changes it, as today.
2. **Where the head's binding is read.** `handoff` reads `.agents/orchestration.json` at the lane checkout's `HEAD`
   with `git show HEAD:...`. `lead-accept` reads it at the handed head, with `git show <head>:...` in the lead
   checkout after its fetch. A head whose binding is missing or doesn't parse adds nothing. No working-tree file is
   read, so an uncommitted edit adds nothing.
3. **The lead selects an added entry as an object.** `lead-accept --checks` sends `select` a JSON list in the order
   given. A base catalogue id goes as its id. An added entry goes as the head binding's object, unchanged. `select`
   validates the object as it validates any object, so its refusals stand. Guards and readers join as before. The
   journal records the selection exactly as `select` writes it today.
4. **The proposal includes added entries.** `proposed_checks` also keeps a test module's id when it is an added
   entry, so `lead-accept` with no choice proposes the new module's check.
5. **The hand-off shows which ids are added.** The hand-off message marks each added id, for example
   `tests.test_new (added by this story)`, so the lead sees that the entry is part of the diff under review. The
   record keeps the ids as given.
6. **This repository catalogues every test module.** A named test in `tests/test_own_deliveries.py`,
   `test_every_test_module_is_catalogued`, asserts set equality: the module names found by
   `(ROOT / "tests").glob("test_*.py")` equal `CATALOGUE_MODULES` plus a new `UNCATALOGUED_MODULES`, and the two tuples
   are disjoint. An empty or wrong discovery therefore fails, because `CATALOGUE_MODULES` is not empty, and so does a
   stale exclusion. The failure names each module that is missing or extra. Each entry in `UNCATALOGUED_MODULES`
   gives its reason. The story
   catalogues `tests.test_suite_budget` in the binding and the pin, so the exclusion list starts empty. The existing
   assertion, that every catalogued module exists and holds a case, is kept: a story adds the module and its entry
   in the same diff.
6a. **The guard runs when a test module changes.** A reader in `.agents/orchestration.json` maps `tests/test_*.py` to
   `tests.test_own_deliveries`. A later story that adds or renames a test module then has the guard selected with
   its checks, and a missing entry fails in the queue, not on `main` after the merge.
7. **Pending entries are not added.** The ticket also offered a catalogue entry marked pending. Decision 1 makes it
   unnecessary: a story's own entry is selectable from its own head.

## Out of scope

- The state tool's `select` and its validation, which already take objects.
- Selecting a check that is in no catalogue at all, through `lead-accept`. A JSON list through the state tool's
  `select` still does that.
- A catalogue guard in `doctor` for other adopters. The guard is this repository's test.
- The orchestration guide and the lane brief template. Both belong to another live delivery on this base, V26-3553.
  This change is described in run-delivery's own `SKILL.md` and in `docs/own-deliveries.md`, and the template's line
  stays true.

## Approval Handoffs

- The operator approves this plan before `lead-setup`.
- The operator grants lanes `push` and `draftPullRequests`.

## Execution posture and sensors

Test-first. Sensors: `tests/test_run_delivery.py` for the hand-off; `tests/test_run_delivery_supervisor.py` for
acceptance, the proposal, the refusal text and the queue running the selection; `tests/test_own_deliveries.py` for the
guard, the pin and the reader; `scripts/validate-registry.py` and `scripts/update-provenance.py --check` for the catalog,
profiles and provenance. The candidate's checks: `tests.test_run_delivery`, `tests.test_run_delivery_supervisor`,
`tests.test_own_deliveries`, plus the guards.

## Scenarios

In `tests/test_run_delivery.py`, for the hand-off:

- A lane's story adds `tests/test_new.py` and its catalogue entry, and commits both. `handoff --checks
  tests.test_run_delivery,tests.test_new` is accepted. The message marks `tests.test_new` as added and does not mark
  `tests.test_run_delivery`.
- The same hand-off, with the entry only in the working tree: the `--checks` refusal names `tests.test_new`.
- A head binding that doesn't parse adds nothing: `tests.test_new` is refused, and no parse error escapes.
- `handoff --checks tests.test_nowhere` is refused, naming both catalogues.
- A head binding that adds a catalogue entry under a binding check's id, such as `validate-registry`: the id is not
  an added entry.

In `tests/test_run_delivery_supervisor.py`, next to the existing selection tests, for acceptance and the queue:

- `lead-accept --checks tests.test_two,tests.test_new` at the handed head records a selection, in that order, whose
  `tests.test_new` check equals the head binding's entry, and whose `tests.test_two` is the base catalogue's entry,
  plus the guards. `queue_run` then merges the item with its checks selected by the lead.
- `lead-accept` reads the head's committed binding: a lane checkout whose working tree differs from its head selects
  the head's entry.
- `lead-accept` with neither choice proposes `--checks` with `tests.test_new` in it. A changed test module in neither
  catalogue is still not proposed.
- A candidate that redefines an existing entry's argv and names its id: the base's definition is selected.
- The refusal of an id in neither catalogue lists both catalogues. The existing refusal assertions are updated to it.

In `tests/test_own_deliveries.py`:

- The guard asserts the set equality and disjointness of Decision 6, and the reader of Decision 6a is pinned.
- The lane's evidence records one planted-failure run: an uncatalogued `tests/test_zz_planted.py` makes
  `test_every_test_module_is_catalogued` fail naming it, and the file is then removed.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3431 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_own_deliveries.py`, `.agents/orchestration.json`, `docs/own-deliveries.md`, `catalog.json`, `profiles/*` |

`provenance.lock.json` is regenerable. The lane runs the writer, as `AGENTS.md` describes. V26-3553 is live on the
same base and edits `run_delivery.py`, `tests/test_run_delivery.py`, `.agents/orchestration.json` and
`tests/test_own_deliveries.py` in regions of its own. The lead claims this delivery's files with `lead-own --claim`
so each delivery sees the other's, and the queue's sync resolves any overlap at merge.

## Stories

### V26-3431. Let a story add its test module's catalogue entry without the lead listing it first, and catch a module nobody catalogued

Lane 1, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`,
`tests/test_own_deliveries.py`, `.agents/orchestration.json`, `docs/own-deliveries.md`, `catalog.json`,
`profiles/run-delivery.json`, `profiles/linear.json` and `profiles/jira.json`.

Implement Decisions 1 to 5 in `check_ids`, `handoff`, `lead-accept` and `proposed_checks`. Add the guard of
Decision 6 and the reader of Decision 6a, and catalogue `tests.test_suite_budget`. Describe added entries in run-delivery's `SKILL.md` command
table for `handoff` and `lead-accept`, and in `docs/own-deliveries.md`. Update the catalog entry, every selected
profile and provenance.

Acceptance criteria:

- `c1` A lane can name the test module its story creates in `handoff --checks` before the module's catalogue entry
  is on the base.
- `c2` The lead can select that check with `lead-accept --checks`, and the queue runs it at the handed head.
- `c3` An uncatalogued test module fails a named guard.
