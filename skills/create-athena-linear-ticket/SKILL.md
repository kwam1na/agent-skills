---
name: create-athena-linear-ticket
description: Use when you need Athena-specific defaults while creating Linear tickets for Athena work.
---

# Create Athena Linear Ticket

Use `$create-linear-ticket` as the generic core workflow. This wrapper keeps Athena defaults and Athena label guidance on top of that cross-project flow.

If the tickets already exist and the user wants implementation instead of ticket creation, do not keep using this skill for execution. Hand off to `$execute-athena-linear-ticket`.

## When to Use

- The user asks to create Athena tickets from current work.
- The user wants planning first, then Athena ticket creation.
- The user wants Athena defaults applied when project context was omitted.

Do not use this skill when:
- the relevant tickets already exist
- the task is for a non-Athena project that already has clear project context
- the user is asking to implement or continue implementation

## Athena Defaults

- Apply Athena defaults only when the user did not provide project context.
- Team: `V26` (`yaegars`)
- Project: `athena`
- Common package labels (apply only when relevant):
  - `pkg:athena-webapp`
  - `pkg:storefront-webapp`

## Wrapper Behavior

1. Start with `$create-linear-ticket`.
- Follow the generic planning-first, atomic-ticket workflow there.
- Reuse the generic project resolution contract before mutating Linear.

2. Apply Athena defaults only when the generic resolver does not already have context.
- If the prompt already includes a Linear issue id, explicit project name, or explicit team name, let the generic resolver validate and use that context.
- Only inject `V26` / `athena` when the generic resolver would otherwise have to stop and ask for project context.

3. Respect non-Athena context when it is explicit.
- If the supplied or resolved context points to a different project, stop using this wrapper and continue with `$create-linear-ticket` instead of overriding the project.

4. Keep Athena-specific labels optional.
- Apply Athena package labels only when the touched code area actually matches them.
- Do not force Athena labels onto tickets for unrelated scopes.

5. Hand off to the Athena execution wrapper when implementation is next.
- If implementation should follow immediately, say: `Use $execute-athena-linear-ticket for implementation.`

## Quick Reference

- Athena ticket creation with missing project context: use this wrapper
- Non-Athena ticket creation or already-resolved context: use `$create-linear-ticket`
- Athena defaults are fallback context only, not overrides
- Athena package labels stay optional and scope-dependent
