"""Tests for nokori.search.idf_stats dynamic IDF computation."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from nokori.policy import (
    DYNAMIC_IDF_NORMAL,
    DYNAMIC_IDF_SMALL_POOL,
)
from nokori.search.idf_stats import (
    GENERIC_TOKENS,
    _trigger_tokens_for_rule,
    build_idf_stats,
    compute_trigger_idf_sum,
)

# ---------------------------------------------------------------------------
# Mock rule helper
# ---------------------------------------------------------------------------


@dataclass
class MockRule:
    id: str
    trigger_canonical: str
    trigger_canonical_zh: str = ""
    trigger_variants: tuple[str, ...] = ()
    trigger_variants_zh: tuple[str, ...] = ()
    action: str = ""
    rationale: str = ""
    search_terms: str = ""


def _make_pool(n: int, *, prefix: str = "rule") -> list[MockRule]:
    """Create n distinct rules with unique trigger tokens."""
    return [
        MockRule(
            id=f"{prefix}_{i}",
            trigger_canonical=f"trigger_term_{i} specificity_{i}",
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. N=0 makes dynamic IDF evidence unavailable
# ---------------------------------------------------------------------------


class TestEmptyPool:
    def test_build_returns_empty_sentinel(self):
        stats = build_idf_stats([])
        assert stats.rule_pool_size == 0
        assert stats.pool_version == "empty"
        assert stats.df_by_token == {}
        assert stats.dynamic_threshold == 0.0

    def test_trigger_idf_sum_returns_zero(self):
        stats = build_idf_stats([])
        assert compute_trigger_idf_sum(["anything", "here"], stats) == 0.0





# ---------------------------------------------------------------------------
# 4. Trigger IDF uses only trigger/variant fields (not action/search)
# ---------------------------------------------------------------------------


class TestTriggerFieldsOnly:
    def test_trigger_tokens_from_canonical(self):
        rule = MockRule(
            id="r1",
            trigger_canonical="kubernetes deployment rolling",
            action="use kubectl rollout restart",
            search_terms="k8s pod restart",
        )
        tokens = _trigger_tokens_for_rule(rule)
        assert "kubernetes" in tokens
        assert "deployment" in tokens
        assert "rolling" in tokens

    def test_trigger_tokens_from_variants(self):
        rule = MockRule(
            id="r2",
            trigger_canonical="docker compose",
            trigger_variants=("docker-compose up", "compose build"),
        )
        tokens = _trigger_tokens_for_rule(rule)
        assert "docker" in tokens
        assert "compose" in tokens
        assert "build" in tokens

    def test_action_terms_not_in_trigger_tokens(self):
        rule = MockRule(
            id="r3",
            trigger_canonical="memory leak detected",
            action="use valgrind to profile heap allocations",
            rationale="memory profiling finds leaks",
            search_terms="valgrind massif heaptrack",
        )
        tokens = _trigger_tokens_for_rule(rule)
        assert "valgrind" not in tokens
        assert "profile" not in tokens
        assert "heap" not in tokens
        assert "allocations" not in tokens
        assert "massif" not in tokens
        assert "heaptrack" not in tokens


# ---------------------------------------------------------------------------
# 5. action/rationale/search terms don't contribute to trigger_idf_sum
# ---------------------------------------------------------------------------


class TestActionDoesNotContribute:
    def test_idf_sum_ignores_action_tokens(self):
        rules = [
            MockRule(
                id="r1",
                trigger_canonical="postgres vacuum",
                action="run VACUUM ANALYZE on the table",
                search_terms="autovacuum bloat",
            ),
            MockRule(
                id="r2",
                trigger_canonical="redis cache eviction",
                action="configure maxmemory-policy allkeys-lru",
            ),
        ]
        stats = build_idf_stats(rules)

        # "vacuum" is in df_by_token because it appears in trigger_canonical
        assert "vacuum" in stats.df_by_token

        # "analyze" is only in action, should NOT be in df_by_token
        assert "analyze" not in stats.df_by_token
        assert "autovacuum" not in stats.df_by_token
        assert "bloat" not in stats.df_by_token
        assert "maxmemory" not in stats.df_by_token

    def test_idf_sum_for_action_only_token_is_zero(self):
        rules = [
            MockRule(
                id="r1",
                trigger_canonical="database slow query",
                action="add composite index on columns",
            ),
        ]
        stats = build_idf_stats(rules)
        # "composite" and "index" are only in action
        result = compute_trigger_idf_sum(["composite", "index"], stats)
        assert result == 0.0




# ---------------------------------------------------------------------------
# 8. Pool version changes when version inputs change
# ---------------------------------------------------------------------------


class TestPoolVersioning:
    def test_different_tokenizer_version(self):
        pool = _make_pool(5)
        stats_a = build_idf_stats(pool, tokenizer_version="1.0.0")
        stats_b = build_idf_stats(pool, tokenizer_version="2.0.0")
        # tokenizer_version is recorded; pool_version is rule-set hash
        assert stats_a.tokenizer_version != stats_b.tokenizer_version
        assert stats_a.pool_version != stats_b.pool_version

    def test_different_matcher_compiler_version(self):
        pool = _make_pool(5)
        stats_a = build_idf_stats(pool, matcher_compiler_version="1.0.0")
        stats_b = build_idf_stats(pool, matcher_compiler_version="2.0.0")
        assert stats_a.matcher_compiler_version != stats_b.matcher_compiler_version
        assert stats_a.pool_version != stats_b.pool_version

    def test_different_concept_compiler_version(self):
        pool = _make_pool(5)
        stats_a = build_idf_stats(pool, concept_compiler_version="1.0.0")
        stats_b = build_idf_stats(pool, concept_compiler_version="2.0.0")
        assert stats_a.concept_compiler_version != stats_b.concept_compiler_version
        assert stats_a.pool_version != stats_b.pool_version

    def test_different_eligible_rule_set(self):
        pool_a = _make_pool(5, prefix="alpha")
        pool_b = _make_pool(5, prefix="beta")
        stats_a = build_idf_stats(pool_a)
        stats_b = build_idf_stats(pool_b)
        assert stats_a.eligible_rule_set_hash != stats_b.eligible_rule_set_hash
        assert stats_a.pool_version != stats_b.pool_version

    def test_generic_token_policy_version_recorded(self):
        pool = _make_pool(5)
        stats = build_idf_stats(pool)
        assert stats.generic_token_policy_version == "1.0.0"


# ---------------------------------------------------------------------------
# 9. dynamic_threshold formula matches spec
# ---------------------------------------------------------------------------


class TestDynamicThresholdFormula:
    """Verify formula: trigger_info_min = max(2 * idf_10pct, absolute_trigger_info_min)
    where idf_10pct = log(1 + (N - rare_df + 0.5) / (rare_df + 0.5))
    and rare_df = max(1, ceil(N * 0.10))
    """

    @pytest.mark.parametrize("n", [5, 10, 15, 19])
    def test_small_pool_formula(self, n: int):
        pool = _make_pool(n)
        stats = build_idf_stats(pool)

        rare_df = max(1, math.ceil(n * 0.10))
        idf_10pct = math.log(1 + (n - rare_df + 0.5) / (rare_df + 0.5))
        dynamic = 2 * idf_10pct
        expected = max(dynamic, DYNAMIC_IDF_SMALL_POOL.absolute_trigger_info_min)
        assert stats.dynamic_threshold == pytest.approx(expected)

    @pytest.mark.parametrize("n", [20, 30, 50, 100])
    def test_normal_pool_formula(self, n: int):
        pool = _make_pool(n)
        stats = build_idf_stats(pool)

        rare_df = max(1, math.ceil(n * 0.10))
        idf_10pct = math.log(1 + (n - rare_df + 0.5) / (rare_df + 0.5))
        dynamic = 2 * idf_10pct
        expected = max(dynamic, DYNAMIC_IDF_NORMAL.absolute_trigger_info_min)
        assert stats.dynamic_threshold == pytest.approx(expected)

    def test_threshold_increases_with_pool_size(self):
        stats_small = build_idf_stats(_make_pool(5))
        stats_large = build_idf_stats(_make_pool(100))
        # Larger pool -> higher idf_10pct -> higher dynamic threshold
        assert stats_large.dynamic_threshold >= stats_small.dynamic_threshold


# ---------------------------------------------------------------------------
# 10. GENERIC_TOKENS are excluded from trigger anchors
# ---------------------------------------------------------------------------


class TestGenericTokenExclusion:
    def test_generic_tokens_present_in_set(self):
        # Verify known stop words are in GENERIC_TOKENS
        for word in ("the", "is", "and", "or", "for", "with", "not"):
            assert word in GENERIC_TOKENS

    def test_generic_tokens_in_trigger_excluded_from_df(self):
        # Non-generic tokens are recorded
        rule = MockRule(
            id="r1",
            trigger_canonical="the kubernetes deployment is failing",
        )
        stats = build_idf_stats([rule])
        # Non-generic tokens are recorded
        assert "kubernetes" in stats.df_by_token
        assert "deployment" in stats.df_by_token
        assert "failing" in stats.df_by_token
        assert "the" not in stats.df_by_token
        assert "is" not in stats.df_by_token

    def test_generic_tokens_are_frozenset(self):
        assert isinstance(GENERIC_TOKENS, frozenset)

    def test_generic_tokens_are_lowercase(self):
        for token in GENERIC_TOKENS:
            assert token == token.lower()

    def test_idf_sum_for_only_generic_tokens_when_not_in_pool(self):
        # If prompt only matches generic tokens that aren't in any rule's
        # trigger, idf_sum should be 0
        rules = [
            MockRule(id="r1", trigger_canonical="kubernetes deployment"),
        ]
        stats = build_idf_stats(rules)
        # "the" and "is" are generic and not in any trigger canonical
        result = compute_trigger_idf_sum(["the", "is"], stats)
        assert result == 0.0
