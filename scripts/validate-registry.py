#!/usr/bin/env python3
"""Compatibility entry point for the authoritative offline validator.

``--target-base <ref>`` names the branch a change will merge into. When that is
the default branch, a qualification-pending marker is refused: the default
branch never carries skipped qualification. Without the argument, validation
stays tree-only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agent_skills.validate import canonical_json, main
from qualification_pending import MarkerError, is_default_branch, load_marker


def run() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--target-base")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    known, _ = parser.parse_known_args()
    sys.argv = [sys.argv[0], *[
        argument for index, argument in enumerate(sys.argv[1:])
        if not _is_target_base(sys.argv[1:], index)
    ]]
    try:
        marker = load_marker(known.root)
    except MarkerError as error:
        print(canonical_json({"error": str(error), "ok": False}), end="")
        return 1
    if marker is not None and known.target_base is not None and is_default_branch(known.target_base):
        print(
            canonical_json({
                "error": "qualification is pending for a change that targets the default branch; "
                "re-earn the named sets and delete the marker first",
                "ok": False,
                "qualificationPending": marker["sets"],
                "targetBase": known.target_base,
            }),
            end="",
        )
        return 1
    return main()


def _is_target_base(arguments: list[str], index: int) -> bool:
    argument = arguments[index]
    if argument == "--target-base" or argument.startswith("--target-base="):
        return True
    return index > 0 and arguments[index - 1] == "--target-base"


if __name__ == "__main__":
    raise SystemExit(run())
