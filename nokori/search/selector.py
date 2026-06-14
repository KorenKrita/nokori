"""InjectionSelector — selects HOT/WARM rules from eligible results with diversity and budget constraints.

Interface: select_injection(eligible_results, max_injection_chars, ...) → SelectionResult
Implementation: utility scoring, MMR deduplication, diversity control, character budget — all internal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import ScoredResult
from ..policy import (
    DYNAMIC_IDF_NORMAL,
    DYNAMIC_IDF_SMALL_POOL,
    HOT_MAX_DEFAULT,
    SMALL_POOL_THRESHOLD,
    WARM_HARD_MAX,
)
from .evidence import compute_base_utility


@dataclass(frozen=True)
class SelectionResult:
    hot: list[ScoredResult] = field(default_factory=list)
    warm: list[ScoredResult] = field(default_factory=list)
    shadow_matches: list[ScoredResult] = field(default_factory=list)


_MMR_PENALTY_WEIGHT: float = 2.0
_WARM_MIN_THRESHOLD: float = 1.0
_DIVERSITY_OVERLAP_MAX: float = 0.80
_FORMAT_OVERHEAD: int = 25


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
    max_sim = max(jaccard(candidate_tokens, selected) for selected in selected_tokens_list)
    return max_sim * _MMR_PENALTY_WEIGHT


def compute_utility(
    scored_result: ScoredResult,
    selected_tokens_list: list[frozenset[str]] | None = None,
) -> float:
    # All eligible results have trigger_evidence_passed=True (guaranteed by
    # applicability.py). Fallback handles unevaluated results (e.g. web API).
    if scored_result.trigger_evidence_passed:
        base = scored_result.ranking_utility
    else:
        base = compute_base_utility(
            trigger_idf_sum=scored_result.trigger_idf_sum,
            strong_variant_phrase_hit=scored_result.strong_variant_phrase_hit,
            rule_status=scored_result.rule.status,
            observed_usefulness_score=scored_result.rule.observed_usefulness_score,
            false_positive_score=scored_result.rule.false_positive_score,
            eligible=True,
        )

    near_duplicate_penalty = mmr_penalty(
        scored_result.matched_trigger_tokens,
        selected_tokens_list or [],
    )
    return base - near_duplicate_penalty


def _has_distinct_domain(candidate: ScoredResult, selected: list[ScoredResult]) -> bool:
    candidate_domains = set(candidate.rule.domain_tags) if candidate.rule.domain_tags else set()
    candidate_groups = frozenset(
        g.get("id", "") for g in (candidate.rule.required_concept_groups or [])
    )

    for s in selected:
        s_domains = set(s.rule.domain_tags) if s.rule.domain_tags else set()
        s_groups = frozenset(g.get("id", "") for g in (s.rule.required_concept_groups or []))
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

    # Greedy approximation: sort by utility without MMR penalty, then apply MMR
    # during selection. This avoids O(n^2) re-sorting but may skip near-optimal
    # candidates when tokens overlap heavily.
    scored_with_utility: list[tuple[float, ScoredResult]] = []
    for sr in eligible_results:
        u = compute_utility(sr, selected_tokens_list=None)
        scored_with_utility.append((u, sr))

    scored_with_utility.sort(key=lambda x: x[0], reverse=True)

    hot: list[ScoredResult] = []
    warm: list[ScoredResult] = []
    shadow_matches: list[ScoredResult] = []
    selected_tokens: list[frozenset[str]] = []
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
        idf_policy = (
            DYNAMIC_IDF_SMALL_POOL if pool_size < SMALL_POOL_THRESHOLD else DYNAMIC_IDF_NORMAL
        )
        for _initial_utility, sr in scored_with_utility:
            if sr is hot[0]:
                continue
            if has_runtime_levels and sr.level not in ("hot", "gate"):
                continue
            u = compute_utility(sr, selected_tokens_list=selected_tokens)
            if u <= 0:
                continue
            has_strong_evidence = (
                (sr.strong_variant_phrase_hit and sr.required_concepts_match)
                or sr.level == "gate"
                or (
                    sr.trigger_idf_sum >= idf_policy.absolute_trigger_info_min
                    and sr.trigger_coverage >= idf_policy.trigger_coverage_min
                    and sr.required_concepts_match
                    and sr.distinct_trigger_terms >= idf_policy.distinct_trigger_terms_min
                )
            )
            if _has_distinct_domain(sr, hot) and has_strong_evidence:
                hot.append(sr)
                selected_tokens.append(sr.matched_trigger_tokens)
                break

    hot_set = set(id(sr) for sr in hot)
    chars_used = 0

    prev_warm_utility: float | None = None
    for _initial_utility, sr in scored_with_utility:
        if id(sr) in hot_set:
            continue
        if has_runtime_levels and sr.level not in ("warm", "hot", "gate"):
            shadow_matches.append(sr)
            continue
        if len(warm) >= warm_hard_max:
            shadow_matches.append(sr)
            break

        u = compute_utility(sr, selected_tokens_list=selected_tokens)

        if selected_tokens:
            max_overlap = max(jaccard(sr.matched_trigger_tokens, st) for st in selected_tokens)
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
