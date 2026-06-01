"""Shared transcript path resolution from hook payloads."""
from __future__ import annotations

import os
from pathlib import Path

from .logging import get_logger

log = get_logger("nokori.utils.transcript")


def transcript_key(path: Path) -> str:
    """Canonical key for a transcript path (extract jobs, coalesce claims)."""
    return str(path.expanduser().resolve())


def _allowed_roots() -> list[Path]:
    roots: list[Path] = [
        Path.home() / ".claude",
        Path.home() / ".cursor",
    ]
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
    """True when path resolves under ~/.claude, ~/.cursor, NOKORI_DATA_DIR, or extra roots."""
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


def _transcript_candidate(payload: dict) -> str | None:
    raw = payload.get("transcript_path") or payload.get("transcript")
    if raw not in (None, "", "null"):
        return str(raw)
    env = os.environ.get("CURSOR_TRANSCRIPT_PATH", "").strip()
    return env or None


def transcript_resolve_failure_reason(payload: dict) -> str:
    """Human-readable reason resolve_transcript_path returned None (for logs)."""
    candidate = _transcript_candidate(payload)
    if not candidate:
        return "transcript_path missing and CURSOR_TRANSCRIPT_PATH unset"
    path = Path(candidate).expanduser()
    if path.suffix.lower() != ".jsonl":
        return f"not a .jsonl file: {path}"
    if not _is_under_allowed_root(path):
        return f"outside allowed roots: {path}"
    if not path.is_file():
        return f"file not found: {path}"
    return "unknown"


def resolve_transcript_path(payload: dict) -> Path | None:
    candidate = _transcript_candidate(payload)
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
