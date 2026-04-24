# Custom Agent Skills

This repository syncs a focused set of custom local Codex skills from `/Users/kwamina/.codex/skills`.

## Included Skills

- `bootstrap-agent-harness`
- `compound-delivery-kernel`
- `create-linear-ticket`
- `execute-linear-ticket`
- `deliver-work`

## Syncing

Run the bundled sync script from the repository root:

```bash
./scripts/sync-local-skills.sh
```

You can also pass a different local skills root:

```bash
./scripts/sync-local-skills.sh /path/to/skills
```
