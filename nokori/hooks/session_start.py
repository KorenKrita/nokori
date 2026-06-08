from __future__ import annotations

import uuid

from ..config import Config
from ..db import open_db, total_rule_count
from ..events.observability import write_event
from ..lifecycle import hot_cache, maintenance
from ..search import embedding as embedding_search
from ..search import embed_ipc
from ..utils import sessions
from ..utils.hook_response import session_start_response
from ..utils.host import Host, effective_session_id
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id_detailed

log = get_logger("nokori.hooks.session_start")


def _maybe_kickstart_embed(cfg: Config, db) -> str:
    """Attempt embed server kickstart. Returns status string for observability."""
    rule_count = total_rule_count(db)
    if not embedding_search.embedding_active(cfg, rule_count):
        return "skipped_threshold"
    if not embedding_search.use_local_config(cfg):
        return "skipped_not_local"
    if not embedding_search.local_model_cached(cfg):
        log.warning(
            "local embed weights missing under %s; run: nokori embed prefetch",
            embedding_search.local_model_cache_dir(cfg),
        )
        return "skipped_weights_missing"
    if not embedding_search.local_embed_package_available():
        log.warning(
            "local embed package missing; run: pip install -e \".[local-embed]\""
        )
        return "skipped_package_missing"
    if not cfg.embed_server_auto_start:
        return "skipped_auto_start_off"
    if embed_ipc.ping(cfg):
        log.info("embed server already running (ping ok)")
        return "already_running"
    embed_ipc.kickstart_server(cfg)
    return "kickstarted"


def handle(payload: dict, cfg: Config, *, host: Host) -> dict:
    session_id = effective_session_id(payload, default="")
    if not session_id:
        session_id = str(uuid.uuid4())
    project_id, from_git = resolve_project_id_detailed(payload.get("cwd"))
    sessions.register(
        cfg, session_id, project_id, project_id_from_git=from_git,
    )

    cache_text = None
    embed_status = "skipped"
    maintenance_ok = True
    hot_cache_ok = True
    db = open_db(cfg.db_path)
    try:
        try:
            maintenance.run_maintenance(db, cfg)
        except Exception:
            log.exception("session_start maintenance failed")
            maintenance_ok = False
        try:
            embed_status = _maybe_kickstart_embed(cfg, db)
        except Exception:
            log.exception("session_start embed kickstart failed")
            embed_status = "failed"
        try:
            cache_text = hot_cache.maybe_inject(payload, cfg, db)
        except Exception:
            log.exception("session_start hot_cache failed")
            hot_cache_ok = False

        rule_count = 0
        try:
            rule_count = total_rule_count(db)
        except Exception:
            pass
        all_ok = maintenance_ok and hot_cache_ok and embed_status != "failed"
        write_event(
            db,
            source="session_start",
            session_id=session_id,
            outcome="ok" if all_ok else "partial_failure",
            details={
                "embed_status": embed_status,
                "hot_cache_injected": cache_text is not None,
                "maintenance_ok": maintenance_ok,
                "project_id": project_id,
                "rule_count": rule_count,
            },
        )
    finally:
        db.close()

    return session_start_response(host, cache_text)
