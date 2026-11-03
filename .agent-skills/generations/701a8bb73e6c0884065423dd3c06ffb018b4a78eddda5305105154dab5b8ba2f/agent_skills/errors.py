from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Finding:
    rule_id: str
    artifact: str
    message: str
    exit_class: str = "invalid-corpus"

    def as_dict(self) -> dict[str, str]:
        return {
            "artifact": self.artifact,
            "exitClass": self.exit_class,
            "message": self.message,
            "ruleId": self.rule_id,
        }
