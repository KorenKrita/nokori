"""Tests for nokori.search.embedding_profiles."""

from __future__ import annotations

import pytest

from nokori.policy import SAFETY_MARGIN_COSINE
from nokori.runtime.applicability import ApplicabilityResult, evaluate_applicability
from nokori.search.embedding_profiles import (
    CHECKED_IN_PROFILES,
    REQUIRED_BUCKETS,
    BucketThresholds,
    EmbeddingProfile,
    compute_hot_min,
    compute_warm_min,
    get_threshold,
    is_known_profile,
    load_profile,
    validate_profile,
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
        assert hasattr(profile, "embedding_only_allowed")


# ---------------------------------------------------------------------------
# 2. Generation policy uses same field names as profile schema
# ---------------------------------------------------------------------------


class TestGenerationPolicyFieldNames:
    def test_compute_warm_min_uses_medium_p10_and_near_miss_p95(self):
        bucket = _make_bucket(medium_p10=0.70, near_miss_p95=0.60)
        expected = max(bucket.medium_p10, bucket.near_miss_p95 + SAFETY_MARGIN_COSINE)
        assert compute_warm_min(bucket.medium_p10, bucket.near_miss_p95) == expected

    def test_compute_hot_min_uses_positive_p10_and_near_miss_p99(self):
        bucket = _make_bucket(positive_p10=0.85, near_miss_p99=0.65)
        expected = max(bucket.positive_p10, bucket.near_miss_p99 + SAFETY_MARGIN_COSINE)
        assert compute_hot_min(bucket.positive_p10, bucket.near_miss_p99) == expected

    def test_generation_policy_field_names_match_schema(self):
        """The generation formulas reference fields that exist on BucketThresholds."""
        bucket = _make_bucket()
        # These attribute accesses would raise if field names diverged.
        _ = compute_warm_min(bucket.medium_p10, bucket.near_miss_p95)
        _ = compute_hot_min(bucket.positive_p10, bucket.near_miss_p99)


# ---------------------------------------------------------------------------
# 3. Unknown profile returns None from load_profile
# ---------------------------------------------------------------------------


class TestUnknownProfile:
    def test_load_profile_returns_none_for_unknown(self):
        result = load_profile("nonexistent-model-xyz")
        assert result is None


# ---------------------------------------------------------------------------
# 4. Unknown profile cannot influence WARM/HOT/Gate (get_threshold returns None)
# ---------------------------------------------------------------------------


class TestUnknownCannotInfluence:
    def test_get_threshold_returns_none_for_none_profile(self):
        assert get_threshold(None, "overall", "warm") is None
        assert get_threshold(None, "overall", "hot") is None
        assert get_threshold(None, "zh", "warm") is None
        assert get_threshold(None, None, "hot") is None

    def test_unknown_model_get_threshold_pipeline(self):
        """End-to-end: unknown model -> load_profile -> get_threshold -> None."""
        profile = load_profile("unknown-model-abc")
        assert get_threshold(profile, "overall", "warm") is None
        assert get_threshold(profile, "zh", "hot") is None


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
# 6. Bucket fallback: if specific bucket missing, uses overall
# ---------------------------------------------------------------------------


class TestBucketFallback:
    def test_known_bucket_uses_specific_thresholds(self, registered_profile):
        zh_bucket = _make_bucket(warm_min=0.72, hot_min=0.88)
        registered_profile.buckets["zh"] = zh_bucket

        assert get_threshold(registered_profile, "zh", "warm") == 0.72
        assert get_threshold(registered_profile, "zh", "hot") == 0.88

    def test_unknown_bucket_falls_back_to_overall(self, registered_profile):
        assert get_threshold(registered_profile, "nonexistent_bucket", "warm") == registered_profile.overall.warm_min
        assert get_threshold(registered_profile, "nonexistent_bucket", "hot") == registered_profile.overall.hot_min

    def test_none_bucket_falls_back_to_overall(self, registered_profile):
        assert get_threshold(registered_profile, None, "warm") == registered_profile.overall.warm_min
        assert get_threshold(registered_profile, None, "hot") == registered_profile.overall.hot_min


# ---------------------------------------------------------------------------
# 7. validate_profile catches constraint violations
# ---------------------------------------------------------------------------


class TestValidateProfile:
    def test_valid_profile_passes(self):
        profile = _make_valid_profile()
        errors = validate_profile(profile)
        assert errors == []

    def test_catches_near_miss_p99_gte_positive_p10(self):
        """near_miss_p99 >= positive_p10 is invalid."""
        bad_overall = _make_bucket(near_miss_p99=0.90, positive_p10=0.85)
        profile = EmbeddingProfile(
            model_id="bad-1",
            profile_version="1.0.0",
            dimension=384,
            normalization="cosine",
            overall=bad_overall,
            buckets={
                "zh": _make_bucket(),
                "mixed": _make_bucket(),
                "code_or_cli": _make_bucket(),
            },
        )
        errors = validate_profile(profile)
        assert any("near_miss_p99 must be < positive_p10" in e for e in errors)

    def test_catches_negative_p99_gte_medium_p10(self):
        """negative_p99 >= medium_p10 is invalid."""
        bad_overall = _make_bucket(negative_p99=0.75, medium_p10=0.70)
        profile = EmbeddingProfile(
            model_id="bad-2",
            profile_version="1.0.0",
            dimension=384,
            normalization="cosine",
            overall=bad_overall,
            buckets={
                "zh": _make_bucket(),
                "mixed": _make_bucket(),
                "code_or_cli": _make_bucket(),
            },
        )
        errors = validate_profile(profile)
        assert any("negative_p99 must be < medium_p10" in e for e in errors)

    def test_catches_missing_required_buckets(self):
        """Missing required buckets are reported."""
        profile = EmbeddingProfile(
            model_id="bad-3",
            profile_version="1.0.0",
            dimension=384,
            normalization="cosine",
            overall=_make_bucket(),
            buckets={"zh": _make_bucket()},  # missing mixed, code_or_cli
        )
        errors = validate_profile(profile)
        assert any("required bucket missing: mixed" in e for e in errors)
        assert any("required bucket missing: code_or_cli" in e for e in errors)

    def test_catches_bucket_specific_violations(self):
        """Validation also checks per-bucket consistency."""
        bad_zh = _make_bucket(near_miss_p99=0.90, positive_p10=0.85)
        profile = EmbeddingProfile(
            model_id="bad-4",
            profile_version="1.0.0",
            dimension=384,
            normalization="cosine",
            overall=_make_bucket(),
            buckets={
                "zh": bad_zh,
                "mixed": _make_bucket(),
                "code_or_cli": _make_bucket(),
            },
        )
        errors = validate_profile(profile)
        assert any("zh: near_miss_p99 must be < positive_p10" in e for e in errors)


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
