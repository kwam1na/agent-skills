"""Build a product from the tree, with the runtime rebinding the release command uses."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from agent_skills.release import build_release


ROOT = Path(__file__).resolve().parents[1]


def _release_command():
    spec = importlib.util.spec_from_file_location("cut_release", ROOT / "scripts" / "cut-release.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_product(directory: Path, profile: str, release_id: str) -> tuple[Path, Path]:
    """The archive and metadata of ``profile``'s product, written under ``directory``."""
    archive = directory / f"{profile}-product.zip"
    metadata = directory / f"{profile}-product.release.json"
    runtime = _release_command().rebound_runtime(profile, directory / f"{profile}-product-runtime")
    build_release(ROOT, archive, metadata, release_id=release_id, profile_id=profile, runtime_directory=runtime)
    return archive, metadata
