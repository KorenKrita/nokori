"""Evidence and decision types for lifecycle transitions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvidenceSnapshot:
    """Pre-aggregated evidence for pure policy evaluation.

    All DB reads are performed upfront and packed into this structure,
    enabling policy functions to be tested without a database.
    """

    # Fire evidence (from evidence.gather_fire_evidence)
    observed_useful_strong: int = 0
    observed_useful_total: int = 0
    irrelevant_in_last_5: int = 0
    irrelevant_in_window: int = 0
    harmful_lifetime: int = 0
    false_positive_rate: float = 0.0
    fire_total_evaluated: int = 0
    distinct_strong_useful_sessions: int = 0

    # Shadow evidence (from evidence.gather_shadow_evidence)
    shadow_would_help_high: int = 0
    shadow_would_help_low: int = 0
    shadow_irrelevant: int = 0
    shadow_risky: int = 0
    shadow_near_miss: int = 0
    shadow_distinct_sessions: int = 0
    shadow_evaluated_count: int = 0
    shadow_task_deduped_count: int = 0
    shadow_fp_rate: float = 0.0
    best_single_session_strong: int = 0
    best_single_session_contexts: int = 0

    # Candidate-specific metadata
    synthetic_eval_passed: bool = False
    admission_quality: float = 0.0
    has_miss_evidence: bool = False

    # Common metadata
    has_replacement: bool = False
    rule_version: int = 1  # ponytail: default 1 kept for unit test ergonomics; gather_* always passes explicit value

    # Suppressed-specific
    suppressed_at_missing: bool = False
    suppressed_at_unparseable: bool = False
    ttl_expired: bool = False
    recent_harmful_after_suppression: int = 0

    # Trusted-specific
    project_scope: str | None = None
    distinct_useful_projects: int = 0


@dataclass(frozen=True)
class TransitionDecision:
    """Pure policy decision result, independent of DB."""

    new_status: str | None = None  # None = no transition
    reason: str = ""
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionResult:
    rule_id: str
    old_status: str
    new_status: str | None  # None = no change
    reason: str
    applied: bool

