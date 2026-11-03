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

## Checks

The binding declares no affected-checks provider, on purpose. Nearly every test here
reads the corpus or a release built from all of it, so working out what a change
affects from its paths would either select most of the suite or miss what a shared
file moves. Instead every candidate runs corpus validation, and the lead selects
each candidate's checks from its unit's sensors, as `selection.lead` allows. With no
selection the queue runs the whole suite.

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
