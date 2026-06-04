"""Versioned trigger IDF stats builder for the autonomous rule quality flywheel.

Computes IDF statistics over the active+trusted rule pool. Used by the hot path
to determine trigger evidence thresholds dynamically.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from ..policy import (
    DYNAMIC_IDF_NORMAL,
    DYNAMIC_IDF_SMALL_POOL,
    IDF_MAX_SHADOW,
    SMALL_POOL_THRESHOLD,
)
from .tokenizer import tokenize

# ---------------------------------------------------------------------------
# Version strings
# ---------------------------------------------------------------------------

TOKENIZER_VERSION: str = "1.0.0"
GENERIC_TOKEN_POLICY_VERSION: str = "1.0.0"

# ---------------------------------------------------------------------------
# Generic tokens excluded from trigger anchors
# ---------------------------------------------------------------------------

GENERIC_TOKENS: frozenset[str] = frozenset((
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "to",
    "for",
    "and",
    "or",
    "in",
    "on",
    "at",
    "of",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "with",
    "from",
    "by",
    "not",
    "no",
    "do",
    "does",
    "did",
    "if",
    "when",
    "then",
    "but",
    "so",
    "as",
    "has",
    "have",
    "had",
    "will",
    "would",
    "can",
    "could",
    "should",
    "may",
    "might",
    "shall",
    "must",
    "about",
    "up",
    "out",
    "just",
    "also",
    "than",
    "very",
    "too",
    "any",
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
    "into",
    "over",
    "after",
    "before",
    "between",
    "under",
    "again",
    "there",
    "here",
    "where",
    "how",
    "what",
    "which",
    "who",
    "whom",
    "why",
    "we",
    "you",
    "they",
    "he",
    "she",
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
))


# ---------------------------------------------------------------------------
# IdfPoolStats dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdfPoolStats:
    """Versioned IDF statistics snapshot over the eligible rule pool."""

    pool_version: str
    rule_pool_size: int
    eligible_rule_set_hash: str
    tokenizer_version: str
    matcher_compiler_version: str
    generic_token_policy_version: str
    concept_compiler_version: str
    df_by_token: dict[str, int]
    dynamic_threshold: float
    built_at: str


# ---------------------------------------------------------------------------
# Sentinel for empty pool
# ---------------------------------------------------------------------------

_EMPTY_POOL_STATS = IdfPoolStats(
    pool_version="empty",
    rule_pool_size=0,
    eligible_rule_set_hash="",
    tokenizer_version=TOKENIZER_VERSION,
    matcher_compiler_version="",
    generic_token_policy_version=GENERIC_TOKEN_POLICY_VERSION,
    concept_compiler_version="",
    df_by_token={},
    dynamic_threshold=0.0,
    built_at="",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compute_eligible_rule_set_hash(rules) -> str:
    """Hash of sorted rule IDs in the pool."""
    ids = sorted(r.id for r in rules)
    return hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]


def compute_pool_version(
    rules,
    tokenizer_version: str,
    matcher_compiler_version: str,
    generic_token_policy_version: str,
    concept_compiler_version: str,
) -> str:
    """Composite pool version that changes when any component changes (spec section 9.3).

    Includes: tokenizer, matcher compiler, generic-token policy,
    concept compiler, AND eligible rule-set hash.
    """
    rule_set_hash = compute_eligible_rule_set_hash(rules)
    composite = (
        f"{rule_set_hash}|{tokenizer_version}|{matcher_compiler_version}"
        f"|{generic_token_policy_version}|{concept_compiler_version}"
    )
    return hashlib.sha256(composite.encode()).hexdigest()[:16]


def _trigger_tokens_for_rule(rule) -> set[str]:
    """Extract tokens from trigger_canonical + trigger_variants fields only."""
    tokens: set[str] = set()
    tokens.update(tokenize(rule.trigger_canonical))
    if rule.trigger_canonical_zh:
        tokens.update(tokenize(rule.trigger_canonical_zh))
    for v in rule.trigger_variants:
        text = v.get("text") if isinstance(v, dict) else v
        tokens.update(tokenize(str(text or "")))
    for v in rule.trigger_variants_zh:
        text = v.get("text") if isinstance(v, dict) else v
        tokens.update(tokenize(str(text or "")))
    return tokens


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------


def build_idf_stats(
    rules,
    tokenizer_version: str = TOKENIZER_VERSION,
    matcher_compiler_version: str = "1.0.0",
    concept_compiler_version: str = "1.0.0",
) -> IdfPoolStats:
    """Build versioned IDF statistics over the active+trusted rule pool.

    Args:
        rules: Iterable of Rule objects (should be active+trusted pool).
        tokenizer_version: Version string of the tokenizer used.
        matcher_compiler_version: Version string of the matcher compiler.
        concept_compiler_version: Version string of the concept compiler.

    Returns:
        IdfPoolStats with computed thresholds and document frequencies.
    """
    pool = list(rules)
    n = len(pool)

    if n == 0:
        return _EMPTY_POOL_STATS

    # Compute df_trigger(t) for each token across trigger fields
    df: Counter[str] = Counter()
    for rule in pool:
        rule_tokens = _trigger_tokens_for_rule(rule)
        df.update(rule_tokens)

    # Select absolute_trigger_info_min based on pool size
    if n < SMALL_POOL_THRESHOLD:
        absolute_trigger_info_min = DYNAMIC_IDF_SMALL_POOL.absolute_trigger_info_min
    else:
        absolute_trigger_info_min = DYNAMIC_IDF_NORMAL.absolute_trigger_info_min

    # Compute dynamic threshold per section 9.3 formula
    rare_df = max(1, math.ceil(n * 0.10))
    idf_10pct = math.log(1 + (n - rare_df + 0.5) / (rare_df + 0.5))
    dynamic_trigger_info_min = 2 * idf_10pct
    trigger_info_min = max(dynamic_trigger_info_min, absolute_trigger_info_min)

    pool_version = compute_pool_version(
        pool, tokenizer_version, matcher_compiler_version,
        GENERIC_TOKEN_POLICY_VERSION, concept_compiler_version,
    )

    return IdfPoolStats(
        pool_version=pool_version,
        rule_pool_size=n,
        eligible_rule_set_hash=compute_eligible_rule_set_hash(pool),
        tokenizer_version=tokenizer_version,
        matcher_compiler_version=matcher_compiler_version,
        generic_token_policy_version=GENERIC_TOKEN_POLICY_VERSION,
        concept_compiler_version=concept_compiler_version,
        df_by_token=dict(df),
        dynamic_threshold=trigger_info_min,
        built_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# IDF computation helpers
# ---------------------------------------------------------------------------


def compute_trigger_idf_sum(tokens: list[str], idf_stats: IdfPoolStats) -> float:
    """Sum IDF values for matched trigger tokens.

    Args:
        tokens: Tokens from the query/prompt that matched trigger anchors.
        idf_stats: Pre-computed IDF pool statistics.

    Returns:
        Sum of IDF values. Returns 0.0 if pool is empty.
    """
    n = idf_stats.rule_pool_size
    if n == 0:
        return 0.0

    total = 0.0
    for t in tokens:
        df_t = idf_stats.df_by_token.get(t, 0)
        if df_t > 0:
            total += math.log(1 + (n - df_t + 0.5) / (df_t + 0.5))
    return total


def store_idf_stats(db, idf_stats: IdfPoolStats) -> None:
    """Persist a built IDF pool snapshot for event auditability."""
    from ..db import dumps_json

    with db.transaction() as tx:
        tx.execute(
            "INSERT OR REPLACE INTO trigger_idf_stats "
            "(pool_version, rule_pool_size, eligible_rule_set_hash, "
            "tokenizer_version, matcher_compiler_version, "
            "generic_token_policy_version, concept_compiler_version, "
            "df_by_token, dynamic_threshold, built_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                idf_stats.pool_version,
                idf_stats.rule_pool_size,
                idf_stats.eligible_rule_set_hash,
                idf_stats.tokenizer_version,
                idf_stats.matcher_compiler_version,
                idf_stats.generic_token_policy_version,
                idf_stats.concept_compiler_version,
                dumps_json(idf_stats.df_by_token),
                idf_stats.dynamic_threshold,
                idf_stats.built_at,
            ),
        )


def compute_shadow_idf(token: str, idf_stats: IdfPoolStats) -> float:
    """Compute shadow IDF for a token, clamped to IDF_MAX_SHADOW.

    For candidate/suppressed shadow scoring. Prevents novelty from becoming
    unbounded evidence.

    Args:
        token: A single trigger token.
        idf_stats: Pre-computed IDF pool statistics.

    Returns:
        Clamped IDF value. Returns 0.0 if pool is empty.
    """
    n = idf_stats.rule_pool_size
    if n == 0:
        return 0.0

    df_t = idf_stats.df_by_token.get(token, 0)
    df_effective = max(1, df_t)
    raw = math.log(1 + (n - df_effective + 0.5) / (df_effective + 0.5))
    return min(raw, IDF_MAX_SHADOW)


# ---------------------------------------------------------------------------
# Small pool requirements
# ---------------------------------------------------------------------------


def get_small_pool_requirements(n: int) -> dict:
    """Returns trigger requirements based on pool size N.

    Args:
        n: Number of rules in the eligible pool.

    Returns:
        Dict with trigger_coverage_min, distinct_trigger_terms_min,
        requires_strong_evidence keys.
    """
    if n == 0:
        return {
            "trigger_coverage_min": None,
            "distinct_trigger_terms_min": None,
            "requires_strong_evidence": True,
            "dynamic_idf_available": False,
        }

    if n < SMALL_POOL_THRESHOLD:
        policy = DYNAMIC_IDF_SMALL_POOL
        return {
            "trigger_coverage_min": policy.trigger_coverage_min,
            "distinct_trigger_terms_min": policy.distinct_trigger_terms_min,
            "requires_strong_evidence": True,
            "dynamic_idf_available": True,
        }

    policy = DYNAMIC_IDF_NORMAL
    return {
        "trigger_coverage_min": policy.trigger_coverage_min,
        "distinct_trigger_terms_min": policy.distinct_trigger_terms_min,
        "requires_strong_evidence": False,
        "dynamic_idf_available": True,
    }
