---
title: "Preserve reviewer reports without inventing a run"
date: 2026-10-23
category: design-patterns
module: delivery-views
problem_type: design_pattern
severity: medium
tags: [review, observations]
applies_when: "A lane has native review outcomes but no product run"
---

# Preserve reviewer reports without inventing a run

## Problem

A journal's review-round counts can establish that review was observed, but
cannot reconstruct its findings or prove approval. A lane without a product run
still has actual native outcomes, and a view needs to show them.

## Solution

Capture those original bytes through the supported state command and reference
their digest from a new observation; never synthesize a run or a replacement
reviewer report.

Bind every round to its subject and candidate, keep changing candidate refs
through the history, and accept a closure only from the finding's originating
lens. Read artifacts under size and path bounds, verify their digest, then
project them into the existing review cards. Check the current candidate again
in the browser when state pushes arrive so a retained card cannot approve a
new head while the next render is pending. Bind the card to the current journal
boundary and digest too: the same head can gain new dissent. Capture history
only by exact-byte extension, so a delayed retry cannot hide a newer round.
Charge failed reads to the read budget and share one page budget across both
native and product-backed reviews.

Work still under review has no head to bind to. Capture each round as it is
reduced, at that round's own candidate, after journaling it. Bind work that
isn't queued to its latest capture only when that capture follows the latest
journaled round and holds exactly that round's number of outcomes. Once the work
is handed off, the head binds it again.

## Why This Matters

The observation preserves review provenance for readers. Product admission,
release qualification and the authority of a workflow remain separate. A
synthesized run or report would claim evidence that never existed.

## Prevention

Treat unavailable artifacts and unknown candidate identity as unavailable, not
zero findings. A round journaled after the capture leaves the evidence
unavailable, and so does a capture with no journaled round or one holding a
different number of rounds. Nothing tells a capture made by hand from one made
at reduction, so these checks are the binding.

## Examples

- A retained review card when a state push brings a new head: the card is checked
  against the current candidate again, so it cannot approve the new head while the
  next render is pending.
- A capture taken before the latest round was journaled: the work's review
  evidence reads as unavailable until a capture follows that round.

## Related

- [Preserve source time during merge recovery](merge-occurrence-and-observation-2026-10-23.md),
  the same rule of recording what was observed rather than inventing history.
