"""nokori stream — AI-friendly event stream output.

Dumps event history (default) or continuously follows new events (--follow).
Designed for AI agent consumption, not human viewing.
"""

from __future__ import annotations

import argparse
import json
import time

from ..config import Config
from ..db import open_db
from ..events.observability import query_events
from ..utils.time import local_hours_ago


def run(args: argparse.Namespace, cfg: Config) -> int:
    since = args.since
    if since is None:
        since = local_hours_ago(1)

    session_id = args.session or None
    source = args.type or None
    verbose = args.verbose
    limit = args.limit
    follow = args.follow

    if follow:
        return _follow_mode(cfg, since=since, session_id=session_id, source=source, verbose=verbose)
    return _dump_mode(
        cfg, since=since, session_id=session_id, source=source, verbose=verbose, limit=limit
    )


def _dump_mode(
    cfg: Config,
    *,
    since: str,
    session_id: str | None,
    source: str | None,
    verbose: bool,
    limit: int,
) -> int:
    db = open_db(cfg.db_path)
    try:
        events = query_events(db, session_id=session_id, source=source, since=since, limit=limit)
        for event in events:
            _print_event(event, verbose=verbose)
    finally:
        db.close()
    return 0


def _follow_mode(
    cfg: Config, *, since: str, session_id: str | None, source: str | None, verbose: bool
) -> int:
    last_id: str | None = None

    db = open_db(cfg.db_path)
    try:
        initial = query_events(db, session_id=session_id, source=source, since=since, limit=20)
        for event in initial:
            _print_event(event, verbose=verbose)
            last_id = event["id"]
    finally:
        db.close()

    last_seen_at = since
    try:
        while True:
            time.sleep(5)
            db = open_db(cfg.db_path)
            try:
                events = query_events(
                    db,
                    session_id=session_id,
                    source=source,
                    after_id=last_id,
                    since=since,
                    limit=50,
                )
                if not events and last_id is not None:
                    cursor_exists = db.fetchone(
                        "SELECT 1 FROM hook_events WHERE id = ?", (last_id,)
                    )
                    if cursor_exists is None:
                        last_id = None
                        events = query_events(
                            db,
                            session_id=session_id,
                            source=source,
                            since=last_seen_at,
                            limit=50,
                        )
                for event in events:
                    _print_event(event, verbose=verbose)
                    last_id = event["id"]
                    last_seen_at = event.get("created_at") or last_seen_at
            finally:
                db.close()
    except (KeyboardInterrupt, BrokenPipeError):
        return 0


def _print_event(event: dict, *, verbose: bool) -> None:
    if verbose:
        out = event
        details = event.get("details")
        if isinstance(details, str):
            try:
                out = {**event, "details": json.loads(details)}
            except (json.JSONDecodeError, TypeError):
                pass
        print(json.dumps(out, ensure_ascii=False))
    else:
        ts = event.get("created_at", "")
        source = event.get("source", "")
        outcome = event.get("outcome", "")
        session = event.get("session_id") or "-"
        snippet = event.get("prompt_snippet") or ""
        if snippet and len(snippet) > 60:
            snippet = snippet[:60] + "..."
        parts = [ts, source, outcome, f"session={session}"]
        if snippet:
            parts.append(f'"{snippet}"')
        print(" | ".join(parts))
