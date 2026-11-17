---
title: "A suite's time is the processes its tests start, and where they are made to wait"
date: 2026-11-14
last_updated: 2026-11-17
category: best-practices
module: test-suite
problem_type: best_practice
severity: medium
tags: [tests, run-tests, git, macos, subprocess, budget, fixtures]
applies_when: "The full suite gets slower, or a new test class is about to be marked to run alone"
---

# A suite's time is the processes its tests start, and where they are made to wait

## Problem

The full suite took 62 minutes at four workers on a 10-core machine, and gated every
delivery. Nothing in it slept for long. Its time was units made to run alone, and
tens of thousands of short processes that each paid a fixed cost before doing any work.

## Solution

Measure before changing anything: `scripts/run-tests.py --timings` gives each unit's
time, and a start-up hook in the environment's `site-packages` that logs each Python
child's script, parent and time shows who starts what. `GIT_TRACE2_EVENT` does the same
for git. Four causes accounted for the time:

- Units marked `run_serially` took 48 of the 62 minutes. Most kept to their own folders,
  ports and locks, and needed a quiet machine, not an empty one. They are `run_quietly`
  now, and run four at once.
- macOS's `/usr/bin/git` looks the real git up on every call: 13 ms against 4 ms. A
  merge-queue test makes about 380 calls. The runner puts git's own programs first on
  a worker's `PATH`.
- A tool started as a command is compiled from source every time, 30 to 100 ms for a
  script of several thousand lines, since Python keeps no bytecode for the script it
  is asked to run. `run_delivery.py` read the record by starting `delivery_state.py
  show`, often several times a command; it reads through the loaded module now.
- Long units discovered late left workers idle at the end. Units that cannot be split
  start first, and the chunks fill in.

That took the same tests from 62 minutes to 20.

A fifth cause showed once the budget was in place: classes whose every case built the
same delivery by running the whole flow. Such a class builds its fixture once for each
set of settings and keeps a copy. Each later case gets the copy restored into the same
folder, with whatever the earlier case left running there stopped first.
`kept_fixture()` in `tests/test_run_delivery.py` does this. One class went from 482
seconds to 181 run alone, eight more gave back 452 unit seconds between them, and the
suite went from 1,748 seconds to 1,625.

## Why This Matters

The suite is the gate, so its time is every delivery's time. The work then grew back
by a quarter in a day through 371 honest new tests, each a few seconds of fixture and
commands. No single test was slow, so nothing but a total could have caught it.

## Prevention

`tests/suite-budget.json` holds the budget, `scripts/run-tests.py --budget` checks a
whole run against it, and the run before a tag uses it. `tests/test_suite_budget.py`
keeps a limit from being raised and makes every unit that runs alone state its reason.

Count processes when adding a case: a fixture built by running the whole flow costs
seconds, and a second fixture in one case costs them again.

Check the budget's headroom before trusting a timed run. The units' own seconds,
summed and divided by the workers, are the least the run can take; when that sits
within a few percent of the budget, what else the machine is doing decides the result.
Stop previews, tunnels and browser tabs you started before a timed run, by the pids
you recorded.

A class that shares a fixture proves the sharing changed nothing: the same test names
and count, no assertion line changed, the class passing reversed and shuffled, and its
longest cases passing alone. An assertion inside the shared build now runs once per
build, not once per case, so say so in the change.

## Examples

A bytecode cache does not rescue a script run as a command. Pointing
`PYTHONPYCACHEPREFIX` at a folder of precompiled tool modules made each start 22 ms
slower: with a prefix set, Python looks there for the standard library's bytecode
too, finds none, and compiles every module it imports.

Reading the record in-process in the tests' own helpers saved nothing measurable, so
it was taken back out. Many of those reads sit in loops that wait on a supervisor's
pass, where the wait sets the time and not the read.

Eight workers instead of four took the suite from 21.6 to 18.2 minutes, for twice the
load, because the quiet units were still held to four.

A budget without headroom failed on load, not on tests. The units summed to about
6,730 seconds, so four workers needed about 1,680 of a 1,740-second budget. Three runs
on nearly the same code took 1,726, 1,924 and 1,748 seconds; the last two passed every
test and exited 3. A load average of 7 from previews and a tunnel made every unit
about 12% slower. Each over-budget run took half an hour to say so.

A shared fixture does not suit a class whose cases listen on a port committed in the
fixture's own files. A restored copy keeps one port across cases, and a case that
started its own listener there failed. Three such classes kept a fixture per case;
their builds took under three seconds. A case with settings no other case uses keeps
its own fixture too.

## Related

`AGENTS.md` carries the budget's standing rule. The temp-folder side of the suite's
cost is in `measure-test-temp-churn-2026-11-03.md`.
