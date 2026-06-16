"""Shared retrieve + injection path for UserPromptSubmit and Cursor deferred preToolUse.

Split into two independently testable layers:
  - retrieve_and_format(): pure pipeline (fetch → retrieve → format → return outcome)
  - record_injection_events(): side-effect layer (fire + shadow event persistence)
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..db import Db, fetch_rules
from ..events.fire import create_fire_event
from ..events.shadow import (
    compute_context_fingerprint,
    create_shadow_event,
    is_duplicate_shadow_context,
)
from ..gate.blocker import format_injection
from ..gate.marker import MarkerRule, prompt_hash
from ..models import ScoredResult
from ..search.engine import RetrievalEngine
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


# ---------------------------------------------------------------------------
# Pure pipeline: fetch → retrieve → format
# ---------------------------------------------------------------------------


def _fetch_formal_and_shadow(db: Db, cfg: Config, project_id: str | None) -> tuple[list, list]:
    """Fetch injection pool (active+trusted) and shadow pool (candidate+suppressed)."""
    if project_id is None:
        formal_rules = fetch_rules(db, statuses=("active", "trusted"), global_only=True)
    else:
        formal_rules = fetch_rules(db, statuses=("active", "trusted"), project_id=project_id)
    if cfg.promotion_enabled:
        if project_id is None:
            shadow_rules = fetch_rules(db, statuses=("candidate", "suppressed"), global_only=True)
        else:
            shadow_rules = fetch_rules(db, statuses=("candidate", "suppressed"), project_id=project_id)
    else:
        shadow_rules = []
    return formal_rules, shadow_rules


def retrieve_and_format(
    db: Db,
    cfg: Config,
    *,
    prompt: str,
    project_id: str | None,
    engine: RetrievalEngine | None = None,
) -> PromptInjectOutcome | None:
    """Retrieve rules and build injection text. No side effects.

    Returns None if there are no rules to search.
    """
    formal_rules, shadow_rules = _fetch_formal_and_shadow(db, cfg, project_id)
    if not formal_rules and not shadow_rules:
        return None

    if engine is None:
        engine = RetrievalEngine(cfg, db)

    try:
        result = engine.retrieve(
            prompt,
            formal_rules,
            shadow_rules,
            interaction="hook",
        )
    except OSError as e:
        raise RetrieveFailed(str(e)) from e

    hot, warm = result.hot, result.warm
    shadow_hot, shadow_warm = result.shadow_hot, result.shadow_warm

    normalized = normalize_prompt_for_hash(prompt)
    ph = prompt_hash(normalized or prompt)

    text, rendered_entries = format_injection(
        hot, warm, max_chars=cfg.max_injection_chars, dismiss_phrase=cfg.dismiss_phrase
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


# ---------------------------------------------------------------------------
# Side-effect layer: event persistence
# ---------------------------------------------------------------------------


def build_decision_features(r: ScoredResult) -> dict:
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
    db: Db,
    session_id: str,
    ph: str,
    results: list,
    *,
    turn_index: int | None = None,
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


def record_injection_events(
    db: Db,
    outcome: PromptInjectOutcome,
    *,
    session_id: str,
    turn_index: int | None = None,
    prompt_text: str | None = None,
    record_injections: bool = True,
    record_shadow_hits: bool = True,
) -> None:
    """Persist fire and shadow events for a completed injection outcome.

    This is the side-effect counterpart to retrieve_and_format().

    Args:
        prompt_text: The original prompt text (strongly recommended). When provided,
            fire events store a truncated copy (up to 4000 chars) for posthoc
            evaluation access. Without it, events only contain a hash reference
            and posthoc evaluator cannot access the original prompt.
    """
    if record_injections:
        _record_rendered_fire_events(
            db,
            session_id,
            outcome.ph,
            outcome.hot,
            outcome.warm,
            outcome.rendered_entries,
            turn_index=turn_index,
            prompt_text=prompt_text,
        )

    if record_shadow_hits:
        _record_shadow_events(
            db,
            session_id,
            outcome.ph,
            outcome.shadow_hot + outcome.shadow_warm,
            turn_index=turn_index,
        )


# ---------------------------------------------------------------------------
# Combined entry point (backward-compatible)
# ---------------------------------------------------------------------------


def marker_rules_from_scored(scored: list) -> list[MarkerRule]:
    """Build MarkerRule list from scored gate results (ScoredResult objects)."""
    return [
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
        for r in scored
    ]


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
    engine: RetrievalEngine | None = None,
) -> PromptInjectOutcome | None:
    """Retrieve rules and build injection text. None if there are no rules to search.

    Combines retrieve_and_format() + record_injection_events() for backward compatibility.
    """
    outcome = retrieve_and_format(
        db,
        cfg,
        prompt=prompt,
        project_id=project_id,
        engine=engine,
    )
    if outcome is None:
        return None

    record_injection_events(
        db,
        outcome,
        session_id=session_id,
        turn_index=turn_index,
        prompt_text=prompt,
        record_injections=record_injections,
        record_shadow_hits=record_shadow_hits,
    )

    return outcome
