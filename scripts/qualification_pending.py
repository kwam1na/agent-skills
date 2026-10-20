"""Read the qualification-pending marker.

While a change that alters a qualified profile is assembled on an integration
branch, the marker names the qualification sets whose committed records are
stale. Their byte-comparison tests skip with the marker's reason, the release
CLI refuses to cut a release, and registry validation refuses the marker for
any change that targets the default branch. Deleting the marker, with every
named set re-earned, is what lets the branch merge.
"""

from __future__ import annotations

import json
from pathlib import Path


MARKER = "qualification-pending.json"
SCHEMA_VERSION = "qualification-pending/1"
SETS = ("bootstrap", "core", "linear", "provider")
DEFAULT_BRANCH = "main"
REF_PREFIXES = ("refs/heads/", "refs/remotes/origin/", "origin/")


class MarkerError(ValueError):
    pass


def load_marker(root: Path) -> dict | None:
    """The validated marker, or None when the corpus carries none."""
    path = Path(root) / MARKER
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        marker = json.loads(text)
    except json.JSONDecodeError as error:
        raise MarkerError(f"{MARKER} is not JSON: {error}") from None
    if not isinstance(marker, dict) or set(marker) != {"schemaVersion", "reason", "sets"}:
        raise MarkerError(f"{MARKER} must hold exactly schemaVersion, reason and sets")
    if marker["schemaVersion"] != SCHEMA_VERSION:
        raise MarkerError(f"{MARKER} must declare {SCHEMA_VERSION}")
    if not isinstance(marker["reason"], str) or not marker["reason"].strip():
        raise MarkerError(f"{MARKER} must give a reason")
    sets = marker["sets"]
    if (
        not isinstance(sets, list)
        or not sets
        or any(name not in SETS for name in sets)
        or sets != sorted(set(sets))
    ):
        raise MarkerError(f"{MARKER} sets must be a sorted, unique, non-empty list drawn from {', '.join(SETS)}")
    if text != json.dumps(marker, indent=2, ensure_ascii=False, sort_keys=True) + "\n":
        raise MarkerError(f"{MARKER} must be canonical two-space JSON")
    return marker


def pending_reason(root: Path, set_id: str) -> str | None:
    """Why ``set_id``'s committed records are stale, or None when they are current."""
    if set_id not in SETS:
        raise MarkerError(f"unknown qualification set: {set_id}")
    marker = load_marker(root)
    if marker is None or set_id not in marker["sets"]:
        return None
    return marker["reason"]


def is_default_branch(ref: str, default: str = DEFAULT_BRANCH) -> bool:
    name = ref.strip()
    for prefix in REF_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name == default
