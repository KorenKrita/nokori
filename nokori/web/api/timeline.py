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
            events = query_events_latest(
                db, session_id=session_id, source=source, limit=limit
            )
            has_more = False
        else:
            events = query_events(
                db, session_id=session_id, source=source, after_id=after_id, limit=limit + 1
            )
            has_more = len(events) > limit
            if has_more:
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
