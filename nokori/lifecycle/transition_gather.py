"""Evidence gathering helpers for lifecycle transitions."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from ..db import Db
from ..events.fire import count_distinct_useful_projects
from ..policy import RECENT_TIME_WINDOW_DAYS, SUPPRESSION_TTL_DAYS
from ..utils.time import local_now, now_iso, parse_iso
from .evidence import (
    count_harmful_since,
    gather_candidate_extras,
    gather_fire_evidence,
    gather_shadow_evidence,
)
from .transition_types import EvidenceSnapshot


def _gather_candidate_evidence(db: Db, row: sqlite3.Row | dict, rule_version: int) -> EvidenceSnapshot:
    """Gather all evidence needed for candidate evaluation."""
    rule_id = row["id"]

    shadow = gather_shadow_evidence(db, rule_id, rule_version)

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

    extras = gather_candidate_extras(db, rule_id, rule_version)

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
        synthetic_eval_passed=extras["synthetic_eval_passed"],
        admission_quality=extras["admission_quality"],
        has_miss_evidence=extras["has_miss_evidence"],
        has_replacement=row["replacement_id"] is not None,
        rule_version=rule_version,
    )


def _gather_active_evidence(db: Db, row: sqlite3.Row | dict, rule_version: int) -> EvidenceSnapshot:
    """Gather all evidence needed for active evaluation."""
    rule_id = row["id"]
    fire = gather_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)

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


def _gather_trusted_evidence(db: Db, row: sqlite3.Row | dict, rule_version: int) -> EvidenceSnapshot:
    """Gather all evidence needed for trusted evaluation."""
    rule_id = row["id"]
    fire = gather_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)

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


def _gather_suppressed_evidence(db: Db, row: sqlite3.Row | dict, rule_version: int) -> EvidenceSnapshot:
    """Gather all evidence needed for suppressed evaluation."""
    rule_id = row["id"]
    suppressed_at_iso = row["suppressed_at"]

    # Guard: if suppressed_at is NULL, return minimal evidence
    if suppressed_at_iso is None:
        return EvidenceSnapshot(
            rule_version=rule_version,
            suppressed_at_missing=True,
        )

    shadow = gather_shadow_evidence(
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
    recent_harmful = count_harmful_since(db, rule_id, suppressed_at_iso)

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


def update_derived_scores(db: Db, rule_id: str) -> None:
    """Recompute derived scores from event counts and persist."""
    fire = gather_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)

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

