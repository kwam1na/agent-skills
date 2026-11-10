# This repository's own deliveries

This repository delivers its own epics the way an adopter does: with an installed
`linear` generation at `.agent-skills/current`, a standing binding at
`.agents/orchestration.json`, and `run-delivery` doing the mechanical steps. The
commands are in `.agent-skills/current/skills/run-delivery/references/adopting.md`
from its section on a first delivery onward.

## The installed generation and the corpus

`.agent-skills/`, `.agents/skills` and `.claude/skills` are the installed product.
They are committed, so every lane resolves the same tool, and nothing edits them by
hand. Corpus validation, provenance and `cut-release` read `skills/`, `catalog.json`
and `profiles/`, so a generation is never a source for a release, and bytecode that
lands in one affects none of them. A test's corpus copy leaves it out.

Update it as an adopter does, and commit the result. Download into a new empty folder
and verify each file as `README.md` says:

```bash
gh release download <version> --repo kwam1na/agent-skills --pattern 'linear-*' --dir <new empty folder>
```

Then apply it from this repository's top level:

```bash
python3 -B <folder>/linear-<version>.zip --root . --product apply --archive <folder>/linear-<version>.zip --metadata <folder>/linear-<version>.release.json --maintenance
```

The views and the state tool a delivery runs are the generation's, so a change made
in `skills/` shows in a delivery here only after this.

## Credentials

Nothing here reads a `.env`, this repository's or another's. Export two variables in
the lead's shell, from wherever you keep such things:

- `LINEAR_API_KEY`, a personal key from Linear's settings, for the tracker snapshot.
- `GITHUB_TOKEN`, for the forge snapshot. `gh auth token` prints one for a signed-in
  `gh`, which the merge queue's `ready` and `merge` steps use as well.

```bash
export GITHUB_TOKEN="$(gh auth token)"
```

The setup check names `LINEAR_API_KEY` when it's unset. It doesn't check
`GITHUB_TOKEN`, and a missing one shows up when the forge snapshot runs. The views
scrub both from what they show.

## Tracker updates

Linear is reachable only through the host's tools here, so the binding names a host
publisher. Its outbox holds the delivery's tracker updates and moves, and the lead posts
them with those tools. The close waits on them, and the line map's Needs you box shows
what is still held.

## Checks

The binding declares no affected-checks provider, on purpose. Nearly every test here
reads the corpus or a release built from all of it, so working out what a change
affects from its paths would either select most of the suite or miss what a shared
file moves. Instead every candidate runs corpus validation, and the lead selects
each candidate's checks from its unit's sensors, as `selection.lead` allows. With no
selection the queue runs the whole suite.

The lead selects by catalogue id. `selection.catalogue` holds one check per test
module, its id the module's name, such as `tests.test_run_delivery`, so a selection
is a comma-separated list:

```bash
python3 -B .agent-skills/current/skills/orchestrate-work/scripts/delivery_state.py \
  --dir .orchestration/state select --subject <key> --binding .agents/orchestration.json \
  --head <reviewed head> --checks tests.test_run_delivery,tests.test_lead_wait \
  --reason "<why these cover the diff>"
```

Each entry runs its module through `scripts/run-tests.py --workers 4`, as the full
suite does, except `tests.test_registry`, which holds no unittest case and runs as
the script it is. A module added later is selectable by id once its entry is added
to the catalogue and to the pin in `tests/test_own_deliveries.py`. Until then, a
JSON list of full entries still selects it.

Two guards join every selection, named or not: `tests.test_linear_qualification`,
which holds the corpus pins any doc or skill change can break, and
`tests.test_registry`. A change to `skills/delivery-views/scripts/snapshot.py` also
brings `tests.test_delivery_views_adopter` and `tests.test_delivery_views_snapshots`,
the line map's outside adopter among them. Corpus validation stays in `alwaysRun`.
`select` says what it added and why, and warns when a selected check failed its last
base re-run at the current base head.

A lane's gates are in `.agents/run-delivery.json`: corpus validation and the
provenance check, both of which run on a fresh clone's standard library. The lane
brief still bars a full suite in a lane.

## Later deliveries

The standing binding's base is `main`. A delivery that lands through its own
integration base changes `base.ref` on that base, in that delivery's first commit,
and removes the change when it integrates. Its record lives in the lead's checkout,
`.orchestration/lanes/<delivery>/.orchestration/state`, which git ignores.

## Setting a machine up

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py doctor
```

Fix the first line that isn't `ok` and run it again, until it exits 0.
