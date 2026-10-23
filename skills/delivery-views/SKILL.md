---
name: delivery-views
description: Give a repository that delivers epics through orchestrate-work a ready-made live line map, delivery history page and tracker and forge snapshots, configured rather than coded.
---

# Delivery Views

Ready-made views and snapshot commands for a repository that delivers an epic
through `$orchestrate-work`. The repository writes configuration; it writes no
view or snapshot code of its own. This skill holds everything provider- or
web-specific, so the core workflow stays neutral.

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
wrapper and the snapshot runner pass no working directory of their own. Each
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
happened, parked), with a detail panel for each piece of work. Its style and
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

A round whose report names a `briefs` member shows, beside each reviewer, its
brief and its result, each expanding in place as text: up to 64 KiB, cut at a
character boundary with a note when longer, with "no brief recorded" for one
the report names as null and "unavailable" for one that cannot be read. The
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

## Runs, cache and storage

Every `runs` command runs bounded: its own process group, `timeoutSeconds`, a
16 MiB output cap, stdin closed and the `scrubEnv` variables removed. A
timeout, oversize output, a non-zero exit or output that is not JSON makes
that run unavailable and never fails the render. All of a render's runs share
one deadline: the renderer's `--deadline-seconds`, 20 when absent, capped at
the renderer's declared `timeoutSeconds`, read through `--binding`, less 5
seconds.

The run cache is `<state directory>/.cache/delivery-views/runs.json`, spec
`delivery-views-runs/1`, written atomically; a corrupt cache is rebuilt and an
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
  "title": "Shift planner delivery",
  "tracker": {
    "name": "Jira",
    "issueUrl": "https://eshyft.atlassian.net/browse/{key}",
    "keyPattern": "^(SHFT|EDD)-[0-9]+$"
  },
  "forges": [
    {
      "name": "Bitbucket",
      "repository": "eshyft/eshyftnew",
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
        ".agents/orchestration.json"
      ],
      "output": "line-map.html",
      "timeoutSeconds": 30
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
      "https://eshyft.atlassian.net",
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
      "eshyft",
      "--repo",
      "eshyftnew",
      "--binding",
      ".agents/orchestration.json",
      "--subject-pattern",
      "(SHFT|EDD)-[0-9]+",
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
        ".agents/orchestration.json"
      ],
      "output": "line-map.html",
      "timeoutSeconds": 30
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
