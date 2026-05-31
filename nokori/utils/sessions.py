"""Session registry under {data_dir}/active_sessions/.

| API | Meaning |
|-----|---------|
| `count_open_sessions` | `ended_at` unset — used by SessionEnd extract defer |
| `count_active_sessions` / `list_active_sessions` | open + activity within `session_idle_seconds` — status UI |
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from .time import now_iso, parse_iso


def _path_for(cfg: Config, session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return cfg.sessions_dir / f"{safe}.json"


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def register(
    cfg: Config,
    session_id: str,
    project_id: str | None = None,
    *,
    project_id_from_git: bool | None = None,
) -> None:
    cfg.ensure_dirs()
    payload = {
        "session_id": session_id,
        "project_id": project_id,
        "started_at": now_iso(),
        "last_activity": now_iso(),
        "ended_at": None,
    }
    if project_id_from_git is not None:
        payload["project_id_from_git"] = project_id_from_git
    _atomic_write_json(_path_for(cfg, session_id), payload)


def get_project_id(cfg: Config, session_id: str) -> str | None:
    """Cached project_id from SessionStart (avoids git on every UserPromptSubmit)."""
    p = _path_for(cfg, session_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = data.get("project_id")
    return str(pid) if pid else None


def get_project_id_from_git(cfg: Config, session_id: str) -> bool:
    p = _path_for(cfg, session_id)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("project_id_from_git"))


def resolve_project_id_for_session(
    cfg: Config,
    session_id: str,
    cwd: str | None,
) -> str | None:
    """Use session cache; refresh when cwd maps to a different project_id."""
    from .project import resolve_project_id_detailed

    cached = get_project_id(cfg, session_id)
    cached_from_git = get_project_id_from_git(cfg, session_id)
    if not cwd:
        return cached
    resolved, used_git = resolve_project_id_detailed(cwd)
    if resolved is None:
        return cached
    if used_git:
        if cached != resolved:
            update_project_id(cfg, session_id, resolved, project_id_from_git=True)
        return resolved
    if cached_from_git:
        return cached
    if cached is None:
        update_project_id(cfg, session_id, resolved, project_id_from_git=False)
        return resolved
    if cached != resolved:
        update_project_id(cfg, session_id, resolved, project_id_from_git=False)
        return resolved
    return cached


def update_project_id(
    cfg: Config,
    session_id: str,
    project_id: str,
    *,
    project_id_from_git: bool | None = None,
) -> None:
    cfg.ensure_dirs()
    p = _path_for(cfg, session_id)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"session_id": session_id}
    else:
        data = {"session_id": session_id, "started_at": now_iso(), "ended_at": None}
    data["project_id"] = project_id
    if project_id_from_git is not None:
        data["project_id_from_git"] = project_id_from_git
    data["last_activity"] = now_iso()
    _atomic_write_json(p, data)


def touch(cfg: Config, session_id: str) -> None:
    p = _path_for(cfg, session_id)
    if not p.exists():
        register(cfg, session_id)
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        register(cfg, session_id)
        return
    data["last_activity"] = now_iso()
    _atomic_write_json(p, data)


def end(cfg: Config, session_id: str) -> None:
    p = _path_for(cfg, session_id)
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    data["ended_at"] = now_iso()
    _atomic_write_json(p, data)


def is_session_open(data: dict) -> bool:
    """Claude session has not ended (SessionEnd not received). Used for extract defer."""
    return not data.get("ended_at")


def count_open_sessions(
    cfg: Config,
    *,
    exclude_session: str | None = None,
) -> int:
    n = 0
    for data in list_session_records(cfg):
        sid = data.get("session_id")
        if exclude_session and sid == exclude_session:
            continue
        if is_session_open(data):
            n += 1
    return n


def is_active_record(
    data: dict,
    *,
    idle_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Session is open and had activity within idle window (status display / stale cleanup)."""
    if data.get("ended_at"):
        return False
    now = now or datetime.now(timezone.utc)
    last = parse_iso(data.get("last_activity")) or parse_iso(data.get("started_at"))
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() <= idle_seconds


def list_session_records(cfg: Config) -> list[dict]:
    if not cfg.sessions_dir.is_dir():
        return []
    rows: list[dict] = []
    for path in cfg.sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["_path"] = str(path)
        rows.append(data)
    return rows


def count_active_sessions(
    cfg: Config,
    *,
    exclude_session: str | None = None,
    idle_seconds: int | None = None,
) -> int:
    """Count open sessions with recent activity (status UI; not used for extract defer)."""
    idle = idle_seconds if idle_seconds is not None else cfg.session_idle_seconds
    now = datetime.now(timezone.utc)
    n = 0
    for data in list_session_records(cfg):
        sid = data.get("session_id")
        if exclude_session and sid == exclude_session:
            continue
        if is_active_record(data, idle_seconds=idle, now=now):
            n += 1
    return n


def list_active_sessions(
    cfg: Config,
    *,
    idle_seconds: int | None = None,
    records: list[dict] | None = None,
) -> list[dict]:
    idle = idle_seconds if idle_seconds is not None else cfg.session_idle_seconds
    now = datetime.now(timezone.utc)
    rows = records if records is not None else list_session_records(cfg)
    active = [
        d
        for d in rows
        if is_active_record(d, idle_seconds=idle, now=now)
    ]
    active.sort(key=lambda r: r.get("last_activity") or "", reverse=True)
    return active


SESSION_FILE_RETENTION_DAYS = 60


def prune_ended_session_files(cfg: Config, max_age_days: int = SESSION_FILE_RETENTION_DAYS) -> int:
    """Remove session registry files ended longer than max_age_days ago."""
    now = datetime.now(timezone.utc)
    removed = 0
    for data in list_session_records(cfg):
        ended = data.get("ended_at")
        if not ended:
            continue
        ended_dt = parse_iso(ended)
        if ended_dt is None:
            continue
        age_days = (now - ended_dt).days
        if age_days < max_age_days:
            continue
        path_raw = data.get("_path")
        path = Path(path_raw) if path_raw else None
        if path is None:
            sid = data.get("session_id")
            if not sid:
                continue
            path = _path_for(cfg, sid)
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return removed
