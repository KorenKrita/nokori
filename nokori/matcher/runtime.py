"""Deterministic runtime matcher evaluation for compiled rules.

Evaluates a CompiledMatcher against prompt text and optional tool/path/tag
context. Produces a MatchResult with all matched concept/group/variant/exclusion
details and trigger coverage.

No LLM calls. Uses stdlib + re + shared policy/applicability imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from nokori.matcher.compiler import (
    CompiledAlias,
    CompiledConcept,
    CompiledConceptGroup,
    CompiledExcludedContext,
    CompiledMatcher,
    CompiledVariant,
)
from nokori.policy import (
    DYNAMIC_IDF_NORMAL,
    DYNAMIC_IDF_SMALL_POOL,
    SMALL_POOL_THRESHOLD,
)
from nokori.runtime.applicability import (
    _trigger_evidence_passes,
    _strong_trigger_evidence,
)

# ---------------------------------------------------------------------------
# Token splitting (same logic as compiler)
# ---------------------------------------------------------------------------

_TOKEN_SPLIT_RE = re.compile(r"[\s/\-_.,:;!?\"'`()\[\]{}]+")


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens."""
    return [t for t in _TOKEN_SPLIT_RE.split(text.lower()) if t]


# ---------------------------------------------------------------------------
# MatchResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchResult:
    matched_concept_ids: frozenset[str]
    matched_group_ids: frozenset[str]
    strong_variant_hits: tuple[str, ...]
    weak_variant_hits: tuple[str, ...]
    excluded_context_hits: tuple[str, ...]
    matched_trigger_anchors: frozenset[str]
    action_only_match: bool
    search_only_match: bool
    required_concepts_match: bool
    trigger_coverage: float
    trigger_idf_sum: float = 0.0
    distinct_trigger_terms: int = 0
    trigger_evidence_passed: bool = False
    strong_trigger_evidence: bool = False


# ---------------------------------------------------------------------------
# Internal evaluation helpers
# ---------------------------------------------------------------------------


def _text_contains_phrase(text_lower: str, phrase_lower: str) -> bool:
    """Check if text contains phrase as substring (case-insensitive, pre-lowered)."""
    return phrase_lower in text_lower


def _check_neighbor_present(
    neighbors: tuple[str, ...],
    text_lower: str,
    prompt_tokens: frozenset[str],
    tool_name_lower: Optional[str],
    path_hints_lower: tuple[str, ...],
    project_tags_lower: frozenset[str],
) -> bool:
    """Check if any required neighbor term is present in the combined context."""
    for neighbor in neighbors:
        neighbor_lower = neighbor.lower()
        # Check prompt text
        if neighbor_lower in text_lower:
            return True
        # Check tool name
        if tool_name_lower and neighbor_lower in tool_name_lower:
            return True
        # Check path hints
        for path in path_hints_lower:
            if neighbor_lower in path:
                return True
        # Check project tags
        if neighbor_lower in project_tags_lower:
            return True
    return False


def _evaluate_alias(
    alias: CompiledAlias,
    match_mode: str,
    text_lower: str,
    prompt_tokens: frozenset[str],
    tool_name_lower: Optional[str],
    tool_input_lower: Optional[str],
    path_hints_lower: tuple[str, ...],
    project_tags_lower: frozenset[str],
) -> bool:
    """Evaluate whether a single alias matches in the given context.

    Returns True if the alias text is found. For weak aliases, the caller
    must separately check neighbor requirements.
    """
    if match_mode == "tool_pattern":
        # Match against tool name
        if tool_name_lower and alias.text_lower in tool_name_lower:
            return True
        # Also check tool input
        if tool_input_lower and alias.text_lower in tool_input_lower:
            return True
        return False

    if match_mode == "regex":
        if alias.compiled_pattern and alias.compiled_pattern.search(text_lower):
            return True
        if tool_input_lower and alias.compiled_pattern and alias.compiled_pattern.search(tool_input_lower):
            return True
        return False

    if match_mode == "all_terms":
        # All tokens in the alias must appear in the prompt tokens
        if alias.tokens and all(t in prompt_tokens for t in alias.tokens):
            return True
        return False

    if match_mode in ("any_alias", "phrase"):
        # Substring match
        if alias.compiled_pattern and alias.compiled_pattern.search(text_lower):
            return True
        if tool_input_lower and alias.compiled_pattern and alias.compiled_pattern.search(tool_input_lower):
            return True
        return False

    return False


def _evaluate_concept(
    concept: CompiledConcept,
    text_lower: str,
    prompt_tokens: frozenset[str],
    tool_name_lower: Optional[str],
    tool_input_lower: Optional[str],
    path_hints_lower: tuple[str, ...],
    project_tags_lower: frozenset[str],
) -> bool:
    """Evaluate whether a concept is satisfied.

    A concept is satisfied when at least one alias matches. For weak aliases,
    neighbor requirements must also be satisfied. A concept satisfied only by
    weak aliases (without neighbor evidence) is NOT satisfied.
    """
    has_strong_match = False

    for alias in concept.aliases:
        matched = _evaluate_alias(
            alias,
            concept.match_mode,
            text_lower,
            prompt_tokens,
            tool_name_lower,
            tool_input_lower,
            path_hints_lower,
            project_tags_lower,
        )
        if not matched:
            continue

        if alias.strength == "strong":
            has_strong_match = True
            break  # Strong match immediately satisfies

        # Weak alias: check neighbor requirements
        if alias.requires_neighbor:
            neighbor_ok = _check_neighbor_present(
                alias.requires_neighbor,
                text_lower,
                prompt_tokens,
                tool_name_lower,
                path_hints_lower,
                project_tags_lower,
            )
            if neighbor_ok:
                has_strong_match = True
                break

    return has_strong_match


def _evaluate_concept_groups(
    groups: tuple[CompiledConceptGroup, ...],
    satisfied_concepts: frozenset[str],
) -> tuple[bool, frozenset[str]]:
    """Evaluate required concept groups.

    Returns (any_group_matched, set_of_matched_group_ids).
    A group matches when ALL its concept ids are in satisfied_concepts.
    """
    matched_groups: set[str] = set()
    for group in groups:
        if all(cid in satisfied_concepts for cid in group.all_of):
            matched_groups.add(group.id)

    return (len(matched_groups) > 0, frozenset(matched_groups))


def _evaluate_variant(
    variant: CompiledVariant,
    text_lower: str,
    tool_input_lower: Optional[str],
) -> bool:
    """Check if a variant phrase is present in text."""
    if variant.compiled_pattern:
        if variant.compiled_pattern.search(text_lower):
            return True
        if tool_input_lower and variant.compiled_pattern.search(tool_input_lower):
            return True
    return False


def _evaluate_excluded_context(
    ctx: CompiledExcludedContext,
    text_lower: str,
    tool_input_lower: Optional[str],
    prompt_only_lower: str,
    *,
    trigger_anchor_tokens: frozenset[str],
    trigger_anchor_phrases: tuple[str, ...],
) -> bool:
    """Check if an excluded context pattern matches within its configured scope.

    Returns True if the exclusion fires (meaning the rule should be suppressed).
    """
    # Determine which text to search based on scope
    if ctx.scope == "tool_input_only":
        if not tool_input_lower:
            return False
        search_text = tool_input_lower
    elif ctx.scope == "prompt_only":
        search_text = prompt_only_lower
    elif ctx.scope == "global":
        search_text = text_lower
    elif ctx.scope == "near_trigger_span":
        search_texts = _near_trigger_window_texts(
            text_lower,
            window_tokens=ctx.window_tokens,
            trigger_anchor_tokens=trigger_anchor_tokens,
            trigger_anchor_phrases=trigger_anchor_phrases,
        )
        if not search_texts:
            return False
        return any(_excluded_context_matches(ctx, search_text) for search_text in search_texts)
    else:
        search_text = text_lower

    return _excluded_context_matches(ctx, search_text)


def _excluded_context_matches(ctx: CompiledExcludedContext, search_text: str) -> bool:
    # Match based on mode
    if ctx.match_mode == "regex":
        for pattern in ctx.compiled_patterns:
            if pattern.search(search_text):
                return True
    elif ctx.match_mode in ("phrase", "negative_context_detector"):
        for pattern in ctx.compiled_patterns:
            if pattern.search(search_text):
                return True
    elif ctx.match_mode == "tool_pattern":
        for pat in ctx.patterns_lower:
            if pat in search_text:
                return True

    return False


def _near_trigger_window_texts(
    text_lower: str,
    *,
    window_tokens: int,
    trigger_anchor_tokens: frozenset[str],
    trigger_anchor_phrases: tuple[str, ...],
) -> tuple[str, ...]:
    """Return token windows around matched trigger anchors.

    near_trigger_span suppressors are intentionally scoped to proximity. We
    derive trigger positions from matched anchor tokens and phrases, then only
    evaluate exclusions inside +/- window_tokens around those positions.
    """
    tokens = _tokenize(text_lower)
    if not tokens:
        return ()

    trigger_positions: set[int] = {
        idx for idx, tok in enumerate(tokens) if tok in trigger_anchor_tokens
    }

    for phrase in trigger_anchor_phrases:
        phrase_tokens = _tokenize(phrase)
        if not phrase_tokens:
            continue
        width = len(phrase_tokens)
        for idx in range(0, len(tokens) - width + 1):
            if tokens[idx : idx + width] == phrase_tokens:
                trigger_positions.update(range(idx, idx + width))

    if not trigger_positions:
        return ()

    radius = max(0, int(window_tokens))
    windows: list[str] = []
    seen: set[tuple[int, int]] = set()
    for pos in sorted(trigger_positions):
        start = max(0, pos - radius)
        end = min(len(tokens), pos + radius + 1)
        span = (start, end)
        if span in seen:
            continue
        seen.add(span)
        windows.append(" ".join(tokens[start:end]))
    return tuple(windows)


def _compute_trigger_coverage(
    matcher: CompiledMatcher,
    prompt_tokens: frozenset[str],
    text_lower: str,
) -> tuple[float, frozenset[str]]:
    """Compute trigger coverage: matched_anchors / total_anchors.

    Returns (coverage_float, set_of_matched_anchor_tokens).
    """
    anchors = matcher.trigger_anchors
    matched: set[str] = set()

    # Check token anchors against both prompt_tokens and combined_lower tokens
    combined_tokens = frozenset(_tokenize(text_lower))
    for tok in anchors.anchor_tokens:
        if tok in prompt_tokens or tok in combined_tokens:
            matched.add(tok)

    # Check phrase anchors
    for phrase in anchors.anchor_phrases:
        if phrase in text_lower:
            matched.add(phrase)

    coverage = len(matched) / anchors.total_anchors
    return (coverage, frozenset(matched))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_match(
    matcher: CompiledMatcher,
    prompt_text: str,
    tool_name: Optional[str] = None,
    tool_input: Optional[str] = None,
    path_hints: Optional[list[str]] = None,
    project_tags: Optional[list[str]] = None,
    idf_stats: Optional[dict] = None,
) -> MatchResult:
    """Evaluate a compiled matcher against prompt/tool context.

    Deterministic. No LLM calls.

    Args:
        matcher: A CompiledMatcher from compile_rule().
        prompt_text: The user prompt or combined text to match against.
        tool_name: Optional tool name being invoked (for tool_pattern matching).
        tool_input: Optional stringified tool input (for tool_pattern matching).
        path_hints: Optional list of file/path strings for context.
        project_tags: Optional list of project-level tags.

    Returns:
        MatchResult with all match details and trigger_coverage.
    """
    # Prepare normalized inputs
    text_lower = prompt_text.lower()
    prompt_tokens = frozenset(_tokenize(prompt_text))
    tool_name_lower = tool_name.lower() if tool_name else None
    tool_input_lower = tool_input.lower() if tool_input else None
    path_hints_lower = tuple(p.lower() for p in (path_hints or []))
    project_tags_lower = frozenset(t.lower() for t in (project_tags or []))

    # Combined text for general matching includes prompt + tool input
    combined_lower = text_lower
    if tool_input_lower:
        combined_lower = text_lower + " " + tool_input_lower

    # 1. Evaluate concepts
    # Per spec 9.2 field-level scoring: concepts evaluate against prompt text
    # only. Tool input is only checked when concept has match_mode "tool_pattern".
    satisfied_concepts: set[str] = set()
    for concept in matcher.concepts:
        concept_text = text_lower
        concept_tool_input = tool_input_lower
        if _evaluate_concept(
            concept,
            concept_text,
            prompt_tokens,
            tool_name_lower,
            concept_tool_input,
            path_hints_lower,
            project_tags_lower,
        ):
            satisfied_concepts.add(concept.id)

    matched_concept_ids = frozenset(satisfied_concepts)

    # 2. Evaluate concept groups
    required_concepts_match, matched_group_ids = _evaluate_concept_groups(
        matcher.concept_groups, matched_concept_ids
    )

    # 3. Evaluate variants
    strong_variant_hits: list[str] = []
    weak_variant_hits: list[str] = []

    for variant in matcher.variants:
        if not _evaluate_variant(variant, combined_lower, tool_input_lower):
            continue

        if variant.kind == "strong_anchor":
            # Strong anchor only counts if its required concepts are satisfied
            if all(cid in satisfied_concepts for cid in variant.requires_concepts):
                strong_variant_hits.append(variant.text)
        else:
            weak_variant_hits.append(variant.text)

    # 4. Evaluate excluded contexts
    excluded_context_hits: list[str] = []
    anchor_tokens = matcher.trigger_anchors.anchor_tokens
    anchor_phrases = matcher.trigger_anchors.anchor_phrases
    for ctx in matcher.excluded_contexts:
        if _evaluate_excluded_context(
            ctx,
            combined_lower,
            tool_input_lower,
            text_lower,
            trigger_anchor_tokens=anchor_tokens,
            trigger_anchor_phrases=anchor_phrases,
        ):
            # Check if override is allowed and override_requires are met
            if ctx.override_allowed and (not ctx.override_requires or all(
                _text_contains_phrase(combined_lower, req.lower())
                for req in ctx.override_requires
            )):
                continue  # Override applies, exclusion does not fire

            excluded_context_hits.append(ctx.id)

    # 5. Compute trigger coverage
    trigger_coverage, matched_anchors = _compute_trigger_coverage(
        matcher, prompt_tokens, combined_lower
    )

    # 6. Determine action_only and search_only flags
    # action_only: no trigger/concept/variant evidence at all
    has_trigger_evidence = (
        bool(strong_variant_hits)
        or required_concepts_match
        or trigger_coverage > 0.0
    )
    action_only_match = not has_trigger_evidence

    # search_only: search terms match but no trigger evidence
    # (We check if any search terms appear in text but trigger evidence is absent)
    search_term_hit = False
    for _lang, terms in matcher.search_terms.items():
        for term in terms:
            if term.lower() in combined_lower:
                search_term_hit = True
                break
        if search_term_hit:
            break

    search_only_match = search_term_hit and not has_trigger_evidence

    # 7. Compute trigger IDF sum and distinct trigger terms if idf_stats provided
    # (idf_stats dict keys: pool_size, df_by_token, is_shadow, idf_max,
    #  dynamic_threshold, trigger_coverage_min, distinct_trigger_terms_min)
    trigger_idf_sum = 0.0
    distinct_trigger_terms = 0
    if idf_stats and matched_anchors:
        pool_size = idf_stats.get("pool_size", 0)
        df_by_token = idf_stats.get("df_by_token", {})
        is_shadow = idf_stats.get("is_shadow", False)
        idf_max = idf_stats.get("idf_max", 3.0)
        if pool_size > 0:
            import math
            seen_terms: set[str] = set()
            for token in matched_anchors:
                if ' ' in token:
                    continue
                if token in seen_terms:
                    continue
                seen_terms.add(token)
                df_t = df_by_token.get(token, 0)
                if is_shadow:
                    # Shadow IDF: df_effective = max(1, df), cap at idf_max (spec 9.3)
                    df_effective = max(1, df_t)
                    raw_idf = math.log(
                        1 + (pool_size - df_effective + 0.5) / (df_effective + 0.5)
                    )
                    trigger_idf_sum += min(raw_idf, idf_max)
                else:
                    # Normal IDF uses raw df_trigger(t) per spec formula.
                    # df=0 means novel term not in any rule — still computable
                    # but should not appear since matched_anchors come from rules.
                    if df_t > 0:
                        trigger_idf_sum += math.log(
                            1 + (pool_size - df_t + 0.5) / (df_t + 0.5)
                        )
                    else:
                        # Novel term safety: use df=1 as minimum
                        trigger_idf_sum += math.log(
                            1 + (pool_size - 0.5) / 1.5
                        )
            distinct_trigger_terms = len(seen_terms)

    # 8. Evaluate trigger evidence pass/fail (spec section 9.3)
    # Excluded context match forces COLD regardless of trigger evidence (spec 9.4)
    has_exclusion = bool(excluded_context_hits)
    trigger_evidence_passed = (not has_exclusion) and _evaluate_trigger_evidence(
        strong_variant_phrase_hit=bool(strong_variant_hits),
        required_concepts_match=required_concepts_match,
        trigger_idf_sum=trigger_idf_sum,
        trigger_coverage=trigger_coverage,
        distinct_trigger_terms=distinct_trigger_terms,
        idf_stats=idf_stats,
    )
    strong_evidence = (not has_exclusion) and _evaluate_strong_trigger_evidence(
        strong_variant_phrase_hit=bool(strong_variant_hits),
        required_concepts_match=required_concepts_match,
        trigger_idf_sum=trigger_idf_sum,
        trigger_coverage=trigger_coverage,
        distinct_trigger_terms=distinct_trigger_terms,
        idf_stats=idf_stats,
    )

    return MatchResult(
        matched_concept_ids=matched_concept_ids,
        matched_group_ids=matched_group_ids,
        strong_variant_hits=tuple(strong_variant_hits),
        weak_variant_hits=tuple(weak_variant_hits),
        excluded_context_hits=tuple(excluded_context_hits),
        matched_trigger_anchors=matched_anchors,
        action_only_match=action_only_match,
        search_only_match=search_only_match,
        required_concepts_match=required_concepts_match,
        trigger_coverage=trigger_coverage,
        trigger_idf_sum=trigger_idf_sum,
        distinct_trigger_terms=distinct_trigger_terms,
        trigger_evidence_passed=trigger_evidence_passed,
        strong_trigger_evidence=strong_evidence,
    )


# ---------------------------------------------------------------------------
# Trigger evidence pass/fail evaluation (spec section 9.3)
#
# Canonical implementations live in nokori.runtime.applicability. The adapters
# below translate the idf_stats dict interface used by evaluate_match into the
# scalar parameters expected by the canonical functions.
#
# Note on strong_variant_phrase_hit path: the synthetic eval validation for
# variants is enforced at compilation/insertion time (compile_rule rejects
# unvalidated strong_anchors), not at runtime. This documents that the spec
# requirement is handled by the cold pipeline's compilation gate.
# ---------------------------------------------------------------------------


def _evaluate_trigger_evidence(
    *,
    strong_variant_phrase_hit: bool,
    required_concepts_match: bool,
    trigger_idf_sum: float,
    trigger_coverage: float,
    distinct_trigger_terms: int,
    idf_stats: Optional[dict],
) -> bool:
    """Adapter: delegates to applicability._trigger_evidence_passes."""
    if idf_stats is None:
        pool_size = 0
        idf_stats_available = False
        dynamic_trigger_info_min = None
    else:
        pool_size = idf_stats.get("pool_size", 0)
        idf_stats_available = pool_size > 0
        dynamic_trigger_info_min = idf_stats.get("dynamic_threshold")

    return _trigger_evidence_passes(
        strong_variant_phrase_hit=strong_variant_phrase_hit,
        required_concepts_match=required_concepts_match,
        trigger_idf_sum=trigger_idf_sum,
        trigger_coverage=trigger_coverage,
        distinct_trigger_terms=distinct_trigger_terms,
        idf_stats_available=idf_stats_available,
        pool_size=pool_size,
        dynamic_trigger_info_min=dynamic_trigger_info_min,
    )


def _evaluate_strong_trigger_evidence(
    *,
    strong_variant_phrase_hit: bool,
    required_concepts_match: bool,
    trigger_idf_sum: float,
    trigger_coverage: float,
    distinct_trigger_terms: int,
    idf_stats: Optional[dict],
) -> bool:
    """Adapter: delegates to applicability._strong_trigger_evidence."""
    if idf_stats is None:
        pool_size = 0
        idf_stats_available = False
        dynamic_trigger_info_min = None
    else:
        pool_size = idf_stats.get("pool_size", 0)
        idf_stats_available = pool_size > 0
        dynamic_trigger_info_min = idf_stats.get("dynamic_threshold")

    return _strong_trigger_evidence(
        strong_variant_phrase_hit=strong_variant_phrase_hit,
        required_concepts_match=required_concepts_match,
        trigger_idf_sum=trigger_idf_sum,
        trigger_coverage=trigger_coverage,
        distinct_trigger_terms=distinct_trigger_terms,
        idf_stats_available=idf_stats_available,
        pool_size=pool_size,
        dynamic_trigger_info_min=dynamic_trigger_info_min,
    )


def compute_dynamic_threshold(pool_size: int) -> dict:
    """Compute dynamic trigger threshold values per spec formula.

    Given a pool_size (N = number of rules in the active pool), computes:
      - rare_df: floor(N * 0.10) clamped to min 1
      - idf_10pct: log(1 + (N - rare_df + 0.5) / (rare_df + 0.5))
      - dynamic_trigger_info_min = max(2 * idf_10pct, absolute_min)

    This allows callers to validate their passed dynamic_threshold against
    the spec-defined formula.

    Args:
        pool_size: Number of rules in the active pool (N).

    Returns:
        Dict with keys: pool_size, rare_df, idf_10pct, dynamic_trigger_info_min.
    """
    import math

    if pool_size <= 0:
        return {
            "pool_size": 0,
            "rare_df": 0,
            "idf_10pct": 0.0,
            "dynamic_trigger_info_min": 0.0,
            "dynamic_threshold": 0.0,
        }

    rare_df = max(1, math.ceil(pool_size * 0.10))
    idf_10pct = math.log(1 + (pool_size - rare_df + 0.5) / (rare_df + 0.5))

    if pool_size < SMALL_POOL_THRESHOLD:
        absolute_min = DYNAMIC_IDF_SMALL_POOL.absolute_trigger_info_min
    else:
        absolute_min = DYNAMIC_IDF_NORMAL.absolute_trigger_info_min

    # Spec: dynamic_trigger_info_min = 2 * idf_10pct; trigger_info_min = max(dynamic, absolute)
    dynamic_trigger_info_min = max(2 * idf_10pct, absolute_min)

    return {
        "pool_size": pool_size,
        "rare_df": rare_df,
        "idf_10pct": idf_10pct,
        "dynamic_trigger_info_min": dynamic_trigger_info_min,
        "dynamic_threshold": dynamic_trigger_info_min,
    }
