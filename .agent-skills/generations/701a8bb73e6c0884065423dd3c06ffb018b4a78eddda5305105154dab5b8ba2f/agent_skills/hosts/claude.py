from pathlib import Path

from .base import HostProjection


CLAUDE_CODE = HostProjection(host="claude-code", root=Path(".claude/skills"))
