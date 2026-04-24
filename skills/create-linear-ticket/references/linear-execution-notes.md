# Linear Execution Notes

## Connectivity Check
- Confirm the Linear tools are available and the resolved team/project are accessible before mutating tickets.

## Planning Source
- If available, run `superpowers:writing-plans` first to generate the task checklist.
- If unavailable, use `atomic-plan-template.md` to produce the checklist before ticket creation.

## Mutation Fallback
If direct MCP mutation fails in-session, use:
`codex exec --dangerously-bypass-approvals-and-sandbox "<instruction to create/update the Linear issue>"`

## Label Handling
- Apply explicit user-requested labels first.
- Use existing workspace labels when equivalent labels already exist.
- Report any label mapping choices in the final handoff.
