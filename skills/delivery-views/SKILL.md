---
name: delivery-views
description: Give a repository that delivers epics through orchestrate-work a ready-made live line map, delivery history page and tracker and forge snapshots, configured rather than coded.
---

# Delivery Views

Ready-made views and snapshot commands for a repository that delivers an epic
through `$orchestrate-work`. The repository writes configuration; it writes no
view or snapshot code of its own. This skill holds everything provider- or
web-specific, so the core workflow stays neutral.

GitHub snapshots retain `mergedAt` from the source. The line map uses a recovered
merge's `occurredAt` for landing times and retains journal `at` as observation
time in the activity feed. A missing source time is shown as unknown. Recovery
does not extend a single-repository snapshot to other repositories: supply a
separate verified forge snapshot for work that the configured repository cannot
list, then reconcile it through the state tool.

Detail times follow the work item's current stage and recorded attempt:
building shows the recorded assignment as Started, review its round start,
queued its hand-off, checking its latest check start, checked its matching
finish, stopped its stop, and landed its merge occurrence. Exact timestamps
and their source appear in tooltips and accessible labels. Missing times stay
unknown; planned work has no start label, and a mutable lane focus time never
supplies a work item's start. The journey's intermediate milestone label uses
its own row so it cannot collide with the finish label.

## Dependencies

- `$orchestrate-work` owns the delivery state, its binding, the render wrapper
  and the live server. These views are argv renderers and snapshot commands in
  its binding, run and bounded by its state tool.

## Declaring the views

A repository declares each view as an argv renderer in its binding's `state`,
and may name one as the live server's front page:

```json
"renderers": [
  { "id": "line-map", "argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/render.py", "line-map", "--config", ".agents/delivery-views.json", "--binding", ".agents/orchestration.json", "--state-dir", "{stateDirectory}"], "output": "line-map.html", "timeoutSeconds": 30 }
],
"liveHome": "line-map"
```

Start `serve`, `live` and renders from the repository's top level: the
binding names each script by its path in the repository, and the render
wrapper and the state tool's snapshot commands pass no working directory of
their own. Each
script here then resolves the top level itself (`git rev-parse
--show-toplevel`) and resolves `--config`, `--binding`, `--state-dir` and
`{root}` against it, so run by hand it works from any directory inside the
repository.
A `--config` or `--binding` that is not found exits non-zero naming the path.
The record is the binding's `state.directory` unless `--state-dir` names another;
pass `--state-dir {stateDirectory}` in the renderer and snapshot argv so a
repository serving several records reads, and caches runs for, the one served.

Check a configuration before declaring it:

```bash
python3 -B .agent-skills/current/skills/delivery-views/scripts/views_common.py check --config .agents/delivery-views.json --binding .agents/orchestration.json
```

## The line map

`scripts/render.py line-map` reads the delivery record on stdin and prints one
page: the delivery told in six chapters (now, the line, who, what's left, what
happened, parked), and a seventh, the plan, when the delivery has one (see
"The plan chapter" below), with a detail panel for each piece of work. Its style and
program are the skill's `assets/line-map.css`, `assets/line-map.js`,
`assets/run-summary.css` and `assets/run-summary.js`, inlined into the page, and
it loads only its two typefaces from Google Fonts. It
opts in to the live stream, so `live` serves it at `/view/<output>` and the
page follows every record write; opened as a file, it shows the record as it
was rendered. A history renderer declared in the same binding (`render.py
history`) is linked from its footer.

A piece whose queue or planned element names a `runId` shows that run in its
detail: its review rounds as a track, and a card for the picked round. With
`runs` configured, the renderer appends these arguments to its argv and reads
JSON from each: `runs list --json` once a render, `runs view <id> --json` for
a run the list shows changed since its cached summary, and `runs artifact <id>
<artifact> --json` for each round report the view references and each brief and
result a report names. A run that cannot be read shows as unavailable; without
`runs` there is no run section at all. All of a render's run commands share the
deadline under "Runs, cache and storage" below; `--deadline-seconds` sets it.

A round whose report names a `briefs` member retains each reviewer's brief and
result in its embedded evidence. The Brief / Result control row is hidden;
reviewer verdicts, findings and fix closures remain visible. Stored evidence is
not deleted. Text remains bounded to 64 KiB at a character boundary. The
embedded run data keeps within 4 MiB: run summaries come first, the oldest
stops' runs becoming unavailable when they alone are over it, then brief and
result texts from the newest round backwards. The record shows what was stored
as each lens's brief and result, as stored; it does not prove the lens received
or followed it.

## The history

`scripts/render.py history` prints the delivery history: past deliveries and
runs, newest first, each opening the line map's run summary in place. Its
style and program are `assets/history.css` and `assets/history.js` with the
run summary's two assets, inlined into the page. Declare it beside the line
map; the line map links to it, and it links back to the binding's line map, as
a file and, when served, at the line map's `/view/` address:

```json
{ "id": "history", "argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/render.py", "history", "--config", ".agents/delivery-views.json", "--binding", ".agents/orchestration.json", "--state-dir", "{stateDirectory}"], "output": "delivery-history.html", "timeoutSeconds": 60 }
```

It lists every run `runs list --json` shows, read with `runs show <id> --json`
(reused from its own cache in the state directory while the inventory shows
the run unchanged), then every `.json` file in the `exports` directory that no
`ignore` pattern matches and the product has not already listed: a run export,
or an older telemetry record (`schemaVersion: 1` with a branch), its ticket
read from the branch by the tracker's key pattern. A run or file
that cannot be read is listed with a note. The record on stdin only names the
delivery each run belongs to. Its run commands share the render's deadline, and
past the 4 MiB page budget the oldest rows give up their summaries first.
`--now` fixes the history's as-of time, for tests.

## The plan chapter

A delivery may also say what it committed to. The line map then gains a
chapter, **The plan**, that answers one question for the engineer working with
the agents: is the work still what was agreed, and what proves it? The chapter
reads one input, `delivery-plan/1`, beside the record the page already reads.
It knows nothing about any repository, tracker, plan format or kind of evidence
beyond what that input declares. `$run-delivery` produces the input; a
repository that writes it any other way gets the same chapter.

### The input: `delivery-plan/1`

The renderer reads `<state directory>/plan.json`: one JSON object, at most
1 MiB, refused unread if larger. Its schema is
`references/delivery-plan.schema.json`. Check a file before relying on it:

```bash
python3 -B .agent-skills/current/skills/delivery-views/scripts/views_common.py check-plan <state directory>/plan.json
```

It exits 0 with a summary, or 2 naming the failing key path without echoing
any value.

| Field | Contents |
| --- | --- |
| `schemaVersion` | `delivery-plan/1`. |
| `updated` | When the producer last wrote it. |
| `outcome` | `{summary, text}`: one sentence, and the full text. |
| `requiredLenses` | Lens ids every approving review round must include. |
| `proofKinds` | Ordered `{id, label, source}`, with `source` either `record` or `producer`. Columns follow this order. |
| `units` | Ordered `{subject, label?, title, removed?, criteria: [{id, text}], proofs: [kind ids owed]}`. |
| `finish` | Ordered `{id, label, text, at, covers: {criteria: [ids] \| "all" \| [], proofs: [kind ids]}}`. `id` is the record's finish line id; `[]` covers no criteria by design. |
| `approved` | `{at, review?, snapshot: {units, finish}}`, frozen once at the start of the delivery. |
| `changes` | Ordered `{id, at, kind, subjects, criteria?, by, why?, title?, source?, ref?, before?, after?}`, `kind` one of `added`, `removed`, `relaxed`, `changed` or `finish`; `by` may be `unrecorded`. |
| `evidence` | `{<subject>: {<producer kind>: {state, head?, at?, reason?, items?}}}`, `state` one of `done`, `todo` or `waived`. `done` and `waived` need `head`; `waived` also needs `reason`. |
| `approvals` | Optional, ordered `{id?, text, state: done \| waiting, who?, blocking?}`. `id` is unique; `blocking` is false when absent. |
| `outOfScope` | Optional list of texts. |
| `planReview` | Optional: how the plan came to be approved. `{lenses, state, endedReason?, rounds}`, below. |

Texts are capped at 600 characters, `outcome.text` at 2,000 and labels at 200;
lists at 200 units, 1,000 criteria, 500 changes and 30 finish lines. Ids match
`[A-Za-z0-9._-]{1,64}` and criterion ids are unique. Unknown keys are refused,
and so is a record kind other than `handed`, `merged` and `review`. Subjects
are data only: the page finds units through its own map, never through a
selector built from one, and sets every text as text. A `ref` becomes a link
only when the renderer already links that change request.

`planReview` holds what the plan file keeps of an unbounded review. `lenses` is
the review's ordered lens ids, 1 to 12, fixed for the review. `state` is
`aligned`, `open` or `ended`, checked against the last entry: `aligned` needs
every lens's verdict `approve` there, `open` at least one that isn't, and
`ended` needs `endedReason`, at most 600 characters. `rounds` holds at most 32
entries, each `{round, fromRound?, candidateRef, at, outcome, verdicts,
findings, raised, settled, clipped?}`: the run summary's round shape. `verdicts`
maps each lens to `approve`, `changes` or `unavailable`; `findings` counts
`P0` to `P3`; `raised` lists at most 30 `{id, lens, severity, summary, state:
unresolved | resolved}`, summaries at most 280 characters; `settled` lists at
most 30 `{id, lens, state: fixed | dropped | open, fixCommit, resolution}`,
with `fixCommit` a full commit id exactly when the state is `fixed`. A plan has
no deferral path. Entries cover rounds 1 to the last entry's `round` in order,
with no gap or overlap; an entry with `fromRound` is a fold of rounds
`fromRound` to `round`, lists nothing, carries its last round's verdicts and
candidate and its rounds' summed counts, and is never the last entry.
`clipped` is `{findings, closures, texts}`: what the entry doesn't list, and
whether its texts were shortened. Serialized, `planReview` is at most 256 KiB.
run-delivery's producer fits every review to these caps; any producer that
writes the same keys gets the same fold.

With no `plan.json`, the page is exactly as it was: no chapter, no rail entry,
no line in Now, and What's left keeps its finish-line list. An invalid file
renders as if absent, plus the chapter holding one flag, "The plan couldn't be
read", with the key path. A valid plan gets a fixed 512 KiB share of the page,
taken from the runs' budget. Over its share, the plan review's texts are
clipped first, the oldest rounds' resolutions then their summaries, then the
oldest changes' text, then other text outside the approved snapshot. The
snapshot, every count, and each review entry's coverage, verdicts and listed
fix commits are kept, and the changes fold says what was clipped.

### The chapter

It sits between What's left and What happened. Open by default: the title
("N of M criteria proven", adding ", W by waiver" when a proof was waived), the
outcome's summary, what needs the engineer, the units not yet proven with each
one's next missing proof, and one meter per finish line. With a `planReview`,
one line beside the approval reads "Approved <date> after N review rounds",
then "· every lens aligned", "· ended without alignment" or "· still open"; N
is the last entry's `round`. Without one, the approval keeps its own `review`
text, in the units fold. Folded, each with a count: all units (with an "as
approved / as amended" switch), with a review "Plan review (N rounds)", drawn
by the run summary's track and cards (a fold reads "Rounds a–b" with its
counts, an entry says what it doesn't list, the summary says the review is
unbounded, and an ended review shows its reason), changes since
approval (a scope-over-time chart and word redlines from `before` and
`after`), waived proofs with their reasons, approvals ahead, out of scope, and
the full outcome. Open folds are remembered per viewer under the page's theme
key, and the page works without storage. Now gains the outcome's summary and
"N of M criteria proven · plan changed K times"; each stop on The line opens its
unit; and What's left shows the meters instead of its finish-line list. At
phone width each unit becomes a card, with its proofs as labelled pills.

A criterion is **proven**; a finish line is **met**, as the record says.

### Proofs from the record

Record proofs are derived in the page from every record it draws, embedded or
pushed, by the same lookups The line uses:

- **`handed`** is done when a queue item exists for the subject, its stage is
  not `stopped`, and it is not pending acceptance.
- **`merged`** is done when the queue item's stage is `merged`. A hand-off after
  a merge supersedes it, and a `merged_unverified` stop leaves it open.
- **`review`** approves from every `requiredLenses` lens, at the queue item's
  `head`: the reviewed and handed head, never `syncedHead`, so a synced or
  merged unit keeps its proofs. Its source is the first that applies: the
  run's latest round, with the journal's verdicts laid over it; an available
  review observation of the queue item's `head`; then the journal's
  `review.round` entries. Rounds the lane only reported carry no verdicts. A
  round that names no candidate counts only when it was recorded after the
  `candidate.handed` before the current one; a unit handed off once counts
  every round. The review reads **stale** only when a source positively shows
  approval for another head, **open** for a round that hasn't approved, and
  **not recorded** for nothing.

Producer proofs come from `evidence`. A missing entry is `todo`. Once the
subject has a queue item, a `done` or `waived` entry whose `head` differs from
that item's `head` is stale and counts as not done; before hand-off an entry
counts at face value, and the unit's open row says "before hand-off".

### Definitions

- A **unit is proven** when every proof it owes is done or waived and none is
  stale; a **criterion is proven** when its unit is. Removed units count in
  neither N nor M.
- A **finish line's meter** fills by the share of covered criteria whose units
  have every `covers.proofs` kind done, and a second segment adds those with
  the kinds done or waived, so the meter and the title agree. A kind a unit
  doesn't owe is settled for it. `"all"` covers every criterion; `[]` covers
  proofs only, over every unit. Whether the line is met comes only from the
  record's finish line with the same id; a record finish line with no plan
  entry still shows, with no fill.

### What needs the engineer

The chapter's attention area shows these flags, in this order, and with none it
reads "Nothing in the plan needs you right now."

1. **The plan couldn't be read.**
2. **The base is broken**: the last base event in the record is `base.broken`.
   While it holds, the title can't read "Every criterion is proven".
3. **Approvals are blocking work**: an `approvals` entry is `blocking` and
   `waiting`. The flag names each. A waiting approval that isn't blocking
   raises nothing and stays in "Approvals ahead".
4. **Work in the record isn't in the plan**: a queue or planned subject has no
   unit. The flag names the subjects.
5. **The plan and the record disagree on finish lines**: a finish line in the
   plan that the record lacks, or the reverse.
6. **A finish line no longer covers the plan**: its `covers.criteria` is an
   explicit, non-empty list that omits a criterion added or changed after its
   `at`. Lines covering `"all"` or `[]` never raise it.
7. **A change touches open work**: a `relaxed`, `changed` or `removed` change
   names a unit that is neither proven nor removed. It clears once that unit's
   review approves at a time after the change.
8. **A change has no reason**: its `why` is absent.
9. **A waived proof on a unit not yet proven.**

### Live

The page embeds the validated plan once and derives record proofs from each
pushed record, so the chapter redraws as The line does. A producer writes
`plan.json` only when its content, apart from `updated`, changed: atomically
first, then a decision entry in the delivery journal whose summary starts "Plan updated:",
names the new `updated` value and says what changed. That moves the record, so
the live server renders the page again from the new file. An open page shows
"The plan changed. Reload to see it." beside the title when the latest "Plan
updated:" entry in a pushed record names an `updated` value other than its
embedded plan's; a page rendered or reloaded after the change shows none.

## Configuration: `delivery-views-config/1`

`.agents/delivery-views.json`; every key but `spec` is optional.

| Key | Contents |
| --- | --- |
| `spec` | `delivery-views-config/1`. |
| `title` | The pages' title, at most 80 characters; `Delivery` when absent. |
| `tracker` | `{name, issueUrl, keyPattern?}`. `issueUrl` takes `{key}`; an item is linked only when `keyPattern`, at most 200 characters, matches the whole key. The host takes no placeholder. |
| `forges` | Each `{name, repository, pullUrl, commitUrl}`. `pullUrl` takes `{repository}` and `{number}`; `commitUrl` takes `{repository}` and `{sha}`. |
| `runs` | `{argv, timeoutSeconds?}`: the command that prints a delivery run, `{root}` being the repository top level; `timeoutSeconds` is 10 when absent. Absent: no run section and no command run. |
| `findings` | `{reportSpecs}`: the round report specs read, in preference order; `["review-round-report/1"]` when absent. |
| `lenses` | `{"<lens id>": {name, icon?}}`. `icon` names a shipped icon (`bullseye`, `spy`, `shield`, `bug`, `gauge`, `lock`, `book`); a lens with none shows its initial. `lens.outcome-correctness` and `lens.adversarial-testing` have defaults. |
| `scrubEnv` | Environment variable names removed from every `runs` child's environment, so provider tokens reach only the snapshot commands. |
| `exports` | History only: `{directory, ignore?}`, a repository-relative directory of exported runs and the file patterns it skips. |

A refusal names the key path and exits non-zero; the render wrapper records
the failed renderer and keeps serving. Refused: an unknown key; a URL template
whose scheme is not `https` or whose host is empty; a placeholder the key does
not take; a `keyPattern` over 200 characters or one that does not compile;
text over its cap.

## Links

Placeholder values are percent-encoded as path segments, and `repository`
keeps its single `/`. A value, or a repository segment, of `.` or `..` renders
as text, since a browser follows such a segment even when encoded. A produced URL is used
only when its scheme, host and port equal the template's, `:443` counting as
no port; otherwise the value renders as text. A change request is linked when
it is `#<n>` or `<repository name>#<n>` on the first forge, or a URL one
forge's `pullUrl` matches; a fix commit only when it is 40 lowercase hex.
A matching URL may name any safe canonical owner/repository on that configured
origin and template; no per-repository configuration is needed. Shared link
metadata carries the number, decoded repository and URL. The page shows `#N`
with the repository in its title and accessible name. Unknown origins, paths
or unsafe encodings remain text.

## Runs, cache and storage

Every `runs` command runs bounded: its own process group, `timeoutSeconds`, a
16 MiB output cap, stdin closed and the `scrubEnv` variables removed. A
timeout, oversize output, a non-zero exit or output that is not JSON makes
that run unavailable and never fails the render. All of a render's runs share
one deadline: the renderer's `--deadline-seconds`, 20 when absent, capped at
the renderer's declared `timeoutSeconds`, read through `--binding`, less 5
seconds. Runs are read six at a time, so one slow or hanging run holds only
its own slot. The line map keeps a run's latest 12 rounds; the history keeps
every round.

The run cache is `<state directory>/.cache/delivery-views/runs.json`, spec
`delivery-views-runs/2`, written atomically; a corrupt cache is rebuilt and an
unwritable one only costs a re-read. A page keeps its theme under
`delivery-views:<first 16 hex of the SHA-256 of the resolved state
directory>:theme`, so two repositories served from one address never share it.

## Snapshot commands

`scripts/snapshot.py --kind jira|bitbucket|linear|github` prints one `work-snapshot/1` for the
binding's `trackerSnapshot` or `forgeSnapshot`, exits 0, and prints nothing
else; on failure stdout is empty, the exit code is non-zero and stderr holds
one line naming the cause. Every kind takes `--timeout-seconds`, one deadline
for the whole run (25 when absent; set it below the binding's
`timeoutSeconds`), and `--now`, the snapshot's time, for tests.

- Credentials come only from the environment variables `--token-env` and
  `--user-env` name; a missing one fails naming it. Requests carry them only
  to the provider's own origin, and a redirect or next page on another origin
  is refused. No token, nor its encoded form, is ever printed.
- Paging runs until done; reaching 500 items fails. HTTP 429 and 5xx are
  retried at most twice, honouring `Retry-After` within the deadline; 401 and
  403 fail at once.
- A field the mapping needs that is missing or unrecognised fails the
  snapshot; nothing is defaulted.

**Jira Cloud** (tracker): `--kind jira --site https://<site>.atlassian.net
--parent {parent} --user-env JIRA_EMAIL --token-env JIRA_API_TOKEN
[--canceled-statuses "Won't Do,Duplicate"] [--blocks-link Blocks]`. The
parent's children come from the enhanced JQL search, paged by
`nextPageToken`, with Basic email and API token. A status listed as canceled
is `canceled`; otherwise its category maps `new` to `backlog`,
`indeterminate` to `started` and `done` to `completed`. The issue's summary
is the title, its description flattened to plain text of at most 1000
characters is the summary, and `blockedBy` holds the other children linked
inward by the blocks link type.

**Bitbucket Cloud** (forge): `--kind bitbucket --workspace <workspace>
--repo <repository> --binding .agents/orchestration.json --state-dir
{stateDirectory} --subject-pattern "<regex>" --token-env BITBUCKET_TOKEN [--user-env BITBUCKET_EMAIL]
[--since <ISO time>] [--host bitbucket.org]`. With `--user-env` it uses
Basic (an API token with its email), without it Bearer (a workspace or
repository access token). Pull requests in any state created on or after the
since time (`--since`, else the record's first journal entry) are matched to
a subject the record holds by the pattern on the title, then on the source
branch. For a subject with several, the most recently updated open one wins,
else the most recently updated merged one, else the most recently updated.
`OPEN` is `open` (`draft` when marked draft), `MERGED` is `merged`, and
`DECLINED` and `SUPERSEDED` are `closed`. `changeRequest` is the pull
request's page, kept only on `--host`; `head` is the source commit resolved
to its full hash, left out when it cannot be resolved. A pull request created
before the since time, for a ticket moved into the epic later, needs
`--since`. Keep `--subject-pattern` simple: it runs against every pull
request's title and branch.

**Linear** (tracker): `--kind linear --parent {parent} --token-env
LINEAR_API_KEY`. The parent's children come from Linear's GraphQL API, paged
by cursor, with the personal key as the Authorization header; a child listed
twice is kept once. State types map `triage`, `backlog` and `unstarted` to
`backlog`, and `started`, `completed` and `canceled` to themselves. The
summary is the first paragraph under the description's `## Summary` heading,
as plain text, at most 1000 characters, and `blockedBy` holds the issues
related to it as blockers, other than the parent. Any `lin_api_` key in a refusal is redacted.

**GitHub** (forge): `--kind github --owner <owner> --repo <repository>
--binding .agents/orchestration.json --state-dir {stateDirectory}
--subject-pattern "<regex>" --token-env GITHUB_TOKEN [--since <ISO time>] [--host github.com]`. Pull requests come
from the REST API, newest first, paged by the `Link` header, with a Bearer
token, until one was created before the since time (`--since`, else the
record's first journal entry). A pull request counts for each subject its
title names in opening brackets, as `[A-1]: ...` or `[A-1, A-2]: ...`, that
the record holds and the pattern matches. For a subject with several, an open
one wins, else the latest merged, else the latest closed, by when it merged
or closed. An open pull request is `open` (`draft` when marked draft), a
closed one `merged` when it merged and `closed` otherwise. `changeRequest` is
its page, kept only on `--host`; `head` is its head commit, left out when it
is not a full hash. A pull request created before the since time, for a
ticket moved into the epic later, needs `--since`.

## Adopting the views

A repository adopts these views with configuration alone: install a release
whose profile includes `delivery-views`, write `.agents/delivery-views.json`,
and declare the snapshot commands, and the views, in the binding's `state`.
It writes no view or snapshot code of its own.

- **Start at the top level.** Run `serve`, `live` and renders from the
  repository's top level, where the binding's script paths lead; the scripts
  resolve every other path in the configuration and the binding from there.
- **Credentials by environment variable.** Export each token the snapshot
  commands name before starting `serve`, which runs them; nothing goes in the
  committed binding or configuration. List the same variables in `scrubEnv`
  so no `runs` child sees them, and start `live`, which runs no snapshot
  command, without them.
- **Check before declaring.** `views_common.py check`, above, validates the
  configuration and names where the views read and write.
- **The record's start.** A forge snapshot takes pull requests created since
  the record's first journal entry. A new record's journal is empty, so give
  `--since` until it has one, and for a ticket moved into the epic with older
  pull requests.

### Jira and Bitbucket

`.agents/delivery-views.json`:

```json
{
  "spec": "delivery-views-config/1",
  "title": "Example app delivery",
  "tracker": {
    "name": "Jira",
    "issueUrl": "https://example.atlassian.net/browse/{key}",
    "keyPattern": "^(APP|ABC)-[0-9]+$"
  },
  "forges": [
    {
      "name": "Bitbucket",
      "repository": "example/app",
      "pullUrl": "https://bitbucket.org/{repository}/pull-requests/{number}",
      "commitUrl": "https://bitbucket.org/{repository}/commits/{sha}"
    }
  ],
  "scrubEnv": [
    "JIRA_API_TOKEN",
    "JIRA_EMAIL",
    "BITBUCKET_TOKEN",
    "BITBUCKET_EMAIL"
  ]
}
```

In the binding's `state`, with `{parent}` the record's `meta.trackerParent`
(here the Jira epic's key):

```json
{
  "renderers": [
    {
      "id": "line-map",
      "argv": [
        "python3",
        "-B",
        ".agent-skills/current/skills/delivery-views/scripts/render.py",
        "line-map",
        "--config",
        ".agents/delivery-views.json",
        "--binding",
        ".agents/orchestration.json",
        "--state-dir",
        "{stateDirectory}"
      ],
      "output": "line-map.html",
      "timeoutSeconds": 30
    },
    {
      "id": "history",
      "argv": [
        "python3",
        "-B",
        ".agent-skills/current/skills/delivery-views/scripts/render.py",
        "history",
        "--config",
        ".agents/delivery-views.json",
        "--binding",
        ".agents/orchestration.json",
        "--state-dir",
        "{stateDirectory}"
      ],
      "output": "delivery-history.html",
      "timeoutSeconds": 60
    }
  ],
  "liveHome": "line-map",
  "trackerSnapshot": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
      "--kind",
      "jira",
      "--site",
      "https://example.atlassian.net",
      "--parent",
      "{parent}",
      "--user-env",
      "JIRA_EMAIL",
      "--token-env",
      "JIRA_API_TOKEN",
      "--canceled-statuses",
      "Won't Do,Duplicate",
      "--timeout-seconds",
      "25"
    ],
    "timeoutSeconds": 30
  },
  "forgeSnapshot": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
      "--kind",
      "bitbucket",
      "--workspace",
      "example",
      "--repo",
      "app",
      "--binding",
      ".agents/orchestration.json",
      "--state-dir",
      "{stateDirectory}",
      "--subject-pattern",
      "(APP|ABC)-[0-9]+",
      "--token-env",
      "BITBUCKET_TOKEN",
      "--user-env",
      "BITBUCKET_EMAIL",
      "--since",
      "2026-10-19T00:00:00Z",
      "--timeout-seconds",
      "25"
    ],
    "timeoutSeconds": 30
  }
}
```

Export `JIRA_EMAIL` and `JIRA_API_TOKEN` (an Atlassian API token) and
`BITBUCKET_EMAIL` and `BITBUCKET_TOKEN` (an API token used with the email for
Basic authorization; leave out `--user-env` and `BITBUCKET_EMAIL` to use a
workspace or repository access token as Bearer instead).

### Linear and GitHub

`.agents/delivery-views.json`, here with the delivery harness's runs and run
exports:

```json
{
  "spec": "delivery-views-config/1",
  "title": "Delivery",
  "tracker": {
    "name": "Linear",
    "issueUrl": "https://linear.app/example-team/issue/{key}",
    "keyPattern": "^ENG-[0-9]+$"
  },
  "forges": [
    {
      "name": "GitHub",
      "repository": "example-org/example-repo",
      "pullUrl": "https://github.com/{repository}/pull/{number}",
      "commitUrl": "https://github.com/{repository}/commit/{sha}"
    }
  ],
  "runs": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current",
      "--root",
      "{root}",
      "harness"
    ],
    "timeoutSeconds": 10
  },
  "scrubEnv": [
    "LINEAR_API_KEY",
    "GITHUB_TOKEN"
  ],
  "exports": {
    "directory": "telemetry/delivery-runs",
    "ignore": [
      "delivery-record--*"
    ]
  }
}
```

In the binding's `state`, with `{parent}` the Linear parent issue's
identifier:

```json
{
  "renderers": [
    {
      "id": "line-map",
      "argv": [
        "python3",
        "-B",
        ".agent-skills/current/skills/delivery-views/scripts/render.py",
        "line-map",
        "--config",
        ".agents/delivery-views.json",
        "--binding",
        ".agents/orchestration.json",
        "--state-dir",
        "{stateDirectory}"
      ],
      "output": "line-map.html",
      "timeoutSeconds": 30
    },
    {
      "id": "history",
      "argv": [
        "python3",
        "-B",
        ".agent-skills/current/skills/delivery-views/scripts/render.py",
        "history",
        "--config",
        ".agents/delivery-views.json",
        "--binding",
        ".agents/orchestration.json",
        "--state-dir",
        "{stateDirectory}"
      ],
      "output": "delivery-history.html",
      "timeoutSeconds": 60
    }
  ],
  "liveHome": "line-map",
  "trackerSnapshot": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
      "--kind",
      "linear",
      "--parent",
      "{parent}",
      "--token-env",
      "LINEAR_API_KEY",
      "--timeout-seconds",
      "25"
    ],
    "timeoutSeconds": 30
  },
  "forgeSnapshot": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
      "--kind",
      "github",
      "--owner",
      "example-org",
      "--repo",
      "example-repo",
      "--binding",
      ".agents/orchestration.json",
      "--state-dir",
      "{stateDirectory}",
      "--subject-pattern",
      "^ENG-[0-9]+$",
      "--token-env",
      "GITHUB_TOKEN",
      "--timeout-seconds",
      "25"
    ],
    "timeoutSeconds": 30
  }
}
```

Export `LINEAR_API_KEY` (a personal API key) and `GITHUB_TOKEN` (a token that
can read the repository's pull requests).

## Round reports: `review-round-report/1`

A round's findings record, one canonical JSON document per closed round:

| Field | Contents |
| --- | --- |
| `spec` | `review-round-report/1`. |
| `runId`, `roundId` | The run and the round, text of at most 128 characters. |
| `ticket` | The work item, free text of at most 64 characters, or null. |
| `round` | The round number, from 1. |
| `candidateTreeSha` | The reviewed tree, lowercase hex. |
| `closedAt` | When the round closed, a UTC timestamp. |
| `raised` | At most 50 findings in file order, each exactly `{id, lens, severity, summary, state, deferredIssue}`: `id` is `r<round>-f<k>`, `severity` P0 to P3, `summary` 1 to 280 code points with no control character or line break, `state` `open`, `deferred` or `dropped`, and `deferredIssue` free text of at most 64 characters exactly when deferred, else null. |
| `resolved` | Earlier rounds' findings settled here, each exactly `{id, state, fixCommit, deferredIssue}`: `state` `fixed`, `dropped` or `deferred`; `fixCommit` 40 lowercase hex exactly when fixed, else null; `deferredIssue` as above. An id is resolved at most once. |
| `briefs` | Optional. `{"<lens id>": {brief, result}}`: the artifact ids of what the lens was told and what it returned, each null when not recorded. A report without it stays valid. |

`report_problem` in `scripts/views_common.py` checks a report and names the
first problem's key path.


## Native reviews without a run

The line map also reads the validated `reviewEvidence` observations supplied by
`delivery_state.py show`. Use orchestrate-work's `capture-review` command with
actual outcome files after hand-off; do not manufacture a `runId`. Shared round
cards show the real lens verdicts, findings, reviewed candidate for each round,
and originating closure text. These reports are for display only. Current
approval requires the final candidate to match the stop's retained head, not
its synced head;
missing, tampered, oversized or stale evidence says unavailable. Live candidate
changes invalidate the displayed approval immediately. Brief / Result controls
remain hidden. Run-backed cards continue to read the configured run store.
