from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..config import Config
from ..db import (
    Db,
    fetch_rules,
    fetch_shadow_rules,
    log_injections_batch,
    open_db,
    archive_rule,
    find_rule_id_by_recent_injection,
)
from ..gate import marker as marker_io
from ..gate.blocker import format_injection, select_gate_rules
from ..gate.marker import MarkerRule, prompt_hash
from ..lifecycle import maintenance, promotion
from ..search.retrieve import retrieve_formal_and_shadow
from ..utils import sessions
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id
from ..utils.time import now_iso

log = get_logger("nokori.hooks.user_prompt_submit")

def _dismiss_re(phrase: str) -> re.Pattern[str]:
    escaped = re.escape(phrase.lower())
    return re.compile(
        rf"(?i)(?P<phrase>{escaped})[\s,，、;:：]+(?P<sid>[a-f0-9]{{6,32}})\b"
    )


def _run_dismiss(db: Db, prompt: str, session_id: str, cfg: Config) -> int:
    """Returns number of rules archived via inline dismiss in this prompt."""
    phrase = (cfg.dismiss_phrase or "dismiss").lower()
    pattern = _dismiss_re(phrase)
    count = 0
    now = now_iso()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_iso = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
    for m in pattern.finditer(prompt or ""):
        sid = m.group("sid").lower()
        rid = find_rule_id_by_recent_injection(db, session_id, sid, cutoff_iso)
        if rid is None:
            continue
        archive_rule(db, rid, "user_dismissed_prompt", now)
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
                    action=r.rule.action,
                    source_type=r.rule.source_type,
                    rationale=r.rule.rationale,
                )
                for r in gate_rules
            ],
            ph=ph,
        )
        marker_io.prune_stale_markers(cfg, session_id, ph)
    else:
        marker_io.delete_session(cfg, session_id)


def handle(payload: dict, cfg: Config) -> dict:
    session_id = payload.get("session_id") or "-"
    prompt = payload.get("prompt") or ""
    project_id = sessions.resolve_project_id_for_session(
        cfg, session_id, payload.get("cwd"),
    )

    sessions.touch(cfg, session_id)

    db = open_db(cfg.db_path)
    try:
        _run_dismiss(db, prompt, session_id, cfg)

        if project_id is None:
            formal_rules = fetch_rules(
                db, statuses=("active", "dormant"), global_only=True
            )
        else:
            formal_rules = fetch_rules(
                db, statuses=("active", "dormant"), project_id=project_id
            )
        shadow_rules = (
            fetch_shadow_rules(db, project_id=project_id)
            if project_id and cfg.promotion_enabled
            else []
        )
        if not formal_rules and not shadow_rules:
            if cfg.gate_enabled:
                marker_io.delete_session(cfg, session_id)
            return {"continue": True}

        result, shadow_hot = retrieve_formal_and_shadow(
            prompt,
            formal_rules,
            shadow_rules,
            db,
            cfg,
            interaction="hook",
        )
        hot, warm = result.hot, result.warm

        if project_id:
            for r in shadow_hot:
                try:
                    promotion.record_shadow_hit(db, r.rule.id, project_id)
                except Exception as e:
                    log.info(
                        "shadow promotion skipped rule=%s: %s", r.rule.id, e,
                    )

        if not hot and not warm:
            if cfg.gate_enabled:
                marker_io.delete_session(cfg, session_id)
            return {"continue": True}

        text = format_injection(
            hot, warm, max_chars=cfg.max_injection_chars, dismiss_phrase=cfg.dismiss_phrase
        )

        ph = prompt_hash(prompt)
        now = now_iso()
        if text:
            entries = [(r.rule.id, "hot") for r in hot]
            entries.extend((r.rule.id, "warm") for r in warm)
            log_injections_batch(db, session_id, ph, entries, now)
        for r in warm:
            if r.retrieval_hot and r.rule.status == "dormant":
                maintenance.reactivate_dormant_on_retrieval_hot(db, r.rule.id)

        _update_gate_marker(cfg, session_id, prompt, hot, ph)

        log.info(
            "injected hot=%d warm=%d shadow_hot=%d session=%s",
            len(hot), len(warm), len(shadow_hot), session_id,
        )
        if not text:
            return {"continue": True}
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                       "additionalContext": text}}
    finally:
        db.close()
