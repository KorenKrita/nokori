from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from nokori.db import open_db
from nokori.extract import jobs as job_io
from nokori.search import embed_ipc
from nokori.utils.time import iso_of
from nokori.web.deps import get_config

router = APIRouter()


@router.get("/dashboard")
def dashboard():
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rules_by_status = db.fetchall(
            "SELECT status, COUNT(*) AS n FROM rules GROUP BY status"
        )
        cutoff = iso_of(datetime.now(timezone.utc) - timedelta(hours=24))
        inj_row = db.fetchone(
            "SELECT COUNT(*) AS n FROM injections WHERE created_at >= ?", (cutoff,)
        )
        inj_hot = db.fetchone(
            "SELECT COUNT(*) AS n FROM injections WHERE created_at >= ? AND level = 'hot'",
            (cutoff,),
        )
        global_count = db.fetchone(
            "SELECT COUNT(*) AS n FROM rules WHERE project_scope = 'global'"
        )
    finally:
        db.close()

    by_status = {r["status"]: r["n"] for r in rules_by_status}
    est = embed_ipc.server_status(cfg)
    pending_jobs = len(job_io.list_jobs(cfg, status="pending"))

    return {
        "data": {
            "rules": {
                "total": sum(by_status.values()),
                "active": by_status.get("active", 0),
                "dormant": by_status.get("dormant", 0),
                "candidate": by_status.get("candidate", 0),
                "merged": by_status.get("merged", 0),
                "archived": by_status.get("archived", 0),
                "global": global_count["n"] if global_count else 0,
            },
            "injections_24h": inj_row["n"] if inj_row else 0,
            "injections_hot_24h": inj_hot["n"] if inj_hot else 0,
            "gate_enabled": cfg.gate_enabled,
            "embed_server": {
                "running": est["running"],
                "pid": est["pid"],
                "idle_seconds": est["idle_seconds"],
            },
            "extract_pending": pending_jobs,
            "extract_mode": cfg.extract_mode,
            "promotion_enabled": cfg.promotion_enabled,
            "hot_cache_enabled": cfg.hot_cache_enabled,
        }
    }
