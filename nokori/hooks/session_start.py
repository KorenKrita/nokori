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
from ..utils.hook_response import session_start_response
from ..utils.host import Host, detect_host_from_payload, effective_session_id
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id_detailed

log = get_logger("nokori.hooks.session_start")


def _maybe_kickstart_embed(cfg: Config, db) -> None:
    rule_count = total_rule_count(db)
    if not embedding_search.embedding_active(cfg, rule_count):
        return
    if not embedding_search.use_local_config(cfg):
        return
    if not embedding_search.local_model_cached(cfg):
        log.warning(
            "local embed weights missing under %s; run: nokori embed prefetch",
            embedding_search.local_model_cache_dir(cfg),
        )
        return
    if not embedding_search.local_embed_package_available():
        log.warning(
            "local embed package missing; run: pip install -e \".[local-embed]\""
        )
        return
    if not cfg.embed_server_auto_start:
        return
    if embed_ipc.ping(cfg):
        log.info("embed server already running (ping ok)")
        return
    embed_ipc.kickstart_server(cfg)


def handle(payload: dict, cfg: Config, *, host: Host | None = None) -> dict:
    session_id = effective_session_id(payload, default="")
    if not session_id:
        session_id = str(uuid.uuid4())
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

    if host is None:
        host = detect_host_from_payload(payload)
    return session_start_response(host, cache_text)
