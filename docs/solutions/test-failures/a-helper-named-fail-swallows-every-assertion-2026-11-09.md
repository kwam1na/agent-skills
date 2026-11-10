---
title: "A test helper named `fail` swallows every assertion in its class"
date: 2026-11-09
category: test-failures
module: tests
problem_type: test_failure
severity: high
tags: [unittest, mutation, fixtures]
symptoms: "A new TestCase class stays green under a mutant that should fail many of its rows, and each failing assertion instead plants another failure rule in the stub"
root_cause: "A helper named fail overrode TestCase.fail, which assertEqual, assertIn, assertTrue and the rest call when they fail"
resolution_type: code_fix
applies_when: "A unittest.TestCase subclass defines a helper method whose name a TestCase method already has"
---

# A test helper named `fail` swallows every assertion in its class

## Problem

The Linear adapter's contract rows had a helper, `fail(op, status, code, ...)`, that
planted a failure in the stub tracker. It was named the way `Delivery.fail` is named
in the same module. On a `unittest.TestCase`, though, that name overrides
`TestCase.fail`. `assertEqual`, `assertIn`, `assertTrue` and the rest call
`self.fail(message)` when they fail. So every failing assertion in the class called the
helper instead, which just appended a failure rule to the stub, and the test passed.
The rows went green with the behaviour they pinned mutated away.

## Symptoms

- A mutant that maps `FORBIDDEN` to 400, or that reads an `errors` answer as success,
  leaves `LinearAdapterContractTests` and `LinearPublisherTests` green, although many
  of their rows assert exactly those statuses.
- The suite reports no failures and no errors. Only the probe's verdict, `SURVIVED`,
  shows anything is wrong.
- After a failing assertion, the stub's `failures` list holds one more rule than the
  row planted.

## What Didn't Work

Reading the rows didn't show it. They named the right calls and asserted the right
values, and each passed when run alone. A full green run of the module showed nothing
either, because a class that can't fail reports exactly as one that passes.

## Solution

Never give a `TestCase` helper the name of a `TestCase` method. Here it became
`inject`, in both classes. Then rerun the probes: the five that had survived turned
red, one of them once it was aimed at the row that pins its rule.

## Why This Works

`unittest`'s assertion methods report a failure through `self.fail`, which raises
`self.failureException`. With that name back on `TestCase`, a failing assertion raises
again instead of calling the stub helper, and the row fails as it should.

## Prevention

- Run at least one mutant per new test class before trusting that class's green.
  The check that caught this was a mutant expected to fail many rows that failed none.
- Name stub helpers for what they do to the stub (`inject`, `plant`), never with a
  verb `unittest.TestCase` already defines (`fail`, `skip`, `run`, `debug`, `subTest`).

## Related Issues

- [Keep an agent-facing transport rule apart from a machine-facing write path](../architecture-patterns/agent-rule-and-machine-path-are-separate-2026-11-09.md),
  the delivery whose rows this hid.
- [Prove each condition of a gate on its own, with the gate still shut](../best-practices/prove-each-gate-condition-alone-2026-11-03.md),
  on red proofs as evidence a row can fail.
