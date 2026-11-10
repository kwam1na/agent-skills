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
   `transportBoundary` stay word for word. What is withdrawn is the claim that Linear is reachable only through the host: the
   `tracker_publisher.py` comment, the `host` bullet in `skills/run-delivery/references/config.md`, the troubleshooting entry in
   `references/adopting.md`, and `docs/own-deliveries.md`'s "Tracker updates". Per AGENTS.md, V26-3269 searches by meaning across every
   skill in the `run-delivery` and `linear` profiles for that claim and its synonyms ("only through the host's connector", "host-only
   tracker", "by this corpus's rule", "Linear needs a host publisher"), rewords each hit, and pins their absence. The generic wording
   for any tracker reachable only through a host, such as the orchestrate-work guide's host publisher, stays, since a host publisher
   remains supported.
2. **`tracker_linear.py` keeps `tracker-adapter/1` exactly (V26-3269).** It sits beside `tracker_jira.py` and has its shape:
   `--token-env` (default `LINEAR_API_KEY`) before the operation, then `status`, `markers`, `comment`, `transition` and `create`, each
   printing one JSON object, a failure exiting 3 with `{"error", "httpStatus"}`. It talks to Linear's GraphQL endpoint with
   `urllib.request` only, as `snapshot.py` does. Linear has no per-issue properties, so a marker is carried in the comment body as a
   trailing HTML comment, `<!-- run-delivery:<marker> -->`, which Linear's Markdown hides, and `markers --ticket` reads the issue's
   comments for it. A move's marker is a comment posted with the move, so a crash between the move and its marker leaves the ticket in the
   target status, which Decision 3 already treats as made. A created item's marker is a label, as for Jira. A status is a workflow state
   name on the item's team, and every state is offered (`transitions` lists them all with `offered: true`), because Linear has no
   transition graph. `commentLimit` is Linear's comment limit, counted the way `tracker_publisher.py` counts. The token is read only
   from the named variable and never printed, logged or written to the ledger.
3. **One "already in the target status" rule, in the publisher (V26-3269, used by V26-3261).** Before a move, the publisher reads the
   ticket's status through the adapter's `status`. A ticket already in the target status, moved by Linear's GitHub integration or by
   hand, is recorded in the ledger as `handled` with the status it found, and no comment or move is posted for that entry. V26-3261's
   reconcile reads the same ledger outcome, so a host-publisher delivery and a publisher delivery reconcile a forge-moved ticket the same
   way, and neither reopens or re-posts it. The rule is tracker-neutral, so Jira gets it too.
4. **`lead-setup` adds the publisher for Linear as it does for Jira (V26-3269).** `tracker.adapter: "linear"` in
   `.agents/run-delivery.json`, or a binding whose tracker snapshot is `--kind linear` and names its `--token-env`, makes `settings()`
   resolve the Linear adapter and the `publisher` mode. A Linear snapshot with no adapter named keeps today's default, `host`, so an
   adopter who never opts in sees no change: the default moves only when the configuration names the adapter. `doctor` checks the token
   variable as it does for Jira.
5. **The host outbox stays and gets posts and moves (V26-3261).** For adopters who keep `updates: host`, the outbox carries, beside the
   raw entries, each entry's rendered post and move from the tracker updates table, and one `ack` covers a published batch. The rendering
   is the publisher's own, so the outbox's text is what the publisher would have posted. Scope 2 shipped in 04e9bc8, and V26-3261 pins it
   with a row rather than building it again.
6. **The Linear qualifier's sensor covers agent-facing documents and fails an empty delta (V26-3097).** Its charter is the agent-facing
   Linear skills. It inspects every file the `linear` profile adds over `run-delivery`, `SKILL.md` and references included, for an
   actionable instruction to reach Linear any other way, and a delta with no file fails. `tracker_linear.py` lives in run-delivery, the
   base, so it is outside the delta by design, and the qualifier's result says so where it is read: the sensor proves the Linear skills
   reach Linear only through the host, not that no file in the generation does. A row moves a Linear skill into the `run-delivery`
   profile and asserts the qualifier fails.
7. **The scheduled connector check is a routine of the operator's, described in the repository (V26-3083).** The check needs a host with
   the Linear connector, so it runs as a scheduled cloud agent on the operator's account, weekly, against a disposable team. The
   repository holds what it runs: `docs/linear-connector-check.md` gains the routine's prompt, its schedule, where its credentials live
   (the connector's own sign-in, never the repository), how a failure reaches the owner (a push notification and a Linear issue in the
   disposable team, read the next day), and the cleanup step it ends with. Creating the routine is an approval handoff: the lead creates
   it only with the operator's yes, and records its first run's result on the item.
8. **The release and this repository's switch.** After V26-3269 and V26-3261 merge, the lead runs the full suite on macOS with Python
   3.11, tags the next candidate on a commit CI passed as the tip of a push to `main`, and applies it, agreed with the leads of the
   deliveries running then, whose supervisors restart on it with `lead-run --resume`. V26-3269's last step, by lane 1, then sets
   `tracker.adapter: "linear"` in `.agents/run-delivery.json`, replaces the binding's host publisher with the tracker publisher, and
   updates `docs/own-deliveries.md`'s "Tracker updates" and "Credentials" (with V26-3261's keychain recipe). That step lands through the
   queue like any change.

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
GraphQL server for each operation, each failure class (429 and 5xx transient, another 4xx permanent, unreachable), the marker round trip,
the comment limit, and a token that appears in no output, log or ledger line. Publisher rows: with the Linear adapter configured, a
delivery's `lane.assigned`, hand-off, merge and decision entries post with no lead action and are in the ledger once, and a ticket already
in the target status is `handled` with no comment. Absence pins cover Decision 1's withdrawn wording, and presence pins keep
`linear-tracker-adapter`'s MCP-only rule. After the release applies, the switch of Decision 8. Files: `skills/run-delivery/adapters/`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/references/config.md`,
`skills/run-delivery/references/adopting.md`, `skills/run-delivery/SKILL.md`, `tests/test_run_delivery_tracker.py`,
`tests/test_run_delivery.py`, `catalog.json`, `profiles/`, `.agents/run-delivery.json`, `.agents/orchestration.json`,
`docs/own-deliveries.md` and `tests/test_own_deliveries.py`. Sensors: `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery.py`,
`tests/test_own_deliveries.py`, `scripts/validate-registry.py`. The item's compounding note goes in `docs/solutions/`.

### V26-3261. Make the host outbox a list of posts and moves, and document tracker credentials for an agent shell

Lane 1, tier 1, after V26-3269. Test-first, per the item and Decisions 3 and 5. Rows: the outbox for a merge lists "comment on <key>: …;
move <key> to Done"; one `ack` covers a published batch; assigning three stories starts none until `lane-start`; a ticket already Done
from forge automation reconciles with no duplicate post. `doctor`'s fix line for `LINEAR_API_KEY` points to the keychain-and-`~/.zshenv`
recipe in `docs/own-deliveries.md`, and the orchestrate-work guide notes the forge automation overlap and how to turn it off. Files:
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/scripts/run_delivery.py`, `tests/test_run_delivery_tracker.py`,
`tests/test_orchestration_render.py`, `tests/test_run_delivery.py` and `docs/own-deliveries.md`. Sensors: `tests/test_run_delivery_tracker.py`,
`tests/test_orchestration_render.py`, `tests/test_run_delivery.py`, `scripts/validate-registry.py`.

### V26-3097. Widen what the Linear qualifier's transport sensor proves

Lane 2, tier 1, not blocked. Test-first, per the item and Decision 6. Rows: a planted `curl` instruction in a Linear `SKILL.md` outside
the adapter fails; a Linear skill moved into the `run-delivery` profile, emptying the delta, fails; `tracker_linear.py` in run-delivery
leaves the qualifier passing, and its result states the sensor's charter. Files: `scripts/qualify-linear.py` and
`tests/test_linear_qualification.py`. Sensors: `tests/test_linear_qualification.py`, `scripts/validate-registry.py`.

### V26-3083. Run the on-demand Linear connector check on a schedule

Lane 2, tier 1, after V26-3097. Sensor-only, per the item and Decision 7. The lane writes the routine's prompt, schedule, credential
location, failure route and cleanup into `docs/linear-connector-check.md`, and a corpus pin keeps "gates nothing" beside the new section.
The lead creates the routine under the approval handoff, and the item closes on its first run's result. Files:
`docs/linear-connector-check.md` and `tests/test_linear_qualification.py`. Sensors: the first scheduled run's result,
`scripts/validate-registry.py`.

## Execution posture

`test-first` for V26-3269, V26-3261 and V26-3097, and `sensor-only` for V26-3083. Lanes work to the focused lane profile: no full suite in
a lane. The queue runs the selected checks on the synced head, `fullHealth` runs after each merge, and the lead runs the full suite before
tagging.

## Approval handoffs

- The plan itself, before `lead-setup`.
- The release candidate's tag and its apply here, under the operator's grant for this delivery, agreed with V26-3250's lead.
- Creating the scheduled connector-check routine on the operator's account (V26-3083).
- Nothing else is outward-facing beyond the merge queue's own merges, which the binding already authorizes.

## Tracking

The epic and its four items are in Linear. Until the switch, the lead posts at each milestone with the host's Linear tools and moves
items to their final state. After it, the tracker publisher does.
