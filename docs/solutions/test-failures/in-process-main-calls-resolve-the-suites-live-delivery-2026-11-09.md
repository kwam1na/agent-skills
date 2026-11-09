---
title: "A test that calls a tool's main() in-process resolves whatever the test process points at, not its case"
date: 2026-11-09
category: test-failures
module: run-delivery
problem_type: test_failure
severity: high
tags: [tests, in-process, working-directory, globals, audit-hooks, subprocess]
symptoms: "supervisor: running (pid N) status lines for a real delivery printed after unittest's OK, and a ResourceWarning that a subprocess is still running"
root_cause: "main() resolves the repository from the process's working directory and caches the configuration in a module global, so a case that calls it in the test process reaches the checkout the suite runs from and any configuration an earlier call left"
resolution_type: code_fix
---

# A test that calls a tool's main() in-process resolves whatever the test process points at, not its case

## Problem

`RunDeliveryArgumentTests` checks argument parsing by calling `run_delivery.py`'s `main()` in the test process, with
each command function patched out. But `main()` doesn't end with the command. Every `lead-*` command finishes with
`supervisor_line(cfg())`. `cfg()` builds a `Config` from `repo()`, which is the test process's working directory, and
caches it in the module-global `CFG`. Under a lane, that working directory is a story's own checkout of the live
repository. So the configuration was the live delivery's, its record existed and its supervisor held the lock. Each
call then read that lock and printed `supervisor: running (pid ...)` for the real supervisor.

The cache made it worse. Once one call had cached the live configuration, a later call naming another delivery with
`--delivery K` still got the cached one. So a run printed eight lines, not five.

`Config` had a second, quieter version of the same mistake. Given a `root`, it took that root's settings but its main
checkout (`main_checkout()`) and its branch (`git rev-parse HEAD`) from the working directory. So a configuration
built for one repository from inside another mixed the two.

## Symptoms

- After unittest's `OK`, a run printed status lines naming a real supervisor's pid and live view. Stdout written to
  a file is block-buffered, so the lines come out when the interpreter exits, after the summary on stderr.
- The count matched the in-process `lead-*` calls that weren't `lead-setup`, `lead-init` or `lead-run`, plus the ones
  served from the cache.
- Separately, `ResourceWarning: subprocess N is still running` appeared, from a cleanup that sent SIGKILL to a
  `Popen` but never waited for it. The child stays unreaped and the object's `returncode` stays `None`, so
  `Popen.__del__` warns.
- With every ResourceWarning made an error (`PYTHONWARNINGS=error::ResourceWarning`, which the tool's own child
  processes inherit), two product sources showed up as crashes. A lead command that starts the detached supervisor
  dropped its never-waited `Popen`. An in-app `Lease` that gave up on a held lock left its lock file open.

## What Didn't Work

- Stripping the tool's settings from the environment, as the fixtures' cleaned environment does, doesn't help on its own. Resolution starts from the
  working directory, and from a global that no environment variable touches.
- A temporary folder with no repository as the working directory hides the problem without proving anything.
  `repo()` fails before any setting is read, so the settings strip can never be shown red.
- Running the guarded class as an inner `unittest` suite from the guard row backfired. When the inner suite finishes,
  it runs the module's teardown, and with it every `addModuleCleanup`. That removed the release the module's other
  cases share. Its file removals also showed up as relative paths.

## Solution

Each part is proved by its own red row, from a planted live checkout: a main checkout with a linked story worktree,
a delivery record, and a supervisor lock held under every name the tool reads.

- **The case owns its resolution.** `RunDeliveryArgumentTests.setUp` makes a git repository of the case's own that
  holds no delivery, works from it, and strips every setting the tool reads.
- **No call inherits another's configuration.** `main()` sets `CFG = None` before it parses.
- **A configuration is its root's.** `Config` takes `main_checkout(root)` and the branch from `root`.
- **The tool lets go of what it means to.** `start_supervisor` drops the supervisor's handle with `ResourceWarning`
  ignored, because that process is started to outlive the command. A `Lease` that gives up closes its file.
- **A stopped child is reaped.** `stop_and_reap(proc)` stops the process group and waits for it. A scan of both
  modules flags any cleanup lambda that stops a `Popen` it doesn't wait for.

Contact is detected three ways: status lines, a process audit hook, and a file snapshot. The audit hook catches
`open`, file changes, `fcntl.flock` and `subprocess.Popen`, resolving a command run without a `cwd` against where the
process is. Because a lock probe changes neither content nor mtime, a snapshot alone would miss it. `shutil.rmtree`
is counted once, by the folder it removes. The guard row runs each test under its class's own setup, never as a
suite.

## Why This Works

A call can only reach a delivery through what it resolves: the working directory, the environment, and the module's
globals. The case now sets all three, and the tool no longer mixes the working directory into a configuration built
for another root. Each guard has a red row with only that guard removed, so none hides behind another. The one step
that isn't a guard, the case's own repository, is what makes the settings strip testable at all.

## Prevention

- A test that calls a CLI's `main()` in-process must set its working directory, environment and the module's globals
  to its own case, because `main()` reads all three.
- An entry point that can be called twice in one process clears what the previous call cached.
- A function that takes a root takes everything from that root, git included.
- Watch for contact with audit events, not mtimes: a read or a lock probe changes nothing a snapshot sees.
- Running tests as a suite inside a test also runs the module's cleanups. Run each test under its class's setup
  instead.
- A cleanup that kills a `Popen` also waits for it. A process started to outlive its starter has its handle dropped
  deliberately, not left to the garbage collector.
- Run a suite once with `PYTHONWARNINGS=error::ResourceWarning`. It reaches the processes the tests start, so leaks in
  the tool under test show up too.

## Related Issues

- The eight stray `supervisor:` lines and the `ResourceWarning` in another lane's run of the two run-delivery
  modules, on 2026-11-09.
- The fixtures' cleaned environment, and the guard that runs the lead commands from a planted live checkout
  (`LiveDeliveryOutOfReachTests`).
- `docs/solutions/test-failures/detached-fixture-supervisors-outlive-interrupted-workers-2026-11-09.md`: stopping
  what a case started.
