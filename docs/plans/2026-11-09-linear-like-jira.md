---
reviewSlug: linear-like-jira
---

# Make Linear work the way Jira does

Epic V26-3332: the open Linear items, gathered under one epic by the operator's ruling on V26-3269 (2026-11-09), "linear should work the
same way jira works", and reshaped by the operator's second ruling the same day, "do we still need the host work since we're moving to
the api? doesn't make sense to support both modes". So every tracker run-delivery ships posts through the tracker publisher, and the host
publisher mode goes. The items are V26-3269 (the Linear adapter), V26-3334 (removing host mode, which supersedes the canceled V26-3261),
V26-3333 (this repository's switch, split from V26-3269's Scope 4), V26-3097 (the Linear qualifier), V26-3083 (the scheduled connector
check) V26-3339 (the lead's check choice at acceptance, added by the operator's third ruling) V26-3342 (repairing `main` after V26-3269) and V26-3343 (the connector check's marker searches). Each item's tracker scope and acceptance criteria bind what it does. This plan decides only what the items leave open: how the
rulings read in the corpus, the choices two items share, order, file ownership while another delivery holds some of the same files, and
how the work reaches `main`.

## Finish line

- Each item is merged to `main` through the merge queue, or closed with a recorded reason.
- `main`'s CI on the last merged commit passes on the Ubuntu job and `cut-release`, with the run linked.
- The full local suite passes on macOS with Python 3.11 at the commit the release candidate is tagged on.
- A major release candidate carrying the Linear adapter and the host mode's removal is tagged, published and applied here, and this
  repository's own deliveries post their Linear updates and moves through the tracker publisher, with no outbox for the lead to drain.
- No finding is deferred: every finding any review in this delivery raises, at any severity, is fixed in the story that found it, and no
  follow-up item is filed for it.

## Out of scope

- V26-2947, a release-set tracking item from before tag-driven releases, and V26-1592, which is Athena's repository.
- The rest of V26-3306 (V26-3260, V26-3262, V26-3270) and of V26-3266.
- Any change to how agents themselves reach Linear: `linear-tracker-adapter`, `create-linear-ticket` and `execute-linear-ticket` stay
  MCP-only.
- A Linear write path anywhere but run-delivery's tracker publisher and the commands that already post through a tracker adapter.
- `tracker.updates: manual`, which stays: a tracker with no adapter, by the adopter's choice, has its moves made by people.
- A final (non-candidate) release. The candidate is promoted at a later batch boundary, as `docs/product-generation.md` says.

## Decisions

1. **The first ruling splits an agent-facing rule from a machine-facing path, exactly as Jira does.** `jira-tracker-adapter` allows only
   the host's Atlassian MCP connector, while run-delivery's `tracker_jira.py` writes to Jira's REST API with a token from the
   environment. Linear takes the same split. `linear-tracker-adapter`'s "use only the host's native Linear MCP capability" and its
   contract's `transportBoundary` stay word for word, held by presence pins. What is withdrawn is the claim that Linear is reachable only
   through the host: the `tracker_publisher.py` comment, the `host` bullet in `skills/run-delivery/references/config.md` and the
   troubleshooting entry in `references/adopting.md`, all by V26-3269, and `docs/own-deliveries.md`'s "Tracker updates", by V26-3333,
   since this repository's binding stays host-mode until the switch. Per AGENTS.md, V26-3269 searches by meaning across every skill in
   the `run-delivery` and `linear` profiles for that claim and its synonyms ("only through the host's connector", "host-only tracker",
   "by this corpus's rule", "Linear needs a host publisher"), rewords each hit, and pins their absence. The generic host-publisher wording
   goes with the mode, in V26-3334 (Decision 5). No rewording names Linear's endpoint beside an instruction in a Linear skill, which
   V26-3097's sensor would refuse; the lead selects `tests/test_linear_qualification.py` for every lane 1 candidate that touches a file
   the `linear` profile adds.
2. **`tracker_linear.py` keeps `tracker-adapter/1` exactly (V26-3269).** It sits beside `tracker_jira.py` and has its shape:
   `--token-env` (default `LINEAR_API_KEY`) before the operation, then `status`, `markers`, `comment`, `transition` and `create`, each
   printing one JSON object, a failure exiting 3 with `{"error", "httpStatus"}`. It talks to Linear's GraphQL endpoint with
   `urllib.request` only, as `snapshot.py` does, and sends the personal key as-is in `Authorization`, as `snapshot.py` does, never as a
   bearer token. The token is read only from the named variable, a missing one fails before any request, and it is never printed,
   logged or written to the ledger.
   - **Errors.** Linear reports failures as GraphQL `errors`, at HTTP 200 or otherwise. The adapter maps them whatever the HTTP status:
     a rate-limit code (`RATELIMITED`) to `httpStatus` 429, an authentication code to 401, a forbidden code to 403, and any other error
     to 400. `classify()` is unchanged, so a rate limit or an authentication error is transient, as for Jira; a forbidden one is decided
     by `site_refuses()`, permanent for that ticket and transient when the whole site refuses too, as for Jira; and the rest are
     permanent `failed`. An HTTP 429 or 5xx with no `errors` is passed through, and an unreachable endpoint is `null`, as for Jira. An
     answer with `errors` is never success, so no ledger line is written for it.
   - **Markers.** Linear has no per-issue properties, so a marker is carried in the comment body as a trailing HTML comment,
     `<!-- run-delivery:<marker> -->`. `markers --ticket` follows the comments connection's `pageInfo` to the end, bounded by a
     `MAX_PAGES` like `tracker_jira.py`'s. Before it hands off, V26-3269 posts one marked comment through the adapter to an item in this
     delivery and reads it back with `markers --ticket` against real Linear, recorded on the item. If Linear drops or rewrites the HTML
     comment, the carrier is a visible last line, `run-delivery: <marker>`, instead, and the plan's wording follows the result.
   - **Moves.** A move is the state update, then its marker comment, in that order, so a crash or failure between them leaves the ticket
     in the target status with no marker, which the publisher's existing already-in-target rule (Decision 3) reads as made, never as
     ours. A status is a workflow state name on the item's team. `status` lists each state as a transition named for it, `to` the same
     name, and `offered` only for a state whose workflow type is the current one's or later, in the order triage, backlog, unstarted,
     started, completed, canceled, except that a completed or canceled ticket offers none. So a story can move from In Progress to
     another started state such as In Review, and to Done, but a replayed start move never sends a Done ticket back, as Jira's
     workflow refuses it.
   - **Create.** `--project` is the team key, which `project_of` already derives from the delivery key; `--type` is accepted and
     ignored, since Linear has no issue types; a created item's marker is a label, as for Jira.
   - **Limit.** `commentLimit` is the adapter's constant, taken from Linear's documented comment limit and counted the way
     `tracker_publisher.py` counts. The comment-limit row sets the stub's limit independently, so the row cannot agree with itself.
3. **The already-in-target rule is the publisher's existing one, kept (V26-3269).** Today a move whose target is the ticket's status,
   made by someone else, is recorded `skip` ("already Done: made by someone else") and the entry's update comment is still posted once,
   its marker keeping it single; `test_a_move_already_made_is_skipped_and_said_to_be_someone_elses` pins it for Jira. That rule is what
   makes a ticket Linear's GitHub integration closed on merge read as made, with the merge update posted and nothing moved twice, which is
   also what the canceled V26-3261's Scope 3 asked for. V26-3269 runs that row for the Linear adapter too, and adds that `needs_you()`
   raises no item for it, while `test_a_move_not_offered_is_handled_once_and_shown` keeps asserting its `handled` item. `handled` keeps
   meaning "not offered, make it by hand" (config.md), so V26-3269's AC3, "recorded as `handled`", is met by the existing `skip` outcome
   with the status found and the reason "already <status>: made by someone else", as the lead recorded on V26-3269.
4. **Linear is chosen the way Jira is, in two steps.** V26-3269 adds the adapter while host mode still exists, so it is opt-in there:
   `tracker.adapter: "linear"` in `.agents/run-delivery.json` (or `tracker.argv`) selects the Linear adapter and the `publisher` mode,
   with the binding's `--kind linear` snapshot supplying the default `tokenEnv`, and a Linear snapshot with no adapter named keeps
   `host`, so `HostOutboxTests` stays green until V26-3334. V26-3334 then makes a `--kind linear` snapshot imply the Linear adapter, as
   `--kind jira` already implies Jira's, so Linear needs no extra setting, and `lead-setup` adds the tracker publisher for either. `doctor`
   checks the token variable for both.
5. **The second ruling removes host mode (V26-3334).** Removed:
   - from orchestrate-work: the binding schema's host publisher (`kind: "host"`), refused by validation with the fix "name the tracker
     publisher"; the state tool's outbox and `ack`, and the close's wait on them; the render wrapper's outbox write; and the outbox's
     items in Needs you and on the line map. `SKILL.md`, the guide and `schemas.json` match.
   - from run-delivery: `tracker.updates: host` and the default that chose it; the moves and posts `lane-start`, `handoff` and the lead
     commands print for a host to make; and the outbox reading in `needs_you()`. `doctor`, `lead-init` and `lead-setup` refuse a host
     publisher, naming the fix. `config.md`, `adopting.md` and `SKILL.md` match.
   The removal is pinned by behaviour and by exact tokens. Behaviour: `delivery_state.py ack` is refused as an unknown command, and a
   binding naming a host publisher fails validation. Tokens, each absent from every shipped skill, reference and script: the `ack`
   subcommand's registration in `delivery_state.py`, the `"kind": "host"` literal in `schemas.json` and any `== "host"` comparison in a
   script, the outbox file name the render wrapper wrote (`OUTBOX_FILE`'s value) outside the one named exception, and the phrases "host
   publisher" and "host outbox" in prose. The one exception is the reopen code that recognises a host-mode record's old files by their
   path constant, so it can ignore them; it names them once, in one module. The tests that pinned host behaviour are deleted with it,
   each named in the hand-off, except V26-3269's `journalThrough` boundary row, which is kept unchanged as the new generation's reopen
   behaviour. `published.json` stays only as something read: after V26-3334 nothing writes it, and Decision 8's seeding reads the
   `journalThrough` an earlier `ack` left there. `tracker.updates: manual` stays. A record
   opened under host mode reopens under the new generation with its outbox files ignored, no outbox item, and no re-post. Because a
   binding naming a host publisher stops validating, the release is a major version, and its release notes say what an adopter changes:
   replace the host publisher with the tracker publisher (`lead-setup` adds it), and export the tracker's token.
6. **The Linear qualifier's sensor covers agent-facing documents and holds the whole delta (V26-3097).** Its charter is the agent-facing
   Linear skills. It inspects every file the `linear` profile adds over `run-delivery`, `SKILL.md`, `references/` and `agents/` included,
   for an actionable instruction to reach Linear any other way. The Linear-only skill ids (`create-linear-ticket`, `execute-linear-ticket`,
   `linear-tracker-adapter`) are a trusted constant in `scripts/qualify-linear.py`, held the way `TRUSTED_CONTRACT_SHA256` is, never
   derived from the candidate's catalog or profiles. Every file of those skills must be in the delta, so a Linear skill moved into the
   `run-delivery` profile, with the catalog and profiles changed together so the registry stays valid, fails the qualifier naming the
   missing skill, and an empty delta fails too. Adding a Linear skill means changing that constant on purpose. `tracker_linear.py` lives
   in run-delivery, the base, so it is outside the delta by design, and the qualifier's result says so where it is read: the sensor proves
   the Linear skills reach Linear only through the host, not that no file in the generation does.
7. **The scheduled connector check is a routine of the operator's, described in the repository (V26-3083).** The check needs a host with
   the Linear connector, so it runs as a scheduled cloud agent on the operator's account, weekly, against disposable issues it creates
   in one disposable team and cancels. The connector offers no issue delete, so V26-3083's AC3, "It leaves no entities behind", reads
   as: no open entity is left, and the cleanup step cancels every issue the run created, confirmed by searching for the run's marker
   and finding only canceled issues, as the lead recorded on V26-3083. The repository holds what it runs:
   `docs/linear-connector-check.md` gains the routine's prompt, its schedule, where its credentials live (the connector's own sign-in,
   never the repository), how a failure reaches the owner (a push notification and the routine's own run result, never a Linear entity,
   since the connector may be what failed), and the cleanup step it ends with, pinned beside "gates nothing". The lead creates the routine
   under the operator's standing approval, and the item closes on two recorded results: the first scheduled run, and one deliberately
   failing run, against a team that does not exist, whose notification reached the owner.
8. **The release and this repository's switch land together (V26-3333).** The new generation refuses a host publisher, and the old one
   can't run the Linear adapter, so this repository's binding switches in the same change that applies the release. After V26-3269,
   V26-3334 and V26-3339 merge, the lead runs the full suite on macOS with Python 3.11 and tags the next major candidate on a commit CI passed as the
   tip of a push to `main`, agreed with V26-3250's lead, whose supervisor must restart on it. V26-3333, by lane 1 in tier 2, applies the
   candidate on its branch, switches `.agents/orchestration.json` from the host publisher to the tracker publisher, and updates
   `docs/own-deliveries.md`'s "Tracker updates" and "Credentials", with the keychain-and-`~/.zshenv` recipe for an agent shell moved there
   from V26-3261's Scope 4 and `doctor`'s fix line pointing to it. It lands through the queue. Immediately before it merges, the lead
   posts every outbox entry still held and runs `ack`, stops this delivery's supervisor by its pid, and asks V26-3250's lead to stop
   theirs, since each lead stops only what it started. The lead then merges V26-3333 with one `lead-run --once` pass under the old
   generation; the entries that pass journals sit above `journalThrough`, so the publisher posts them later. Once it merges, each
   supervisor restarts with `lead-run --resume` on the new generation, V26-3250's by its own lead. A record opened under host mode has no ledger, so the publisher's first run over it writes
   a final `skip`, "published before the tracker publisher", for every entry at or below `published.json`'s `journalThrough`, and posts
   nothing for them. V26-3269 builds that seeding and pins it with a record holding entries at `journalThrough` and at
   `journalThrough` + 1: the first is a final `skip` with no comment, and the second is posted once through the shipped
   `tracker_linear.py`, with its comment and marker on the stub. A replayed start move on a Done ticket is recorded `handled` and never
   moves it, with its `needs_you()` item asserted. The finish line's evidence is the first entry posted after the switch: its ledger line,
   its Linear comment carrying the marker, and a `markers --ticket` read against real Linear returning it, linked on V26-3333.
9. **The third ruling adds V26-3339.** Under `selection.lead`, a candidate the lead accepts without a selection is checked on the
   binding's `fallback`, the whole suite here, and nothing in run-delivery says so: V26-3097 checked for 15 minutes that way on a
   two-file diff. On the operator's ruling (2026-11-10), "close the gap in this run", V26-3339 makes the choice explicit at acceptance
   and visible while checking. It adds no selection logic of its own: `lead-accept --checks` records through the state tool's `select`,
   with its validation, guards and readers, and the proposal is the catalogue ids matching the diff's test modules and the lane's named
   checks. It goes after V26-3334 on lane 1, which owns the files, and before the major candidate is tagged, so it ships in it.
10. **A broken base is repaired forward (V26-3342).** `fullHealth` failed at `c16bded`, V26-3269's merge, on
    `tests.test_compound_learning_corpus`: its two solution notes miss the note shape that test pins, and the lead's selection for it
    left that module out. Per the base-broken rules, V26-3342 lands the smallest fix through the queue and closes the gap: the binding's
    `selection.readers` maps `docs/solutions/*` to that module. It goes first on lane 1, which owns `docs/solutions/`, and the lane
    takes `.agents/orchestration.json` and `tests/test_own_deliveries.py` for it, ahead of V26-3333.
11. **The connector check's searches count only marked issues (V26-3343).** V26-3083's first deliberate failing run showed that
    Linear's search matches loosely: step 1's "expecting nothing" returned two older, unrelated issues. The run judged it correctly,
    but the text would let a stricter reader fail the weekly check, or count unrelated issues in the cleanup. V26-3343 says, in the
    on-demand steps and the routine prompt alike, that a match is an issue carrying the run's marker. Lane 2 owns the doc and its pins.
    Once it merges, the lead updates the two scheduled tasks' prompts to the merged text.

## Units and order

Two lanes. V26-3269 and V26-3334 both change the tracker publisher and its tests, and V26-3334 builds on the adapter, so lane 1 holds
them in order and then V26-3333. Lane 2 holds the qualifier and the connector check, which share nothing with lane 1.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3269, V26-3334, V26-3342, V26-3339 | `skills/run-delivery/`, `skills/orchestrate-work/`, `skills/delivery-views/`, `skills/linear-tracker-adapter/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_orchestration_render.py`, `tests/test_orchestration_state.py`, `tests/test_orchestration_adopter.py`, `tests/fixtures/orchestration-adopter/`, `tests/test_merge_queue.py`, `tests/delivery_views_page/`, `docs/solutions/`, `catalog.json`, `profiles/` |
| 2 | 1 | V26-3333 | `.agent-skills/`, `.agents/skills/`, `.claude/skills/`, `.agents/orchestration.json`, `.agents/run-delivery.json`, `docs/own-deliveries.md`, `tests/test_own_deliveries.py` |
| 1 | 2 | V26-3097, V26-3083, V26-3343 | `scripts/qualify-linear.py`, `tests/test_linear_qualification.py`, `docs/linear-connector-check.md` |

**Another delivery on `main`.** V26-3250 runs at the same time with its own queue. Its lane 1 holds `skills/run-delivery/`,
`skills/orchestrate-work/`, `skills/delivery-views/` and their tests, and its lane 2 holds `.agents/orchestration.json`,
`docs/own-deliveries.md` and `tests/test_own_deliveries.py`. The operator chose to run both at once. No lead queues a candidate on a
shared file while the other delivery's candidate touching it is queued, and each lead tells the other when a story merges. V26-3334
changes code V26-3250's lane 1 may be changing, so the lead tells V26-3250's lead before V26-3334 starts. V26-3333's apply and switch
are agreed with V26-3250's lead, whose own release candidate and binding change (V26-3309) the two leads order between them.
`provenance.lock.json` is regenerated by the writer at sync and never merged by hand. A lane works and reviews freely meanwhile: only
the hand-off to the queue waits, and a lane rebases only when git reports a conflict.

**Shared resources.** No lane runs a `lead-*` command from the main checkout, and no test reaches `.orchestration/` or `.agents/deliveries/`
outside its own folder. The adapter's rows run a stub HTTP server the test owns, on an ephemeral port, and never reach Linear.

## Stories

### V26-3269. Post a delivery's Linear updates and moves from a publisher, not by hand

Lane 1, tier 1, not blocked. Test-first, per the item and Decisions 1 to 4, and Decision 8's seeding. Contract rows run
`tracker_linear.py` against a stub GraphQL server the test owns, which checks each request's operation name, required variables and
`Authorization` header, refusing a header that is not exactly the named variable's value with Linear's 401 shape. Rows cover each
operation; each failure class (HTTP 429 and 5xx transient, another 4xx, except 401 and 403, permanent, unreachable, and `errors` at HTTP
200 for `comment`, `transition` with an unknown state and `status`, mapped as Decision 2 says, with the publisher's outcome named in each
row: an authentication code transient with no ledger success line, a forbidden code permanent for that ticket and transient when the
whole site refuses too, any other error a permanent `failed`); a missing token variable and a different `--token-env` name; the marker
round trip with the marker on the second page of comments; the comment limit; a failure between a move's two calls, in each order, re-run
to a ledger that matches the stub with one marker comment; `offered` refusing an earlier workflow type, with the allow side in the same
row (In Progress offers In Review and Done, and the move is made); and a token that appears in no output, log or ledger line. Publisher
rows run through the shipped `tracker_linear.py`, as the Jira rows run through `tracker_jira.py`: with the Linear adapter configured, a
delivery's `lane.assigned`, hand-off, merge and decision entries post with no lead action and are in the ledger once; the
already-in-target row of Decision 3 runs for both adapters, with no `needs_you()` item; and the first run over a host-mode record skips
through `journalThrough`, as Decision 8 says. Settings rows: `tracker.adapter: "linear"` gives mode publisher with the snapshot's
`tokenEnv`, and a Linear snapshot naming `--token-env` with no adapter gives mode host and no refusal. Absence pins cover Decision 1's
withdrawn wording, and presence pins keep `linear-tracker-adapter`'s MCP-only rule. Before it hands off, the live marker check of
Decision 2, recorded on the item. Files: `skills/run-delivery/adapters/`, `skills/run-delivery/scripts/tracker_publisher.py`,
`skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/references/config.md`, `skills/run-delivery/references/adopting.md`,
`skills/run-delivery/SKILL.md`, `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery.py`, `catalog.json` and `profiles/`.
Sensors: `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery.py`, `tests/test_linear_qualification.py`,
`scripts/validate-registry.py`. The item's compounding note goes in `docs/solutions/`.

### V26-3334. Remove the host publisher mode: every tracker posts through the tracker publisher

Lane 1, tier 1, after V26-3269. Test-first, per the item and Decisions 4 and 5. Rows: a binding naming a host publisher fails validation,
and `doctor` and `lead-init` refuse it naming the fix; a `--kind linear` snapshot binding with the tracker publisher and no `tracker`
settings posts through the shipped `tracker_linear.py` against the stub, as the `--kind jira` row does through `tracker_jira.py`;
`tracker.updates: host` is refused with the fix; `manual` still works: with `tracker.updates: manual`, `lane-start` prints the
start move and post, and `handoff` prints the review move, as today; `lead-setup` on a `--kind linear` binding adds the tracker publisher,
as the Jira row does; `delivery_state.py ack` is refused as an unknown command; a record opened under host mode, with outbox files and a
`published.json`, reopens with no outbox item and no re-post, while V26-3269's `journalThrough` boundary row stays unchanged and
green; `lane-start` and `handoff` print no move for anyone to make when the
publisher posts; and the absence pins of Decision 5. The settings row V26-3269 added for host mode is replaced by the implied-adapter row,
and the replaced and deleted host rows are named in the hand-off. The line map's page tests drop their outbox cases. Files:
`skills/orchestrate-work/`, `skills/delivery-views/`, `skills/run-delivery/`, `tests/test_orchestration_state.py`,
`tests/test_orchestration_render.py`, `tests/test_orchestration_adopter.py`, `tests/fixtures/orchestration-adopter/`,
`tests/test_merge_queue.py`, `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery.py`,
`tests/delivery_views_page/`, `catalog.json` and `profiles/`. Sensors: those test modules, the page lane, `scripts/validate-registry.py`.

### V26-3342. Repair main: give V26-3269's solution notes the corpus's note shape, and select the corpus check for notes

Lane 1, tier 1, before V26-3339, per the item and Decision 10. The two notes gain what `tests.test_compound_learning_corpus` pins
(the missing sections in one, `symptoms:` in the other) without losing what they teach; the binding's `selection.readers` gains
`docs/solutions/*` → `tests.test_compound_learning_corpus`, with any pin on the readers updated. Files: `docs/solutions/`,
`.agents/orchestration.json`, `tests/test_own_deliveries.py`. Sensors: `tests.test_compound_learning_corpus`,
`tests.test_own_deliveries`, `scripts/validate-registry.py`.

### V26-3339. Make the lead choose a candidate's checks, or the full suite, when accepting it

Lane 1, tier 1, after V26-3334. Test-first, per the item and Decision 9. Rows: under a binding with `selection.lead`, `lead-accept`
with neither `--checks` nor `--full-suite` refuses, records nothing, and prints a proposed `--checks` built from the candidate's diff
and the hand-off's named checks; `--checks` records the selection at the handed head through `select`, and an id `select` refuses is
refused the same way; `--full-suite` accepts on the fallback and journals that as the lead's decision; `handoff --checks` takes
catalogue ids, refuses an unknown one, and shows them in the hand-off message and the record; while a candidate checks on the fallback
under `selection.lead`, the supervisor's status line and the live view's checking panel say so, naming the fallback; and a binding
without `selection.lead` accepts exactly as before. Files: `skills/run-delivery/`, `skills/orchestrate-work/`,
`tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_orchestration_render.py`, `catalog.json`,
`profiles/` and `provenance.lock.json`. Sensors: those test modules, `scripts/validate-registry.py`.

### V26-3333. Post this repository's own deliveries' Linear updates through the tracker publisher

Lane 1, tier 2, after V26-3269 and V26-3334. Per the item and Decision 8, it starts once the lead has tagged and published the major
release candidate. Test-first: a `StandingBinding` row in `tests/test_own_deliveries.py` pins the tracker publisher in the committed
binding, with no host publisher. Beside it, rows on `docs/own-deliveries.md` pin the absence of "reachable only through the host" and
Decision 1's listed synonyms, and of "outbox", and the presence of the publisher posting with `LINEAR_API_KEY` and of the
keychain-and-`~/.zshenv` recipe under "Credentials". Then the lane applies the candidate as `docs/own-deliveries.md` says, switches the
binding, and rewrites "Tracker updates" and "Credentials". The lead drains and acks the host outbox immediately before it merges, merges it with
one `lead-run --once` pass as Decision 8 says, and records the finish-line evidence on the item. Files: `.agent-skills/`, `.agents/skills/`, `.claude/skills/`,
`.agents/orchestration.json`, `.agents/run-delivery.json`, `docs/own-deliveries.md` and `tests/test_own_deliveries.py`. Sensors:
`tests/test_own_deliveries.py`, `scripts/validate-registry.py`, `run_delivery.py doctor`.

### V26-3097. Widen what the Linear qualifier's transport sensor proves

Lane 2, tier 1, not blocked. Test-first, per the item and Decision 6. Rows: a planted `curl` instruction in a Linear `SKILL.md` outside
the adapter fails; a planted endpoint instruction in a Linear `references/*.md` file fails; one Linear skill, carrying a planted
instruction, moved into the `run-delivery` profile with the catalog and profiles changed together, fails, naming the missing skill; an
empty delta fails; the unmodified corpus, with `tracker_linear.py` in run-delivery, passes, and its result states the sensor's charter.
Files: `scripts/qualify-linear.py` and `tests/test_linear_qualification.py`. Sensors: `tests/test_linear_qualification.py`,
`scripts/validate-registry.py`.

### V26-3083. Run the on-demand Linear connector check on a schedule

Lane 2, tier 1, after V26-3097. Sensor-only, per the item and Decision 7. The lane writes the routine's prompt, schedule, credential
location, failure route and cleanup into `docs/linear-connector-check.md`, and corpus pins keep "gates nothing", the cleanup step and
"never the repository" beside the new section. The lead creates the routine, and the item closes on the two results Decision 7 names.
Files: `docs/linear-connector-check.md` and `tests/test_linear_qualification.py`. Sensors: `tests/test_linear_qualification.py`, the
first scheduled run's result, the deliberately failing run's notification, `scripts/validate-registry.py`.

### V26-3343. Make the connector check's marker searches count only issues that carry the marker

Lane 2, tier 1, after V26-3083, per the item and Decision 11. Sensor-only. The doc's on-demand steps and the routine prompt say a match
is an issue whose title or description carries the run's marker, in step 1, step 3 and the cleanup search, with the exact pins in
`tests/test_linear_qualification.py` updated and each proven red by a mutant. Files: `docs/linear-connector-check.md`,
`tests/test_linear_qualification.py`. Sensors: `tests.test_linear_qualification`, `scripts/validate-registry.py`.

## Execution posture

`test-first` for V26-3269, V26-3334, V26-3342, V26-3339, V26-3333 and V26-3097, and `sensor-only` for V26-3083 and V26-3343. Lanes work to the focused lane profile: no
full suite in a lane. The queue runs the selected checks on the synced head, `fullHealth` runs after each merge, and the lead runs the
full suite before tagging.

## Approval handoffs

- The plan itself, before `lead-setup`.
- The major release candidate's tag, agreed with V26-3250's lead, and its apply here in V26-3333.
- Creating the scheduled connector-check routine on the operator's account (V26-3083).
- The operator granted all of these on 2026-11-09 ("you're approved to make judgement calls to land the delivery along with any and
  all approvals"); the lead records each on the epic as it is used.
- Nothing else is outward-facing beyond the merge queue's own merges, which the binding already authorizes.

## Tracking

The epic and its items are in Linear: V26-3269, V26-3334, V26-3342, V26-3339, V26-3333, V26-3097, V26-3083 and V26-3343, with V26-3261 canceled as superseded. Until
the switch, the lead posts at each milestone with the host's Linear tools and moves items to their final state. After it, the tracker
publisher does.
