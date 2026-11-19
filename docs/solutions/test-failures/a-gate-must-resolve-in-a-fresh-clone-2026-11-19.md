---
title: "A gate that names .venv/bin/python breaks the setup check in a fresh clone"
date: 2026-11-19
category: test-failures
module: run-delivery
problem_type: test_failure
severity: medium
tags: [gates, doctor, setup-check, fresh-clone, lead-push]
symptoms: "tests.test_own_deliveries.FreshClone fails twice: doctor reports 'wrong gates: these don't resolve: gate <id> (.venv/bin/python)' on any head that holds the gate"
root_cause: "doctor resolves each gate's program in a fresh clone before any .venv exists, and the lead's push of the gate to the base ran no check"
resolution_type: config_change
---

# A gate that names .venv/bin/python breaks the setup check in a fresh clone

## Problem

To run the queue's standing guards in the lane gate for one delivery, the lead added two gates to
`.agents/run-delivery.json` on the integration base. Their argv began with `.venv/bin/python`, as the catalogue's
checks do. `lead-push` published the change, and both `tests.test_own_deliveries.FreshClone` tests failed on every
head that held it. A lane found the failures during its full suite and traced them to the base.

## Symptoms

- `test_the_setup_check_passes_from_a_fresh_clone` and
  `test_missing_publishing_credential_warns_without_selecting_a_delivery` fail. The message is
  `wrong    gates: these don't resolve: gate guard-linear-qualification (.venv/bin/python), gate guard-registry
  (.venv/bin/python); fix: install them, or correct the binding`.
- Both tests pass at the commit before the gate change.

## What Didn't Work

- Plain `python3` as the program. `scripts/run-tests.py` starts its workers with the interpreter that runs it, so
  outside the venv they lack the test dependencies.

## Solution

Name a program that resolves in a fresh clone, and reach the venv through it:

```json
{"id": "guard-registry", "argv": ["sh", "-c", ".venv/bin/python -B tests/test_registry.py"], "timeoutSeconds": 900}
```

`doctor` resolves `sh`. In a lane's checkout, the binding's `prepare` step builds `.venv` before `gate` runs.

## Why This Works

`doctor`'s gates check resolves each gate's first argv element against `PATH` and the checkout, in whatever tree it
runs in. A fresh clone has no `.venv`, because the prepare step hasn't run. The catalogue's checks run only after
prepare, which is why their `.venv/bin/python` never fails this way.

## Prevention

- After any lead edit to gates, the binding or `prepare`, run `tests.test_own_deliveries` and
  `scripts/validate-registry.py` before `lead-push`. `lead-push` publishes without running a check, and no `fullHealth`
  runs after a lead's own commit.

## Related Issues

- `best-practices/granting-line-spans-in-shared-files-2026-11-19.md`, from the same delivery.
