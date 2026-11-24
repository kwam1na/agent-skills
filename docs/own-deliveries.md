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

In the same commit, add the release id and its archive's sha256 to `ATTESTED_ARCHIVES` in
`tests/test_own_deliveries.py`. The standing-binding gate pins the installed generation to that list,
so an apply without the entry fails every check of the binding on the branch it lands on.

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

## Hosted progress

This repository opts into `https://deliveries.v26technologies.com` through
`.agents/delivery-views.json`. Each delivery has its own unlisted read-only link;
the site's root lists nothing. The installed lifecycle starts its publisher and
shows the link in its summary. Closing retains the acknowledged completed view.

`AGENT_SKILLS_DELIVERY_PUBLISH_TOKEN` is a separate repository-scoped publishing
credential, held in the login Keychain under service
`v26.hosted-delivery-publishing`, account `agent-skills`. Retrieve it through the
operator's approved Keychain access and supply the named variable in the
environment of `lead-init`, or when starting or resuming `lead-run`. A running
supervisor cannot receive a later shell export; restart it with the credential.
The token belongs only in the publishing command's environment, never in source,
arguments, delivery notes or share links. Cloudflare management credentials are
not publishing credentials. Share the delivery's `/d/…` link; anyone receiving it
can view its saved progress.

## Runs on the live view

Every delivery workflow run here, a plan, a diagnosis, a ticket or a routed
request, reports its run through the run-event command `AGENTS.md` declares, and
the binding's `state.liveRuns` has the live server watch the run store. The runs
page is the front page while no delivery record exists; a lead's delivery server
serves it too. Start or find the standing server from the repository root, after
making the state directory:

```bash
mkdir -p .orchestration/state
python3 -B .agent-skills/current/skills/orchestrate-work/scripts/render_state.py live --dir .orchestration/state --binding .agents/orchestration.json --if-absent
```

It prints the address of a running server, or serves and announces one. Stop the
one this checkout started with `--stop` in place of `--if-absent`. The run store
sits under the git common directory, so one server shows every worktree's runs.
Runs are observability: no gate, approval or finish line reads them.

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
the script it is. Every `tests/test_*.py` module is catalogued:
`test_every_test_module_is_catalogued` in `tests/test_own_deliveries.py` fails naming
each module that is neither in the catalogue's pin there nor in its
`UNCATALOGUED_MODULES`, where each exclusion gives its reason. A story that adds a
module adds its entry to the binding and to that pin in the same diff, and names the
new id in its own `handoff --checks`. An id the base's catalogue doesn't hold and the
binding committed at the story's head does is an entry the story adds: the hand-off
marks it added by this story, and `lead-accept --checks` selects it as the head
defines it, before the entry reaches the base, so the queue runs it at the handed head.

Two guards join every selection, named or not: `tests.test_linear_qualification`,
which holds the corpus pins any doc or skill change can break, and
`tests.test_registry`. A change to `skills/delivery-views/scripts/snapshot.py` also
brings `tests.test_delivery_views_adopter` and `tests.test_delivery_views_snapshots`,
the line map's outside adopter among them. A change under `docs/solutions/` brings
`tests.test_compound_learning_corpus`, which pins each note's shape. A change to
any Markdown file under `skills/` brings the six corpus tests that read the shipped
skill prose, `tests.test_run_event_emission_corpus` among them, which holds each
journal kind to the one skill that owns it. A change to any `tests/test_*.py` module
brings `tests.test_own_deliveries`, so a module added without its entry fails in the
queue. Corpus validation stays in `alwaysRun`.
The acceptance's decision names every check it selected, those added with them.

A lane's gates are in `.agents/run-delivery.json`: corpus validation and the
provenance check, both of which run on a fresh clone's standard library. The lane
brief still bars a full suite in a lane.

A failed post-merge `fullHealth` is re-checked before it breaks the base once a
release that holds the binding's `fullHealthRerun` member is installed. Until then
the binding declares no member, which the installed release would refuse. Then it
declares this one, which reads `scripts/run-tests.py`'s own output: a failed, erroring
or unexpectedly passing test is named and re-run alone, once, with `unittest`. A
crashed or timed-out unit names no test, and nor does a failing subTest or a class's
or module's fixture, whose line names more than a test id: a run with any of these is
not re-checked:

```json fullHealthRerun
{
  "argv": [".venv/bin/python", "-B", "-m", "unittest", "{tests}"],
  "failurePattern": "(?m)^(?:FAIL|ERROR|UNEXPECTED SUCCESS): (\\S+)$",
  "ranPattern": "(?m)^Ran [1-9]\\d* tests? in ",
  "unrecheckedPattern": "(?m)^(?:CRASHED|TIMED OUT): |^(?:FAIL|ERROR|UNEXPECTED SUCCESS): \\S+ "
}
```

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

The lead's own run of the full suite, the one `AGENTS.md` and the finish line ask
for before an integration reaches `main`, goes through run-delivery under any
posture, at the integration candidate, with the suite's budget:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py \
  lead-suite --at <commit> -- --budget tests/suite-budget.json
```

It runs in a detached checkout of that commit under the work folder, so an edit in
the lead's checkout while it runs changes nothing, and appends its result, with the
commit, the platform and the interpreter's version, to the flow log's
`<delivery>.suite.jsonl`. Cite that line when marking the finish line. A run over
its budget exits 3 and is recorded so.

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

Preserve only the reviewed hosted check additions after the target restore. Use
the fetched, reviewed integration ref below, replacing `<name>` as above. The
script refuses missing additions, different same-ID target entries and an altered
service reader. It retains every other target field and existing entry, including
base/remote, gates, prepare, merge strategy and posture. It changes no installed
file. Inspect and stage the resulting binding diff explicitly.

```bash
PYTHONOPTIMIZE=0 python3 - <<'PY'
import copy, json, subprocess
from pathlib import Path
binding = Path(".agents/orchestration.json")
target = json.loads(subprocess.check_output(["git", "show", "origin/main:.agents/orchestration.json"]))
source = json.loads(subprocess.check_output(["git", "show", "origin/integrate/<name>:.agents/orchestration.json"]))
assert json.loads(binding.read_text()) == target, "restore the target binding first"
service = ["hosted.install", "hosted.typecheck", "hosted.build", "hosted.test", "hosted.browser-install", "hosted.browser", "hosted.conformance", "hosted.node-test", "hosted.conformance-node"]
allowed = ["tests.test_delivery_views_hosted", "tests.test_delivery_views_hosted_projection", "tests.test_run_delivery_hosted", *service]
reader = {"paths": ["hosted/delivery-viewer/**", "hosted/protocol/**", "hosted/conformance/**", "skills/delivery-views/assets/**", "skills/delivery-views/scripts/render.py", "skills/delivery-views/scripts/hosted.py", "skills/delivery-views/scripts/views_common.py", "skills/delivery-views/references/hosted-snapshot.schema.json", "tests/fixtures/delivery-views/hosted/**", ".nvmrc", ".github/workflows/hosted-delivery-viewer.yml"], "checks": service}
assert reader in source["selection"]["readers"], "reviewed service reader differs"
merged = copy.deepcopy(target)
rows = merged["selection"]["catalogue"]
target_ids = [row["id"] for row in rows]
python_ids = [key for key in target_ids if key.startswith("tests.")]
assert python_ids == sorted(python_ids), "target Python checks are not in canonical order"
assert [key for key in target_ids if key in service] == [key for key in service if key in target_ids], "target service checks are not in reviewed order"
for key in allowed:
    candidates = [row for row in source["selection"]["catalogue"] if row["id"] == key]
    assert len(candidates) == 1, f"missing or duplicate reviewed check: {key}"
    existing = [row for row in rows if row["id"] == key]
    assert not existing or existing == candidates, f"different same-ID target check: {key}"
    if not existing:
        if key.startswith("tests."):
            at = next((i for i, row in enumerate(rows) if row["id"].startswith("tests.") and row["id"] > key), None)
            if at is None:
                at = next((i for i, row in enumerate(rows) if row["id"] in service), len(rows))
        else:
            at = next((i for i, row in enumerate(rows) if row["id"] in service and service.index(row["id"]) > service.index(key)), len(rows))
        rows.insert(at, candidates[0])
readers = merged["selection"]["readers"]
assert not any(row["paths"] == reader["paths"] and row != reader for row in readers), "different target service reader"
if reader not in readers:
    readers.append(reader)
# Everything outside these exact additions must still equal the target.
restored = copy.deepcopy(merged)
restored["selection"]["catalogue"] = target["selection"]["catalogue"]
restored["selection"]["readers"] = target["selection"]["readers"]
assert restored == target
assert all(row in rows for row in target["selection"]["catalogue"])
assert all(row in readers for row in target["selection"]["readers"])
assert [row for row in rows if row["id"] in target_ids] == target["selection"]["catalogue"]
binding.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
PY
git diff origin/main -- .agents/orchestration.json
git add .agents/orchestration.json
```

The binding diff must contain only these catalogue/reader additions; the installed
receipt, host projections, hooks and remaining standing settings still match the
target exactly as checked separately above. Do not carry the integration base or
other branch-specific operational configuration into `main`.

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

Every repository on the machine, this one and Athena alike, shares one machine
ledger (orchestrate-work's guide, "The machine ledger"). Set the machine's
capacity once, in slots, with the reason, or reset it to the core count:

```bash
python3 -B .agent-skills/current/skills/orchestrate-work/scripts/machine.py capacity --slots 8 --why "16 cores, keep half free for the editor and browsers"
python3 -B .agent-skills/current/skills/orchestrate-work/scripts/machine.py status
```

A run that needs the machine to itself, such as a timing-sensitive suite, takes
an exclusive claim. It waits for the machine to empty and nothing else starts
while it runs:

```bash
python3 -B .agent-skills/current/skills/orchestrate-work/scripts/machine.py run --exclusive --label "full suite" -- .venv/bin/python scripts/run-tests.py --workers 4 --budget tests/suite-budget.json
```

The hand-kept suite slot, and waiting on Athena's quiet-machine lease, are
retired once Athena claims through the same ledger. Until
then, keep using them beside it.

## Hosted service verification

The deployable service has its own locked package and CI workflow, described in
[hosted deliveries](hosted-deliveries.md). Shared schema, trusted assets/theme
source and hosted fixture changes select the service checks through binding
readers, and so do the protocol spec, the conformance suite and the corpus
publisher's transport (`hosted.py` and `views_common.py`). The catalogue IDs are
`hosted.install`, `hosted.typecheck`, `hosted.build`, `hosted.test`,
`hosted.browser-install`, `hosted.browser` (Chromium against the Worker and the
portable server), `hosted.conformance`, which runs the conformance suite's fast
tier against the built Worker and its planted-failure copies, `hosted.node-test`,
the portable server's tests, and `hosted.conformance-node`, which builds the
release's service bundle with `scripts/cut-release.py --service-bundle`, extracts
it under `TMPDIR` and runs the fast tier against a server built from that
extraction alone. Their dependencies keep service installation out of unrelated
Python checks. The browser install sets `PLAYWRIGHT_SKIP_BROWSER_GC=1`, so it
leaves the machine's other Playwright browsers in place. The service CI runs four
parallel jobs: these fast checks, the Worker's slow tier, the portable server's
slow tier, and the container image built from an extracted bundle, which runs
read-only against the fast tier. Docker is optional locally:
`node test/conformance/container.mjs` exits 3 and says the check is unavailable
where Docker is missing, never that it passed. Use supported Node
22.22.2+ or 24.15+ on the merge runner's PATH, and install Chromium's operating
system prerequisites there. The separate Ubuntu service CI installs them itself.
The lead must verify its exact main-push result before publishing a candidate or
deploying; the corpus's own CI jobs inform and hold neither. The service and the
publishing generation move together: a snapshot or renderer version change
deploys the service in the same step that applies the generation (hosted
deliveries, "Versions move together"). An absent/red service
job is not replaced by a passing local suite. The lead owns the budgeted macOS
Python 3.11 full suite; packaging lanes run only focused checks.

Installed-archive qualification from local builds is not published-candidate
adoption. Record the authenticated released archive and active receipt separately
from service build/deployment/asset identities, and leave actual remote acceptance
open through the live scenarios. See the hosted guide for reviewed production
configuration and the candidate/final sequence.
