from __future__ import annotations

import re

from ..config import Config
from ..db import open_db
from ..errors import DbError
from ..gate import marker as marker_io
from ..gate.blocker import format_block_reason
from ..utils.logging import get_logger

log = get_logger("nokori.hooks.pre_tool_use")


def _compiled_gate_matcher(matcher: str) -> re.Pattern[str] | None:
    try:
        return re.compile(matcher)
    except re.error:
        return None


def _tool_matches_gate(tool_name: str | None, matcher: str) -> bool:
    if not tool_name or not matcher:
        return False
    pattern = _compiled_gate_matcher(matcher)
    if pattern is None:
        log.warning("invalid gate matcher %r; skipping gate for this tool", matcher)
        return False
    return bool(pattern.fullmatch(tool_name))


def handle(payload: dict, cfg: Config) -> dict:
    if not cfg.gate_enabled:
        return {}

    tool_name = payload.get("tool_name")
    if not _tool_matches_gate(tool_name, cfg.gate_matcher):
        return {}

    session_id = payload.get("session_id") or "-"

    text_ph = marker_io.resolve_current_prompt_hash(payload, cfg, session_id)
    on_disk = marker_io.read_latest_marker(cfg, session_id)
    current_ph = text_ph
    if not current_ph:
        try:
            db = open_db(cfg.db_path)
        except DbError as e:
            log.warning("gate db open failed, fail-open session=%s: %s", session_id, e)
            return {}
        try:
            if on_disk and on_disk.rules and not current_ph:
                if marker_io.injection_exists(db, session_id, on_disk.prompt_hash):
                    current_ph = on_disk.prompt_hash
                else:
                    marker_io.delete(
                        cfg, session_id, prompt_hash_value=on_disk.prompt_hash,
                    )
            if not current_ph:
                current_ph = marker_io.resolve_current_prompt_hash(
                    payload, cfg, session_id, db=db,
                )
        finally:
            db.close()
    if not current_ph:
        marker_io.delete_session(cfg, session_id)
        return {}

    marker = marker_io.read(cfg, session_id, prompt_hash_value=current_ph)
    if marker is None:
        marker_io.prune_stale_markers(cfg, session_id, current_ph)
        return {}

    if marker_io.is_expired(marker, cfg.gate_ttl_seconds):
        marker_io.delete(cfg, session_id, prompt_hash_value=current_ph)
        return {}

    if not marker.rules:
        marker_io.delete(cfg, session_id, prompt_hash_value=current_ph)
        return {}

    if not marker_io.prompt_hash_matches(marker, current_ph, session_id=session_id):
        marker_io.delete(cfg, session_id, prompt_hash_value=current_ph)
        return {}

    marker_io.delete(cfg, session_id, prompt_hash_value=current_ph)

    reason = format_block_reason(marker.rules, dismiss_phrase=cfg.dismiss_phrase)
    log.info(
        "gate blocked tool session=%s rules=%s",
        session_id, ",".join(r.short_id for r in marker.rules),
    )
    # continue=True is required by Claude Code; tool denial is via permissionDecision.
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }
