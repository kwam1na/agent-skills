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
| Lane | `review-prepare <key> --round N [--grace] [--restart]` | Private, prepared checkout per lens at HEAD; one filled round brief per lens (with the story's in-app records); prints the reviewer prompts. A prepared round is only redone with `--restart`, which is recorded for the lead. |
| Lane | `review-reduce <key> --round N` | Checks each lens result against its brief, assembles and validates the round file, journals it, cleans up. A mismatched result is kept as an attempt and only that lens re-runs. Idempotent. |
| Lane | `gate <key>` | Runs the repository's gates in order (each in its own process group, stopped on timeout), recorded against HEAD. |
| Lane | `build <key>` | The in-app build, stamped with its commit. |
| Lane | `app-check <key> <name> --mode hold\|fail\|loaded --path P ... [--baseline \| --probe]` | One in-app check under the lease, scored against strict rules and logged. `--probe` looks things up without making evidence. |
| Lane | `push <key>` | Pushes the story's own branch from its own checkout, never forced. Refused unless the delivery grants `push`. |
| Lane | `app-video <key> <name> --mode hold\|fail\|loaded --path P ... [--after-build DIR]` | Records the same steps on the baseline build and on this build (or `--after-build`), and stitches a before/after clip. Required at hand-off for stories marked `"evidence": "video"`. |
| Lane | `open-pr <key> --title T --body-file F` | The story's draft PR into the delivery base, through the forge adapter. Refused unless the delivery grants `draftPullRequests`. |
| Lane | `pr-review <key> [--wait]` | With `review.pr`: waits for the pipeline at HEAD and its review comment, saves it and prints it for triage. Reported as unsupported by a forge adapter without a `reviews` command. |
| Lane | `handoff <key> --pr URL [--pr-review-triage T] [--findings T] [--blocked LABEL=REASON]...` | Verifies everything, captures review evidence, hands off, prints the messages. |
| Lead | `lead-setup <key> --plan P --base B --pattern RE [--tracker-snapshot F] [--ship-from REF]` | Drafts `.agents/deliveries/<key>.json` from the plan's headings (named groups `key`, `title`, optional `label`, `pinned`), cross-checked with the tracker; finds the tooling commit. |
| Lead | `lead-init` | Creates the record: headline, planned stories, finish lines, acceptance policy. Refuses stories without lanes. |
| Lead | `lead-brief` | Renders the lane brief from the templates and the config, and enters any story the record doesn't hold yet as planned work. |
| Lead | `lead-assign <key...> --lane L --title T [--note N]` | Records the assignment; prints the assignment message. |
| Lead | `lead-accept <key> [--accept-blocked LABEL]... [--accept-restarts]` | Verifies a hand-off's claims (head, PR, rounds, evidence, owned files, gates, in-app), makes the lead decide every blocked item and restart, then accepts and journals the decisions. |
| Lead | `lead-cleanup [key...]` | After a story merges: keeps its lane evidence beside the record, then removes its checkout and local branch, and its remote branch only if `run-delivery.json` allows it and its PR is closed. Removes nothing unless every tip is on the base and the checkout is clean. With no keys, sweeps every story and skips the unmerged ones. |
| Lead | `lead-reconcile [--every S]` | Takes the binding's forge and tracker snapshots and reconciles the record with them, so a story's stop leaves "Not started" once its ticket starts or its PR opens. Run it with `--every` in the background beside the merge queue. |
| Lead | `lead-evidence record\|page\|published\|status [key...]` | Records missing before/after clips against a stamped build of the merged base, reusing each story's passing hold and fail checks, then builds the delivery's evidence page (index.html plus videos/) to publish and link from the tracker. |
| Lead | `lead-close` | Last, after `delivery.closed`: cleans up every story, renders the views, archives the record, logs and evidence, stops this delivery's merge queue, live view and `lead-reconcile` sweep, removes the runner's and the ship checkouts, frees the in-app lease, and lists what's left for the operator. |
| Lead | `ship [--no-gates]` | Builds the product-only branch from the delivery base's ship paths, then prepares it and runs the gates there. Gate scripts that only the delivery base has are lent for the run and removed after. Commits nothing, pushes nothing. |
| Setup | `config` | Every resolved setting and the file it came from. |
| Setup | `adapter-test` | Proves the repository's in-app serve and check adapters keep the contract, on a fixture page. |
| Setup | `doctor [--delivery K] [--offline] [--run-adapters] [--run-gates]` | The setup check: one line per check, with the fix for each that fails. See below. |

## The setup check

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py doctor [--delivery <key>] [--offline] [--run-adapters] [--run-gates]
```

It prints one line per check: `ok`, `missing` or `wrong` with its fix, or `skipped` with the check it depends on or the
flag that turns it on. A check whose prerequisite failed is skipped, so one missing prerequisite shows as one line.
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
| The tracker snapshot's token variables are set | the binding declares a tracker snapshot |
| The in-app build, serve and check programs resolve; the lease and baseline folders are writable | in-app checks are declared |
| The in-app adapters keep their contract on the fixture page | `--run-adapters`; skipped at once if the lease is held |
| ffmpeg and ffprobe, with the filters video evidence uses | a story is marked for video evidence |
| Node 22.6 or newer reads the harness configuration, through the generation's runtime loader | `harness.config.ts` exists |
| The delivery file: every story has a lane, and lanes own disjoint files | `--delivery` |
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

No command calls a host's own interface. Where a host offers less, the workflow takes the path on the right:

| Host behaviour | Without it |
| --- | --- |
| Lanes as separate sessions the operator can open and steer | Background agents, with each lane's report relayed by the lead |
| Messaging between sessions | The operator carries the lead's message to the lane |
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
  2. `lead-evidence page --out <dir>` (with `--note KEY=TEXT` for a story that has no clip and why).
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
   `.agents/deliveries/<key>.json`. Complete it from the plan (lanes and the files they own, each story's lane and in-app
   requirements, finish lines, brief notes), commit it with the delivery's tooling, then `lead-init` and `lead-brief`.
   To add a story mid-delivery, add it to that file (and the plan), commit and push, then run `lead-brief`. It, the
   `lead-reconcile` sweep and `lead-assign` all enter a story the record doesn't hold as planned work for its lane,
   moving it out of the follow-ups if the tracker reconcile filed it there, so the live view shows it straight away.

## Adapters

- **Forge:** `forge.adapter` is `bitbucket` or `github`, the reference adapters in `adapters/`, or `forge.argv`
  names your own command with the same contract: `find`, `open` and `get`, printing JSON, and `reviews` where the forge
  has it. A reference adapter's only write is opening a draft change request, which `open-pr` does only under the
  delivery's `authority`; neither prints a token. The GitHub adapter uses `gh` and its own sign-in, and has no
  `reviews`.
- **Sign-in values** come only from the environment or the adopter's own adapter. `app-check` and `app-video` refuse an
  adapter argument whose flag name or assignment key looks like one (password, token, secret, API key, credential).
- **In-app:** `inApp.build`, `inApp.serve` and `inApp.check` are argv commands. The check reads a JSON spec
  (`{spec}`) and prints a report with `ready`, `build` (the served stamp), `held`, `failed`, `evals`, `afterEvals`,
  `pageErrors` and `consoleErrors`. The contract is in `references/config.md`.
