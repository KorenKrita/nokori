from __future__ import annotations

import re

from ..config import Config
from ..db import open_db
from ..errors import GateMarkerError
from ..gate import marker as marker_io
from ..gate.blocker import format_block_reason
from ..utils.logging import get_logger

log = get_logger("nokori.hooks.pre_tool_use")


def _tool_matches_gate(tool_name: str | None, matcher: str) -> bool:
    if not tool_name or not matcher:
        return False
    try:
        return bool(re.fullmatch(matcher, tool_name))
    except re.error:
        log.warning("invalid gate matcher %r; skipping gate for this tool", matcher)
        return False


def handle(payload: dict, cfg: Config) -> dict:
    if not cfg.gate_enabled:
        return {"continue": True}

    tool_name = payload.get("tool_name")
    if not _tool_matches_gate(tool_name, cfg.gate_matcher):
        return {"continue": True}

    session_id = payload.get("session_id") or "-"

    try:
        marker = marker_io.read(cfg, session_id)
    except GateMarkerError as e:
        log.warning("gate marker unreadable, removing: %s", e)
        marker_io.delete(cfg, session_id)
        return {"continue": True}

    if marker is None:
        return {"continue": True}

    if marker_io.is_expired(marker, cfg.gate_ttl_seconds):
        marker_io.delete(cfg, session_id)
        return {"continue": True}

    if not marker.rules:
        marker_io.delete(cfg, session_id)
        return {"continue": True}

    db = open_db(cfg.db_path)
    try:
        current_ph = marker_io.resolve_current_prompt_hash(payload, db, session_id)
        if not marker_io.prompt_hash_matches(marker, current_ph):
            log.info(
                "gate marker stale (prompt_hash mismatch), clearing session=%s",
                session_id,
            )
            marker_io.delete(cfg, session_id)
            return {"continue": True}
    finally:
        db.close()

    marker_io.delete(cfg, session_id)

    reason = format_block_reason(marker.rules, dismiss_phrase=cfg.dismiss_phrase)
    log.info(
        "gate blocked tool session=%s rules=%s",
        session_id, ",".join(r.short_id for r in marker.rules),
    )
    # PreToolUse: use hookSpecificOutput only (top-level decision/reason are deprecated).
    # https://code.claude.com/docs/en/hooks — PreToolUse decision control
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }
