---
title: Show a plan on the live and hosted views while it is being planned - Plan
type: feat
date: 2026-11-23
topic: plan-preview-views
reviewSlug: plan-preview-views
execution: code
---

# Show a plan on the live and hosted views while it is being planned - Plan

## Goal

Make a plan visible from its first commit, by default and without the engineer
asking. While `plan-work` writes a plan and `plan-review` reviews it, the
repository's live server and, where it configures one, the hosted viewer show the
plan as the line map's plan chapter: its stories by lane, their order, criteria,
the finish line, the decisions and approvals it waits on, what is out of scope,
and every plan-review round with its findings. The preview is rebuilt on every
plan commit the workflow reaches and every reduced review round. It never reads
as approved: it says the review is agent-only until a person approves.

An adopter's prototype proved the shape by hand. It seeded a throwaway record
with `delivery_state.py init --seed`, wrote `plan.json` with
`review_projection.fit_plan_review`, served it with `render_state.py live
--state-config` (line-map renderer only), and published it with `hosted.py start
--record-root`. It held for a 12-story plan across four review rounds. It hit four
gaps, each settled below: no command for a record outside a delivery, the hosted
sidecars landing outside the record's own folder, an approval line reading
"Approved" for an agent-only review, and rebuilds that had to replace files in
place so the watchers survived.

## Current state (evidence)

Checked against `main` at `7dd81d29` on 2026-11-23.

- **The producer.** `build_plan` in `skills/run-delivery/scripts/run_delivery.py`
  builds `delivery-plan/1` from the delivery file and the changes log. Units come
  from `plan_unit` (criteria from the delivery file, which `lead-setup` drafts from
  the tracker's Acceptance Criteria), finish lines from `plan_finish`, `approvals`
  and `outOfScope` from the delivery file. `approved` is always set:
  `{"at", "review"?, "snapshot"}`. `check_plan` validates through the release's
  own `views_common.validate_plan` and the 1 MiB `PLAN_LIMIT_BYTES`.
  `rebuild_plan` writes `plan.json` by `os.replace` of a temporary file in the same
  folder, and journals `plan.updated`, which is what makes the live view re-render.
- **The plan's review.** `plan-review prepare/reduce/end --plan <path>` keep their
  rounds in `<main>/<work>/plan-review/<reviewSlug>/`, keyed by the plan's front
  matter (`review_slug`, `plan_review_config`). `plan_review_of(c)` already turns
  the store's rounds into `planReview` through `review_projection.round_detail` and
  `fit_plan_review`, for a plan store as for a delivery's. `lead-setup` and
  `lead-init` adopt the store (`adopt_plan_review`), moving it and leaving
  `adopted.json`; `plan_review_config` then refuses `--plan`.
- **The plan format.** `lead-setup` already parses a plan-work plan:
  `STORY_HEADING` (`### KEY. Title` under `## Stories`), `story_plan` (the lane
  sentence: lane, tier, `after` keys), `units_rows` and `units_owns` (the
  `## Units and order` table), `plan_list_items` (a section's top-level list, each
  item one line of at most 600 characters, at most 100 items) and
  `plan_sections`. Story keys match `PLAN_KEY`, so a plan under review can use
  placeholder keys before its tracker items exist. Nothing reads a story's
  scenarios: criteria come only from the tracker.
- **The plan contract.** `skills/delivery-views/references/delivery-plan.schema.json`
  is closed at the top level (`additionalProperties: false`) and requires
  `approved`. `units`, `finish` and `approvals` have no minimum count. It has no
  member for decisions.
- **The approval line.** `approvalLine` in `skills/delivery-views/assets/line-map.js`
  prints "Approved <day> after N review rounds · <ending>" whenever `planReview`
  has rounds, reading `approved.at` and ignoring `approved.review`. The plan
  switcher prints "Approved <when> · <approved.review>".
- **The hosted cut.** `hosted.py collect` reads the record through
  `render.read_plan_block`, so `plan.json` travels whole, fitted by `plan_block`.
  `hosted-snapshot.schema.json` types `plan` as a generic record, so the hosted
  service's validator accepts any plan member. The hosted page's line map is built
  from `skills/delivery-views/assets/` when the service is deployed, so a deployed
  origin runs the line map of its own build. The publisher declares
  `delivery-line-map/3`.
- **The hosted publisher's files.** `hosted.py` writes its sidecars
  (`hosted-identity.json`, `hosted-status.json`, `hosted-owner.json`,
  `hosted-owner.lock`, `hosted-control.lock`, `hosted-pending-*`, `hosted-retry.json`,
  `hosted-terminal*`) into its `--state-dir`, temporary files beside each.
  `selected_files` captures only the record's canonical files. `watch` stops when
  its state directory's device and inode change, and `public.capture`
  fingerprints file identity, so a rebuild must replace files inside a folder that
  stays put. `load_inputs` refuses a state directory outside the checkout unless
  `--archive-root` or `--record-root` covers it. run-delivery's `hosted_files`
  writes `hosted-view-config.json` and `hosted-view-binding.json` into `c.state`,
  and `hosted_call` passes `--record-root c.state` and `--key c.delivery`. For a
  record that is not the delivery's, these land in the delivery's folder, not the
  record's: the prototype's sidecar gap.
- **The live server.** `render_state.py live` serves one state directory, renders on
  every record write, and registers a verified entry (pid, port,
  `stateDirectory`, `liveRuns`) under the git common directory.
  `live --if-absent` returns any verified entry of the repository, preferring one
  with `liveRuns`, so a second server serving another folder could be returned as
  the standing one. With no record the runs page (`state.liveRuns`, V26-3670) is
  the front page. run-delivery starts a delivery's view detached, recording
  `live.json` in the record's folder, on a port derived from the main checkout and
  the key (`derived_live_port`), and stops it only by its recorded pid.
- **The binding.** `.agents/orchestration.json` `state` names renderers
  (`markdown`, `page`, `line-map`, `delivery-history`, `runs`), a tracker
  `publishers` entry, tracker and forge snapshots, `liveHome: line-map` and
  `liveRuns`. Serving a preview under the repository's own binding would run its
  tracker publisher and snapshots against a throwaway record.
- **The runs page.** V26-3670 shows that a `plan-work` run is happening (its stage,
  waits, gates, review rounds), not what the plan says, and links nowhere else.
- **Tests that read these surfaces.** `tests/test_run_delivery_plan_review.py`,
  `tests/test_run_delivery_hosted.py`, `tests/test_delivery_views_plan.py`,
  `tests/test_delivery_views_plan_fixtures.py`, `tests/test_delivery_views_hosted.py`,
  `tests/test_delivery_views_hosted_projection.py`,
  `tests/test_orchestration_render.py`, `tests/test_delivery_views_render.py`, and
  the page lane's `tests/delivery_views_page/line-map-plan*.test.mjs` through
  `tests/test_delivery_views_page.py`.
- **Knowledge library:** not searched. No knowledge tool is loaded in this session.

## Scope

In scope:

1. **The preview member.** An optional `preview` member of `delivery-plan/1`,
   written only by the preview builder, that the line map reads to say the review
   is agent-only and to show the plan's decisions.
2. **The preview builder.** A run-delivery command, `plan-view --plan <path>`,
   that builds `delivery-plan/1` from a committed plan-work plan and its
   plan-review store, with no delivery file, binding record, tracker or forge, and
   writes it into a preview record kept per `reviewSlug`.
3. **Rebuilding in place.** Every rebuild keeps the preview folder and replaces only
   its files, so the live server and the hosted watcher keep following it.
4. **Serving and publishing.** The preview is served by its own live server with
   the binding's line-map renderer only, and published by the hosted publisher
   where the repository configures one, every sidecar inside the preview's folder.
   The standing server's runs page lists the previews.
5. **The posture.** `plan-work`, `plan-review prepare`, `reduce` and `end` build
   the preview and hand the operator both links, unasked. Silent where the
   repository has no live view or hosted configuration.

Out of scope:

- Comments, reactions or any write from the page. The views stay read-only.
- Any change to the delivery record, the producer, `lead-setup` or `lead-init`
  once a delivery starts. Adopting the review store stays as it is.
- A git hook or file watcher. A plan commit the workflow does not reach (a commit
  made by hand outside `plan-work` and `plan-review`) shows on the next rebuild.
- A new renderer version, protocol change or hosted service change.
- Windows validation.

## Decisions recorded here

- **One optional member, `preview`.** `delivery-plan/1` gains an optional top-level
  `preview` object, closed, with:
  - `plan`: the plan's repository-relative path (at most 600 characters);
  - `commit`: the 40-hex commit the plan was read at;
  - `approval`: `agents-only` until a delivery adopts the review, then `handed`;
  - `delivery`: the adopting delivery's key, present exactly when `approval` is
    `handed`;
  - `layout`: `delivery` when the plan holds stories in the delivery layout,
    otherwise `other`;
  - `decisions`: at most 100 strings of at most 600 characters.

  It stays `delivery-plan/1`: the member is additive and optional, the producer
  never writes it, and every record a delivery writes validates exactly as
  before. `views_common.validate_plan` and the schema move together, and the
  schema test pins both. No new renderer version.
- **`approved` stays required and stays truthful in shape.** A preview sets
  `approved.at` to the plan commit's committer time, `approved.snapshot` to the
  preview's own units and finish lines, `changes` to `[]` and `evidence` to `{}`,
  and leaves out `approved.review`. The member that says what `approved` means is
  `preview.approval`.
- **The approval line for a preview.** With `preview` present, the line map never
  prints "Approved". It prints:
  - with no review round: "Agent review only · not reviewed yet · not approved by
    a person";
  - with rounds: "Agent review only · N review rounds · <ending> · not approved by
    a person", the ending words as today;
  - once handed: "Handed to delivery <key> · N review rounds · <ending>".

  The plan switcher ("As amended" / "As approved") is not drawn for a preview, and
  the plan chapter shows a "Decisions" fold from `preview.decisions` and, for
  `layout: other`, one line saying the plan is not in the delivery layout and so
  shows no stories.
- **An origin built before this change.** Its line map ignores `preview` and would
  print "Approved". So a preview's `outcome.summary` always begins "Plan preview
  (agent review only):", which every line map shows as the page's headline, and
  `docs/hosted-deliveries.md` says to redeploy the viewer from the release that
  carries previews. No capability negotiation is added.
- **What the builder reads, and from where.** It reads the plan as committed at
  `HEAD` of the checkout it runs in (`git show HEAD:<path>`), never the working
  tree, and refuses an uncommitted or untracked plan as `plan-review prepare`
  does. From the plan:
  - the title: front matter `title`, else the first `# ` heading;
  - `outcome`: `summary` is the preview prefix plus the title, `text` the first
    paragraph of `## Goal` (clipped to 2000 characters), else the title;
  - lanes, tiers and order: `## Units and order` through `units_rows`, and each
    story's lane sentence through `story_plan`;
  - `units`: one per `### KEY. Title` under `## Stories` (`STORY_HEADING`), in plan
    order, `proofs` the three record kinds, `title` from the heading;
  - criteria: the items of the story's `Scenarios:` list, then of its `Done when`
    list, each a nested list item joined to one line and clipped as
    `plan_list_items` clips, ids `<key-lowercase>-<n>`; a story with neither has no
    criteria and is shown as such;
  - `finish`: the top-level items of `## Finish line`, ids `finish-<n>`, label
    `Finish <n>`, `covers: {}` (a line only a person checks);
  - `approvals`: the top-level items of `## Approval handoffs`, each `waiting`;
  - `outOfScope`: the items of a heading named `Out of scope`, else of the list
    after an `Out of scope:` line inside `## Scope`;
  - `preview.decisions`: the top-level items of the first `##` heading starting
    `Decisions`;
  - `planReview`: `plan_review_of` over the plan's store, unchanged.

  A missing section yields an empty member, never a refusal. The parsing helpers
  are the ones `lead-setup` uses, shared, not copied.
- **A plan outside the delivery layout.** A plan with no `### KEY. Title` heading
  under `## Stories` is still previewed: `layout: other`, no units, and the
  record's `planned` list empty. Its outcome, finish line, decisions, approvals,
  out-of-scope and review rounds show as for any plan.
- **The preview record.** It lives at `<main>/<work>/plan-preview/<reviewSlug>/`,
  beside the review store and never inside it, so adopting the store moves nothing
  the watchers follow. The folder holds:
  - the record: `state.json` and `journal.jsonl`, made with `delivery_state.py init
    --seed` (headline the outcome summary, `planned` one row per unit with its lane
    and `after` keys) and changed only through `delivery_state.py patch` pinned to
    the version it read;
  - `plan.json`, written by `os.replace` of a temporary file in the same folder,
    then journaled as `plan.updated`;
  - `preview.json`: the slug, plan path, commit, and the live and hosted links
    last handed out;
  - every sidecar: `live.json`, `hosted-view-config.json`,
    `hosted-view-binding.json` and the hosted publisher's own files.

  The folder is created once and never removed or replaced by a rebuild. A rebuild
  that changes nothing apart from `updated` writes nothing, as `rebuild_plan`
  does. Rebuilds hold a lock file in the folder, so two commands rebuilding one
  preview run one after the other.
- **The hosted publisher's sidecars stay in the preview's folder.** `hosted_files`
  and `hosted_call` take the record folder and key as arguments instead of reading
  `c.state` and `c.delivery`; the delivery path passes exactly what it passes
  today. For a preview: `--state-dir` and `--record-root` are the preview folder,
  `--key plan-<reviewSlug>`, and no `--archive-root`. Nothing is written to the
  delivery's record folder, the preview folder's parent or the review store.
- **The live server for a preview.** `plan-view` starts `render_state.py live` on the
  preview folder, detached, with a state configuration derived from the binding:
  `directory`, the one renderer `state.liveHome` names, `liveHome`, and nothing
  else (no publishers, snapshots, `liveRuns` or other renderers). Its port is
  derived as a delivery's is, from the main checkout and `plan-<reviewSlug>`, so a
  preview keeps its link across restarts; a held port falls back to a free one,
  recorded in `live.json`. A running preview server is reused while it serves from
  the same generation's `render_state.py`; otherwise it is stopped by its recorded
  pid and started again.
- **Preview servers are not the standing server.** `render_state.py live` gains a
  `--preview` flag, recorded in the server's entry and `/health`. `live
  --if-absent` never returns a preview entry, so this repository's standing-server
  command keeps its meaning. The standing server's runs page lists the
  repository's verified preview entries in a "Plans in review" section: each
  preview's title, plan path, its local link and, from its `preview.json`, its
  hosted link. With no preview the section is absent and every existing golden is
  unchanged.
- **When it is silent.** The live preview is skipped, saying nothing, when the
  binding has no `state.liveHome` naming an argv renderer, or when there is no
  binding. The hosted preview is skipped, saying nothing, when
  `.agents/delivery-views.json` has no `hosted`. With `hosted` configured but no
  token in its `tokenEnv`, the command prints one line saying the preview was not
  published and why. A preview that cannot be built (an invalid plan contract)
  prints the reason and exits non-zero from `plan-view`, but never fails the
  `plan-review` command that triggered it.
- **When it is rebuilt.** At the end of `plan-review prepare --plan`, `reduce
  --plan` and `end --plan`, and whenever `plan-view --plan` runs. `plan-work`'s
  skill says to run `plan-view` after each plan commit where run-delivery is
  installed, and to hand the operator both links it prints, unasked. Each command
  prints the links as `plan preview: <local>` and `plan preview (hosted):
  <link>`, once per command.
- **After adoption.** `plan-view --plan` on a store a delivery adopted rebuilds once
  with `approval: handed` and the delivery key, stops the preview's live server
  by its recorded pid, and leaves the hosted publisher to publish that last cut
  and stop. Later calls print where the delivery's own views are and change
  nothing. `plan-view --plan <path> --stop` stops both at any time. Nothing in
  `lead-setup` or `lead-init` changes.
- **Release shape.** `delivery-plan/1` gains an optional member, so this ships first
  as a candidate under AGENTS.md, is applied in this repository, and is then
  promoted. It is a minor version.
- **Suite budget.** New Python cases reuse the fixtures their modules already build
  (the plan-review store fixture, the hosted fake origin, the live server
  fixture). No case starts a second live server where one suffices, and
  `tests/suite-budget.json` is not raised.

## Finish line

All of the following hold at one main-push commit:

1. Every plan-review lens aligned on this plan.
2. In a fixture repository with a binding that names `liveHome` and a hosted
   configuration pointed at the test origin, `plan-view --plan` on a committed
   delivery-layout plan with no review round prints both links. The local page
   shows the stories by lane with their criteria, the finish line, decisions,
   approvals and out-of-scope, and the line "Agent review only · not reviewed yet
   · not approved by a person". The test origin holds a cut whose plan carries
   the same `preview` member.
3. After `plan-review prepare` and `reduce` on that plan, the same local page and
   hosted cut show the round, with no restart of either process: the live
   server's pid and the hosted watcher's pid are unchanged, and the preview
   folder's inode is unchanged.
4. The word "Approved" appears nowhere in a preview's plan chapter, and every
   delivery-written plan fixture renders byte for byte as before.
5. In a fixture repository with neither a `liveHome` nor `hosted`, `plan-work`'s
   commands print no preview line and start no process.
6. Docs, the skill sentences, catalog, profiles and provenance have landed. The full
   local suite passes on macOS with Python 3.11 inside its budget.

Applying the candidate in this repository, and watching one real plan's preview
through its review on the live and hosted views, is operator acceptance. This
delivery does not claim it.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | PPV-1 | `skills/delivery-views/references/delivery-plan.schema.json`, `skills/delivery-views/scripts/views_common.py`, `skills/delivery-views/assets/line-map.js`, `skills/delivery-views/assets/line-map.css`, `tests/test_delivery_views_plan.py`, `tests/test_delivery_views_plan_fixtures.py`, `tests/delivery_views_page/` |
| 1 | 3 | PPV-3 | `skills/orchestrate-work/scripts/render_state.py`, `tests/test_orchestration_render.py` |
| 2 | 2 | PPV-2 | `skills/run-delivery/scripts/run_delivery.py`, `tests/test_run_delivery_plan_preview.py` |
| 3 | 2 | PPV-4 | `tests/test_run_delivery_plan_review.py`, `tests/test_run_delivery_hosted.py` |
| 4 | 1 | PPV-5 | `skills/plan-work/SKILL.md`, `skills/run-delivery/SKILL.md`, `skills/delivery-views/SKILL.md`, `skills/orchestrate-work/SKILL.md`, `docs/hosted-deliveries.md`, `docs/own-deliveries.md` |

Every story that changes a releasable skill also refreshes `catalog.json`, the
selected `profiles/` and `provenance.lock.json` in its own change, through
`scripts/update-provenance.py`. These three files are shared by every lane: see
Shared resources.

The stories are children of the epic V26-3775.

## Stories

### PPV-1. Say "agent review only" and show decisions on a plan preview

Lane 1, tier 1, not blocked. Test-first. Files:
`skills/delivery-views/references/delivery-plan.schema.json`,
`skills/delivery-views/scripts/views_common.py`,
`skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `tests/test_delivery_views_plan.py`,
`tests/test_delivery_views_plan_fixtures.py` and `tests/delivery_views_page/`.

- Add the optional, closed `preview` member to the schema and to
  `validate_plan`, with the bounds the decisions state.
- In the line map: the preview approval line, no plan switcher, the "Decisions"
  fold, and the `layout: other` line. Nothing changes for a plan without
  `preview`.
- Add preview fixtures to the plan fixture set: no round, three rounds open,
  aligned, ended, handed, and `layout: other`.
- Before editing, search the hosted service's build hooks
  (`hosted/delivery-viewer/scripts/build-assets.mjs`) for exact text it patches in
  `line-map.js`, and keep every hooked line byte for byte.
- Scenarios:
  - a plan with `preview` and no rounds validates and renders "Agent review only ·
    not reviewed yet · not approved by a person";
  - with three open rounds it renders "Agent review only · 3 review rounds · still
    open · not approved by a person";
  - aligned and ended render their endings the same way;
  - handed renders "Handed to delivery <key>" and never "not approved by a person";
  - no preview fixture's plan chapter contains "Approved" or the switcher;
  - the "Decisions" fold lists every decision, and `layout: other` shows its line
    and no units;
  - `preview` with an unknown field, `delivery` without `handed`, `handed` without
    `delivery`, a short `commit`, or 101 decisions is refused by `validate_plan`;
  - every existing delivery plan fixture validates and renders unchanged.

### PPV-3. Mark preview servers and list them on the runs page

Lane 3, tier 1, not blocked. Test-first. Files:
`skills/orchestrate-work/scripts/render_state.py` and
`tests/test_orchestration_render.py`.

- `live --preview` records `"preview": true` in the server's entry and `/health`.
- `live --if-absent` skips preview entries, whichever order they were written in.
- The runs page, served by the standing server, gains the "Plans in review"
  section from verified preview entries and each one's `preview.json`, read
  defensively: an unreadable or oversized `preview.json` drops that preview's
  hosted link, never the page.
- Scenarios:
  - with only a preview server running, `live --if-absent` starts a standing server
    instead of returning the preview's address;
  - with both running, it returns the standing server's;
  - the runs page lists one row per verified preview, with its local link and,
    when `preview.json` names one, its hosted link;
  - a preview entry whose pid no longer serves is not listed;
  - with no preview, the runs page and every golden are byte for byte unchanged;
  - `live --stop` on a preview folder stops only that folder's server.

### PPV-2. Build a plan preview from a plan file and its review store

Lane 2, tier 2, after PPV-1. Test-first. Files:
`skills/run-delivery/scripts/run_delivery.py` and
`tests/test_run_delivery_plan_preview.py`.

- Add `plan-view --plan <path>` (build and rebuild only in this story; serving and
  publishing are PPV-4's). It needs no delivery file, tracker, forge or record.
- Share `lead-setup`'s plan parsing; add the scenarios and finish-line readers.
- Make the preview record as the decisions state: the folder, `init --seed`, then
  `patch` pinned to its version, `plan.json` by `os.replace`, `plan.updated`
  journaled, `preview.json`, and the rebuild lock.
- Validate through `check_plan`'s validator and size limit before writing.
- Scenarios:
  - a delivery-layout plan gives one unit per story with its lane, `after` keys,
    and the scenarios and done-when items as criteria;
  - finish lines, approvals, out-of-scope (both forms) and decisions are read;
  - a plan with no `## Stories` gives `layout: other`, no units and an empty
    `planned`, and still validates;
  - a plan changed only in the working tree is refused; the preview reflects
    `HEAD` and names its commit;
  - with a store holding two rounds, `planReview` equals what `plan_review_of`
    gives for that store;
  - a second build with nothing changed writes nothing and journals nothing; a
    build after a new commit replaces `plan.json` and `state.json` and journals one
    `plan.updated`, with the folder's inode unchanged;
  - a story removed from the plan leaves `planned` and `units` without it;
  - two builds started together leave one consistent record (the lock);
  - a plan with no `reviewSlug` is refused with the same front-matter advice
    `plan-review` gives;
  - nothing is written outside the preview folder.

### PPV-4. Serve and publish the preview, unasked, from plan-review

Lane 2, tier 3, after PPV-2 and PPV-3. Test-first. Files:
`skills/run-delivery/scripts/run_delivery.py`,
`tests/test_run_delivery_plan_preview.py`,
`tests/test_run_delivery_plan_review.py` and `tests/test_run_delivery_hosted.py`.

Lane 2 already owns `run_delivery.py` and `tests/test_run_delivery_plan_preview.py`
from its tier-2 row.

- `plan-view` starts or reuses the preview's live server (`--preview`, derived
  state configuration, derived port, `live.json`) and the hosted publisher
  (`hosted_files` and `hosted_call` taking the folder and key), and prints both
  links.
- `plan-review prepare`, `reduce` and `end` with `--plan` rebuild the preview at
  their end and print its links; a preview failure is printed and never changes
  their exit code.
- `plan-view --stop`, and the adopted-store path, as the decisions state.
- Scenarios:
  - finish lines 2, 3 and 5, end to end in fixture repositories, with the hosted
    fake origin `tests/test_run_delivery_hosted.py` already uses;
  - the derived state configuration holds only `directory`, the `liveHome`
    renderer and `liveHome`, so the binding's tracker publisher never runs (a
    publisher that writes a marker file leaves none);
  - every hosted sidecar is written inside the preview folder, and the delivery's
    record folder and the preview folder's parent are untouched;
  - a delivery's `hosted_call` arguments are unchanged (an existing assertion,
    re-derived only where the call's signature moves);
  - with `hosted` configured and no token, one line says the preview was not
    published, and the live link is still printed;
  - a broken plan contract makes `plan-view` exit non-zero and `plan-review reduce`
    still record its round and exit 0;
  - after adoption, `plan-view` rebuilds once with `handed`, stops the live server
    by its recorded pid, and a later call changes nothing;
  - `plan-view --stop` stops only processes the preview recorded.

### PPV-5. Make the preview the default posture in the workflows' docs

Lane 1, tier 4, after PPV-4. Sensor-only. Files: `skills/plan-work/SKILL.md`,
`skills/run-delivery/SKILL.md`, `skills/delivery-views/SKILL.md`,
`skills/orchestrate-work/SKILL.md`, `docs/hosted-deliveries.md` and
`docs/own-deliveries.md`.

- `plan-work`: where run-delivery is installed, run `plan-view` after each plan
  commit and hand the operator both links, unasked; say nothing where it prints
  none.
- `run-delivery`: the `plan-view` row in the commands table, and the preview line
  on the `plan-review` rows.
- `delivery-views`: the `preview` member, the approval line, and that an origin
  must be redeployed from the release carrying previews.
- `orchestrate-work`: `live --preview`, and the runs page's "Plans in review".
- `docs/own-deliveries.md`: where a plan's preview links appear in this
  repository's own deliveries.
- Before editing, search the tests that read these files for pinned phrases, and
  re-derive each moved pin in the same change.

## Shared resources

- **Provenance, catalog and profiles.** Every story refreshes `catalog.json`,
  `profiles/` and `provenance.lock.json`. On a merge conflict confined to the lock,
  take either side and run `scripts/update-provenance.py`, as AGENTS.md says. The
  merge queue lands one story at a time, so each lands on a lock its predecessor
  already refreshed.
- **`run_delivery.py`.** Only lane 2 edits it; PPV-4 follows PPV-2 in the same lane.
- **Ports.** Live servers in tests bind port 0 or set `RUN_DELIVERY_LIVE_PORT=0`,
  never a derived port. The hosted fake origin takes a free loopback port.
- **Processes.** Every server a test starts is stopped by its recorded pid, never by
  pattern. A class that starts real live servers or the hosted watcher is marked
  `run_quietly`, not `run_serially`, unless it needs a fixed path outside its
  folder.
- **Git common directory.** Live entries are written under the fixture repository's
  own common directory, never the checkout's.
- **Full suite.** Run once per hand-off head through the shared slot script.

## Sensors

- **Python:** `tests.test_delivery_views_plan`, `tests.test_delivery_views_plan_fixtures`,
  `tests.test_delivery_views_hosted`, `tests.test_delivery_views_hosted_projection`,
  `tests.test_orchestration_render`, `tests.test_run_delivery_plan_preview`,
  `tests.test_run_delivery_plan_review`, `tests.test_run_delivery_hosted`,
  `tests.test_provenance`, `tests.test_catalog` and `scripts/validate-registry.py`.
- **Page lane:** `tests.test_delivery_views_page` (Node 24, jsdom).
- **Hosted service:** `hosted.build` and `hosted.browser`, which read
  `skills/delivery-views/assets/**`, to prove the line map's build hooks hold.
- **No-regression:** `tests.test_run_delivery`, `tests.test_lead_plan_changes`,
  `tests.test_delivery_views_render`, unchanged.
- **Before any tag:** the full local suite on macOS with Python 3.11, with
  `--budget tests/suite-budget.json`.

## Approval handoffs

1. The operator approves this plan, after plan review.
2. Tracker items are created under V26-3775 for PPV-1 to PPV-5
   (`create-linear-ticket`), and the plan's keys are renamed to them.
3. Release: the operator tags a candidate, it is applied in this repository, and the
   final is tagged on the same commit.
4. Live acceptance: the operator watches one real plan's preview through at least
   two review rounds on the live and hosted views.

## Risks

- **A plan written outside the format.** Criteria come from `Scenarios:` and
  `Done when` lists; a plan that states them otherwise shows stories without
  criteria. The preview says so per story rather than guessing.
- **Older origins.** A hosted origin not redeployed prints "Approved" beside the
  "Plan preview (agent review only)" headline until it is redeployed.
- **Hook drift.** `build-assets.mjs` patches `line-map.js` by exact text; PPV-1
  keeps the hooked lines, and `hosted.build` refuses drift.
- **Process count.** Each plan in review runs one live server and one hosted
  watcher. They stop after adoption or on `--stop`; a preview abandoned without
  either keeps running until the machine restarts.
