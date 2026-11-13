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

Nothing here reads a `.env`, this repository's or another's. Two variables reach the
lead's shell, from wherever you keep such things:

- `LINEAR_API_KEY`, a personal key from Linear's settings, for the tracker publisher and
  the tracker snapshot.
- `GITHUB_TOKEN`, for the forge snapshot. `gh auth token` prints one for a signed-in
  `gh`, which the merge queue's `ready` and `merge` steps use as well.

An agent's shell never sees a variable exported in the operator's terminal. On macOS,
keep the key in the login keychain, where the command asks for it so it lands in no
shell history:

```bash
security add-generic-password -a "$USER" -s LINEAR_API_KEY -w
```

Then export both from `~/.zshenv`:

```bash
export LINEAR_API_KEY="$(security find-generic-password -a "$USER" -s LINEAR_API_KEY -w)"
export GITHUB_TOKEN="$(gh auth token)"
```

zsh reads `~/.zshenv` for every shell, the non-interactive one an agent's tool runs
included, and `~/.zshrc` only for an interactive one.

The setup check names `LINEAR_API_KEY` when it's unset, and this recipe is its fix here.
It doesn't check `GITHUB_TOKEN`, and a missing one shows up when the forge snapshot
runs. The views scrub both from what they show, and the publisher writes the key to no
output, log or ledger line.

## Tracker updates

The binding names run-delivery's tracker publisher, which posts this repository's
deliveries' Linear updates and moves through Linear's API with `LINEAR_API_KEY`, with
no lead action. It runs whenever the render wrapper does, so a running supervisor posts
each new journal entry on its next pass, and records it in the ledger,
`tracker-published.jsonl` beside the record. Each post carries a marker, which the
adapter reads back from Linear:

```bash
python3 -B .agent-skills/current/skills/run-delivery/adapters/tracker_linear.py markers --ticket <key>
```

A post Linear refuses shows in the line map's Needs you box.

A record a host-mode generation opened is seeded on the publisher's first run: every
entry at or below the `journalThrough` the last acknowledgement left in
`published.json` is skipped, and nothing is posted twice. The binding configures no
status moves, so starting, reviewing and closing a ticket stay with people and Linear's
GitHub integration.

## Checks

The binding declares no affected-checks provider, on purpose. Nearly every test here
reads the corpus or a release built from all of it, so working out what a change
affects from its paths would either select most of the suite or miss what a shared
file moves. Instead every candidate runs corpus validation, and the lead selects
each candidate's checks from its unit's sensors, as `selection.lead` allows, when it
accepts the candidate.

The lead selects by catalogue id. `selection.catalogue` holds one check per test
module, its id the module's name, such as `tests.test_run_delivery`, so a selection
is a comma-separated list:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py \
  lead-accept <key> --checks tests.test_run_delivery,tests.test_lead_wait --reason "<why these cover the diff>"
```

`lead-accept` refuses a candidate with neither `--checks` nor `--full-suite`, and prints a
proposed `--checks`: the catalogue ids of the test modules the diff changes, then the
checks the hand-off named. `--checks` records the selection at the handed head, through
the state tool's own validation. `--full-suite` accepts it on the fallback, the whole
suite, as the lead's recorded decision. A lane names the catalogue ids it ran with
`handoff --checks`, and they show in the hand-off and on the queue row.

Each entry runs its module through `scripts/run-tests.py --workers 4`, as the full
suite does, except `tests.test_registry`, which holds no unittest case and runs as
the script it is. A module added later is selectable by id once its entry is added
to the catalogue and to the pin in `tests/test_own_deliveries.py`.

Two guards join every selection, named or not: `tests.test_linear_qualification`,
which holds the corpus pins any doc or skill change can break, and
`tests.test_registry`. A change to `skills/delivery-views/scripts/snapshot.py` also
brings `tests.test_delivery_views_adopter` and `tests.test_delivery_views_snapshots`,
the line map's outside adopter among them. A change under `docs/solutions/` brings
`tests.test_compound_learning_corpus`, which pins each note's shape. Corpus validation stays in `alwaysRun`.
The acceptance's decision names every check it selected, those added with them.

A lane's gates are in `.agents/run-delivery.json`: corpus validation and the
provenance check, both of which run on a fresh clone's standard library. The lane
brief still bars a full suite in a lane.

## Later deliveries

The standing binding serves `main` and `integrate/*`. After applying a verified
release whose `lead-setup --help` lists `--integration`, set up an integration
base from a clean checkout on `main`:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py \
  lead-setup <delivery> --plan <approved-plan> --integration integrate/<name> \
  --authority push,draftPullRequests,readyPullRequests --granted-by "<operator>"
```

Use the lead checkout printed by setup. Its draft names the integration base and
`main` as the integration target, and its binding already points at the
integration base. Review the draft, commit the initial binding and delivery
configuration together, and push the branch. Run `--delivery <delivery> lead-init` and `--delivery <delivery> lead-brief`
there. The brief names the actual lead checkout and record location. Rerun the
same setup to resume a matching checkout. The standing-binding test recognizes
the integration binding through the matching delivery declaration even in a
detached check checkout. Under the gate posture, the supervisor runs the
binding's full suite on the integration base.

With an earlier installed release, create the lead worktree from `main` with
`git worktree add -b integrate/<name> .orchestration/lanes/<delivery> main`.
From that checkout run the same setup command with `--base integrate/<name>`
in place of `--integration integrate/<name>`. Set `base.ref` in that checkout's
binding to the integration branch and add `"integrationTarget": "main"` to the
draft before committing and pushing them together. Run the named lead commands
from that checkout and give lanes its path and record location when assigning.

For an ongoing base update, use a released installation whose help lists `lead-sync`. Configure
`baseMove.provenanceWriter` as `["python3", "-B", "scripts/update-provenance.py"]`, download and verify the
archive and metadata matching the current `main` receipt, and run from the lead checkout.

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py --delivery <delivery> \
  lead-sync --merge --archive <verified archive> --metadata <verified metadata>
```

The command parks the supervisor, merges `main`, applies its generation through the supported lifecycle,
refreshes provenance and publishes under the queue lock. Resolve named conflicts and stage the resolutions, then run
`lead-sync --merge --resume` while keeping the merge open. Its pending intent also recovers a publication whose record
update was interrupted. Keep the intent until reconciliation succeeds. Newly assigned lanes start from the
moved remote base, and recorded merged SHAs remain reachable. Record shared flake fixes as pending upstream
in a delivery decision and land the fix on `main` too.

For final integration, follow the orchestration guide's **Reviewing an
integration base for the default branch** recipe. In a clean integration review
checkout, replace the branch placeholder with the delivery's recorded base:

```bash
git fetch origin main integrate/<name>
git switch -c codex/integration-review origin/integrate/<name>
git merge --no-ff origin/main
```

Resolve substantive conflicts, then restore the standing settings and the
target's installed generation together. Include any additional host projection
paths named by the target receipt.

```bash
git restore --source=origin/main --staged --worktree -- \
  .agents/orchestration.json .agents/run-delivery.json .agents/delivery-posture.json \
  .agent-skills/ .agents/skills/ .claude/settings.json .claude/skills/
git diff --exit-code origin/main -- \
  .agents/orchestration.json .agents/run-delivery.json .agents/delivery-posture.json \
  .agent-skills/ .agents/skills/ .claude/settings.json .claude/skills/
git show origin/main:.agent-skills/active.json | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["release"]["archiveSha256"])'
python3 -c 'import json; print(json.load(open(".agent-skills/active.json"))["release"]["archiveSha256"])'
```

Require the digests to match. If a merge is in progress, finish it after resolving
the conflicts and restoring these paths. Otherwise commit the restore. Retain
the reviewed product source, tests, catalog, profiles, provenance and delivery
documents. Keep the record and operational logs in the close-out archive.
Run the integration gates and review on that exact candidate before publishing
the change request to `main`.

## Hooks

The installed generation puts two entries into the committed `.claude/settings.json`,
one under `Stop` and one under `SessionStart`. Each runs the generation's hook
dispatcher:

```bash
python3 -B "${CLAUDE_PROJECT_DIR}/.agent-skills/current/__main__.py" hook claude-code stop || exit 1
```

The dispatcher runs the hooks run-delivery declares:

- **`lead.may-stop`, on every stop.** It holds a lead's stop once while a lane works
  or a candidate is queued and no `lead-wait` listens. The hold names the delivery and
  its exact `lead-wait` command, and the delivery's journal records it as one
  `hook.held`. A lane session, a session outside a delivery, and a lead with a
  listener stop freely. This repository sets it to `enforce` in
  `.agents/run-delivery.json`, where the default is `warn`.
- **`delivery.orient`, on every session start, resume, clear, compact and fork.** It
  tells a session in a delivery its role and next command. It only ever allows.

The entries belong to the generation, so an update rewrites them and nothing else
edits them by hand. [The product generation guide](product-generation.md#claude-code-hook-entries)
says what an apply writes, and that a downgrade goes through `rollback`.

To turn a hook off, or to `warn`, for one delivery, set it in `hooks` in
`.agents/run-delivery.json` on that delivery's base. Run-delivery reads the
configuration from there:

```json
"hooks": {"lead.may-stop": "off"}
```

The standing base is `main`, so a change there reaches every delivery. A delivery
that lands through its own integration base changes `hooks` on that base in its
first commit, as it does `base.ref`, and removes the change when it integrates.
`doctor` names any unknown hook id or mode.

Claude Code runs a project's hooks only once the folder is trusted. Lead each
delivery from a trusted checkout. A `claude -p` session counts as trusted.

## Setting a machine up

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py doctor
```

Fix the first line that isn't `ok` and run it again, until it exits 0.
