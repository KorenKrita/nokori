from __future__ import annotations

import re

from ..config import Config
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

    marker_io.delete(cfg, session_id)

    if not marker.rules:
        return {"continue": True}

    reason = format_block_reason(marker.rules, dismiss_phrase=cfg.dismiss_phrase)
    log.info(
        "gate blocked tool session=%s rules=%s",
        session_id, ",".join(r.short_id for r in marker.rules),
    )
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "decision": "block",
        "reason": reason,
    }
