"""Shared policy constants for the autonomous rule quality flywheel.

This module is the lowest-level module in the flywheel design. It has no
imports from other nokori modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Version strings
# ---------------------------------------------------------------------------

RUNTIME_POLICY_VERSION: str = "1.0.0"

# ---------------------------------------------------------------------------
# Status literals (section 3.1)
# ---------------------------------------------------------------------------

Status = Literal["candidate", "active", "trusted", "suppressed", "archived"]


# ---------------------------------------------------------------------------
# Severity literals (section 4)
# ---------------------------------------------------------------------------

Severity = Literal["reminder", "high_risk", "gate_eligible"]

# ---------------------------------------------------------------------------
# Source origin literals (section 12)
# ---------------------------------------------------------------------------

SourceOrigin = Literal["transcript_extraction", "external_source_material"]

# ---------------------------------------------------------------------------
# Activation origin literals (section 12)
# ---------------------------------------------------------------------------

ActivationOrigin = Literal[
    "cold_fast_lane",
    "shadow_promotion",
    "merge_replacement",
    "external_shadow_promotion",
]

# ---------------------------------------------------------------------------
# Posthoc label literals (section 10.2)
# ---------------------------------------------------------------------------

PosthocLabel = Literal[
    "observed_useful",
    "plausible_useful",
    "irrelevant",
    "harmful",
    "unclear",
]

# ---------------------------------------------------------------------------
# Posthoc reason code literals (section 10.2)
# ---------------------------------------------------------------------------

PosthocReasonCode = Literal[
    "useful_prevented_error",
    "useful_improved_quality",
    "useful_followed_preference",
    "irrelevant_not_applicable",
    "irrelevant_redundant",
    "irrelevant_unused",
    "harmful_distracted",
    "harmful_wrong_scope",
    "harmful_blocked_valid_action",
]

# ---------------------------------------------------------------------------
# Merge operation literals (section 8.2 / 8.4)
# ---------------------------------------------------------------------------

MergeOperation = Literal[
    "merge_into_existing",
    "update_existing_fields",
    "replace_existing",
    "keep_both",
    "reject_new",
    "suppress_existing",
    "archive_existing",
    "split_required",
]

# ---------------------------------------------------------------------------
# False-positive event classification (section 3.4)
# ---------------------------------------------------------------------------

FALSE_POSITIVE_REASON_CODES: frozenset[PosthocReasonCode] = frozenset(
    (
        "irrelevant_not_applicable",
        "harmful_wrong_scope",
        "harmful_blocked_valid_action",
        "harmful_distracted",
    )
)

EVALUATED_LABELS: frozenset[PosthocLabel] = frozenset(
    (
        "observed_useful",
        "plausible_useful",
        "irrelevant",
        "harmful",
    )
)

# ---------------------------------------------------------------------------
# State transition thresholds (section 3.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColdFastLaneThresholds:
    admission_overall_quality_min: float = 0.90
    evidence_support_min: float = 0.90
    trigger_specificity_min: float = 0.85
    scope_control_min: float = 0.85
    action_clarity_min: float = 0.80
    generalization_safety_min: float = 0.75
    synthetic_eval_passed: bool = True
    global_adversarial_failures_max: int = 0
    archived_fingerprint_conflict: bool = False
    final_judge_decision: str = "accept_active"
    merge_operation_must_not_require: tuple[MergeOperation, ...] = ("split_required",)


@dataclass(frozen=True)
class CandidateToActiveThresholds:
    synthetic_eval_passed: bool = True
    shadow_strong_match_count_min: int = 3
    evaluated_shadow_match_count_min: int = 5
    distinct_shadow_sessions_min: int = 2
    counterfactual_would_help_high_min: int = 2
    risky_or_near_miss_shadow_count_max: int = 0
    shadow_false_positive_rate_max: float = 0.0


@dataclass(frozen=True)
class CandidateToActiveSingleSessionThresholds:
    synthetic_eval_passed: bool = True
    admission_overall_quality_min: float = 0.88
    shadow_strong_match_count_min: int = 3
    evaluated_shadow_match_count_min: int = 5
    counterfactual_would_help_high_min: int = 3
    observed_agent_miss_or_user_correction: bool = True
    risky_or_near_miss_shadow_count_max: int = 0
    shadow_false_positive_rate_max: float = 0.0


@dataclass(frozen=True)
class CandidateToArchivedThresholds:
    risky_or_harmful_shadow_count_min: int = 2
    irrelevant_shadow_count_min: int = 5
    covered_by_replacement: bool = True


@dataclass(frozen=True)
class ActiveToTrustedThresholds:
    observed_useful_count_min: int = 3
    evaluated_fire_count_min: int = 5
    distinct_observed_useful_sessions_min: int = 2
    harmful_count_max: int = 0
    recent_false_positive_rate_max: float = 0.15


@dataclass(frozen=True)
class ActiveToSuppressedThresholds:
    harmful_count_min: int = 1
    irrelevant_count_in_last_5_min: int = 3
    recent_false_positive_rate_min: float = 0.50


@dataclass(frozen=True)
class TrustedToActiveThresholds:
    evaluated_fire_count_in_recent_window_min: int = 5
    observed_useful_count_in_recent_window_max: int = 0
    irrelevant_count_in_recent_window_min: int = 2
    harmful_count_max: int = 0
    recent_false_positive_rate_min: float = 0.30


@dataclass(frozen=True)
class TrustedToSuppressedThresholds:
    harmful_count_min: int = 1
    irrelevant_count_in_last_5_min: int = 3
    recent_false_positive_rate_min: float = 0.35


@dataclass(frozen=True)
class SuppressedToActiveThresholds:
    shadow_recovery_would_help_high_min: int = 3
    distinct_recovery_sessions_min: int = 2
    recent_harmful_count_max: int = 0


@dataclass(frozen=True)
class SuppressedToArchivedThresholds:
    risky_or_harmful_shadow_count_after_suppression_min: int = 2
    covered_by_replacement: bool = True


COLD_FAST_LANE = ColdFastLaneThresholds()
CANDIDATE_TO_ACTIVE = CandidateToActiveThresholds()
CANDIDATE_TO_ACTIVE_SINGLE_SESSION = CandidateToActiveSingleSessionThresholds()
CANDIDATE_TO_ARCHIVED = CandidateToArchivedThresholds()
ACTIVE_TO_TRUSTED = ActiveToTrustedThresholds()
ACTIVE_TO_SUPPRESSED = ActiveToSuppressedThresholds()
TRUSTED_TO_ACTIVE = TrustedToActiveThresholds()
TRUSTED_TO_SUPPRESSED = TrustedToSuppressedThresholds()
SUPPRESSED_TO_ACTIVE = SuppressedToActiveThresholds()
SUPPRESSED_TO_ARCHIVED = SuppressedToArchivedThresholds()


# ---------------------------------------------------------------------------
# Score windows (section 3.4)
# ---------------------------------------------------------------------------

RECENT_EVENT_WINDOW: int = 10
SHADOW_EVENT_WINDOW: int = 10
RECENT_TIME_WINDOW_DAYS: int = 30
MINIMUM_RATE_DENOMINATOR: int = 5
SUPPRESSION_TTL_DAYS: int = 30

# ---------------------------------------------------------------------------
# Runtime constants (section 9)
# ---------------------------------------------------------------------------

CROSS_PROJECT_PROMOTION_THRESHOLD: int = 3

WARM_HARD_MAX: int = 3
HOT_MAX_DEFAULT: int = 1

SMALL_POOL_THRESHOLD: int = 20
IDF_MAX_SHADOW: float = 3.0
SAFETY_MARGIN_COSINE: float = 0.02


@dataclass(frozen=True)
class DynamicIDFPolicy:
    absolute_trigger_info_min: float
    trigger_coverage_min: float
    distinct_trigger_terms_min: int


DYNAMIC_IDF_SMALL_POOL = DynamicIDFPolicy(
    absolute_trigger_info_min=2.40,
    trigger_coverage_min=0.40,
    distinct_trigger_terms_min=2,
)

DYNAMIC_IDF_NORMAL = DynamicIDFPolicy(
    absolute_trigger_info_min=1.20,
    trigger_coverage_min=0.25,
    distinct_trigger_terms_min=1,
)

# ---------------------------------------------------------------------------
# CAS fields for lifecycle transitions (section 13)
# ---------------------------------------------------------------------------

CAS_FIELDS: tuple[str, ...] = (
    "rule_id",
    "rule_version",
    "status",
    "runtime_policy_version",
)
