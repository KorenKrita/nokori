from __future__ import annotations

import json
import re

from ..config import Config
from ..db import open_db
from ..errors import DbError
from ..gate import marker as marker_io
from ..gate.blocker import format_block_reason, format_cursor_user_notice
from ..policy import RUNTIME_POLICY_VERSION
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


def _is_gate_eligible_rule(rule, db=None) -> bool:
    """Gate eligibility for marker rules, revalidated against current DB state."""
    row = None
    if db is not None:
        if getattr(rule, "rule_id", None):
            row = db.fetchone(
                "SELECT id, short_id, status, severity, rule_version, "
                "runtime_policy_version FROM rules WHERE id = ?",
                (rule.rule_id,),
            )
        if row is None and getattr(rule, "short_id", None):
            row = db.fetchone(
                "SELECT id, short_id, status, severity, rule_version, "
                "runtime_policy_version FROM rules WHERE short_id = ?",
                (rule.short_id,),
            )
    if row is not None:
        if row["status"] != "trusted" or row["severity"] != "gate_eligible":
            return False
        marker_version = getattr(rule, "rule_version", None)
        if marker_version is not None and int(row["rule_version"]) != marker_version:
            return False
        marker_policy = getattr(rule, "runtime_policy_version", None)
        if marker_policy and marker_policy != row["runtime_policy_version"]:
            return False
        return row["runtime_policy_version"] == RUNTIME_POLICY_VERSION
    # DB available but rule not found → rule was deleted/archived after marker creation
    if db is not None:
        return False
    # Degraded no-DB mode: trust marker attributes as last resort
    return (
        getattr(rule, "status", None) == "trusted"
        and getattr(rule, "severity", None) == "gate_eligible"
        and getattr(rule, "runtime_policy_version", None) == RUNTIME_POLICY_VERSION
    )


def _has_tool_evidence(rule, payload: dict) -> bool:
    """Require tool evidence when tool input is inspectable.

    If the tool input is not inspectable (no input field), we allow
    the gate to proceed without tool evidence (prompt-only gate).
    """
    tool_input = payload.get("tool_input") or payload.get("input")
    if not tool_input:
        # No inspectable tool input -- prompt-only gate is valid
        return True
    if isinstance(tool_input, str):
        haystack = tool_input.lower()
    else:
        haystack = json.dumps(tool_input, ensure_ascii=False, sort_keys=True).lower()

    trigger = getattr(rule, "trigger", "") or ""
    action = getattr(rule, "action", "") or ""
    for phrase in (trigger, action):
        phrase = phrase.strip().lower()
        if phrase and phrase in haystack:
            return True

    tokens = {
        t
        for t in re.findall(r"[a-z0-9_+-]{4,}", f"{trigger} {action}".lower())
        if t not in {"the", "and", "for", "with", "before", "after", "rule",
                     "when", "that", "this", "from", "into", "also", "have",
                     "been", "will", "should", "must", "always", "never"}
    }
    if not tokens:
        return False
    # Word-boundary match to avoid substring false positives in paths/identifiers
    hits = {t for t in tokens if re.search(r'\b' + re.escape(t) + r'\b', haystack)}
    return len(hits) >= max(1, len(tokens) // 2)


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

        # Filter to only gate-eligible rules whose prompt evidence still matches
        # inspectable tool input. If input is unrelated, keep marker for a later
        # tool call in this turn instead of consuming it.
        gate_rules = [
            r for r in marker.rules
            if _is_gate_eligible_rule(r, db) and _has_tool_evidence(r, payload)
        ]
        if not gate_rules:
            return {}
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
