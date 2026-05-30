from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def resolve_project_id(cwd: str | None) -> str | None:
    """Derive a stable project identifier from the working directory."""
    pid, _used_git = resolve_project_id_detailed(cwd)
    return pid


def resolve_project_id_detailed(cwd: str | None) -> tuple[str | None, bool]:
    """Return (project_id, used_git_root).

    When git root resolution fails, callers can avoid overwriting a session
    cache that was established via git with a cwd-only hash.
    """
    if not cwd:
        return None, False
    try:
        norm = str(Path(cwd).expanduser().resolve())
    except OSError:
        norm = cwd
    root = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=norm,
        )
        if result.returncode == 0 and result.stdout.strip():
            root = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        pass
    used_git = root is not None
    path = root or norm
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = path
    short_hash = hashlib.sha256(resolved.encode()).hexdigest()[:8]
    name = Path(resolved).name
    return f"{name}-{short_hash}", used_git
