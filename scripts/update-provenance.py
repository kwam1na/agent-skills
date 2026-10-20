#!/usr/bin/env python3
"""Recompute the output digests in provenance.lock.json from the files on disk.

Only the ``sha256`` of each listed output changes. Entries, their outputs and
every other field stay as they are, and the lock is written in the corpus's
canonical JSON, so a tree whose digests are current is left byte for byte.
Adding or removing an output remains a deliberate edit to the lock: a listed
output that is missing is refused and nothing is written.

A rebase that conflicts only in the lock is resolved by taking either side and
running this writer.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
LOCK = "provenance.lock.json"
CATALOG = "catalog.json"


class WriterError(Exception):
    """A refusal; nothing was written."""


def _modules(root: Path):
    sys.path.insert(0, str(root))
    try:
        from agent_skills.validate import canonical_json, sha256_bytes
    finally:
        sys.path.remove(str(root))
    return canonical_json, sha256_bytes


def entry_for_skill(root: Path, skill: str) -> str:
    """The provenance entry id the catalog names for ``skill``."""
    catalog = json.loads((root / CATALOG).read_text(encoding="utf-8"))
    for item in catalog["skills"]:
        if item["id"] == skill:
            return item["provenanceId"]
    raise WriterError(f"the catalog has no skill {skill}")


def recompute(root: Path, skill: str | None = None) -> tuple[dict, list[str]]:
    """The lock with current digests, and the output paths whose digest changed."""
    _, sha256_bytes = _modules(root)
    lock = json.loads((root / LOCK).read_text(encoding="utf-8"))
    selected = None if skill is None else entry_for_skill(root, skill)
    if selected is not None and not any(entry["id"] == selected for entry in lock["entries"]):
        raise WriterError(f"the lock has no entry {selected} for skill {skill}")
    changed = []
    missing = []
    for entry in lock["entries"]:
        if selected is not None and entry["id"] != selected:
            continue
        for output in entry["outputs"]:
            path = root / output["path"]
            if not path.is_file():
                missing.append(output["path"])
                continue
            current = sha256_bytes(path.read_bytes())
            if output["sha256"] != current:
                output["sha256"] = current
                changed.append(output["path"])
    if missing:
        raise WriterError("listed outputs are missing: " + ", ".join(missing))
    return lock, changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the corpus root (default: this checkout)")
    parser.add_argument("--skill", help="recompute only this catalog skill's provenance entry")
    parser.add_argument("--check", action="store_true", help="report stale digests and exit 1, writing nothing")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        lock, changed = recompute(root, args.skill)
    except WriterError as error:
        print(f"update-provenance: {error}", file=sys.stderr)
        return 2
    if args.check:
        for path in changed:
            print(f"stale: {path}")
        return 1 if changed else 0
    if changed:
        canonical_json, _ = _modules(root)
        (root / LOCK).write_bytes(canonical_json(lock).encode("utf-8"))
    for path in changed:
        print(f"updated: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
