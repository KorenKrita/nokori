"""Tests for nokori.eval.synthetic module.

Covers:
1. Positive prompts achieve at least expected_min_decision (warm+)
2. Medium positives may warm but need not hot
3. Near-miss prompts must be COLD (any injection = fail)
4. Negatives must be COLD
5. Global adversarial cases must be COLD
6. Results bound to exact versions (changed version = stale)
7. decision_meets_min and decision_within_max logic
8. DECISION_RANK ordering: cold < warm < hot < gate
9. Eval passes only when ALL cases satisfy their constraints
10. Eval fails if any near-miss would inject
"""

from __future__ import annotations

import pytest

from nokori.eval.synthetic import (
    BENCHMARK_VERSION,
    CONCEPT_COMPILER_VERSION,
    DECISION_RANK,
    EMBEDDING_PROFILE_VERSION,
    _case_passes,
    decision_meets_min,
    decision_within_max,
    run_synthetic_eval,
)
from nokori.matcher.compiler import (
    COMPILER_VERSION as MATCHER_COMPILER_VERSION,
    CompiledMatcher,
    compile_rule,
)
from nokori.policy import RUNTIME_POLICY_VERSION
from nokori.search.idf_stats import IdfPoolStats, TOKENIZER_VERSION

# ---------------------------------------------------------------------------
# Helpers: build mock compiled matchers and IDF stats
# ---------------------------------------------------------------------------


def _make_idf_stats(
    *,
    pool_version: str = "test-pool-v1",
    rule_pool_size: int = 10,
    df_by_token: dict[str, int] | None = None,
    dynamic_threshold: float = 2.0,
) -> IdfPoolStats:
    """Build a minimal IdfPoolStats for testing."""
    return IdfPoolStats(
        pool_version=pool_version,
        rule_pool_size=rule_pool_size,
        eligible_rule_set_hash="abc123",
        tokenizer_version=TOKENIZER_VERSION,
        matcher_compiler_version=MATCHER_COMPILER_VERSION,
        generic_token_policy_version="1.0.0",
        concept_compiler_version="1.0.0",
        df_by_token=df_by_token or {"react": 2, "hooks": 1, "useeffect": 1, "cleanup": 1},
        dynamic_threshold=dynamic_threshold,
        built_at="2025-01-01T00:00:00+00:00",
    )


def _make_matcher_for_react_hooks() -> CompiledMatcher:
    """Compile a matcher targeting 'React hooks cleanup' patterns.

    Concepts: react_concept (react, hooks) + cleanup_concept (cleanup, useEffect)
    Group: both concepts required
    Variant: strong_anchor = "useEffect cleanup function"
    Excluded context: "class component lifecycle"
    Trigger anchors: react, hooks, cleanup, useeffect
    """
    trigger_data = {
        "concepts": [
            {
                "id": "react_concept",
                "label": "React Hooks",
                "match_mode": "any_alias",
                "required": True,
                "aliases": [
                    {"text": "react hooks", "strength": "strong"},
                    {"text": "react", "strength": "strong"},
                ],
            },
            {
                "id": "cleanup_concept",
                "label": "Cleanup Pattern",
                "match_mode": "any_alias",
                "required": True,
                "aliases": [
                    {"text": "cleanup", "strength": "strong"},
                    {"text": "useEffect", "strength": "strong"},
                ],
            },
        ],
        "required_concept_groups": [
            {"id": "g_main", "all_of": ["react_concept", "cleanup_concept"]},
        ],
        "variants": [
            {
                "text": "useEffect cleanup function",
                "kind": "strong_anchor",
                "requires_concepts": ["react_concept", "cleanup_concept"],
            },
            {
                "text": "effect cleanup",
                "kind": "weak_recall",
            },
        ],
        "excluded_contexts": [
            {
                "id": "exc_class",
                "label": "Class Component",
                "patterns": ["class component lifecycle"],
                "scope": "global",
                "match_mode": "phrase",
                "window_tokens": 12,
            },
        ],
    }
    return compile_rule(trigger_data)


def _make_rule_data(
    *,
    rule_id: str = "rule-react-hooks-cleanup",
    version: int = 1,
    status: str = "active",
    severity: str = "reminder",
    first_observed_useful_at: str | None = "2025-01-01T00:00:00+00:00",
) -> dict:
    return {
        "id": rule_id,
        "version": version,
        "status": status,
        "severity": severity,
        "first_observed_useful_at": first_observed_useful_at,
    }


# ---------------------------------------------------------------------------
# Test: DECISION_RANK ordering (cold < warm < hot < gate)
# ---------------------------------------------------------------------------


class TestDecisionRank:
    def test_ordering_cold_lt_warm(self):
        assert DECISION_RANK["cold"] < DECISION_RANK["warm"]

    def test_ordering_warm_lt_hot(self):
        assert DECISION_RANK["warm"] < DECISION_RANK["hot"]

    def test_ordering_hot_lt_gate(self):
        assert DECISION_RANK["hot"] < DECISION_RANK["gate"]

    def test_all_ranks_present(self):
        assert set(DECISION_RANK.keys()) == {"cold", "warm", "hot", "gate"}


# ---------------------------------------------------------------------------
# Test: decision_meets_min and decision_within_max logic
# ---------------------------------------------------------------------------


class TestDecisionComparisons:
    """decision_meets_min returns True when actual >= expected_min."""

    @pytest.mark.parametrize(
        "actual,expected_min,expected",
        [
            ("cold", "cold", True),
            ("warm", "cold", True),
            ("hot", "cold", True),
            ("gate", "cold", True),
            ("warm", "warm", True),
            ("hot", "warm", True),
            ("gate", "warm", True),
            ("cold", "warm", False),
            ("cold", "hot", False),
            ("warm", "hot", False),
            ("cold", "gate", False),
            ("warm", "gate", False),
            ("hot", "gate", False),
            ("gate", "gate", True),
        ],
    )
    def test_decision_meets_min(self, actual, expected_min, expected):
        assert decision_meets_min(actual, expected_min) is expected

    @pytest.mark.parametrize(
        "actual,expected_max,expected",
        [
            ("cold", "cold", True),
            ("cold", "warm", True),
            ("cold", "hot", True),
            ("cold", "gate", True),
            ("warm", "cold", False),
            ("warm", "warm", True),
            ("warm", "hot", True),
            ("hot", "warm", False),
            ("hot", "hot", True),
            ("hot", "gate", True),
            ("gate", "hot", False),
            ("gate", "gate", True),
        ],
    )
    def test_decision_within_max(self, actual, expected_max, expected):
        assert decision_within_max(actual, expected_max) is expected

    def test_unknown_actual_meets_min_returns_false(self):
        # Unknown actual gets rank -1
        assert decision_meets_min("unknown", "cold") is False

    def test_unknown_actual_within_max_returns_false(self):
        # Unknown actual gets rank 99
        assert decision_within_max("unknown", "gate") is False


# ---------------------------------------------------------------------------
# Test: Positive prompts must achieve at least expected_min_decision (warm+)
# ---------------------------------------------------------------------------


class TestPositiveCases:
    @pytest.fixture
    def matcher(self):
        return _make_matcher_for_react_hooks()

    @pytest.fixture
    def idf_stats(self):
        return _make_idf_stats()

    @pytest.fixture
    def rule_data(self):
        return _make_rule_data()

    def test_positive_prompt_achieves_warm_or_better(self, matcher, idf_stats, rule_data):
        """A strong positive case must result in warm+ decision."""
        eval_cases = [
            {
                "prompt": "How do I properly handle useEffect cleanup function in React hooks?",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        positive_results = [r for r in result.results if r["case_type"] == "positive"]
        assert len(positive_results) == 1
        assert decision_meets_min(positive_results[0]["actual_decision"], "warm")

    def test_positive_case_passes_flag(self, matcher, idf_stats, rule_data):
        """Positive case that achieves expected_min should have case_passed=True."""
        eval_cases = [
            {
                "prompt": "My React hooks useEffect cleanup is leaking subscriptions",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        assert result.results[0]["case_passed"] is True

    def test_positive_cold_fails(self, matcher, idf_stats, rule_data):
        """A positive case that lands cold must fail."""
        # Craft a prompt that wont match the trigger adequately
        eval_cases = [
            {
                "prompt": "What is Python list comprehension syntax?",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        assert result.results[0]["case_passed"] is False
        assert result.passed is False


# ---------------------------------------------------------------------------
# Test: Medium positives may warm but need not hot
# ---------------------------------------------------------------------------


class TestMediumPositiveCases:
    @pytest.fixture
    def matcher(self):
        return _make_matcher_for_react_hooks()

    @pytest.fixture
    def idf_stats(self):
        return _make_idf_stats()

    @pytest.fixture
    def rule_data(self):
        return _make_rule_data()

    def test_medium_positive_cold_is_acceptable(self):
        """Medium positive landing cold is not a failure."""
        assert _case_passes("medium_positive", "cold", {"expected_max_decision": "warm"}) is True

    def test_medium_positive_warm_within_max_passes(self):
        """Medium positive at warm (within max=warm) passes."""
        assert _case_passes("medium_positive", "warm", {"expected_max_decision": "warm"}) is True

    def test_medium_positive_hot_exceeds_max_fails(self):
        """Medium positive exceeding expected_max=warm fails."""
        assert _case_passes("medium_positive", "hot", {"expected_max_decision": "warm"}) is False

    def test_medium_positive_gate_exceeds_max_fails(self):
        assert _case_passes("medium_positive", "gate", {"expected_max_decision": "warm"}) is False


# ---------------------------------------------------------------------------
# Test: Near-miss prompts must be COLD
# ---------------------------------------------------------------------------


class TestNearMissCases:
    @pytest.fixture
    def matcher(self):
        return _make_matcher_for_react_hooks()

    @pytest.fixture
    def idf_stats(self):
        return _make_idf_stats()

    @pytest.fixture
    def rule_data(self):
        return _make_rule_data()

    def test_near_miss_cold_passes(self):
        """Near-miss case at cold passes."""
        assert _case_passes("near_miss", "cold", {"expected_max_decision": "cold"}) is True

    def test_near_miss_warm_fails(self):
        """Near-miss case at warm fails (injection = failure)."""
        assert _case_passes("near_miss", "warm", {"expected_max_decision": "cold"}) is False

    def test_near_miss_hot_fails(self):
        assert _case_passes("near_miss", "hot", {"expected_max_decision": "cold"}) is False

    def test_near_miss_must_be_cold_in_eval(self, matcher, idf_stats, rule_data):
        """A near-miss prompt that only touches part of trigger stays cold."""
        eval_cases = [
            {
                "prompt": "How do I clean up files in my Python project directory?",
                "case_type": "near_miss",
                "expected_max_decision": "cold",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        assert result.results[0]["actual_decision"] == "cold"
        assert result.results[0]["case_passed"] is True


# ---------------------------------------------------------------------------
# Test: Negatives must be COLD
# ---------------------------------------------------------------------------


class TestNegativeCases:
    @pytest.fixture
    def matcher(self):
        return _make_matcher_for_react_hooks()

    @pytest.fixture
    def idf_stats(self):
        return _make_idf_stats()

    @pytest.fixture
    def rule_data(self):
        return _make_rule_data()

    def test_negative_cold_passes(self):
        assert _case_passes("negative", "cold", {"expected_max_decision": "cold"}) is True

    def test_negative_warm_fails(self):
        assert _case_passes("negative", "warm", {"expected_max_decision": "cold"}) is False

    def test_negative_in_eval_stays_cold(self, matcher, idf_stats, rule_data):
        """Unrelated prompt must produce cold decision."""
        eval_cases = [
            {
                "prompt": "Explain how Docker networking works with bridge mode.",
                "case_type": "negative",
                "expected_max_decision": "cold",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        assert result.results[0]["actual_decision"] == "cold"
        assert result.results[0]["case_passed"] is True
        assert result.passed is True


# ---------------------------------------------------------------------------
# Test: Global adversarial cases must be COLD
# ---------------------------------------------------------------------------


class TestGlobalAdversarialCases:
    @pytest.fixture
    def matcher(self):
        return _make_matcher_for_react_hooks()

    @pytest.fixture
    def idf_stats(self):
        return _make_idf_stats()

    @pytest.fixture
    def rule_data(self):
        return _make_rule_data()

    def test_adversarial_cold_passes(self):
        assert _case_passes("global_adversarial", "cold", {}) is True

    def test_adversarial_warm_fails(self):
        assert _case_passes("global_adversarial", "warm", {}) is False

    def test_adversarial_hot_fails(self):
        assert _case_passes("global_adversarial", "hot", {}) is False

    def test_global_adversarial_in_eval(self, matcher, idf_stats, rule_data):
        """Global adversarial cases appended via separate list must stay cold."""
        eval_cases = [
            {
                "prompt": "My React hooks useEffect cleanup function is not running",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
        ]
        global_adversarial = [
            {
                "prompt": "Tell me a joke about programming in assembly language",
            },
        ]
        result = run_synthetic_eval(
            rule_data, matcher, idf_stats, eval_cases,
            global_adversarial_cases=global_adversarial,
        )
        adversarial_results = [r for r in result.results if r["case_type"] == "global_adversarial"]
        assert len(adversarial_results) == 1
        assert adversarial_results[0]["actual_decision"] == "cold"
        assert adversarial_results[0]["case_passed"] is True


# ---------------------------------------------------------------------------
# Test: Results bound to exact versions (changed version = stale)
# ---------------------------------------------------------------------------


class TestVersionBinding:
    @pytest.fixture
    def matcher(self):
        return _make_matcher_for_react_hooks()

    @pytest.fixture
    def idf_stats(self):
        return _make_idf_stats(pool_version="pool-v42")

    @pytest.fixture
    def rule_data(self):
        return _make_rule_data(version=3)

    def test_result_captures_all_versions(self, matcher, idf_stats, rule_data):
        """SyntheticEvalResult must record all component version strings."""
        eval_cases = [
            {
                "prompt": "React hooks useEffect cleanup pattern best practices",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        assert result.runtime_policy_version == RUNTIME_POLICY_VERSION
        assert result.tokenizer_version == TOKENIZER_VERSION
        assert result.matcher_compiler_version == MATCHER_COMPILER_VERSION
        assert result.concept_compiler_version == CONCEPT_COMPILER_VERSION
        assert result.embedding_profile_version == EMBEDDING_PROFILE_VERSION
        assert result.trigger_idf_pool_version == "pool-v42"
        assert result.benchmark_version == BENCHMARK_VERSION
        assert result.rule_id == "rule-react-hooks-cleanup"
        assert result.rule_version == 3

    def test_different_pool_version_means_stale(self, matcher, rule_data):
        """An eval generated with pool_version=A is stale if pool is now B."""
        stats_a = _make_idf_stats(pool_version="pool-A")
        stats_b = _make_idf_stats(pool_version="pool-B")

        eval_cases = [
            {
                "prompt": "React hooks useEffect cleanup pattern",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
        ]
        result_a = run_synthetic_eval(rule_data, matcher, stats_a, eval_cases)
        result_b = run_synthetic_eval(rule_data, matcher, stats_b, eval_cases)

        # Same rule, different pool version => versions differ
        assert result_a.trigger_idf_pool_version != result_b.trigger_idf_pool_version


# ---------------------------------------------------------------------------
# Test: Eval passes only when ALL cases satisfy their constraints
# ---------------------------------------------------------------------------


class TestEvalPassesOnlyWhenAllSatisfied:
    @pytest.fixture
    def matcher(self):
        return _make_matcher_for_react_hooks()

    @pytest.fixture
    def idf_stats(self):
        return _make_idf_stats()

    @pytest.fixture
    def rule_data(self):
        return _make_rule_data()

    def test_all_pass_yields_overall_pass(self, matcher, idf_stats, rule_data):
        """When every case meets its constraint, overall passed=True."""
        eval_cases = [
            {
                "prompt": "How to handle React hooks useEffect cleanup function correctly",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
            {
                "prompt": "Explain how Docker networking bridge mode works",
                "case_type": "negative",
                "expected_max_decision": "cold",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        assert result.passed is True

    def test_single_failure_yields_overall_fail(self, matcher, idf_stats, rule_data):
        """Even one failing case means overall passed=False."""
        eval_cases = [
            {
                "prompt": "How to handle React hooks useEffect cleanup function correctly",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
            {
                # This positive case will fail because prompt is unrelated
                "prompt": "Explain quantum computing basics",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        assert result.passed is False

    def test_negative_pass_but_positive_fail(self, matcher, idf_stats, rule_data):
        """Negatives passing is not enough if positives fail."""
        eval_cases = [
            {
                "prompt": "What is Rust ownership model?",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
            {
                "prompt": "Explain Docker networking",
                "case_type": "negative",
                "expected_max_decision": "cold",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        assert result.passed is False


# ---------------------------------------------------------------------------
# Test: Eval fails if any near-miss would inject
# ---------------------------------------------------------------------------


class TestNearMissInjectionFails:
    @pytest.fixture
    def matcher(self):
        return _make_matcher_for_react_hooks()

    @pytest.fixture
    def idf_stats(self):
        return _make_idf_stats()

    @pytest.fixture
    def rule_data(self):
        return _make_rule_data()

    def test_near_miss_injection_causes_failure(self, matcher, idf_stats, rule_data):
        """If a near-miss case would inject (warm+), eval must fail.

        This tests the hard failure check in run_synthetic_eval that iterates
        results and ensures near_miss/negative/adversarial at warm+ -> fail.
        """
        # Use _case_passes to verify the contract at the unit level
        assert _case_passes("near_miss", "warm", {"expected_max_decision": "cold"}) is False
        assert _case_passes("near_miss", "hot", {"expected_max_decision": "cold"}) is False
        assert _case_passes("near_miss", "gate", {"expected_max_decision": "cold"}) is False

    def test_near_miss_that_matches_all_concepts_must_still_be_cold(
        self, matcher, idf_stats, rule_data
    ):
        """A near-miss prompt that accidentally triggers concepts should still be cold
        due to excluded context or insufficient trigger evidence.

        Here we use the excluded_context to suppress injection.
        """
        eval_cases = [
            {
                # Contains react + cleanup but also the exclusion phrase
                "prompt": "In the class component lifecycle, how do I cleanup React effects?",
                "case_type": "near_miss",
                "expected_max_decision": "cold",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        near_miss_results = [r for r in result.results if r["case_type"] == "near_miss"]
        assert near_miss_results[0]["actual_decision"] == "cold"
        assert result.passed is True


# ---------------------------------------------------------------------------
# Test: Unknown case_type fails conservatively
# ---------------------------------------------------------------------------


class TestUnknownCaseType:
    def test_unknown_type_fails(self):
        assert _case_passes("invented_type", "cold", {}) is False
        assert _case_passes("invented_type", "warm", {}) is False


# ---------------------------------------------------------------------------
# Test: Result structure integrity
# ---------------------------------------------------------------------------


class TestResultStructure:
    @pytest.fixture
    def matcher(self):
        return _make_matcher_for_react_hooks()

    @pytest.fixture
    def idf_stats(self):
        return _make_idf_stats()

    @pytest.fixture
    def rule_data(self):
        return _make_rule_data()

    def test_result_contains_all_cases_and_results(self, matcher, idf_stats, rule_data):
        eval_cases = [
            {
                "prompt": "React hooks useEffect cleanup pattern guide",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
            {
                "prompt": "Docker compose networking",
                "case_type": "negative",
                "expected_max_decision": "cold",
            },
        ]
        global_adversarial = [
            {"prompt": "How to cook pasta"},
        ]
        result = run_synthetic_eval(
            rule_data, matcher, idf_stats, eval_cases,
            global_adversarial_cases=global_adversarial,
        )
        # 2 rule cases + 1 adversarial = 3 total
        assert len(result.cases) == 3
        assert len(result.results) == 3
        assert result.cases[2]["case_type"] == "global_adversarial"

    def test_result_is_frozen_dataclass(self, matcher, idf_stats, rule_data):
        eval_cases = [
            {
                "prompt": "React hooks useEffect cleanup function example",
                "case_type": "positive",
                "expected_min_decision": "warm",
            },
        ]
        result = run_synthetic_eval(rule_data, matcher, idf_stats, eval_cases)
        with pytest.raises(Exception):
            result.passed = False  # type: ignore[misc]
