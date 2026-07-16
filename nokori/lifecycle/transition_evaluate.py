"""Pure policy evaluation functions for lifecycle transitions (no DB access)."""

from __future__ import annotations

from ..policy import (
    ACTIVE_TO_SUPPRESSED,
    ACTIVE_TO_TRUSTED,
    CANDIDATE_TO_ACTIVE,
    CANDIDATE_TO_ACTIVE_SINGLE_SESSION,
    CANDIDATE_TO_ARCHIVED,
    CROSS_PROJECT_PROMOTION_THRESHOLD,
    MINIMUM_RATE_DENOMINATOR,
    SUPPRESSED_TO_ACTIVE,
    SUPPRESSED_TO_ARCHIVED,
    TRUSTED_TO_ACTIVE,
    TRUSTED_TO_SUPPRESSED,
)
from .transition_types import EvidenceSnapshot, TransitionDecision


def _evaluate_candidate(evidence: EvidenceSnapshot) -> TransitionDecision:
    """Pure policy: evaluate candidate rule for promotion/archival.

    Accepts pre-aggregated EvidenceSnapshot, returns a TransitionDecision
    without touching the database.
    """
    # Check candidate -> archived (fast downgrade path)
    risky_harmful = evidence.shadow_risky + evidence.shadow_near_miss
    if risky_harmful >= CANDIDATE_TO_ARCHIVED.risky_or_harmful_shadow_count_min:
        return TransitionDecision(
            new_status="archived",
            reason=f"risky_or_harmful_shadow_count={risky_harmful}",
        )

    if evidence.shadow_irrelevant >= CANDIDATE_TO_ARCHIVED.irrelevant_shadow_count_min:
        return TransitionDecision(
            new_status="archived",
            reason=f"irrelevant_shadow_count={evidence.shadow_irrelevant}",
        )

    if evidence.has_replacement:
        return TransitionDecision(
            new_status="archived",
            reason="covered_by_replacement",
        )

    # Check candidate -> active (normal path)
    th = CANDIDATE_TO_ACTIVE
    strong_count = evidence.shadow_would_help_high
    evaluated_count = evidence.shadow_evaluated_count
    distinct_sessions = evidence.shadow_distinct_sessions
    shadow_fp_rate = evidence.shadow_fp_rate

    normal_path = (
        strong_count >= th.shadow_strong_match_count_min
        and evaluated_count >= th.evaluated_shadow_match_count_min
        and distinct_sessions >= th.distinct_shadow_sessions_min
        and strong_count >= th.counterfactual_would_help_high_min
        and risky_harmful <= th.risky_or_near_miss_shadow_count_max
        and shadow_fp_rate <= th.shadow_false_positive_rate_max
    )

    # Shadow evidence can substitute for synthetic eval: real-world matching
    # already demonstrates the matcher works, the simulated test is redundant.
    if not evidence.synthetic_eval_passed and not normal_path:
        return TransitionDecision(
            new_status=None,
            reason="synthetic_eval not passed and insufficient shadow evidence",
        )

    if normal_path:
        return TransitionDecision(
            new_status="active",
            reason=(
                f"shadow_promotion: strong={strong_count} "
                f"evaluated={evaluated_count} sessions={distinct_sessions}"
            ),
        )

    # Check single-session exception
    ss = CANDIDATE_TO_ACTIVE_SINGLE_SESSION
    best_single_session_strong = evidence.best_single_session_strong

    has_single_session_evidence = (
        evidence.admission_quality >= ss.admission_overall_quality_min
        and best_single_session_strong >= ss.shadow_strong_match_count_min
        and evaluated_count >= ss.evaluated_shadow_match_count_min
        and best_single_session_strong >= ss.counterfactual_would_help_high_min
        and risky_harmful <= ss.risky_or_near_miss_shadow_count_max
        and shadow_fp_rate <= ss.shadow_false_positive_rate_max
    )

    # Verify context diversity + observed_agent_miss_or_user_correction
    if has_single_session_evidence:
        best_session_contexts = evidence.best_single_session_contexts
        if best_session_contexts < 2:
            return TransitionDecision(
                new_status=None,
                reason=(
                    f"single_session_exception: insufficient per-session context diversity "
                    f"({best_session_contexts} < 2)"
                ),
            )

        if evidence.has_miss_evidence:
            return TransitionDecision(
                new_status="active",
                reason=(
                    f"single_session_exception: quality={evidence.admission_quality:.2f} "
                    f"strong={strong_count} "
                    f"contexts={best_session_contexts}"
                ),
            )

    return TransitionDecision(
        new_status=None,
        reason="insufficient promotion evidence",
    )


def _evaluate_active(evidence: EvidenceSnapshot) -> TransitionDecision:
    """Pure policy: evaluate active rule for promotion/suppression.

    Accepts pre-aggregated EvidenceSnapshot, returns a TransitionDecision
    without touching the database.
    """
    # Check active -> suppressed (fast downgrade)
    # Harmful uses lifetime count — does NOT decay by time (spec 3.4)
    sup = ACTIVE_TO_SUPPRESSED
    if evidence.harmful_lifetime >= sup.harmful_count_min:
        return TransitionDecision(
            new_status="suppressed",
            reason=f"lifetime_harmful_count={evidence.harmful_lifetime}",
        )

    if evidence.irrelevant_in_last_5 >= sup.irrelevant_count_in_last_5_min:
        return TransitionDecision(
            new_status="suppressed",
            reason=f"irrelevant_in_last_5={evidence.irrelevant_in_last_5}",
        )

    fp_rate = evidence.false_positive_rate
    total_evaluated = evidence.fire_total_evaluated
    if (
        total_evaluated >= MINIMUM_RATE_DENOMINATOR
        and fp_rate >= sup.recent_false_positive_rate_min
    ):
        return TransitionDecision(
            new_status="suppressed",
            reason=f"false_positive_rate={fp_rate:.2f}",
        )

    # Check active -> trusted (slow upgrade)
    # INVARIANT: trusted promotion uses ONLY fire events (observed_useful), never shadow/counterfactual
    # Use observed_useful_strong (attribution_weight > 0.5) for promotion threshold.
    th = ACTIVE_TO_TRUSTED
    observed_useful = evidence.observed_useful_strong
    distinct_sessions = evidence.distinct_strong_useful_sessions

    # Rate-based promotion NOT allowed below minimum_rate_denominator (spec 3.4)
    # Spec 3.3: harmful_count = 0 for trusted promotion. Per spec 3.4:
    # "Harmful events do not decay below suppression thresholds merely because time passes."
    # Use lifetime harmful for both suppression AND promotion gating.
    if (
        total_evaluated >= th.evaluated_fire_count_min
        and total_evaluated >= MINIMUM_RATE_DENOMINATOR
        and observed_useful >= th.observed_useful_count_min
        and distinct_sessions >= th.distinct_observed_useful_sessions_min
        and evidence.harmful_lifetime <= th.harmful_count_max
        and fp_rate <= th.recent_false_positive_rate_max
    ):
        return TransitionDecision(
            new_status="trusted",
            reason=(
                f"trusted_promotion: useful={observed_useful} "
                f"evaluated={total_evaluated} sessions={distinct_sessions}"
            ),
        )

    return TransitionDecision(new_status=None, reason="no transition triggered")


def _evaluate_trusted(evidence: EvidenceSnapshot) -> TransitionDecision:
    """Pure policy: evaluate trusted rule for suppression/decay/cross-project.

    Accepts pre-aggregated EvidenceSnapshot, returns a TransitionDecision
    without touching the database.
    """
    # Check trusted -> suppressed (fast downgrade)
    # Harmful uses lifetime count — does NOT decay by time (spec 3.4)
    sup = TRUSTED_TO_SUPPRESSED
    if evidence.harmful_lifetime >= sup.harmful_count_min:
        return TransitionDecision(
            new_status="suppressed",
            reason=f"lifetime_harmful_count={evidence.harmful_lifetime}",
        )

    if evidence.irrelevant_in_last_5 >= sup.irrelevant_count_in_last_5_min:
        return TransitionDecision(
            new_status="suppressed",
            reason=f"irrelevant_in_last_5={evidence.irrelevant_in_last_5}",
        )

    fp_rate = evidence.false_positive_rate
    total_evaluated = evidence.fire_total_evaluated
    if (
        total_evaluated >= MINIMUM_RATE_DENOMINATOR
        and fp_rate >= sup.recent_false_positive_rate_min
    ):
        return TransitionDecision(
            new_status="suppressed",
            reason=f"false_positive_rate={fp_rate:.2f}",
        )

    # Check trusted -> active (decay)
    th = TRUSTED_TO_ACTIVE
    observed_useful = evidence.observed_useful_total
    irrelevant = evidence.irrelevant_in_window

    # Rate-based decay requires minimum_rate_denominator (spec 3.4)
    # Spec 3.3 'harmful_count = 0' — use lifetime harmful for consistency.
    if (
        total_evaluated >= th.evaluated_fire_count_in_recent_window_min
        and total_evaluated >= MINIMUM_RATE_DENOMINATOR
        and observed_useful <= th.observed_useful_count_in_recent_window_max
        and irrelevant >= th.irrelevant_count_in_recent_window_min
        and evidence.harmful_lifetime <= th.harmful_count_max
        and fp_rate >= th.recent_false_positive_rate_min
    ):
        return TransitionDecision(
            new_status="active",
            reason=(
                f"trust_decay: useful={observed_useful} irrelevant={irrelevant} fp_rate={fp_rate:.2f}"
            ),
        )

    # Cross-project promotion (ADR 0002: default on).
    # Uses lifetime count (no time window) — a rule that helped across 3+ projects
    # at any point in its history has proven cross-project value.
    if evidence.project_scope == "project" and evidence.distinct_useful_projects >= CROSS_PROJECT_PROMOTION_THRESHOLD:
        return TransitionDecision(
            new_status=None,
            reason="cross_project_promotion",
            metadata={"distinct_project_count": evidence.distinct_useful_projects},
        )

    return TransitionDecision(new_status=None, reason="no transition triggered")


def _evaluate_suppressed(evidence: EvidenceSnapshot) -> TransitionDecision:
    """Pure policy: evaluate suppressed rule for recovery/archival.

    Accepts pre-aggregated EvidenceSnapshot, returns a TransitionDecision
    without touching the database.
    """
    # Guard: if suppressed_at is NULL (e.g. migrated rule), skip evaluation entirely.
    if evidence.suppressed_at_missing:
        return TransitionDecision(
            new_status=None,
            reason="missing suppressed_at timestamp",
        )

    # Guard: unparseable suppressed_at
    if evidence.suppressed_at_unparseable:
        return TransitionDecision(
            new_status=None,
            reason="unparseable suppressed_at timestamp",
        )

    # Check suppressed -> archived (fast downgrade)
    risky_harmful = evidence.shadow_risky + evidence.shadow_near_miss
    if risky_harmful >= SUPPRESSED_TO_ARCHIVED.risky_or_harmful_shadow_count_after_suppression_min:
        return TransitionDecision(
            new_status="archived",
            reason=f"risky_or_harmful_after_suppression={risky_harmful}",
        )

    if evidence.has_replacement:
        return TransitionDecision(
            new_status="archived",
            reason="covered_by_replacement",
        )

    # Check TTL FIRST — prevents recovery after TTL expiry
    if evidence.ttl_expired:
        # TTL expired AND recovery evidence insufficient -> archive
        would_help_high = evidence.shadow_would_help_high
        if would_help_high < SUPPRESSED_TO_ACTIVE.shadow_recovery_would_help_high_min:
            return TransitionDecision(
                new_status="archived",
                reason="no_recovery_before_ttl",
            )
        # TTL expired but recovery evidence exists — still archive (no recovery after TTL)
        return TransitionDecision(
            new_status="archived",
            reason="ttl_expired",
        )

    # TTL NOT expired — check suppressed -> active (recovery)
    th = SUPPRESSED_TO_ACTIVE
    would_help_high = evidence.shadow_would_help_high
    distinct_sessions = evidence.shadow_distinct_sessions
    recent_harmful = evidence.recent_harmful_after_suppression

    if (
        would_help_high >= th.shadow_recovery_would_help_high_min
        and distinct_sessions >= th.distinct_recovery_sessions_min
        and recent_harmful <= th.recent_harmful_count_max
    ):
        return TransitionDecision(
            new_status="active",
            reason=f"shadow_recovery: would_help_high={would_help_high} sessions={distinct_sessions}",
        )

    return TransitionDecision(new_status=None, reason="no transition triggered")

