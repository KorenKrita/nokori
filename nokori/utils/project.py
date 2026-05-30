from __future__ import annotations

import subprocess
from pathlib import Path


def resolve_project_id(cwd: str | None) -> str | None:
    """Derive a stable project identifier from the working directory.

    Tries git rev-parse to get the repo root basename (stable across symlinks
    and worktrees). Falls back to the last path component of cwd.
    """
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).name or None
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        pass
    return cwd.rstrip("/").split("/")[-1] or None
