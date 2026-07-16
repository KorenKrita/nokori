"""Batch evaluation path for pending lifecycle transitions."""

from __future__ import annotations

import json

from ..db import Db
from ..events.fire import batch_count_distinct_useful_projects
from ..policy import RECENT_EVENT_WINDOW, RECENT_TIME_WINDOW_DAYS
from ..utils.logging import get_logger
from ..utils.sql_batch import batched
from ..utils.time import local_days_ago
from .evidence import (
    candidate_has_miss_evidence,
    compute_fire_counts,
    gather_shadow_evidence,
)
from .transition_apply import _apply_cross_project_promotion, _apply_decision
from .transition_evaluate import (
    _evaluate_active,
    _evaluate_candidate,
    _evaluate_suppressed,
    _evaluate_trusted,
)
from .transition_gather import _gather_suppressed_evidence
from .transition_types import EvidenceSnapshot, TransitionResult

log = get_logger("nokori.lifecycle.transitions")


def run_all_pending_transitions(db: Db) -> list[TransitionResult]:
    """Batch-evaluate all non-archived rules using minimal DB queries."""
    from ..db import fetch_rule_ids

    rule_ids = fetch_rule_ids(db, statuses=("candidate", "active", "trusted", "suppressed"))
    if not rule_ids:
        return []
    return _batch_evaluate_transitions(db, rule_ids)


# ---------------------------------------------------------------------------
# Batch evaluation (eliminates N+1 queries in run_all_pending_transitions)
# ---------------------------------------------------------------------------


def _batch_evaluate_transitions(db: Db, rule_ids: list[str]) -> list[TransitionResult]:
    """Batch version of evaluate_transitions used by run_all_pending_transitions."""
    results: list[TransitionResult] = []

    # 1. Batch fetch rule metadata
    rule_meta: dict[str, dict] = {}
    for chunk in batched(rule_ids):
        placeholders = ",".join("?" * len(chunk))
        rows = db.fetchall(
            "SELECT id, rule_version, status, runtime_policy_version, "
            f"suppressed_at, replacement_id, project_scope FROM rules WHERE id IN ({placeholders})",
            tuple(chunk),
        )
        for r in rows:
            rule_meta[r["id"]] = dict(r)

    # 2. Group by status
    by_status: dict[str, list[str]] = {}
    for rid, meta in rule_meta.items():
        by_status.setdefault(meta["status"], []).append(rid)

    # 3. Batch evidence + evaluate per status group
    candidate_ids = by_status.get("candidate", [])
    if candidate_ids:
        results.extend(_batch_candidates(db, candidate_ids, rule_meta))

    active_ids = by_status.get("active", [])
    if active_ids:
        results.extend(_batch_active_trusted(db, active_ids, rule_meta, status="active"))

    trusted_ids = by_status.get("trusted", [])
    if trusted_ids:
        results.extend(_batch_active_trusted(db, trusted_ids, rule_meta, status="trusted"))

    suppressed_ids = by_status.get("suppressed", [])
    if suppressed_ids:
        results.extend(_batch_suppressed(db, suppressed_ids, rule_meta))

    return results


def _batch_candidates(
    db: Db, rule_ids: list[str], rule_meta: dict[str, dict]
) -> list[TransitionResult]:
    """Batch evidence gathering and evaluation for candidate rules."""
    results: list[TransitionResult] = []

    # Batch shadow evidence (still per-rule because gather_shadow_evidence does
    # complex fingerprint dedup + task dedup that doesn't batch simply)
    shadow_by_rule: dict[str, dict] = {}
    for rid in rule_ids:
        meta = rule_meta[rid]
        shadow_by_rule[rid] = gather_shadow_evidence(db, rid, meta["rule_version"])

    # Batch synthetic eval: latest passed status per (rule_id, rule_version)
    # Each rule contributes 2 params (rule_id + rule_version), so halve the batch size
    synth_passed: dict[str, bool] = {}
    for chunk in batched(rule_ids, batch_size=450):
        # Get all relevant evals, then pick latest per rule_id in Python
        params: list = []
        conditions: list[str] = []
        for rid in chunk:
            rv = rule_meta[rid]["rule_version"]
            conditions.append("(rule_id = ? AND rule_version = ?)")
            params.extend([rid, rv])
        where = " OR ".join(conditions)
        rows = db.fetchall(
            f"SELECT rule_id, passed, created_at FROM rule_synthetic_evals "
            f"WHERE ({where}) ORDER BY created_at DESC",
            tuple(params),
        )
        # Keep first (latest) per rule_id
        for r in rows:
            rid = r["rule_id"]
            if rid not in synth_passed:
                synth_passed[rid] = r["passed"] == 1

    # Batch admission quality from rule_reviews
    admission_quality: dict[str, float] = {}
    for chunk in batched(rule_ids):
        placeholders = ",".join("?" * len(chunk))
        rows = db.fetchall(
            f"SELECT rule_id, scores, created_at FROM rule_reviews "
            f"WHERE rule_id IN ({placeholders}) AND decision = 'accept_active' "
            f"ORDER BY created_at DESC",
            tuple(chunk),
        )
        for r in rows:
            rid = r["rule_id"]
            if rid not in admission_quality:
                if r["scores"]:
                    try:
                        scores = json.loads(r["scores"])
                        admission_quality[rid] = scores.get("overall_quality", 0.0)
                    except (json.JSONDecodeError, TypeError):
                        admission_quality[rid] = 0.0
                else:
                    admission_quality[rid] = 0.0

    # Batch miss evidence check
    miss_evidence: dict[str, bool] = {}
    for rid in rule_ids:
        meta = rule_meta[rid]
        miss_evidence[rid] = candidate_has_miss_evidence(db, rid, meta["rule_version"])

    # Build EvidenceSnapshot and evaluate for each candidate
    for rid in rule_ids:
        try:
            meta = rule_meta[rid]
            rule_version = meta["rule_version"]
            rpv = meta["runtime_policy_version"]

            shadow = shadow_by_rule.get(rid, {})
            strong_count = shadow.get("would_help_high", 0)
            shadow_irrelevant = shadow.get("irrelevant", 0)
            shadow_risky = shadow.get("risky", 0)
            shadow_near_miss = shadow.get("near_miss", 0)
            shadow_weak = shadow.get("would_help_low", 0)
            evaluated_count = (
                strong_count + shadow_weak + shadow_irrelevant + shadow_risky + shadow_near_miss
            )
            task_deduped_count = shadow.get("task_deduped_count", evaluated_count)
            shadow_fp_numerator = shadow_irrelevant + shadow_near_miss
            shadow_fp_rate = (
                shadow_fp_numerator / max(1, task_deduped_count)
                if task_deduped_count > 0
                else 0.0
            )

            evidence = EvidenceSnapshot(
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
                synthetic_eval_passed=synth_passed.get(rid, False),
                admission_quality=admission_quality.get(rid, 0.0),
                has_miss_evidence=miss_evidence.get(rid, False),
                has_replacement=meta["replacement_id"] is not None,
                rule_version=rule_version,
            )

            decision = _evaluate_candidate(evidence)
            results.append(
                _apply_decision(db, rid, rule_version, "candidate", rpv, decision)
            )
        except Exception as exc:
            log.exception("batch evaluate_transitions failed for rule=%s: %s", rid, exc)

    return results


def _batch_active_trusted(
    db: Db, rule_ids: list[str], rule_meta: dict[str, dict], *, status: str
) -> list[TransitionResult]:
    """Batch evidence gathering and evaluation for active/trusted rules."""
    results: list[TransitionResult] = []

    # Batch fire evidence: fetch recent fire events for all rules at once.
    # ponytail: batch SQL intentionally stays here — gather_fire_evidence is per-rule;
    # this batch path uses a single windowed query for N rules (performance).
    cutoff = local_days_ago(RECENT_TIME_WINDOW_DAYS)
    fire_by_rule: dict[str, dict] = {}

    for chunk in batched(rule_ids):
        placeholders = ",".join("?" * len(chunk))
        rows = db.fetchall(
            f"SELECT rule_id, posthoc_label, posthoc_reason_code, posthoc_score, session_id "
            f"FROM ("
            f"  SELECT rule_id, posthoc_label, posthoc_reason_code, posthoc_score, session_id, "
            f"    ROW_NUMBER() OVER (PARTITION BY rule_id ORDER BY created_at DESC) AS rn "
            f"  FROM rule_fire_events "
            f"  WHERE rule_id IN ({placeholders}) "
            f"  AND posthoc_label IS NOT NULL AND posthoc_label != 'unclear' "
            f"  AND created_at >= ?"
            f") WHERE rn <= ? ORDER BY rule_id, rn",
            (*chunk, cutoff, RECENT_EVENT_WINDOW),
        )

        per_rule_rows: dict[str, list] = {}
        for r in rows:
            rid = r["rule_id"]
            if rid not in per_rule_rows:
                per_rule_rows[rid] = []
            per_rule_rows[rid].append(r)

        for rid, rrows in per_rule_rows.items():
            fire_by_rule[rid] = compute_fire_counts(rrows)

    # Batch lifetime harmful counts (batch variant of gather_fire_evidence's lifetime_harmful)
    harmful_by_rule: dict[str, int] = {}
    for chunk in batched(rule_ids):
        placeholders = ",".join("?" * len(chunk))
        rows = db.fetchall(
            f"SELECT rule_id, COUNT(*) AS n FROM rule_fire_events "
            f"WHERE rule_id IN ({placeholders}) AND posthoc_label = 'harmful' "
            f"GROUP BY rule_id",
            tuple(chunk),
        )
        for r in rows:
            harmful_by_rule[r["rule_id"]] = int(r["n"])

    # For trusted: batch distinct useful projects
    distinct_projects_by_rule: dict[str, int] = {}
    if status == "trusted":
        project_scope_ids = [
            rid for rid in rule_ids if rule_meta[rid]["project_scope"] == "project"
        ]
        if project_scope_ids:
            for chunk in batched(project_scope_ids):
                distinct_projects_by_rule.update(
                    batch_count_distinct_useful_projects(db, chunk)
                )

    # Build evidence and evaluate
    for rid in rule_ids:
        try:
            meta = rule_meta[rid]
            rule_version = meta["rule_version"]
            rpv = meta["runtime_policy_version"]
            fire = fire_by_rule.get(rid, {})
            lifetime_harmful = harmful_by_rule.get(rid, 0)

            evidence = EvidenceSnapshot(
                observed_useful_strong=fire.get("observed_useful_strong", 0),
                observed_useful_total=fire.get("observed_useful", 0),
                irrelevant_in_last_5=fire.get("irrelevant_in_last_5", 0),
                irrelevant_in_window=fire.get("irrelevant", 0),
                harmful_lifetime=lifetime_harmful,
                false_positive_rate=fire.get("false_positive_rate", 0.0),
                fire_total_evaluated=fire.get("total_evaluated", 0),
                distinct_strong_useful_sessions=fire.get("distinct_strong_useful_sessions", 0),
                project_scope=meta["project_scope"] if status == "trusted" else None,
                distinct_useful_projects=distinct_projects_by_rule.get(rid, 0),
                rule_version=rule_version,
            )

            if status == "active":
                decision = _evaluate_active(evidence)
                results.append(
                    _apply_decision(db, rid, rule_version, "active", rpv, decision)
                )
            else:
                decision = _evaluate_trusted(evidence)
                if decision.reason == "cross_project_promotion":
                    results.append(
                        _apply_cross_project_promotion(
                            db,
                            rid,
                            rule_version,
                            "trusted",
                            distinct_count=decision.metadata.get("distinct_project_count"),
                        )
                    )
                else:
                    results.append(
                        _apply_decision(db, rid, rule_version, "trusted", rpv, decision)
                    )
        except Exception as exc:
            log.exception("batch evaluate_transitions failed for rule=%s: %s", rid, exc)

    return results


def _batch_suppressed(
    db: Db, rule_ids: list[str], rule_meta: dict[str, dict]
) -> list[TransitionResult]:
    """Evaluate suppressed rules. Per-rule queries (suppressed rules are rare)."""
    results: list[TransitionResult] = []
    for rid in rule_ids:
        try:
            meta = rule_meta[rid]
            rule_version = meta["rule_version"]
            rpv = meta["runtime_policy_version"]
            evidence = _gather_suppressed_evidence(db, meta, rule_version)
            decision = _evaluate_suppressed(evidence)
            results.append(
                _apply_decision(db, rid, rule_version, "suppressed", rpv, decision)
            )
        except Exception as exc:
            log.exception("batch evaluate_transitions failed for rule=%s: %s", rid, exc)
    return results

