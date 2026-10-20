---
name: orchestrate-work
description: Deliver a decomposed epic with one lead session coordinating lane sessions, a lead-owned merge queue, and a delivery state document that any view can render.
---

# Orchestrate Work

Deliver a decomposed epic: one lead session plans and assigns tickets to lane
sessions, owns every merge, shared resource and release, and keeps the
delivery's state current. Each lane delivers one ticket at a time through the
existing delivery workflow and never merges.

## Delivery state

The state document and the journal record where the epic stands. The state is
the contract; views are rendered from it and can be replaced without touching
it.

`references/schemas.json` holds the skill's schemas, keyed by name:

- `delivery-state/1` describes the state document.
- `delivery-journal/1` describes one journal entry. The journal is
  append-only.

Write both only through the state tool, `scripts/delivery_state.py`. Run it
with `python3 -B` so no bytecode is written beside the skill:

```bash
python3 -B scripts/delivery_state.py --dir <state directory> init --headline "<headline>"
python3 -B scripts/delivery_state.py --dir <state directory> show
python3 -B scripts/delivery_state.py --dir <state directory> patch --writer lead --version <n> --patch '<json merge patch>'
python3 -B scripts/delivery_state.py --dir <state directory> patch --writer lead --version <n> --element 'queue[subject=<subject>]' --patch '<json merge patch>'
python3 -B scripts/delivery_state.py --dir <state directory> patch --writer runner --version <n> --set '{"/meta/checking/now": null}'
python3 -B scripts/delivery_state.py --dir <state directory> journal --kind <kind> --summary "<what happened>"
```

- A merge patch removes any field it sets to null. To set a nullable field to
  null, use `--set`, which assigns exact values by JSON Pointer (relative to
  the selected element when `--element` is given). `--patch` and `--set` may
  be combined; the merge patch applies first.
- A patch is pinned to the state version it was written against. A stale
  version is refused and nothing is written; read the state again and rewrite
  the patch.
- The declared writer must own every field the patch changes. The runner owns
  `meta.checking`, `meta.baseHead`, `meta.releasedHead`, and each queue
  element's `stage`, `checks` and `syncedHead`. The lead owns everything else.
  The tool maintains `version` and `meta.updatedAt`.
- A queue element the lead adds starts with `stage` set to `queued`, empty
  `checks` and a null `syncedHead`.
- Journal appends are never refused for concurrency; the tool allocates the
  next id.
- `show` prints the validated `{state, journal}` pair that renderers read.
- Output is ASCII JSON on every platform and console; a patch read from
  stdin is UTF-8, with or without a byte-order mark. Text that has no UTF-8
  form, such as a lone surrogate, is refused. The state is UTF-8 and the journal ASCII, both with `\n`
  line ends, so their bytes are the same everywhere.
- `extensions` holds a repository's own fields. It is validated as an object
  only.
