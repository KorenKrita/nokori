"""Shared retrieve + injection path for UserPromptSubmit and Cursor deferred preToolUse."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..db import Db, fetch_rules, fetch_shadow_rules, log_injections_batch
from ..gate.blocker import format_injection
from ..gate.marker import prompt_hash
from ..lifecycle import maintenance, promotion
from ..search.retrieve import retrieve_formal_and_shadow
from ..utils.logging import get_logger
from ..utils.prompt_text import normalize_prompt_for_hash
from ..utils.time import now_iso

log = get_logger("nokori.hooks.prompt_inject")


class RetrieveFailed(OSError):
    """Retrieve/index I/O failed; callers may fail-open."""


@dataclass
class PromptInjectOutcome:
    hot: list
    warm: list
    shadow_hot: list
    shadow_warm: list
    text: str
    rendered_entries: list
    ph: str


def _fetch_formal_and_shadow(
    db: Db, cfg: Config, project_id: str | None
) -> tuple[list, list]:
    if project_id is None:
        formal_rules = fetch_rules(db, statuses=("active", "dormant"), global_only=True)
    else:
        formal_rules = fetch_rules(
            db, statuses=("active", "dormant"), project_id=project_id
        )
    shadow_rules = (
        fetch_shadow_rules(db, project_id=project_id)
        if project_id and cfg.promotion_enabled
        else []
    )
    return formal_rules, shadow_rules


def inject_for_prompt(
    db: Db,
    cfg: Config,
    *,
    session_id: str,
    prompt: str,
    project_id: str | None,
    record_injections: bool = True,
    reactivate_dormant: bool = True,
    record_shadow_hits: bool = True,
) -> PromptInjectOutcome | None:
    """Retrieve rules and build injection text. None if there are no rules to search."""
    formal_rules, shadow_rules = _fetch_formal_and_shadow(db, cfg, project_id)
    if not formal_rules and not shadow_rules:
        return None

    try:
        result, shadow_hot, shadow_warm = retrieve_formal_and_shadow(
            prompt,
            formal_rules,
            shadow_rules,
            db,
            cfg,
            interaction="hook",
        )
    except OSError as e:
        raise RetrieveFailed(str(e)) from e

    hot, warm = result.hot, result.warm

    if record_shadow_hits and project_id:
        for r in shadow_hot + shadow_warm:
            try:
                promotion.record_shadow_hit(db, r.rule.id, project_id)
            except Exception as e:
                log.info("shadow promotion skipped rule=%s: %s", r.rule.id, e)

    text, rendered_entries = format_injection(
        hot, warm, max_chars=cfg.max_injection_chars, dismiss_phrase=cfg.dismiss_phrase
    )

    normalized = normalize_prompt_for_hash(prompt)
    ph = prompt_hash(normalized or prompt)

    if text and record_injections:
        now = now_iso()
        log_injections_batch(db, session_id, ph, rendered_entries, now)
        if reactivate_dormant:
            for r in warm:
                if r.retrieval_hot and r.rule.status == "dormant":
                    maintenance.reactivate_dormant_on_retrieval_hot(db, r.rule.id)

    return PromptInjectOutcome(
        hot=hot,
        warm=warm,
        shadow_hot=shadow_hot,
        shadow_warm=shadow_warm,
        text=text,
        rendered_entries=rendered_entries,
        ph=ph,
    )
