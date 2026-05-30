from __future__ import annotations

import re

from ..config import Config
from ..gate import marker as marker_io
from ..gate.blocker import format_block_reason
from ..utils.logging import get_logger

log = get_logger("nokori.hooks.pre_tool_use")


def _tool_matches_gate(tool_name: str | None, matcher: str) -> bool:
    if not tool_name or not matcher:
        return True
    return bool(re.fullmatch(matcher, tool_name))


def handle(payload: dict, cfg: Config) -> dict:
    if not cfg.gate_enabled:
        return {}

    tool_name = payload.get("tool_name")
    if not _tool_matches_gate(tool_name, cfg.gate_matcher):
        return {}

    session_id = payload.get("session_id") or "-"

    marker = marker_io.read(cfg, session_id)
    if marker is None:
        return {}

    if marker_io.is_expired(marker, cfg.gate_ttl_seconds):
        marker_io.delete(cfg, session_id)
        return {}

    marker_io.delete(cfg, session_id)

    if not marker.rules:
        return {}

    reason = format_block_reason(marker.rules, dismiss_phrase=cfg.dismiss_phrase)
    log.info(
        "gate blocked tool session=%s rules=%s",
        session_id, ",".join(r.short_id for r in marker.rules),
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "decision": "block",
        "reason": reason,
    }
