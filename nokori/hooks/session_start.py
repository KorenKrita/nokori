from __future__ import annotations

from ..config import Config
from ..db import open_db
from ..lifecycle import hot_cache, maintenance
from ..utils import sessions
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id

log = get_logger("nokori.hooks.session_start")


def handle(payload: dict, cfg: Config) -> dict:
    session_id = payload.get("session_id") or "-"
    project_id = resolve_project_id(payload.get("cwd"))
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
