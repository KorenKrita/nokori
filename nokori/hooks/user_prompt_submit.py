from __future__ import annotations

import re
from datetime import datetime, timezone

from ..config import Config
from ..db import (
    Db,
    fetch_rules,
    fetch_shadow_rules,
    log_injection,
    open_db,
    archive_rule,
    find_rule_id_by_recent_injection,
)
from ..gate import marker as marker_io
from ..gate.blocker import format_injection, select_gate_rules
from ..gate.marker import MarkerRule, prompt_hash
from ..lifecycle import maintenance, promotion
from ..search import bm25, ranker
from ..search import embedding as embedding_search
from ..utils import sessions
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id
from ..utils.time import now_iso

log = get_logger("nokori.hooks.user_prompt_submit")

_DISMISS_RE = re.compile(r"\b(?P<phrase>\w+)\s+(?P<sid>[a-f0-9]{6,32})\b")


def _run_dismiss(db: Db, prompt: str, session_id: str, cfg: Config) -> int:
    """Returns number of rules archived via inline dismiss in this prompt."""
    phrase = (cfg.dismiss_phrase or "dismiss").lower()
    count = 0
    now = now_iso()
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_iso = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
    for m in _DISMISS_RE.finditer(prompt or ""):
        if m.group("phrase").lower() != phrase:
            continue
        sid = m.group("sid")
        rid = find_rule_id_by_recent_injection(db, session_id, sid, cutoff_iso)
        if rid is None:
            continue
        archive_rule(db, rid, "user_dismissed_prompt", now)
        log.info("rule dismissed via prompt short=%s session=%s", sid, session_id)
        count += 1
    return count


def _run_shadow_pool(db: Db, prompt: str, project_id: str) -> None:
    """Check shadow pool rules and record hits. Never injects."""
    shadow_rules = fetch_shadow_rules(db, project_id=project_id)
    if not shadow_rules:
        return
    shadow_bm25 = bm25.search(prompt, shadow_rules, top_k=5)
    shadow_fused = ranker.rrf_fuse(shadow_bm25, [])
    for r in shadow_fused:
        if r.rrf_score < ranker.MIN_ABSOLUTE_SCORE:
            continue
        if not ranker._meets_min_evidence(r):
            continue
        promotion.record_shadow_hit(db, r.rule.id, project_id)


def handle(payload: dict, cfg: Config) -> dict:
    session_id = payload.get("session_id") or "-"
    prompt = payload.get("prompt") or ""
    project_id = resolve_project_id(payload.get("cwd"))

    sessions.touch(cfg, session_id)

    db = open_db(cfg.db_path)
    try:
        _run_dismiss(db, prompt, session_id, cfg)

        rules = fetch_rules(
            db, statuses=("active", "dormant"), project_id=project_id
        )
        if not rules:
            if project_id:
                _run_shadow_pool(db, prompt, project_id)
            return {"continue": True}

        bm25_results = bm25.search(prompt, rules, top_k=10)

        embed_results = []
        if embedding_search.auto_enabled(cfg, len(rules)):
            if embedding_search.use_local(cfg):
                local_client = embedding_search.LocalEmbeddingClient(cfg)
                embed_results = embedding_search.search_local(
                    prompt, rules, db, local_client, top_k=10
                )
            else:
                client = embedding_search.EmbeddingClient(cfg)
                embed_results = embedding_search.search(
                    prompt, rules, db, client, top_k=10
                )

        fused = ranker.rrf_fuse(bm25_results, embed_results)
        hot, warm = ranker.tier_results(fused)

        if not hot and not warm:
            return {"continue": True}

        text = format_injection(
            hot, warm, max_chars=cfg.max_injection_chars, dismiss_phrase=cfg.dismiss_phrase
        )

        ph = prompt_hash(prompt)
        now = now_iso()
        for r in hot:
            log_injection(db, r.rule.id, session_id, ph, "hot", now)
        for r in warm:
            log_injection(db, r.rule.id, session_id, ph, "warm", now)
            if getattr(r, "retrieval_hot", False) and r.rule.status == "dormant":
                maintenance.reactivate_dormant_on_retrieval_hot(db, r.rule.id)

        if cfg.gate_enabled:
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

        # Shadow pool: other projects' high-confidence rules, not injected
        if project_id:
            _run_shadow_pool(db, prompt, project_id)

        log.info(
            "injected hot=%d warm=%d session=%s",
            len(hot), len(warm), session_id,
        )
        if not text:
            return {"continue": True}
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                       "additionalContext": text}}
    finally:
        db.close()
