"""Tests for nokori.search.embedding_profiles."""

from __future__ import annotations

import pytest

from nokori.search.applicability import ApplicabilityResult, evaluate_applicability
from nokori.search.embedding_profiles import (
    CHECKED_IN_PROFILES,
    REQUIRED_BUCKETS,
    BucketThresholds,
    EmbeddingProfile,
    is_known_profile,
    load_profile,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_bucket(
    positive_p10: float = 0.85,
    medium_p10: float = 0.70,
    medium_p50: float = 0.75,
    near_miss_p95: float = 0.60,
    near_miss_p99: float = 0.65,
    negative_p99: float = 0.50,
    warm_min: float = 0.70,
    hot_min: float = 0.85,
) -> BucketThresholds:
    return BucketThresholds(
        positive_p10=positive_p10,
        medium_p10=medium_p10,
        medium_p50=medium_p50,
        near_miss_p95=near_miss_p95,
        near_miss_p99=near_miss_p99,
        negative_p99=negative_p99,
        warm_min=warm_min,
        hot_min=hot_min,
    )


def _make_valid_profile(model_id: str = "test-model-v1") -> EmbeddingProfile:
    overall = _make_bucket()
    return EmbeddingProfile(
        model_id=model_id,
        profile_version="1.0.0",
        dimension=384,
        normalization="cosine",
        overall=overall,
        buckets={
            "zh": _make_bucket(),
            "mixed": _make_bucket(),
            "code_or_cli": _make_bucket(),
        },
    )


@pytest.fixture()
def registered_profile(monkeypatch):
    """Register a test profile in CHECKED_IN_PROFILES for the test."""
    profile = _make_valid_profile("registered-test-model")
    monkeypatch.setitem(CHECKED_IN_PROFILES, "registered-test-model", profile)
    return profile


# ---------------------------------------------------------------------------
# 1. Profile schema includes all required percentile fields
# ---------------------------------------------------------------------------


class TestProfileSchema:
    def test_bucket_has_all_percentile_fields(self):
        bucket = _make_bucket()
        required_fields = {
            "positive_p10",
            "medium_p10",
            "medium_p50",
            "near_miss_p95",
            "near_miss_p99",
            "negative_p99",
            "warm_min",
            "hot_min",
        }
        actual_fields = set(vars(bucket).keys())
        assert required_fields == actual_fields

    def test_embedding_profile_has_required_fields(self):
        profile = _make_valid_profile()
        assert hasattr(profile, "model_id")
        assert hasattr(profile, "profile_version")
        assert hasattr(profile, "dimension")
        assert hasattr(profile, "normalization")
        assert hasattr(profile, "overall")
        assert hasattr(profile, "buckets")




# ---------------------------------------------------------------------------
# 3. Unknown profile returns None from load_profile
# ---------------------------------------------------------------------------


class TestUnknownProfile:
    def test_load_profile_returns_none_for_unknown(self):
        result = load_profile("nonexistent-model-xyz")
        assert result is None




# ---------------------------------------------------------------------------
# 5. Embedding-only evidence stays COLD (verify via runtime applicability)
# ---------------------------------------------------------------------------


class TestEmbeddingOnlyStaysCold:
    def test_embedding_only_match_returns_cold(self):
        result = evaluate_applicability(
            rule_status="active",
            rule_severity="reminder",
            rule_first_observed_useful_at=None,
            trigger_idf_sum=5.0,
            trigger_coverage=1.0,
            distinct_trigger_terms=3,
            strong_variant_phrase_hit=False,
            required_concepts_match=True,
            excluded_context_hit=False,
            action_only_match=False,
            search_only_match=False,
            embedding_only_match=True,
            idf_stats_available=True,
            pool_size=10,
            has_tool_input=False,
        )
        assert isinstance(result, ApplicabilityResult)
        assert result.decision == "cold"
        assert result.eligible is False
        assert "embedding_only_match" in result.reason




# ---------------------------------------------------------------------------
# 8. REQUIRED_BUCKETS includes overall, zh, mixed, code_or_cli
# ---------------------------------------------------------------------------


class TestRequiredBuckets:
    def test_required_buckets_contents(self):
        assert "overall" in REQUIRED_BUCKETS
        assert "zh" in REQUIRED_BUCKETS
        assert "mixed" in REQUIRED_BUCKETS
        assert "code_or_cli" in REQUIRED_BUCKETS

    def test_required_buckets_is_tuple(self):
        assert isinstance(REQUIRED_BUCKETS, tuple)


# ---------------------------------------------------------------------------
# 9. is_known_profile returns False for unknown models
# ---------------------------------------------------------------------------


class TestIsKnownProfile:
    def test_returns_false_for_unknown(self):
        assert is_known_profile("completely-unknown-model") is False

    def test_returns_true_for_registered(self, registered_profile):
        assert is_known_profile("registered-test-model") is True

    def test_load_profile_returns_registered(self, registered_profile):
        assert load_profile("registered-test-model") is registered_profile
