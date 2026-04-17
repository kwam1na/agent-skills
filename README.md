# Custom Agent Skills

Custom skills bundled for reuse across machines and agent clients.

## Included Skills

- `athena-ticket-retrospective`
- `bootstrap-agent-harness`
- `create-athena-linear-ticket`
- `create-linear-ticket`
- `designing-frontends`
- `execute-athena-linear-ticket`
- `execute-linear-ticket`
- `frontend-skill`
- `playwright`

## Usage

Clone this repo anywhere and wire the folders in `skills/` into your client's normal skill discovery path.

This repository intentionally does not ship client-specific install or uninstall helpers.

## Notes

- System skills and runtime-provided skills are intentionally excluded.
- Some individual skills still assume the presence of specific tools, plugins, or agent capabilities.
- This pass keeps the repository packaging neutral without rewriting every skill to a fully agent-agnostic format.
