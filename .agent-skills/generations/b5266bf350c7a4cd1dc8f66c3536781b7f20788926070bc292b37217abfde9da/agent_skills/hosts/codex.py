from pathlib import Path

from .base import HostProjection


CODEX = HostProjection(host="codex", root=Path(".agents/skills"))
