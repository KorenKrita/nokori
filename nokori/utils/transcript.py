"""Shared transcript path resolution from hook payloads."""
from __future__ import annotations

import os
from pathlib import Path

from .logging import get_logger

log = get_logger("nokori.utils.transcript")


def _allowed_roots() -> list[Path]:
    roots: list[Path] = [Path.home() / ".claude"]
    for env_name in ("CLAUDE_PROJECT_DIR", "NOKORI_DATA_DIR"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value).expanduser().resolve())
    extra = os.environ.get("NOKORI_TRANSCRIPT_EXTRA_ROOTS", "")
    for part in extra.split(os.pathsep):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser().resolve())
    return roots


def is_path_allowed(path: Path) -> bool:
    """True when path resolves under ~/.claude, NOKORI_DATA_DIR, or extra roots."""
    return _is_under_allowed_root(path)


def _is_under_allowed_root(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in _allowed_roots():
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(root_resolved)
            return True
        except ValueError:
            continue
    return False


def resolve_transcript_path(payload: dict) -> Path | None:
    candidate = payload.get("transcript_path") or payload.get("transcript")
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if path.suffix.lower() != ".jsonl":
        log.warning("transcript_path is not a .jsonl file: %s", path)
        return None
    if not _is_under_allowed_root(path):
        log.warning("transcript_path outside allowed roots: %s", path)
        return None
    if path.is_file():
        return path
    return None
