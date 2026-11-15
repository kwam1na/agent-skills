from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostProjection:
    host: str
    root: Path
    mode: str = "relative-symlink"

    def as_dict(self) -> dict[str, str]:
        return {
            "host": self.host,
            "mode": self.mode,
            "root": self.root.as_posix(),
        }
