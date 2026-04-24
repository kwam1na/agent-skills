# Linear Project Resolution Contract

Use this before any Linear mutation when team or project context is missing, implicit, or ambiguous.

1. If a Linear issue id is present, call `get_issue` first and derive the team/project from the issue.
2. Otherwise, validate any explicit team/project names from the prompt with `get_team` and `get_project`.
3. If neither source exists, inspect the current working directory:
   - current directory basename
   - git repo root basename when it differs
   - obvious package or workspace metadata such as `package.json` names
4. Validate cwd-derived candidates in Linear before asking the user.
5. If exactly one confident project match exists, use it automatically and report that it was cwd-derived.
6. Stop and ask the user for project context when:
   - zero plausible matches remain
   - multiple plausible matches remain
   - the issue exists but has no project
   - explicit team/project names conflict with the issue
7. If the named or derived project does not exist in Linear, stop and surface the mismatch clearly.

Treat the resolved team/project as required context for later Linear operations and report it in the final handoff.
