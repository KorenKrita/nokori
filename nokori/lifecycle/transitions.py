"""State transition engine for the autonomous rule quality flywheel.

Evaluates evidence and applies policy-driven status transitions using
CAS-style updates to prevent stale-state races.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta

from ..db import Db
from ..events.fire import count_distinct_useful_projects
from ..events.observability import write_event
from ..events.shadow import count_shadow_evidence
from ..policy import (
    ACTIVE_TO_SUPPRESSED,
    ACTIVE_TO_TRUSTED,
    CANDIDATE_TO_ACTIVE,
    CANDIDATE_TO_ACTIVE_SINGLE_SESSION,
    CANDIDATE_TO_ARCHIVED,
    CROSS_PROJECT_PROMOTION_THRESHOLD,
    FALSE_POSITIVE_REASON_CODES,
    MINIMUM_RATE_DENOMINATOR,
    RECENT_EVENT_WINDOW,
    RECENT_TIME_WINDOW_DAYS,
    RUNTIME_POLICY_VERSION,
    SUPPRESSED_TO_ACTIVE,
    SUPPRESSED_TO_ARCHIVED,
    SUPPRESSION_TTL_DAYS,
    TRUSTED_TO_ACTIVE,
    TRUSTED_TO_SUPPRESSED,
    Status,
)
from ..utils.logging import get_logger
from ..utils.time import iso_of, local_now, now_iso, parse_iso

log = get_logger("nokori.lifecycle.transitions")


# ---------------------------------------------------------------------------
# Evidence and decision types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceSnapshot:
    """Pre-aggregated evidence for pure policy evaluation.

    All DB reads are performed upfront and packed into this structure,
    enabling policy functions to be tested without a database.
    """

    # Fire evidence (from _aggregate_fire_evidence)
    observed_useful_strong: int = 0
    observed_useful_total: int = 0
    irrelevant_in_last_5: int = 0
    irrelevant_in_window: int = 0
    harmful_lifetime: int = 0
    false_positive_rate: float = 0.0
    fire_total_evaluated: int = 0
    distinct_strong_useful_sessions: int = 0

    # Shadow evidence (from _aggregate_shadow_evidence)
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


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def evaluate_transitions(db: Db, rule_id: str) -> TransitionResult:
    """Main entry point. Reads rule state, aggregates events, applies policy."""
    row = db.fetchone(
        "SELECT id, rule_version, status, runtime_policy_version, "
        "suppressed_at, replacement_id, project_scope "
        "FROM rules WHERE id = ?",
        (rule_id,),
    )
    if row is None:
        return TransitionResult(
            rule_id=rule_id,
            old_status="",
            new_status=None,
            reason="rule not found",
            applied=False,
        )

    status: Status = row["status"]
    rule_version: int = row["rule_version"]
    rpv = row["runtime_policy_version"]

    if status == "candidate":
        evidence = _gather_candidate_evidence(db, row, rule_version)
        decision = _evaluate_candidate(evidence)
        return _apply_decision(db, rule_id, rule_version, status, rpv, decision)
    if status == "active":
        evidence = _gather_active_evidence(db, row, rule_version)
        decision = _evaluate_active(evidence)
        return _apply_decision(db, rule_id, rule_version, status, rpv, decision)
    if status == "trusted":
        evidence = _gather_trusted_evidence(db, row, rule_version)
        decision = _evaluate_trusted(evidence)
        if decision.reason == "cross_project_promotion":
            return _apply_cross_project_promotion(
                db, rule_id, rule_version, status,
                distinct_count=decision.metadata.get("distinct_project_count"),
            )
        return _apply_decision(db, rule_id, rule_version, status, rpv, decision)
    if status == "suppressed":
        evidence = _gather_suppressed_evidence(db, row, rule_version)
        decision = _evaluate_suppressed(evidence)
        return _apply_decision(db, rule_id, rule_version, status, rpv, decision)

    # archived rules do not transition
    return TransitionResult(
        rule_id=rule_id,
        old_status=status,
        new_status=None,
        reason="terminal state",
        applied=False,
    )


def compute_promotion_barriers(
    db: Db, rule_id: str, status: str, rule_version: int, suppressed_at: str | None = None
) -> dict | None:
    """Return structured threshold gaps for the rule's next promotion.

    Returns None if no valid forward promotion exists (trusted, archived, etc.).
    """
    if status == "candidate":
        return _barriers_candidate_to_active(db, rule_id, rule_version)
    if status == "active":
        return _barriers_active_to_trusted(db, rule_id)
    if status == "suppressed":
        return _barriers_suppressed_to_active(db, rule_id, rule_version, suppressed_at)
    return None


def _barriers_candidate_to_active(db: Db, rule_id: str, rule_version: int) -> dict:
    shadow = _aggregate_shadow_evidence(db, rule_id, rule_version)
    th = CANDIDATE_TO_ACTIVE

    strong_count = shadow.get("would_help_high", 0)
    evaluated_count = (
        shadow.get("would_help_high", 0)
        + shadow.get("would_help_low", 0)
        + shadow.get("irrelevant", 0)
        + shadow.get("risky", 0)
        + shadow.get("near_miss", 0)
    )
    task_deduped_count = shadow.get("task_deduped_count", evaluated_count)
    distinct_sessions = shadow.get("distinct_sessions", 0)
    risky_harmful = shadow.get("risky", 0) + shadow.get("near_miss", 0)
    shadow_fp_numerator = shadow.get("irrelevant", 0) + shadow.get("near_miss", 0)
    shadow_fp_rate = shadow_fp_numerator / task_deduped_count if task_deduped_count > 0 else 0.0

    thresholds = [
        {
            "name": "shadow_strong_match_count",
            "current": strong_count,
            "target": th.shadow_strong_match_count_min,
            "met": strong_count >= th.shadow_strong_match_count_min,
            "direction": "min",
        },
        {
            "name": "evaluated_shadow_match_count",
            "current": evaluated_count,
            "target": th.evaluated_shadow_match_count_min,
            "met": evaluated_count >= th.evaluated_shadow_match_count_min,
            "direction": "min",
        },
        {
            "name": "distinct_shadow_sessions",
            "current": distinct_sessions,
            "target": th.distinct_shadow_sessions_min,
            "met": distinct_sessions >= th.distinct_shadow_sessions_min,
            "direction": "min",
        },
        {
            "name": "counterfactual_would_help_high",
            "current": strong_count,
            "target": th.counterfactual_would_help_high_min,
            "met": strong_count >= th.counterfactual_would_help_high_min,
            "direction": "min",
        },
        {
            "name": "risky_or_near_miss_shadow_count",
            "current": risky_harmful,
            "target": th.risky_or_near_miss_shadow_count_max,
            "met": risky_harmful <= th.risky_or_near_miss_shadow_count_max,
            "direction": "max",
        },
        {
            "name": "shadow_false_positive_rate",
            "current": round(shadow_fp_rate, 4),
            "target": th.shadow_false_positive_rate_max,
            "met": shadow_fp_rate <= th.shadow_false_positive_rate_max,
            "direction": "max",
        },
    ]

    blocking = next((t["name"] for t in thresholds if not t["met"]), None)
    return {
        "current_state": "candidate",
        "target_state": "active",
        "thresholds": thresholds,
        "blocking": blocking,
    }


def _barriers_active_to_trusted(db: Db, rule_id: str) -> dict:
    fire = _aggregate_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)
    th = ACTIVE_TO_TRUSTED

    observed_useful = fire.get("observed_useful_strong", 0)
    total_evaluated = fire.get("total_evaluated", 0)
    distinct_sessions = fire.get("distinct_strong_useful_sessions", 0)
    lifetime_harmful = fire.get("lifetime_harmful", 0)
    fp_rate = fire.get("false_positive_rate", 0.0)

    thresholds = [
        {
            "name": "observed_useful_count",
            "current": observed_useful,
            "target": th.observed_useful_count_min,
            "met": observed_useful >= th.observed_useful_count_min,
            "direction": "min",
        },
        {
            "name": "evaluated_fire_count",
            "current": total_evaluated,
            "target": max(th.evaluated_fire_count_min, MINIMUM_RATE_DENOMINATOR),
            "met": total_evaluated >= th.evaluated_fire_count_min
            and total_evaluated >= MINIMUM_RATE_DENOMINATOR,
            "direction": "min",
        },
        {
            "name": "distinct_observed_useful_sessions",
            "current": distinct_sessions,
            "target": th.distinct_observed_useful_sessions_min,
            "met": distinct_sessions >= th.distinct_observed_useful_sessions_min,
            "direction": "min",
        },
        {
            "name": "harmful_count",
            "current": lifetime_harmful,
            "target": th.harmful_count_max,
            "met": lifetime_harmful <= th.harmful_count_max,
            "direction": "max",
        },
        {
            "name": "recent_false_positive_rate",
            "current": round(fp_rate, 4),
            "target": th.recent_false_positive_rate_max,
            "met": fp_rate <= th.recent_false_positive_rate_max,
            "direction": "max",
        },
    ]

    blocking = next((t["name"] for t in thresholds if not t["met"]), None)
    return {
        "current_state": "active",
        "target_state": "trusted",
        "thresholds": thresholds,
        "blocking": blocking,
    }


def _barriers_suppressed_to_active(
    db: Db, rule_id: str, rule_version: int, suppressed_at: str | None
) -> dict | None:
    if suppressed_at is None:
        return None
    shadow = _aggregate_shadow_evidence(
        db,
        rule_id,
        rule_version,
        since_iso=suppressed_at,
        shadow_type="suppression_recovery",
    )
    th = SUPPRESSED_TO_ACTIVE

    would_help_high = shadow.get("would_help_high", 0)
    distinct_sessions = shadow.get("distinct_sessions", 0)

    # Recent harmful from fire events after suppression
    harmful_row = db.fetchone(
        "SELECT COUNT(*) AS n FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label = 'harmful' AND created_at >= ?",
        (rule_id, suppressed_at),
    )
    recent_harmful = harmful_row["n"] if harmful_row else 0

    thresholds = [
        {
            "name": "shadow_recovery_would_help_high",
            "current": would_help_high,
            "target": th.shadow_recovery_would_help_high_min,
            "met": would_help_high >= th.shadow_recovery_would_help_high_min,
            "direction": "min",
        },
        {
            "name": "distinct_recovery_sessions",
            "current": distinct_sessions,
            "target": th.distinct_recovery_sessions_min,
            "met": distinct_sessions >= th.distinct_recovery_sessions_min,
            "direction": "min",
        },
        {
            "name": "recent_harmful_count",
            "current": recent_harmful,
            "target": th.recent_harmful_count_max,
            "met": recent_harmful <= th.recent_harmful_count_max,
            "direction": "max",
        },
    ]

    blocking = next((t["name"] for t in thresholds if not t["met"]), None)
    return {
        "current_state": "suppressed",
        "target_state": "active",
        "thresholds": thresholds,
        "blocking": blocking,
    }


def run_all_pending_transitions(db: Db) -> list[TransitionResult]:
    """Iterate rules by status, evaluate each, return results."""
    from ..db import fetch_rule_ids

    results: list[TransitionResult] = []
    rule_ids = fetch_rule_ids(db, statuses=("candidate", "active", "trusted", "suppressed"))
    for rule_id in rule_ids:
        try:
            result = evaluate_transitions(db, rule_id)
        except Exception as exc:
            log.exception("evaluate_transitions failed for rule=%s: %s", rule_id, exc)
            continue
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Evidence gathering (centralizes all DB reads for each status)
# ---------------------------------------------------------------------------


def _gather_candidate_evidence(db: Db, row, rule_version: int) -> EvidenceSnapshot:
    """Gather all evidence needed for candidate evaluation."""
    rule_id = row["id"]

    shadow = _aggregate_shadow_evidence(db, rule_id, rule_version)

    # Compute shadow-derived fields
    strong_count = shadow.get("would_help_high", 0)
    shadow_irrelevant = shadow.get("irrelevant", 0)
    shadow_risky = shadow.get("risky", 0)
    shadow_near_miss = shadow.get("near_miss", 0)
    shadow_weak = shadow.get("would_help_low", 0)
    evaluated_count = strong_count + shadow_weak + shadow_irrelevant + shadow_risky + shadow_near_miss
    task_deduped_count = shadow.get("task_deduped_count", evaluated_count)
    shadow_fp_numerator = shadow_irrelevant + shadow_near_miss
    shadow_fp_rate = (
        shadow_fp_numerator / max(1, task_deduped_count) if task_deduped_count > 0 else 0.0
    )

    # Check synthetic eval status
    synth_row = db.fetchone(
        "SELECT passed FROM rule_synthetic_evals "
        "WHERE rule_id = ? AND rule_version = ? ORDER BY created_at DESC LIMIT 1",
        (rule_id, rule_version),
    )
    synthetic_eval_passed = synth_row is not None and synth_row["passed"] == 1

    # Get admission quality from rule_reviews
    quality_row = db.fetchone(
        "SELECT scores FROM rule_reviews "
        "WHERE rule_id = ? AND decision = 'accept_active' "
        "ORDER BY created_at DESC LIMIT 1",
        (rule_id,),
    )
    admission_quality = 0.0
    if quality_row and quality_row["scores"]:
        try:
            scores = json.loads(quality_row["scores"])
            admission_quality = scores.get("overall_quality", 0.0)
        except (json.JSONDecodeError, TypeError):
            pass

    # Check miss evidence
    has_miss_evidence = _candidate_has_miss_evidence(db, rule_id, rule_version)

    return EvidenceSnapshot(
        shadow_would_help_high=strong_count,
        shadow_would_help_low=shadow_weak,
        shadow_irrelevant=shadow_irrelevant,
        shadow_risky=shadow_risky,
        shadow_near_miss=shadow_near_miss,
        shadow_distinct_sessions=shadow.get("distinct_sessions", 0),
        shadow_evaluated_count=evaluated_count,
        shadow_task_deduped_count=task_deduped_count,
        shadow_fp_rate=shadow_fp_rate,
        best_single_session_strong=shadow.get("best_single_session_strong", 0),
        best_single_session_contexts=shadow.get("best_single_session_contexts", 0),
        synthetic_eval_passed=synthetic_eval_passed,
        admission_quality=admission_quality,
        has_miss_evidence=has_miss_evidence,
        has_replacement=row["replacement_id"] is not None,
        rule_version=rule_version,
    )


def _gather_active_evidence(db: Db, row, rule_version: int) -> EvidenceSnapshot:
    """Gather all evidence needed for active evaluation."""
    rule_id = row["id"]
    fire = _aggregate_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)

    return EvidenceSnapshot(
        observed_useful_strong=fire.get("observed_useful_strong", 0),
        observed_useful_total=fire.get("observed_useful", 0),
        irrelevant_in_last_5=fire.get("irrelevant_in_last_5", 0),
        irrelevant_in_window=fire.get("irrelevant", 0),
        harmful_lifetime=fire.get("lifetime_harmful", 0),
        false_positive_rate=fire.get("false_positive_rate", 0.0),
        fire_total_evaluated=fire.get("total_evaluated", 0),
        distinct_strong_useful_sessions=fire.get("distinct_strong_useful_sessions", 0),
        rule_version=rule_version,
    )


def _gather_trusted_evidence(db: Db, row, rule_version: int) -> EvidenceSnapshot:
    """Gather all evidence needed for trusted evaluation."""
    rule_id = row["id"]
    fire = _aggregate_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)

    # Cross-project promotion check
    distinct_projects = 0
    if row["project_scope"] == "project":
        distinct_projects = count_distinct_useful_projects(db, rule_id)

    return EvidenceSnapshot(
        observed_useful_strong=fire.get("observed_useful_strong", 0),
        observed_useful_total=fire.get("observed_useful", 0),
        irrelevant_in_last_5=fire.get("irrelevant_in_last_5", 0),
        irrelevant_in_window=fire.get("irrelevant", 0),
        harmful_lifetime=fire.get("lifetime_harmful", 0),
        false_positive_rate=fire.get("false_positive_rate", 0.0),
        fire_total_evaluated=fire.get("total_evaluated", 0),
        distinct_strong_useful_sessions=fire.get("distinct_strong_useful_sessions", 0),
        project_scope=row["project_scope"],
        distinct_useful_projects=distinct_projects,
        rule_version=rule_version,
    )


def _gather_suppressed_evidence(db: Db, row, rule_version: int) -> EvidenceSnapshot:
    """Gather all evidence needed for suppressed evaluation."""
    rule_id = row["id"]
    suppressed_at_iso = row["suppressed_at"]

    # Guard: if suppressed_at is NULL, return minimal evidence
    if suppressed_at_iso is None:
        return EvidenceSnapshot(
            rule_version=rule_version,
            suppressed_at_missing=True,
        )

    shadow = _aggregate_shadow_evidence(
        db,
        rule_id,
        rule_version,
        since_iso=suppressed_at_iso,
        shadow_type="suppression_recovery",
    )

    # Compute TTL
    suppressed_at = parse_iso(suppressed_at_iso)
    if suppressed_at is None:
        # Unparseable suppressed_at
        return EvidenceSnapshot(
            rule_version=rule_version,
            suppressed_at_unparseable=True,
        )

    ttl_deadline = suppressed_at + timedelta(days=SUPPRESSION_TTL_DAYS)
    ttl_expired = local_now() > ttl_deadline

    # Recent harmful from fire events after suppression
    recent_harmful = 0
    harmful_row = db.fetchone(
        "SELECT COUNT(*) AS n FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label = 'harmful' AND created_at >= ?",
        (rule_id, suppressed_at_iso),
    )
    if harmful_row:
        recent_harmful = harmful_row["n"]

    return EvidenceSnapshot(
        shadow_would_help_high=shadow.get("would_help_high", 0),
        shadow_would_help_low=shadow.get("would_help_low", 0),
        shadow_irrelevant=shadow.get("irrelevant", 0),
        shadow_risky=shadow.get("risky", 0),
        shadow_near_miss=shadow.get("near_miss", 0),
        shadow_distinct_sessions=shadow.get("distinct_sessions", 0),
        has_replacement=row["replacement_id"] is not None,
        ttl_expired=ttl_expired,
        recent_harmful_after_suppression=recent_harmful,
        rule_version=rule_version,
    )


# ---------------------------------------------------------------------------
# Evidence aggregation (DB access layer)
# ---------------------------------------------------------------------------


def _aggregate_fire_evidence(db: Db, rule_id: str, window_days: int = 30) -> dict:
    """Count fire event labels using BOTH count window (last 10) AND time window (30 days) per spec 3.4.

    Uses BOTH count window (last 10) AND time window (30 days) per spec 3.4.
    Harmful events are counted lifetime — they do NOT decay by time alone.
    """
    cutoff = _days_ago_iso(window_days)
    recent_rows = db.fetchall(
        "SELECT posthoc_label, posthoc_reason_code, posthoc_score, session_id "
        "FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label IS NOT NULL AND posthoc_label != 'unclear' "
        "AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT ?",
        (rule_id, cutoff, RECENT_EVENT_WINDOW),
    )

    counts: dict[str, int | float] = {
        "observed_useful": 0,
        "observed_useful_strong": 0,
        "plausible_useful": 0,
        "irrelevant": 0,
        "harmful": 0,
        "unclear": 0,
        "total_evaluated": len(recent_rows),
    }
    reason_counts: dict[str, int] = {}
    useful_sessions: set[str] = set()
    strong_useful_sessions: set[str] = set()

    for r in recent_rows:
        label = r["posthoc_label"]
        if label in counts and label != "total_evaluated":
            counts[label] = int(counts[label]) + 1
        rc = r["posthoc_reason_code"]
        if rc:
            reason_counts[rc] = reason_counts.get(rc, 0) + 1
        if label == "observed_useful" and r["session_id"]:
            useful_sessions.add(r["session_id"])
            # Spec 10.2: only strong-attribution events count toward trusted promotion.
            # NULL = legacy event (pre-attribution system) -> treated as strong for compat.
            # > 0.5 = new system confirmed strong causal attribution.
            # <= 0.5 = weak/redundant attribution -> excluded from promotion count.
            attribution_weight = r["posthoc_score"]
            if attribution_weight is None or attribution_weight > 0.5:
                counts["observed_useful_strong"] = int(counts["observed_useful_strong"]) + 1
                strong_useful_sessions.add(r["session_id"])

    counts["reason_counts"] = reason_counts
    counts["distinct_observed_useful_sessions"] = len(useful_sessions)
    counts["distinct_strong_useful_sessions"] = len(strong_useful_sessions)
    counts["false_positive_rate"] = compute_false_positive_rate(counts)

    # Irrelevant in last 5 evaluated fire events
    counts["irrelevant_in_last_5"] = sum(
        1 for r in recent_rows[:5] if r["posthoc_label"] == "irrelevant"
    )

    # CRITICAL: harmful events do NOT decay by time (section 3.4).
    # Count ALL lifetime harmful events for suppression decisions.
    lifetime_harmful_row = db.fetchone(
        "SELECT COUNT(*) AS n FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label = 'harmful'",
        (rule_id,),
    )
    counts["lifetime_harmful"] = lifetime_harmful_row["n"] if lifetime_harmful_row else 0

    return counts


def _aggregate_shadow_evidence(
    db: Db,
    rule_id: str,
    rule_version: int,
    window_days: int = 30,
    shadow_type: str | None = None,
    since_iso: str | None = None,
) -> dict:
    """Count shadow labels with fingerprint dedup, distinct sessions."""
    return count_shadow_evidence(
        db,
        rule_id,
        rule_version,
        window_days=window_days,
        shadow_type=shadow_type,
        since_iso=since_iso,
    )


# ---------------------------------------------------------------------------
# False-positive rate computation
# ---------------------------------------------------------------------------


def compute_false_positive_rate(events: dict) -> float:
    """Compute FP rate from event counts.

    fp_events = irrelevant_not_applicable + harmful_wrong_scope
                + harmful_blocked_valid_action + harmful_distracted
    denominator = total_evaluated (unclear already excluded by query)
    """
    reason_counts = events.get("reason_counts", {})
    fp_events = sum(reason_counts.get(code, 0) for code in FALSE_POSITIVE_REASON_CODES)

    total_evaluated = events.get("total_evaluated", 0)
    denominator = total_evaluated

    return fp_events / max(1, denominator)


# ---------------------------------------------------------------------------
# Derived score updates
# ---------------------------------------------------------------------------


def update_derived_scores(db: Db, rule_id: str) -> None:
    """Recompute derived scores from event counts and persist."""
    fire = _aggregate_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)

    total = fire.get("total_evaluated", 0)
    denom = max(1, total)

    observed_usefulness_score = fire.get("observed_useful", 0) / denom
    plausible_usefulness_score = fire.get("plausible_useful", 0) / denom
    false_positive_score = fire.get("false_positive_rate", 0.0)
    harmful_score = fire.get("harmful", 0) / denom

    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET "
            "observed_usefulness_score = ?, "
            "plausible_usefulness_score = ?, "
            "false_positive_score = ?, "
            "harmful_score = ?, "
            "updated_at = ? "
            "WHERE id = ?",
            (
                observed_usefulness_score,
                plausible_usefulness_score,
                false_positive_score,
                harmful_score,
                now,
                rule_id,
            ),
        )


# ---------------------------------------------------------------------------
# Pure policy evaluation functions (no DB access)
# ---------------------------------------------------------------------------


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
    if evidence.project_scope == "project":
        if evidence.distinct_useful_projects >= CROSS_PROJECT_PROMOTION_THRESHOLD:
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


# ---------------------------------------------------------------------------
# Decision application (DB mutation layer)
# ---------------------------------------------------------------------------


def _apply_decision(
    db: Db,
    rule_id: str,
    rule_version: int,
    old_status: str,
    rpv: str | None,
    decision: TransitionDecision,
) -> TransitionResult:
    """Apply a TransitionDecision by calling _apply_transition if needed."""
    if decision.new_status is None:
        return TransitionResult(
            rule_id=rule_id,
            old_status=old_status,
            new_status=None,
            reason=decision.reason,
            applied=False,
        )

    applied = _apply_transition(
        db, rule_id, rule_version, old_status, decision.new_status, rpv, decision.reason
    )
    return TransitionResult(
        rule_id=rule_id,
        old_status=old_status,
        new_status=decision.new_status,
        reason=decision.reason,
        applied=applied,
    )


def _apply_cross_project_promotion(
    db: Db, rule_id: str, rule_version: int, old_status: str,
    *, distinct_count: int | None = None,
) -> TransitionResult:
    """Apply cross-project scope promotion (not a status change)."""
    now = now_iso()
    with db.transaction() as tx:
        cur = tx.execute(
            "UPDATE rules SET project_scope = 'global', "
            "rule_version = rule_version + 1, updated_at = ? "
            "WHERE id = ? AND rule_version = ? AND project_scope = 'project'",
            (now, rule_id, rule_version),
        )
        applied = cur.rowcount == 1
    if applied:
        log.info(
            "cross_project_promotion rule=%s",
            rule_id,
        )
        details: dict = {
            "rule_id": rule_id,
            "transition_type": "cross_project_promotion",
        }
        if distinct_count is not None:
            details["distinct_project_count"] = distinct_count
        write_event(
            db,
            source="lifecycle_transition",
            outcome="cross_project_promotion",
            details=details,
        )
        return TransitionResult(
            rule_id, old_status, None, "cross_project_promotion", True
        )
    log.debug("stale cross_project_promotion rule=%s (CAS failed)", rule_id)
    return TransitionResult(
        rule_id, old_status, None, "cross_project_promotion_conflict", False
    )


# ---------------------------------------------------------------------------
# CAS-style transition application
# ---------------------------------------------------------------------------


def _apply_transition(
    db: Db,
    rule_id: str,
    rule_version: int,
    old_status: str,
    new_status: str,
    runtime_policy_version: str | None,
    reason: str,
) -> bool:
    """CAS update: only succeeds if rule_version and status match expectations.

    Returns True if applied (rowcount=1), False if stale.
    """
    now = now_iso()
    extra_sets = ""
    extra_params: list = []

    if new_status == "suppressed":
        extra_sets = ", suppressed_at = ?"
        extra_params.append(now)
    elif new_status == "trusted":
        extra_sets = ", trusted_at = ?"
        extra_params.append(now)

    if runtime_policy_version is None:
        policy_where = "runtime_policy_version IS NULL"
        policy_params: tuple = ()
    else:
        policy_where = "runtime_policy_version = ?"
        policy_params = (runtime_policy_version,)

    with db.transaction() as tx:
        cur = tx.execute(
            "UPDATE rules SET "
            "status = ?, "
            "rule_version = rule_version + 1, "
            "runtime_policy_version = ?, "
            "updated_at = ?"
            f"{extra_sets} "
            "WHERE id = ? AND rule_version = ? AND status = ? "
            f"AND {policy_where}",
            (
                new_status,
                RUNTIME_POLICY_VERSION,
                now,
                *extra_params,
                rule_id,
                rule_version,
                old_status,
                *policy_params,
            ),
        )
        applied = cur.rowcount == 1

    if applied:
        log.info(
            "transition rule=%s %s->%s reason=%s",
            rule_id,
            old_status,
            new_status,
            reason,
        )
        write_event(
            db,
            source="lifecycle_transition",
            outcome=f"{old_status}_to_{new_status}",
            details={
                "rule_id": rule_id,
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
            },
        )
        # System-automated archival creates system-strength negative fingerprint (spec section 11)
        if new_status == "archived":
            _create_system_archive_fingerprint(db, rule_id)
    else:
        log.debug(
            "stale transition rule=%s %s->%s (CAS failed)",
            rule_id,
            old_status,
            new_status,
        )

    return applied


def _create_system_archive_fingerprint(db: Db, rule_id: str) -> None:
    """Create a system-strength archived fingerprint for automated archival."""
    row = db.fetchone(
        "SELECT trigger_canonical, action_instruction, domain_tags FROM rules WHERE id = ?",
        (rule_id,),
    )
    if row is None:
        return
    from ..archive.fingerprints import create_archived_fingerprint_from_data
    from ..db import loads_json

    domain_tags = loads_json(row["domain_tags"], []) if row["domain_tags"] else []
    create_archived_fingerprint_from_data(
        db,
        rule_id=rule_id,
        trigger_canonical=row["trigger_canonical"] or "",
        action_instruction=row["action_instruction"] or "",
        domain_tags=domain_tags,
        strength="system",
    )


# ---------------------------------------------------------------------------
# Helper: miss evidence check for candidate single-session exception
# ---------------------------------------------------------------------------


def _candidate_has_miss_evidence(db: Db, rule_id: str, rule_version: int) -> bool:
    """Check shadow-only observed miss/user correction evidence for a candidate."""
    rows = db.fetchall(
        "SELECT decision_features FROM rule_shadow_events "
        "WHERE rule_id = ? AND shadow_rule_version = ? "
        "AND shadow_label = 'would_help_high'",
        (rule_id, rule_version),
    )
    for row in rows:
        try:
            features = json.loads(row["decision_features"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(features, dict):
            continue
        if features.get("observed_agent_miss") is True:
            return True
        if features.get("user_correction") is True:
            return True
        if features.get("source") in {"agent_miss", "user_correction"}:
            return True
        evidence = features.get("evidence")
        if isinstance(evidence, list) and (
            "agent_miss" in evidence or "user_correction" in evidence
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _days_ago_iso(days: int) -> str:
    return iso_of(local_now() - timedelta(days=days))
