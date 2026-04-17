# Codex Custom Skills

Custom Codex skills bundled for reuse across machines.

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

## Install

Clone the repo anywhere, then run:

```bash
./install.sh
```

By default this creates symlinks in `${CODEX_HOME:-$HOME/.codex}/skills`, so updates are just:

```bash
git pull
```

If you want standalone copies instead of symlinks:

```bash
./install.sh --copy
```

## Uninstall

The uninstall script removes symlinks created by `install.sh`:

```bash
./uninstall.sh
```

## Notes

- System skills and runtime-provided skills are intentionally excluded.
- The installer backs up conflicting skill directories before replacing them.
- These skills may depend on plugins or system skills already available in Codex.
