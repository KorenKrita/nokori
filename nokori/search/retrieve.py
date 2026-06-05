from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from typing import Literal

from ..config import Config
from ..db import Db, SCHEMA_VERSION, loads_json
from ..matcher.compiler import CompilationError, compile_rule
from ..matcher.runtime import evaluate_match
from ..models import Rule, ScoredResult
from ..policy import RUNTIME_POLICY_VERSION
from ..runtime.applicability import evaluate_applicability, meets_min_evidence
from ..runtime.selection import SelectionResult, select_injection
from . import bm25, ranker
from . import embedding as embedding_search
from .idf_stats import build_idf_stats, compute_trigger_idf_sum, store_idf_stats
from .tokenizer import tokenize

InteractionKind = Literal["hook", "cli"]


@dataclass(frozen=True)
class RetrievalResult:
    hot: list[ScoredResult]
    warm: list[ScoredResult]
    bm25_matches: int
    embed_mode: str  # off | local | remote
    bm25_rule_ids: frozenset[str] = frozenset()


def _variant_dicts(rule: Rule, required_concepts: list[str]) -> list[dict]:
    variants: list[dict] = []
    raw_variants = (
        loads_json(rule.trigger_variants, [])
        if isinstance(rule.trigger_variants, str)
        else rule.trigger_variants
    )
    for variant in raw_variants:
        if isinstance(variant, dict):
            variants.append(variant)
        else:
            text = str(variant).strip()
            if text:
                variants.append({
                    "text": text,
                    "kind": "strong_anchor"
                    if required_concepts and len(tokenize(text)) >= 2
                    else "weak_recall",
                    "requires_concepts": required_concepts
                    if required_concepts and len(tokenize(text)) >= 2
                    else [],
                })

    canonical = rule.trigger_canonical.strip()
    if canonical and required_concepts:
        existing = {v.get("text") for v in variants}
        if canonical not in existing:
            is_multi_token = len(tokenize(canonical)) >= 2
            variants.append({
                "text": canonical,
                "kind": "strong_anchor" if is_multi_token else "weak_recall",
                "requires_concepts": required_concepts if is_multi_token else [],
            })
    return variants


def _trigger_data_for_rule(rule: Rule) -> dict | None:
    concepts = loads_json(rule.concepts, [])
    groups = loads_json(rule.required_concept_groups, [])
    excluded_contexts = loads_json(rule.excluded_contexts, [])

    if rule.schema_version >= SCHEMA_VERSION and (not concepts or not groups):
        return None

    if not concepts or not groups:
        concept_id = "legacy_trigger"
        concepts = [{
            "id": concept_id,
            "label": rule.trigger_canonical[:80] or "legacy trigger",
            "aliases": [{"text": rule.trigger_canonical, "strength": "strong"}],
            "match_mode": "phrase",
            "required": True,
        }]
        groups = [{"id": "legacy_primary", "all_of": [concept_id]}]

    required_concepts = list(groups[0].get("all_of") or []) if groups else []
    return {
        "concepts": concepts,
        "required_concept_groups": groups,
        "excluded_contexts": excluded_contexts,
        "variants": _variant_dicts(rule, required_concepts),
    }


def _apply_runtime_applicability(
    result: ScoredResult,
    prompt: str,
    *,
    idf_stats,
    cfg: Config,
) -> ScoredResult | None:
    if (
        result.rule.schema_version < SCHEMA_VERSION
        and not loads_json(result.rule.concepts, [])
        and not loads_json(result.rule.required_concept_groups, [])
    ):
        if not meets_min_evidence(result):
            return None
        level = "hot" if result.rule.status == "trusted" else "warm"
        return replace(
            result,
            trigger_idf_pool_version=idf_stats.pool_version,
            embedding_profile_version=cfg.embed_model or "unknown",
            runtime_policy_version=RUNTIME_POLICY_VERSION,
            level=level,
            decision_reason="legacy unstructured rule: fielded minimum evidence passed",
        )

    trigger_data = _trigger_data_for_rule(result.rule)
    if trigger_data is None:
        return None

    try:
        matcher = compile_rule(
            trigger_data,
            search_terms=result.rule.search_terms,
        )
    except CompilationError:
        if result.rule.schema_version >= SCHEMA_VERSION:
            return None
        return result if meets_min_evidence(result) else None

    match = evaluate_match(matcher, prompt)
    trigger_tokens = set(result.matched_trigger_tokens)
    for anchor in match.matched_trigger_anchors:
        trigger_tokens.update(tokenize(anchor))
    trigger_idf_sum = compute_trigger_idf_sum(sorted(trigger_tokens), idf_stats)

    applicability = evaluate_applicability(
        rule_status=result.rule.status,
        rule_severity=result.rule.severity,
        rule_first_observed_useful_at=result.rule.first_observed_useful_at,
        trigger_idf_sum=trigger_idf_sum,
        trigger_coverage=match.trigger_coverage,
        distinct_trigger_terms=len(trigger_tokens),
        strong_variant_phrase_hit=bool(match.strong_variant_hits),
        required_concepts_match=match.required_concepts_match,
        excluded_context_hit=bool(match.excluded_context_hits),
        action_only_match=match.action_only_match,
        search_only_match=match.search_only_match,
        embedding_only_match=result.embedding_only_match,
        idf_stats_available=idf_stats.rule_pool_size > 0,
        pool_size=idf_stats.rule_pool_size,
        dynamic_trigger_info_min=idf_stats.dynamic_threshold,
        has_tool_input=False,
        observed_usefulness_score=result.rule.observed_usefulness_score,
        false_positive_score=result.rule.false_positive_score,
    )

    if not applicability.eligible and result.rule.status not in ("candidate", "suppressed"):
        return None
    if not applicability.eligible and not applicability.trigger_evidence_passed:
        return None

    level = applicability.decision
    if level == "cold":
        level = "hot" if match.strong_variant_hits else "warm"

    return replace(
        result,
        trigger_idf_sum=trigger_idf_sum,
        trigger_coverage=match.trigger_coverage,
        distinct_trigger_terms=len(trigger_tokens),
        strong_variant_phrase_hit=bool(match.strong_variant_hits),
        weak_variant_recall_hit=bool(match.weak_variant_hits),
        required_concepts_match=match.required_concepts_match,
        excluded_context_hit=bool(match.excluded_context_hits),
        action_only_match=match.action_only_match,
        search_only_match=match.search_only_match,
        matched_trigger_tokens=frozenset(trigger_tokens),
        trigger_idf_pool_version=idf_stats.pool_version,
        embedding_profile_version=cfg.embed_model or "unknown",
        runtime_policy_version=RUNTIME_POLICY_VERSION,
        decision_reason=applicability.reason,
        level=level,
    )


def retrieve_and_tier(
    prompt: str,
    rules: Sequence[Rule],
    db: Db,
    cfg: Config,
    *,
    top_k: int = 10,
    interaction: InteractionKind = "cli",
    pool_size: int | None = None,
    background_idf_rules: Sequence[Rule] | None = None,
) -> RetrievalResult:
    """BM25 + optional embedding RRF, then applicability + selection tiering.

    Local embedding uses a shared embed server (one loaded model for all hooks).
    Remote embed uses a shorter timeout on hook path (cfg.embed_hook_timeout_seconds).
    """
    if not rules:
        return RetrievalResult([], [], 0, "off")

    bm25_results = bm25.search(prompt, rules, top_k=top_k)
    embed_results: list[ScoredResult] = []
    embed_mode = "off"

    # Embed auto-enable uses the retrieval pool size (this query's rules), not
    # the whole DB — avoids turning on embedding for small projects when the
    # global library is large.
    if pool_size is None:
        pool_size = len(rules)
    if embedding_search.auto_enabled(cfg, pool_size):
        if embedding_search.use_local(cfg):
            timeout = float(
                cfg.embed_hook_timeout_seconds if interaction == "hook" else 30
            )
            embed_results, embed_mode = embedding_search.search_local_shared(
                prompt,
                rules,
                db,
                cfg,
                top_k=top_k,
                timeout=timeout,
                interaction=interaction,
            )
        else:
            timeout = (
                cfg.embed_hook_timeout_seconds
                if interaction == "hook"
                else 10
            )
            client = embedding_search.EmbeddingClient(cfg)
            embed_results = embedding_search.search(
                prompt, rules, db, client, top_k=top_k, timeout=timeout
            )
            embed_mode = "remote"

    fused = ranker.rrf_fuse(bm25_results, embed_results)

    # Applicability gate: compile v6 matchers and apply the runtime policy.
    # For shadow scoring, use the formal (active/trusted) pool's IDF stats (spec 9.3)
    idf_pool = background_idf_rules if background_idf_rules is not None else rules
    idf_stats = build_idf_stats(r for r in idf_pool if r.status in ("active", "trusted"))
    store_idf_stats(db, idf_stats)
    eligible = [
        applied
        for r in fused
        if (applied := _apply_runtime_applicability(r, prompt, idf_stats=idf_stats, cfg=cfg))
        is not None
    ]

    # Selection: split into HOT/WARM via utility + diversity
    selection: SelectionResult = select_injection(
        eligible, max_injection_chars=cfg.max_injection_chars,
        pool_size=idf_stats.rule_pool_size,
    )
    hot = selection.hot
    warm = selection.warm

    bm25_ids = frozenset(r.rule.id for r in bm25_results)
    return RetrievalResult(hot, warm, len(bm25_results), embed_mode, bm25_ids)


def retrieve_formal_and_shadow(
    prompt: str,
    formal_rules: Sequence[Rule],
    shadow_rules: Sequence[Rule],
    db: Db,
    cfg: Config,
    *,
    pool_size: int | None = None,
    interaction: InteractionKind = "hook",
) -> tuple[RetrievalResult, list[ScoredResult], list[ScoredResult]]:
    """Retrieve formal and shadow pools without letting shadow consume formal slots."""
    formal_ids = {r.id for r in formal_rules}
    shadow_only = [r for r in shadow_rules if r.id not in formal_ids]
    combined = list(formal_rules) + shadow_only
    if not combined:
        empty = RetrievalResult([], [], 0, "off")
        return empty, [], []

    effective_pool = pool_size if pool_size is not None else len(combined)
    formal_result = retrieve_and_tier(
        prompt,
        formal_rules,
        db,
        cfg,
        top_k=10,
        interaction=interaction,
        pool_size=effective_pool,
    )
    # Spec section 9.3: shadow scoring uses the same active/trusted background pool
    shadow_result = retrieve_and_tier(
        prompt,
        shadow_only,
        db,
        cfg,
        top_k=10,
        interaction=interaction,
        pool_size=effective_pool,
        background_idf_rules=formal_rules,
    )
    return formal_result, shadow_result.hot, shadow_result.warm
