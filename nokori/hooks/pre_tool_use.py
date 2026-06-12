from __future__ import annotations

from ..config import Config
from ..db import open_db
from ..errors import DbError
from ..events.observability import write_event
from ..gate import marker as marker_io
from ..gate.blocker import format_block_reason, format_cursor_user_notice
from ..gate.engine import (
    is_gate_eligible_rule,
    has_tool_evidence,
    tool_input_exclusion_fires,
    tool_matches_gate,
)
from .cursor_deferred import maybe_deferred_pre_tool_use
from ..utils.hook_diag import log_diag
from ..utils.hook_response import pre_tool_deny_response
from ..gate.marker import prompt_hash
from ..utils.host import Host, effective_gate_matcher, effective_session_id
from ..utils.logging import get_logger
from ..utils.prompt_text import normalize_prompt_for_hash

log = get_logger("nokori.hooks.pre_tool_use")


def _run_gate(payload: dict, cfg: Config, session_id: str, host) -> tuple[dict, str, list[str]]:
    """Run gate logic. Returns (response_dict, outcome_reason, blocked_short_ids) for observability."""
    tool_name = payload.get("tool_name") or payload.get("tool")
    gate_matcher = effective_gate_matcher(cfg.gate_matcher, host)

    if not cfg.gate_enabled:
        log_diag(
            log,
            "[diag] pre_tool_use skip gate_enabled=false tool=%s host=%s",
            tool_name or "-",
            host.value,
        )
        return {}, "passed_gate_disabled", []

    matched = tool_matches_gate(tool_name, gate_matcher)
    log_diag(
        log,
        "[diag] pre_tool_use gate_check tool=%s host=%s matcher=%s matched=%s "
        "cfg_gate_matcher=%s",
        tool_name or "-",
        host.value,
        gate_matcher,
        matched,
        cfg.gate_matcher,
    )
    if not matched:
        return {}, "passed_tool_not_matched", []

    on_disk = marker_io.read_latest_marker(cfg, session_id)
    current_ph: str | None = None
    prompt_raw = payload.get("prompt")
    if isinstance(prompt_raw, str) and prompt_raw.strip():
        current_ph = prompt_hash(normalize_prompt_for_hash(prompt_raw))

    try:
        db = open_db(cfg.db_path)
    except DbError as e:
        log.warning("gate db open failed, fail-open session=%s: %s", session_id, e)
        return {}, "passed_db_open_failed", []
    try:
        if not current_ph:
            if on_disk and on_disk.rules:
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
        if not current_ph:
            marker_io.delete_session(cfg, session_id)
            return {}, "passed_no_prompt_hash", []

        if on_disk and on_disk.prompt_hash == current_ph:
            marker = on_disk
        else:
            marker = marker_io.read(cfg, session_id, prompt_hash_value=current_ph)
        if marker is None:
            marker_io.prune_stale_markers(cfg, session_id, current_ph)
            return {}, "passed_no_marker", []

        if marker_io.is_expired(marker, cfg.gate_ttl_seconds):
            marker_io.delete(cfg, session_id, prompt_hash_value=current_ph)
            return {}, "passed_marker_expired", []

        if not marker.rules:
            marker_io.delete(cfg, session_id, prompt_hash_value=current_ph)
            return {}, "passed_empty_marker", []

        if not marker_io.prompt_hash_matches(marker, current_ph, session_id=session_id):
            marker_io.delete(cfg, session_id, prompt_hash_value=current_ph)
            return {}, "passed_hash_mismatch", []

        # Filter to only gate-eligible rules whose prompt evidence still matches
        # inspectable tool input AND whose tool_input_only exclusions don't fire.
        gate_rules = []
        for r in marker.rules:
            eligible, excluded_contexts = is_gate_eligible_rule(r, db)
            if not eligible:
                continue
            if not has_tool_evidence(r, payload):
                continue
            if tool_input_exclusion_fires(r, payload, excluded_contexts):
                continue
            gate_rules.append(r)
        if not gate_rules:
            return {}, "passed_no_eligible_rules", []
    finally:
        db.close()

    marker_io.delete(cfg, session_id, prompt_hash_value=current_ph)

    reason = format_block_reason(gate_rules, dismiss_phrase=cfg.dismiss_phrase)
    log.info(
        "gate blocked tool session=%s rules=%s",
        session_id, ",".join(r.short_id for r in gate_rules),
    )
    short_ids = sorted({r.short_id for r in gate_rules})
    if host.value == "cursor":
        user_note = format_cursor_user_notice(
            tool_name=tool_name or "tool",
            rule_short_ids=short_ids,
            dismiss_phrase=cfg.dismiss_phrase,
            deferred=False,
        )
        return pre_tool_deny_response(
            host, reason, user_message=user_note, agent_message=reason,
        ), "blocked", short_ids
    return pre_tool_deny_response(host, reason), "blocked", short_ids


def _write_pre_tool_event(cfg: Config, session_id: str, payload: dict, outcome: str, blocked_by: list[str] | None = None) -> None:
    """Write pre_tool_use observability event. Opens its own DB connection (fail-open)."""
    try:
        tool_name = payload.get("tool_name") or payload.get("tool")
        details: dict = {"tool_name": tool_name}
        if blocked_by:
            details["blocked_by"] = blocked_by
        db = open_db(cfg.db_path)
        try:
            write_event(
                db, source="pre_tool_use", session_id=session_id,
                outcome=outcome,
                details=details,
            )
        finally:
            db.close()
    except Exception as e:
        log.warning("pre_tool_use observability failed: %s", e)


def handle(payload: dict, cfg: Config, *, host: Host) -> dict:
    session_id = effective_session_id(payload)
    tool_name = payload.get("tool_name") or payload.get("tool")

    deferred = maybe_deferred_pre_tool_use(
        payload, cfg, session_id, tool_name, host
    )
    if deferred is not None:
        _write_pre_tool_event(cfg, session_id, payload, "deferred")
        return deferred

    response, outcome, blocked_ids = _run_gate(payload, cfg, session_id, host)
    _write_pre_tool_event(cfg, session_id, payload, outcome, blocked_by=blocked_ids or None)
    return response
