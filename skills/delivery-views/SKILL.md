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
  { "id": "line-map", "argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/line_map.py", "--config", ".agents/delivery-views.json", "--binding", ".agents/orchestration.json"], "output": "line-map.html", "timeoutSeconds": 30 }
],
"liveHome": "line-map"
```

Start `serve`, `live` and renders from inside the repository. The render
wrapper passes no working directory of its own, so every script here resolves
the repository top level from its working directory (`git rev-parse
--show-toplevel`) and resolves `--config`, `--binding` and `{root}` against it.
A `--config` or `--binding` that is not found exits non-zero naming the path.

Check a configuration before declaring it:

```bash
python3 -B .agent-skills/current/skills/delivery-views/scripts/views_common.py check --config .agents/delivery-views.json --binding .agents/orchestration.json
```

## Configuration: `delivery-views-config/1`

`.agents/delivery-views.json`; every key but `spec` is optional.

| Key | Contents |
| --- | --- |
| `spec` | `delivery-views-config/1`. |
| `title` | The pages' title, at most 80 characters; `Delivery` when absent. |
| `tracker` | `{name, issueUrl, keyPattern?}`. `issueUrl` takes `{key}`; an item is linked only when `keyPattern`, at most 200 characters, matches it. |
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

Placeholder values are percent-encoded as path segments; `repository` keeps
its single `/`, and a segment of dots alone is encoded. A produced URL is used
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

## Round reports: `review-round-report/1`

A round's findings record, one canonical JSON document per closed round:

| Field | Contents |
| --- | --- |
| `spec` | `review-round-report/1`. |
| `runId`, `roundId` | The run and the round, text of at most 128 characters. |
| `ticket` | The work item, free text of at most 64 characters. |
| `round` | The round number, from 1. |
| `candidateTreeSha` | The reviewed tree, lowercase hex. |
| `closedAt` | When the round closed, a UTC timestamp. |
| `raised` | At most 50 findings in file order, each exactly `{id, lens, severity, summary, state, deferredIssue}`: `id` is `r<round>-f<k>`, `severity` P0 to P3, `summary` 1 to 280 code points with no control character or line break, `state` `open`, `deferred` or `dropped`, and `deferredIssue` free text of at most 64 characters exactly when deferred, else null. |
| `resolved` | Earlier rounds' findings settled here, each exactly `{id, state, fixCommit, deferredIssue}`: `state` `fixed`, `dropped` or `deferred`; `fixCommit` 40 lowercase hex exactly when fixed, else null; `deferredIssue` as above. An id is resolved at most once. |
| `briefs` | Optional. `{"<lens id>": {brief, result}}`: the artifact ids of what the lens was told and what it returned, each null when not recorded. A report without it stays valid. |

`report_problem` in `scripts/views_common.py` checks a report and names the
first problem's key path.
