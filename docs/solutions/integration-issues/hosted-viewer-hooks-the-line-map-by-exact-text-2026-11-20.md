---
title: The hosted viewer hooks the line map by exact text
date: 2026-11-20
category: integration-issues
module: delivery-views
problem_type: integration_issue
severity: medium
tags: [hosted, line-map, build-assets, merge-queue, checks]
symptoms: "A line-map change passed every check its lane ran, then failed hosted.typecheck, hosted.build and hosted.test in the merge queue with hosted.asset_hook_drift"
root_cause: "hosted/delivery-viewer/scripts/build-assets.mjs patches skills/delivery-views/assets/line-map.js by exact line text, and the change renamed one hooked line"
resolution_type: code_fix
---

# The hosted viewer hooks the line map by exact text

## Problem

The hosted delivery viewer reuses the corpus's `line-map.js`. Its build adapts the file with
`hosted/delivery-viewer/scripts/build-assets.mjs`, where each `patch(before, after)` needs its `before` text to appear in
the file exactly once, or the build throws `hosted.asset_hook_drift`. A page change that only renames or reshapes a
hooked line therefore breaks the hosted build. The page lane and the render tests cannot see it, because they read the
corpus's file and never run the build.

## Symptoms

- In V26-3670, V26-3668 changed `function refreshRuns() {` to `function refreshRuns(wait) {`. Its lane ran the page
  lane, the render and history tests, and the corpus pins, and all of them passed.
- In the merge queue, `hosted.typecheck`, `hosted.build` and `hosted.test` all failed at the merged tree with
  `Error: hosted.asset_hook_drift` from `build-assets.mjs`, and the queue stopped the candidate.

## What Didn't Work

The lane's chosen checks, and the lead's first selection at acceptance, covered every module that reads `line-map.js`,
but none that builds the hosted page. The failure surfaced only because the queue added the hosted checks itself.

## Solution

Update the hook's `before` text in `build-assets.mjs` to the new line, keeping its `after` doing the same thing. Then
run the three hosted checks (`hosted.typecheck`, `hosted.build`, `hosted.test`) before review. In V26-3670 the hooked
script was granted to the lane changing `line-map.js`, and V26-3686 and V26-3691 then kept the hooks matching and named
the hosted checks in their hand-offs.

## Why This Works

The patch guard fails loudly on a missing hook rather than building a page that silently skips the hosted adaptation,
such as reading live runs it cannot reach. Matching the hook to the new text keeps that adaptation, and running the
hosted checks before review catches drift in the lane, not in the queue.

## Prevention

- Any change to `skills/delivery-views/assets/line-map.js` (and any file the build reads) checks every `patch("...")`
  `before` string in `build-assets.mjs` still appears exactly once, and runs the three hosted checks.
- A lead accepting such a change selects `hosted.typecheck`, `hosted.build` and `hosted.test` with it.
- The lane changing the page also needs a claim on `build-assets.mjs`, and on `hosted/delivery-viewer/test/viewer.test.ts`
  when the page's markup changes what that test clicks.

## Related Issues

- `docs/solutions/integration-issues/preserve-delivery-history-when-updating-an-integration-base-2026-11-13.md`
