"""Tests for nokori.runtime.applicability hard eligibility engine.

Covers sections 9.4-9.5 of the autonomous rule quality flywheel plan:
state permissions, trigger evidence paths, severity constraints.
"""

from nokori.policy import (
    DYNAMIC_IDF_NORMAL,
    DYNAMIC_IDF_SMALL_POOL,
    SMALL_POOL_THRESHOLD,
)
from nokori.runtime.applicability import evaluate_applicability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Baseline kwargs that pass trigger evidence (Path A: strong_variant + concepts).
PASS_BASELINE = dict(
    rule_status="active",
    rule_severity="reminder",
    rule_first_observed_useful_at="2025-01-01T00:00:00Z",
    trigger_idf_sum=0.0,
    trigger_coverage=0.0,
    distinct_trigger_terms=0,
    strong_variant_phrase_hit=True,
    required_concepts_match=True,
    excluded_context_hit=False,
    action_only_match=False,
    search_only_match=False,
    embedding_only_match=False,
    idf_stats_available=False,
    pool_size=0,
    has_tool_input=False,
    tool_evidence_passed=False,
    false_positive_score=0.0,
)


def _eval(**overrides):
    """Call evaluate_applicability merging overrides onto PASS_BASELINE."""
    kwargs = {**PASS_BASELINE, **overrides}
    return evaluate_applicability(**kwargs)


# ---------------------------------------------------------------------------
# 1. action_only_match -> COLD
# ---------------------------------------------------------------------------


class TestActionOnlyMatch:
    def test_action_only_produces_cold(self):
        r = _eval(action_only_match=True)
        assert r.decision == "cold"
        assert r.eligible is False
        assert r.trigger_evidence_passed is False

    def test_action_only_overrides_strong_variant(self):
        r = _eval(action_only_match=True, strong_variant_phrase_hit=True)
        assert r.decision == "cold"


# ---------------------------------------------------------------------------
# 2. search_only_match -> COLD (recall only, cannot WARM/HOT/Gate)
# ---------------------------------------------------------------------------


class TestSearchOnlyMatch:
    def test_search_only_produces_cold(self):
        r = _eval(search_only_match=True)
        assert r.decision == "cold"
        assert r.eligible is False

    def test_search_only_reason_mentions_recall(self):
        r = _eval(search_only_match=True)
        assert "recall" in r.reason


# ---------------------------------------------------------------------------
# 3. embedding_only_match -> COLD
# ---------------------------------------------------------------------------


class TestEmbeddingOnlyMatch:
    def test_embedding_only_produces_cold(self):
        r = _eval(embedding_only_match=True)
        assert r.decision == "cold"
        assert r.eligible is False
        assert r.trigger_evidence_passed is False


# ---------------------------------------------------------------------------
# 4. excluded_context_hit -> COLD (override not tested here)
# ---------------------------------------------------------------------------


class TestExcludedContextHit:
    def test_excluded_context_produces_cold(self):
        r = _eval(excluded_context_hit=True)
        assert r.decision == "cold"
        assert r.eligible is False

    def test_excluded_context_reason_mentions_excluded(self):
        r = _eval(excluded_context_hit=True)
        assert "excluded" in r.reason


# ---------------------------------------------------------------------------
# 5. active cannot Gate (regardless of other evidence)
# ---------------------------------------------------------------------------


class TestActiveCannotGate:
    def test_active_reminder_cannot_gate(self):
        r = _eval(rule_status="active", rule_severity="reminder")
        assert r.decision != "gate"

    def test_active_gate_eligible_cannot_gate(self):
        r = _eval(rule_status="active", rule_severity="gate_eligible")
        assert r.decision != "gate"

    def test_active_high_risk_cannot_gate(self):
        r = _eval(rule_status="active", rule_severity="high_risk")
        assert r.decision != "gate"

    def test_active_max_is_hot(self):
        r = _eval(
            rule_status="active",
            rule_severity="reminder",
            rule_first_observed_useful_at="2025-01-01T00:00:00Z",
        )
        assert r.decision in ("warm", "hot")


# ---------------------------------------------------------------------------
# 6. trusted Gate requires severity=gate_eligible + tool evidence when
#    tool input exists
# ---------------------------------------------------------------------------


class TestTrustedGate:
    def test_trusted_gate_eligible_no_tool_input_gates(self):
        r = _eval(
            rule_status="trusted",
            rule_severity="gate_eligible",
            has_tool_input=False,
        )
        assert r.decision == "gate"
        assert r.eligible is True

    def test_trusted_gate_eligible_with_tool_evidence_gates(self):
        r = _eval(
            rule_status="trusted",
            rule_severity="gate_eligible",
            has_tool_input=True,
            tool_evidence_passed=True,
        )
        assert r.decision == "gate"

    def test_trusted_gate_eligible_tool_input_no_evidence_no_gate(self):
        r = _eval(
            rule_status="trusted",
            rule_severity="gate_eligible",
            has_tool_input=True,
            tool_evidence_passed=False,
        )
        assert r.decision != "gate"
        assert r.decision in ("warm", "hot")

    def test_trusted_reminder_cannot_gate(self):
        r = _eval(rule_status="trusted", rule_severity="reminder")
        assert r.decision != "gate"

    def test_trusted_high_risk_cannot_gate(self):
        r = _eval(rule_status="trusted", rule_severity="high_risk")
        assert r.decision != "gate"


# ---------------------------------------------------------------------------
# 7. newly promoted active (no first_observed_useful_at) -> WARM only,
#    never HOT
# ---------------------------------------------------------------------------


class TestNewlyPromotedActive:
    def test_no_first_observed_useful_at_is_warm(self):
        r = _eval(
            rule_status="active",
            rule_first_observed_useful_at=None,
        )
        assert r.decision == "warm"
        assert r.eligible is True

    def test_no_first_observed_useful_at_never_hot(self):
        r = _eval(
            rule_status="active",
            rule_first_observed_useful_at=None,
            strong_variant_phrase_hit=True,
            required_concepts_match=True,
        )
        assert r.decision == "warm"


# ---------------------------------------------------------------------------
# 8. high_risk severity is stricter than reminder for HOT
# ---------------------------------------------------------------------------


class TestHighRiskStricter:
    """high_risk requires 1.5x IDF + full coverage for HOT; reminder does not."""

    def test_reminder_hot_with_normal_idf(self):
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            rule_status="active",
            rule_severity="reminder",
            rule_first_observed_useful_at="2025-01-01T00:00:00Z",
            strong_variant_phrase_hit=False,
            trigger_idf_sum=policy.absolute_trigger_info_min,
            trigger_coverage=policy.trigger_coverage_min,
            distinct_trigger_terms=policy.distinct_trigger_terms_min,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
        )
        assert r.decision == "hot"

    def test_high_risk_warm_with_normal_idf(self):
        """Same IDF that gives reminder HOT only gives high_risk WARM."""
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            rule_status="active",
            rule_severity="high_risk",
            rule_first_observed_useful_at="2025-01-01T00:00:00Z",
            strong_variant_phrase_hit=False,
            trigger_idf_sum=policy.absolute_trigger_info_min,
            trigger_coverage=policy.trigger_coverage_min,
            distinct_trigger_terms=policy.distinct_trigger_terms_min,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
        )
        assert r.decision == "warm"

    def test_high_risk_hot_with_elevated_idf(self):
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            rule_status="active",
            rule_severity="high_risk",
            rule_first_observed_useful_at="2025-01-01T00:00:00Z",
            strong_variant_phrase_hit=False,
            trigger_idf_sum=1.5 * policy.absolute_trigger_info_min,
            trigger_coverage=policy.trigger_coverage_min,
            distinct_trigger_terms=policy.distinct_trigger_terms_min,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
        )
        assert r.decision == "hot"


# ---------------------------------------------------------------------------
# 9. N=0: only strong_variant + required_concepts path works
# ---------------------------------------------------------------------------


class TestPoolSizeZero:
    def test_pool_zero_strong_variant_passes(self):
        r = _eval(
            pool_size=0,
            idf_stats_available=True,
            strong_variant_phrase_hit=True,
            required_concepts_match=True,
        )
        assert r.trigger_evidence_passed is True
        assert r.eligible is True

    def test_pool_zero_idf_only_fails(self):
        """IDF-based paths cannot fire when pool_size=0."""
        r = _eval(
            pool_size=0,
            idf_stats_available=True,
            strong_variant_phrase_hit=False,
            trigger_idf_sum=10.0,
            trigger_coverage=1.0,
            distinct_trigger_terms=5,
        )
        assert r.trigger_evidence_passed is False
        assert r.decision == "cold"

    def test_pool_zero_no_idf_stats_falls_to_path_a(self):
        r = _eval(
            pool_size=0,
            idf_stats_available=False,
            strong_variant_phrase_hit=True,
            required_concepts_match=True,
        )
        assert r.trigger_evidence_passed is True


# ---------------------------------------------------------------------------
# 10. Small pool: stricter coverage required
# ---------------------------------------------------------------------------


class TestSmallPool:
    """pool_size < SMALL_POOL_THRESHOLD uses DYNAMIC_IDF_SMALL_POOL thresholds."""

    def test_below_threshold_uses_small_pool_policy(self):
        small_pool = SMALL_POOL_THRESHOLD - 1
        small_policy = DYNAMIC_IDF_SMALL_POOL

        # Just barely meets small pool thresholds -> passes
        r = _eval(
            pool_size=small_pool,
            idf_stats_available=True,
            strong_variant_phrase_hit=False,
            trigger_idf_sum=small_policy.absolute_trigger_info_min,
            trigger_coverage=small_policy.trigger_coverage_min,
            distinct_trigger_terms=small_policy.distinct_trigger_terms_min,
        )
        assert r.trigger_evidence_passed is True

    def test_normal_thresholds_insufficient_for_small_pool(self):
        """Normal policy thresholds are lower; they fail under small pool."""
        small_pool = SMALL_POOL_THRESHOLD - 1
        normal_policy = DYNAMIC_IDF_NORMAL

        # Meets normal policy but not small pool policy
        r = _eval(
            pool_size=small_pool,
            idf_stats_available=True,
            strong_variant_phrase_hit=False,
            trigger_idf_sum=normal_policy.absolute_trigger_info_min,
            trigger_coverage=normal_policy.trigger_coverage_min,
            distinct_trigger_terms=normal_policy.distinct_trigger_terms_min,
        )
        assert r.trigger_evidence_passed is False
        assert r.decision == "cold"


# ---------------------------------------------------------------------------
# 11. Trigger evidence path A: strong_variant + concepts
# ---------------------------------------------------------------------------


class TestPathA:
    def test_path_a_passes(self):
        r = _eval(
            strong_variant_phrase_hit=True,
            required_concepts_match=True,
            trigger_idf_sum=0.0,
            trigger_coverage=0.0,
            distinct_trigger_terms=0,
            idf_stats_available=False,
            pool_size=0,
        )
        assert r.trigger_evidence_passed is True
        assert r.eligible is True

    def test_path_a_requires_concepts(self):
        r = _eval(
            strong_variant_phrase_hit=True,
            required_concepts_match=False,
        )
        # required_concepts_match=False is a hard disqualifier checked first
        assert r.decision == "cold"
        assert r.eligible is False


# ---------------------------------------------------------------------------
# 12. Trigger evidence path B: idf_sum + coverage + concepts + distinct_terms
# ---------------------------------------------------------------------------


class TestPathB:
    def test_path_b_passes_normal_pool(self):
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            strong_variant_phrase_hit=False,
            trigger_idf_sum=policy.absolute_trigger_info_min,
            trigger_coverage=policy.trigger_coverage_min,
            distinct_trigger_terms=policy.distinct_trigger_terms_min,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
        )
        assert r.trigger_evidence_passed is True

    def test_path_b_fails_below_idf(self):
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            strong_variant_phrase_hit=False,
            trigger_idf_sum=policy.absolute_trigger_info_min - 0.01,
            trigger_coverage=policy.trigger_coverage_min,
            distinct_trigger_terms=policy.distinct_trigger_terms_min,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
        )
        assert r.trigger_evidence_passed is False

    def test_path_b_fails_below_coverage(self):
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            strong_variant_phrase_hit=False,
            trigger_idf_sum=policy.absolute_trigger_info_min,
            trigger_coverage=policy.trigger_coverage_min - 0.01,
            distinct_trigger_terms=policy.distinct_trigger_terms_min,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
        )
        assert r.trigger_evidence_passed is False

    def test_path_b_fails_below_distinct_terms(self):
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            strong_variant_phrase_hit=False,
            trigger_idf_sum=policy.absolute_trigger_info_min,
            trigger_coverage=policy.trigger_coverage_min,
            distinct_trigger_terms=policy.distinct_trigger_terms_min - 1,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
        )
        assert r.trigger_evidence_passed is False

    def test_dynamic_threshold_overrides_static_absolute_minimum(self):
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            strong_variant_phrase_hit=False,
            trigger_idf_sum=policy.absolute_trigger_info_min + 0.1,
            trigger_coverage=policy.trigger_coverage_min,
            distinct_trigger_terms=policy.distinct_trigger_terms_min,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
            dynamic_trigger_info_min=policy.absolute_trigger_info_min + 1.0,
        )
        assert r.trigger_evidence_passed is False
        assert r.eligible is False


# ---------------------------------------------------------------------------
# 13. Trigger evidence path C: 1.5x idf_sum + concepts + distinct_terms
# ---------------------------------------------------------------------------


class TestPathC:
    def test_path_c_passes_without_coverage(self):
        """Path C relaxes coverage requirement in exchange for 1.5x IDF."""
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            strong_variant_phrase_hit=False,
            trigger_idf_sum=1.5 * policy.absolute_trigger_info_min,
            trigger_coverage=0.0,  # no coverage
            distinct_trigger_terms=policy.distinct_trigger_terms_min,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
        )
        assert r.trigger_evidence_passed is True

    def test_path_c_fails_below_elevated_idf(self):
        policy = DYNAMIC_IDF_NORMAL
        r = _eval(
            strong_variant_phrase_hit=False,
            trigger_idf_sum=1.5 * policy.absolute_trigger_info_min - 0.01,
            trigger_coverage=0.0,
            distinct_trigger_terms=policy.distinct_trigger_terms_min,
            idf_stats_available=True,
            pool_size=SMALL_POOL_THRESHOLD,
        )
        # Path B fails (no coverage), Path C fails (below 1.5x)
        assert r.trigger_evidence_passed is False


# ---------------------------------------------------------------------------
# 14. candidate -> always COLD
# ---------------------------------------------------------------------------


class TestCandidateStatus:
    def test_candidate_is_cold(self):
        r = _eval(rule_status="candidate")
        assert r.decision == "cold"
        assert r.eligible is False

    def test_candidate_trigger_evidence_still_evaluated(self):
        r = _eval(rule_status="candidate")
        assert r.trigger_evidence_passed is True


# ---------------------------------------------------------------------------
# 15. suppressed -> always COLD
# ---------------------------------------------------------------------------


class TestSuppressedStatus:
    def test_suppressed_is_cold(self):
        r = _eval(rule_status="suppressed")
        assert r.decision == "cold"
        assert r.eligible is False

    def test_suppressed_trigger_evidence_still_evaluated(self):
        r = _eval(rule_status="suppressed")
        assert r.trigger_evidence_passed is True


# ---------------------------------------------------------------------------
# 16. required_concepts_match=False -> always COLD
# ---------------------------------------------------------------------------


class TestRequiredConceptsFalse:
    def test_required_concepts_false_is_cold(self):
        r = _eval(required_concepts_match=False)
        assert r.decision == "cold"
        assert r.eligible is False

    def test_required_concepts_false_regardless_of_status(self):
        for status in ("active", "trusted", "candidate", "suppressed"):
            r = _eval(rule_status=status, required_concepts_match=False)
            assert r.decision == "cold", f"Expected cold for status={status}"

    def test_required_concepts_false_regardless_of_strong_variant(self):
        r = _eval(
            required_concepts_match=False,
            strong_variant_phrase_hit=True,
        )
        assert r.decision == "cold"


# ---------------------------------------------------------------------------
# 17. excluded_context_override allows injection despite excluded_context_hit
# ---------------------------------------------------------------------------


class TestExcludedContextOverride:
    def test_excluded_context_override_allows_injection(self):
        """excluded_context_hit=True + excluded_context_override_passed=True -> not COLD."""
        r = _eval(
            excluded_context_hit=True,
            excluded_context_override_passed=True,
        )
        assert r.decision != "cold"
        assert r.eligible is True


# ---------------------------------------------------------------------------
# 18. near_miss_context -> COLD
# ---------------------------------------------------------------------------


class TestNearMissContext:
    def test_near_miss_context_is_cold(self):
        """near_miss_context=True -> COLD."""
        r = _eval(near_miss_context=True)
        assert r.decision == "cold"
        assert r.eligible is False

    def test_near_miss_with_override_passes(self):
        """near_miss_context=True + excluded_context_override_passed=True -> not COLD."""
        r = _eval(
            near_miss_context=True,
            excluded_context_override_passed=True,
        )
        assert r.decision != "cold"
        assert r.eligible is True


# ---------------------------------------------------------------------------
# 19. false_positive_score downgrades active HOT to WARM
# ---------------------------------------------------------------------------


def test_active_hot_blocked_by_false_positive_score():
    """Nonzero false_positive_score downgrades active HOT to WARM."""
    r = _eval(
        rule_status="active",
        rule_severity="reminder",
        rule_first_observed_useful_at="2025-01-01T00:00:00Z",
        strong_variant_phrase_hit=True,
        required_concepts_match=True,
        false_positive_score=0.5,
    )
    assert r.decision == "warm"


# ---------------------------------------------------------------------------
# 20. archived status -> COLD
# ---------------------------------------------------------------------------


def test_archived_state_returns_cold():
    """Archived rules return COLD."""
    r = _eval(rule_status="archived")
    assert r.decision == "cold"
    assert r.eligible is False
