"""Atomic JSON file writes shared across the package."""
from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path: Path, payload: dict, *, mkdir: bool = False) -> None:
    """Write JSON to a temp file then os.replace into place (crash-safe)."""
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
