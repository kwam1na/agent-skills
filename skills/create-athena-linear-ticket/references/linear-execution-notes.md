# Linear Execution Notes

## Connectivity Check
- Run: `codex mcp list`
- Confirm `linear` is enabled and target team/project are available.

## Planning Source
- If available, run `superpowers:writing-plans` first to generate the task checklist.
- If unavailable, use `atomic-plan-template.md` to produce the checklist before ticket creation.

## Mutation Fallback
If direct MCP mutation fails in-session, use:
`codex exec --dangerously-bypass-approvals-and-sandbox "<instruction to create/update the Linear issue>"`

## Label Handling
- Apply package labels based on impacted package(s).
- Prefer exact requested labels first.
- If only equivalent workspace labels exist, use them and report the mapping.

## Return Payload
Always return:
- Issue key
- Issue URL
- Title
- Labels applied
- Dependency status (`can start now` / `blocked by`)
- Assumptions made during creation
