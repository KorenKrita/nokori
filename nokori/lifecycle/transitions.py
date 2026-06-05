"""State transition engine for the autonomous rule quality flywheel.

Evaluates evidence and applies policy-driven status transitions using
CAS-style updates to prevent stale-state races.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..db import Db
from ..events.shadow import count_shadow_evidence
from ..policy import (
    ACTIVE_TO_SUPPRESSED,
    ACTIVE_TO_TRUSTED,
    CANDIDATE_TO_ACTIVE,
    CANDIDATE_TO_ACTIVE_SINGLE_SESSION,
    CANDIDATE_TO_ARCHIVED,
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
from ..utils.time import now_iso, parse_iso

log = get_logger("nokori.lifecycle.transitions")


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
        "suppressed_at, replacement_id "
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

    if status == "candidate":
        return _evaluate_candidate(db, row, rule_version)
    if status == "active":
        return _evaluate_active(db, row, rule_version)
    if status == "trusted":
        return _evaluate_trusted(db, row, rule_version)
    if status == "suppressed":
        return _evaluate_suppressed(db, row, rule_version)

    # archived rules do not transition
    return TransitionResult(
        rule_id=rule_id,
        old_status=status,
        new_status=None,
        reason="terminal state",
        applied=False,
    )


def run_all_pending_transitions(db: Db) -> list[TransitionResult]:
    """Iterate rules by status, evaluate each, return results."""
    results: list[TransitionResult] = []
    rows = db.fetchall(
        "SELECT id FROM rules WHERE status IN ('candidate','active','trusted','suppressed')"
    )
    for row in rows:
        result = evaluate_transitions(db, row["id"])
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Evidence aggregation
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
            # NULL = legacy event (pre-attribution system) → treated as strong for compat.
            # > 0.5 = new system confirmed strong causal attribution.
            # <= 0.5 = weak/redundant attribution → excluded from promotion count.
            attribution_weight = r["posthoc_score"]
            if attribution_weight is None or attribution_weight > 0.5:
                counts["observed_useful_strong"] = int(counts["observed_useful_strong"]) + 1
                strong_useful_sessions.add(r["session_id"])

    counts["reason_counts"] = reason_counts  # type: ignore[assignment]
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
    db: Db, rule_id: str, rule_version: int, window_days: int = 30,
    shadow_type: str | None = None,
    since_iso: str | None = None,
) -> dict:
    """Count shadow labels with fingerprint dedup, distinct sessions."""
    return count_shadow_evidence(
        db, rule_id, rule_version, window_days=window_days, shadow_type=shadow_type,
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
# Per-status evaluation logic
# ---------------------------------------------------------------------------


def _evaluate_candidate(db: Db, row, rule_version: int) -> TransitionResult:
    rule_id = row["id"]
    old_status = "candidate"
    rpv = row["runtime_policy_version"]

    shadow = _aggregate_shadow_evidence(db, rule_id, rule_version)

    # Check candidate -> archived (fast downgrade path)
    risky_harmful = shadow.get("risky", 0) + shadow.get("near_miss", 0)
    if risky_harmful >= CANDIDATE_TO_ARCHIVED.risky_or_harmful_shadow_count_min:
        reason = f"risky_or_harmful_shadow_count={risky_harmful}"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "archived", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "archived", reason, applied)

    if shadow.get("irrelevant", 0) >= CANDIDATE_TO_ARCHIVED.irrelevant_shadow_count_min:
        reason = f"irrelevant_shadow_count={shadow['irrelevant']}"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "archived", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "archived", reason, applied)

    if row["replacement_id"] is not None:
        reason = "covered_by_replacement"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "archived", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "archived", reason, applied)

    # Check synthetic eval status
    synth_row = db.fetchone(
        "SELECT passed FROM rule_synthetic_evals "
        "WHERE rule_id = ? AND rule_version = ? ORDER BY created_at DESC LIMIT 1",
        (rule_id, rule_version),
    )
    synthetic_eval_passed = synth_row is not None and synth_row["passed"] == 1

    if not synthetic_eval_passed:
        return TransitionResult(
            rule_id, old_status, None, "synthetic_eval not passed", False
        )

    # Check candidate -> active (normal path)
    th = CANDIDATE_TO_ACTIVE
    # shadow_strong_match_count = would_help_high only (spec: strong match)
    strong_count = shadow.get("would_help_high", 0)
    would_help_high = shadow.get("would_help_high", 0)
    # Denominator excludes 'unclear' per spec section 3.4
    evaluated_count = (
        shadow.get("would_help_high", 0)
        + shadow.get("would_help_low", 0)
        + shadow.get("irrelevant", 0)
        + shadow.get("risky", 0)
        + shadow.get("near_miss", 0)
    )
    distinct_sessions = shadow.get("distinct_sessions", 0)
    # Use task-deduped count as effective sample count for promotion thresholds.
    # This prevents inflated counts from repeated events within the same task.
    task_deduped_count = shadow.get("task_deduped_count", evaluated_count)

    # Shadow-to-posthoc reason_code mapping:
    #   'irrelevant'  -> irrelevant_not_applicable (FP: rule fired but was not relevant)
    #   'near_miss'   -> irrelevant_not_applicable (FP: close but should not have matched)
    #   'risky'       -> harmful_wrong_scope (suppression signal, NOT counted as FP)
    # FP numerator = irrelevant + near_miss (closest posthoc FP equivalents).
    shadow_fp_numerator = shadow.get("irrelevant", 0) + shadow.get("near_miss", 0)
    shadow_fp_rate = (
        shadow_fp_numerator / max(1, task_deduped_count)
        if task_deduped_count > 0
        else 0.0
    )

    normal_path = (
        strong_count >= th.shadow_strong_match_count_min
        and evaluated_count >= th.evaluated_shadow_match_count_min
        and distinct_sessions >= th.distinct_shadow_sessions_min
        and would_help_high >= th.counterfactual_would_help_high_min
        and risky_harmful <= th.risky_or_near_miss_shadow_count_max
        and shadow_fp_rate <= th.shadow_false_positive_rate_max
    )

    if normal_path:
        reason = (
            f"shadow_promotion: strong={strong_count} "
            f"evaluated={evaluated_count} sessions={distinct_sessions}"
        )
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "active", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "active", reason, applied)

    # Check single-session exception
    ss = CANDIDATE_TO_ACTIVE_SINGLE_SESSION
    # Need admission_overall_quality from rule_reviews
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

    # Spec requires strong matches within ONE session, not across all sessions
    best_single_session_strong = shadow.get("best_single_session_strong", 0)

    has_single_session_evidence = (
        admission_quality >= ss.admission_overall_quality_min
        and best_single_session_strong >= ss.shadow_strong_match_count_min
        and evaluated_count >= ss.evaluated_shadow_match_count_min
        and best_single_session_strong >= ss.counterfactual_would_help_high_min
        and risky_harmful <= ss.risky_or_near_miss_shadow_count_max
        and shadow_fp_rate <= ss.shadow_false_positive_rate_max
    )

    # Verify context diversity + observed_agent_miss_or_user_correction
    if has_single_session_evidence:
        # Spec requires at least 2 distinct user intents/contexts WITHIN the single session
        best_session_contexts = shadow.get("best_single_session_contexts", 0)
        if best_session_contexts < 2:
            return TransitionResult(
                rule_id, old_status, None,
                f"single_session_exception: insufficient per-session context diversity ({best_session_contexts} < 2)",
                False,
            )

        has_miss_evidence = _candidate_has_miss_evidence(db, rule_id, rule_version)
        if has_miss_evidence:
            reason = (
                f"single_session_exception: quality={admission_quality:.2f} "
                f"strong={strong_count} would_help_high={would_help_high} "
                f"contexts={best_session_contexts}"
            )
            applied = _apply_transition(
                db, rule_id, rule_version, old_status, "active", rpv, reason
            )
            return TransitionResult(rule_id, old_status, "active", reason, applied)

    return TransitionResult(
        rule_id, old_status, None, "insufficient promotion evidence", False
    )


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


def _evaluate_active(db: Db, row, rule_version: int) -> TransitionResult:
    rule_id = row["id"]
    old_status = "active"
    rpv = row["runtime_policy_version"]

    fire = _aggregate_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)

    # Check active -> suppressed (fast downgrade)
    # Harmful uses lifetime count — does NOT decay by time (spec 3.4)
    sup = ACTIVE_TO_SUPPRESSED
    lifetime_harmful = fire.get("lifetime_harmful", 0)
    if lifetime_harmful >= sup.harmful_count_min:
        reason = f"lifetime_harmful_count={lifetime_harmful}"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "suppressed", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "suppressed", reason, applied)

    irrelevant_last_5 = fire.get("irrelevant_in_last_5", 0)
    if irrelevant_last_5 >= sup.irrelevant_count_in_last_5_min:
        reason = f"irrelevant_in_last_5={irrelevant_last_5}"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "suppressed", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "suppressed", reason, applied)

    fp_rate = fire.get("false_positive_rate", 0.0)
    total_evaluated = fire.get("total_evaluated", 0)
    if total_evaluated >= MINIMUM_RATE_DENOMINATOR and fp_rate >= sup.recent_false_positive_rate_min:
        reason = f"false_positive_rate={fp_rate:.2f}"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "suppressed", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "suppressed", reason, applied)

    # Check active -> trusted (slow upgrade)
    # INVARIANT: trusted promotion uses ONLY fire events (observed_useful), never shadow/counterfactual
    # Use observed_useful_strong (attribution_weight > 0.5) for promotion threshold.
    th = ACTIVE_TO_TRUSTED
    observed_useful = fire.get("observed_useful_strong", 0)
    total_evaluated = fire.get("total_evaluated", 0)
    distinct_sessions = fire.get("distinct_strong_useful_sessions", 0)

    # Defensive guard: reject if shadow evidence was accidentally mixed into fire aggregation.
    # The _aggregate_fire_evidence query only reads rule_fire_events, but verify the counts
    # are sourced exclusively from real injection observations.
    if fire.get("_source") == "shadow":
        log.error(
            "trusted promotion rejected: fire evidence contaminated with shadow data rule=%s",
            rule_id,
        )
        return TransitionResult(
            rule_id, old_status, None, "shadow_evidence_contamination", False
        )

    # Rate-based promotion NOT allowed below minimum_rate_denominator (spec 3.4)
    # Spec 3.3: harmful_count = 0 for trusted promotion. Per spec 3.4:
    # "Harmful events do not decay below suppression thresholds merely because time passes."
    # Use lifetime harmful for both suppression AND promotion gating.
    if (
        total_evaluated >= th.evaluated_fire_count_min
        and total_evaluated >= MINIMUM_RATE_DENOMINATOR
        and observed_useful >= th.observed_useful_count_min
        and distinct_sessions >= th.distinct_observed_useful_sessions_min
        and lifetime_harmful <= th.harmful_count_max
        and fp_rate <= th.recent_false_positive_rate_max
    ):
        reason = (
            f"trusted_promotion: useful={observed_useful} "
            f"evaluated={total_evaluated} sessions={distinct_sessions}"
        )
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "trusted", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "trusted", reason, applied)

    return TransitionResult(
        rule_id, old_status, None, "no transition triggered", False
    )


def _evaluate_trusted(db: Db, row, rule_version: int) -> TransitionResult:
    rule_id = row["id"]
    old_status = "trusted"
    rpv = row["runtime_policy_version"]

    fire = _aggregate_fire_evidence(db, rule_id, window_days=RECENT_TIME_WINDOW_DAYS)

    # Check trusted -> suppressed (fast downgrade)
    # Harmful uses lifetime count — does NOT decay by time (spec 3.4)
    sup = TRUSTED_TO_SUPPRESSED
    lifetime_harmful = fire.get("lifetime_harmful", 0)
    if lifetime_harmful >= sup.harmful_count_min:
        reason = f"lifetime_harmful_count={lifetime_harmful}"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "suppressed", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "suppressed", reason, applied)

    irrelevant_last_5 = fire.get("irrelevant_in_last_5", 0)
    if irrelevant_last_5 >= sup.irrelevant_count_in_last_5_min:
        reason = f"irrelevant_in_last_5={irrelevant_last_5}"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "suppressed", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "suppressed", reason, applied)

    fp_rate = fire.get("false_positive_rate", 0.0)
    total_evaluated = fire.get("total_evaluated", 0)
    if total_evaluated >= MINIMUM_RATE_DENOMINATOR and fp_rate >= sup.recent_false_positive_rate_min:
        reason = f"false_positive_rate={fp_rate:.2f}"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "suppressed", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "suppressed", reason, applied)

    # Check trusted -> active (decay)
    th = TRUSTED_TO_ACTIVE
    observed_useful = fire.get("observed_useful", 0)
    irrelevant = fire.get("irrelevant", 0)

    # Rate-based decay requires minimum_rate_denominator (spec 3.4)
    # Spec 3.3 'harmful_count = 0' — use lifetime harmful for consistency.
    # Suppression guard above catches lifetime_harmful >= 1 first, but
    # defensive: if suppression thresholds ever change, decay still won't
    # promote a rule with any historical harm.
    if (
        total_evaluated >= th.evaluated_fire_count_in_recent_window_min
        and total_evaluated >= MINIMUM_RATE_DENOMINATOR
        and observed_useful <= th.observed_useful_count_in_recent_window_max
        and irrelevant >= th.irrelevant_count_in_recent_window_min
        and lifetime_harmful <= th.harmful_count_max
        and fp_rate >= th.recent_false_positive_rate_min
    ):
        reason = (
            f"trust_decay: useful={observed_useful} "
            f"irrelevant={irrelevant} fp_rate={fp_rate:.2f}"
        )
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "active", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "active", reason, applied)

    return TransitionResult(
        rule_id, old_status, None, "no transition triggered", False
    )


def _evaluate_suppressed(db: Db, row, rule_version: int) -> TransitionResult:
    rule_id = row["id"]
    old_status = "suppressed"
    rpv = row["runtime_policy_version"]

    # Only count shadow evidence AFTER suppression with recovery type (spec 10.3)
    suppressed_at_iso = row["suppressed_at"]
    shadow = _aggregate_shadow_evidence(
        db, rule_id, rule_version, since_iso=suppressed_at_iso,
        shadow_type="suppression_recovery",
    )

    # Check suppressed -> archived (fast downgrade)
    risky_harmful = shadow.get("risky", 0) + shadow.get("near_miss", 0)
    if risky_harmful >= SUPPRESSED_TO_ARCHIVED.risky_or_harmful_shadow_count_after_suppression_min:
        reason = f"risky_or_harmful_after_suppression={risky_harmful}"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "archived", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "archived", reason, applied)

    if row["replacement_id"] is not None:
        reason = "covered_by_replacement"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "archived", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "archived", reason, applied)

    # Check TTL FIRST — prevents recovery after TTL expiry
    suppressed_at = parse_iso(row["suppressed_at"])
    ttl_expired = False
    if suppressed_at is not None:
        ttl_deadline = suppressed_at + timedelta(days=SUPPRESSION_TTL_DAYS)
        ttl_expired = datetime.now(timezone.utc) > ttl_deadline

    if ttl_expired:
        # TTL expired AND recovery evidence insufficient → archive
        would_help_high = shadow.get("would_help_high", 0)
        if would_help_high < SUPPRESSED_TO_ACTIVE.shadow_recovery_would_help_high_min:
            reason = "no_recovery_before_ttl"
            applied = _apply_transition(
                db, rule_id, rule_version, old_status, "archived", rpv, reason
            )
            return TransitionResult(
                rule_id, old_status, "archived", reason, applied
            )
        # TTL expired but recovery evidence exists — still archive (no recovery after TTL)
        reason = "ttl_expired"
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "archived", rpv, reason
        )
        return TransitionResult(
            rule_id, old_status, "archived", reason, applied
        )

    # TTL NOT expired — check suppressed -> active (recovery)
    th = SUPPRESSED_TO_ACTIVE
    would_help_high = shadow.get("would_help_high", 0)
    distinct_sessions = shadow.get("distinct_sessions", 0)

    # Recent harmful from fire events after suppression
    suppressed_at_iso = row["suppressed_at"]
    recent_harmful = 0
    if suppressed_at_iso:
        harmful_row = db.fetchone(
            "SELECT COUNT(*) AS n FROM rule_fire_events "
            "WHERE rule_id = ? AND posthoc_label = 'harmful' AND created_at >= ?",
            (rule_id, suppressed_at_iso),
        )
        if harmful_row:
            recent_harmful = harmful_row["n"]

    if (
        would_help_high >= th.shadow_recovery_would_help_high_min
        and distinct_sessions >= th.distinct_recovery_sessions_min
        and recent_harmful <= th.recent_harmful_count_max
    ):
        reason = (
            f"shadow_recovery: would_help_high={would_help_high} "
            f"sessions={distinct_sessions}"
        )
        applied = _apply_transition(
            db, rule_id, rule_version, old_status, "active", rpv, reason
        )
        return TransitionResult(rule_id, old_status, "active", reason, applied)

    return TransitionResult(
        rule_id, old_status, None, "no transition triggered", False
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _days_ago_iso(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
