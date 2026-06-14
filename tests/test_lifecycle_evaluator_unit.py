"""Pure unit tests for lifecycle policy evaluators.

These tests call the refactored _evaluate_* functions directly with
EvidenceSnapshot instances — no database needed. They validate that
the policy logic produces correct TransitionDecisions for various
evidence configurations.
"""

from nokori.lifecycle.transitions import (
    EvidenceSnapshot,
    _evaluate_active,
    _evaluate_candidate,
    _evaluate_suppressed,
    _evaluate_trusted,
)

# ---------------------------------------------------------------------------
# _evaluate_candidate: normal promotion path
# ---------------------------------------------------------------------------


class TestEvaluateCandidatePromotion:
    def test_promotes_with_sufficient_shadow_evidence(self):
        """Normal path: strong >= 3, evaluated >= 5, sessions >= 2, no FP."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=3,
            shadow_would_help_low=2,
            shadow_evaluated_count=5,
            shadow_distinct_sessions=2,
            shadow_fp_rate=0.0,
            synthetic_eval_passed=True,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status == "active"
        assert "shadow_promotion" in decision.reason

    def test_promotes_without_synthetic_eval_if_shadow_sufficient(self):
        """Shadow evidence substitutes for failed synthetic eval."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=4,
            shadow_would_help_low=2,
            shadow_evaluated_count=6,
            shadow_distinct_sessions=3,
            shadow_fp_rate=0.0,
            synthetic_eval_passed=False,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status == "active"

    def test_blocked_by_insufficient_sessions(self):
        """Strong evidence but only 1 session blocks promotion."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=5,
            shadow_would_help_low=2,
            shadow_evaluated_count=7,
            shadow_distinct_sessions=1,  # needs >= 2
            shadow_fp_rate=0.0,
            synthetic_eval_passed=True,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status is None

    def test_blocked_by_insufficient_strong_count(self):
        """Evaluated enough but strong < 3 blocks promotion."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=2,  # needs >= 3
            shadow_would_help_low=4,
            shadow_evaluated_count=6,
            shadow_distinct_sessions=2,
            shadow_fp_rate=0.0,
            synthetic_eval_passed=True,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status is None

    def test_blocked_by_false_positive_rate(self):
        """Any non-zero shadow FP rate blocks (threshold is 0.0)."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=3,
            shadow_would_help_low=1,
            shadow_irrelevant=1,
            shadow_evaluated_count=5,
            shadow_distinct_sessions=2,
            shadow_fp_rate=0.2,  # > 0.0
            synthetic_eval_passed=True,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status is None

    def test_blocked_by_risky_events(self):
        """Any risky/near_miss blocks (threshold max is 0)."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=3,
            shadow_would_help_low=1,
            shadow_risky=1,
            shadow_evaluated_count=5,
            shadow_distinct_sessions=2,
            shadow_fp_rate=0.0,
            synthetic_eval_passed=True,
        )
        decision = _evaluate_candidate(evidence)
        # risky_harmful = 1 >= CANDIDATE_TO_ACTIVE.risky_or_near_miss_shadow_count_max (0)
        # BUT also risky=1 < CANDIDATE_TO_ARCHIVED.risky_or_harmful_shadow_count_min (2)
        # So it does NOT archive, just blocks promotion
        assert decision.new_status is None


# ---------------------------------------------------------------------------
# _evaluate_candidate: archival path
# ---------------------------------------------------------------------------


class TestEvaluateCandidateArchival:
    def test_archives_on_risky_count(self):
        """risky + near_miss >= 2 triggers archival."""
        evidence = EvidenceSnapshot(
            shadow_risky=2,
            shadow_evaluated_count=2,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status == "archived"
        assert "risky_or_harmful" in decision.reason

    def test_archives_on_near_miss_count(self):
        """near_miss alone >= 2 triggers archival."""
        evidence = EvidenceSnapshot(
            shadow_near_miss=2,
            shadow_evaluated_count=2,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status == "archived"

    def test_archives_on_irrelevant_count(self):
        """irrelevant >= 5 triggers archival."""
        evidence = EvidenceSnapshot(
            shadow_irrelevant=5,
            shadow_evaluated_count=5,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status == "archived"
        assert "irrelevant" in decision.reason

    def test_archives_on_replacement(self):
        """has_replacement triggers archival."""
        evidence = EvidenceSnapshot(has_replacement=True)
        decision = _evaluate_candidate(evidence)
        assert decision.new_status == "archived"
        assert "replacement" in decision.reason


# ---------------------------------------------------------------------------
# _evaluate_candidate: single-session exception
# ---------------------------------------------------------------------------


class TestEvaluateCandidateSingleSession:
    def test_single_session_promotes_with_all_criteria(self):
        """High quality + strong single session + miss evidence promotes."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=3,
            shadow_would_help_low=2,
            shadow_evaluated_count=5,
            shadow_distinct_sessions=1,  # Only 1 session - normal path blocked
            shadow_fp_rate=0.0,
            best_single_session_strong=3,
            best_single_session_contexts=3,
            admission_quality=0.92,
            has_miss_evidence=True,
            synthetic_eval_passed=True,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status == "active"
        assert "single_session_exception" in decision.reason

    def test_single_session_blocked_without_miss_evidence(self):
        """All criteria met except miss evidence blocks promotion."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=3,
            shadow_would_help_low=2,
            shadow_evaluated_count=5,
            shadow_distinct_sessions=1,
            shadow_fp_rate=0.0,
            best_single_session_strong=3,
            best_single_session_contexts=3,
            admission_quality=0.92,
            has_miss_evidence=False,
            synthetic_eval_passed=True,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status is None

    def test_single_session_blocked_by_low_quality(self):
        """Quality below 0.88 blocks single-session exception."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=3,
            shadow_would_help_low=2,
            shadow_evaluated_count=5,
            shadow_distinct_sessions=1,
            shadow_fp_rate=0.0,
            best_single_session_strong=3,
            best_single_session_contexts=3,
            admission_quality=0.70,  # Below 0.88
            has_miss_evidence=True,
            synthetic_eval_passed=True,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status is None

    def test_single_session_blocked_by_insufficient_contexts(self):
        """Fewer than 2 distinct contexts in best session blocks."""
        evidence = EvidenceSnapshot(
            shadow_would_help_high=3,
            shadow_would_help_low=2,
            shadow_evaluated_count=5,
            shadow_distinct_sessions=1,
            shadow_fp_rate=0.0,
            best_single_session_strong=3,
            best_single_session_contexts=1,  # needs >= 2
            admission_quality=0.92,
            has_miss_evidence=True,
            synthetic_eval_passed=True,
        )
        decision = _evaluate_candidate(evidence)
        assert decision.new_status is None
        assert "context diversity" in decision.reason


# ---------------------------------------------------------------------------
# _evaluate_active
# ---------------------------------------------------------------------------


class TestEvaluateActive:
    def test_suppresses_on_harmful(self):
        """Any lifetime harmful >= 1 triggers suppression."""
        evidence = EvidenceSnapshot(harmful_lifetime=1, fire_total_evaluated=1)
        decision = _evaluate_active(evidence)
        assert decision.new_status == "suppressed"
        assert "harmful" in decision.reason

    def test_suppresses_on_irrelevant_in_last_5(self):
        """3+ irrelevant in last 5 triggers suppression."""
        evidence = EvidenceSnapshot(
            irrelevant_in_last_5=3,
            fire_total_evaluated=5,
        )
        decision = _evaluate_active(evidence)
        assert decision.new_status == "suppressed"
        assert "irrelevant" in decision.reason

    def test_suppresses_on_high_fp_rate(self):
        """FP rate >= 0.50 with sufficient denominator triggers suppression."""
        evidence = EvidenceSnapshot(
            false_positive_rate=0.50,
            fire_total_evaluated=5,  # >= MINIMUM_RATE_DENOMINATOR
        )
        decision = _evaluate_active(evidence)
        assert decision.new_status == "suppressed"
        assert "false_positive" in decision.reason

    def test_fp_rate_below_denominator_does_not_suppress(self):
        """FP rate check requires minimum_rate_denominator events."""
        evidence = EvidenceSnapshot(
            false_positive_rate=0.60,
            fire_total_evaluated=4,  # < 5 = MINIMUM_RATE_DENOMINATOR
        )
        decision = _evaluate_active(evidence)
        assert decision.new_status is None

    def test_promotes_to_trusted(self):
        """Sufficient observed_useful_strong across sessions promotes to trusted."""
        evidence = EvidenceSnapshot(
            observed_useful_strong=3,
            fire_total_evaluated=5,
            distinct_strong_useful_sessions=2,
            harmful_lifetime=0,
            false_positive_rate=0.0,
        )
        decision = _evaluate_active(evidence)
        assert decision.new_status == "trusted"
        assert "trusted_promotion" in decision.reason

    def test_trusted_blocked_by_harmful(self):
        """Even 1 lifetime harmful blocks trusted promotion (harmful_count_max=0)."""
        evidence = EvidenceSnapshot(
            observed_useful_strong=3,
            fire_total_evaluated=5,
            distinct_strong_useful_sessions=2,
            harmful_lifetime=1,  # blocks
            false_positive_rate=0.0,
        )
        decision = _evaluate_active(evidence)
        # Harmful >= 1 triggers suppression FIRST (checked before trusted promotion)
        assert decision.new_status == "suppressed"

    def test_trusted_blocked_by_insufficient_sessions(self):
        """Only 1 distinct strong useful session blocks trusted promotion."""
        evidence = EvidenceSnapshot(
            observed_useful_strong=3,
            fire_total_evaluated=5,
            distinct_strong_useful_sessions=1,  # needs >= 2
            harmful_lifetime=0,
            false_positive_rate=0.0,
        )
        decision = _evaluate_active(evidence)
        assert decision.new_status is None

    def test_no_transition_with_mixed_evidence(self):
        """Moderate evidence doesn't trigger any transition."""
        evidence = EvidenceSnapshot(
            observed_useful_strong=1,
            fire_total_evaluated=3,
            distinct_strong_useful_sessions=1,
            harmful_lifetime=0,
            false_positive_rate=0.1,
            irrelevant_in_last_5=1,
        )
        decision = _evaluate_active(evidence)
        assert decision.new_status is None


# ---------------------------------------------------------------------------
# _evaluate_trusted
# ---------------------------------------------------------------------------


class TestEvaluateTrusted:
    def test_suppresses_on_harmful(self):
        """Harmful >= 1 triggers suppression."""
        evidence = EvidenceSnapshot(harmful_lifetime=1, fire_total_evaluated=1)
        decision = _evaluate_trusted(evidence)
        assert decision.new_status == "suppressed"

    def test_suppresses_on_irrelevant_in_last_5(self):
        """3+ irrelevant in last 5 triggers suppression."""
        evidence = EvidenceSnapshot(
            irrelevant_in_last_5=3,
            fire_total_evaluated=5,
        )
        decision = _evaluate_trusted(evidence)
        assert decision.new_status == "suppressed"

    def test_suppresses_on_high_fp_rate(self):
        """FP rate >= 0.35 with denominator >= 5 triggers suppression."""
        evidence = EvidenceSnapshot(
            false_positive_rate=0.35,
            fire_total_evaluated=5,
        )
        decision = _evaluate_trusted(evidence)
        assert decision.new_status == "suppressed"

    def test_decays_to_active(self):
        """Decay: no useful, high irrelevant, high FP rate."""
        evidence = EvidenceSnapshot(
            observed_useful_total=0,  # <= max (0)
            irrelevant_in_window=3,  # >= min (2)
            fire_total_evaluated=10,  # >= 5
            harmful_lifetime=0,  # <= max (0)
            false_positive_rate=0.30,  # >= min (0.30)
            irrelevant_in_last_5=2,  # < 3 (doesn't trigger suppression)
        )
        decision = _evaluate_trusted(evidence)
        assert decision.new_status == "active"
        assert "decay" in decision.reason

    def test_no_decay_when_useful_present(self):
        """observed_useful > 0 blocks decay (max is 0)."""
        evidence = EvidenceSnapshot(
            observed_useful_total=1,  # > 0 = max
            irrelevant_in_window=3,
            fire_total_evaluated=10,
            harmful_lifetime=0,
            false_positive_rate=0.30,
            irrelevant_in_last_5=2,
        )
        decision = _evaluate_trusted(evidence)
        assert decision.new_status is None

    def test_cross_project_promotion(self):
        """Trusted project-scoped rule with 3+ distinct projects triggers promotion."""
        evidence = EvidenceSnapshot(
            fire_total_evaluated=0,
            project_scope="project",
            distinct_useful_projects=3,
        )
        decision = _evaluate_trusted(evidence)
        assert decision.new_status is None  # scope change, not status change
        assert decision.reason == "cross_project_promotion"

    def test_cross_project_not_triggered_for_global(self):
        """Global-scoped rule does not trigger cross-project promotion."""
        evidence = EvidenceSnapshot(
            fire_total_evaluated=0,
            project_scope="global",
            distinct_useful_projects=5,
        )
        decision = _evaluate_trusted(evidence)
        assert decision.reason != "cross_project_promotion"
        assert decision.new_status is None


# ---------------------------------------------------------------------------
# _evaluate_suppressed
# ---------------------------------------------------------------------------


class TestEvaluateSuppressed:
    def test_missing_suppressed_at(self):
        """NULL suppressed_at prevents evaluation."""
        evidence = EvidenceSnapshot(suppressed_at_missing=True)
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status is None
        assert "missing" in decision.reason

    def test_unparseable_suppressed_at(self):
        """Unparseable suppressed_at prevents evaluation."""
        evidence = EvidenceSnapshot(suppressed_at_unparseable=True)
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status is None
        assert "unparseable" in decision.reason

    def test_archives_on_risky_after_suppression(self):
        """risky + near_miss >= 2 after suppression triggers archival."""
        evidence = EvidenceSnapshot(
            shadow_risky=2,
        )
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status == "archived"
        assert "risky_or_harmful" in decision.reason

    def test_archives_on_replacement(self):
        """has_replacement triggers archival."""
        evidence = EvidenceSnapshot(has_replacement=True)
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status == "archived"
        assert "replacement" in decision.reason

    def test_archives_on_ttl_expired_no_recovery(self):
        """TTL expired with insufficient recovery evidence archives."""
        evidence = EvidenceSnapshot(
            ttl_expired=True,
            shadow_would_help_high=1,  # < 3 required
        )
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status == "archived"
        assert "no_recovery_before_ttl" in decision.reason

    def test_archives_on_ttl_expired_even_with_evidence(self):
        """TTL expired archives even if recovery thresholds are met."""
        evidence = EvidenceSnapshot(
            ttl_expired=True,
            shadow_would_help_high=5,
            shadow_distinct_sessions=3,
        )
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status == "archived"
        assert "ttl_expired" in decision.reason

    def test_recovers_to_active(self):
        """Sufficient recovery evidence before TTL promotes to active."""
        evidence = EvidenceSnapshot(
            ttl_expired=False,
            shadow_would_help_high=3,
            shadow_distinct_sessions=2,
            recent_harmful_after_suppression=0,
        )
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status == "active"
        assert "recovery" in decision.reason

    def test_recovery_blocked_by_recent_harmful(self):
        """Recent harmful fire event after suppression blocks recovery."""
        evidence = EvidenceSnapshot(
            ttl_expired=False,
            shadow_would_help_high=3,
            shadow_distinct_sessions=2,
            recent_harmful_after_suppression=1,  # > 0 = max
        )
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status is None

    def test_recovery_blocked_by_insufficient_sessions(self):
        """Only 1 distinct session blocks recovery (needs >= 2)."""
        evidence = EvidenceSnapshot(
            ttl_expired=False,
            shadow_would_help_high=3,
            shadow_distinct_sessions=1,  # needs >= 2
            recent_harmful_after_suppression=0,
        )
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status is None

    def test_no_transition_with_partial_evidence(self):
        """Some recovery evidence but not enough stays suppressed."""
        evidence = EvidenceSnapshot(
            ttl_expired=False,
            shadow_would_help_high=1,  # needs >= 3
            shadow_distinct_sessions=2,
            recent_harmful_after_suppression=0,
        )
        decision = _evaluate_suppressed(evidence)
        assert decision.new_status is None
