---
title: "A test helper named `fail` swallows every assertion in its class"
date: 2026-11-09
category: test-failures
module: tests
problem_type: test_failure
severity: high
tags: [unittest, mutation, fixtures]
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

## Solution

Never give a `TestCase` helper the name of a `TestCase` method. Here it became
`inject`. The mistake was found by running mutants, not by reading the rows: a mutant
that should have failed many rows failed none. The check that catches it is to run at
least one mutant per new test class before trusting the class's green.

## Why This Matters

A class that can't fail looks just like a class that passes. The rows read correctly,
ran, and covered the right calls; only a red proof shows the difference.
