---
title: "A neutral capability keeps its first provider's shape out"
date: 2026-11-16
category: architecture-patterns
module: deliver-work
problem_type: architecture_pattern
severity: medium
tags: [core-boundary, capability-contract, adapters, absence-pins, core-leakage]
applies_when: "Adding a core capability while its first adapter is designed in the same delivery"
---

# A neutral capability keeps its first provider's shape out

## Problem

When a core capability and its first adapter are designed together, the contract
tends to take on the provider's vocabulary. The knowledge-library capability was
designed against hive-mined, whose items carry `item_id`, `type` and `project`, and
whose library keeps `projects.txt`, `tags.txt` and a `how-to` type. Every one of
those names reads naturally in a contract. Once one lands in core, the second
provider has to adopt the first one's model, and the capability is no longer neutral.

The core boundary check, `_validate_core_leakage` in `agent_skills/validate.py`,
forbids provider *names* in core outputs. It knows nothing about a provider's field,
type or list names, so a contract that says `item_id` passes it.

## Solution

Use three guards together:

1. **Name the provider in the boundary pattern.** The provider-boundary pattern in
   `agent_skills/validate.py` gains the provider's names as whole words (`hive`,
   `hive-mined`, `qmd`). Naming the provider in a core output is then a
   `core.leakage` finding that names the file. Whole-word matching keeps `archive`,
   `beehives` and `qmdx` clean, and `tests/test_core_boundary.py` pins both sides.
2. **Pin the provider's shape absent and the neutral shape present.**
   `tests/test_knowledge_library.py` pins `item_id`, `projects.txt`, `tags.txt` and
   `how-to` absent from the capability contract and from every skill that consults
   the capability, paired with positive pins on the neutral names `itemRef`, `kind`
   and `scope`. The positive pin is what stops a rewording from passing by deleting
   the field altogether.
3. **Put the mapping in the adapter, and only there.** `hive-knowledge-adapter`
   says `itemRef` is `item_id`, `kind` is `type`, and a hit's `scope` is its
   `project`. It ships in the `run-delivery` closure, outside core, so the scan
   never reads it.

Prove each pin red by planting the provider's field in a core file with that file's
digest refreshed, so the release build succeeds and the pin's own assertion fails.

## Why This Matters

A capability's first provider is the only one anybody has seen, so its shape looks
like the natural shape. Neutrality has to be checked by a sensor: review alone
won't catch `item_id` in a sentence that reads well. The name check and the shape
pins guard different things. The validator stops core from saying *who* the provider
is, and the pins stop core from adopting *what* the provider's data looks like.

## Prevention

- When a delivery adds a capability and its adapter together, list the provider's
  field, type, file and list names in the plan, and pin each one absent from core.
- Pair every absence pin with a positive pin on the neutral name it replaces.
- Add the provider's names to the boundary pattern in the same change that adds the
  adapter, with a whole-word test that keeps ordinary words clean.
- Keep optional request fields provider-free too. A request `scope` that the
  adapter maps to the provider's `project` is neutral, while a request field named
  `project` would not be.

## Examples

The knowledge-library plan made it a binding decision, "the neutral contract
carries no provider shape", and listed the shape literals to pin before any story
started. The first story landed the guards, then three lanes and four more merges
edited the contract and the consulting skills without a leak. The pin's own red proof
plants `item_id` in the capability contract with its digest refreshed. The
boundary test plants "Find an earlier lesson with hive-mined search first." in a
core skill.

## Related

`docs/plans/2026-11-16-knowledge-library-read-side.md`, decisions 2 and 3, sets the
boundary. `skills/deliver-work/references/capability-contract.md` holds the
Knowledge Library Operations. `agent-rule-and-machine-path-are-separate-2026-11-09.md`
is a related boundary note from another angle.
