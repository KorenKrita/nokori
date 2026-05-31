from __future__ import annotations

import uuid

from ..config import Config
from ..db import open_db, total_rule_count
from ..extract import jobs as job_io
from ..extract.lock import is_locked
from ..lifecycle import hot_cache, maintenance
from ..search import embedding as embedding_search
from ..search import embed_ipc
from ..utils import sessions
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id_detailed

log = get_logger("nokori.hooks.session_start")


def _maybe_kickstart_embed(cfg: Config, db) -> None:
    if not embedding_search.use_local(cfg):
        return
    if not embedding_search.auto_enabled(cfg, total_rule_count(db)):
        return
    if cfg.embed_server_auto_start:
        embed_ipc.kickstart_server(cfg)


def handle(payload: dict, cfg: Config) -> dict:
    session_id = payload.get("session_id") or str(uuid.uuid4())
    project_id, from_git = resolve_project_id_detailed(payload.get("cwd"))
    sessions.register(
        cfg, session_id, project_id, project_id_from_git=from_git,
    )

    cache_text = None
    db = open_db(cfg.db_path)
    try:
        try:
            maintenance.run_due_jobs(db, cfg)
        except Exception:
            log.exception("session_start maintenance failed")
        try:
            _maybe_kickstart_embed(cfg, db)
        except Exception:
            log.exception("session_start embed kickstart failed")
        try:
            cache_text = hot_cache.maybe_inject(payload, cfg, db)
        except Exception:
            log.exception("session_start hot_cache failed")
        if cfg.extract_mode == "async" and job_io.list_jobs(cfg, status="pending"):
            if not is_locked(cfg):
                from .session_end import _spawn_async_extract

                _spawn_async_extract(cfg)
                log.info("session_start retrying pending extract jobs")
    finally:
        db.close()

    if cache_text:
        return {"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": cache_text,
        }}
    return {"continue": True}
