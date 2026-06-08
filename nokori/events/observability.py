"""Observability event recording — structured replacement for scattered log files.

Provides write_event() and write_error() with fail-open semantics (never raises).
Events are retained for 30 days and cleaned by maintenance sweep.
"""
from __future__ import annotations

import uuid
from typing import Any

from ..db import Db, dumps_json
from ..utils.logging import get_logger
from ..utils.time import now_iso

log = get_logger("nokori.events.observability")

OBSERVABILITY_RETENTION_DAYS = 30
OBSERVABILITY_CLEANUP_INTERVAL_DAYS = 7


def write_event(
    db: Db,
    source: str,
    outcome: str | None = None,
    *,
    session_id: str | None = None,
    prompt_snippet: str | None = None,
    details: dict[str, Any] | None = None,
) -> str | None:
    """Write a decision event to hook_events. Returns event ID or None on failure.

    Never raises — all errors are logged and swallowed (fail-open).
    """
    event_id = str(uuid.uuid4())
    now = now_iso()
    try:
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO hook_events "
                "(id, session_id, source, outcome, prompt_snippet, details, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    session_id,
                    source,
                    outcome,
                    prompt_snippet,
                    dumps_json(details) if details is not None else None,
                    now,
                ),
            )
        return event_id
    except Exception as e:
        log.warning("write_event failed source=%s: %s", source, e)
        return None


def write_error(
    db: Db,
    source: str,
    role: str,
    error_type: str,
    message: str | None = None,
    *,
    session_id: str | None = None,
    model_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> str | None:
    """Write an error event to error_events. Returns event ID or None on failure.

    Never raises — all errors are logged and swallowed (fail-open).
    """
    event_id = str(uuid.uuid4())
    now = now_iso()
    try:
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO error_events "
                "(id, session_id, source, role, model_id, error_type, message, details, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    session_id,
                    source,
                    role,
                    model_id,
                    error_type,
                    message,
                    dumps_json(details) if details is not None else None,
                    now,
                ),
            )
        return event_id
    except Exception as e:
        log.warning("write_error failed source=%s role=%s: %s", source, role, e)
        return None


def query_events(
    db: Db,
    *,
    session_id: str | None = None,
    source: str | None = None,
    after_id: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query hook_events with optional filters. Returns list of dicts, oldest first."""
    where = []
    params: list = []

    if session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)
    if source is not None:
        where.append("source = ?")
        params.append(source)
    if since is not None:
        where.append("created_at >= ?")
        params.append(since)
    if after_id is not None:
        where.append("rowid > (SELECT rowid FROM hook_events WHERE id = ?)")
        params.append(after_id)

    sql = "SELECT * FROM hook_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rowid ASC LIMIT ?"
    params.append(limit)

    rows = db.fetchall(sql, tuple(params))
    return [dict(r) for r in rows]


def query_events_latest(
    db: Db,
    *,
    session_id: str | None = None,
    source: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query the most recent hook_events. Returns list of dicts, oldest first."""
    where = []
    params: list = []

    if session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)
    if source is not None:
        where.append("source = ?")
        params.append(source)

    sql = "SELECT * FROM hook_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rowid DESC LIMIT ?"
    params.append(limit)

    rows = db.fetchall(sql, tuple(params))
    return [dict(r) for r in reversed(rows)]


def query_errors(
    db: Db,
    *,
    group_by: str = "role",
    session_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Aggregate error_events by a dimension. Returns list of dicts with count."""
    column_map = {"role": "role", "model_id": "model_id", "error_type": "error_type", "source": "source"}
    group_col = column_map.get(group_by, "role")

    where = []
    params: list = []

    if session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)
    if since is not None:
        where.append("created_at >= ?")
        params.append(since)
    if until is not None:
        where.append("created_at <= ?")
        params.append(until)

    sql = f"SELECT {group_col}, COUNT(*) AS count FROM error_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" GROUP BY {group_col} ORDER BY count DESC"

    rows = db.fetchall(sql, tuple(params))
    return [dict(r) for r in rows]
