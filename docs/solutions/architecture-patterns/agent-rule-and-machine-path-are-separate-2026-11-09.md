---
title: "Keep an agent-facing transport rule apart from a machine-facing write path"
date: 2026-11-09
category: architecture-patterns
module: run-delivery
problem_type: architecture_pattern
severity: medium
tags: [tracker, linear, jira, publisher, transport-boundary]
applies_when: "A tracker skill limits agents to the host's connector, and a delivery tool needs to write to the same tracker unattended"
---

# Keep an agent-facing transport rule apart from a machine-facing write path

## Problem

`linear-tracker-adapter` tells agents to "use only the host's native Linear MCP
capability". run-delivery's documents had widened that rule into a claim about
Linear itself: they said Linear was reachable only through the host. So the
tracker publisher couldn't write to Linear, and every Linear update and move
waited in a host outbox until the lead posted it by hand. Jira never had that
problem. Its agent skill is MCP-only as well, but `tracker_jira.py` writes to
Jira's REST API with a token from the environment.

## Solution

Treat these as two separate rules, the way Jira already does:

- **The agent-facing rule.** Agents reach the tracker only through the host's
  connector. It lives in the tracker skill and its contract's
  `transportBoundary`, and it stays word for word, held by presence pins.
- **The machine-facing path.** The tracker publisher writes through a
  `tracker-adapter/1` command (`tracker_linear.py` beside `tracker_jira.py`)
  with a key from a named environment variable. That is the same variable the
  tracker snapshot already reads with.

What gets withdrawn is only the widened claim. Search every file the affected
profiles ship for it and its synonyms, reword each hit, and pin their absence
(`LinearWordingTests`).

Two Linear facts surfaced while building the adapter, and both are worth
checking before trusting a plan's premise:

- Linear's docs give no comment-length limit. The API states one when it
  refuses a longer body, and it validates before it looks the issue up. Probing
  a nonexistent issue therefore measures the limit without posting anything:
  100,000 UTF-16 units.
- Linear keeps a trailing HTML comment in a comment body. That makes
  `<!-- run-delivery:<marker> -->` a marker carrier that stays invisible and
  reads back intact.

## Why This Matters

An agent-facing rule that gets read as a fact about the provider blocks
unattended automation, which never needed the rule. If the two are kept apart
and each is pinned separately, a reader can't take one for the other. A later
change also can't loosen the agent rule while it widens the machine path.
