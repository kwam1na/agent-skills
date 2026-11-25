---
title: "cut-release fails on a machine whose ledger records an override"
date: 2026-11-25
category: test-failures
module: release
problem_type: test_failure
severity: high
tags: [cut-release, qualification, machine-ledger, isolation, pinned-views]
symptoms: "cut-release fails with 'core exited 1: orchestration-workflow: sensor failed: test_orchestration_render.PinnedViewTests.test_the_built_in_views_of_a_fixed_state_are_byte_for_byte_unchanged' on a clean tree, while the suite passes"
root_cause: "scripts/qualify-core.py loads sensor modules by bare name, so tests/__init__.py never runs and AGENT_SKILLS_MACHINE_DIR is never pointed at a fresh folder; the pinned views rendered against the real ledger, whose overrides add a Machine section"
resolution_type: code_fix
---

# cut-release fails on a machine whose ledger records an override

## Problem

`tests/__init__.py` gives every test process its own machine ledger by setting `AGENT_SKILLS_MACHINE_DIR` to a fresh
folder. The core qualifier runs its scenarios' sensors in a child process, loading each by bare name
(`test_orchestration_render`) from the candidate's `tests` folder, so the package's `__init__` never runs and the
sensors read the ledger of whoever ran the cut.

## Symptoms

`scripts/cut-release.py` fails on a clean tree with `core exited 1: orchestration-workflow: sensor failed:
test_orchestration_render.PinnedViewTests.test_the_built_in_views_of_a_fixed_state_are_byte_for_byte_unchanged`, while
the full suite passes. It starts once the machine's ledger records an override, such as a run with `--now --why`: the
built-in views gain a Machine section and the pinned bytes no longer match. With `AGENT_SKILLS_MACHINE_DIR` set to a
fresh folder, the same cut passes.

## What Didn't Work

Reading main's red CI `cut-release` as the same failure. From 2026-11-22 the cut step passed on CI's fresh runner. The
next step failed because it still expected 14 files in the release folder, though each release had carried the
hosted delivery service bundle and its `.sha256` since then, making 16.

## Solution

`_candidate_scenario` in `scripts/qualify-core.py` gives each scenario's child process an empty ledger inside its own
scenario folder, removed with the qualification's temporary folder. `tests/test_qualification.py` checks that each
scenario gets a fresh ledger of its own, and that a full qualification passes against a ledger recording an override
and leaves it byte for byte unchanged. CI's release-folder check now expects 16 files.

## Why This Works

The ledger the views read is the one `AGENT_SKILLS_MACHINE_DIR` names. Setting it in the environment the qualifier
hands each scenario covers every sensor and every process a sensor starts, however the sensor's module was loaded.

## Prevention

The harness that runs a test must isolate any machine-wide state the test reads. A package `__init__` covers only the
runs that import the package. A qualifier, a runner or anything else that loads test modules another way has to set
up that isolation itself.

## Related Issues

`tests-that-read-the-hosts-load-2026-11-18.md`: the same kind of leak, through the host's load instead of its ledger.
