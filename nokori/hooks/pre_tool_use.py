from __future__ import annotations

import json
import re

from ..config import Config
from ..db import open_db
from ..errors import DbError
from ..events.observability import write_event
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


def _is_gate_eligible_rule(rule, db=None) -> tuple[bool, list | None]:
    """Gate eligibility for marker rules, revalidated against current DB state.

    Returns (eligible, excluded_contexts) so callers can reuse excluded_contexts
    without a separate DB query.
    """
    row = None
    if db is not None:
        if getattr(rule, "rule_id", None):
            row = db.fetchone(
                "SELECT id, short_id, status, severity, rule_version, "
                "runtime_policy_version, excluded_contexts FROM rules WHERE id = ?",
                (rule.rule_id,),
            )
        if row is None and getattr(rule, "short_id", None):
            row = db.fetchone(
                "SELECT id, short_id, status, severity, rule_version, "
                "runtime_policy_version, excluded_contexts FROM rules WHERE short_id = ?",
                (rule.short_id,),
            )
    if row is not None:
        if row["status"] != "trusted" or row["severity"] != "gate_eligible":
            return False, None
        marker_version = getattr(rule, "rule_version", None)
        if marker_version is not None and int(row["rule_version"]) != marker_version:
            return False, None
        marker_policy = getattr(rule, "runtime_policy_version", None)
        if marker_policy and marker_policy != row["runtime_policy_version"]:
            return False, None
        from ..db import loads_json
        excluded_contexts = loads_json(row["excluded_contexts"], []) if row["excluded_contexts"] else []
        eligible = row["runtime_policy_version"] == RUNTIME_POLICY_VERSION
        return eligible, excluded_contexts if eligible else None
    # DB available but rule not found → rule was deleted/archived after marker creation
    if db is not None:
        return False, None
    # Degraded no-DB mode: trust marker attributes as last resort
    eligible = (
        getattr(rule, "status", None) == "trusted"
        and getattr(rule, "severity", None) == "gate_eligible"
        and getattr(rule, "runtime_policy_version", None) == RUNTIME_POLICY_VERSION
    )
    return eligible, None


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
        return True
    # Truncate haystack to prevent O(tokens × haystack) on very large tool inputs
    haystack = haystack[:8000]
    # Simple containment check — precision loss acceptable for fuzzy relevance
    hits = {t for t in tokens if t in haystack}
    return len(hits) >= max(1, len(tokens) // 2)


def _tool_input_exclusion_fires(rule, payload: dict, excluded_contexts: list | None) -> bool:
    """Check if any tool_input_only excluded_context fires against tool input.

    At gate-marker creation time, tool_input_only exclusions cannot fire (no tool_input yet).
    Re-evaluate them now that tool_input is available.

    excluded_contexts is pre-fetched by _is_gate_eligible_rule to avoid N+1 queries.
    """
    tool_input = payload.get("tool_input") or payload.get("input")
    if not tool_input:
        return False

    if not excluded_contexts:
        return False

    rule_id = getattr(rule, "rule_id", None) or getattr(rule, "short_id", None)

    if isinstance(tool_input, str):
        haystack = tool_input.lower()
    else:
        haystack = json.dumps(tool_input, ensure_ascii=False).lower()

    from ..matcher.compiler import CompilationError, _compile_excluded_context
    from ..matcher.runtime import _excluded_context_matches

    for ctx in excluded_contexts:
        if ctx.get("scope") != "tool_input_only":
            continue
        try:
            compiled = _compile_excluded_context(ctx)
        except (CompilationError, TypeError, AttributeError) as exc:
            log.warning("invalid tool_input_only exclusion for gate rule %s: %s", rule_id, exc)
            continue
        if _excluded_context_matches(compiled, haystack):
            return True
    return False


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
            eligible, excluded_contexts = _is_gate_eligible_rule(r, db)
            if not eligible:
                continue
            if not _has_tool_evidence(r, payload):
                continue
            if _tool_input_exclusion_fires(r, payload, excluded_contexts):
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
    tool_name = payload.get("tool_name") or payload.get("tool")
    details: dict = {"tool_name": tool_name}
    if blocked_by:
        details["blocked_by"] = blocked_by
    try:
        db = open_db(cfg.db_path)
    except Exception as e:
        log.warning("pre_tool_use observability db open failed: %s", e)
        return
    try:
        write_event(
            db, source="pre_tool_use", session_id=session_id,
            outcome=outcome,
            details=details,
        )
    finally:
        db.close()


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
