---
name: run-delivery
description: Run the mechanical steps of an agent-skills (orchestrate-work) delivery as commands, in any repository - lens round briefs and round files for review, gate runs, in-app checks under a lease, draft PRs, lane hand-offs, and the lead's init, brief, assignment, acceptance and ship. Use as a lane or lead in a delivery whose repository has `.agents/run-delivery.json`, or to set one up.
---

# run-delivery

The agent-skills delivery workflow (`deliver-work` → `orchestrate-work`, `obtain-review`/`review-work`) leaves its
mechanical steps to the agent:
- filling a round brief per lens per round
- assembling round files that `capture-review` accepts
- journaling rounds
- capturing review evidence after hand-off
- checking each hand-off's claims
- running in-app checks without colliding over shared ports and sign-ins
- building the product-only branch

This skill does them as commands, so agents spend their attention on the work. It wraps the release it is installed
with, and replaces none of it: the `orchestrate-work` state tool and its round validator, the `obtain-review`
round-brief template and the lens charters, all found in the installed generation the tool runs from. Each brief
carries that generation's release identity, read from its own entry in the repository's receipt
(`.agent-skills/active.json`). It runs on POSIX hosts only.

Run it from the repository's top level, by the path every checkout of the delivery base resolves:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py <command> ...
```

**New to it?** Start with [the adoption guide](references/adopting.md): installation, the configuration files, the
setup check, a first delivery as lead and as lane, host notes, the operating rules and troubleshooting. This page is
the command reference.

## Commands

| Who | Command | What it does |
| --- | --- | --- |
| Lane | `review-prepare <key> --round N [--grace] [--restart]` | Private, prepared checkout per lens at HEAD, kept across the story's rounds: each round resets it to exactly that round's candidate, keeping its ignored environment unless the prepare commands or a dependency file changed, which prepares it again; the review's last reduction removes it. One filled round brief per lens (with the story's in-app records); prints the reviewer prompts. A prepared round is only redone with `--restart`, which is recorded for the lead. |
| Lane | `review-reduce <key> --round N` | Checks each lens result against its brief, assembles and validates the round file, journals it, cleans up. A mismatched result is kept as an attempt and only that lens re-runs. Idempotent. |
| Lane | `gate <key>` | Runs the repository's gates in order (each in its own process group, stopped on timeout), recorded against HEAD. |
| Lane | `build [<key>]` | The in-app build, stamped with its commit. With no key, the delivery's integration build, whose `{key}` is the delivery's own key. |
| Lane | `app-check <key> <name> --mode hold\|fail\|loaded --path P ... [--baseline \| --probe]` | One in-app check under the lease, scored against strict rules and logged. `--probe` looks things up without making evidence. |
| Lane | `push <key>` | Pushes the story's own branch from its own checkout, never forced. Refused unless the delivery grants `push`. |
| Lane | `app-video <key> <name> --mode hold\|fail\|loaded --path P ... [--after-build DIR]` | Records the same steps on the baseline build and on this build (or `--after-build`), and stitches a before/after clip. Required at hand-off for stories marked `"evidence": "video"`. |
| Lane | `open-pr <key> --title T --body-file F` | The story's draft PR into the delivery base, through the forge adapter. Refused unless the delivery grants `draftPullRequests`. |
| Lane | `pr-review <key> [--wait]` | With `review.pr`: waits for the pipeline at HEAD and its review comment, saves it and prints it for triage. Reported as unsupported by a forge adapter without a `reviews` command. |
| Merge queue | `pr-review-current --branch B --head H [--wait]` | The binding's `reviewGate`: exits 0 only when `review.pr`'s review is current and its pipeline passed at that head, waiting up to `waitSeconds` with `--wait`; otherwise exits non-zero with one line on stderr saying why. |
| Lane | `handoff <key> --pr URL [--checks IDS] [--pr-review-triage T] [--findings T] [--blocked LABEL=REASON]...` | Verifies everything, captures review evidence, hands off, prints the messages. `--checks` names the checks the lane ran, by the binding's catalogue ids, comma-separated: an unknown id is refused, and the ids are shown in the message and kept on the record, for the lead's acceptance to propose. |
| Lane | `handoff <key> --no-change --evidence TEXT\|@FILE` | A story whose criteria already hold on the base: hands it off with the evidence, naming each criterion by id, and the base head it ran at (the checkout's HEAD, which the base must hold). No branch, PR or review round, and it never enters the merge queue. |
| Lead | `lead-setup <key> --plan P --base B [--pattern RE] (--authority push,draftPullRequests[,readyPullRequests] --granted-by WHO \| --no-authority) [--tracker-snapshot F] [--ship-from REF] [--approvals-section S] [--out-of-scope-section S]` | Drafts `.agents/deliveries/<key>.json` from the plan's headings (by default plan-work's `### KEY. Title` under `## Stories`; `--pattern` names groups `key`, `title`, optional `label`, `pinned`), cross-checked with the tracker. From a plan in plan-work's format it drafts each story's lane and `after` keys, each lane's `owns` from the `Units and order` table's lowest-tier row, and each story's criteria from its tracker item's Acceptance Criteria (the snapshot file, else the binding's `trackerSnapshot`), and notes what it couldn't draft, and the approvals and out-of-scope list from the named sections' list items; finds the tooling commit. Writes nothing until it has the operator's grant to lanes, or `--no-authority`; `readyPullRequests` is the lead's grant, which `lead-accept` marks drafts ready under. `--force` keeps `planReview`, `approvals`, `outOfScope` and the grant. |
| Lead | `plan-review prepare [--restart]` | Reviews the plan before it is approved, with the delivery file's `planReview.lenses`, fixed at round 1: a clean checkout of the committed plan per lens, identity-checked, and one unbounded round brief each; prints the reviewer prompts. A round whose candidate is the last one's is refused. |
| Lead | `plan-review reduce` | Checks each lens result (P2 and P3 notes belong in evidence; nothing is deferred), the end identity checks, then records the round, removes its checkouts, and says when the review isn't converging. Rebuilds the plan once the record exists. With `--plan`, a round that leaves every lens aligned writes the plan's review record beside it, `<plan>.review.json`, and keeps a hand-written one. |
| Lead | `plan-review end --reason T` | Ends a review that isn't converging, with the operator's reason (at most 600 characters). No round count ends one. With `--plan`, writes the review record as `reduce` does, with the reason and the findings left open among its residual risks. |
| Lead | `plan-review check RECORD...` | Validates `plan-review-evidence/1` records, hand-written or written by a review: their shape, and for a written one its rounds, each lens's final result and its outcome agreeing. |
| Lead | `lead-init [--replace] [--no-supervisor]` | Creates the record: headline, planned stories, finish lines, acceptance policy, then starts the delivery's supervisor and prints the live view's address. Refuses stories without lanes, refuses to freeze the plan while its review is open, and refuses each setup gap `doctor --delivery` reports. Refuses a state directory that holds a record or a flow log that holds a snapshot or a supervisor's lock; `--replace` stops that supervisor and archives both. |
| Lead | `lead-run [--resume] [--once]` | The supervisor: keeps `serve` and the live view running, and each pass reconciles, cleans up merged stories once their lane moves off them, journals `finish.met`, renders with publishers, copies the record to `latest`, and runs the close-out once nothing holds it (references/config.md). `--resume` stops what a killed one left and starts it again; `--once` runs one pass. Every other lead command but `lead-setup` and `lead-init` ends with the supervisor's status line, which reads the record's close marks: a stopped supervisor whose close has both marks says the delivery is closed, and one whose close has only its `started` mark says the close stopped partway and names `lead-run --resume` or `lead-close` to finish it. |
| Lead | `lead-resolve <journal id>\|full-health --why T` | Resolves a recorded blocker, or asks the supervisor's next pass to rerun a failed `fullHealth` (under the `focused` posture, once the base returns to `gate`). |
| Lead | `lead-hints` | Prints the delivery's `work-hints/1` for orchestrate-work's `next`: each story's files from its plan section's `Files:` sentence and its blockers from its `after` keys. Name it as the binding's `state.workHints` so `next` ranks by the plan's order without the tracker. |
| Lead | `lead-brief` | Renders the lane brief from the templates and the config, and enters any story the record doesn't hold yet as planned work. |
| Lead | `lead-assign <key...> --lane L --title T [--note N]` | Records the assignment; prints the assignment message. First it warns, without refusing, of the files outside the lane that `lead-accept` would refuse: those the story's plan section names, and those that have often changed with them (in at least 3 and a quarter of a named file's recent changes), with each one's owner, so the lead can move them before the lane starts. |
| Lead | `lead-accept <key> [--checks IDS \| --full-suite] [--reason T] [--accept-blocked LABEL]... [--accept-restarts]` or `lead-accept --requeue <key>` | On a no-change hand-off, checks that the base still holds the head its evidence ran at and that the evidence names every one of the story's criteria (a story with none is refused, naming the fix), then settles it: never queued, counted by the close and the finish lines, no base commit. Otherwise verifies a hand-off's claims (head, PR, rounds, evidence, owned files, gates, in-app), makes the lead decide every blocked item and restart, marks the draft ready under the `readyPullRequests` grant, then accepts and journals the decisions. Under a binding with `selection.lead`, the lead chooses the candidate's checks as it accepts: `--checks` records them through the state tool's `select` at the handed head, refused as `select` refuses, and put back as it was when the acceptance then fails; `--full-suite` accepts it on the binding's `fallback`, journalled as the lead's decision, clearing a selection still standing at the head. Under a binding that names a provider, a candidate with no selection runs the provider's answer, so `--full-suite` is refused there and the lead chooses with `--checks`. With neither, it is refused, records nothing, and prints a proposed `--checks`: the catalogue ids of the test modules the diff changes and the checks the hand-off named. A binding without `selection.lead` refuses both, and accepts as before. `--requeue` puts a stopped candidate the lead has resolved back in the queue at its handed head, with no new hand-off. On work already merged outside the queue it checks the merged PR instead of the open one, can't check owned files, marks nothing ready, and records the decisions once; work merged with no hand-off is refused, for the lead to journal a decision instead. |
| Lead | `lead-carry-review <key> --from HEAD --to HEAD --reason T` | Prints the files changed from the head the captured review covers to the queued head, then carries the review there through the state tool's `carry-review`; its refusal is passed through and nothing is written. |
| Lead | `lead-cleanup [key...]` | After a story merges: keeps its lane evidence beside the record, then removes its checkout and local branch, and its remote branch only if `run-delivery.json` allows it and its PR is closed. Removes nothing unless every tip is on the base and the checkout is clean. With no keys, sweeps every story and skips the unmerged ones. |
| Lead | `lead-reconcile` | One sweep: takes the binding's forge and tracker snapshots and reconciles the record with them, so a story's stop leaves "Not started" once its ticket starts or its draft PR opens, and reaches "In review" if the PR is opened for review. The supervisor runs one every pass. |
| Lead | `lead-evidence record\|page\|published\|status [key...]` | Records missing before/after clips against a stamped build of the merged base, reusing each story's passing hold and fail checks, then builds the delivery's evidence page (index.html plus videos/) to publish and link from the tracker. |
| Lead | `lead-plan change\|why\|approval ...` | Records a change to the approved plan (`change --kind added` on a new key adds the whole story: plan heading, delivery-file story with its `why`, planned entry), its reason, or an approval marked done or waiting (`approval <id> --state done\|waiting [--who W]`), committing the delivery file in the lead's clean checkout of the base, or in a lead worktree of its own at the base's tip or ahead with only this delivery's own plan commits; never pushes. |
| Lead | `lead-close` | To close early, or with the supervisor down, after `delivery.closed`: asks a running supervisor to stop after its pass, stops a post-merge `fullHealth` run still going, which records no result, records a `fullHealth` skipped under the base's `focused` posture, then cleans up every story, renders the views, archives the record, logs, plan review and evidence, stops this delivery's merge queue, live view and `lead-reconcile` sweep, removes the runner's and the ship checkouts, frees the in-app lease, and lists what's left for the operator. It closes the latest `delivery.closed` that no new work has reopened, lists the tracker updates still owed without waiting for them, writes the close's `finished` mark in the record and the archived copy once the archive has landed, and names the closing entry in `closed.json`. It finishes a close that stopped partway. |
| Lead | `ship [--no-gates]` | Builds the product-only branch from the delivery base's ship paths, then prepares it and runs the gates there. Gate scripts that only the delivery base has are lent for the run and removed after. Commits nothing, pushes nothing. |
| Setup | `config` | Every resolved setting and the file it came from. |
| Setup | `adapter-test` | Proves the repository's in-app serve and check adapters keep the contract, on a fixture page. |
| Setup | `doctor [--delivery K] [--offline] [--run-adapters] [--run-gates]` | The setup check: one line per check, with the fix for each that fails. See below. |

## The setup check

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py doctor [--delivery <key>] [--offline] [--run-adapters] [--run-gates]
```

It prints one line per check: `ok`, `missing` or `wrong` with its fix, `warn` for a finding that doesn't fail it, or
`skipped` with the check it depends on or the flag that turns it on. A check whose prerequisite failed is skipped, so one missing prerequisite shows as one line.
It exits 0 when no check is `missing` or `wrong`, 1 when one is, and 2 on a usage error or a non-POSIX host.

| Check | When |
| --- | --- |
| The host is POSIX, with git 2.31 or newer, Python 3.11 or newer, `ps` and `lsof` | always |
| The binding validates against `orchestration-binding/1` | always |
| `.agents/run-delivery.json` resolves with no conflicts, and no earlier name is in use | always |
| The running generation is the receipt's current one | always |
| The installed generation is committed on the delivery base, so lanes resolve the tool | always |
| Gates are declared, or the configuration says there are none; gate and prepare programs resolve | always |
| The lane checkout folder is ignored by git | always |
| The tracker issue URL is set | always |
| `review.pr` isn't configured with an adapter that lacks `reviews` | `review.pr` is configured |
| `gh` is installed and signed in | the GitHub adapter is configured |
| The forge adapter answers `find` | a forge is configured, and not `--offline` |
| Forge write access: reported as not verified, since the check never writes to a forge | a forge is configured |
| The tracker snapshot's and tracker adapter's token variables are set | the binding declares a tracker snapshot, or the configuration names a tracker adapter |
| The in-app build, serve and check programs resolve; the lease and baseline folders are writable | in-app checks are declared |
| The in-app adapters keep their contract on the fixture page | `--run-adapters`; skipped at once if the lease is held |
| ffmpeg and ffprobe, with the filters video evidence uses | a story is marked for video evidence |
| Node 22.6 or newer reads the harness configuration, through the generation's runtime loader | `harness.config.ts` exists |
| The delivery file: every story has a lane, and lanes own disjoint files | `--delivery` |
| The delivery supervisor is running, or the delivery is closed. A close with its `started` mark and no `finished` mark, and no supervisor running, is `wrong`: it stopped partway, and the row names what finishes it | `--delivery` |
| Story files: each file a story's plan section names is its lane's. A file outside the lane is reported as `warn`, which doesn't fail the check, since a plan may move files between lanes as stories merge | `--delivery` |
| The gates pass on the base, in a fresh prepared checkout that is removed afterwards | `--run-gates` |

What it changes: it fetches the delivery base (not with `--offline`, which reads the local remote-tracking refs), runs
Node on `harness.config.ts` when there is one, and creates and removes one temporary file to test each folder it needs
to write. It writes no tracked, delivery or record file and leaves nothing running. Its messages are explained in the
troubleshooting section of [the adoption guide](references/adopting.md).

## Running lanes

Each lane runs as **its own session, linked to the lead's**, never as a reviewer or helper inside the lead. The
operator can then open, watch and steer each lane, and stop it on its own.

- One session per lane, started fresh. Its prompt is exactly what `lead-assign` prints, plus anything the lead decides
  for that lane (an order, a wait). The brief and the record carry everything else.
- The session shares the main checkout: the lane makes each story's checkout itself, at `laneCheckout`
  (`.orchestration/lanes/<key>` by default, beside the binding's state and ignored by git), and touches nothing
  outside it.
- A lane ends its turn after each hand-off, or when blocked, with the report `lead-assign` asks for. The lead reads
  that report as a claim, checks it with `lead-accept`, and sends the lane its next story. Follow-ups and returned
  candidates go to the same session, never a new one.
- Lens reviewers belong to the lane, as the brief says.

## Host behaviour

No command calls a host's own interface. A lane is always a session of its own, never a subagent and never the lead
working in its own session. When the host cannot start or message sessions, no lane starts: the lead records the blocker
and brings it to the operator, who may choose to deliver the stories one at a time through `$execute-work`. Where a host
offers less otherwise, the workflow takes the path on the right:

| Host behaviour | Without it |
| --- | --- |
| Publishing the evidence page | A local page the operator shares |
| Tracker moves | The lane reports the move in its hand-off, and the lead makes it |

## Where lanes read the delivery

A lane reads the delivery's settings, plan and brief notes from the delivery base on the remote, not from its
checkout. The lead pushes a change once, and every lane sees it without merging the base, which would move the lane's
reviewed head and cost a review round. The brief tells lanes never to merge the base; the merge queue syncs at merge time.

## Authority

Who may push and open pull requests is the operator's grant, written in the delivery file as `authority`
(`push`, `draftPullRequests`, `grantedBy`, `grantedOn`). `lead-brief` states it in the lane brief, and `push` and
`open-pr` refuse anything it doesn't grant. Without it, lanes don't push: the lead does. No grant covers force-pushing,
merging, or marking a pull request ready.

## Evidence (video, when relevant)

- **When it's relevant.** `lead-setup` marks a story `"evidence": "video"` when its in-app checks hold or fail a
  request (it pins a pattern), because then the story changes what the page shows while it loads or fails. It also adds
  the finish line `evidence-page`. The lead removes the mark where the change isn't visible, and can add it elsewhere.
- **Lane.** A marked story's hand-off needs a current before/after clip (`app-video`); `lead-accept` re-checks it.
- **Lead, after each marked story merges:**
  1. `lead-evidence record <key> --after-build <stamped build of the merged base>` for any story without a clip.
  2. `lead-evidence page --out <dir>` (with `--note KEY=TEXT` for a story that has no clip and why). The page's
     `--title`, `--intro` and notes are kept in the record (`evidence-notes.json`) and reused on every rebuild; a flag
     given again replaces what was kept, and `--note KEY=` removes that story's note.
  3. Publish the page privately, at the same address each time (with the host's publishing, or as a local page the
     operator shares), then `lead-evidence published --url <address> --out <dir>`. The page shows application
     content.
  4. Link it from the epic once, and each story's section (`<address>#<key in lowercase>`) from its ticket.
- **Close-out.** `lead-evidence status` says whether the page carries every merged marked story's latest clip;
  `lead-close` lists any gap, and the `evidence-page` finish line is met only when it's current.

## Cleaning up

- After each merge, the lead runs `lead-cleanup <key>` (or a bare `lead-cleanup` to sweep).
- At close-out, after orchestrate-work's `delivery.closed`, the lead runs `lead-close`. It doesn't touch the lane
  sessions, the operator's main checkout or the lead's own checkout: it lists them for the operator.

## Setting up a repository

[The adoption guide](references/adopting.md) takes a repository through it, block by block. In short:

1. Install a release and commit it on the delivery base, so every lane's checkout resolves the tool.
2. Most settings come from what the repository already declares: the orchestrate-work binding (base, gates,
   prepare, record, leases), `.agents/delivery-views.json` (tracker, forge) and `harness.config.ts` (extra lenses,
   cross-checked against the binding). `.agents/run-delivery.json` holds only the rest: branch and checkout patterns,
   the never-touch list, and the in-app adapters (see `references/config.md`). `run_delivery.py config` shows each
   setting and where it came from, and `doctor` checks the whole setup.
3. Per delivery, after the plan is approved and the tracker items exist: `lead-setup` drafts
   `.agents/deliveries/<key>.json`. From a plan in plan-work's format it drafts each story's lane, criteria and `after`
   keys and the files each lane owns, and notes anything it couldn't. Check those against the plan, complete the rest
   (in-app requirements, finish lines, brief notes), commit it with the delivery's tooling, then `lead-init` and
   `lead-brief`.
   To add a story mid-delivery in one command, run `lead-plan change --kind added --subjects <new-key> --title T --text C
   --why W --lane L`: it adds the plan heading, the delivery-file story with its `why` and the planned entry, and
   commits; push it, and `lead-assign` accepts the story. By hand, add it to that file (and the plan) with a `why`,
   one line saying why it joins the delivery, commit and push, then run `lead-brief`. It, the `lead-reconcile` sweep and `lead-assign` all enter a story
   the record doesn't hold as planned work for its lane, moving it out of the follow-ups if the tracker reconcile filed
   it there, so the live view shows it straight away, and the plan records it as an `added` change carrying that
   `why`. A story added after `lead-init` froze the plan with no `why` is refused, by name and with the fix, and
   nothing is registered; the supervisor's sweep leaves it unregistered and says so. A story already in the file at
   `lead-init` is part of the frozen plan and needs none. `doctor --delivery` names a story waiting without its `why`,
   and an `added` change a record already holds with no reason, until `lead-plan why` records one.

## Adapters

- **Forge:** `forge.adapter` is `bitbucket` or `github`, the reference adapters in `adapters/`, or `forge.argv`
  names your own command with the same contract: `find`, `open` and `get`, printing JSON, and `reviews` where the forge
  has it. The reference adapters also answer `candidate`, which a binding's candidate step can call for the
  conventional `feature/<key>` branch. A reference adapter's only write is opening a draft change request, which `open-pr` does only under the
  delivery's `authority`; neither prints a token. The GitHub adapter uses `gh` and its own sign-in, and has no
  `reviews`.
- **Sign-in values** come only from the environment or the adopter's own adapter. `app-check` and `app-video` refuse an
  adapter argument whose flag name or assignment key looks like one (password, token, secret, API key, credential).
- **In-app:** `inApp.build`, `inApp.serve` and `inApp.check` are argv commands. The check reads a JSON spec
  (`{spec}`) and prints a report with `ready`, `build` (the served stamp), `held`, `failed`, `evals`, `afterEvals`,
  `pageErrors` and `consoleErrors`. The contract is in `references/config.md`.
