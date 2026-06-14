from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from nokori.db import open_db
from nokori.events.observability import query_events, query_events_latest
from nokori.web.deps import get_config

router = APIRouter()


@router.get("/timeline")
def get_timeline(
    after_id: str | None = Query(None),
    session_id: str | None = Query(None),
    source: str | None = Query(None),
    latest: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    if latest and after_id is not None:
        raise HTTPException(400, detail="Cannot use 'latest' and 'after_id' together")
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        if latest:
            events = query_events_latest(db, session_id=session_id, source=source, limit=limit)
            has_more = False
        else:
            events = query_events(
                db, session_id=session_id, source=source, after_id=after_id, limit=limit + 1
            )
            has_more = len(events) > limit
            if has_more:
                events = events[:limit]

        # When filtering by session, also include related pipeline events
        # (cold_pipeline/cli_extract don't carry session_id but are causally linked)
        # Only on first page (no after_id) to avoid repeats across pages.
        if session_id and not source and not latest and after_id is None:
            pipeline_events = _find_related_pipeline_events(db, session_id, limit)
            if pipeline_events:
                existing_ids = {e["id"] for e in events}
                for pe in pipeline_events:
                    if pe["id"] not in existing_ids:
                        events.append(pe)
                events.sort(key=lambda e: e.get("created_at", ""))
                if len(events) > limit:
                    has_more = True
                    events = events[:limit]

        for event in events:
            if event.get("details") and isinstance(event["details"], str):
                try:
                    event["details"] = json.loads(event["details"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return {"events": events, "count": len(events), "has_more": has_more}
    finally:
        db.close()


def _find_related_pipeline_events(db, session_id: str, limit: int) -> list[dict]:
    """Find cold_pipeline/cli_extract events related to a session.

    Strategy: find the session's time range, then get pipeline events
    that occurred within a window after the session's last event.
    """
    session_range = db.fetchone(
        "SELECT MAX(created_at) AS end_at FROM hook_events WHERE session_id = ?",
        (session_id,),
    )
    if not session_range or not session_range["end_at"]:
        return []

    # Pipeline events typically run shortly after session end
    rows = db.fetchall(
        "SELECT * FROM hook_events "
        "WHERE source IN ('cold_pipeline', 'cli_extract') "
        "AND session_id IS NULL "
        "AND created_at >= datetime(?, '-1 minutes') "
        "AND created_at <= datetime(?, '+10 minutes') "
        "ORDER BY rowid ASC LIMIT ?",
        (session_range["end_at"], session_range["end_at"], limit),
    )
    return [dict(r) for r in rows]


@router.get("/timeline/sessions")
def get_timeline_sessions(limit: int = Query(50, ge=1, le=200)):
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rows = db.fetchall(
            "SELECT session_id, MAX(created_at) AS last_active, COUNT(*) AS event_count "
            "FROM hook_events WHERE session_id IS NOT NULL "
            "GROUP BY session_id ORDER BY last_active DESC LIMIT ?",
            (limit,),
        )
        return {"sessions": [dict(r) for r in rows]}
    finally:
        db.close()
