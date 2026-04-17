#!/usr/bin/env bash
set -euo pipefail

mode="symlink"
target_root="${CODEX_HOME:-$HOME/.codex}/skills"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skills_dir="$repo_dir/skills"
timestamp="$(date +%Y%m%d%H%M%S)"

usage() {
  cat <<'EOF'
Usage: ./install.sh [--copy] [--target PATH]

Options:
  --copy         Copy skills instead of creating symlinks.
  --target PATH  Override the install root. Default: ${CODEX_HOME:-$HOME/.codex}/skills
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --copy)
      mode="copy"
      shift
      ;;
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

mkdir -p "$target_root"

for source_dir in "$skills_dir"/*; do
  [[ -d "$source_dir" ]] || continue

  name="$(basename "$source_dir")"
  dest="$target_root/$name"

  if [[ -L "$dest" ]] && [[ "$(readlink "$dest")" == "$source_dir" ]]; then
    echo "Already installed: $name"
    continue
  fi

  if [[ -e "$dest" || -L "$dest" ]]; then
    backup="$dest.bak.$timestamp"
    mv "$dest" "$backup"
    echo "Backed up existing $name to $backup"
  fi

  if [[ "$mode" == "copy" ]]; then
    cp -R "$source_dir" "$dest"
    echo "Copied $name to $dest"
  else
    ln -s "$source_dir" "$dest"
    echo "Linked $name to $dest"
  fi
done
