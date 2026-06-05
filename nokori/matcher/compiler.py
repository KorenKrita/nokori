"""Deterministic matcher compiler for rule trigger/concept/variant/exclusion structures.

Compiles trigger_data dicts (from schema_types or raw JSON) into frozen
dataclass structures that the runtime can evaluate without LLM calls.

No imports from other nokori modules. Uses only stdlib + re.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

COMPILER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Generic tokens that cannot be strong anchors alone
# ---------------------------------------------------------------------------

GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "must",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "our",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "if",
        "then",
        "else",
        "and",
        "or",
        "but",
        "not",
        "no",
        "so",
        "as",
        "at",
        "by",
        "for",
        "in",
        "of",
        "on",
        "to",
        "up",
        "out",
        "off",
        "with",
        "from",
        "into",
        "over",
        "after",
        "before",
        "between",
        "under",
        "about",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "same",
        "than",
        "too",
        "very",
        "just",
        "also",
        "now",
        "here",
        "there",
        "use",
        "make",
        "get",
        "set",
        "run",
        "add",
        "put",
        "let",
        "try",
        "new",
        "old",
        "good",
        "bad",
        "big",
        "small",
        "long",
        "short",
        "first",
        "last",
        "next",
        "code",
        "file",
        "data",
        "type",
        "name",
        "value",
        "like",
        "want",
        "know",
        "think",
        "see",
        "look",
        "find",
        "give",
        "tell",
        "take",
        "come",
        "go",
        "work",
        "call",
        "thing",
        "way",
        "time",
        "day",
        "part",
        "done",
        "well",
    }
)

# ---------------------------------------------------------------------------
# Generic action words that should not be trigger anchors
# ---------------------------------------------------------------------------

GENERIC_ACTION_WORDS: frozenset[str] = frozenset(
    {
        "should",
        "must",
        "always",
        "never",
        "ensure",
        "make",
        "check",
        "verify",
        "avoid",
        "prevent",
        "handle",
        "implement",
    }
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CompilationError(ValueError):
    """Raised when trigger/concept/variant/exclusion data fails compilation."""


# ---------------------------------------------------------------------------
# Compiled dataclasses
# ---------------------------------------------------------------------------

AliasStrength = Literal["strong", "weak"]
ConceptMatchMode = Literal["any_alias", "phrase", "all_terms", "regex", "tool_pattern"]
VariantKind = Literal["strong_anchor", "weak_recall"]
ExcludedContextMatchMode = Literal[
    "negative_context_detector", "phrase", "regex", "tool_pattern"
]
ExcludedContextScope = Literal[
    "global", "near_trigger_span", "tool_input_only", "prompt_only"
]


@dataclass(frozen=True)
class CompiledAlias:
    text: str
    text_lower: str
    strength: AliasStrength
    requires_neighbor: tuple[str, ...] = ()
    # Pre-compiled pattern for the alias based on parent concept match_mode
    compiled_pattern: Optional[re.Pattern[str]] = field(default=None, repr=False)
    # For all_terms mode: pre-split tokens
    tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledConcept:
    id: str
    label: str
    match_mode: ConceptMatchMode
    required: bool
    aliases: tuple[CompiledAlias, ...]


@dataclass(frozen=True)
class CompiledConceptGroup:
    id: str
    all_of: tuple[str, ...]  # concept ids


@dataclass(frozen=True)
class CompiledVariant:
    text: str
    text_lower: str
    kind: VariantKind
    requires_concepts: tuple[str, ...]
    # Pre-compiled phrase pattern for matching
    compiled_pattern: Optional[re.Pattern[str]] = field(default=None, repr=False)
    tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledExcludedContext:
    id: str
    label: str
    match_mode: ExcludedContextMatchMode
    scope: ExcludedContextScope
    window_tokens: int
    override_allowed: bool
    override_requires: tuple[str, ...]
    # Pre-compiled patterns
    compiled_patterns: tuple[re.Pattern[str], ...] = field(default=(), repr=False)
    # Raw patterns for substring matching modes
    patterns_lower: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledTriggerAnchors:
    """The set of compiled trigger anchor tokens/phrases for coverage computation."""

    anchor_tokens: frozenset[str]
    anchor_phrases: tuple[str, ...]
    total_anchors: int


@dataclass(frozen=True)
class CompiledMatcher:
    compiler_version: str
    concepts: tuple[CompiledConcept, ...]
    concept_groups: tuple[CompiledConceptGroup, ...]
    variants: tuple[CompiledVariant, ...]
    excluded_contexts: tuple[CompiledExcludedContext, ...]
    trigger_anchors: CompiledTriggerAnchors
    # Search terms are recall-only, stored for reference
    search_terms: dict[str, tuple[str, ...]]


# ---------------------------------------------------------------------------
# Internal compilation helpers
# ---------------------------------------------------------------------------

_TOKEN_SPLIT_RE = re.compile(r"[\s/\-_.,:;!?\"'`()\[\]{}]+")


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens."""
    return [t for t in _TOKEN_SPLIT_RE.split(text.lower()) if t]


def _is_multi_token(text: str) -> bool:
    """Check whether text contains more than one token."""
    tokens = _tokenize(text)
    return len(tokens) > 1


def _is_single_generic_token(text: str) -> bool:
    """Check whether text is a single generic token."""
    tokens = _tokenize(text)
    return len(tokens) == 1 and tokens[0] in GENERIC_TOKENS


def _compile_phrase_pattern(text: str) -> re.Pattern[str]:
    """Create a case-insensitive regex for phrase substring matching."""
    escaped = re.escape(text.lower())
    return re.compile(escaped, re.IGNORECASE)


def _compile_regex_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a regex pattern, raising CompilationError on invalid regex."""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise CompilationError(f"Invalid regex pattern '{pattern}': {e}") from e


def _compile_alias(
    alias_data: dict[str, Any], match_mode: ConceptMatchMode
) -> CompiledAlias:
    """Compile a single alias entry."""
    text = alias_data.get("text", "")
    if not text:
        raise CompilationError("Alias text must not be empty")

    strength: AliasStrength = alias_data.get("strength", "strong")
    requires_neighbor = tuple(alias_data.get("requires_neighbor") or ())

    text_lower = text.lower()
    compiled_pattern: Optional[re.Pattern[str]] = None
    tokens: tuple[str, ...] = ()

    if match_mode == "phrase":
        compiled_pattern = _compile_phrase_pattern(text)
    elif match_mode == "regex":
        compiled_pattern = _compile_regex_pattern(text)
    elif match_mode == "all_terms":
        tokens = tuple(_tokenize(text))
    elif match_mode == "any_alias":
        # For any_alias, compile as phrase substring
        compiled_pattern = _compile_phrase_pattern(text)
    # tool_pattern: stored as-is, matched against tool name at runtime

    return CompiledAlias(
        text=text,
        text_lower=text_lower,
        strength=strength,
        requires_neighbor=requires_neighbor,
        compiled_pattern=compiled_pattern,
        tokens=tokens,
    )


def _compile_concept(data: dict[str, Any]) -> CompiledConcept:
    """Compile a concept definition."""
    concept_id = data.get("id", "")
    if not concept_id:
        raise CompilationError("Concept must have a non-empty 'id'")

    label = data.get("label", "")
    match_mode: ConceptMatchMode = data.get("match_mode", "any_alias")
    required = data.get("required", False)
    aliases_data = data.get("aliases", [])

    if not aliases_data:
        raise CompilationError(f"Concept '{concept_id}' must have at least one alias")

    compiled_aliases = tuple(
        _compile_alias(a, match_mode) for a in aliases_data
    )

    return CompiledConcept(
        id=concept_id,
        label=label,
        match_mode=match_mode,
        required=required,
        aliases=compiled_aliases,
    )


def _compile_concept_group(data: dict[str, Any]) -> CompiledConceptGroup:
    """Compile a required concept group."""
    group_id = data.get("id", "")
    if not group_id:
        raise CompilationError("Concept group must have a non-empty 'id'")

    all_of = data.get("all_of", [])
    if not all_of:
        raise CompilationError(
            f"Concept group '{group_id}' must have at least one concept in 'all_of'"
        )

    return CompiledConceptGroup(id=group_id, all_of=tuple(all_of))


def _compile_variant(data: dict[str, Any]) -> CompiledVariant:
    """Compile a variant entry."""
    text = data.get("text", "")
    if not text:
        raise CompilationError("Variant text must not be empty")

    kind: VariantKind = data.get("kind", "weak_recall")
    requires_concepts = tuple(data.get("requires_concepts") or ())

    # Validate strong_anchor constraints
    if kind == "strong_anchor":
        if not requires_concepts:
            raise CompilationError(
                f"strong_anchor variant '{text}' must have requires_concepts"
            )
        if not _is_multi_token(text):
            raise CompilationError(
                f"strong_anchor variant '{text}' must be multi-token"
            )
        if _is_single_generic_token(text):
            raise CompilationError(
                f"strong_anchor variant '{text}' cannot be a single generic token"
            )

    text_lower = text.lower()
    compiled_pattern = _compile_phrase_pattern(text)
    tokens = tuple(_tokenize(text))

    return CompiledVariant(
        text=text,
        text_lower=text_lower,
        kind=kind,
        requires_concepts=requires_concepts,
        compiled_pattern=compiled_pattern,
        tokens=tokens,
    )


def _compile_excluded_context(data: dict[str, Any]) -> CompiledExcludedContext:
    """Compile an excluded context entry."""
    ctx_id = data.get("id", "")
    if not ctx_id:
        raise CompilationError("Excluded context must have a non-empty 'id'")

    label = data.get("label", "")
    patterns = data.get("patterns", [])
    if not patterns:
        raise CompilationError(
            f"Excluded context '{ctx_id}' must have at least one pattern"
        )

    match_mode: ExcludedContextMatchMode = data.get("match_mode", "phrase")
    scope: ExcludedContextScope = data.get("scope", "global")
    window_tokens = data.get("window_tokens", 12)
    override_allowed = data.get("override_allowed", False)
    override_requires = tuple(data.get("override_requires") or ())

    compiled_patterns: list[re.Pattern[str]] = []
    patterns_lower: list[str] = []

    if match_mode == "regex":
        compiled_patterns = [_compile_regex_pattern(p) for p in patterns]
    elif match_mode in ("phrase", "negative_context_detector"):
        compiled_patterns = [_compile_phrase_pattern(p) for p in patterns]
        patterns_lower = [p.lower() for p in patterns]
    elif match_mode == "tool_pattern":
        patterns_lower = [p.lower() for p in patterns]

    return CompiledExcludedContext(
        id=ctx_id,
        label=label,
        match_mode=match_mode,
        scope=scope,
        window_tokens=window_tokens,
        override_allowed=override_allowed,
        override_requires=override_requires,
        compiled_patterns=tuple(compiled_patterns),
        patterns_lower=tuple(patterns_lower),
    )


def _build_trigger_anchors(
    concepts: tuple[CompiledConcept, ...],
    variants: tuple[CompiledVariant, ...],
    canonical_trigger_text: str = "",
) -> CompiledTriggerAnchors:
    """Build the set of trigger anchors from compiled concepts and variants.

    Anchors are concept alias tokens (non-generic, from required concepts)
    plus strong-anchor variant phrase tokens. Weak-recall variants are recall
    hints only and must not raise trigger_coverage.

    Additionally, high-IDF trigger terms are extracted from canonical_trigger_text
    by splitting into tokens and filtering out GENERIC_TOKENS and
    GENERIC_ACTION_WORDS.
    """
    anchor_tokens: set[str] = set()
    anchor_phrases: list[str] = []

    # Collect tokens from required concept aliases
    for concept in concepts:
        if not concept.required:
            continue
        for alias in concept.aliases:
            tokens = _tokenize(alias.text)
            for tok in tokens:
                if tok not in GENERIC_TOKENS:
                    anchor_tokens.add(tok)

    # Collect strong variant phrase anchors only.
    for variant in variants:
        if variant.kind != "strong_anchor":
            continue
        anchor_phrases.append(variant.text_lower)
        tokens = _tokenize(variant.text)
        for tok in tokens:
            if tok not in GENERIC_TOKENS:
                anchor_tokens.add(tok)

    # Add high-IDF trigger terms from canonical trigger text
    if canonical_trigger_text:
        canonical_tokens = _tokenize(canonical_trigger_text)
        for tok in canonical_tokens:
            if tok not in GENERIC_TOKENS and tok not in GENERIC_ACTION_WORDS:
                anchor_tokens.add(tok)

    total = len(anchor_tokens) + len(anchor_phrases)

    return CompiledTriggerAnchors(
        anchor_tokens=frozenset(anchor_tokens),
        anchor_phrases=tuple(anchor_phrases),
        total_anchors=max(total, 1),  # avoid division by zero
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compile_rule(
    trigger_data: dict[str, Any],
    action_data: Optional[dict[str, Any]] = None,
    search_terms: Optional[dict[str, Any]] = None,
) -> CompiledMatcher:
    """Compile rule trigger/action/search data into a deterministic CompiledMatcher.

    Args:
        trigger_data: Dict with keys: required_concept_groups, concepts,
            excluded_contexts, variants. Follows the schema in plan section 4.
        action_data: Optional action dict (currently unused by matcher but
            reserved for future severity-aware compilation).
        search_terms: Optional dict mapping language codes to term lists.
            Stored for recall reference; not used as trigger evidence.

    Returns:
        CompiledMatcher frozen dataclass.

    Raises:
        CompilationError: If data is invalid or violates compilation constraints.
    """
    # Compile concept groups
    groups_data = trigger_data.get("required_concept_groups", [])
    if not groups_data:
        raise CompilationError(
            "At least one required concept group is required"
        )

    concept_groups = tuple(_compile_concept_group(g) for g in groups_data)

    # Compile concepts
    concepts_data = trigger_data.get("concepts", [])
    concepts = tuple(_compile_concept(c) for c in concepts_data)

    # Validate that concept groups reference existing concepts
    concept_ids = {c.id for c in concepts}
    for group in concept_groups:
        for cid in group.all_of:
            if cid not in concept_ids:
                raise CompilationError(
                    f"Concept group '{group.id}' references unknown concept '{cid}'"
                )

    # Validate that at least one group references only required concepts
    required_concept_ids = {c.id for c in concepts if c.required}
    has_valid_group = False
    for group in concept_groups:
        if all(cid in required_concept_ids for cid in group.all_of):
            has_valid_group = True
            break

    if not has_valid_group:
        raise CompilationError(
            "At least one concept group must contain only required concepts"
        )

    # Compile variants
    variants_data = trigger_data.get("variants", [])
    variants = tuple(_compile_variant(v) for v in variants_data)

    # Validate that strong_anchor variant requires_concepts reference existing concepts
    for variant in variants:
        if variant.kind == "strong_anchor":
            for cid in variant.requires_concepts:
                if cid not in concept_ids:
                    raise CompilationError(
                        f"Variant '{variant.text}' references unknown concept '{cid}'"
                    )

    # Compile excluded contexts
    excluded_data = trigger_data.get("excluded_contexts", [])
    excluded_contexts = tuple(
        _compile_excluded_context(e) for e in excluded_data
    )

    # Build trigger anchors
    canonical_trigger_text = trigger_data.get("canonical_trigger_text", "")
    trigger_anchors = _build_trigger_anchors(concepts, variants, canonical_trigger_text)

    # Normalize search terms
    raw_search = search_terms or {}
    normalized_search: dict[str, tuple[str, ...]] = {
        k: tuple(v) if isinstance(v, list) else (v,)
        for k, v in raw_search.items()
    }

    return CompiledMatcher(
        compiler_version=COMPILER_VERSION,
        concepts=concepts,
        concept_groups=concept_groups,
        variants=variants,
        excluded_contexts=excluded_contexts,
        trigger_anchors=trigger_anchors,
        search_terms=normalized_search,
    )
