"""EvidenceEvaluator — evaluates trigger evidence and runtime applicability for a single rule match.

Interface: evaluate(ScoredResult, prompt, idf_stats) → ScoredResult | None
Implementation: trigger data construction, matcher compilation, concept matching,
IDF computation, applicability evaluation, result decoration — all internal.
"""

from __future__ import annotations

from dataclasses import replace

from ..db import SCHEMA_VERSION
from ..matcher.compiler import CompilationError, compile_rule
from ..matcher.runtime import evaluate_match
from ..models import Rule, ScoredResult
from ..policy import RUNTIME_POLICY_VERSION
from ..runtime.applicability import evaluate_applicability, meets_min_evidence
from .idf_stats import IdfPoolStats, compute_trigger_idf_sum
from .tokenizer import tokenize


def compute_base_utility(
    *,
    trigger_idf_sum: float,
    strong_variant_phrase_hit: bool,
    rule_status: str,
    observed_usefulness_score: float,
    false_positive_score: float,
    eligible: bool,
) -> float:
    """Per-rule utility without cross-result MMR penalty.

    May return negative values when false_positive_score outweighs other terms.
    """
    if not eligible:
        return 0.0
    variant_phrase_bonus = 1.0 if strong_variant_phrase_hit else 0.0
    if rule_status == "trusted":
        trust_bonus = 1.5
    elif observed_usefulness_score > 0:
        trust_bonus = 0.5
    else:
        trust_bonus = 0.0
    fp_penalty = false_positive_score * 2.0
    return trigger_idf_sum + variant_phrase_bonus + trust_bonus - fp_penalty


def _legacy_pass_result(
    result: ScoredResult,
    idf_stats: IdfPoolStats,
    embedding_profile_version: str | None,
) -> ScoredResult:
    level = "hot" if result.rule.status == "trusted" else "warm"
    # trigger_idf_sum is 0.0 for legacy rules (no IDF computation); intentional —
    # encourages migration to schema_version >= 7 where IDF contributes to ranking.
    ranking_utility = compute_base_utility(
        trigger_idf_sum=result.trigger_idf_sum,
        strong_variant_phrase_hit=result.strong_variant_phrase_hit,
        rule_status=result.rule.status,
        observed_usefulness_score=result.rule.observed_usefulness_score,
        false_positive_score=result.rule.false_positive_score,
        eligible=True,
    )
    return replace(
        result,
        trigger_idf_pool_version=idf_stats.pool_version,
        embedding_profile_version=embedding_profile_version,
        runtime_policy_version=RUNTIME_POLICY_VERSION,
        trigger_evidence_passed=True,
        decision_penalties=(),
        level=level,
        decision_reason="legacy unstructured rule: fielded minimum evidence passed",
        ranking_utility=ranking_utility,
    )


def evaluate_evidence(
    result: ScoredResult,
    prompt: str,
    *,
    idf_stats: IdfPoolStats,
) -> ScoredResult | None:
    """Evaluate trigger evidence and runtime applicability for a single scored result.

    Returns the result decorated with applicability metadata, or None if the rule
    does not pass evidence thresholds.
    """
    embedding_profile_version = (
        result.embedding_profile_version
        if result.embedding_profile_version is not None
        else ("unknown" if result.embedding_profile_unknown else None)
    )

    if (
        result.rule.schema_version < SCHEMA_VERSION
        and not result.rule.concepts
        and not result.rule.required_concept_groups
    ):
        if not meets_min_evidence(result):
            return None
        return _legacy_pass_result(result, idf_stats, embedding_profile_version)

    trigger_data = trigger_data_for_rule(result.rule)
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
        if not meets_min_evidence(result):
            return None
        return _legacy_pass_result(result, idf_stats, embedding_profile_version)

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
        excluded_context_override_passed=match.excluded_context_override_passed,
        action_only_match=match.action_only_match,
        search_only_match=match.search_only_match,
        embedding_only_match=result.embedding_only_match,
        idf_stats_available=idf_stats.rule_pool_size > 0,
        pool_size=idf_stats.rule_pool_size,
        dynamic_trigger_info_min=idf_stats.dynamic_threshold,
        has_tool_input=False,
        false_positive_score=result.rule.false_positive_score,
    )

    # Allow ineligible results through only for candidate/suppressed rules
    # that passed trigger evidence (for shadow event tracking upstream).
    if not applicability.eligible:
        if result.rule.status not in ("candidate", "suppressed"):
            return None
        if not applicability.trigger_evidence_passed:
            return None

    level = applicability.decision if applicability.decision != "cold" else None

    ranking_utility = compute_base_utility(
        trigger_idf_sum=trigger_idf_sum,
        strong_variant_phrase_hit=bool(match.strong_variant_hits),
        rule_status=result.rule.status,
        observed_usefulness_score=result.rule.observed_usefulness_score,
        false_positive_score=result.rule.false_positive_score,
        eligible=applicability.eligible,
    )

    return replace(
        result,
        trigger_idf_sum=trigger_idf_sum,
        trigger_coverage=match.trigger_coverage,
        distinct_trigger_terms=len(trigger_tokens),
        strong_variant_phrase_hit=bool(match.strong_variant_hits),
        weak_variant_recall_hit=bool(match.weak_variant_hits),
        required_concepts_match=match.required_concepts_match,
        excluded_context_hit=bool(match.excluded_context_hits),
        excluded_context_override_passed=match.excluded_context_override_passed,
        action_only_match=match.action_only_match,
        search_only_match=match.search_only_match,
        matched_trigger_tokens=frozenset(trigger_tokens),
        trigger_idf_pool_version=idf_stats.pool_version,
        embedding_profile_version=embedding_profile_version,
        runtime_policy_version=RUNTIME_POLICY_VERSION,
        decision_reason=applicability.reason,
        trigger_evidence_passed=applicability.trigger_evidence_passed,
        decision_penalties=tuple(applicability.penalties),
        level=level,
        ranking_utility=ranking_utility,
    )


# ---------------------------------------------------------------------------
# Internal: trigger data extraction
# ---------------------------------------------------------------------------


def _variant_dicts(rule: Rule, required_concepts: list[str]) -> list[dict]:
    variants: list[dict] = []
    raw_variants = rule.trigger_variants or []
    for variant in raw_variants:
        if isinstance(variant, dict):
            variants.append(variant)
        else:
            text = str(variant).strip()
            if text:
                is_strong = required_concepts and len(tokenize(text)) >= 2
                variants.append({
                    "text": text,
                    "kind": "strong_anchor" if is_strong else "weak_recall",
                    "requires_concepts": required_concepts if is_strong else [],
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


def trigger_data_for_rule(rule: Rule) -> dict | None:
    concepts = rule.concepts or []
    groups = rule.required_concept_groups or []
    excluded_contexts = rule.excluded_contexts or []

    if rule.schema_version >= SCHEMA_VERSION and (not concepts or not groups):
        return None

    if not concepts or not groups:
        if not rule.trigger_canonical.strip():
            return None
        concept_id = "legacy_trigger"
        concepts = [{
            "id": concept_id,
            "label": rule.trigger_canonical[:80],
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
