from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from nokori.db import open_db
from nokori.events.observability import query_errors
from nokori.web.deps import get_config

router = APIRouter()


@router.get("/monitor/overview")
def get_monitor_overview(
    session_id: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
):
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        where_parts = []
        params: list = []
        if session_id:
            where_parts.append("session_id = ?")
            params.append(session_id)
        if since:
            where_parts.append("created_at >= ?")
            params.append(since)
        if until:
            where_parts.append("created_at <= ?")
            params.append(until)

        where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        total_events = db.fetchone(
            f"SELECT COUNT(*) AS n FROM hook_events{where_clause}",
            tuple(params),
        )
        total_errors = db.fetchone(
            f"SELECT COUNT(*) AS n FROM error_events{where_clause}",
            tuple(params),
        )

        events_by_source = db.fetchall(
            f"SELECT source, COUNT(*) AS count FROM hook_events{where_clause} "
            "GROUP BY source ORDER BY count DESC",
            tuple(params),
        )

        events_by_outcome = db.fetchall(
            f"SELECT outcome, COUNT(*) AS count FROM hook_events{where_clause} "
            "GROUP BY outcome ORDER BY count DESC LIMIT 20",
            tuple(params),
        )

        error_summary = query_errors(
            db, group_by="role", session_id=session_id, since=since, until=until
        )

        # Conversion funnel: cold pipeline events
        pipeline_where = list(where_parts)
        pipeline_params = list(params)
        pipeline_where.append("source = 'cold_pipeline'")
        pw = (" WHERE " + " AND ".join(pipeline_where)) if pipeline_where else ""

        funnel_rows = db.fetchall(
            f"SELECT outcome, COUNT(*) AS count FROM hook_events{pw} GROUP BY outcome",
            tuple(pipeline_params),
        )
        funnel = {r["outcome"]: r["count"] for r in funnel_rows}

        return {
            "total_events": total_events["n"] if total_events else 0,
            "total_errors": total_errors["n"] if total_errors else 0,
            "events_by_source": [dict(r) for r in events_by_source],
            "events_by_outcome": [dict(r) for r in events_by_outcome],
            "error_summary": error_summary,
            "pipeline_funnel": funnel,
        }
    finally:
        db.close()


@router.get("/monitor/errors")
def get_monitor_errors(
    group_by: Literal["role", "model_id", "error_type", "source"] = Query("role"),
    session_id: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
):
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        results = query_errors(
            db, group_by=group_by, session_id=session_id, since=since, until=until
        )
        return {"errors": results, "group_by": group_by}
    finally:
        db.close()


@router.get("/monitor/errors/trend")
def get_error_trend(
    since: str | None = Query(None),
    until: str | None = Query(None),
    session_id: str | None = Query(None),
):
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        where_parts = []
        params: list = []
        if session_id:
            where_parts.append("session_id = ?")
            params.append(session_id)
        if since:
            where_parts.append("created_at >= ?")
            params.append(since)
        if until:
            where_parts.append("created_at <= ?")
            params.append(until)

        where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        rows = db.fetchall(
            "SELECT DATE(created_at) AS day, error_type, COUNT(*) AS count "
            f"FROM error_events{where_clause} "
            "GROUP BY day, error_type ORDER BY day ASC",
            tuple(params),
        )
        return {"trend": [dict(r) for r in rows]}
    finally:
        db.close()
