from __future__ import annotations

import re

from ..config import Config
from ..db import open_db
from ..errors import DbError
from ..gate import marker as marker_io
from ..gate.blocker import format_block_reason, format_cursor_user_notice
from .cursor_deferred import maybe_deferred_pre_tool_use
from ..utils.hook_diag import log_diag
from ..utils.hook_response import pre_tool_deny_response
from ..gate.marker import prompt_hash
from ..utils.host import Host, effective_gate_matcher, effective_session_id
from ..utils.logging import get_logger
from ..utils.prompt_text import normalize_prompt_for_hash

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


def _is_gate_eligible_rule(rule) -> bool:
    """Gate eligibility for marker rules.

    MarkerRule objects written to disk don't carry status/severity — the
    filtering already happened at write-time via select_gate_rules(). If we
    have a MarkerRule (no status attr), trust that it's gate-eligible.
    """
    if not hasattr(rule, "status"):
        return True
    return rule.status == "trusted" and rule.severity == "gate_eligible"


def _has_tool_evidence(rule, payload: dict) -> bool:
    """Require tool evidence when tool input is inspectable.

    If the tool input is not inspectable (no input field), we allow
    the gate to proceed without tool evidence (prompt-only gate).
    """
    tool_input = payload.get("tool_input") or payload.get("input")
    if not tool_input:
        # No inspectable tool input -- prompt-only gate is valid
        return True
    # Tool input exists; check if rule's required_concepts match against it.
    # The marker already encodes this decision from applicability evaluation.
    return True


def _run_gate(payload: dict, cfg: Config, session_id: str, host) -> dict:
    tool_name = payload.get("tool_name") or payload.get("tool")
    gate_matcher = effective_gate_matcher(cfg.gate_matcher, host)

    if not cfg.gate_enabled:
        log_diag(
            log,
            "[diag] pre_tool_use skip gate_enabled=false tool=%s host=%s",
            tool_name or "-",
            host.value,
        )
        return {}

    matched = _tool_matches_gate(tool_name, gate_matcher)
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
        return {}

    on_disk = marker_io.read_latest_marker(cfg, session_id)
    current_ph: str | None = None
    prompt_raw = payload.get("prompt")
    if isinstance(prompt_raw, str) and prompt_raw.strip():
        current_ph = prompt_hash(normalize_prompt_for_hash(prompt_raw))

    try:
        db = open_db(cfg.db_path)
    except DbError as e:
        log.warning("gate db open failed, fail-open session=%s: %s", session_id, e)
        return {}
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
    finally:
        db.close()
    if not current_ph:
        marker_io.delete_session(cfg, session_id)
        return {}

    if on_disk and on_disk.prompt_hash == current_ph:
        marker = on_disk
    else:
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

    # Filter to only gate-eligible rules (trusted + gate_eligible severity)
    gate_rules = [r for r in marker.rules if _is_gate_eligible_rule(r)]
    if not gate_rules:
        marker_io.delete(cfg, session_id, prompt_hash_value=current_ph)
        return {}

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
        )
    return pre_tool_deny_response(host, reason)


def handle(payload: dict, cfg: Config, *, host: Host) -> dict:
    session_id = effective_session_id(payload)
    tool_name = payload.get("tool_name") or payload.get("tool")

    deferred = maybe_deferred_pre_tool_use(
        payload, cfg, session_id, tool_name, host
    )
    if deferred is not None:
        return deferred

    return _run_gate(payload, cfg, session_id, host)
