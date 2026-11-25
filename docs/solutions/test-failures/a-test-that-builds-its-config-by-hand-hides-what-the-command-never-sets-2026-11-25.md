---
title: A test that builds its config by hand hides what the command never sets
date: 2026-11-25
category: test-failures
module: run-delivery
problem_type: test_failure
severity: high
tags: [config, unit-tests, cli, collab, handover, end-to-end]
symptoms: "`collab give --scope delivery --to SEAT` crashed on a real command line with AttributeError: 'Config' object has no attribute 'flow_log', while every unit test of the delivery handover passed"
root_cause: "The handover's unit tests passed a namespace standing in for the config, with flow_log and delivery set, but the command built its Config with need_delivery=False, which sets neither, so only the real command path read attributes that were never there"
resolution_type: code_fix
---

# A test that builds its config by hand hides what the command never sets

## Problem

`run_delivery.py` builds a `Config` differently for each kind of command. A lead command loads its delivery, so
`c.delivery`, `c.flow_log` and the lead files are set. The `collab` commands used `Config(repo(), need_delivery=False,
pick=False)`, because they find their record by key rather than by the binding's delivery, and that constructor leaves
the delivery's attributes unset.

The delivery handover (`collab give --scope delivery`) parks the publishers, which writes the friction stub through
`lead_file(c, "friction.md")`, and that reads `c.flow_log`. The handover's tests called the handover functions
directly with a `SimpleNamespace` holding every attribute the code reads. They passed, and the command crashed the
first time a person, or here an end-to-end test, ran it.

## Symptoms

- On a real command line: `give_delivery -> park -> friction_stub_path(c) -> lead_file(c, "friction.md")`, then
  `AttributeError: 'Config' object has no attribute 'flow_log'`.
- The story's unit tests and its review rounds were green. The two-machine end-to-end class, which drives only
  user-facing commands, was the first to reach the real path.
- Once the crash was fixed, the new command-line test at first checked only the key names of what the handover
  carries. Dropping the fix left it green, because the friction stub was looked up under the wrong key and came back
  empty rather than failing.

## What Didn't Work

- Adding the missing attribute to the test's namespace. That makes the stand-in match the code, and teaches nothing
  about what the real constructor sets.
- Asserting the shape of the carried data (its keys) after a real command. A wrong lookup that returns an empty
  value still has the right keys.

## Solution

- `collab_config` now loads the shared record's own delivery, or planning record, and sets `c.state` and `c.flow_log`
  from that record's folder, so every reader on the collab path sees what a lead command would.
- Every `lead_file` and `c.flow_log` read reachable from the collab commands was traced, and one more was fixed the
  same way.
- A test runs `collab give --scope delivery` as a subprocess on a shared record. It first writes a's friction stub
  and evidence notes, then asserts their values, not just their keys, arrive on b. Dropping the fix makes it fail.

## Why This Works

A command-line test goes through the same `Config` constructor the command uses, so an attribute the constructor
never sets fails the test as it fails the person. Asserting carried values rather than names makes a wrong lookup
visible, because it returns the wrong value instead of the wrong shape.

## Prevention

- When a command builds its config with a narrower constructor than the lead commands, give each code path it
  reaches at least one test that runs the command itself, not only its functions with a stand-in config.
- A test that builds a config by hand should say which constructor it stands in for. Prefer building the real one
  in the fixture where it is cheap.
- For anything a command carries or copies, assert the values that arrive, seeded with distinct content, so a
  lookup under the wrong key fails.
- An end-to-end class that drives only user-facing commands is worth its cost: it found this crash after every
  unit test and two review rounds had passed.

## Related Issues

- [A new Config attribute breaks the namespaces that stand in for Config](a-new-config-attribute-breaks-the-namespaces-that-stand-in-for-config-2026-11-25.md):
  the same mismatch the other way round. There, the stand-in lacked a field every real `Config` has, so a correct
  change failed the tests. Here, the real constructor lacked a field the stand-in had, so a broken command passed
  them. Both come from a namespace that copies some of the fields `Config` sets instead of being the real thing.
- [A recipe test that normalizes its fixture hides the real input's drift](a-recipe-test-that-normalizes-its-fixture-hides-the-real-inputs-drift-2026-11-23.md):
  the same family, a test whose input is cleaner than what the code meets in use.
