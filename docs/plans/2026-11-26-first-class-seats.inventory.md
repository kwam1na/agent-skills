# First-class seats - actor-word inventory

Every people-facing word that names an actor, as of `main` at `2ac337ca`, with
its disposition under `docs/plans/2026-11-26-first-class-seats.md`. `L` is
`skills/delivery-views/assets/line-map.js`. "Seat-aware" sites change; "kept"
sites keep their words (decision 7).

## Line map

| L:line | Words | Function | Owner scope | Disposition |
| --- | --- | --- | --- | --- |
| 1546 | "<target> moved. The lead is bringing <CR> up to date." | rejoinStage | delivery | kept |
| 1551 | "<CR>'s checks are still running.", mark "Merge · forge" | rejoinStage | forge | kept |
| 1552 | "<CR> waits for your merge.", mark "Merge · you" | rejoinStage | delivery | seat-aware, page-worded |
| 1556, 1560, 1564 | "The integration review is in round N.", "The lead opens the change request next.", "The lead is making it." | rejoinStage | delivery | kept |
| 1571, 1581, 1587 | suite running / passed / runs next (mark who lead, unnamed) | rejoinStage | delivery | kept |
| 1578 | "Every test passed, N min over budget. It's your call to accept.", mark "Suite · you" | rejoinStage | delivery | seat-aware, page-worded |
| 1584 | "The full suite failed at <sha>. The lead is fixing it." | rejoinStage | delivery | kept |
| 1611 | mark Close, who lead | nowState | delivery | kept |
| 1635-1647 | Needs you override: sentence from `said.headline`, mark "· you", fact "<mark> waits on you." | nowState | the item's | seat-aware (`yours`) |
| 1654-1660 | "Updates stopped …", "Last update …" | nowState | this machine | kept |
| 1716-1719 | mark suffix only for "you" or "forge" | nowBlock | rule | seat-aware: another seat's label for a person's call |
| 1822-1826 | status override; `waitLines` server sentences ("The lead's {work} waits …") | drawNow | delivery | kept |
| 1836-1840 | Needs you leads when an operator row exists | drawNow | placement | seat-aware: only when a row is yours |
| 2099 | "N more is waiting for the lead to accept the hand-over." | checkingPanel | lane | kept |
| 2161, 2166, 2177 | "Checks chosen by the lead …", "the lead selected no checks" | checkingPanel | queue | kept |
| 2537, 2538 | "The lead finishes it by resuming the close." / "… with a close that doesn't wait for the tracker" | closeStoppedPartway | delivery | kept |
| 2601 | heading "Waiting on someone" | needsYou | box | kept |
| 2610 | "Hidden while updates are stopped, until the lead restarts them." | needsYou | this machine | kept |
| 2619 | rank: holding, then operator | needsYou | rule | seat-aware: holding, then yours, then others' |
| 2621 | `summary` verbatim | needsYou | mixed | seat-aware (`summary` / `summaryAll`) |
| 2627-2632 | `data-who`, `said.who`, `said.headline`, `said.clears` | needsYou | the item's | seat-aware (`said` / `saidAll`) |
| 376, 379, 3375, 3550 | "waiting for the lead to accept" | stage words, legend, storyState | lane | kept |
| 1226, 3473 | lane label "Lead" | laneLabel | delivery | kept |
| 3173, 3177 | "Not yet: the lead merges …", "… runs the full suite …" | rejoinLedger | delivery | kept |
| 3202 | "The merge is the operator's." (also the merge bar via `rejoinSaid`) | rejoinLedger | delivery | seat-aware, page-worded |
| 3515 | "Needs the lead first: …" | showDetail | unclear | kept |
| 3710 | "Carried by the lead. …" | runFor | lane or queue | kept |
| 4095, 4116, 4123, 4165 | Who chapter: "No one has reported in yet.", lane waits, "waiting on <waitingOn>", "<lease> held by <holder>" | drawWho | lane | kept |
| 4370, 4671 | "It's waiting for the lead to accept it." | proofSaid | lane | kept |
| 4472 | "Marked met by the lead" / "The lead marks this one" | meter | delivery | kept |
| 4600-4630, 5189 | approvals, "Tell the agent leading this which you approve …" | approvalList, drawPlan | plan | kept: the plan's attention area, not Needs you |
| 4931-4933 | "Endorsed by A and B", stale endorsements | previewLine | plan | already by label |
| 5499, 5529 | "Each call the lead makes, and why.", "The lead made N decisions." | What happened | delivery | kept |
| 5698-5829 | Seats and Feedback chapters | drawSeats, feedback | various | already by label |

Other page files: `runs.js:68` "Needs you" badge (this machine's runs, kept);
`run-summary.js:171-172, 1004` "… or the operator ends it" (plan review, kept).
The local corner (`render.py:1846-1870`) shows the checkout's git email; kept.

## Supervisor words (`skills/orchestrate-work/references/wait-words.json`)

| Group | Words | Disposition |
| --- | --- | --- |
| `needsYou.who` | "You", "The lead", lane label | seat-aware: adds other-seat person, other seat's lead, "Someone" |
| `needsYou.whom` | "you", "the lead", lane label | same, used inside `laneAsks` and the fallback headline |
| `needsYou.summary`, `part` | "N things need {whom}" | seat-aware: shared summary |
| `needsYou.why` / `clears` with "the lead" | e.g. "until the lead decides", "Clears when the lead resolves it." | kept: "the lead" here is the owner's lead and the row's `who` already names the owner once |
| `stages.where.handed`, `waits.*`, `lanes.held`, `listening.*` | "the lead" | kept |

The summary is also rebuilt in `render.py:1471` when a tracker lag is present;
that path gains the same shared form.

## Not people-facing

Item `text` and `clears`, lead-wait and status lines, assignment messages and
close hints are read by the lead. Tracker posts are posted by the delivery
holder and say "the lead" without "you". The friction note copies item `text`.
None changes.
