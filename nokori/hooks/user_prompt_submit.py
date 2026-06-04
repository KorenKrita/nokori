from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..config import Config
from ..db import (
    Db,
    archive_rule,
    find_rule_id_by_recent_injection,
    find_rule_id_injected_since,
    open_db,
)
from ..gate import marker as marker_io
from ..gate import prompt_ack
from ..gate.blocker import select_gate_rules
from ..gate.marker import MarkerRule, prompt_hash
from ..utils.prompt_text import normalize_prompt_for_hash
from ..utils import sessions
from ..utils.hook_response import user_prompt_submit_response
from ..utils.host import Host, effective_session_id
from ..utils.logging import get_logger
from ..utils.time import iso_of, now_iso
from .prompt_inject import RetrieveFailed, inject_for_prompt

log = get_logger("nokori.hooks.user_prompt_submit")


def _dismiss_re(phrase: str) -> re.Pattern[str]:
    escaped = re.escape(phrase.lower())
    return re.compile(
        rf"(?i)(?<![a-z]){escaped}[\s,，、;:：]+(?P<sid>[a-f0-9]{{6,32}})\b"
    )


def _run_dismiss(db: Db, prompt: str, session_id: str, cfg: Config) -> int:
    """Returns number of rules archived via inline dismiss in this prompt."""
    phrase = (cfg.dismiss_phrase or "dismiss").lower()
    pattern = _dismiss_re(phrase)
    count = 0
    seen_sids: set[str] = set()
    now = now_iso()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_iso = iso_of(cutoff)
    for m in pattern.finditer(prompt or ""):
        sid = m.group("sid").lower()
        if sid in seen_sids:
            continue
        seen_sids.add(sid)
        if session_id in (None, "", "-"):
            rid = find_rule_id_injected_since(db, sid, cutoff_iso)
        else:
            rid = find_rule_id_by_recent_injection(db, session_id, sid, cutoff_iso)
        if rid is None:
            continue
        archive_rule(db, rid, "user_dismissed_prompt", now)
        marker_io.strip_short_id_from_all_markers(cfg, sid)
        log.info("rule dismissed via prompt short=%s session=%s", sid, session_id)
        count += 1
    return count


def _update_gate_marker(
    cfg: Config, session_id: str, prompt: str, hot, ph: str
) -> None:
    if not cfg.gate_enabled:
        return
    gate_rules = select_gate_rules(hot)
    if gate_rules:
        marker_io.write(
            cfg,
            session_id,
            prompt,
            [
                MarkerRule(
                    short_id=r.rule.short_id,
                    action=r.rule.action_instruction,
                    trigger=r.rule.trigger_canonical,
                    source_type=r.rule.source_origin,
                )
                for r in gate_rules
            ],
            ph=ph,
        )
        marker_io.prune_stale_markers(cfg, session_id, ph)
    else:
        marker_io.delete_session(cfg, session_id)


def handle(payload: dict, cfg: Config, *, host: Host) -> dict:
    session_id = effective_session_id(payload)
    prompt = payload.get("prompt") or ""
    normalized_prompt = normalize_prompt_for_hash(prompt)
    ph_for_ack = prompt_hash(normalized_prompt) if normalized_prompt else ""

    project_id = sessions.resolve_project_id_for_session(
        cfg, session_id, payload.get("cwd"),
    )

    sessions.touch(cfg, session_id)

    db = open_db(cfg.db_path)
    try:
        _run_dismiss(db, prompt, session_id, cfg)

        try:
            outcome = inject_for_prompt(
                db,
                cfg,
                session_id=session_id,
                prompt=prompt,
                project_id=project_id,
                turn_index=payload.get("turn_index"),
            )
        except RetrieveFailed as e:
            log.warning("retrieve failed (%s); continuing without rules", e)
            if cfg.gate_enabled:
                marker_io.delete_session(cfg, session_id)
            return {"continue": True}

        if outcome is None:
            if cfg.gate_enabled:
                marker_io.delete_session(cfg, session_id)
            return {"continue": True}

        hot, warm = outcome.hot, outcome.warm
        shadow_hot, shadow_warm = outcome.shadow_hot, outcome.shadow_warm
        text = outcome.text
        rendered_entries = outcome.rendered_entries
        ph = outcome.ph

        if not hot and not warm:
            if cfg.gate_enabled:
                marker_io.delete_session(cfg, session_id)
            return {"continue": True}

        if text:
            injected_hot_ids = {
                rid for rid, level in rendered_entries if level == "hot"
            }
            gate_hot = [r for r in hot if r.rule.id in injected_hot_ids]
            _update_gate_marker(
                cfg, session_id, normalized_prompt or prompt, gate_hot, ph,
            )
        elif cfg.gate_enabled:
            marker_io.delete_session(cfg, session_id)

        if ph_for_ack:
            prompt_ack.record(cfg, session_id, ph_for_ack)

        log.info(
            "injected hot=%d warm=%d shadow_hot=%d shadow_warm=%d session=%s",
            len(hot), len(warm), len(shadow_hot), len(shadow_warm), session_id,
        )
        if host == Host.CURSOR and text:
            log.info(
                "cursor beforeSubmitPrompt injection (best-effort; "
                "official schema is continue/user_message only) session=%s",
                session_id,
            )
        return user_prompt_submit_response(host, text or None)
    finally:
        db.close()
