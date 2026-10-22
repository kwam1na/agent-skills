"""Skip a qualification set's byte-comparison tests while its records are pending."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load():
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "qualification_pending", ROOT / "scripts/qualification_pending.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


qualification_pending = _load()


def pending(set_id: str) -> str | None:
    """Why ``set_id``'s committed records are stale, or None when they are current."""
    return qualification_pending.pending_reason(ROOT, set_id)


def skip_if_pending(case: unittest.TestCase, *set_ids: str) -> None:
    """Report ``case`` as skipped when any named set's committed records are stale.

    Call it after every assertion that does not compare against committed
    records, so the rest of the test still runs while qualification is pending.
    """
    for set_id in set_ids:
        reason = pending(set_id)
        if reason is not None:
            case.skipTest(f"{set_id} qualification pending: {reason}")
