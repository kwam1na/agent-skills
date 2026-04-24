#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${1:-$HOME/.codex/skills}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="$REPO_ROOT/skills"

skills=(
  bootstrap-agent-harness
  compound-delivery-kernel
  create-linear-ticket
  execute-linear-ticket
  deliver-work
)

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required" >&2
  exit 1
fi

mkdir -p "$SKILLS_DIR"
find "$SKILLS_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

for skill in "${skills[@]}"; do
  src="$SOURCE_ROOT/$skill/"
  dst="$SKILLS_DIR/$skill/"

  if [[ ! -d "$src" ]]; then
    echo "Missing local skill directory: $src" >&2
    exit 1
  fi

  mkdir -p "$dst"
  rsync -a --delete --delete-excluded \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    "$src" "$dst"
done

echo "Synced ${#skills[@]} custom skills from $SOURCE_ROOT"
