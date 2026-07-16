"""Promotion barrier gap computation for lifecycle UI/API."""

from __future__ import annotations

from ..db import Db
from ..policy import (
    ACTIVE_TO_TRUSTED,
    CANDIDATE_TO_ACTIVE,
    MINIMUM_RATE_DENOMINATOR,
    RECENT_TIME_WINDOW_DAYS,
    SUPPRESSED_TO_ACTIVE,
)
from .evidence import count_harmful_since, gather_fire_evidence, gather_shadow_evidence


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
    shadow = gather_shadow_evidence(db, rule_id, rule_version)
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
    fire = gather_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)
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
    shadow = gather_shadow_evidence(
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
    recent_harmful = count_harmful_since(db, rule_id, suppressed_at)

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

