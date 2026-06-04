"""Hard eligibility engine for runtime applicability decisions.

Implements sections 9.3, 9.4, 9.5 of the autonomous rule quality flywheel plan.
Determines whether a matched rule may inject (and at what level) based on
trigger evidence, state permissions, and severity constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..models import ScoredResult
from ..policy import (
    DYNAMIC_IDF_NORMAL,
    DYNAMIC_IDF_SMALL_POOL,
    SMALL_POOL_THRESHOLD,
    DynamicIDFPolicy,
    Severity,
    Status,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ApplicabilityDecision = Literal["cold", "warm", "hot", "gate"]


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplicabilityResult:
    """Outcome of hard eligibility evaluation for a single rule match."""

    decision: ApplicabilityDecision
    eligible: bool
    reason: str
    trigger_evidence_passed: bool
    penalties: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Legacy compatibility
# ---------------------------------------------------------------------------


def meets_min_evidence(r: ScoredResult) -> bool:
    """Hard eligibility gate: does this result carry enough retrieval signal?

    Replaces ranker.meets_min_evidence with fielded-aware logic.
    """
    # Strong variant phrase hit is high-confidence on its own.
    if r.strong_variant_phrase_hit:
        return True

    # At least 2 trigger tokens matched.
    if len(r.matched_trigger_tokens) >= 2:
        return True

    # 1 trigger token + variant recall.
    if len(r.matched_trigger_tokens) >= 1 and len(r.matched_variant_tokens) >= 1:
        return True

    # Embedding-only with strong cosine: pre-filter pass only.
    # NOTE: embedding_only_match results are always COLD in evaluate_applicability
    # (they lack trigger evidence), so this branch does not influence WARM/HOT/Gate.
    # Retained as a pre-filter for hybrid results that have cosine + partial triggers.
    if r.cosine is not None and r.cosine >= 0.55:
        return True

    # Action-only or search-only matches without trigger evidence are weak.
    if r.action_only_match or r.search_only_match:
        return False

    # Fallback: at least 1 trigger token + some other signal.
    if len(r.matched_trigger_tokens) >= 1 and (
        len(r.matched_action_tokens) >= 1 or len(r.matched_search_tokens) >= 1
    ):
        return True

    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _select_idf_policy(pool_size: int) -> DynamicIDFPolicy:
    if pool_size < SMALL_POOL_THRESHOLD:
        return DYNAMIC_IDF_SMALL_POOL
    return DYNAMIC_IDF_NORMAL


def _trigger_evidence_passes(
    *,
    strong_variant_phrase_hit: bool,
    required_concepts_match: bool,
    trigger_idf_sum: float,
    trigger_coverage: float,
    distinct_trigger_terms: int,
    idf_stats_available: bool,
    pool_size: int,
    dynamic_trigger_info_min: float | None = None,
) -> bool:
    """Evaluate trigger evidence paths (section 9.3).

    Path A: strong_variant_phrase_hit AND required_concepts_match
    Path B: idf_sum >= threshold AND coverage >= min AND concepts AND distinct >= min
    Path C: idf_sum >= 1.5 * threshold AND concepts AND distinct >= min

    N=0: only Path A can pass (strong variant + concepts).
    """
    # Path A is always available
    if strong_variant_phrase_hit and required_concepts_match:
        return True

    # Without IDF stats or empty pool, only Path A applies
    if not idf_stats_available or pool_size == 0:
        return False

    policy = _select_idf_policy(pool_size)
    trigger_info_min = _trigger_info_min(policy, dynamic_trigger_info_min)
    coverage_min = policy.trigger_coverage_min
    distinct_min = policy.distinct_trigger_terms_min

    # Path B
    if (
        trigger_idf_sum >= trigger_info_min
        and trigger_coverage >= coverage_min
        and required_concepts_match
        and distinct_trigger_terms >= distinct_min
    ):
        return True

    # Path C (relaxed coverage, stricter IDF)
    if (
        trigger_idf_sum >= 1.5 * trigger_info_min
        and required_concepts_match
        and distinct_trigger_terms >= distinct_min
    ):
        return True

    return False


def _strong_trigger_evidence(
    *,
    strong_variant_phrase_hit: bool,
    required_concepts_match: bool,
    trigger_idf_sum: float,
    trigger_coverage: float,
    distinct_trigger_terms: int,
    idf_stats_available: bool,
    pool_size: int,
    dynamic_trigger_info_min: float | None = None,
) -> bool:
    """Strong trigger evidence required for HOT on active rules.

    Strong means Path A passes, or Path B passes with full thresholds.
    """
    # Path A is strong by definition
    if strong_variant_phrase_hit and required_concepts_match:
        return True

    if not idf_stats_available or pool_size == 0:
        return False

    policy = _select_idf_policy(pool_size)
    trigger_info_min = _trigger_info_min(policy, dynamic_trigger_info_min)
    coverage_min = policy.trigger_coverage_min
    distinct_min = policy.distinct_trigger_terms_min

    # Strong requires meeting Path B fully (coverage + IDF + distinct + concepts)
    if (
        trigger_idf_sum >= trigger_info_min
        and trigger_coverage >= coverage_min
        and required_concepts_match
        and distinct_trigger_terms >= distinct_min
    ):
        return True

    return False


def _high_risk_strong_evidence(
    *,
    strong_variant_phrase_hit: bool,
    required_concepts_match: bool,
    trigger_idf_sum: float,
    trigger_coverage: float,
    distinct_trigger_terms: int,
    idf_stats_available: bool,
    pool_size: int,
    dynamic_trigger_info_min: float | None = None,
) -> bool:
    """Stricter evidence bar for high_risk severity HOT decisions.

    High-risk rules need stronger proof: Path A, or Path B with elevated IDF.
    """
    if strong_variant_phrase_hit and required_concepts_match:
        return True

    if not idf_stats_available or pool_size == 0:
        return False

    policy = _select_idf_policy(pool_size)
    trigger_info_min = _trigger_info_min(policy, dynamic_trigger_info_min)
    coverage_min = policy.trigger_coverage_min
    distinct_min = policy.distinct_trigger_terms_min

    # High-risk requires 1.5x IDF threshold AND full coverage
    if (
        trigger_idf_sum >= 1.5 * trigger_info_min
        and trigger_coverage >= coverage_min
        and required_concepts_match
        and distinct_trigger_terms >= distinct_min
    ):
        return True

    return False


def _trigger_info_min(
    policy: DynamicIDFPolicy,
    dynamic_trigger_info_min: float | None,
) -> float:
    """Use persisted dynamic threshold when available, never below static floor."""
    if dynamic_trigger_info_min is None:
        return policy.absolute_trigger_info_min
    return max(policy.absolute_trigger_info_min, dynamic_trigger_info_min)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_applicability(
    rule_status: Status,
    rule_severity: Severity,
    rule_first_observed_useful_at: str | None,
    trigger_idf_sum: float,
    trigger_coverage: float,
    distinct_trigger_terms: int,
    strong_variant_phrase_hit: bool,
    required_concepts_match: bool,
    excluded_context_hit: bool,
    excluded_context_override_passed: bool = False,
    action_only_match: bool = False,
    search_only_match: bool = False,
    embedding_only_match: bool = False,
    idf_stats_available: bool = True,
    pool_size: int = 0,
    has_tool_input: bool = False,
    tool_evidence_passed: bool = False,
    observed_usefulness_score: float = 0.0,
    false_positive_score: float = 0.0,
    dynamic_trigger_info_min: float | None = None,
) -> ApplicabilityResult:
    """Evaluate hard eligibility for a single rule match.

    Returns an ApplicabilityResult indicating the maximum injection level
    permitted and whether the rule is eligible for injection at all.
    """
    penalties: list[str] = []

    # ------------------------------------------------------------------
    # Hard disqualifiers -> COLD
    # ------------------------------------------------------------------

    if action_only_match:
        return ApplicabilityResult(
            decision="cold",
            eligible=False,
            reason="action_only_match: trigger evidence absent",
            trigger_evidence_passed=False,
            penalties=penalties,
        )

    if search_only_match:
        return ApplicabilityResult(
            decision="cold",
            eligible=False,
            reason="search_only_match: recall only, cannot inject",
            trigger_evidence_passed=False,
            penalties=penalties,
        )

    if embedding_only_match:
        return ApplicabilityResult(
            decision="cold",
            eligible=False,
            reason="embedding_only_match: trigger evidence absent",
            trigger_evidence_passed=False,
            penalties=penalties,
        )

    if excluded_context_hit and not excluded_context_override_passed:
        return ApplicabilityResult(
            decision="cold",
            eligible=False,
            reason="excluded_context_hit: rule explicitly excluded",
            trigger_evidence_passed=False,
            penalties=penalties,
        )

    if not required_concepts_match:
        return ApplicabilityResult(
            decision="cold",
            eligible=False,
            reason="required_concepts_match failed: trigger evidence cannot pass",
            trigger_evidence_passed=False,
            penalties=penalties,
        )

    # ------------------------------------------------------------------
    # Trigger evidence evaluation
    # ------------------------------------------------------------------

    evidence_kwargs = dict(
        strong_variant_phrase_hit=strong_variant_phrase_hit,
        required_concepts_match=required_concepts_match,
        trigger_idf_sum=trigger_idf_sum,
        trigger_coverage=trigger_coverage,
        distinct_trigger_terms=distinct_trigger_terms,
        idf_stats_available=idf_stats_available,
        pool_size=pool_size,
        dynamic_trigger_info_min=dynamic_trigger_info_min,
    )

    trigger_passed = _trigger_evidence_passes(**evidence_kwargs)

    if not trigger_passed:
        return ApplicabilityResult(
            decision="cold",
            eligible=False,
            reason="trigger evidence insufficient: no path (A/B/C) satisfied",
            trigger_evidence_passed=False,
            penalties=penalties,
        )

    # ------------------------------------------------------------------
    # State permissions (section 9.5)
    # ------------------------------------------------------------------

    # candidate -> shadow only
    if rule_status == "candidate":
        return ApplicabilityResult(
            decision="cold",
            eligible=False,
            reason="candidate status: shadow retrieval only",
            trigger_evidence_passed=True,
            penalties=penalties,
        )

    # suppressed -> shadow recovery only
    if rule_status == "suppressed":
        return ApplicabilityResult(
            decision="cold",
            eligible=False,
            reason="suppressed status: shadow recovery only",
            trigger_evidence_passed=True,
            penalties=penalties,
        )

    # archived -> no hot-path retrieval
    if rule_status == "archived":
        return ApplicabilityResult(
            decision="cold",
            eligible=False,
            reason="archived status: no hot-path retrieval",
            trigger_evidence_passed=False,
            penalties=penalties,
        )

    # ------------------------------------------------------------------
    # False-positive penalty tracking
    # ------------------------------------------------------------------

    if false_positive_score > 0.0:
        penalties.append(
            f"recent_false_positive_score={false_positive_score:.2f}"
        )

    # ------------------------------------------------------------------
    # Active rules
    # ------------------------------------------------------------------

    if rule_status == "active":
        # Newly promoted active (no first_observed_useful_at) -> WARM only
        if rule_first_observed_useful_at is None:
            return ApplicabilityResult(
                decision="warm",
                eligible=True,
                reason="active without observed useful history: WARM only",
                trigger_evidence_passed=True,
                penalties=penalties,
            )

        # Active with history: HOT requires strong evidence
        if rule_severity == "high_risk":
            has_strong = _high_risk_strong_evidence(**evidence_kwargs)
        else:
            has_strong = _strong_trigger_evidence(**evidence_kwargs)

        if has_strong and false_positive_score == 0.0:
            return ApplicabilityResult(
                decision="hot",
                eligible=True,
                reason="active with observed useful + strong trigger evidence",
                trigger_evidence_passed=True,
                penalties=penalties,
            )

        # Fall back to WARM
        return ApplicabilityResult(
            decision="warm",
            eligible=True,
            reason="active: trigger evidence passed, strong evidence insufficient for HOT",
            trigger_evidence_passed=True,
            penalties=penalties,
        )

    # ------------------------------------------------------------------
    # Trusted rules
    # ------------------------------------------------------------------

    if rule_status == "trusted":
        # Gate: requires gate_eligible severity + STRONG prompt evidence + tool evidence
        if rule_severity == "gate_eligible":
            has_strong_for_gate = _strong_trigger_evidence(**evidence_kwargs)
            if has_strong_for_gate:
                if has_tool_input:
                    if tool_evidence_passed:
                        return ApplicabilityResult(
                            decision="gate",
                            eligible=True,
                            reason="trusted + gate_eligible + strong evidence + tool evidence",
                            trigger_evidence_passed=True,
                            penalties=penalties,
                        )
                    # Tool input available but evidence did not pass -> fall to HOT
                else:
                    # Prompt-only gate (no tool input exists yet)
                    return ApplicabilityResult(
                        decision="gate",
                        eligible=True,
                        reason="trusted + gate_eligible + strong evidence + prompt-only",
                        trigger_evidence_passed=True,
                        penalties=penalties,
                    )

        # HOT for trusted: spec says trusted "can WARM/HOT" without strong-evidence requirement
        # (unlike active which requires strong evidence for HOT)
        if false_positive_score > 0.0:
            penalties.append(f"recent_fp_penalty={false_positive_score:.2f}")
        return ApplicabilityResult(
            decision="hot",
            eligible=True,
            reason="trusted: trigger evidence passed -> HOT eligible",
            trigger_evidence_passed=True,
            penalties=penalties,
        )

    # ------------------------------------------------------------------
    # Fallback (should not reach here with valid Status)
    # ------------------------------------------------------------------

    return ApplicabilityResult(
        decision="cold",
        eligible=False,
        reason=f"unhandled status: {rule_status}",
        trigger_evidence_passed=trigger_passed,
        penalties=penalties,
    )
