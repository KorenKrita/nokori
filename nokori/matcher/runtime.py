"""Deterministic runtime matcher evaluation for compiled rules.

Evaluates a CompiledMatcher against prompt text and optional tool/path/tag
context. Produces a MatchResult with all matched concept/group/variant/exclusion
details and trigger coverage.

No LLM calls. No imports from other nokori modules. Uses only stdlib + re.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from nokori.matcher.compiler import (
    CompiledAlias,
    CompiledConcept,
    CompiledConceptGroup,
    CompiledExcludedContext,
    CompiledMatcher,
    CompiledVariant,
    GENERIC_TOKENS,
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
        # For near_trigger_span, we search the full text but the semantic
        # constraint is proximity. Since we don't have span positions in this
        # simplified evaluation, we match against the full combined text.
        # A more advanced implementation could use token position windows.
        search_text = text_lower
    else:
        search_text = text_lower

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

    # Check token anchors
    for tok in anchors.anchor_tokens:
        if tok in prompt_tokens:
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
    satisfied_concepts: set[str] = set()
    for concept in matcher.concepts:
        if _evaluate_concept(
            concept,
            combined_lower,
            prompt_tokens,
            tool_name_lower,
            tool_input_lower,
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
    for ctx in matcher.excluded_contexts:
        if _evaluate_excluded_context(
            ctx, combined_lower, tool_input_lower, text_lower
        ):
            # Check if override is allowed and override_requires are met
            if ctx.override_allowed and ctx.override_requires:
                # All override_requires must be present for the override
                all_overrides_met = all(
                    _text_contains_phrase(combined_lower, req.lower())
                    for req in ctx.override_requires
                )
                if all_overrides_met:
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
    )
