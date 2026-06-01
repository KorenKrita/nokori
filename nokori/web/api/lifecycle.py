from __future__ import annotations

from fastapi import APIRouter

from nokori.db import open_db
from nokori.lifecycle.promotion import (
    CROSS_PROJECT_PROMOTE_THRESHOLD,
    unique_promotion_project_ids,
)
from nokori.web.deps import get_config

router = APIRouter()


@router.get("/lifecycle/promotion")
def promotion_progress():
    cfg = get_config()
    if not cfg.promotion_enabled:
        return {"data": {"enabled": False, "candidates": []}}

    db = open_db(cfg.db_path)
    try:
        rows = db.fetchall(
            "SELECT short_id, project_id, trigger_text, promotion_evidence, "
            "shadow_hit_count FROM rules "
            "WHERE status = 'active' AND confidence = 'high' "
            "AND source_type IN ('correction','anti_pattern','solution') "
            "AND project_scope = 'project' AND project_id IS NOT NULL "
            "ORDER BY updated_at DESC"
        )
    finally:
        db.close()

    candidates = []
    for row in rows:
        projects = unique_promotion_project_ids(row["promotion_evidence"])
        if not projects:
            continue
        candidates.append({
            "short_id": row["short_id"],
            "project_id": row["project_id"],
            "trigger_text": row["trigger_text"],
            "shadow_hit_count": row["shadow_hit_count"],
            "unique_projects": projects,
            "progress": len(projects),
            "threshold": CROSS_PROJECT_PROMOTE_THRESHOLD,
        })
    candidates.sort(key=lambda x: (-x["progress"], x["short_id"]))
    return {"data": {"enabled": True, "candidates": candidates}}


@router.get("/lifecycle/maintenance")
def maintenance_status():
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rows = db.fetchall("SELECT key, last_run FROM maintenance_meta")
    finally:
        db.close()
    return {"data": {row["key"]: row["last_run"] for row in rows}}
