from __future__ import annotations

import re
from datetime import datetime, timezone

from ..config import Config
from ..db import (
    fetch_rules,
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

log = get_logger("nokori.hooks.user_prompt_submit")

_DISMISS_RE = re.compile(r"\b(?P<phrase>\w+)\s+(?P<sid>[a-f0-9]{6,32})\b")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _resolve_project_id(payload: dict) -> str | None:
    cwd = payload.get("cwd")
    if not cwd:
        return None
    return cwd.rstrip("/").split("/")[-1] or None


def _run_dismiss(prompt: str, session_id: str, cfg: Config) -> int:
    """Returns number of rules archived via inline dismiss in this prompt."""
    phrase = (cfg.dismiss_phrase or "dismiss").lower()
    count = 0
    cutoff_iso = (
        datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - 24 * 3600, tz=timezone.utc
        )
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    now = _now_iso()
    db = open_db(cfg.db_path)
    try:
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
    finally:
        db.close()
    return count


def handle(payload: dict, cfg: Config) -> dict:
    session_id = payload.get("session_id") or "-"
    prompt = payload.get("prompt") or ""
    project_id = _resolve_project_id(payload)

    sessions.touch(cfg, session_id)
    _run_dismiss(prompt, session_id, cfg)

    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(
            db, statuses=("active", "dormant"), project_id=project_id
        )
    finally:
        db.close()

    if not rules:
        return {"continue": True}

    bm25_results = bm25.search(prompt, rules, top_k=10)

    embed_results = []
    if embedding_search.auto_enabled(cfg, len(rules)):
        db = open_db(cfg.db_path)
        try:
            client = embedding_search.EmbeddingClient(cfg)
            embed_results = embedding_search.search(prompt, rules, db, client, top_k=10)
        finally:
            db.close()

    fused = ranker.rrf_fuse(bm25_results, embed_results)
    hot, warm = ranker.tier_results(fused)

    if not hot and not warm:
        return {"continue": True}

    text = format_injection(
        hot, warm, max_chars=cfg.max_injection_chars, dismiss_phrase=cfg.dismiss_phrase
    )

    ph = prompt_hash(prompt)
    db = open_db(cfg.db_path)
    try:
        now = _now_iso()
        for r in hot:
            log_injection(db, r.rule.id, session_id, ph, "hot", now)
        for r in warm:
            log_injection(db, r.rule.id, session_id, ph, "warm", now)
        for r in warm:
            if getattr(r, "retrieval_hot", False) and r.rule.status == "dormant":
                maintenance.reactivate_dormant_on_retrieval_hot(db, r.rule.id)
        if project_id:
            for r in list(hot) + list(warm):
                rule_proj = r.rule.project_id
                if rule_proj is None or rule_proj == project_id:
                    continue
                promotion.record_shadow_hit(db, r.rule.id, project_id)
    finally:
        db.close()

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
            )

    log.info(
        "injected hot=%d warm=%d session=%s",
        len(hot), len(warm), session_id,
    )
    if not text:
        return {"continue": True}
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                   "additionalContext": text}}
