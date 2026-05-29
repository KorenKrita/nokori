from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _path_for(cfg: Config, session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return cfg.sessions_dir / f"{safe}.json"


def register(cfg: Config, session_id: str, project_id: str | None = None) -> None:
    cfg.ensure_dirs()
    payload = {
        "session_id": session_id,
        "project_id": project_id,
        "started_at": _now_iso(),
        "last_activity": _now_iso(),
        "ended_at": None,
    }
    _path_for(cfg, session_id).write_text(json.dumps(payload), encoding="utf-8")


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
    data["last_activity"] = _now_iso()
    p.write_text(json.dumps(data), encoding="utf-8")


def end(cfg: Config, session_id: str) -> None:
    p = _path_for(cfg, session_id)
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    data["ended_at"] = _now_iso()
    p.write_text(json.dumps(data), encoding="utf-8")


def has_recent_activity(cfg: Config, within_seconds: int = 30) -> bool:
    if not cfg.sessions_dir.exists():
        return False
    now = datetime.now(timezone.utc)
    for entry in cfg.sessions_dir.glob("*.json"):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("ended_at"):
            continue
        last = data.get("last_activity")
        if not last:
            continue
        try:
            dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - dt).total_seconds() <= within_seconds:
            return True
    return False
