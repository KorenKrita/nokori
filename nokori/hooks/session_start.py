from __future__ import annotations

from ..config import Config
from ..db import open_db
from ..lifecycle import hot_cache, maintenance
from ..utils import sessions
from ..utils.logging import get_logger

log = get_logger("nokori.hooks.session_start")


def _resolve_project_id(payload: dict) -> str | None:
    cwd = payload.get("cwd")
    if not cwd:
        return None
    return cwd.rstrip("/").split("/")[-1] or None


def handle(payload: dict, cfg: Config) -> dict:
    session_id = payload.get("session_id") or "-"
    project_id = _resolve_project_id(payload)
    sessions.register(cfg, session_id, project_id)

    db = open_db(cfg.db_path)
    cache_text = None
    try:
        maintenance.run_due_jobs(db)
        cache_text = hot_cache.maybe_inject(payload, cfg, db)
    except Exception:
        log.exception("session_start maintenance failed")
    finally:
        db.close()

    if cache_text:
        return {"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": cache_text,
        }}
    return {"continue": True}
