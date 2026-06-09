"""RetrievalEngine — deep module owning the entire hot-path retrieval decision.

Interface: (prompt, formal_pool, shadow_pool) → RetrievalResult
Implementation: BM25 scoring, optional embedding RRF, runtime applicability,
HOT/WARM/COLD selection with budget and diversity — all internal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from ..config import Config
from ..db import Db, SCHEMA_VERSION
from ..matcher.compiler import CompilationError, compile_rule
from ..matcher.runtime import evaluate_match
from ..models import Rule, ScoredResult
from ..policy import (
    DYNAMIC_IDF_NORMAL,
    DYNAMIC_IDF_SMALL_POOL,
    HOT_MAX_DEFAULT,
    RUNTIME_POLICY_VERSION,
    SMALL_POOL_THRESHOLD,
    WARM_HARD_MAX,
)
from ..runtime.applicability import evaluate_applicability, meets_min_evidence
from .idf_stats import build_idf_stats, compute_trigger_idf_sum, store_idf_stats
from .scorer import SearchScorer
from .tokenizer import tokenize

InteractionKind = Literal["hook", "cli"]


@dataclass(frozen=True)
class RetrievalResult:
    hot: list[ScoredResult]
    warm: list[ScoredResult]
    shadow_hot: list[ScoredResult]
    shadow_warm: list[ScoredResult]
    bm25_matches: int
    embed_mode: str
    bm25_rule_ids: frozenset[str] = frozenset()


class RetrievalEngine:
    """Session-scoped retrieval engine.

    Owns IDF cache state across prompts within a session.
    """

    def __init__(self, cfg: Config, db: Db) -> None:
        self._cfg = cfg
        self._db = db
        self._scorer = SearchScorer(cfg, db)
        self._last_stored_pool_version: str | None = None

    @property
    def cfg(self) -> Config:
        return self._cfg

    @property
    def db(self) -> Db:
        return self._db

    def retrieve(
        self,
        prompt: str,
        formal_rules: Sequence[Rule],
        shadow_rules: Sequence[Rule],
        *,
        interaction: InteractionKind = "hook",
    ) -> RetrievalResult:
        """Main interface: retrieve, score, decide HOT/WARM/COLD for formal + shadow pools."""
        formal_ids = {r.id for r in formal_rules}
        shadow_only = [r for r in shadow_rules if r.id not in formal_ids]
        combined = list(formal_rules) + shadow_only
        if not combined:
            return RetrievalResult([], [], [], [], 0, "off")

        effective_pool = len(combined)

        formal_result = self.retrieve_and_tier(
            prompt,
            formal_rules,
            interaction=interaction,
            pool_size=effective_pool,
        )

        shadow_result = self.retrieve_and_tier(
            prompt,
            shadow_only,
            interaction=interaction,
            pool_size=effective_pool,
            background_idf_rules=formal_rules,
        )

        return RetrievalResult(
            hot=formal_result.hot,
            warm=formal_result.warm,
            shadow_hot=shadow_result.hot,
            shadow_warm=shadow_result.warm,
            bm25_matches=formal_result.bm25_matches,
            embed_mode=formal_result.embed_mode,
            bm25_rule_ids=formal_result.bm25_rule_ids,
        )

    def retrieve_and_tier(
        self,
        prompt: str,
        rules: Sequence[Rule],
        *,
        top_k: int = 10,
        interaction: InteractionKind = "cli",
        pool_size: int | None = None,
        background_idf_rules: Sequence[Rule] | None = None,
    ) -> _TierResult:
        if not rules:
            return _TierResult([], [], 0, "off")

        fused = self._scorer.score(
            prompt, rules, top_k=top_k, interaction=interaction, pool_size=pool_size,
        )

        idf_pool = background_idf_rules if background_idf_rules is not None else rules
        idf_stats = build_idf_stats(r for r in idf_pool if r.status in ("active", "trusted"))
        if idf_stats.pool_version != self._last_stored_pool_version:
            store_idf_stats(self._db, idf_stats)
            self._last_stored_pool_version = idf_stats.pool_version

        eligible = [
            applied
            for r in fused
            if (applied := self._apply_runtime_applicability(r, prompt, idf_stats=idf_stats))
            is not None
        ]

        selection = select_injection(
            eligible,
            max_injection_chars=self._cfg.max_injection_chars,
            pool_size=idf_stats.rule_pool_size,
        )

        bm25_ids = frozenset(r.rule.id for r in fused if r.bm25_score > 0)
        bm25_count = len(bm25_ids)
        embed_mode = self._scorer.last_embed_mode
        return _TierResult(selection.hot, selection.warm, bm25_count, embed_mode, bm25_ids)

    def _apply_runtime_applicability(
        self,
        result: ScoredResult,
        prompt: str,
        *,
        idf_stats,
    ) -> ScoredResult | None:
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
            level = "hot" if result.rule.status == "trusted" else "warm"
            return replace(
                result,
                trigger_idf_pool_version=idf_stats.pool_version,
                embedding_profile_version=embedding_profile_version,
                runtime_policy_version=RUNTIME_POLICY_VERSION,
                trigger_evidence_passed=True,
                decision_penalties=(),
                level=level,
                decision_reason="legacy unstructured rule: fielded minimum evidence passed",
            )

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
            level = "hot" if result.rule.status == "trusted" else "warm"
            return replace(
                result,
                trigger_idf_pool_version=idf_stats.pool_version,
                embedding_profile_version=embedding_profile_version,
                runtime_policy_version=RUNTIME_POLICY_VERSION,
                trigger_evidence_passed=True,
                decision_penalties=(),
                level=level,
                decision_reason="legacy unstructured rule: fielded minimum evidence passed",
            )

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

        if not applicability.eligible and result.rule.status not in ("candidate", "suppressed"):
            return None
        if not applicability.eligible and not applicability.trigger_evidence_passed:
            return None

        level = applicability.decision if applicability.decision != "cold" else None

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
        )


# ---------------------------------------------------------------------------
# Internal: tier result (before shadow merging)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TierResult:
    hot: list[ScoredResult]
    warm: list[ScoredResult]
    bm25_matches: int
    embed_mode: str
    bm25_rule_ids: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Internal: trigger data extraction (will be replaced by matcher unification)
# ---------------------------------------------------------------------------


def variant_dicts(rule: Rule, required_concepts: list[str]) -> list[dict]:
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
        "variants": variant_dicts(rule, required_concepts),
    }


# ---------------------------------------------------------------------------
# Internal: selection logic (absorbed from runtime/selection.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionResult:
    hot: list[ScoredResult]
    warm: list[ScoredResult]
    shadow_matches: list[ScoredResult]


_MMR_PENALTY_WEIGHT: float = 2.0


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def mmr_penalty(
    candidate_tokens: frozenset[str],
    selected_tokens_list: list[frozenset[str]],
) -> float:
    if not selected_tokens_list:
        return 0.0
    max_sim = max(
        jaccard(candidate_tokens, selected) for selected in selected_tokens_list
    )
    return max_sim * _MMR_PENALTY_WEIGHT


def compute_utility(
    scored_result: ScoredResult,
    selected_tokens_list: list[frozenset[str]] | None = None,
) -> float:
    trigger_idf_sum = scored_result.trigger_idf_sum
    variant_phrase_bonus = 1.0 if scored_result.strong_variant_phrase_hit else 0.0

    rule = scored_result.rule
    if rule.status == "trusted":
        trusted_or_usefulness_bonus = 1.5
    elif rule.observed_usefulness_score > 0:
        trusted_or_usefulness_bonus = 0.5
    else:
        trusted_or_usefulness_bonus = 0.0

    near_duplicate_penalty = mmr_penalty(
        scored_result.matched_trigger_tokens,
        selected_tokens_list or [],
    )
    recent_false_positive_penalty = rule.false_positive_score * 2.0

    return (
        trigger_idf_sum
        + variant_phrase_bonus
        + trusted_or_usefulness_bonus
        - near_duplicate_penalty
        - recent_false_positive_penalty
    )


_WARM_MIN_THRESHOLD: float = 1.0
_DIVERSITY_OVERLAP_MAX: float = 0.80
_FORMAT_OVERHEAD: int = 25


def _has_distinct_domain(candidate: ScoredResult, selected: list[ScoredResult]) -> bool:
    candidate_domains = set(candidate.rule.domain_tags) if candidate.rule.domain_tags else set()
    candidate_groups = frozenset(
        g.get("id", "") for g in (candidate.rule.required_concept_groups or [])
    )

    for s in selected:
        s_domains = set(s.rule.domain_tags) if s.rule.domain_tags else set()
        s_groups = frozenset(
            g.get("id", "") for g in (s.rule.required_concept_groups or [])
        )
        if candidate_domains == s_domains and candidate_groups == s_groups:
            if not candidate_domains and not candidate_groups:
                overlap = jaccard(candidate.matched_trigger_tokens, s.matched_trigger_tokens)
                if overlap < 0.3:
                    continue
            return False
    return True


def _char_len(result: ScoredResult) -> int:
    rule = result.rule
    return len(rule.trigger_canonical) + len(rule.action_instruction) + _FORMAT_OVERHEAD


def select_injection(
    eligible_results: list[ScoredResult],
    max_injection_chars: int,
    warm_hard_max: int = WARM_HARD_MAX,
    pool_size: int = 0,
) -> SelectionResult:
    if not eligible_results:
        return SelectionResult([], [], [])

    scored_with_utility: list[tuple[float, ScoredResult]] = []
    for sr in eligible_results:
        u = compute_utility(sr, selected_tokens_list=None)
        scored_with_utility.append((u, sr))

    scored_with_utility.sort(key=lambda x: x[0], reverse=True)

    hot: list[ScoredResult] = []
    warm: list[ScoredResult] = []
    shadow_matches: list[ScoredResult] = []
    selected_tokens: list[frozenset[str]] = []
    chars_used = 0
    has_runtime_levels = any(sr.level is not None for _, sr in scored_with_utility)

    hot_max = HOT_MAX_DEFAULT
    for _initial_utility, sr in scored_with_utility:
        if has_runtime_levels and sr.level not in ("hot", "gate"):
            continue
        if len(hot) >= hot_max:
            break
        u = compute_utility(sr, selected_tokens_list=selected_tokens)
        if u <= 0:
            continue
        hot.append(sr)
        selected_tokens.append(sr.matched_trigger_tokens)

    if len(hot) == 1 and len(scored_with_utility) > 1:
        for _initial_utility, sr in scored_with_utility:
            if sr is hot[0]:
                continue
            if has_runtime_levels and sr.level not in ("hot", "gate"):
                continue
            u = compute_utility(sr, selected_tokens_list=selected_tokens)
            if u <= 0:
                continue
            _idf_policy = DYNAMIC_IDF_SMALL_POOL if pool_size < SMALL_POOL_THRESHOLD else DYNAMIC_IDF_NORMAL
            has_strong_evidence = (
                (sr.strong_variant_phrase_hit and sr.required_concepts_match)
                or sr.level == "gate"
                or (
                    sr.trigger_idf_sum >= _idf_policy.absolute_trigger_info_min
                    and sr.trigger_coverage >= _idf_policy.trigger_coverage_min
                    and sr.required_concepts_match
                    and sr.distinct_trigger_terms >= _idf_policy.distinct_trigger_terms_min
                )
            )
            if _has_distinct_domain(sr, hot) and has_strong_evidence:
                hot.append(sr)
                selected_tokens.append(sr.matched_trigger_tokens)
                break

    hot_set = set(id(sr) for sr in hot)

    prev_warm_utility: float | None = None
    for _initial_utility, sr in scored_with_utility:
        if id(sr) in hot_set:
            continue
        if has_runtime_levels and sr.level not in ("warm", "hot", "gate"):
            shadow_matches.append(sr)
            continue
        if len(warm) >= warm_hard_max:
            break

        u = compute_utility(sr, selected_tokens_list=selected_tokens)

        if selected_tokens:
            max_overlap = max(
                jaccard(sr.matched_trigger_tokens, st) for st in selected_tokens
            )
            if max_overlap > _DIVERSITY_OVERLAP_MAX:
                shadow_matches.append(sr)
                continue

        if len(warm) >= 2:
            threshold = max(
                _WARM_MIN_THRESHOLD,
                (prev_warm_utility or 0.0) * 0.80,
            )
            if u < threshold:
                shadow_matches.append(sr)
                continue

        cost = _char_len(sr)
        if chars_used + cost > max_injection_chars:
            shadow_matches.append(sr)
            continue

        warm.append(sr)
        selected_tokens.append(sr.matched_trigger_tokens)
        chars_used += cost
        prev_warm_utility = u

    selected_ids = hot_set | set(id(sr) for sr in warm) | set(id(sr) for sr in shadow_matches)
    for _u, sr in scored_with_utility:
        if id(sr) not in selected_ids:
            shadow_matches.append(sr)

    return SelectionResult(hot=hot, warm=warm, shadow_matches=shadow_matches)
