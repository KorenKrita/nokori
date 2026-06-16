from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from nokori.db import fetch_rule_by_short_id, fetch_rules, open_db
from nokori.events.fire import (
    batch_count_distinct_useful_projects,
    count_evaluated_fire_events,
    get_fire_events_for_rule,
)
from nokori.lifecycle.transitions import compute_promotion_barriers
from nokori.policy import CROSS_PROJECT_PROMOTION_THRESHOLD
from nokori.web.deps import get_config
from nokori.web.models import (
    DecisionFeaturesOut,
    FireEventOut,
    ShadowEventOut,
    SyntheticEvalSummary,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Fire events for a rule
# ---------------------------------------------------------------------------


def _fire_event_to_out(row: dict) -> dict:
    """Convert a fire event DB row to FireEventOut dict."""
    decision_features = None
    raw_features = row.get("decision_features")
    if raw_features:
        try:
            features_data = (
                json.loads(raw_features) if isinstance(raw_features, str) else raw_features
            )
            decision_features = DecisionFeaturesOut(**features_data)
        except (json.JSONDecodeError, TypeError, ValueError):
            decision_features = None

    structured_snapshot = None
    raw_snapshot = row.get("injected_structured_snapshot")
    if raw_snapshot:
        try:
            structured_snapshot = (
                json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else raw_snapshot
            )
        except (json.JSONDecodeError, TypeError):
            structured_snapshot = None

    return FireEventOut(
        id=row["id"],
        rule_id=row["rule_id"],
        session_id=row["session_id"],
        injected_rule_version=row.get("injected_rule_version"),
        injected_trigger_snapshot=row.get("injected_trigger_snapshot"),
        injected_action_snapshot=row.get("injected_action_snapshot"),
        injected_structured_snapshot=structured_snapshot,
        trigger_idf_pool_version=row.get("trigger_idf_pool_version"),
        runtime_policy_version=row.get("runtime_policy_version"),
        embedding_profile_version=row.get("embedding_profile_version"),
        prompt_hash=row.get("prompt_hash"),
        turn_index=row.get("turn_index"),
        level=row.get("level", ""),
        decision_features=decision_features,
        posthoc_label=row.get("posthoc_label"),
        posthoc_reason_code=row.get("posthoc_reason_code"),
        posthoc_score=row.get("posthoc_score"),
        created_at=row["created_at"],
    ).model_dump()


@router.get("/lifecycle/rules/{short_id}/fire-events")
def rule_fire_events(
    short_id: str,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Return fire event history for a rule (most recent first)."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
        events = get_fire_events_for_rule(db, rule.id, limit=limit)
    finally:
        db.close()
    return {"data": [_fire_event_to_out(e) for e in events]}


# ---------------------------------------------------------------------------
# Shadow events for a rule
# ---------------------------------------------------------------------------


@router.get("/lifecycle/rules/{short_id}/shadow-events")
def rule_shadow_events(
    short_id: str,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Return shadow event history for a rule (most recent first)."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
        rows = db.fetchall(
            "SELECT * FROM rule_shadow_events WHERE rule_id = ? ORDER BY created_at DESC LIMIT ?",
            (rule.id, limit),
        )
    finally:
        db.close()

    events = []
    for row in rows:
        d = dict(row)
        events.append(
            ShadowEventOut(
                id=d["id"],
                rule_id=d["rule_id"],
                session_id=d["session_id"],
                shadow_rule_version=d.get("shadow_rule_version"),
                prompt_hash=d.get("prompt_hash"),
                shadow_label=d.get("shadow_label"),
                shadow_type=d.get("shadow_type"),
                context_fingerprint=d.get("context_fingerprint"),
                status_at_match=d.get("status_at_match"),
                created_at=d["created_at"],
            ).model_dump()
        )
    return {"data": events}


# ---------------------------------------------------------------------------
# Posthoc evaluation summary
# ---------------------------------------------------------------------------


@router.get("/lifecycle/rules/{short_id}/posthoc")
def rule_posthoc_summary(
    short_id: str,
    window_days: int = Query(30, ge=1, le=365),
) -> dict:
    """Return posthoc evaluation summary for a rule within a time window."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
        counts = count_evaluated_fire_events(db, rule.id, window_days=window_days)
    finally:
        db.close()

    return {
        "data": {
            "rule_id": rule.id,
            "short_id": rule.short_id,
            "window_days": window_days,
            "observed_useful": counts.get("observed_useful", 0),
            "plausible_useful": counts.get("plausible_useful", 0),
            "irrelevant": counts.get("irrelevant", 0),
            "harmful": counts.get("harmful", 0),
            "unclear": counts.get("unclear", 0),
            "total_evaluated": counts.get("total_evaluated", 0),
        }
    }


# ---------------------------------------------------------------------------
# Synthetic eval status
# ---------------------------------------------------------------------------


@router.get("/lifecycle/rules/{short_id}/synthetic-eval")
def rule_synthetic_eval(short_id: str) -> dict:
    """Return latest synthetic eval result summary for a rule."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
        row = db.fetchone(
            "SELECT * FROM rule_synthetic_evals WHERE rule_id = ? ORDER BY created_at DESC LIMIT 1",
            (rule.id,),
        )
    finally:
        db.close()

    if row is None:
        return {"data": None}

    d = dict(row)
    # Parse results to compute summary counts
    eval_results = []
    raw_results = d.get("eval_results")
    if raw_results:
        try:
            eval_results = json.loads(raw_results) if isinstance(raw_results, str) else raw_results
        except (json.JSONDecodeError, TypeError):
            eval_results = []

    positive_results = [r for r in eval_results if r.get("case_type") == "positive"]
    near_miss_results = [r for r in eval_results if r.get("case_type") == "near_miss"]
    negative_results = [r for r in eval_results if r.get("case_type") == "negative"]

    summary = SyntheticEvalSummary(
        rule_id=d["rule_id"],
        rule_version=d["rule_version"] or 0,
        passed=bool(d["passed"]),
        runtime_policy_version=d.get("runtime_policy_version"),
        tokenizer_version=d.get("tokenizer_version"),
        matcher_compiler_version=d.get("matcher_compiler_version"),
        benchmark_version=d.get("benchmark_version"),
        total_cases=len(eval_results),
        positive_passed=sum(1 for r in positive_results if r.get("case_passed")),
        positive_total=len(positive_results),
        near_miss_passed=sum(1 for r in near_miss_results if r.get("case_passed")),
        near_miss_total=len(near_miss_results),
        negative_passed=sum(1 for r in negative_results if r.get("case_passed")),
        negative_total=len(negative_results),
        created_at=d.get("created_at"),
    )
    return {"data": summary.model_dump()}


# ---------------------------------------------------------------------------
# Transition history
# ---------------------------------------------------------------------------


@router.get("/lifecycle/rules/{short_id}/transitions")
def rule_transitions(
    short_id: str,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Return transition event history for a rule (if tracked in DB)."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")

        # Check if transition log table exists
        table_check = db.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='rule_transitions'"
        )
        if table_check is None:
            return {"data": []}

        rows = db.fetchall(
            "SELECT * FROM rule_transitions WHERE rule_id = ? ORDER BY created_at DESC LIMIT ?",
            (rule.id, limit),
        )
    finally:
        db.close()

    return {"data": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Promotion barriers
# ---------------------------------------------------------------------------


@router.get("/lifecycle/rules/{short_id}/barriers")
def get_promotion_barriers(short_id: str) -> dict:
    """Return per-criterion promotion barrier data for a rule."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
        result = compute_promotion_barriers(
            db, rule.id, rule.status, rule.rule_version, rule.suppressed_at
        )
    finally:
        db.close()
    return {"data": result}


# ---------------------------------------------------------------------------
# Legacy: promotion progress (kept for backward compat)
# ---------------------------------------------------------------------------


@router.get("/lifecycle/promotion")
def promotion_progress() -> dict:
    """Shadow promotion progress overview (read-only)."""
    cfg = get_config()
    if not cfg.promotion_enabled:
        return {"data": {"enabled": False, "candidates": []}}

    db = open_db(cfg.db_path)
    try:
        rows = db.fetchall(
            "SELECT r.short_id, r.project_id, r.trigger_canonical, "
            "r.trigger_canonical_zh, "
            "r.status, r.rule_version, r.quality_score "
            "FROM rules r "
            "WHERE r.status = 'candidate' "
            "ORDER BY r.quality_score DESC, r.updated_at DESC "
            "LIMIT 100"
        )
    finally:
        db.close()

    candidates = [
        {
            "short_id": row["short_id"],
            "project_id": row["project_id"],
            "trigger_canonical": row["trigger_canonical"],
            "trigger_canonical_zh": row["trigger_canonical_zh"],
            "status": row["status"],
            "rule_version": row["rule_version"],
            "quality_score": row["quality_score"],
        }
        for row in rows
    ]
    return {"data": {"enabled": True, "candidates": candidates}}


# ---------------------------------------------------------------------------
# Legacy: maintenance status (kept)
# ---------------------------------------------------------------------------


@router.get("/lifecycle/maintenance")
def maintenance_status() -> dict:
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rows = db.fetchall("SELECT key, last_run FROM maintenance_meta")
    finally:
        db.close()
    return {"data": {row["key"]: row["last_run"] for row in rows}}


# ---------------------------------------------------------------------------
# Cross-project promotion eligibility
# ---------------------------------------------------------------------------


@router.get("/lifecycle/global-eligible")
def global_eligible_rules() -> dict:
    """Return trusted project-scoped rules approaching cross-project promotion threshold."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(db, statuses=("trusted",))
        project_rules = [r for r in rules if r.project_scope == "project"]
        counts = batch_count_distinct_useful_projects(db, [r.id for r in project_rules])
        eligible = [
            {
                "short_id": r.short_id,
                "trigger_canonical": r.trigger_canonical,
                "trigger_canonical_zh": r.trigger_canonical_zh,
                "project_id": r.project_id,
                "distinct_projects": counts.get(r.id, 0),
                "target": CROSS_PROJECT_PROMOTION_THRESHOLD,
            }
            for r in project_rules
            if counts.get(r.id, 0) >= max(2, CROSS_PROJECT_PROMOTION_THRESHOLD - 1)
        ]
    finally:
        db.close()
    return {"data": eligible}
