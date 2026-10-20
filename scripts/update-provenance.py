#!/usr/bin/env python3
"""Recompute the output digests in provenance.lock.json from the files on disk.

Only the ``sha256`` of each listed output changes. Entries, their outputs and
every other field stay as they are, and the lock is written in the corpus's
canonical JSON, so a tree whose lock is current is left byte for byte and a
current lock in any other layout is restored to canonical form. Adding or
removing an output remains a deliberate edit to the lock: a listed output that
is missing, unsafe or outside the corpus is refused and nothing is written.

A rebase that conflicts only in the lock is resolved by taking either side and
running this writer.

Exit codes: 0 when the lock is current or was rewritten; 1 when ``--check``
finds it stale; 2 for a refusal, such as a malformed lock or a missing output.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import os
import tempfile
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
LOCK = "provenance.lock.json"
CATALOG = "catalog.json"


class WriterError(Exception):
    """A refusal; nothing was written."""


def _modules(root: Path):
    sys.path.insert(0, str(root))
    try:
        from agent_skills.validate import canonical_json, safe_relative_path, sha256_bytes
    finally:
        sys.path.remove(str(root))
    return canonical_json, safe_relative_path, sha256_bytes


def entry_for_skill(root: Path, skill: str) -> str:
    """The provenance entry id the catalog names for ``skill``."""
    catalog = json.loads((root / CATALOG).read_text(encoding="utf-8"))
    for item in catalog["skills"]:
        if item["id"] == skill:
            return item["provenanceId"]
    raise WriterError(f"the catalog has no skill {skill}")


def recompute(root: Path, skill: str | None = None) -> tuple[dict, list[str], bool]:
    """The lock with current digests, the outputs whose digest changed, and
    whether the lock on disk differs from its canonical form."""
    canonical_json, safe_relative_path, sha256_bytes = _modules(root)
    raw = (root / LOCK).read_bytes()
    lock = json.loads(raw.decode("utf-8"))
    selected = None if skill is None else entry_for_skill(root, skill)
    if selected is not None and not any(entry["id"] == selected for entry in lock["entries"]):
        raise WriterError(f"the lock has no entry {selected} for skill {skill}")
    changed = []
    refused = []
    for entry in lock["entries"]:
        if selected is not None and entry["id"] != selected:
            continue
        for output in entry["outputs"]:
            relative = output["path"]
            path = root / relative if safe_relative_path(relative) else None
            # Never read outside the corpus, through ".." or a symlink.
            if path is None or not path.resolve().is_relative_to(root) or not path.is_file():
                refused.append(relative)
                continue
            current = sha256_bytes(path.read_bytes())
            if output["sha256"] != current:
                output["sha256"] = current
                changed.append(relative)
    if refused:
        raise WriterError("listed outputs are missing, unsafe or outside the corpus: " + ", ".join(map(str, refused)))
    # Compared as the validator reads it: text with newlines normalized, so a
    # checkout that converts line endings is not reported as noncanonical.
    noncanonical = canonical_json(lock) != raw.decode("utf-8").replace("\r\n", "\n")
    return lock, changed, noncanonical


def write_atomically(path: Path, contents: bytes) -> None:
    """Replace ``path`` in one step, so an interrupted write leaves the old lock."""
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(contents)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Exit 0: current or rewritten. 1: --check found it stale. 2: refused; nothing was written.",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the corpus root (default: this checkout)")
    parser.add_argument("--skill", help="recompute only this catalog skill's provenance entry")
    parser.add_argument("--check", action="store_true", help="report stale digests and exit 1, writing nothing")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        lock, changed, noncanonical = recompute(root, args.skill)
    except WriterError as error:
        print(f"update-provenance: {error}", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError, ImportError) as error:
        # A malformed or unreadable lock is a refusal, never "stale".
        print(f"update-provenance: cannot read the corpus at {root}: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    if args.check:
        for path in changed:
            print(f"stale: {path}")
        if noncanonical:
            print(f"noncanonical: {LOCK}")
        return 1 if changed or noncanonical else 0
    if changed or noncanonical:
        canonical_json, _, _ = _modules(root)
        write_atomically(root / LOCK, canonical_json(lock).encode("utf-8"))
    for path in changed:
        print(f"updated: {path}")
    if noncanonical and not changed:
        print(f"reformatted: {LOCK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
