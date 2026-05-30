from __future__ import annotations


def resolve_project_id(cwd: str | None) -> str | None:
    if not cwd:
        return None
    return cwd.rstrip("/").split("/")[-1] or None
