# Configuration

Most of what run-delivery needs, the repository already declares for agent-skills. `run_delivery.py` reads it from there,
in this order of precedence:

1. **The orchestrate-work binding** (`.agents/orchestration.json`), because it is what the merge queue actually runs:
   - remote and delivery base: `base.remote`, `base.ref`
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
   - a cross-check: its `baseRef` and `preparationCommands` must agree with the binding's base and fallback checks,
     or `run_delivery.py` refuses and shows both
4. **`.agents/run-delivery.json`** (`delivery-flow/1`), for what none of them declares, written once per repository.
   A `run-delivery.json` value that a higher source also declares, with a different value, is refused as a conflict: one
   side never wins silently.
5. Defaults.

`run_delivery.py config` prints every resolved setting and the file it came from.

**Where a lane reads them.** In the lead's checkout (on the delivery base), these files are read from the working
tree. In a lane's checkout they are read from the delivery base on the remote (`<remote>/<base>:<path>`): `run-delivery.json`,
the binding, `delivery-views.json`, the delivery file, the plan and the brief notes. So the lead's pushed changes
reach every lane at once, and a lane never merges the base, which would move its reviewed head, just to pick up a
tooling change. `RUN_DELIVERY_LOCAL=1` reads the lane's working tree instead (for working on the tooling itself);
`RUN_DELIVERY_CONFIG` names an explicit configuration file.

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
    "transitions": {                          // the lanes' status moves, stated in the lane brief; absent: none
      "source": "https://wiki/workflow",      // the team's workflow page
      "start": ["Refined", "Start work"],     // when a lane starts a story, in order
      "review": ["Submit for review"]         // once its draft PR is open and the gates pass
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
    "archive": "~/.local/share/run-delivery/archive/{repo}/{delivery}"  // where lead-close archives the record
  },
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
  // "baseBranch" and "stateDirectory" are optional overrides: the binding names the base, and the record lives in
  // the lead's checkout (laneCheckout with the delivery key) under the binding's state.directory.
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
  "authority": {                              // the operator's grant to lanes; absent: lanes don't push
    "push": true,                             // each lane's own story branch, never forced (run_delivery.py push)
    "draftPullRequests": true,                // its draft PR into the delivery base (run_delivery.py open-pr)
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

## The forge contract

The command is called with one of:

- `find --source BRANCH` prints `{"url"}` for the branch's open PR, or `{}`.
- `open --source B --destination D --title T --body-file F` opens a draft and prints `{"url"}`.
- `reviews --url URL --head SHA` prints `{"checks": [{name, state: passed|failed|pending, at}], "reviews": [{author, at, body}]}`:
  the head commit's pipeline checks and the PR's top-level comments. Needed only with `review.pr`.
- `get --url URL` prints `{"url", "state": "open"|"merged"|"closed", "head", "source", "destination"}`. It refuses
  a URL that isn't one of the repository's PRs. `head` may be abbreviated, to 7 characters at least.

`FLOW_FORGE_REPOSITORY` and `FLOW_FORGE_TOKEN_ENV` carry the `forge` settings.

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
