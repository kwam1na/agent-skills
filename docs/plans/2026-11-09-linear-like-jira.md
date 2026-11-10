---
reviewSlug: linear-like-jira
---

# Make Linear work the way Jira does

Epic V26-3332: the four open Linear items, gathered under one epic by the operator's ruling on V26-3269 (2026-11-09), "linear should
work the same way jira works". Each item's tracker scope and acceptance criteria bind what it does. This plan decides only what the items
leave open: how the ruling reads in the corpus, the choices two items share, order, file ownership while another delivery holds some of
the same files, and how the work reaches `main`.

## Finish line

- Each item is merged to `main` through the merge queue, or closed with a recorded reason.
- `main`'s CI on the last merged commit passes on the Ubuntu job and `cut-release`, with the run linked.
- The full local suite passes on macOS with Python 3.11 at the commit the release candidate is tagged on.
- A release candidate carrying the Linear adapter is tagged, published and applied here, and this repository's own deliveries post their
  Linear updates and moves through the tracker publisher, with no host outbox for the lead to drain.
- No finding is deferred: every finding any review in this delivery raises, at any severity, is fixed in the story that found it, and no
  follow-up item is filed for it.

## Out of scope

- V26-2947, a release-set tracking item from before tag-driven releases, and V26-1592, which is Athena's repository.
- The rest of V26-3306 (V26-3260, V26-3262, V26-3270) and of V26-3266.
- Any change to how agents themselves reach Linear: `linear-tracker-adapter`, `create-linear-ticket` and `execute-linear-ticket` stay
  MCP-only.
- A Linear write path anywhere but run-delivery's tracker publisher and the commands that already post through a tracker adapter.
- A final (non-candidate) release. The candidate is promoted at a later batch boundary, as `docs/product-generation.md` says.

## Decisions

1. **The ruling splits an agent-facing rule from a machine-facing path, exactly as Jira does.** `jira-tracker-adapter` allows only the
   host's Atlassian MCP connector, while run-delivery's `tracker_jira.py` writes to Jira's REST API with a token from the environment.
   Linear takes the same split. `linear-tracker-adapter`'s "use only the host's native Linear MCP capability" and its contract's
   `transportBoundary` stay word for word, held by presence pins. What is withdrawn is the claim that Linear is reachable only through
   the host: the `tracker_publisher.py` comment, the `host` bullet in `skills/run-delivery/references/config.md`, the troubleshooting
   entry in `references/adopting.md`, and `docs/own-deliveries.md`'s "Tracker updates". Per AGENTS.md, V26-3269 searches by meaning
   across every skill in the `run-delivery` and `linear` profiles for that claim and its synonyms ("only through the host's connector",
   "host-only tracker", "by this corpus's rule", "Linear needs a host publisher"), rewords each hit, and pins their absence. The generic
   wording for any tracker reachable only through a host, such as the orchestrate-work guide's host publisher, stays, with a presence pin,
   since a host publisher remains supported. No rewording names Linear's endpoint beside an instruction in a Linear skill, which
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
     permanent `failed`. An HTTP 429 or 5xx with no `errors` is passed through, and an
     unreachable endpoint is `null`, as for Jira. An answer with `errors` is never success, so no ledger line is written for it.
   - **Markers.** Linear has no per-issue properties, so a marker is carried in the comment body as a trailing HTML comment,
     `<!-- run-delivery:<marker> -->`. `markers --ticket` follows the comments connection's `pageInfo` to the end, bounded by a
     `MAX_PAGES` like `tracker_jira.py`'s. Before the switch, V26-3269 posts one marked comment through the adapter to an item in this
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
3. **The already-in-target rule is the publisher's existing one, kept (V26-3269, used by V26-3261).** Today a move whose target is the
   ticket's status, made by someone else, is recorded `skip` ("already Done: made by someone else") and the entry's update comment is still
   posted once, its marker keeping it single; `test_a_move_already_made_is_skipped_and_said_to_be_someone_elses` pins it for Jira. That
   rule is what makes a ticket Linear's GitHub integration closed on merge read as made, with the merge update posted and nothing moved
   twice. V26-3269 runs that row for the Linear adapter too, and adds that `needs_you()` raises no item for it, while
   `test_a_move_not_offered_is_handled_once_and_shown` keeps asserting its `handled` item. `handled` keeps meaning "not offered, make it
   by hand" (config.md), so V26-3269's AC3, "recorded as `handled`", is met by the existing `skip` outcome with the status found and
   the reason "already <status>: made by someone else". The lead records that reading on V26-3269 under the operator's 2026-11-09
   grant before the story is reviewed. In host mode there is no ledger: V26-3261's reconcile reads the ticket's status from the tracker snapshot, and the outbox
   drops a move whose target the ticket is already in while keeping its post.
4. **`lead-setup` adds the publisher for Linear as it does for Jira, when the configuration names the adapter (V26-3269).** Only
   `tracker.adapter: "linear"` in `.agents/run-delivery.json` (or `tracker.argv`) selects the Linear adapter and the `publisher` mode.
   The binding's `--kind linear` snapshot then only supplies the default `tokenEnv`. A Linear snapshot with no adapter named keeps
   today's default, `host`, with no refusal, so an adopter who never opts in sees no change, and
   `HostOutboxTests.test_an_undrained_host_outbox_raises_needs_you_naming_the_updates` stays as written as that pin. `doctor` checks the
   token variable as it does for Jira.
5. **The host outbox stays and gets posts and moves (V26-3261).** For adopters who keep `updates: host`, the outbox carries, beside the
   raw entries, each entry's rendered post and move from the tracker updates table, and one `ack` covers a published batch. The text is
   the publisher's `body_of()` without what only a tracker adapter knows (the status notes and `commentLimit`), and a row compares the
   two for every kind in the updates table. Scope 2 shipped in 04e9bc8, and V26-3261 pins it with a row whose allow side is in the same
   row: assigning three stories starts none, and `lane-start` then starts exactly that story.
6. **The Linear qualifier's sensor covers agent-facing documents and holds the whole delta (V26-3097).** Its charter is the agent-facing
   Linear skills. It inspects every file the `linear` profile adds over `run-delivery`, `SKILL.md`, `references/` and `agents/` included,
   for an actionable instruction to reach Linear any other way. The Linear-only skill ids (`create-linear-ticket`, `execute-linear-ticket`,
   `linear-tracker-adapter`) are a trusted constant in `scripts/qualify-linear.py`, held the way `TRUSTED_CONTRACT_SHA256` is, never
   derived from the candidate's catalog or profiles. Every file of those skills must be in the delta, so a Linear skill moved into the
   `run-delivery` profile, with the catalog and profiles changed together so the registry stays valid, fails the qualifier naming the
   missing skill, and an empty delta fails too. Adding a Linear skill means changing that constant on purpose. `tracker_linear.py` lives in run-delivery, the base, so it is outside the delta by
   design, and the qualifier's result says so where it is read: the sensor proves the Linear skills reach Linear only through the host,
   not that no file in the generation does.
7. **The scheduled connector check is a routine of the operator's, described in the repository (V26-3083).** The check needs a host with
   the Linear connector, so it runs as a scheduled cloud agent on the operator's account, weekly, against disposable issues it creates
   in one disposable team and cancels. The connector offers no issue delete, so V26-3083's AC3, "It leaves no entities behind", reads
   as: no open entity is left, and the cleanup step cancels every issue the run created, confirmed by searching for the run's marker
   and finding only canceled issues. The lead records that reading on V26-3083 under the operator's grant. The repository holds what it runs: `docs/linear-connector-check.md` gains the routine's prompt, its schedule, where its
   credentials live (the connector's own sign-in, never the repository), how a failure reaches the owner (a push notification and the
   routine's own run result, never a Linear entity, since the connector may be what failed), and the cleanup step it ends with, pinned
   beside "gates nothing". The lead creates the routine under the operator's standing approval, and the item closes on two recorded
   results: the first scheduled run, and one deliberately failing run, against a team that does not exist, whose notification reached
   the owner.
8. **The release and this repository's switch.** After V26-3269 and V26-3261 merge, the lead runs the full suite on macOS with Python
   3.11, tags the next candidate on a commit CI passed as the tip of a push to `main`, and applies it, agreed with the leads of the
   deliveries running then, whose supervisors restart on it with `lead-run --resume`. V26-3269's last step, by lane 1, then sets
   `tracker.adapter: "linear"` in `.agents/run-delivery.json`, replaces the binding's host publisher with the tracker publisher, and
   updates `docs/own-deliveries.md`'s "Tracker updates" and "Credentials" (with V26-3261's keychain recipe). That step lands through the
   queue like any change. A record opened under host mode has no ledger, so the publisher's first run over it writes a final `skip`,
   "published through the host outbox", for every entry at or below `published.json`'s `journalThrough`, and posts nothing for them;
   V26-3269 pins it with a record holding entries at `journalThrough` and at `journalThrough` + 1: the first is a final
   `skip` with no comment, and the second is posted once through the shipped `tracker_linear.py`, with its comment and marker on the stub.
   A replayed start move on a Done ticket is recorded `handled` and never moves it, with its `needs_you()` item asserted. Immediately
   before the switch merges, the lead posts every outbox entry still held and runs `ack`, so nothing posted by hand but not acked is
   posted again. The finish line's evidence is the first entry posted after the
   switch: its ledger line, its Linear comment carrying the marker, and a `markers --ticket` read against real Linear returning it,
   linked on V26-3269.

## Units and order

Two lanes. V26-3269 and V26-3261 both change the tracker publisher and its tests, so lane 1 holds them in order. Lane 2 holds the
qualifier and the connector check, which share nothing with lane 1.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3269, V26-3261 | `skills/run-delivery/`, `skills/orchestrate-work/`, `skills/linear-tracker-adapter/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_tracker.py`, `tests/test_orchestration_render.py`, `tests/test_orchestration_state.py`, `docs/own-deliveries.md`, `tests/test_own_deliveries.py`, `.agents/run-delivery.json`, `.agents/orchestration.json`, `docs/solutions/`, `catalog.json`, `profiles/` |
| 1 | 2 | V26-3097, V26-3083 | `scripts/qualify-linear.py`, `tests/test_linear_qualification.py`, `docs/linear-connector-check.md` |

**Another delivery on `main`.** V26-3250 runs at the same time with its own queue. Its lane 1 holds `skills/run-delivery/`,
`skills/orchestrate-work/` and their tests, and its lane 2 holds `.agents/orchestration.json`, `docs/own-deliveries.md` and
`tests/test_own_deliveries.py`. The operator chose to run both at once. No lead queues a candidate on a shared file while the other
delivery's candidate touching it is queued, and each lead tells the other when a story merges. `provenance.lock.json` is regenerated by
the writer at sync and never merged by hand. A lane works and reviews freely meanwhile: only the hand-off to the queue waits, and a lane
rebases only when git reports a conflict.

**Shared resources.** No lane runs a `lead-*` command from the main checkout, and no test reaches `.orchestration/` or `.agents/deliveries/`
outside its own folder. The adapter's rows run a stub HTTP server the test owns, on an ephemeral port, and never reach Linear.

## Stories

### V26-3269. Post a delivery's Linear updates and moves from a publisher, not by hand

Lane 1, tier 1, not blocked. Test-first, per the item and Decisions 1 to 4 and 8. Contract rows run `tracker_linear.py` against a stub
GraphQL server the test owns, which checks each request's operation name, required variables and `Authorization` header, refusing a
header that is not exactly the named variable's value with Linear's 401 shape. Rows cover each operation; each failure class (HTTP 429
and 5xx transient, another 4xx permanent, unreachable, and `errors` at HTTP 200 for `comment`, `transition` with an unknown state and
`status`, mapped as Decision 2 says, with the publisher's outcome asserted); a missing token variable and a different `--token-env` name;
the marker round trip with the marker on the second page of comments; the comment limit; a failure between a move's two calls, in each
order, re-run to a ledger that matches the stub with one marker comment; `offered` refusing an earlier workflow type, with the allow side in the same row (In Progress offers In Review and Done,
and the move is made); and a token that
appears in no output, log or ledger line. Publisher rows run through the shipped `tracker_linear.py`, as the Jira rows run through
`tracker_jira.py`: with the Linear adapter configured, a delivery's `lane.assigned`, hand-off, merge and decision entries post with no lead
action and are in the ledger once; the already-in-target row of Decision 3 runs for both adapters, with no `needs_you()` item; and the
first run over a host-mode record skips through `journalThrough`. Settings rows: `tracker.adapter: "linear"` gives mode publisher with the
snapshot's `tokenEnv`, and a Linear snapshot naming `--token-env` with no adapter gives mode host and no refusal. Absence pins cover
Decision 1's withdrawn wording, and presence pins keep `linear-tracker-adapter`'s MCP-only rule and the generic host-publisher wording.
Before the switch, the live marker check of Decision 2; after the release applies, the switch and evidence of Decision 8. Files:
`skills/run-delivery/adapters/`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/references/config.md`, `skills/run-delivery/references/adopting.md`, `skills/run-delivery/SKILL.md`,
`tests/test_run_delivery_tracker.py`, `tests/test_run_delivery.py`, `catalog.json`, `profiles/`, `.agents/run-delivery.json`,
`.agents/orchestration.json`, `docs/own-deliveries.md` and `tests/test_own_deliveries.py`. Sensors: `tests/test_run_delivery_tracker.py`,
`tests/test_run_delivery.py`, `tests/test_own_deliveries.py`, `tests/test_linear_qualification.py`, `scripts/validate-registry.py`. The
item's compounding note goes in `docs/solutions/`.

### V26-3261. Make the host outbox a list of posts and moves, and document tracker credentials for an agent shell

Lane 1, tier 1, after V26-3269. Test-first, per the item and Decisions 3 and 5. Rows: the outbox for a merge lists "comment on <key>: …;
move <key> to Done"; the outbox's text matches `body_of()` for every kind in the updates table, less the adapter-only notes; one `ack`
covers a published batch; assigning three stories starts none, and `lane-start` then starts exactly that story; a ticket already Done
from forge automation reconciles from the tracker snapshot with its post kept, its move dropped and no duplicate post. `doctor`'s fix line for `LINEAR_API_KEY` points to the keychain-and-`~/.zshenv`
recipe in `docs/own-deliveries.md`, and the orchestrate-work guide notes the forge automation overlap and how to turn it off. Files:
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/scripts/run_delivery.py`, `tests/test_run_delivery_tracker.py`,
`tests/test_orchestration_render.py`, `tests/test_run_delivery.py` and `docs/own-deliveries.md`. Sensors: `tests/test_run_delivery_tracker.py`,
`tests/test_orchestration_render.py`, `tests/test_run_delivery.py`, `scripts/validate-registry.py`.

### V26-3097. Widen what the Linear qualifier's transport sensor proves

Lane 2, tier 1, not blocked. Test-first, per the item and Decision 6. Rows: a planted `curl` instruction in a Linear `SKILL.md` outside
the adapter fails; a planted endpoint instruction in a Linear `references/*.md` file fails; one Linear skill, carrying a planted
instruction, moved into the `run-delivery` profile with the catalog and profiles changed together, fails, naming the missing skill; an empty delta
fails; the unmodified corpus, with `tracker_linear.py` in run-delivery, passes, and its result states the sensor's charter. Files: `scripts/qualify-linear.py` and
`tests/test_linear_qualification.py`. Sensors: `tests/test_linear_qualification.py`, `scripts/validate-registry.py`.

### V26-3083. Run the on-demand Linear connector check on a schedule

Lane 2, tier 1, after V26-3097. Sensor-only, per the item and Decision 7. The lane writes the routine's prompt, schedule, credential
location, failure route and cleanup into `docs/linear-connector-check.md`, and corpus pins keep "gates nothing", the cleanup step and
"never the repository" beside the new section. The lead creates the routine, and the item closes on the two results Decision 7 names. Files:
`docs/linear-connector-check.md` and `tests/test_linear_qualification.py`. Sensors: `tests/test_linear_qualification.py`, the first scheduled run's result,
the deliberately failing run's notification, `scripts/validate-registry.py`.

## Execution posture

`test-first` for V26-3269, V26-3261 and V26-3097, and `sensor-only` for V26-3083. Lanes work to the focused lane profile: no full suite in
a lane. The queue runs the selected checks on the synced head, `fullHealth` runs after each merge, and the lead runs the full suite before
tagging.

## Approval handoffs

- The plan itself, before `lead-setup`.
- The release candidate's tag and its apply here, under the operator's grant for this delivery, agreed with V26-3250's lead.
- Creating the scheduled connector-check routine on the operator's account (V26-3083).
- The operator granted all of these on 2026-11-09 ("you're approved to make judgement calls to land the delivery along with any and
  all approvals"); the lead records each on the epic as it is used.
- Nothing else is outward-facing beyond the merge queue's own merges, which the binding already authorizes.

## Tracking

The epic and its four items are in Linear. Until the switch, the lead posts at each milestone with the host's Linear tools and moves
items to their final state. After it, the tracker publisher does.
