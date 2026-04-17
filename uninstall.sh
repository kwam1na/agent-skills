#!/usr/bin/env bash
set -euo pipefail

target_root="${CODEX_HOME:-$HOME/.codex}/skills"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skills_dir="$repo_dir/skills"

usage() {
  cat <<'EOF'
Usage: ./uninstall.sh [--target PATH]

Options:
  --target PATH  Override the install root. Default: ${CODEX_HOME:-$HOME/.codex}/skills
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      if [[ $# -lt 2 ]]; then
        usage
        exit 1
      fi
      target_root="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

for source_dir in "$skills_dir"/*; do
  [[ -d "$source_dir" ]] || continue

  name="$(basename "$source_dir")"
  dest="$target_root/$name"

  if [[ -L "$dest" ]] && [[ "$(readlink "$dest")" == "$source_dir" ]]; then
    rm "$dest"
    echo "Removed $dest"
  else
    echo "Skipped $dest"
  fi
done
