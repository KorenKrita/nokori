from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def resolve_project_id(cwd: str | None) -> str | None:
    """Derive a stable project identifier from the working directory.

    Uses git repo root resolved path hashed to 8 chars — stable across
    symlinks, worktrees, and same-name repos at different paths.
    Falls back to hashed cwd if not a git repo.
    """
    if not cwd:
        return None
    try:
        norm = str(Path(cwd).expanduser().resolve())
    except OSError:
        norm = cwd
    return _project_id_for_cwd(norm)


def _project_id_for_cwd(norm_cwd: str) -> str | None:
    root = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=norm_cwd,
        )
        if result.returncode == 0 and result.stdout.strip():
            root = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        pass
    path = root or norm_cwd
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = path
    short_hash = hashlib.sha256(resolved.encode()).hexdigest()[:8]
    name = Path(resolved).name
    return f"{name}-{short_hash}"
