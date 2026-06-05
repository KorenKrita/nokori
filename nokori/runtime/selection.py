"""HOT/WARM injection selection with budget and diversity (section 9.6).

Selects which eligible rules are injected into the prompt, respecting
character budgets, hard counts, marginal utility decay, and MMR diversity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nokori.models import ScoredResult
from nokori.policy import DYNAMIC_IDF_SMALL_POOL, HOT_MAX_DEFAULT, WARM_HARD_MAX


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionResult:
    """Immutable result of injection selection."""

    hot: list[ScoredResult] = field(default_factory=list)
    warm: list[ScoredResult] = field(default_factory=list)
    shadow_matches: list[ScoredResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# MMR diversity penalty
# ---------------------------------------------------------------------------

_MMR_PENALTY_WEIGHT: float = 2.0


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def mmr_penalty(
    candidate_tokens: frozenset[str],
    selected_tokens_list: list[frozenset[str]],
) -> float:
    """Max Jaccard similarity with any selected rule * penalty weight."""
    if not selected_tokens_list:
        return 0.0
    max_sim = max(
        _jaccard(candidate_tokens, selected) for selected in selected_tokens_list
    )
    return max_sim * _MMR_PENALTY_WEIGHT


# ---------------------------------------------------------------------------
# Utility computation
# ---------------------------------------------------------------------------


def compute_utility(
    scored_result: ScoredResult,
    idf_stats: dict[str, float],
    selected_tokens_list: list[frozenset[str]] | None = None,
) -> float:
    """Compute marginal utility for a candidate rule.

    Parameters
    ----------
    scored_result:
        The fielded scoring result for this rule.
    idf_stats:
        Mapping of trigger tokens to their IDF values (unused beyond
        trigger_idf_sum which is pre-computed on ScoredResult).
    selected_tokens_list:
        Trigger token sets of already-selected rules, for MMR penalty.
    """
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

    utility = (
        trigger_idf_sum
        + variant_phrase_bonus
        + trusted_or_usefulness_bonus
        - near_duplicate_penalty
        - recent_false_positive_penalty
    )
    return utility


# ---------------------------------------------------------------------------
# Injection selection
# ---------------------------------------------------------------------------

_WARM_MIN_THRESHOLD: float = 1.0
_DIVERSITY_OVERLAP_MAX: float = 0.80


def _has_distinct_domain(candidate: ScoredResult, selected: list[ScoredResult]) -> bool:
    """Check if candidate has a distinct domain/required-concept set from all selected.

    Spec 9.6: second HOT requires 'distinct domain/required-concept set'.
    """
    from nokori.db import loads_json

    candidate_domains = set(candidate.rule.domain_tags) if candidate.rule.domain_tags else set()
    candidate_groups = frozenset(
        g.get("id", "") for g in (loads_json(candidate.rule.required_concept_groups, [])
                                   if isinstance(candidate.rule.required_concept_groups, str)
                                   else (candidate.rule.required_concept_groups or []))
    )

    for s in selected:
        s_domains = set(s.rule.domain_tags) if s.rule.domain_tags else set()
        s_groups = frozenset(
            g.get("id", "") for g in (loads_json(s.rule.required_concept_groups, [])
                                       if isinstance(s.rule.required_concept_groups, str)
                                       else (s.rule.required_concept_groups or []))
        )
        # Both domain AND concept groups must differ for distinctness
        if candidate_domains == s_domains and candidate_groups == s_groups:
            return False
    return True


def _char_len(result: ScoredResult) -> int:
    """Estimate injected character length for a rule."""
    rule = result.rule
    return len(rule.trigger_canonical) + len(rule.action_instruction)


def select_injection(
    eligible_results: list[ScoredResult],
    max_injection_chars: int,
    warm_hard_max: int = WARM_HARD_MAX,
) -> SelectionResult:
    """Select rules for HOT/WARM injection from eligible candidates.

    Parameters
    ----------
    eligible_results:
        Rules that already passed applicability checks.
    max_injection_chars:
        Character budget for WARM injection payload.
    warm_hard_max:
        Hard cap on WARM slots (default from policy).

    Returns
    -------
    SelectionResult with hot, warm, and shadow_matches lists.
    """
    if not eligible_results:
        return SelectionResult()

    # Compute initial utility (no MMR penalty for first pass ranking)
    scored_with_utility: list[tuple[float, ScoredResult]] = []
    for sr in eligible_results:
        u = compute_utility(sr, {}, selected_tokens_list=None)
        scored_with_utility.append((u, sr))

    scored_with_utility.sort(key=lambda x: x[0], reverse=True)

    hot: list[ScoredResult] = []
    warm: list[ScoredResult] = []
    shadow_matches: list[ScoredResult] = []
    selected_tokens: list[frozenset[str]] = []
    chars_used = 0
    has_runtime_levels = any(sr.level is not None for _, sr in scored_with_utility)

    # --- HOT selection (default max 1, second allowed under strict conditions) ---
    hot_max = HOT_MAX_DEFAULT
    for _initial_utility, sr in scored_with_utility:
        if has_runtime_levels and sr.level not in ("hot", "gate"):
            continue
        if len(hot) >= hot_max:
            break
        # Recompute with MMR against already-selected
        u = compute_utility(sr, {}, selected_tokens_list=selected_tokens)
        if u <= 0:
            continue
        hot.append(sr)
        selected_tokens.append(sr.matched_trigger_tokens)

    # Allow second HOT only if distinct domain/concept set AND strong trigger evidence
    # Strong = Path A (strong_variant_phrase_hit) OR strong trigger_idf_sum (Path B)
    if len(hot) == 1 and len(scored_with_utility) > 1:
        for _initial_utility, sr in scored_with_utility:
            if sr is hot[0]:
                continue
            if has_runtime_levels and sr.level not in ("hot", "gate"):
                continue
            u = compute_utility(sr, {}, selected_tokens_list=selected_tokens)
            if u <= 0:
                continue
            # Strong evidence per spec 9.6: Path A or full Path B.
            # sr.level=='gate' implies strong evidence; sr.level=='hot' does NOT
            # (trusted rules get hot without strong evidence requirement).
            has_strong_evidence = (
                (sr.strong_variant_phrase_hit and sr.required_concepts_match)
                or sr.level == "gate"
                or (
                    sr.trigger_idf_sum >= DYNAMIC_IDF_SMALL_POOL.absolute_trigger_info_min
                    and sr.trigger_coverage >= DYNAMIC_IDF_SMALL_POOL.trigger_coverage_min
                    and sr.required_concepts_match
                    and sr.distinct_trigger_terms >= DYNAMIC_IDF_SMALL_POOL.distinct_trigger_terms_min
                )
            )
            if _has_distinct_domain(sr, hot) and has_strong_evidence:
                hot.append(sr)
                selected_tokens.append(sr.matched_trigger_tokens)
                break

    hot_set = set(id(sr) for sr in hot)

    # --- WARM selection ---
    prev_warm_utility: float | None = None

    for _initial_utility, sr in scored_with_utility:
        if id(sr) in hot_set:
            continue
        if has_runtime_levels and sr.level not in ("warm", "hot", "gate"):
            shadow_matches.append(sr)
            continue
        if len(warm) >= warm_hard_max:
            break

        # Recompute utility with MMR against all selected so far
        u = compute_utility(sr, {}, selected_tokens_list=selected_tokens)

        # Diversity gate: skip if >80% trigger token overlap with any selected
        if selected_tokens:
            max_overlap = max(
                _jaccard(sr.matched_trigger_tokens, st) for st in selected_tokens
            )
            if max_overlap > _DIVERSITY_OVERLAP_MAX:
                shadow_matches.append(sr)
                continue

        # 3rd+ WARM must satisfy marginal utility decay rule
        if len(warm) >= 2:
            threshold = max(
                _WARM_MIN_THRESHOLD,
                (prev_warm_utility or 0.0) * 0.80,
            )
            if u < threshold:
                shadow_matches.append(sr)
                continue

        # Character budget check
        cost = _char_len(sr)
        if chars_used + cost > max_injection_chars:
            shadow_matches.append(sr)
            continue

        warm.append(sr)
        selected_tokens.append(sr.matched_trigger_tokens)
        chars_used += cost
        prev_warm_utility = u

    # Remaining candidates go to shadow
    selected_ids = hot_set | set(id(sr) for sr in warm) | set(id(sr) for sr in shadow_matches)
    for _u, sr in scored_with_utility:
        if id(sr) not in selected_ids:
            shadow_matches.append(sr)

    return SelectionResult(hot=hot, warm=warm, shadow_matches=shadow_matches)


# ---------------------------------------------------------------------------
# Legacy convenience wrapper
# ---------------------------------------------------------------------------

_DEFAULT_INJECTION_CHARS = 1500


def tier_results(
    results: list[ScoredResult],
    max_injection_chars: int = _DEFAULT_INJECTION_CHARS,
) -> tuple[list[ScoredResult], list[ScoredResult]]:
    """Legacy wrapper returning (hot, warm) tuple from select_injection."""
    sel = select_injection(results, max_injection_chars=max_injection_chars)
    return sel.hot, sel.warm
