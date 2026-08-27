"""Manifest of path -> content_hash, enabling incremental scans."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MANIFEST_PATH = Path(".ibwd") / "manifest.json"


def load_manifest(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, str]:
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text())


def save_manifest(manifest: dict[str, str], manifest_path: Path = DEFAULT_MANIFEST_PATH) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
