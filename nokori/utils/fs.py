"""Atomic JSON file writes shared across the package."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def atomic_write_json(
    path: Path,
    payload: dict,
    *,
    mkdir: bool = False,
    indent: int | None = None,
) -> None:
    """Write JSON via unique temp file + os.replace (parallel-safe)."""
    if mkdir or not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=indent)
        if indent is not None:
            text += "\n"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
