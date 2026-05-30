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
    root = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        )
        if result.returncode == 0 and result.stdout.strip():
            root = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        pass
    path = root or cwd
    resolved = str(Path(path).resolve())
    short_hash = hashlib.sha256(resolved.encode()).hexdigest()[:8]
    name = Path(resolved).name
    return f"{name}-{short_hash}"
