#!/usr/bin/env python3
"""Compatibility entry point for the authoritative offline validator."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_skills.validate import main


if __name__ == "__main__":
    raise SystemExit(main())
