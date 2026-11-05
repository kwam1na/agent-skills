---
title: "Two readers of one declaration agree only where a shared vector forces them to"
date: 2026-11-04
category: best-practices
module: run-delivery
problem_type: best_practice
severity: medium
tags: [delivery-posture, close-out, vectors, cross-language, bounds]
applies_when: "A Python reader and a JavaScript reader must give the same answer for the same bytes, or a skipped step must never read as a passed one"
---

# Two readers of one declaration agree only where a shared vector forces them to

## Problem

`run_delivery.py` and the harness each read `.agents/delivery-posture.json`, one in Python and one in TypeScript. Obvious
code in each disagrees on valid-looking input.

## Solution

Spell the rule out, here as an explicit character class, and put each disagreement in the shared vector file.

## Why This Matters

Obvious code in each language reads the same file differently, so the two readers can reach different verdicts on
one declaration unless a shared vector holds each disagreement.

## Prevention

Three smaller things the close-out taught:

- **A bound on a child isn't a bound on its transport.** `subprocess.run(timeout=...)` kills the direct child and then
  waits on its pipes, which a fetch's transport still holds. Start the command in its own group and kill the group, and
  make the test's hanging stub leave a child behind.
- **Text from a declaration has to fit wherever it is shown.** The reason is free text up to 64 KiB, and the plan's
  validator caps text at 600 characters and refuses control characters. A long or multi-line reason would have stopped
  `plan.json` from rebuilding at all. Clean it once, where the record first takes it.
- **A skip needs its own entry, not a state that reads like a result.** The skip has no `result` member, so nothing
  that reads results can count it. The plan chapter gets its own `closeOut` member rather than reusing `done`.

## Examples

- `str.strip` removes U+0085 and U+001C and `String.trim` doesn't, while `trim` removes U+FEFF and `strip` doesn't. A
  blank check built on either one alone says a different thing from the other reader.
- `json.loads` accepts `NaN`, which `JSON.parse` refuses.
- `json.loads` raises on a very long integer, which `JSON.parse` takes as a number.

## Related

- [Read a merge gate's off switch from the base, and try to fake the base](delivery-posture-harness-read-from-the-base-2026-11-04.md),
  the harness's other reader of the same declaration.

### What a broad run would have caught on the two deliveries that ran without one

The proposal-rails epic and the in-store onboarding readiness epic both ran with no close-out run. Nothing can be shown
either way, because neither ran one: the owner removed it on 2026-10-31 and 2026-11-04. So this is no evidence that `focused` is safe, only that the record has no defect traced to the missing run.

- The proposal-rails lead recorded selected checks on each candidate, then 848 tests across 28 files from a clean checkout of
  the merged base. Its follow-ups came from those runs and from review.
- The onboarding epic is still in progress, and its rehearsal hasn't happened.

The ticket's question, whether `focused` should skip the run outright, was settled by the owner's ruling and not by this
evidence. To answer it from evidence, run `fullHealth` once on one such delivery's final base and compare.
