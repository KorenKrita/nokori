from __future__ import annotations

from fastapi import APIRouter

from nokori.db import open_db, total_rule_count
from nokori.search import embed_ipc, embedding as embedding_search
from nokori.web.deps import get_config

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    cfg = get_config()
    checks = {}

    try:
        db = open_db(cfg.db_path)
        try:
            db.schema_version()
            checks["db"] = {"status": "ok", "detail": "readable"}
            rule_count = total_rule_count(db)
            checks["rules"] = {"status": "ok", "detail": f"{rule_count} searchable"}
        finally:
            db.close()
    except Exception as e:
        checks["db"] = {"status": "fail", "detail": str(e)}
        rule_count = 0

    checks["llm"] = {
        "status": "ok" if (cfg.llm_base_url and cfg.llm_model) else "skip",
        "detail": f"{cfg.llm_model}" if cfg.llm_model else "not configured",
    }

    est = embed_ipc.server_status(cfg)
    if embedding_search.auto_enabled(cfg, rule_count):
        checks["embed"] = {
            "status": "ok" if est["running"] else "warn",
            "detail": f"server={'running' if est['running'] else 'stopped'} pid={est['pid']}",
        }
    else:
        checks["embed"] = {"status": "skip", "detail": "not enabled"}

    return {"data": checks}
