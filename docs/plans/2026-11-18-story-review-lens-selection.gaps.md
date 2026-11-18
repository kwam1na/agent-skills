# Gap log: story review lens selection

Gaps in the workflow or the tool found while delivering this plan, each recorded when it was met.

## Plan review briefs don't name which carried findings are still open (workflow)

- **What happened.** In round 3 of this plan's review, every lens result was refused by `plan-review reduce` with
  "resolvedFindings entry '...' isn't an open finding this lens raised earlier". Each lens had re-listed findings that
  round 2 had already closed. This happened with resumed reviewers and with fresh ones on the same brief: five refused
  results across two candidates, against one that got the format right.
- **Why the tool couldn't prevent it.** The plan brief says `resolvedFindings` holds "one entry for every finding this
  lens raised in an earlier round and has not closed" and "every carried finding must appear". It carries every
  earlier result in full but never names which ids are still open. The installed generation's story round brief has
  the same gap, so this delivery's own story review can meet it too.
- **What was done.** With the operator's approval, round 3's reviewers were started with the brief's exact prompt
  plus one sentence naming the lens's open findings.
- **Where it stands.** Tracked as V26-3430. Its brief half is fixed on `main` by a0dffff5 ("Name the still-open
  finding ids in each verification brief", PR #379), which merged after v3.1.0 was tagged, so the installed generation
  this delivery runs still has the old brief. The round-3 clarification stands in for it until a release carrying
  a0dffff5 is applied. V26-3430 is still in Backlog; whether its other half, `reduce` skipping a closed finding, is
  done was not checked.
