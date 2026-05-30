"""Shared transcript path resolution from hook payloads."""
from __future__ import annotations

from pathlib import Path


def resolve_transcript_path(payload: dict) -> Path | None:
    candidate = payload.get("transcript_path") or payload.get("transcript")
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if path.exists():
        return path
    return None
