# Configuration

Most of what run-delivery needs, the repository already declares for agent-skills. `run_delivery.py` reads it from there,
in this order of precedence:

1. **The orchestrate-work binding** (`.agents/orchestration.json`), because it is what the merge queue actually runs:
   - remote and delivery base: `base.remote`, `base.ref`, and the other bases the binding serves, `bases`: exact
     branch names or patterns whose `*` matches within one segment of the name, such as `["main", "integration-*"]`.
     A delivery file's `baseBranch` must be `base.ref` or match `bases`, and `lead-setup` requires `--base` once
     `bases` is declared
   - lane gates: the `fallback` checks, with `{baseHead}` meaning the base, each run in its `cwd` with its timeout
     (default: the binding's `checkTimeoutSeconds`). If the binding also selects checks with a `provider`,
     `alwaysRun` or `profiles`, the lane gates must be listed in `run-delivery.json` `gates`.
   - lens-checkout preparation: `prepare`
   - work folder: the folder of `workRoot`
   - record folder: `state.directory`, inside the lead's checkout
   - the forge token variable: `state.forgeSnapshot`'s `--token-env`
   - the in-app lease's name, which must be one of `leases`
2. **The delivery-views config** (`.agents/delivery-views.json`): the tracker's `issueUrl`, and the forge from
   `forges[0]` (name and repository).
3. **The harness config** (`harness.config.ts`), loaded with Node >= 22.6 through the release's own module hook:
   - extra review lenses: `additionalReviewLenses`, whose `charterPath` is a file in the repository
   - if the file exists but can't be read, every command except `config` stops, unless `run-delivery.json` says
     `"harness": "ignore"`
   - a cross-check: its `baseRef` and `preparationCommands` must agree with the delivery's base and the fallback
     checks, or `run_delivery.py` refuses and shows both. The delivery's base is the binding's `base.ref` unless the
     binding declares `bases`, when it is the delivery file's
4. **`.agents/run-delivery.json`** (`delivery-flow/1`), for what none of them declares, written once per repository.
   A `run-delivery.json` value that a higher source also declares, with a different value, is refused as a conflict: one
   side never wins silently.
5. Defaults.

`run_delivery.py config` prints every resolved setting and the file it came from.

**Where a lane reads them.** In the lead's checkout (on a base the binding serves), these files are read from the working
tree. In a lane's checkout they are read from the delivery base on the remote (`<remote>/<base>:<path>`): `run-delivery.json`,
the binding, `delivery-views.json`, the delivery file, the plan and the brief notes. So the lead's pushed changes
reach every lane at once, and a lane never merges the base, which would move its reviewed head, just to pick up a
tooling change. The lane's base is `RUN_DELIVERY_BASE`, or the base its checkout recorded when it was started, or else
the binding's `base.ref`; a base the binding doesn't serve is refused. `RUN_DELIVERY_LOCAL=1` reads the lane's working
tree instead (for working on the tooling itself); `RUN_DELIVERY_CONFIG` names an explicit configuration file.

**Earlier names.** A repository set up under the tool's earlier name keeps working: `.agents/flow.json` is read when
`.agents/run-delivery.json` is absent, and `DELIVERY_FLOW_CONFIG`, `DELIVERY_FLOW_DELIVERY` and `DELIVERY_FLOW_LOCAL`
are read when their `RUN_DELIVERY_` names are unset. The schema names `delivery-flow/1` and
`delivery-flow-delivery/1`, the build stamp `.flow-build.json` and the forge adapters' `FLOW_FORGE_REPOSITORY` and
`FLOW_FORGE_TOKEN_ENV` keep their names. Archives written under the earlier default folder are not moved.

One delivery's own settings live in **`.agents/deliveries/<key>.json`** (`delivery-flow-delivery/1`).
`run_delivery.py lead-setup` drafts it from the approved plan, and the lead completes it from the same plan. Both files are
kept with the delivery's tooling, so they never ship.

`run_delivery.py` picks the delivery from `--delivery KEY` or `RUN_DELIVERY_DELIVERY`. Failing that, it uses the only
delivery file there is, or the one whose base branch or story branches match the current branch.

Paths are relative to the repository root unless noted. `~` is expanded. Placeholders are filled where listed.

## `.agents/run-delivery.json`

Only what the repository doesn't already declare elsewhere. Every field is optional; a repository without in-app
checks may need no file at all.

```jsonc
{
  "schemaVersion": "delivery-flow/1",
  "branchPattern": "feature/{key}",           // a story's branch (default feature/{key})
  "laneCheckout": ".orchestration/lanes/{key}", // a story's checkout (and the lead's, by the delivery key), from the main checkout
  "deliveries": ".agents/deliveries",
  "release": "../tooling/.agent-skills/current", // default: the generation the tool runs from; set it to use another
  "review": {
    "bound": 4,                               // default: review-work's 4
    "pr": { "author": "Bitbucket Pipelines", "waitSeconds": 1800 },  // a review the forge's pipeline posts on each PR:
                                              // lanes read and triage it before hand-off (pr-review); lead-accept
                                              // requires it current at the handed head
    "lenses": { "lens.x": { "short": "x", "charter": "docs/review/x.md" } }  // added; the charter is a repository file
  },
  "tracker": {                                // optional
    "updates": "publisher",                   // publisher | host | manual (below); default: publisher with an
                                              // adapter, host for a Linear tracker snapshot, otherwise none
    "adapter": "jira",                        // the shipped Jira adapter; or "argv": [...], an adopter's own adapter
    "site": "https://example.atlassian.net", "userEnv": "JIRA_USER", "tokenEnv": "JIRA_TOKEN",
                                              // Jira's: default, the binding's jira trackerSnapshot's flags
    "project": "APP",                         // where follow-ups are filed; default: the delivery key's prefix
    "followupType": "Task",                   // the issue type a follow-up is filed as
    "transitions": {                          // every status move; absent: none
      "source": "https://wiki/workflow",      // the team's workflow page
      "start": ["Refined", "Start work"],     // a story's start, in order: the publisher makes it on lane.assigned
      "review": ["Submit for review"],        // the lane's review move; when, by reviewWhen
      "reviewWhen": "gates-passed",           // gates-passed: open-pr makes it once the gates pass at the PR's
                                              // head; pr-open: as the PR opens; pipeline-green: pr-review makes it
                                              // once the pipeline's review is current and its checks green (needs
                                              // review.pr). Absent: nothing moves by itself, and open-pr prints it
      "lead": {                               // later journal kinds to the moves the publisher makes for them
        "candidate.merged": ["Complete"],     // absent, or an empty list: no move, and the update says the move
        "delivery.closed": ["Close epic"]     // belongs to people (the default)
      },
      "targets": { "Complete": "Done" }       // the status a move leads to, for a move the tracker doesn't list
                                              // from the ticket's status, so one already made reads as made
    }
  },
  "harness": "ignore",                        // only to skip an unreadable harness.config.ts on purpose
  "gates": "none",                            // only if the repository truly has no gates (otherwise a hand-off needs a gate run)
  "neverTouch": [".agents", "scripts/"],      // shown in the lane brief
  // Fallbacks, used only where the binding and delivery-views.json declare nothing: "remote", "workDirectory",
  // "tracker": {"issueUrl"}, "forge": {"adapter"|"argv", "repository", "tokenEnv"}, "review.prepare",
  // "gates": [{"id", "argv", "timeoutSeconds"}] (argv placeholders {base}, {key}, {delivery}).
  // A gate whose argv equals inApp.build.argv also stamps the in-app build when every gate passes.
  "cleanup": {                                // optional
    "deleteRemoteBranches": false,            // lead-cleanup deletes a merged story's remote branch only if true
    "archive": "~/.local/share/run-delivery/archive/{repo}/{delivery}"  // where the record is archived, and where
                                              // the supervisor keeps its current copy, `latest`
  },
  "forge": { "mergeNeedsReady": true },       // optional: the binding's merge step refuses a draft pull request
  "supervisor": { "passSeconds": 60 },        // optional: how long the supervisor waits between passes
  "inApp": {                                  // optional; its "lease" must name one of the binding's leases
    "lease": "app-session",
    "lock": "~/.cache/my-app-session.lock",   // an OS lock, held for each check
    "port": 4201,
    "build": { "argv": ["npm", "run", "build"], "artifact": "dist", "timeoutSeconds": 1800 },
    "serve": { "argv": ["my-static-server", "{dir}", "--port", "{port}"] },
    "check": { "argv": ["my-check", "--spec", "{spec}", "{args}"] },   // {args}: what the lane passes after --
    "baseline": "~/.cache/my-app-baseline",   // a build of the base, with a .flow-build.json stamp
    "consoleErrorPattern": "^ERROR",          // console errors that fail a check (default: any)
    "testFilePattern": "\\.(spec|test)\\.[^/]+$",  // changes to these don't make in-app records stale
    "lockWaitSeconds": 480,
    "checkTimeoutSeconds": 300
  }
}
```

## `.agents/deliveries/<key>.json`

```jsonc
{
  "schemaVersion": "delivery-flow-delivery/1",
  "delivery": "EPIC-1",                       // the parent tracker item: the record's tracker parent
  "headline": "EPIC-1: what the delivery does",
  // "baseBranch" and "stateDirectory" are optional overrides: the binding names the base (baseBranch must be its
  // base.ref or match its bases), and the record lives in the lead's checkout (laneCheckout with the delivery key)
  // under the binding's state.directory.
  "plan": "docs/plans/epic-1.md",
  "laneBrief": "docs/plans/epic-1.lane-brief.md",          // written by lead-brief
  "brief": { "notes": "docs/plans/epic-1.lane-notes.md" }, // this delivery's own rules, appended to the brief
  "context": { "sections": ["Decisions"] },  // plan sections every review brief carries, besides the story's own
  "lanes": { "lane-1": { "owns": ["src/feature/**"] } },   // fnmatch globs (* crosses /); lead-accept enforces them
  "stories": {
    "EPIC-2": {
      "title": "What the story does",
      "lane": "lane-1",
      "label": "S1",                          // optional short name the plan uses
      "pinned": ["/api/things/[^/?]+$"],      // request patterns its in-app checks may hold or fail
      "inApp": "checks"
      // none | loaded | checks (a passing hold, a passing fail, and every pinned pattern used; the default when
      // anything is pinned) | a list of requirements, each {mode: hold|fail|loaded|any, pattern?, args?}.
      // `args` absent: any adapter arguments; `args: []`: none; otherwise those arguments, in order. Each mode
      // requirement needs its own passing run; `any` only asks that some passing run used the pattern. E.g.
      // "evidence": "video" also requires a before/after clip at hand-off (run_delivery.py app-video). lead-setup sets it
      // for every story with a pinned pattern (its checks hold or fail a request), and adds the evidence-page finish line.
      // [{"mode": "fail", "pattern": "/api/things/[^/?]+$", "args": []},
      //  {"mode": "fail", "pattern": "/api/things/[^/?]+$", "args": ["--fail-body", ""]}]
    }
  },
  "finish": [ { "id": "acs", "text": "Every acceptance criterion met on the delivery base" } ],
  "planReview": { "lenses": ["lens.outcome-correctness", "lens.adversarial-testing"] }, // the plan's own review
  "approvals": [                              // drafted by lead-setup --approvals-section; optional
    { "id": "approval-1", "text": "The Product Owner signs off the copy", "state": "waiting", "blocking": true }
  ],
  "outOfScope": ["A per-delivery base branch"], // drafted by lead-setup --out-of-scope-section; optional
  "authority": {                              // the operator's grant to lanes; absent: lanes don't push
    "push": true,                             // each lane's own story branch, never forced (run_delivery.py push)
    "draftPullRequests": true,                // its draft PR into the delivery base (run_delivery.py open-pr)
    "readyPullRequests": true,                // the lead's, never a lane's: lead-accept marks each draft ready
    "grantedBy": "the operator", "grantedOn": "2026-01-01"
  },
  "ship": {                                   // optional
    "from": "origin/main",                    // the product branch's base
    "toolingCommit": "abc1234",               // the delivery-only commit; what follows it under `paths` ships
    "paths": ["src/"],
    "branch": "ship/{delivery}",
    "checkout": ".orchestration/lanes/{branch}", // default: beside the lane checkouts
    "gateTooling": ["scripts/ci/"]            // optional: more of the base's files to lend for ship's gate run
  }
}
```

`ship` runs the prepare steps and the gates in the ship checkout, with `{base}` as `ship.from`. The ship tree carries
only the ship paths, so a gate script that lives with the delivery's tooling isn't there. `ship` lends it for the run:
each file a gate or prepare step names that only the delivery base has, every file beside it that only the base has,
and anything under `gateTooling`. The ship's own files are never replaced, so the gates check what ships. Lent files
are removed after the run, and the run is logged as `<delivery>.ship-gates.jsonl` with the lead's logs.

A story's plan context is the plan section whose heading names its key, plus table rows naming its key or label,
plus the `context.sections`. The plan has no required format beyond that.

`planReview.lenses` names the lenses that review the plan itself, through `run_delivery.py plan-review
prepare|reduce|end`: 1 to 12 of the repository's declared lenses, each with its charter. They are fixed at round 1; a
different set later is refused, so an amendment after `lead-init` is judged by the lenses that reviewed the plan, and
continues the round numbering. The review has no round bound. Its store, `<workDirectory>/review/<delivery>/plan/`
without `checkouts/`, keeps every round in full, and `lead-close` archives it with the delivery. `lead-init` refuses to
freeze the plan while the review is open; with no recorded round it freezes as before.

`approvals` and `outOfScope` reach the plan chapter as they are. `lead-setup --approvals-section NAME` drafts one
approval per top-level list item of that plan section, ids `approval-1`, `approval-2` and so on, each `waiting`;
`--out-of-scope-section NAME` drafts one text per item. A named section that is missing, or holds no list, drafts
nothing and says so. `lead-setup --force` keeps `planReview`, `approvals` and `outOfScope` from the existing file. Set
`"blocking": true` on an approval that blocks work while completing the file: the plan chapter then says "Approvals
are blocking work" until `run_delivery.py lead-plan approval <id> --state done` marks it done. An approval that isn't
blocking stays in the chapter's "Approvals ahead" fold. `lead-plan approval` commits the delivery file in the lead's
clean checkout, as `lead-plan change` does, and never pushes; an approval without an `id` is changed by hand.

## The forge contract

The command is called with one of:

- `find --source BRANCH` prints `{"url"}` for the branch's open PR, or `{}`.
- `open --source B --destination D --title T --body-file F` opens a draft and prints `{"url"}`.
- `reviews --url URL --head SHA` prints `{"checks": [{name, state: passed|failed|pending, at}], "reviews": [{author, at, body}]}`:
  the head commit's pipeline checks and the PR's top-level comments. Needed only with `review.pr`.
- `get --url URL` prints `{"url", "state": "open"|"merged"|"closed", "draft", "head", "source", "destination"}`. It
  refuses a URL that isn't one of the repository's PRs. `head` may be abbreviated, to 7 characters at least.
- `ready --url URL` marks a draft ready and prints `{"url", "draft": false}`. `lead-accept` calls it, before the queue
  may take the candidate, when the delivery's `authority` grants `readyPullRequests`. A refusal leaves the hand-off
  pending.
- `merge --url URL --strategy merge|squash [--head SHA]` merges the PR through the forge and prints `{"url", "state":
  "merged"}`, refusing a PR whose head moved from `--head`.
- `merge --source BRANCH --head SHA --strategy merge|squash` merges the branch's one open PR, at exactly `--head`. It
  refuses a branch with no open PR or with more than one, and a PR whose head isn't `--head`. A binding's `merge` step,
  which knows the branch and the checked head but not the PR, calls it directly:
  `["python3", "-B", ".agent-skills/current/skills/run-delivery/adapters/forge_bitbucket.py", "merge", "--source",
  "{branch}", "--head", "{expectedHead}", "--strategy", "merge"]`, with `FLOW_FORGE_REPOSITORY` and
  `FLOW_FORGE_TOKEN_ENV` in the binding's `environment`. A repository that merges through its forge needs no merge
  script of its own.

`FLOW_FORGE_REPOSITORY` and `FLOW_FORGE_TOKEN_ENV` carry the `forge` settings. Both shipped adapters answer every
command. With `forge.mergeNeedsReady` set, `doctor` reports a delivery without the `readyPullRequests` grant, and
`lead-init` refuses it.

## The supervisor

`lead-init` starts `lead-run`, the delivery's supervisor: one detached process per record, in a session of its own,
started with SIGINT, SIGHUP and SIGQUIT at their defaults. It holds the flow log's `<delivery>.supervisor.lock`, which
records its pid, its start time, its children and the live view's address. Its first line of output is that address,
and every lead command repeats it. It keeps the merge queue's `serve` and the live view running, and every
`supervisor.passSeconds` it runs one pass:

1. the reconcile sweep, as `lead-reconcile` runs it;
2. what `lead-cleanup` does, for each merged story whose lane has moved off it;
3. the post-merge tier: once the delivery has merged anything, and unless the base declares the `focused` posture,
   the binding's `fullHealth` at the base head when no run at that head has happened, or the lead asked for a rerun,
   in a fresh checkout of the head, under the binding's `fullHealthTimeoutSeconds` (`checkTimeoutSeconds` when
   absent; `checkTimeoutCeiling` doesn't bound it). Its result goes to the full-health log, and the journal gets
   `base.verified` on a pass, `base.broken` on a failure, or `base.repaired` on the first pass after a failed run,
   each naming the merge the run follows. A `base.broken` stops the queue until a `base.repaired`, and a pass repairs
   only a break `fullHealth` found. The run is `full-health-run`, in a session of its own that holds the flow log's
   `<delivery>.full-health.lock` while it runs, recording its pid, head and start; the pass starts it and passes
   that follow see it running, so the supervisor keeps passing beside it and the run holds the close until it ends.
   A start that finds the lock held runs nothing, a run that outlives its supervisor is adopted by the next one, and
   `lead-close` or `lead-init --replace` stops it, recording no result. `lead-run --once` waits for the run it
   starts;
4. `finish.met`, once for each finish line whose covered criteria have every covered proof done;
5. the binding's `state` publishers; then "Needs you", written over the last pass's list in the record's
   `needs-you.json`; then the binding's renderers, so the line map shows this pass's list;
6. a copy of the record and the flow log into `latest` at the archive location, under the state lock;
7. the close-out, when the close condition holds.

The close condition: every planned and queued story merged or removed, every finish line met, every lane idle or
done, and no "Needs you" item holding. "Needs you" is derived each pass, and its file is only that pass's view of it,
never read back: a failed `fullHealth` at the
current base head, unless the base declares the `focused` posture, a broken base, a blocked lane or one waiting on the lead, an unresolved `blocker.recorded`, a
child that won't stay up, and the tracker publisher's own items. `lead-resolve <journal id> --why TEXT` resolves a
blocker; `lead-resolve full-health --why TEXT` asks the next pass to rerun a failed `fullHealth` (under the `focused` posture, once
the base returns to `gate`). The runner's own
blocker clears when its hold does, or with `base.repaired`. Each item says whether it holds the close or is only
shown, where it comes from and what clears it. The line map's Now chapter lists the holding items first. A list
from a supervisor that has stopped, or one more than a pass overdue, is shown as unavailable rather than stale.

The close-out runs in order, each step succeeding before the next: `fullHealth`, once per base head, in a fresh
checkout of the base, or skipped under the `focused` posture, which after a merge has already run at that head, so
it starts a run only for a delivery that merged nothing or a head that moved since; a follow-up for each deferred finding, keyed `<story>-<finding id>`; `delivery.closed`, then
waiting for the tracker to have it; releasing the lead's leases, archiving the record and fast-forwarding the
operator's checkout if it is clean and on the base; a friction note stub beside the archive; then the supervisor
stops. Each pass writes what it did to the flow log's `<delivery>.supervisor.jsonl`.

The lead's commands keep working while it is down, and the supervisor skips what is already done when it comes
back. `lead-run --resume` stops what an earlier supervisor left running and starts it again; `lead-run --once` runs one
pass in the foreground. `lead-close` asks a running supervisor to stop after its pass, and refuses while the lead's
settings have uncommitted changes. `lead-init --no-supervisor`, or `RUN_DELIVERY_SUPERVISOR=off`, starts none.

## The delivery posture

`.agents/delivery-posture.json`, read from the tip of the delivery's own `<remote>/<base>` and nowhere else, decides
whether the post-merge tier and the close-out run `fullHealth`. It lives outside this file and the binding because a branch or a lead's
checkout mustn't be able to change it, and because `"harness": "ignore"` must still reach it:

```json
{
  "schemaVersion": "delivery-posture/1",
  "posture": "focused",
  "reason": "The gate is still being optimized."
}
```

`schemaVersion`, `posture` (`gate` or `focused`) and a non-blank `reason` are required for either posture, and any other
member is ignored. `full_health_step`, "Needs you" and `lead-close` each read it fresh, with a fetch bounded at 15 seconds;
offline, the last fetched tip decides.

| What the base holds | Posture | Code |
| --- | --- | --- |
| A valid `focused` declaration | `focused` | none |
| A valid `gate` declaration | `gate` | none |
| No file at the tip | `gate` | `posture_undeclared` |
| Not valid UTF-8 JSON, an object failing the shape above, or an unknown `posture` | `gate` | `posture_malformed` |
| A symlink, tree or submodule at that path, or a blob over 64 KiB | `gate` | `posture_malformed` |
| The base ref can't be resolved or read, or `git` fails or times out | `gate` | `posture_unreadable` |

Under `focused` no run starts and a failed run at that head holds nothing; nothing runs instead. The skip goes in the
full-health log with no `result`. The first skip journals one `decision.recorded`, as does each later change of posture
at close-out. The close-out summary, `delivery.closed`, `closed.json` on both close paths and the plan chapter's `closeOut` say
the run was skipped, naming the source and the reason. A later `gate` starts a real run, even at the same head, and a
result clears `closeOut`. Under `gate` nothing changes, and `posture_malformed` or `posture_unreadable` is named once in
the journal and on each close-out line.

## Tracker updates

How a delivery's tracker updates (the orchestration guide's tracker-update table) and moves get made, by
`tracker.updates`:

- **`publisher`**: the tracker publisher, `tracker_publisher.py` beside `run_delivery.py`, posts them through a tracker adapter. The
  binding names it in `state.publishers`, so it runs whenever the render wrapper runs; `lead-setup` adds it when the
  configuration names an adapter: `{"id": "tracker", "argv": ["python3", "-B",
  ".agent-skills/current/skills/run-delivery/scripts/tracker_publisher.py", "--state-dir", "{stateDirectory}"]}`.
- **`host`**: a tracker reachable only through the host's connector (Linear, by this corpus's rule) uses a host
  publisher, `{"id": "tracker", "kind": "host"}`, whose outbox the lead posts with the host's tracker tools.
- **`manual`**: the lead posts them by hand. Without it, `doctor` fails and `lead-init` refuses a tracker with an
  adapter but no tracker publisher, or a host-only tracker without a host publisher.

**The ledger.** The publisher keeps `tracker-published.jsonl` beside the record, one JSON line per journal id and
outcome: `{"journal", "init", "outcome", "writer", "at", "ticket"?, "action"?, "move"?, "transition"?, "reason"?, "error"?,
"final"?}`. The outcomes are `comment`, `transition` and `create` for an action taken, `skip` for an entry with nothing
to post, `handled` for a move not offered from the ticket's status, and `failed` for a permanent rejection. Every entry
gets at least one line; the ledger covers an entry once a command's line names it or the publisher's line for it is
`final`. `init` is the record's init id (below): a ledger left beside a replaced record covers none of the new
record's entries. A lane or lead command that posts or moves for an entry it journals takes the ledger's lock
(`tracker-published.lock`, waiting for it) across its journal write, its tracker call and its line, writes the line
with its own `writer` and the `ticket`, and `"move": "review"` for a review move; when the call fails it writes no line
and the publisher posts it later. A run that finds the lock held exits 0 at once.

**What is posted.** Entries are taken in id order. A transient failure stops the run, and the next retries from there:
a network error, a timeout, a 5xx, 409, 423, 429 or 401, or a 403 that the status read of the epic (or, with no epic or
an unreadable one, of the project) also gets. Any other 4xx writes `failed` and the run goes on; nothing is journalled,
and the failure shows in "Needs you". Each comment is cut to the adapter's `commentLimit`, with a pointer to the record.
A `decision.recorded` entry is posted only when its journal id is in the flow log's `<delivery>.tracker-marks.jsonl`
(`{"journal", "followup"?: {"story", "finding", "title"}}`), which the command that journals it writes while holding
the ledger's lock: `lead-plan change` does. A mark with a `followup` files one follow-up item, labelled
`agent-followup-<story>-<finding id>`, before posting. Every other unmarked decision gets a `skip` line.

**Moves.** A story's `lane.assigned` waits for its start, a line `{"subject", "lane"?, "at"}` in the flow log's
`<delivery>.lane-starts.jsonl`, at or after the entry. It then gets the start update and the `start` moves, once per
story: never after the ledger holds the story's start or review move, and never when the ticket carries a start
marker from the same delivery under an earlier record. An entry whose story is handed off, merged, reassigned or
removed before it starts gets a `skip` line. The `lead` moves are made for their kinds. Each move reads the ticket's
status first: a move whose target status the ticket already has is skipped and reported as made by someone else, and
one the tracker doesn't offer from the current status is `handled`: shown, never retried or journalled. A move's
target is what the tracker lists for it, or else its `targets` entry; with neither, a move not listed is `handled`.

**Markers.** Each comment and move carries a marker the adapter stores on the ticket:
`agent-delivery:<delivery>:<init id>:<journal id>:<action>`, where the init id is the time of the record's first
journal entry, digits and letters only, and the action is `comment` or `transition:<name>`. Before writing, the
publisher reads the ticket's markers, so a run that died between a write and its line never posts it twice.

**Lane commands.** `lane-start <key> --base <base>` begins a story: it checks the base against the binding's, makes
the story's checkout from it, runs the prepare step, sets the lane's focus, and records the story's start in the
`lane-starts` file. It makes no tracker call: the publisher's start move and update follow the record. Run again, it
changes nothing. `open-pr` and `pr-review` make the review move at the moment `reviewWhen` names, through the
publisher's move rules, with ledger lines of journal id 0 (`move: "review"`, the story as `ticket`), since that move
journals nothing; a refused move writes no line, and running the command again, or `handoff`, retries it. `handoff`
posts the hand-off update on the story, under its `candidate.handed` entry, holding the ledger's lock across the
journal write, the post and the line; when the tracker refuses it, the hand-off stands and the publisher posts it
later. With a tracker reachable only through the host, the commands print each move and post for the lane to make,
and record them in the flow log's `<delivery>.lane-prints.jsonl`. `lead-setup --review-when` records `reviewWhen`;
it is never inferred from `review.pr`.

## The tracker adapter contract (`tracker-adapter/1`)

The command is called with one of these, and prints one JSON object on stdout:

- `status --ticket KEY` prints `{"status", "transitions": [{"name", "to", "offered"}], "commentLimit"}`: the ticket's
  status, and the transitions the tracker lists for it, each with its target status (or null) and whether it is
  offered now. A tracker may list only the moves out of the current status, as Jira does; `commentLimit` counts
  UTF-16 code units.
  `status --project P` prints `{}` when the project can be read.
- `markers --ticket KEY` prints `{"markers": [...]}`, every marker stored on the ticket, its comments' included.
  `markers --project P --marker LABEL` prints `{"items": [{"key"}]}`, the project's items carrying that label.
- `comment --ticket KEY --marker M --body-file F` posts the file as a comment, storing the marker with it.
- `transition --ticket KEY --name NAME --marker M` makes the move, storing the marker with it.
- `create --project P --title T --body-file F --marker LABEL [--parent KEY] [--type TYPE]` files an item carrying the
  label, and prints `{"key"}`.

A failure exits non-zero and prints `{"error", "httpStatus"}`, `httpStatus` null when the tracker wasn't reached. The
shipped Jira adapter, `tracker_jira.py` in the skill's adapters folder, takes `--site`, `--user-env` and `--token-env` before the operation,
as the delivery-views Jira snapshot does; it stores markers as issue properties and on the comment or move itself,
and labels follow-ups, which it finds by JQL within the project.

## The in-app check contract

**The spec.** `{spec}` is a JSON file:

```json
{"path", "holds": [re], "fails": [re], "clicks": [css], "evals": [js], "afterEvals": [js], "for": css, "wait": ms, "afterFor": css}
```

The spec alone decides these. The check must ignore the same flags in `{args}`, and `run_delivery.py` refuses them there.

The check must behave as follows:

- Patterns match request URLs without their query strings, and only API (XHR/fetch) requests.
- Held requests stay pending until every eval has run. A matching request that starts after the release isn't held:
  it is listed with `afterRelease: true`.
- Failed requests get an error response the app can read, CORS included.
- Evals return JSON values. A rule passes only on exactly `true`.

**Video (optional).** A spec may carry `"video": DIR` and `"settle": ms`. The check then records the whole run
into DIR, keeps the page on screen for `settle` ms after the last step, and reports the file as `"video"`.
It may also report `"signedIn"`: a video's "before" page may legitimately render nothing, so `app-video`
checks `signedIn` (falling back to `ready`) rather than `ready`.
`app-video` and `lead-evidence` need it; `adapter-test` doesn't check it. ffmpeg stitches the two recordings.

**The report.** The check prints JSON on stdout:

```json
{"ready", "build", "held": [{"regex", "url", "heldAt", "releasedAt", "afterRelease"}],
 "failed": [{"regex", "url", "status", "at"}], "evals": [{"expr", "result", "evalAt"}],
 "afterEvals": [{"expr", "result", "afterAt", "firstResult", "polls"}], "afterForFound", "alertsSeen",
 "pageErrors", "consoleErrors"}
```

- `ready` means the app rendered and is signed in.
- `build` is the `/.flow-build.json` the server served.
- `alertsSeen` lists every `[role=alert]` with text seen during the check: added, made an alert, or changed. It is
  required: a hold check fails without it, or on any entry.
- Each `failed` entry has its time (`at`). A fail check needs every failure to come before the first eval.

**The build.** `run_delivery.py build` writes `<artifact>/.flow-build.json` with `{head, tree}`, and so does a gate that is
the build. The serve command must serve that file at `/.flow-build.json`.

`run_delivery.py adapter-test` runs a repository's serve and check adapters against a fixture page (the skill's `assets/adapter-fixture/` folder), under
the lease, and fails if they break this contract.
