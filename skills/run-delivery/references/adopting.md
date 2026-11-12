# Adopting run-delivery

This guide takes a repository from nothing to a closed delivery. Read it once as the lead, and give its lane section
to each lane. Every command and file below is in a marked example block. A first line such as
`example repo` or `example file .agents/run-delivery.json` says what the block is:

- **`example file <path>`**: a file to write at that path.
- **`example repo`**: commands to run from the repository's top level.
- **`example lane`**: commands to run from the story's own checkout.
- **`example outline`**: a shape to adapt, not to run as written.

The skill's own files are named by their installed path, under `.agent-skills/current/skills/run-delivery/`. Words in
angle brackets are yours to fill in: `<delivery-key>` is the epic's tracker key, `<story-key>` a story's, and
`<forge-repository>` the repository as the forge names it (`owner/name`).

Sections 3 to 6 walk a GitHub repository whose default branch, `main`, takes direct pushes. Each push to `main` there
is marked as one for an unprotected base. Section 7 is a repository whose default branch is protected, and section 8
says how settings and updates reach a protected base.

## 1. What the workflow does

A delivery takes an approved plan of several stories through one integration base:

- The **lead** does three things: it assigns stories to lanes, accepts each hand-off, and answers what only it or the
  operator can decide.
- Each **lane** takes one story at a time in its own checkout. It builds and tests the change, gets it reviewed by the
  delivery's lenses, opens a draft change request and hands it off.
- The **supervisor**, `lead-run`, does every other lead duty, pass after pass: the reconcile sweep, the merge queue
  and the live view, cleanup after each merge, every tracker update and move, each finish line it can prove, a current
  copy of the record outside the checkout, and the close-out.

`run-delivery` does the mechanical steps as commands. It wraps the release it is installed with: the `orchestrate-work`
state tool and merge queue, the `obtain-review` round brief and the lens charters. The record of the delivery is
`orchestrate-work`'s.

## 2. Prerequisites

- A POSIX host (Linux or macOS). The tool doesn't run on Windows.
- git 2.31 or newer, Python 3.11 or newer, `ps` and `lsof`.
- ffmpeg and ffprobe, only for stories that need before/after video evidence.
- Node at the installed runtime's `nodeMinimum` (22.6 or newer), first on `PATH`: a process and every child it
  starts runs the first `node` on its `PATH`, so an nvm Node further down doesn't count. `doctor` checks it;
  `lead-init`, `lead-run` (`--resume` included) and `lane-start` refuse without it, before they write, stop or start
  anything. A generation without a runtime asks for no Node.
- Python 3.11 or newer, both the interpreter running the tool and the first `python3` on `PATH`; `lane-start` refuses
  without them.
- `gh`, signed in, only for the GitHub forge adapter.
- The repository's own in-app build, serve and check programs, only when the delivery declares in-app checks.
- A remote the lanes push to, and a forge that takes draft change requests.

## 3. Install a release

Install a `run-delivery` release into the repository with the product installer, then commit what it installs, so
every lane's checkout of the base carries the same generation. A repository on the `linear` or `jira` profile already
has these commands: keep installing its own tracker's release, because a generation holds one profile and a
`run-delivery` release would remove the tracker's skills.

```text example outline
python3 -B /path/to/product.zip --root . --product apply \
  --archive /path/to/run-delivery.zip --metadata /path/to/run-delivery.release.json --maintenance
```

```sh example repo
git add -A
git commit -q -m "Install agent-skills"
# On an unprotected base: push it. On a protected one, push a branch and open a pull request (section 8).
git push -q origin main
```

Run the tool by its stable path, `.agent-skills/current/skills/run-delivery/scripts/run_delivery.py`, from the top of
any checkout of the base. An update to the release lands the same way, in a pull request of its own (section 8).

## 4. Configure the repository

Five kinds of file set a repository up. Each is written once and committed on the base.

**Ignored folders.** Lane checkouts, review folders and the record live under `.orchestration/`, which git must
ignore. The in-app build writes `out/` here. The host's own worktrees stay out of the repository too, but not the
host's whole folder: the installer writes the skills' links for the host there, and they belong on the base.

```text example file .gitignore
.orchestration/
out/
.claude/worktrees/
```

**Formatter and hook ignores.** A formatter or a commit hook that rewrites files must leave the delivery's generated
files alone, or each lane's checkout shows changes no lane made: the record's folder, and the lane brief `lead-brief`
writes beside the plan. List them in each tool's ignore file:

```text example file .prettierignore
.orchestration/
docs/plans/*.lane-brief.md
```

**The orchestrate-work binding** says what the merge queue runs: the base, the checks every candidate must pass, and
how a candidate is found, made ready and merged. This one checks every candidate for whitespace errors against the
base, leaving out the generated lane brief, whose layout is the tool's; put your own test command in `fallback` beside
it. The `candidate` and `merge` steps here are a git-only pair, below; section 7 merges through the forge instead.

```json example file .agents/orchestration.json
{
  "schemaVersion": "orchestration-binding/1",
  "base": {"remote": "origin", "ref": "main"},
  "workRoot": ".orchestration/work",
  "candidate": ["python3", "-B", ".agents/delivery/candidate.py", "{candidate}"],
  "prepare": [],
  "hooks": "bypass",
  "regenerable": {"paths": [], "regenerators": []},
  "postSync": [],
  "leadInputs": [],
  "profiles": {},
  "provider": null,
  "alwaysRun": [],
  "fallback": [
    {"id": "whitespace", "argv": ["git", "diff", "--check", "{baseHead}", "--", ".", ":(exclude)docs/plans/*.lane-brief.md"],
     "cwd": ".", "reasons": ["every candidate is checked for whitespace errors against the base"], "prerequisites": []}
  ],
  "checkTimeoutSeconds": 600,
  "checkTimeoutCeiling": 1200,
  "environment": {},
  "ready": ["true", "{candidate}", "{expectedHead}"],
  "merge": ["python3", "-B", ".agents/delivery/merge.py", "{base}", "{branch}", "{expectedHead}", "{baseHead}"],
  "release": [],
  "fullHealth": ["git", "fsck", "--connectivity-only"],
  "leases": [],
  "state": {"directory": ".orchestration/state", "publishers": [], "renderers": [],
            "forgeSnapshot": {"argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
                                       "--kind", "github", "--owner", "<forge-owner>", "--repo", "<forge-name>",
                                       "--binding", ".agents/orchestration.json",
                                       "--subject-pattern", "^feature/(?P<subject>[A-Z]+-[0-9]+)$",
                                       "--token-env", "GITHUB_TOKEN"]}}
}
```

`forgeSnapshot` reads the delivery's pull requests into the record; a repository that configures a forge declares
one, and `lead-init` refuses without it. A binding that reads a tracker with `trackerSnapshot` also declares a tracker
publisher in `publishers` (section 7).

A repository that delivers to more than one long-lived base, such as `main` and a run of integration branches, lists
them in the binding's optional `bases`, beside `base.ref`, which stays the default:
`"bases": ["main", "integration-*"]`. Each entry is an exact branch name, or a pattern whose `*` matches within one
segment of the name. A delivery to any base that matches then needs no edit to the binding. `lead-setup` requires
`--base` once `bases` is declared, so where the work lands is settled before any lane starts. Lanes and the merge queue
read the binding from the delivery's own base, so every base it serves carries a binding that lists it: cut a new base
after the binding gained `bases`, or commit the binding there too. [Base mismatch](#base-mismatch) covers the
refusals.

The queue's `candidate` step turns a story key into its branch and that branch's tip on the remote:

```python example file .agents/delivery/candidate.py
"""The merge queue's candidate step: a story key to its branch and the branch's tip on the remote."""

import json
import subprocess
import sys

branch = "feature/" + sys.argv[1]
tip = subprocess.run(["git", "ls-remote", "--heads", "origin", "refs/heads/" + branch],
                     capture_output=True, text=True, check=True).stdout.split()
if not tip:
    sys.exit(3)
print(json.dumps({"branch": branch, "head": tip[0]}))
```

Its `merge` step lands the checked head on the base, refusing a branch that moved after its check. It pushes to the
base, so it suits only a base that takes direct pushes:

```python example file .agents/delivery/merge.py
"""The merge queue's merge step, git only: the checked head lands on the base as a merge commit."""

import subprocess
import sys

base, branch, expected, base_head = sys.argv[1:5]


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


tip = git("ls-remote", "--heads", "origin", "refs/heads/" + branch).split()
if not tip or tip[0] != expected:
    sys.exit(f"{branch} moved after its check")
git("fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/merge-queue/candidate")
tree = git("rev-parse", expected + "^{tree}")
commit = git("commit-tree", tree, "-p", base_head, "-p", expected, "-m", "Merge " + branch)
# For an unprotected base only: a protected one merges through the forge (section 7).
git("push", "--quiet", f"--force-with-lease=refs/heads/{base}:{base_head}", "origin", f"{commit}:refs/heads/{base}")
```

**The run-delivery configuration** holds what nothing else declares: the tracker's issue address, the forge, and the
in-app adapters. Every field is optional except `schemaVersion`; `.agent-skills/current/skills/run-delivery/references/config.md`
lists them all.

```json example file .agents/run-delivery.json
{
  "schemaVersion": "delivery-flow/1",
  "tracker": {"issueUrl": "https://tracker.example.com/browse/{key}"},
  "forge": {"adapter": "github", "repository": "<forge-repository>"},
  "inApp": {
    "lease": "app",
    "lock": "~/.cache/run-delivery/app.lock",
    "port": 47613,
    "build": {"argv": ["npm", "run", "build"], "artifact": "out"},
    "serve": {"argv": ["npm", "run", "serve", "--", "--port", "{port}", "--dir", "{dir}"]},
    "check": {"argv": ["node", "tools/check.mjs", "{spec}", "{port}", "{args}"]},
    "consoleErrorPattern": "^Error",
    "checkTimeoutSeconds": 300
  }
}
```

**In-app adapters**, when the delivery checks the running app, are three commands of your own:

- `build` builds the app into `artifact`; the tool stamps the build with its commit in `.flow-build.json`.
- `serve` serves a built folder (`{dir}`) on `{port}`, including that stamp at `/.flow-build.json`.
- `check` drives a browser through a spec (`{spec}`, a JSON file) and prints one JSON report: `ready`, `build` (the
  served stamp), `held`, `failed`, `evals`, `afterEvals`, `alertsSeen`, `pageErrors` and `consoleErrors`.
  `.agent-skills/current/skills/run-delivery/references/config.md` has the whole contract.

```text example outline
check <spec file> <port> [adapter arguments]
  1. Read the spec: path, holds, fails, clicks, evals, afterEvals, for, wait, afterFor.
  2. Read /.flow-build.json from the server.
  3. Open the page, holding or failing the requests the spec names, and time each.
  4. Run the evals while held, release, then run the afterEvals.
  5. Print the report as one JSON object.
```

Sign-in values come from the environment or from the adapter itself, never from its arguments: `app-check` refuses an
argument that looks like one. Prove the adapters keep the contract on the skill's fixture page:

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py adapter-test
```

Commit the configuration:

```sh example repo
git add -A
git commit -q -m "Configure deliveries"
# On an unprotected base: push it. On a protected one, open a pull request (section 8).
git push -q origin main
```

### Checks the repository already has

**A linter that fails on old code.** A gate runs on every candidate. When the repository's linter reports problems the
delivery didn't make, lint only the files each candidate changes, so a lane is never held to code it didn't touch:

```text example outline
{"id": "lint", "argv": ["sh", "-c", "git diff --name-only --diff-filter=ACMR \"$0\"...HEAD -- '*.ts' '*.tsx' | xargs -r npx eslint", "{baseHead}"]}
```

**A red baseline.** `doctor --run-gates` runs the gates on the base, in a fresh checkout, before any lane starts. A
gate that fails there fails every candidate, and the queue reads it as a broken base. Repair the base first, or
narrow the gate as above to what candidates change, and say in the plan which old failure is out of scope. Never drop
the gate.

## 5. Run the setup check

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py doctor
```

It prints one line per check: `ok`, `missing` or `wrong` with its fix, or `skipped` with the check it waits on. Fix
the first line that isn't `ok` and run it again, until it exits 0. It fetches the base (`--offline` reads the local
copy instead) and runs Node on `harness.config.ts` when there is one; it writes nothing else. `--run-adapters` adds
the adapter proof, and `--run-gates` runs the gates on the base. With `--delivery <key>` it also checks the delivery:
every story has a plan section, every `context.sections` heading is in the plan, a configured forge or tracker has its
snapshot and publisher in the binding, Node meets the runtime's minimum, the remote base carries the configuration
lanes read, and the supervisor's state. `lead-init` refuses on each of these. [Troubleshooting](#troubleshooting)
explains each message.

## 6. A first delivery

### Plan and review

Write the approved plan, with one heading per story:

```markdown example file docs/plans/<delivery-key>.md
# <delivery-key>: a greeting on the front page

## Stories

### <story-key>. Show the greeting

- Scenarios: the page shows the greeting.
```

Review a plan before it is approved, with or without a delivery. Give it a `reviewSlug` in its front matter, so its
review survives a rename, commit it, and run `plan-review prepare` with the lenses that review it:

```text example outline
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py plan-review prepare --plan docs/plans/<delivery-key>.md --lenses lens.outcome-correctness,lens.adversarial-testing
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py plan-review reduce --plan docs/plans/<delivery-key>.md
```

`prepare` gives each lens a clean checkout of the commit and a filled brief, and prints the reviewer prompts. Save each
reviewer's result where it says, then `reduce` the round. Revise the plan for the open findings, commit, and repeat.
The review has no round bound: `reduce` says when it stops converging, and only the operator ends it, with
`plan-review end --reason TEXT`. When the plan becomes a delivery, `lead-setup` and `lead-init` adopt its review, every
round of which shows in the plan chapter's "Plan review" fold, and `lead-init` refuses to freeze the plan while the
review is open. A delivery file can name its plan's lenses itself instead, `"planReview": {"lenses": [...]}`.

### Set up

Draft the delivery file from the plan's headings:

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-setup <delivery-key> \
  --plan docs/plans/<delivery-key>.md --pattern '^### (?P<key>[A-Z]+-[0-9]+)\. (?P<title>.+)$' \
  --authority push,draftPullRequests --granted-by 'the operator'
```

`lead-setup` asks for the operator's grant to lanes before it writes anything: `--authority` names what lanes may do
(`push`, `draftPullRequests`, or both) and `--granted-by` who allowed it. `--no-authority` says lanes get none and hand
their branches to the lead. `--authority` also takes `readyPullRequests`, the lead's own grant, which a merge step that
refuses drafts needs: `lead-accept` marks each draft ready under it. The lane brief leaves it out, saying lanes never
mark a pull request ready, and `open-pr` still opens a draft.

A plan in plan-work's format (its SKILL.md, "Plan Format for a Delivery", has the grammar and an example) needs no
`--pattern`: each `### KEY. Title` under `## Stories` is a story. Its section's opening sentence, `Lane N, tier T, not
blocked.` or `Lane N, tier T, after KEY.`, gives its lane, `lane-N`, and its `after` keys. The `Units and order`
table's lowest-tier row for each lane gives that lane's `owns`, a folder `dir/` becoming `dir/*`. Each story's criteria
come from its tracker item's Acceptance Criteria section, which the tracker snapshot carries: `--tracker-snapshot F`,
or else the binding's `state.trackerSnapshot`, run for the delivery's key. `lead-setup` notes each story it left
unassigned, each lane it found no row for, and each story it drafted no criteria for, and why.

The `after` keys are the plan's order. `next` reads them without the tracker once the binding names `lead-hints` as
its `state.workHints`, with the argv `python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py
--delivery {parent} lead-hints`. It prints each story's `Files:` paths as its files and its `after` keys as its
blockers. A blocker stops blocking once the record holds it merged or settled with no change needed, or the tracker
snapshot holds it completed or canceled.

Complete the rest from the plan: the in-app checks each story's hand-off needs (`"loaded"` here: one passing check of
the running app), the finish lines, and where the record lives. A plan in another format leaves the lanes, each story's
lane and the files each lane owns to you.

Criteria turn on the live view's plan chapter, which shows whether the work is still what was agreed and what proves
it; the finish line's `label` and `covers` and the `outcome` shape it. Every key is optional; a delivery file with no
`criteria` gets no plan and today's page. Give each story the acceptance criteria the plan names, each with an id
unique across the delivery. A finish line's `covers` says which criteria it stands for (a list of ids, `"all"`, or
`[]` for none by design) and which proofs must be done for it: `merged` here; `review` and `handed`, and
`in-app-<mode>` or `clip` for the in-app checks and video evidence a story needs, are the others. The supervisor meets
each finish line by these covers and proofs (Answer, below). `outcome` is the delivery's purpose in one sentence and
in full. `lead-init` freezes all of it as the approved plan:

```json example file .agents/deliveries/<delivery-key>.json
{
  "schemaVersion": "delivery-flow-delivery/1",
  "delivery": "<delivery-key>",
  "headline": "A greeting on the front page",
  "plan": "docs/plans/<delivery-key>.md",
  "laneBrief": "docs/plans/<delivery-key>.lane-brief.md",
  "stateDirectory": ".orchestration/state",
  "lanes": {"lane-1": {"owns": ["src/*"]}},
  "stories": {"<story-key>": {"title": "Show the greeting", "lane": "lane-1", "inApp": "loaded",
                               "criteria": [{"id": "greeting-shown", "text": "The front page shows the greeting"}]}},
  "finish": [{"id": "greeting", "label": "The greeting", "text": "The front page shows the greeting",
              "covers": {"criteria": "all", "proofs": ["merged"]}}],
  "outcome": {"summary": "Visitors are greeted on the front page.",
              "text": "Visitors are greeted on the front page, and nothing else on it changes."},
  "authority": {"push": true, "draftPullRequests": true, "grantedBy": "the operator", "grantedOn": "2026-01-01"}
}
```

If the plan names approvals someone must give, or what is out of scope, have `lead-setup` draft them from those
sections: `--approvals-section "Approval Handoffs"` drafts one `approvals` entry per list item, each `waiting` with an id
`approval-1`, `approval-2` and so on, and `--out-of-scope-section "Out of scope"` drafts `outOfScope`. While completing
the file, mark each approval that blocks work `"blocking": true`; the plan chapter then says "Approvals are blocking
work", naming it, until `lead-plan approval <id> --state done` records it done and commits the delivery file. One that
is merely ahead stays in the chapter's "Approvals ahead" fold.

Commit it, check the delivery, create the record and write the lane brief. `lead-init` refuses each setup gap
`doctor --delivery` reports, then starts the supervisor and prints the live view's address; every lead command
repeats it.

A command with no `--delivery` finds its delivery once `.agents/deliveries/` holds more than one file: the delivery
whose record is open in this checkout (not in a checkout nested inside it, such as a lead's under the lanes folder);
else the one whose story branch this checkout is on; else the only delivery not known to be closed. A delivery is known to be closed when this machine's flow log holds its `closed.json`, or when the
binding's `trackerSnapshot`, run with `{parent}` as its key, shows its parent completed or canceled, so a delivery closed
from another checkout counts too. A snapshot that fails, or no tracker, leaves it not known to be closed. With none or
several left it refuses, naming each delivery not known to be closed. A `--delivery` whose file is missing is refused
naming the open delivery, and saying when the key is one of a delivery's stories:

```sh example repo
git add -A
git commit -q -m "<delivery-key>: the delivery"
# On an unprotected base: push it. On a protected one, open a pull request (section 8).
git push -q origin main
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py doctor --delivery <delivery-key>
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-init
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-brief
git add -A
git commit -q -m "<delivery-key>: the lane brief"
# On an unprotected base: push it. On a protected one, open a pull request (section 8).
git push -q origin main
```

### Assign

Assign the story, and start the lane with exactly what `lead-assign` prints. From here on, the lane works from the lane
brief:

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-assign <story-key> --lane lane-1 --title "Lane 1"
```

Before it assigns anything, `lead-assign` checks that each story's tracker item offers the first start move the
configuration names, as the kept tracker snapshot reads it. A story that isn't ready is refused, naming its status and
the move that isn't offered, and nothing is written; `--force` assigns it anyway. Neither `lead-assign` nor
`doctor --delivery`, which warns of the same stories, moves the item. Only a snapshot that lists each item's offered
moves can say (the Jira snapshot does; Linear's doesn't), so otherwise both commands print that readiness wasn't
checked, and why.

Once the delivery has started, change the plan's criteria and finish lines only through `lead-plan change`. It records
what the plan said before and after and why, edits the delivery file, and commits it; it never pushes, and it refuses
on a checkout with uncommitted changes, one behind the base or diverged from it, and a story branch. It runs in the
lead's checkout of the base, or in a lead worktree of its own (section 8). `--kind changed` or `relaxed` rewords a criterion,
`added` adds one to a story (or, for a key the delivery doesn't hold yet, the whole story, below), `removed` takes stories out of the delivery (the plan keeps them, marked removed), and
`finish` changes or adds a finish line's `covers`, and with `--text` its wording; an existing line's `--text` alone
rewords it and keeps what it covers. A change of both records two changes, what it covers and then its wording, so
the plan keeps each before and after. Push the commit so lanes see it:

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-plan change --kind relaxed --subjects <story-key> --criterion greeting-shown --text "The front page shows a greeting" --why "The operator accepted any greeting"
# On an unprotected base: push it. On a protected one, open a pull request (section 8).
git push -q origin main
```

To add a story mid-delivery, one command does it all: `lead-plan change --kind added --subjects <new-key> --title T
--text C --why W --lane L [--after KEY ...]`. It adds the story's `### KEY. Title` heading to the plan's
`## Stories`, opening with its lane sentence. It adds the delivery-file story with its `why`, its first criterion
`C` (id `<key>-1`, or `--criterion`) and its `after` keys. Then it records one `added` change carrying `W`, commits
the delivery file and the plan together, and enters the story as planned work, so `lead-assign` accepts it once you
push. Without `--why` it refuses, as it does with a `why` the plan can't carry, an unknown lane or `--after` key, or
a criterion id already taken, and it writes nothing. On a key the delivery already holds, `--kind added` adds a
criterion to that story instead.

By hand instead: add the story to the delivery file and the plan with a `why`, one line saying why it joins the
delivery, then commit and push. `lead-brief`, `lead-reconcile` or `lead-assign` registers it as planned work, and the
plan records it as an `added` change carrying that `why`, such as `"why": "Review found the gap it closes"` beside
the story's `title` and `lane`.

A story added after `lead-init` froze the plan with no `why`, or one the plan can't carry (empty, not text, over the
plan's text cap, or holding a control character), is refused by each of those commands, naming its key and the fix,
and nothing is registered; `lead-plan` refuses too, since its rebuild registers. The supervisor's sweep leaves the
story unregistered and goes on, saying so in its pass. A story already in the file when `lead-init` ran is frozen
with the plan and needs no `why`. `doctor --delivery` names a story waiting without one, and an `added` change a
record already holds with no reason, with the `lead-plan why <change-id> --why <reason>` that gives it one; the plan
chapter flags every change without one. An edit made to the criteria or `covers` by hand shows in the plan as
`unrecorded`.

### The lane delivers a story

The lane begins its story with `lane-start`, from the main checkout. It makes the story's own checkout from the base,
runs the prepare step, sets the lane's focus, and records the story's start, which the tracker's start move waits for.
First it checks the lane's Node and Python, and refuses, before it makes or records anything, when the first `node` on
`PATH` is below the runtime's `nodeMinimum`, or Python is below 3.11:

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lane-start <story-key> --base main
```

It changes and commits the story's files in that checkout. Then it runs the gates, builds, and checks the change in
the app:

```sh example lane
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py gate <story-key>
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py build <story-key>
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py app-check <story-key> greeting --mode loaded --path / --eval "document.title !== ''"
```

It prepares the review round. The first run seeds `.orchestration/review/<story-key>/context.md` from the plan and
stops until the lane replaces its TODO line under "Evidence so far" with what it ran and what it showed; then it runs
it again:

```sh example lane
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py review-prepare <story-key> --round 1
```

It prints one prompt per lens. The lane starts one reviewer per lens with its prompt, and saves each reviewer's final
JSON message, exactly as returned, to the result path printed beside it. Each reduced round shows on the live view's
line map at once, before any hand-off. Then the lane reduces the round, pushes the branch, opens its draft change
request and hands off:

```sh example lane
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py review-reduce <story-key> --round 1
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py push <story-key>
printf 'Summary\n\nWhy\n\nValidation\n' > .orchestration/pr-body.md
pr=$(python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py open-pr <story-key> --title "<story-key>: Show the greeting" --body-file .orchestration/pr-body.md | tail -n 1)
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py handoff <story-key> --pr "$pr"
```

If a lens asks for changes, the lane fixes them, commits, runs the gates again and prepares the next round. It waits
inside its turn for reviewers, gates and the pipeline, and ends the turn only to hand off, to report itself blocked, or
to ask the lead: the message `handoff` prints is its report.

A story whose criteria already hold on the base needs no change, so the lane has no branch to push and no round to
review. It runs the tests or commands that show each criterion holds, in its checkout at the base, and hands off with
that evidence instead, naming each criterion by its id: `handoff <story-key> --no-change --evidence TEXT` (or
`--evidence @FILE`). The record keeps the evidence and the base head it ran at, and the story never enters the merge
queue.

### Accept

Check the hand-off's claims. `lead-accept` verifies the head, the change request, the rounds and their evidence, the
owned files, the gates and the in-app checks, and makes you decide every blocked item. Then it releases the candidate
to the queue the supervisor keeps running:

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-accept <story-key>
```

On a no-change hand-off, `lead-accept` checks that the base still holds the head the evidence ran at (the base may have
moved on since) and that the evidence names every one of the story's criteria by id, refusing with the ids it misses.
A story with no criteria is refused until you record them. Then it settles the story: the record keeps it, closed with
no change needed, the close and the finish lines count it, and nothing is committed to the base.

When the repository's merge step refuses a draft (`forge.mergeNeedsReady`), the delivery's `authority` grants
`readyPullRequests`, and `lead-accept` marks the draft ready before the queue may take it; a failed ready leaves the
hand-off pending and names the adapter. A candidate the queue stopped, which you have since resolved, goes back with
`lead-accept --requeue <story-key>`, with no new hand-off.

A capture binds to the head its last round reviewed. When a commit after that round needs no new round, such as a
fixture-only change, carry the review to the queued head instead of asking for another round:

```text example outline
run_delivery.py lead-carry-review <story-key> --from <reviewed-head> --to <queued-head> --reason "<why no new round>"
```

It prints the files changed between the two heads, the diff your judgement rests on, and then journals the carry
through the state tool's `carry-review`. A `--to` that isn't the story's queued head, a capture whose last round isn't
aligned, or one that leaves a P0 or P1 open is refused with the state tool's own reason, and nothing is written.

### Answer

Everything else is the supervisor's. Each pass it reconciles the forge and the tracker, cleans up each merged story
once its lane has moved off it, journals `finish.met` for each finish line it can prove, renders the views and posts
the tracker's updates, and copies the record to `latest` at the archive location. It judges each finish line by the
record's covers and proofs: once every criterion a line covers has every proof it names done, the line is met and
journalled once. An entry you journalled by hand is skipped. A line the record can't prove holds the close until you
check it, mark it met and journal `finish.met`.

What waits on you is what holds the close, and every lead command ends by saying so: "the close waits on" whatever the
supervisor's last pass found, as `doctor --delivery` does too. Each pass writes the same list to the flow log's
`<delivery-key>.supervisor.jsonl`. It names a story not yet merged, a finish line not met, a lane still busy, and
"Needs you": a failed `fullHealth`, a broken base, a lane that is blocked or waits on you, a merge made before you
accepted it (its checkout stays until you run `lead-accept` or journal a decision), an unresolved blocker, a
child process that won't stay up, and the tracker publisher's own items. The line map's Now chapter shows the same
"Needs you" from the supervisor's last pass, what holds the close first, each item with where it comes from and what
clears it. While the supervisor isn't running, or its list is a pass overdue, the chapter says the list is
unavailable. Resolve a blocker, or rerun a `fullHealth` you judge flaky, with `lead-resolve`:

```text example outline
run_delivery.py lead-resolve <journal id> --why "<what was decided>"
run_delivery.py lead-resolve full-health --why "<why it is flaky>"
```

When a lane has no story left, tell it so; it sets its focus `done`, as its brief shows:

```sh example repo
python3 -B .agent-skills/current/skills/orchestrate-work/scripts/delivery_state.py --dir .orchestration/state focus --lane lane-1 --text "<story-key> merged; nothing left" --state done
```

When every story is merged or removed, every merged story's checkout is cleaned up, every finish line is met, every
lane is idle or done, and nothing in "Needs you" holds it, the supervisor closes the delivery itself, in order: `fullHealth` once per base head (skipped when the
base declares the `focused` posture, and already run at that head when the delivery merged anything, since the
supervisor runs it after each merge or batch), a follow-up for
each deferred finding, `delivery.closed` and then a wait until the tracker has every update, the archive, a friction
note stub beside
it for your `compound-learning` pass, and then it stops. There is nothing to run for the close. `lead-close` is for
closing early, or with the supervisor down. With the lead's checkout gone, the archived copy still serves the live
view:

```text example outline
run_delivery.py view --archive ~/.local/share/run-delivery/archive/<repository>/<delivery-key>/latest
```

To turn the close-out's `fullHealth` off while a repository's broad gate is being worked on, commit
`.agents/delivery-posture.json` to the base, with `"schemaVersion": "delivery-posture/1"`, `"posture": "focused"` and a
`reason`. Nothing else needs changing, and the binding keeps its `fullHealth` so that `"posture": "gate"`, or removing the
file, restores it. A branch or your checkout declaring it does nothing until it reaches the base. If you already have
a file like it under another `schemaVersion`, change that member: any other value reads as malformed, so as `gate`. See
"The delivery posture" in `config.md`.

A story marked for video evidence adds the evidence page after it merges, and a delivery whose base carries tooling
that must not ship builds its product-only branch with `ship`:

```text example outline
run_delivery.py lead-evidence record <story-key> --after-build <stamped build of the merged base>
run_delivery.py lead-evidence page --out <folder>
run_delivery.py lead-evidence published --url <address> --out <folder>
run_delivery.py ship
```

`lead-evidence page` keeps its `--title`, `--intro` and `--note KEY=TEXT` words in the record, in
`evidence-notes.json`, and reuses them each time the page is rebuilt, so a later rebuild says what the first one did.
A flag given again replaces what was kept, and `--note KEY=` with no text removes that story's note.

## 7. A worked adopter

A repository on Bitbucket, with Jira as its tracker, a pipeline that reviews each pull request, a default branch,
`main`, that only takes merged pull requests, and long-lived integration bases it delivers to. Each setting below is
one this repository changes from the walk-through above; everything else stays as there.

| Setting | Default | This repository |
| --- | --- | --- |
| `base.ref`, `bases` | `main`, none | `main`, `["main", "integration-*"]` |
| The binding's `candidate` | a script of your own | the forge adapter's `candidate`, by key |
| The binding's `merge` | a git-only script | the forge adapter's `merge`, by branch |
| `environment` | `{}` | the adapter's repository and token variable |
| `forge` | `github` | `bitbucket`, with `mergeNeedsReady` |
| `forgeSnapshot` | `--kind github` | `--kind bitbucket` |
| `trackerSnapshot`, `publishers` | none | Jira's snapshot, and run-delivery's tracker publisher |
| `tracker` | an issue address | Jira's adapter, and its moves |
| `review.pr` | none | the pipeline's review |
| The binding's `reviewGate`, `checkTimeoutCeiling` | none, `1200` | run-delivery's `pr-review-current`, `1900` |
| `reviewWhen` | none: lanes make the review move by hand | `pipeline-green` |
| `authority` | `push`, `draftPullRequests` | adds `readyPullRequests` |

The binding finds each story's `feature/<key>` branch and merges it through the forge adapter, by branch and checked
head, so neither a candidate script nor a merge script of its own is needed and nothing pushes to `main`. Because it serves several bases, it passes `--destination {base}` too, so a pull request
retargeted to another base in the forge is refused rather than merged there:

```json example outline
{
  "base": {"remote": "origin", "ref": "main"},
  "bases": ["main", "integration-*"],
  "candidate": ["python3", "-B", ".agent-skills/current/skills/run-delivery/adapters/forge_bitbucket.py", "candidate",
                "--key", "{candidate}"],
  "merge": ["python3", "-B", ".agent-skills/current/skills/run-delivery/adapters/forge_bitbucket.py", "merge", "--source", "{branch}",
            "--head", "{expectedHead}", "--strategy", "merge", "--destination", "{base}"],
  "reviewGate": {"argv": ["python3", "-B", ".agent-skills/current/skills/run-delivery/scripts/run_delivery.py",
                          "pr-review-current", "--branch", "{branch}", "--head", "{expectedHead}", "--wait"],
                 "timeoutSeconds": 1900},
  "checkTimeoutCeiling": 1900,
  "environment": {"FLOW_FORGE_REPOSITORY": "<workspace>/<repository>", "FLOW_FORGE_TOKEN_ENV": "BITBUCKET_TOKEN"},
  "state": {"directory": ".orchestration/state", "renderers": [],
            "forgeSnapshot": {"argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
                                       "--kind", "bitbucket", "--workspace", "<workspace>", "--repo", "<repository>",
                                       "--binding", ".agents/orchestration.json",
                                       "--subject-pattern", "^feature/(?P<subject>[A-Z]+-[0-9]+)$",
                                       "--token-env", "BITBUCKET_TOKEN"]},
            "trackerSnapshot": {"argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
                                         "--kind", "jira", "--site", "https://tracker.example.com", "--parent", "{parent}",
                                         "--user-env", "JIRA_USER", "--token-env", "JIRA_TOKEN"]},
            "publishers": [{"id": "tracker", "argv": ["python3", "-B",
                            ".agent-skills/current/skills/run-delivery/scripts/tracker_publisher.py",
                            "--state-dir", "{stateDirectory}"], "timeoutSeconds": 600}]}
}
```

The run-delivery configuration names the forge, the tracker's moves and the pipeline's review:

```json example outline
{
  "schemaVersion": "delivery-flow/1",
  "forge": {"adapter": "bitbucket", "repository": "<workspace>/<repository>", "tokenEnv": "BITBUCKET_TOKEN",
            "mergeNeedsReady": true},
  "tracker": {"issueUrl": "https://tracker.example.com/browse/{key}", "adapter": "jira",
              "transitions": {"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pipeline-green",
                              "lead": {"candidate.merged": ["Complete"]}, "targets": {"Complete": "Done"}}},
  "review": {"pr": {"author": "<the pipeline's reviewer>", "waitSeconds": 1800}}
}
```

With `"reviewWhen": "pipeline-green"`, a lane's `pr-review <story-key> --wait` makes the review move once the pipeline
is green at the change request's head, and `lead-accept` requires the pipeline's review current there, triaged by the
lane. The binding's `reviewGate` checks it once more just before each merge:
`pr-review-current --branch {branch} --head {expectedHead} --wait` exits 0 only when `review.pr`'s review is current and its pipeline passed at the head the queue
is about to merge. It waits up to `waitSeconds` for a review still to come, and otherwise exits non-zero with one line
on stderr saying why: no `review.pr`, an adapter with no `reviews` command, no open PR, the PR at another head, a failed
pipeline, or no review after a passing one. The queue then stops the candidate with `review_not_current`. Give the gate
a `timeoutSeconds` above `waitSeconds`, within the binding's `checkTimeoutCeiling`, so a slow review is reported as
missing rather than killed. Jira gets every update in the orchestration guide's table, posted once each by the tracker
publisher from its own ledger, and the start move at each `lane-start`. A delivery to an integration base names it at
setup, which `bases` then requires:

```text example outline
run_delivery.py lead-setup <delivery-key> --base integration-2 --plan docs/plans/<delivery-key>.md \
  --pattern '^### (?P<key>[A-Z]+-[0-9]+)\. (?P<title>.+)$' --review-when pipeline-green \
  --authority push,draftPullRequests,readyPullRequests --granted-by 'the operator'
```

The grant includes `readyPullRequests`, since the merge step refuses drafts. Every settings
change and the delivery file reach `main`, and each integration base, through a reviewed pull request (section 8).

## 8. Running deliveries

- **Settings land through reviewed pull requests.** On a protected base, the binding, `.agents/run-delivery.json`, the
  delivery file and the lane brief reach the base through a pull request, reviewed and merged like any change, before
  the lanes that read them start. Lanes read them from the base, never from the lead's checkout.
- **An install update goes in its own pull request.** Update the release in a pull request of its own, merged before
  any delivery uses it. Parallel deliveries each carry their own tooling update this way: none rides in a story's
  branch, and none waits for another delivery's.
- **After an install update or a token change, `lead-run --resume`.** The supervisor runs the code and the environment
  it started with. Stop it by the pid every lead command shows (`kill <pid>`), then `lead-run --resume` stops what it
  left running and starts it on the new release, or with the new token exported. Nothing it did is done twice. The
  live view outlives the stop: the resumed supervisor replaces a view of the old release on the same port, and keeps
  one of the same release, so the address the operator has open stays the same.
- **Merge strategy.** Merge with merge commits. After each merge, the queue checks that the base holds the checked
  head's tree on top of the base it checked against, and a squash passes that too. But a squashed story's branch never
  reaches the base, so the supervisor's cleanup reports it unmerged and leaves its checkout and branches for you, and
  the supervisor's close waits on "not cleaned up: <story>" until you remove that checkout.
  Squash, with the adapter's `--strategy squash`, only where the forge requires it.
- **A test account that allows one session at a time.** The in-app lease serializes every lane's checks of the running
  app, so one account signs in for one check at a time. Give the lease's lock a path every checkout shares.
- **Lanes create their own worktrees.** `lane-start` makes each story's checkout from the delivery's base, so a lane
  never edits the lead's checkout or another lane's, and the lead reads a lane's gate and in-app evidence from that
  checkout at acceptance.
- **The lead works from a checkout of the delivery's base.** On a base the binding serves, the lead's commands read the
  settings from its own working tree; anywhere else, they read the base on the remote. Keep the lead's checkout on the
  delivery's base, current with the remote.
- **Several deliveries on one machine: one lead worktree each.** Git checks a branch out in one worktree only, so
  when the operator's checkout already holds the base, each delivery's lead works in a worktree of its own on its
  own branch, such as `.orchestration/lanes/<delivery>` on `lead/<delivery>`, made from the base on the remote and
  brought up to date with `git merge --ff-only origin/<base>`. No lead commits in the operator's checkout or in
  another lead's. `lead-plan change` and `lead-plan approval` run there when its HEAD is the base's tip, or ahead of
  it with that delivery's own delivery file and plan commits only, a clean merge of the base among them: they read the
  worktree's own delivery file, commit it there, and print the push, `git push origin HEAD:<base>`, which you run. A
  worktree behind the base, diverged from it, ahead with any other commit (another lead's, or a merge that adds a file
  of its own), on any delivery's story branch, or with uncommitted changes is refused, naming the reason.
  `doctor --delivery` reads a lead worktree as it reads the base's checkout.
- **The start move happens at each `lane-start`.** A story's tracker start waits for its lane to begin it: assigning a
  story moves nothing, and reassigning one already past started never moves it back.

## 9. Host notes

No command calls a host's own interface. What the workflow can't do for you:

- **Starting lane sessions.** A lane is always a session of its own, never a subagent and never the lead working in its
  own session. The lead starts each with what `lead-assign` prints. When the host can't start or message sessions, no
  lane starts: the lead records the blocker and brings it to the operator, who may choose to deliver the stories one at a time
  through `$execute-work`.
- **Archiving lane sessions.** The close-out archives the record, never a session; the friction note lists archiving
  them as yours.
- **Queued lead messages.** A lane's message waits until the lead's session reads it. The record doesn't wait: a lane
  that is blocked or waits on the lead holds the close, and every lead command names it, from the supervisor's next
  pass after it says so in its focus; a hand-off is in the queue as soon as it is made.
- **Pull requests the host can't watch.** A host that watches only some forges' pull requests leaves the rest to the
  supervisor: its forge snapshot and the pipeline's review reach the record and the lanes without the host.
- **Session limits.** A host's session limit ends a lane's turn mid-story. Its checkout, branch and record remain;
  start it again from the lane brief and `lane-start`, which changes nothing the second time.

Where a host offers less otherwise, the workflow takes the path on the right:

| Host behaviour | Without it |
| --- | --- |
| Publishing the evidence page | A local page the operator shares |
| Tracker moves | `tracker.updates` manual: the commands print each move and post for the lane or lead to make |

## 10. Operating rules

- **Lanes are sessions of their own.** Each lane runs as its own session the operator can open, watch and steer, never
  as a subagent and never as the lead working in its own session. A host that can't start or message sessions stops, as
  the host notes say. Lens reviewers stay subagents of the lane.
- **Lanes never merge the base.** A lane reads the delivery's settings, plan and brief from the base on the remote, so
  the lead's pushed changes reach it without a merge; merging the base would move its reviewed head and cost a review
  round. The merge queue syncs each branch with the base when it merges.
- **Review evidence is captured at hand-off.** `handoff` captures each round's review evidence; without it the line map
  shows no rounds for the story.
- **Restart long-running commands after an update.** The supervisor, `lead-run`, and the merge queue's `serve` and the
  live view it keeps, run the code they started with. After an update, stop the supervisor by its pid, then
  `lead-run --resume`, which also replaces the old release's live view on its port.
- **Start the merge queue with default signals.** The supervisor starts `serve` with SIGINT, SIGHUP and SIGQUIT at
  their defaults, whatever its own parent ignored. A `serve` started by hand needs the same: in its own terminal or
  under a process manager, never with `nohup` or a background `&`, whose ignored signals its checks inherit, so
  signal-handling tests fail as a broken base.
- **The evidence page follows each marked story's merge.** After each story marked for video evidence merges, record
  any missing clip, rebuild the page, publish it privately at the same address, and record it with
  `lead-evidence published`; the page shows application content.
- **Sign-in values stay out of commands.** They come from the environment or the adopter's own adapter, never from a
  command argument, a record, a log, an archive or a published page.

## Troubleshooting

### The setup check

`doctor` prints one line per check. Each entry below starts with the words a failing line carries, and says what to
do. A line marked `skipped` names the check it waits on: fix that one first.

- **`this host lacks`**: install what the line names. The tool needs git 2.31 or newer, Python 3.11 or newer, `ps` and
  `lsof`, on a POSIX host.
- **`which RUN_DELIVERY_CONFIG names`**: the variable points at a file that doesn't exist. Point it at the
  configuration file, or unset it to use `.agents/run-delivery.json`.
- **`no .agents/run-delivery.json`**: write the repository's configuration. A repository that needs no settings of its
  own still writes the file, with only its `schemaVersion`.
- **`the repository's configs disagree`**: two sources declare the same setting differently. `run_delivery.py config`
  shows every setting and the file it came from; change one side so they agree.
- **`earlier names in use`**: rename `.agents/flow.json` to `.agents/run-delivery.json`, and each `DELIVERY_FLOW_`
  variable to its `RUN_DELIVERY_` name. The earlier names still work until then.
- **`no receipt at`**: the repository has no installed release. Install one, and run the tool from it.
- **`is neither the current nor the previous generation`**: the tool runs from a generation the repository's receipt
  doesn't name. Run it as `.agent-skills/current/skills/run-delivery/scripts/run_delivery.py`.
- **`is the receipt's previous one`**: an older generation is still running after an update; its briefs carry the
  older release. Restart the command from `.agent-skills/current`.
- **`no .agents/orchestration.json`**: write the orchestrate-work binding.
- **`isn't JSON`**: the file the line names (the binding, the configuration, the receipt or the delivery file) has a
  syntax error. Correct it; reinstall the release for a broken receipt.
- **`doesn't validate`**: correct the binding against `orchestration-binding/1`; the line names the first problems.
- **`no delivery base`**: set `base.ref` (and `base.remote`) in the binding.
- **`the delivery base`**: the base branch isn't on the remote (or, with `--offline`, isn't fetched yet). Push it.
- **`binding doesn't serve the base`**: see [Base mismatch](#base-mismatch).
- **`has no .agent-skills/current`**, **`isn't a link inside the repository`**, **`doesn't carry`** or
  **`carries generation`**: commit the installed release (`.agent-skills`, with its `current` link) on the delivery base
  and push it. Lanes resolve the tool from their own checkout of the base.
- **`no gates are declared`**: declare the binding's fallback checks, or set `"gates": "none"` in
  `.agents/run-delivery.json` if the repository truly has none.
- **`these don't resolve`**: a gate or prepare program isn't on `PATH` or in the repository. Install it, or correct the
  binding.
- **`isn't ignored by git`**: add the lane checkout folder to `.gitignore`, so lane checkouts don't show as untracked.
- **`no tracker issue URL`**: set `tracker.issueUrl`, with `{key}` where the story key goes.
- **`review.pr is configured, but`**: the forge adapter can't fetch reviews, so hand-offs would wait on a review that
  never comes. Remove `review.pr`, or use an adapter that answers `reviews`.
- **`gh isn't installed`** or **`gh isn't signed in`**: the GitHub adapter works through `gh`. Install it and run
  `gh auth login`.
- **`no token variable for the Bitbucket adapter`** or **`the Bitbucket adapter reads its token from it`**: export the
  variable the line names, holding a token Bitbucket accepts as a bearer token. The value never goes in a file or an
  argument.
- **`forge find failed`**: the forge adapter didn't answer. Check its settings and that the forge is reachable, or run
  the check with `--offline`.
- **`the tracker snapshot or adapter reads`**: export the tracker variables the line names.
- **`the binding names a publisher that runs nothing`**: an earlier generation's publisher with a `kind` and no `argv`.
  Before you switch, post what its outbox still holds and `ack` it under that generation, so nothing it posted is
  posted again. Then replace it in `state.publishers` with run-delivery's tracker publisher (`lead-setup` adds it),
  export the tracker's token (`LINEAR_API_KEY` for Linear), and commit the binding.
- **`these in-app adapters don't resolve`**: install the build, serve or check program the line names, or correct
  `inApp`.
- **`isn't writable`**: make the in-app lease's folder or the baseline folder writable, or point `inApp` elsewhere.
- **`the in-app adapters break the contract`**: the check adapter reports something the rules can't trust. The line
  names each failing fixture case; `adapter-test` prints them one by one.
- **`lease held by`**: a lane is running an in-app check. Run `doctor --run-adapters` again when it is done.
- **`video evidence needs both`**: install ffmpeg, which brings ffprobe. `app-video` refuses with the same message.
- **`which video evidence uses`**: install an ffmpeg build with the filters and encoder the line names.
- **`needs Node 22.6 or newer`**: install Node 22.6 or newer, which reads `harness.config.ts`.
- **`no runtime loader`**: install a product release; it carries the runtime the harness configuration loads through.
- **`harness.config.ts not read`**: correct `harness.config.ts`, or set `"harness": "ignore"` to skip it.
- **`is missing (lead-setup writes it)`**: the delivery file doesn't exist yet. `lead-setup` drafts it.
- **`stories without a lane`**: give each story a lane listed in `lanes`.
- **`lanes own the same files`**: make each lane's `owns` disjoint, so no file belongs to two lanes.
- **`isn't offered; refine it first`**: the story's tracker item is in a status its first start move isn't offered
  from, as the tracker snapshot reads it, so its lane can't start it. Have the item made ready (in the tracker, by
  whoever owns that status), then assign it. `lead-assign` refuses the story until then, and `--force` assigns it
  anyway. Neither command moves the item. A snapshot that doesn't list a story's offered moves, or no snapshot at all,
  leaves readiness unchecked, and both commands say so.
- **`fails on`**: a gate fails on the delivery base itself. Repair the base before any lane starts.
- **`isn't a valid delivery-plan/1`**: the stories' criteria and the finish lines' covers make a plan the view can't
  read. Correct the delivery file at the key path the line names.
- **`the binding has no state.forgeSnapshot`**: the repository configures a forge, but nothing reads its pull requests
  into the record. Add `state.forgeSnapshot` to the binding; delivery-views' `snapshot.py` prints one for GitHub and
  Bitbucket.
- **`the binding has no tracker publisher`**: the binding reads the tracker, but nothing posts the delivery's updates
  and moves to it. A tracker with an adapter, such as Jira or Linear, needs run-delivery's tracker publisher in
  `state.publishers`; `lead-setup` adds it, so commit that. Another tracker needs its own adapter, `tracker.argv`,
  and the same publisher. Or set `tracker.updates` to `"manual"` in `.agents/run-delivery.json` to post them by
  hand.
- **`and the runtime needs Node`** or **`is below the runtime's nodeMinimum`**: the first `node` on `PATH` is missing
  or older than the runtime's minimum. Put the `bin` folder the line names first on `PATH` (an nvm Node that meets the
  minimum), or install Node at the version the line names or newer and put its `bin` folder first. `doctor`,
  `lead-init`, `lead-run` and `lane-start` all say this the same way.
- **`below 3.11`** from `lane-start`: the Python running the tool, or the first `python3` on `PATH`, is older than
  3.11. Run the tool with Python 3.11 or newer, and put its `bin` folder first on `PATH`.
- **`it runs Node`**, **`it runs with no node on its PATH`** or **`it recorded no Node`**, on the delivery supervisor's
  line: the running supervisor recorded the Node first on its `PATH` as it started, which its queue's checks run unless
  their profile puts another first. Below the runtime's minimum, or none, it is `wrong`; an older generation's
  supervisor records none, which is `warn`. Stop it by its pid (`kill <pid>`), then
  `lead-run --resume` with the right Node first on `PATH`.
- **`stories with no plan section`**: each story needs a plan heading that names its key; its lane's review context is
  that section. Add the heading, or correct the story's key. `lead-assign` refuses the story until then.
- **`context.sections the plan lacks`**: add the heading to the plan, or remove it from `context.sections`.
- **`no plan at`**: commit the plan where the delivery file's `plan` says, or correct `plan`.
- **`has no configuration for lanes`**: lanes read `.agents/run-delivery.json` and the delivery file from the remote
  base. Commit them on the base and push it. A local setup, whose lanes read their own working tree, sets
  `RUN_DELIVERY_LOCAL=1` instead.

- **`forge.mergeNeedsReady says the merge step refuses a draft`**: the merge step refuses a draft pull request, and
  the delivery gives the lead no grant to mark one ready. Add `"readyPullRequests": true` to the delivery file's
  `authority`, or draft it again with `lead-setup --force --authority push,draftPullRequests,readyPullRequests`;
  `lead-accept` then marks each draft ready before the queue takes it.
- **`the supervisor is down`**: the supervisor stopped, was killed, or was never started. `lead-run --resume` stops
  whatever it left running and starts it again, and it skips what is already done. Meanwhile every lead command still
  works by hand.
- **`supervisor: down (<why>)`**: every lead command's status line says why the supervisor stopped, as
  `supervisor.json` records it, and the live view's address while the view still serves; the view's page shows the
  same in a banner. `stopped without recording why` is what a killed supervisor leaves, and `not started` what `lead-init --no-supervisor` leaves. `lead-run --resume` starts it
  again and adopts the view, so the address stays the same.
- **`it wanted port`**: another process held the live view's port, so the view serves on a free one, which is
  recorded and kept by later restarts. The line names the port it wanted and who held it. To have the view back on
  its own port, stop that process, stop the view by the pid `live.json` records, then `lead-run --resume`.
- **`don't have the default signal dispositions`**: a child of the supervisor ignores SIGINT, SIGHUP or SIGQUIT, and
  its checks would inherit that and fail as a broken base. Stop the supervisor, then `lead-run --resume`. On a host
  without `/proc`, the line says it can't read them.

### Refusals before a delivery starts

`lead-init` refuses on every failing line above that concerns the delivery, and lists each with its fix; `doctor
--delivery` reports the same. It also refuses when the state directory already holds a record, or the flow log holds
a plan snapshot or a supervisor's lock: the line names the delivery, its base and when it started. A `lead-plan change`
made before `lead-init` doesn't count. `lead-init --replace` stops that supervisor, confirms its process is gone, moves
the record and the flow log into the archive (`cleanup.archive`), and starts afresh, carrying nothing over: the plan
it approves is the delivery file as it stands.

### Base mismatch

A delivery's base must be the binding's `base.ref` or match one of its `bases`. That holds for the delivery file's
`baseBranch`, `lead-setup --base`, a lane's base, and the merge queue's `--base`. Each refuses any other base, naming
the bases the binding serves:

- **To deliver to that base**, add it, or a pattern that matches it, to the binding's `bases`. In a pattern, `*`
  matches within one segment of the branch name, so `integration-*` serves `integration-2` but not
  `integration-2/fix`, and `release/1` needs its own entry or `release/*`.
- **A base cut before its binding gained `bases`** still holds the old binding, and lanes and the merge queue read the
  binding from the base itself. `doctor --delivery <key>` says so on its release line. Commit the binding with `bases` on that base and
  push it.
- **A lane** reads the delivery's settings from `<remote>/<base>`. The base comes from the base the lane was started
  on, which its checkout records, or from `RUN_DELIVERY_BASE`. Without either, it reads the binding's `base.ref`, and a
  delivery committed only on another base is missing there: set `RUN_DELIVERY_BASE` to the delivery's base.
- **The lead** reads its own working tree while its checkout is on a base the binding serves.

### The merge queue

- **Only signal-handling tests fail in the queue's checks, and the base is marked broken**: a `serve` started by hand
  had SIGINT, SIGHUP or SIGQUIT ignored, as `nohup` and a background `&` leave them, and its checks inherited that.
  Stop it and let the supervisor run the queue, or start it with those signals at their defaults, then journal
  `base.repaired`.
