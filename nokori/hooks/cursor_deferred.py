"""Cursor preToolUse path when beforeSubmitPrompt did not run for the latest user turn."""

from __future__ import annotations

from ..config import Config
from ..db import Db, open_db
from ..errors import DbError
from ..extract.reader import read_tail_user_turns
from ..gate import prompt_ack
from ..gate.blocker import (
    format_cursor_agent_delivery,
    format_cursor_user_notice,
    select_gate_rules,
)
from ..gate.marker import MarkerRule, prompt_hash
from ..utils import sessions
from ..utils.hook_response import pre_tool_deny_response
from ..utils.host import Host
from ..utils.logging import get_logger
from ..utils.prompt_text import normalize_prompt_for_hash
from ..utils.transcript import resolve_transcript_path, transcript_resolve_failure_reason
from .prompt_inject import RetrieveFailed, build_decision_features, inject_for_prompt

log = get_logger("nokori.hooks.cursor_deferred")


def _prompt_from_transcript(
    payload: dict,
    *,
    session_id: str,
    tool_name: str | None,
) -> tuple[str, str] | None:
    path = resolve_transcript_path(payload)
    if path is None:
        log.warning(
            "cursor deferred skipped (%s) session=%s tool=%s",
            transcript_resolve_failure_reason(payload),
            session_id,
            tool_name or "-",
        )
        return None
    turns = read_tail_user_turns(path, 1)
    if not turns:
        return None
    normalized = normalize_prompt_for_hash(turns[0].content)
    if not normalized:
        return None
    return normalized, prompt_hash(normalized)


def _generation_id_from_payload(payload: dict) -> str:
    raw = payload.get("generation_id")
    if isinstance(raw, str):
        return raw.strip()
    return ""


def maybe_deferred_pre_tool_use(
    payload: dict,
    cfg: Config,
    session_id: str,
    tool_name: str | None,
    host: Host,
    *,
    db: Db | None = None,
) -> dict | None:
    """Deliver rules via deny+agent_message when the latest user turn skipped submit hook.

    Returns a hook response dict when this path handled the event; None to continue
    with standard gate-only handling.

    If *db* is provided it will be used instead of opening a new connection (caller
    is responsible for closing it).
    """
    if host != Host.CURSOR:
        return None

    resolved = _prompt_from_transcript(
        payload,
        session_id=session_id,
        tool_name=tool_name,
    )
    if resolved is None:
        return None
    prompt_text, ph = resolved

    if prompt_ack.exists(cfg, session_id, ph):
        return None

    generation_id = _generation_id_from_payload(payload)
    if not prompt_ack.try_claim_deferred(cfg, session_id, generation_id, ph):
        return None

    project_id = sessions.resolve_project_id_for_session(
        cfg,
        session_id,
        payload.get("cwd"),
    )

    owns_db = db is None
    if owns_db:
        try:
            db = open_db(cfg.db_path)
        except DbError as e:
            log.warning(
                "cursor deferred db open failed (claimed but abandoned) session=%s: %s",
                session_id,
                e,
            )
            return None
    try:
        try:
            outcome = inject_for_prompt(
                db,
                cfg,
                session_id=session_id,
                prompt=prompt_text,
                project_id=project_id,
                turn_index=payload.get("turn_index"),
            )
        except RetrieveFailed as e:
            log.warning("cursor deferred retrieve failed (%s); fail-open", e)
            return None

        if outcome is None:
            return None

        hot, warm = outcome.hot, outcome.warm
        text = outcome.text
        gate_hot = select_gate_rules(hot)

        if not text and not gate_hot:
            return None

        marker_rules = [
            MarkerRule(
                short_id=r.rule.short_id,
                action=r.rule.action_instruction,
                trigger=r.rule.trigger_canonical,
                source_type=r.rule.source_origin,
                rule_id=r.rule.id,
                status=r.rule.status,
                severity=r.rule.severity,
                rule_version=r.rule.rule_version,
                runtime_policy_version=r.runtime_policy_version,
                trigger_idf_pool_version=r.trigger_idf_pool_version,
                embedding_profile_version=r.embedding_profile_version,
                decision_features=build_decision_features(r),
            )
            for r in gate_hot
        ]
        agent_body = format_cursor_agent_delivery(
            text, marker_rules, dismiss_phrase=cfg.dismiss_phrase
        )
        if not agent_body:
            return None

        short_ids = sorted({r.rule.short_id for r in hot + warm})
        user_note = format_cursor_user_notice(
            tool_name=tool_name or "tool",
            rule_short_ids=short_ids,
            dismiss_phrase=cfg.dismiss_phrase,
            deferred=True,
        )
        log.info(
            "cursor deferred deny tool=%s session=%s ph=%s hot=%d warm=%d",
            tool_name or "-",
            session_id,
            ph[:8],
            len(hot),
            len(warm),
        )
        return pre_tool_deny_response(
            host,
            agent_body,
            user_message=user_note,
            agent_message=agent_body,
        )
    finally:
        if owns_db:
            db.close()
