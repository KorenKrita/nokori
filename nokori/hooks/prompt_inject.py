"""Shared retrieve + injection path for UserPromptSubmit and Cursor deferred preToolUse."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..db import Db, fetch_rules, fetch_shadow_rules
from ..events.fire import create_fire_event
from ..events.shadow import (
    compute_context_fingerprint,
    create_shadow_event,
    is_duplicate_shadow_context,
)
from ..gate.blocker import format_injection
from ..gate.marker import prompt_hash
from ..search.retrieve import retrieve_formal_and_shadow
from ..utils.logging import get_logger
from ..utils.prompt_text import normalize_prompt_for_hash

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
    """Fetch injection pool (active+trusted) and shadow pool (candidate+suppressed)."""
    if project_id is None:
        formal_rules = fetch_rules(db, statuses=("active", "trusted"), global_only=True)
    else:
        formal_rules = fetch_rules(
            db, statuses=("active", "trusted"), project_id=project_id
        )
    if cfg.promotion_enabled:
        if project_id is None:
            shadow_rules = [
                r
                for r in fetch_shadow_rules(db, project_id=None)
                if r.project_scope == "global"
            ]
        else:
            shadow_rules = fetch_shadow_rules(db, project_id=project_id)
    else:
        shadow_rules = []
    return formal_rules, shadow_rules


def build_decision_features(r) -> dict:
    """Extract decision features from a ScoredResult for event logging."""
    return {
        "trigger_idf_sum": r.trigger_idf_sum,
        "trigger_coverage": r.trigger_coverage,
        "distinct_trigger_terms": r.distinct_trigger_terms,
        "strong_variant_phrase_hit": r.strong_variant_phrase_hit,
        "weak_variant_recall_hit": r.weak_variant_recall_hit,
        "required_concepts_match": r.required_concepts_match,
        "excluded_context_hit": r.excluded_context_hit,
        "excluded_context_override_passed": r.excluded_context_override_passed,
        "action_only_match": r.action_only_match,
        "search_only_match": r.search_only_match,
        "embedding_only_match": r.embedding_only_match,
        "matched_trigger_tokens": sorted(r.matched_trigger_tokens),
        "matched_variant_tokens": sorted(r.matched_variant_tokens),
        "decision_reason": r.decision_reason,
        "bm25_score": r.bm25_score,
        "cosine": r.cosine,
        "rrf_score": r.rrf_score,
    }


def _record_fire_events(
    db: Db,
    session_id: str,
    ph: str,
    results: list,
    level: str,
    *,
    turn_index: int | None = None,
    prompt_text: str | None = None,
) -> None:
    """Create fire events for injected WARM/HOT rules."""
    # Store actual prompt text (truncated to 4000 chars) for posthoc evaluator access,
    # instead of just a hash reference.
    if prompt_text and len(prompt_text) > 64:
        bounded_ref = prompt_text[:4000]
    else:
        bounded_ref = f"session:{session_id}:prompt:{ph}"

    for r in results:
        try:
            create_fire_event(
                db,
                r.rule,
                session_id=session_id,
                prompt_hash=ph,
                turn_index=turn_index,
                level=level,
                decision_features=build_decision_features(r),
                idf_pool_version=r.trigger_idf_pool_version,
                embedding_profile_version=r.embedding_profile_version,
                bounded_window_ref=bounded_ref,
            )
        except Exception as e:
            log.info("fire event creation failed rule=%s: %s", r.rule.id, e)


def _record_rendered_fire_events(
    db: Db,
    session_id: str,
    ph: str,
    hot: list,
    warm: list,
    rendered_entries: list[tuple[str, str]],
    *,
    turn_index: int | None = None,
    prompt_text: str | None = None,
) -> None:
    results_by_id = {r.rule.id: r for r in [*hot, *warm]}
    for rule_id, level in rendered_entries:
        result = results_by_id.get(rule_id)
        if result is None:
            continue
        _record_fire_events(
            db,
            session_id,
            ph,
            [result],
            level,
            turn_index=turn_index,
            prompt_text=prompt_text,
        )


_SHADOW_TYPE_BY_STATUS = {
    "candidate": "candidate_probe",
    "suppressed": "suppression_recovery",
}


def _record_shadow_events(
    db: Db, session_id: str, ph: str, results: list,
    *, turn_index: int | None = None,
) -> None:
    """Create shadow events for candidate/suppressed matches with fingerprint dedup."""
    for r in results:
        shadow_type = _SHADOW_TYPE_BY_STATUS.get(r.rule.status)
        if shadow_type is None:
            continue
        fp = compute_context_fingerprint(ph, turn_index=turn_index)
        if is_duplicate_shadow_context(db, r.rule.id, fp):
            continue
        try:
            create_shadow_event(
                db,
                r.rule,
                session_id=session_id,
                status_at_match=r.rule.status,
                shadow_type=shadow_type,
                prompt_hash=ph,
                matched_level="hot_candidate" if r.strong_variant_phrase_hit else "warm_candidate",
                decision_features=build_decision_features(r),
                idf_pool_version=r.trigger_idf_pool_version,
                runtime_policy_version=r.runtime_policy_version,
                embedding_profile_version=r.embedding_profile_version,
                context_fingerprint=fp,
            )
        except Exception as e:
            log.info("shadow event creation failed rule=%s: %s", r.rule.id, e)


def inject_for_prompt(
    db: Db,
    cfg: Config,
    *,
    session_id: str,
    prompt: str,
    project_id: str | None,
    turn_index: int | None = None,
    record_injections: bool = True,
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

    normalized = normalize_prompt_for_hash(prompt)
    ph = prompt_hash(normalized or prompt)

    text, rendered_entries = format_injection(
        hot, warm, max_chars=cfg.max_injection_chars, dismiss_phrase=cfg.dismiss_phrase
    )

    # Record only rules that were actually rendered into the prompt. HOT rules
    # may spill into WARM when the injection budget is tight.
    if record_injections:
        _record_rendered_fire_events(
            db,
            session_id,
            ph,
            hot,
            warm,
            rendered_entries,
            turn_index=turn_index,
            prompt_text=prompt,
        )

    # Record shadow events for candidate/suppressed matches (fingerprint dedup)
    if record_shadow_hits:
        _record_shadow_events(
            db, session_id, ph, shadow_hot + shadow_warm,
            turn_index=turn_index,
        )

    return PromptInjectOutcome(
        hot=hot,
        warm=warm,
        shadow_hot=shadow_hot,
        shadow_warm=shadow_warm,
        text=text,
        rendered_entries=rendered_entries,
        ph=ph,
    )
