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

## 1. What the workflow does

A delivery takes an approved plan of several stories through one integration base:

- The **lead** sets the delivery up, assigns stories to lanes, checks each hand-off, merges through the merge queue,
  and closes the delivery out.
- Each **lane** takes one story at a time in its own checkout. It builds and tests the change, gets it reviewed by the
  delivery's lenses, opens a draft change request and hands it off.

`run-delivery` does the mechanical steps of both as commands. It wraps the release it is installed with: the
`orchestrate-work` state tool and merge queue, the `obtain-review` round brief and the lens charters. The record of the
delivery is `orchestrate-work`'s.

## 2. Prerequisites

- A POSIX host (Linux or macOS). The tool doesn't run on Windows.
- git 2.31 or newer, Python 3.11 or newer, `ps` and `lsof`.
- ffmpeg and ffprobe, only for stories that need before/after video evidence.
- Node at the installed runtime's `nodeMinimum` (22.6 or newer), on `PATH` or under nvm. `doctor` checks it and
  `lead-init` refuses without it.
- `gh`, signed in, only for the GitHub forge adapter.
- The repository's own in-app build, serve and check programs, only when the delivery declares in-app checks.
- A remote the lanes push to, and a forge that takes draft change requests.

## 3. Install a release

Install a `run-delivery` release into the repository with the product installer, then commit what it installs, so
every lane's checkout of the base carries the same generation:

```text example outline
python3 -B /path/to/product.zip --root . --product apply \
  --archive /path/to/run-delivery.zip --metadata /path/to/run-delivery.release.json --maintenance
```

```sh example repo
git add -A
git commit -q -m "Install agent-skills"
git push -q origin main
```

Run the tool by its stable path, `.agent-skills/current/skills/run-delivery/scripts/run_delivery.py`, from the top of
any checkout of the base. After an update, commit the new generation the same way.

## 4. Configure the repository

Four kinds of file set a repository up. Each is written once and committed on the base.

**Ignored folders.** Lane checkouts, review folders and the record live under `.orchestration/`, which git must
ignore. The in-app build writes `out/` here.

```text example file .gitignore
.orchestration/
out/
```

**The orchestrate-work binding** says what the merge queue runs: the base, the checks every candidate must pass, and
how a candidate is found, made ready and merged. This one checks every candidate for whitespace errors against the base;
put your own test command in `fallback` beside it. The `candidate` and `merge` steps here are a git-only pair, below;
a repository that merges through its forge points them at that instead.

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
    {"id": "whitespace", "argv": ["git", "diff", "--check", "{baseHead}"], "cwd": ".",
     "reasons": ["every candidate is checked for whitespace errors against the base"], "prerequisites": []}
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
one, and `lead-init` refuses without it. A binding that reads a tracker with `trackerSnapshot` also declares a
tracker publisher in `publishers`.

A repository that delivers to more than one long-lived base, such as `main` and a run of integration branches, lists
them in the binding's optional `bases`, beside `base.ref`, which stays the default:
`"bases": ["main", "integration-*"]`. Each entry is an exact branch name, or a pattern whose `*` matches within one
segment of the name. A delivery to any base that matches then needs no edit to the binding. `lead-setup` requires
`--base` once `bases` is declared, so where the work lands is settled before any lane starts. Lanes and the merge queue
read the binding from the delivery's own base, so every base it serves carries a binding that lists it: cut a new base
after the binding gained `bases`, or commit the binding there too.

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

Its `merge` step lands the checked head on the base, refusing a branch that moved after its check:

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
git push -q origin main
```

## 5. Run the setup check

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py doctor
```

It prints one line per check: `ok`, `missing` or `wrong` with its fix, or `skipped` with the check it waits on. Fix
the first line that isn't `ok` and run it again, until it exits 0. It fetches the base (`--offline` reads the local
copy instead) and runs Node on `harness.config.ts` when there is one; it writes nothing else. `--run-adapters` adds
the adapter proof, and `--run-gates` runs the gates on the base. [Troubleshooting](#troubleshooting) explains each
message.

## 6. A first delivery

### The lead sets it up

Write the approved plan, with one heading per story:

```markdown example file docs/plans/<delivery-key>.md
# <delivery-key>: a greeting on the front page

## Stories

### <story-key>. Show the greeting

- Scenarios: the page shows the greeting.
```

Draft the delivery file from the plan's headings:

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-setup <delivery-key> \
  --plan docs/plans/<delivery-key>.md --pattern '^### (?P<key>[A-Z]+-[0-9]+)\. (?P<title>.+)$' \
  --authority push,draftPullRequests --granted-by 'the operator'
```

`lead-setup` asks for the operator's grant to lanes before it writes anything: `--authority` names what lanes may do
(`push`, `draftPullRequests`, or both) and `--granted-by` who allowed it. `--no-authority` says lanes get none and hand
their branches to the lead.

Complete it from the plan: the lanes and the files each owns, each story's lane and the in-app checks its hand-off
needs (`"loaded"` here: one passing check of the running app), the finish lines, and where the record lives.

Criteria turn on the live view's plan chapter, which shows whether the work is still what was agreed and what proves
it; the finish line's `label` and `covers` and the `outcome` shape it. Every key is optional; a delivery file with no
`criteria` gets no plan and today's page. Give each story the acceptance criteria the plan names, each with an id
unique across the delivery. A finish line's `covers` says which criteria it stands for (a list of ids, `"all"`, or
`[]` for none by design) and which proofs must be done for it: `merged` here; `review` and `handed`, and
`in-app-<mode>` or `clip` for the in-app checks and video evidence a story needs, are the others. `outcome` is the
delivery's purpose in one sentence and in full. `lead-init` freezes all of it as the approved plan:

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

To review the plan itself before work starts, name its lenses in the delivery file, such as `"planReview": {"lenses":
["lens.outcome-correctness", "lens.adversarial-testing"]}`, commit the plan, and run `plan-review prepare`. It gives
each lens a clean checkout of the commit and a filled brief, and prints the reviewer prompts; save each reviewer's
result where it says, then run `plan-review reduce`. Revise the plan for the open findings, commit, and repeat. The
review has no round bound: `reduce` says when it stops converging, and only the operator ends it, with `plan-review end
--reason TEXT`. Every round, and the commit that closed each finding, is kept and shown in the plan chapter's "Plan
review" fold; `lead-init` refuses to freeze the plan while the review is open.

Commit it, check the delivery, create the record, write the lane brief and assign the story:

```sh example repo
git add -A
git commit -q -m "<delivery-key>: the delivery"
git push -q origin main
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py doctor --delivery <delivery-key>
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-init
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-brief
git add -A
git commit -q -m "<delivery-key>: the lane brief"
git push -q origin main
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-assign <story-key> --lane lane-1 --title "Lane 1"
```

Start the lane with exactly what `lead-assign` prints. From here on, the lane works from the lane brief.

Once the delivery has started, change the plan's criteria and finish lines only through `lead-plan change`. It
records what the plan said before and after and why, edits the delivery file, and commits it; it never pushes, and it
refuses on a checkout with uncommitted changes or one behind the base. `--kind changed` or `relaxed` rewords a
criterion, `added` adds one to a story, `removed` takes stories out of the delivery (the plan keeps them, marked
removed), and `finish` changes or adds a finish line's `covers`. Push the commit so lanes see it:

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-plan change --kind relaxed --subjects <story-key> --criterion greeting-shown --text "The front page shows a greeting" --why "The operator accepted any greeting"
git push -q origin main
```

A story added to the delivery file mid-delivery is recorded as `added` when `lead-brief`, `lead-assign` or the sweep
registers it, with no reason until you give one with `lead-plan why <change-id> --why <reason>`; the plan chapter
flags every change without one. An edit made to the criteria or `covers` by hand shows in the plan as `unrecorded`.
`doctor --delivery` checks the plan the delivery file makes, and says when the chapter is off.

### The lane delivers a story

Make the story's own checkout from the base:

```sh example repo
git fetch -q origin
git worktree add -q --no-track -b feature/<story-key> .orchestration/lanes/<story-key> origin/main
```

Change and commit the story's files in that checkout. Then run the gates, build, and check the change in the app:

```sh example lane
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py gate <story-key>
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py build <story-key>
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py app-check <story-key> greeting --mode loaded --path / --eval "document.title !== ''"
```

Prepare the review round. The first run seeds `.orchestration/review/<story-key>/context.md` from the plan and stops
until you replace its TODO line under "Evidence so far" with what you ran and what it showed; then run it again:

```sh example lane
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py review-prepare <story-key> --round 1
```

It prints one prompt per lens. Start one reviewer per lens with its prompt, and save each reviewer's final JSON message,
exactly as returned, to the result path printed beside it. Then reduce the round, push the branch, open its draft
change request and hand off:

```sh example lane
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py review-reduce <story-key> --round 1
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py push <story-key>
printf 'Summary\n\nWhy\n\nValidation\n' > .orchestration/pr-body.md
pr=$(python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py open-pr <story-key> --title "<story-key>: Show the greeting" --body-file .orchestration/pr-body.md | tail -n 1)
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py handoff <story-key> --pr "$pr"
```

If a lens asks for changes, fix them, commit, run the gates again and prepare the next round. End the turn after the
hand-off with the message `handoff` prints.

### The lead accepts, merges and closes

Check the hand-off's claims, merge through the queue, and clean up the lane's checkout:

```sh example repo
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-accept <story-key>
python3 -B .agent-skills/current/skills/orchestrate-work/scripts/merge_queue.py --repo . --state .orchestration/state --remote origin --base main run <story-key>
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-cleanup <story-key>
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-reconcile
```

A delivery with several stories runs the queue's `serve` instead, once, and leaves it running (see the operating
rules). A story marked for video evidence adds the evidence page after it merges:

```text example outline
run_delivery.py lead-evidence record <story-key> --after-build <stamped build of the merged base>
run_delivery.py lead-evidence page --out <folder>
run_delivery.py lead-evidence published --url <address> --out <folder>
run_delivery.py lead-evidence status
```

A delivery whose base carries tooling that must not ship builds its product-only branch with `ship`, and runs the
gates there:

```text example outline
run_delivery.py ship
```

When every finish line is met, journal the close-out, then close out the delivery's processes, checkouts and lease:

```sh example repo
python3 -B .agent-skills/current/skills/orchestrate-work/scripts/delivery_state.py --dir .orchestration/state journal --kind finish.met --subject greeting --summary "The front page shows the greeting"
python3 -B .agent-skills/current/skills/orchestrate-work/scripts/delivery_state.py --dir .orchestration/state journal --kind delivery.closed --summary "Delivered"
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-close
```

## 7. Host notes

No command calls a host's own interface. A lane is always a session of its own, never a subagent and never the lead
working in its own session. When the host cannot start or message sessions, no lane starts: the lead records the blocker
and brings it to the operator, who may choose to deliver the stories one at a time through `$execute-work`. Where a host
offers less otherwise, the workflow takes the path on the right:

| Host behaviour | Without it |
| --- | --- |
| Publishing the evidence page | A local page the operator shares |
| Tracker moves | The lane reports the move in its hand-off, and the lead makes it |

## 8. Operating rules

- **Lanes are sessions of their own.** Each lane runs as its own session the operator can open, watch and steer, never
  as a subagent and never as the lead working in its own session. A host that can't start or message sessions stops, as
  the host notes say. Lens reviewers stay subagents of the lane.
- **Lanes never merge the base.** A lane reads the delivery's settings, plan and brief from the base on the remote, so
  the lead's pushed changes reach it without a merge; merging the base would move its reviewed head and cost a review
  round. The merge queue syncs each branch with the base when it merges.
- **Review evidence is captured at hand-off.** `handoff` captures each round's review evidence; without it the line map
  shows no rounds for the story.
- **Restart long-running commands after an update.** A running supervisor (`lead-run`), with the merge queue's
  `serve` and the live view it keeps, runs the code it started with; after the release is updated, `lead-close` it or
  stop it, then `lead-run --resume`.
- **Start the merge queue with default signals.** Start `serve` with SIGINT, SIGHUP and SIGQUIT at their defaults, in
  its own terminal or under a process manager, never with `nohup` or a background `&`: its checks inherit those
  signals ignored, and signal-handling tests fail as a broken base.
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
- **`the tracker snapshot reads`**: export the tracker variables the line names.
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
- **`fails on`**: a gate fails on the delivery base itself. Repair the base before any lane starts.
- **`isn't a valid delivery-plan/1`**: the stories' criteria and the finish lines' covers make a plan the view can't
  read. Correct the delivery file at the key path the line names.
- **`the binding has no state.forgeSnapshot`**: the repository configures a forge, but nothing reads its pull requests
  into the record. Add `state.forgeSnapshot` to the binding; delivery-views' `snapshot.py` prints one for GitHub and
  Bitbucket.
- **`the binding has no tracker publisher`**: the binding reads the tracker, but nothing posts the delivery's moves to
  it. Add a tracker publisher to `state.publishers`, or a host publisher whose outbox the lead publishes.
- **`and the runtime needs Node`** or **`is below the runtime's nodeMinimum`**: install Node at the version the line
  names or newer, on `PATH` or under nvm.
- **`stories with no plan section`**: each story needs a plan heading that names its key; its lane's review context is
  that section. Add the heading, or correct the story's key. `lead-assign` refuses the story until then.
- **`context.sections the plan lacks`**: add the heading to the plan, or remove it from `context.sections`.
- **`no plan at`**: commit the plan where the delivery file's `plan` says, or correct `plan`.
- **`has no configuration for lanes`**: lanes read `.agents/run-delivery.json` and the delivery file from the remote
  base. Commit them on the base and push it. A local setup, whose lanes read their own working tree, sets
  `RUN_DELIVERY_LOCAL=1` instead.

- **`forge.mergeNeedsReady says the merge step refuses a draft`**: the repository's merge step refuses a draft pull
  request, and the delivery doesn't let the lead mark one ready. Add `"readyPullRequests": true` to the delivery file's
  `authority`, so `lead-accept` marks each draft ready before the queue takes it.
- **`the supervisor is down`**: the delivery's supervisor stopped, or was never started. `lead-run --resume` stops what
  it left running and starts it again; the lead's commands work by hand meanwhile.
- **`don't have the default signal dispositions`**: a child of the supervisor ignores SIGINT, SIGHUP or SIGQUIT, so its
  checks would fail as a broken base. Stop it, then start the supervisor again with `lead-run --resume`.

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

- **Only signal-handling tests fail in the queue's checks, and the base is marked broken**: the runner was started
  with SIGINT, SIGHUP or SIGQUIT ignored, as `nohup` and a background `&` leave them, and its checks inherited that.
  Start the runner with those signals at their defaults, in its own terminal or under a process manager, then mark
  the base repaired.
