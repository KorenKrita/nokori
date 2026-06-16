from __future__ import annotations

import uuid

from ..config import Config
from ..db import Db, retrieval_pool_count, total_rule_count
from ..lifecycle import hot_cache, maintenance
from ..lifecycle.maintenance import cold_eval_due, mark_cold_eval_run
from ..search import embed_ipc, embedding as embedding_search
from ..utils import sessions
from ..utils.hook_response import session_start_response
from ..utils.host import Host, effective_session_id
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id_detailed
from .context import ErrorCategory, HotPathContext

log = get_logger("nokori.hooks.session_start")

COLD_EVAL_INTERVAL_DAYS = 1


def _maybe_kickstart_embed(cfg: Config, db: Db) -> str:
    """Attempt embed server kickstart. Returns status string for observability."""
    rule_count = retrieval_pool_count(db) if cfg.promotion_enabled else total_rule_count(db)
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
        log.warning('local embed package missing; run: pip install -e ".[local-embed]"')
        return "skipped_package_missing"
    if not cfg.embed_server_auto_start:
        return "skipped_auto_start_off"
    if embed_ipc.ping(cfg):
        log.info("embed server already running (ping ok)")
        return "already_running"
    embed_ipc.kickstart_server(cfg)
    return "kickstarted"


def _maybe_spawn_cold_eval(db: Db, cfg: Config) -> bool:
    """Spawn `nokori maintain` in background if shadow/posthoc eval is due.

    Returns True if spawned.
    """
    if not cold_eval_due(db, COLD_EVAL_INTERVAL_DAYS):
        return False

    has_unlabeled = db.fetchone(
        "SELECT 1 FROM rule_shadow_events WHERE shadow_label IS NULL LIMIT 1"
    )
    has_pending_posthoc = db.fetchone("SELECT 1 FROM posthoc_jobs WHERE status = 'pending' LIMIT 1")
    if not has_unlabeled and not has_pending_posthoc:
        return False

    import os
    import subprocess
    import sys

    _SAFE_VARS = (
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "SHELL",
        "TERM",
        "TMPDIR",
        "XDG_RUNTIME_DIR",
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k in _SAFE_VARS or k.startswith("NOKORI_") or k.startswith("ANTHROPIC_")
    }
    env["NOKORI_DATA_DIR"] = str(cfg.data_dir)

    cfg.ensure_dirs()
    err_log = cfg.logs_dir / "cold-eval.log"
    err_file = None
    try:
        err_file = open(err_log, "a", encoding="utf-8")
    except OSError:
        pass

    try:
        subprocess.Popen(
            [sys.executable, "-m", "nokori", "maintain"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err_file if err_file is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
        mark_cold_eval_run(db)
        log.info("spawned background cold eval (shadow/posthoc)")
        return True
    except Exception as e:
        log.warning("cold eval spawn failed: %s", e)
        return False
    finally:
        if err_file is not None:
            try:
                err_file.close()
            except OSError:
                pass


def handle(payload: dict, cfg: Config, *, host: Host) -> dict:
    session_id = effective_session_id(payload, default="")
    if not session_id:
        session_id = str(uuid.uuid4())
    project_id, from_git = resolve_project_id_detailed(payload.get("cwd"))
    sessions.register(
        cfg,
        session_id,
        project_id,
        project_id_from_git=from_git,
    )

    cache_text = None
    embed_status = "skipped"
    maintenance_ok = True
    hot_cache_ok = True
    cold_eval_spawned = False

    with HotPathContext(payload, cfg, host=host, session_id=session_id) as ctx:
        db = ctx.db
        if db is None:
            log.warning("session_start: db unavailable, fail-open session=%s", session_id)
            return session_start_response(host, None)

        try:
            maintenance.run_maintenance(db, cfg)
        except Exception as e:
            log.exception("session_start maintenance failed")
            maintenance_ok = False
            ctx.add_error("maintenance", ErrorCategory.DEGRADED, str(e), e)

        try:
            cold_eval_spawned = _maybe_spawn_cold_eval(db, cfg)
        except Exception as e:
            log.exception("session_start cold_eval spawn failed")
            ctx.add_error("cold_eval", ErrorCategory.DEGRADED, str(e), e)

        try:
            embed_status = _maybe_kickstart_embed(cfg, db)
        except Exception as e:
            log.exception("session_start embed kickstart failed")
            embed_status = "failed"
            ctx.add_error("embed", ErrorCategory.DEGRADED, str(e), e)

        try:
            cache_text = hot_cache.maybe_inject(payload, cfg, db)
        except Exception as e:
            log.exception("session_start hot_cache failed")
            hot_cache_ok = False
            ctx.add_error("hot_cache", ErrorCategory.DEGRADED, str(e), e)

        rule_count = 0
        try:
            rule_count = retrieval_pool_count(db) if cfg.promotion_enabled else total_rule_count(db)
        except Exception as e:
            ctx.add_error("rule_count", ErrorCategory.DEGRADED, str(e), e)

        all_ok = maintenance_ok and hot_cache_ok and embed_status != "failed"
        ctx.record_event(
            "session_start",
            "ok" if all_ok else "partial_failure",
            details={
                "embed_status": embed_status,
                "hot_cache_injected": cache_text is not None,
                "maintenance_ok": maintenance_ok,
                "cold_eval_spawned": cold_eval_spawned,
                "project_id": project_id,
                "rule_count": rule_count,
            },
        )

    return session_start_response(host, cache_text)
