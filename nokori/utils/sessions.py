"""Session registry under {data_dir}/active_sessions/.

| API | Meaning |
|-----|---------|
| `is_session_open` | `ended_at` unset — used by status command to count open sessions |
| `list_active_sessions` | open + activity within `session_idle_seconds` — status UI |
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from ..config import Config
from .fs import atomic_write_json
from .ids import safe_session_id
from .time import local_now, now_iso, parse_iso


def _path_for(cfg: Config, session_id: str) -> Path:
    return cfg.sessions_dir / f"{safe_session_id(session_id)}.json"


def _read_record(cfg: Config, session_id: str) -> dict | None:
    p = _path_for(cfg, session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


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
    atomic_write_json(_path_for(cfg, session_id), payload)


def resolve_project_id_for_session(
    cfg: Config,
    session_id: str,
    cwd: str | None,
) -> str | None:
    """Use session cache; refresh when cwd maps to a different project_id."""
    from .project import resolve_project_id_detailed

    record = _read_record(cfg, session_id) or {}
    cached_pid = record.get("project_id")
    cached = str(cached_pid) if cached_pid else None
    cached_from_git = bool(record.get("project_id_from_git"))
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
    data = _read_record(cfg, session_id)
    if data is None:
        data = {"session_id": session_id, "started_at": now_iso(), "ended_at": None}
    data["project_id"] = project_id
    if project_id_from_git is not None:
        data["project_id_from_git"] = project_id_from_git
    data["last_activity"] = now_iso()
    atomic_write_json(p, data)


_TOUCH_INTERVAL_SECONDS = 30


def touch(cfg: Config, session_id: str) -> None:
    p = _path_for(cfg, session_id)
    # ponytail: single-writer per session_id; clock skew → extra write (perf only)
    try:
        if 0 <= (time.time() - p.stat().st_mtime) < _TOUCH_INTERVAL_SECONDS:
            return
    except OSError:
        pass
    data = _read_record(cfg, session_id)
    if data is None:
        register(cfg, session_id)
        return
    data["last_activity"] = now_iso()
    atomic_write_json(p, data)


def end(cfg: Config, session_id: str) -> None:
    data = _read_record(cfg, session_id)
    if data is None:
        return
    data["ended_at"] = now_iso()
    atomic_write_json(_path_for(cfg, session_id), data)


def is_session_open(data: dict) -> bool:
    """Claude session has not ended (SessionEnd not received). Used for extract defer."""
    return not data.get("ended_at")


def is_active_record(
    data: dict,
    *,
    idle_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Session is open and had activity within idle window (status display / stale cleanup)."""
    if data.get("ended_at"):
        return False
    now = now or local_now()
    last = parse_iso(data.get("last_activity")) or parse_iso(data.get("started_at"))
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.astimezone()  # assume local tz for legacy naive timestamps
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


def list_active_sessions(
    cfg: Config,
    *,
    idle_seconds: int | None = None,
    records: list[dict] | None = None,
) -> list[dict]:
    idle = idle_seconds if idle_seconds is not None else cfg.session_idle_seconds
    now = local_now()
    rows = records if records is not None else list_session_records(cfg)
    active = [d for d in rows if is_active_record(d, idle_seconds=idle, now=now)]
    active.sort(key=lambda r: r.get("last_activity") or "", reverse=True)
    return active


SESSION_FILE_RETENTION_DAYS = 60


def prune_ended_session_files(cfg: Config, max_age_days: int = SESSION_FILE_RETENTION_DAYS) -> int:
    """Remove session registry files ended longer than max_age_days ago."""
    now = local_now()
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
