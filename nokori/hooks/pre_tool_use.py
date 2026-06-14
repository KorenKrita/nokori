from __future__ import annotations

from ..config import Config
from ..gate import marker as marker_io
from ..gate.blocker import format_block_reason, format_cursor_user_notice
from ..gate.engine import GateEngine, tool_matches_gate
from ..gate.marker import PromptHashResolver
from ..utils.hook_diag import log_diag
from ..utils.hook_response import pre_tool_deny_response
from ..utils.host import Host, effective_gate_matcher, effective_session_id
from ..utils.logging import get_logger
from .context import HotPathContext
from .cursor_deferred import maybe_deferred_pre_tool_use

log = get_logger("nokori.hooks.pre_tool_use")


def _run_gate(ctx: HotPathContext) -> tuple[dict, str, list[str]]:
    """Run gate logic. Returns (response_dict, outcome_reason, blocked_short_ids) for observability."""
    payload = ctx.payload
    cfg = ctx.cfg
    host = ctx.host
    session_id = ctx.session_id
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
        "[diag] pre_tool_use gate_check tool=%s host=%s matcher=%s matched=%s cfg_gate_matcher=%s",
        tool_name or "-",
        host.value,
        gate_matcher,
        matched,
        cfg.gate_matcher,
    )
    if not matched:
        return {}, "passed_tool_not_matched", []

    if ctx.db is None:
        return {}, "passed_db_open_failed", []

    prompt_raw = payload.get("prompt")
    has_prompt_text = isinstance(prompt_raw, str) and prompt_raw.strip()
    on_disk = None if has_prompt_text else marker_io.read_latest_marker(cfg, session_id)
    resolver = PromptHashResolver(cfg, session_id, ctx.db)
    current_ph, ph_source = resolver.resolve(payload, on_disk)

    if not current_ph:
        marker_io.delete_session(cfg, session_id)
        return {}, "passed_no_prompt_hash", []

    engine = GateEngine(cfg, ctx.db)
    decision = engine.should_block(
        tool_name=tool_name,
        prompt_hash=current_ph,
        session_id=session_id,
        payload=payload,
        gate_matcher=gate_matcher,
    )

    if decision.state is not None:
        ctx.record_event(
            "gate_marker_resolved",
            decision.state.value,
            details={
                "tool_name": tool_name,
                "prompt_hash": current_ph,
                "prompt_hash_source": ph_source,
                "rules_checked": decision.rules_checked,
                "rules_blocked": decision.rules_blocked,
                "elapsed_ms": decision.elapsed_ms,
                "deferred": decision.deferred,
            },
        )

    if not decision.blocked:
        if decision.reason == "no_marker":
            marker_io.prune_stale_markers(cfg, session_id, current_ph)
        return {}, f"passed_{decision.reason}", []

    blocked_rules = list(decision.rules)
    reason = format_block_reason(blocked_rules, dismiss_phrase=cfg.dismiss_phrase)
    log.info(
        "gate blocked tool session=%s rules=%s",
        session_id,
        ",".join(r.short_id for r in blocked_rules),
    )
    short_ids = sorted({r.short_id for r in blocked_rules})
    if host.value == "cursor":
        user_note = format_cursor_user_notice(
            tool_name=tool_name or "tool",
            rule_short_ids=short_ids,
            dismiss_phrase=cfg.dismiss_phrase,
            deferred=decision.deferred,
        )
        return (
            pre_tool_deny_response(
                host,
                reason,
                user_message=user_note,
                agent_message=reason,
            ),
            "blocked",
            short_ids,
        )
    return pre_tool_deny_response(host, reason), "blocked", short_ids


def handle(payload: dict, cfg: Config, *, host: Host) -> dict:  # type: ignore[return]
    session_id = effective_session_id(payload)
    tool_name = payload.get("tool_name") or payload.get("tool")

    with HotPathContext(payload, cfg, host=host, session_id=session_id) as ctx:
        deferred = maybe_deferred_pre_tool_use(
            payload,
            cfg,
            session_id,
            tool_name,
            host,
            db=ctx.db,
        )
        if deferred is not None:
            ctx.record_event(
                "pre_tool_use",
                "deferred",
                details={"tool_name": tool_name},
            )
            return deferred

        response, outcome, blocked_ids = _run_gate(ctx)

        details: dict = {"tool_name": tool_name}
        if blocked_ids:
            details["blocked_by"] = blocked_ids
        ctx.record_event("pre_tool_use", outcome, details=details)

        return response
