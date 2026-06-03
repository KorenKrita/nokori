from __future__ import annotations

from fastapi import APIRouter, Query

from nokori.db import open_db
from nokori.web.deps import get_config

router = APIRouter()


@router.get("/injections")
def list_injections(
    level: str | None = Query(None),
    rule_id: str | None = Query(None),
    session_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        where = []
        params: list = []
        if level:
            where.append("i.level = ?")
            params.append(level)
        if rule_id:
            where.append("i.rule_id = ?")
            params.append(rule_id)
        if session_id:
            where.append("i.session_id = ?")
            params.append(session_id)

        where_clause = (" WHERE " + " AND ".join(where)) if where else ""

        count_row = db.fetchone(
            f"SELECT COUNT(*) AS n FROM injections i{where_clause}",
            tuple(params),
        )
        total = count_row["n"] if count_row else 0

        offset = (page - 1) * per_page
        rows = db.fetchall(
            f"SELECT i.id, i.rule_id, r.short_id AS rule_short_id, "
            f"r.project_scope AS rule_project_scope, "
            f"r.project_id AS rule_project_id, "
            f"i.session_id, i.prompt_hash, i.level, i.created_at "
            f"FROM injections i LEFT JOIN rules r ON r.id = i.rule_id"
            f"{where_clause} ORDER BY i.created_at DESC LIMIT ? OFFSET ?",
            tuple(params) + (per_page, offset),
        )
    finally:
        db.close()

    return {
        "data": [
            {
                "id": row["id"],
                "rule_id": row["rule_id"],
                "rule_short_id": row["rule_short_id"],
                "rule_project_scope": row["rule_project_scope"],
                "rule_project_id": row["rule_project_id"],
                "session_id": row["session_id"],
                "prompt_hash": row["prompt_hash"],
                "level": row["level"],
                "created_at": row["created_at"],
            }
            for row in rows
        ],
        "meta": {"total": total, "page": page, "per_page": per_page},
    }
