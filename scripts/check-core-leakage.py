#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_skills.validate import canonical_json, validate_corpus


def main() -> int:
    findings = [finding.as_dict() for finding in validate_corpus(ROOT).findings if finding.rule_id == "core.leakage"]
    print(canonical_json({"findingCount": len(findings), "findings": findings, "ok": not findings}), end="")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
