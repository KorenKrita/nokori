from __future__ import annotations

import json

from fastapi import APIRouter, Query

from nokori.db import open_db
from nokori.web.deps import get_config
from nokori.web.models import DecisionFeaturesOut, FireEventOut

router = APIRouter()


def _fire_event_row_to_dict(row: dict) -> dict:
    """Convert a fire event DB row to response dict."""
    decision_features = None
    raw_features = row.get("decision_features")
    if raw_features:
        try:
            features_data = (
                json.loads(raw_features) if isinstance(raw_features, str) else raw_features
            )
            decision_features = DecisionFeaturesOut(**features_data).model_dump()
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


@router.get("/injections")
def list_injections(
    level: str | None = Query(None),
    rule_id: str | None = Query(None),
    session_id: str | None = Query(None),
    posthoc_label: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """List fire events (rule injections), replacing the legacy injections table.

    Queries rule_fire_events directly. Supports session-based filtering.
    """
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        where = []
        params: list = []

        if level:
            where.append("e.level = ?")
            params.append(level)
        if rule_id:
            where.append("e.rule_id = ?")
            params.append(rule_id)
        if session_id:
            where.append("e.session_id = ?")
            params.append(session_id)
        if posthoc_label:
            where.append("e.posthoc_label = ?")
            params.append(posthoc_label)

        where_clause = (" WHERE " + " AND ".join(where)) if where else ""

        join_clause = " LEFT JOIN rules r ON r.id = e.rule_id"

        count_row = db.fetchone(
            f"SELECT COUNT(*) AS n FROM rule_fire_events e{join_clause}{where_clause}",
            tuple(params),
        )
        total = count_row["n"] if count_row else 0

        offset = (page - 1) * per_page
        rows = db.fetchall(
            f"SELECT e.*, r.short_id AS rule_short_id, r.project_scope AS rule_project_scope"
            f" FROM rule_fire_events e{join_clause}"
            f"{where_clause} ORDER BY e.created_at DESC LIMIT ? OFFSET ?",
            tuple(params) + (per_page, offset),
        )
    finally:
        db.close()

    return {
        "data": [_fire_event_row_to_dict(dict(row)) for row in rows],
        "meta": {"total": total, "page": page, "per_page": per_page},
    }
