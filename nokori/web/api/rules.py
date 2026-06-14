from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from nokori.db import (
    archive_rule,
    fetch_rule_by_short_id,
    fetch_rules,
    loads_json,
    open_db,
)
from nokori.utils.time import now_iso
from nokori.web.deps import get_config, require_write_auth
from nokori.web.models import RuleResponse

router = APIRouter()


def _rule_to_response(
    rule,
    *,
    fire_count: int = 0,
    fire_last_at: str | None = None,
    fire_levels: dict[str, int] | None = None,
    posthoc_labels: dict[str, int] | None = None,
    shadow_count: int = 0,
) -> dict:
    """Convert a Rule dataclass to a RuleResponse dict with all structured fields."""
    return RuleResponse(
        id=rule.id,
        short_id=rule.short_id,
        schema_version=rule.schema_version,
        rule_version=rule.rule_version,
        created_by_pipeline_version=rule.created_by_pipeline_version,
        runtime_policy_version=rule.runtime_policy_version or "1.0.0",
        last_rewritten_by_role=rule.last_rewritten_by_role,
        status=rule.status,
        severity=rule.severity,
        trigger_canonical=rule.trigger_canonical,
        trigger_canonical_zh=rule.trigger_canonical_zh,
        concepts=loads_json(rule.concepts, []),
        required_concept_groups=loads_json(rule.required_concept_groups, []),
        excluded_contexts=loads_json(rule.excluded_contexts, []),
        near_miss_examples=rule.near_miss_examples,
        trigger_variants=rule.trigger_variants
        if isinstance(rule.trigger_variants, list)
        else loads_json(rule.trigger_variants, []),
        trigger_variants_zh=rule.trigger_variants_zh
        if isinstance(rule.trigger_variants_zh, list)
        else loads_json(rule.trigger_variants_zh, []),
        search_terms=rule.search_terms,
        action_instruction=rule.action_instruction,
        action_instruction_zh=rule.action_instruction_zh,
        allowed_behavior=rule.allowed_behavior,
        forbidden_behavior=rule.forbidden_behavior,
        domain_tags=rule.domain_tags,
        tool_tags=rule.tool_tags,
        path_patterns=rule.path_patterns,
        evidence_quotes=rule.evidence_quotes,
        quality_score=rule.quality_score,
        evidence_support_score=rule.evidence_support_score,
        specificity_score=rule.specificity_score,
        retrieval_readiness_score=rule.retrieval_readiness_score,
        observed_usefulness_score=rule.observed_usefulness_score,
        plausible_usefulness_score=rule.plausible_usefulness_score,
        false_positive_score=rule.false_positive_score,
        harmful_score=rule.harmful_score,
        source_origin=rule.source_origin,
        activation_origin=rule.activation_origin,
        first_observed_useful_at=rule.first_observed_useful_at,
        trusted_at=rule.trusted_at,
        suppressed_at=rule.suppressed_at,
        project_scope=rule.project_scope,
        project_id=rule.project_id,
        archived_reason=rule.archived_reason,
        replacement_id=rule.replacement_id,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
        fire_count=fire_count,
        fire_last_at=fire_last_at,
        fire_levels=fire_levels or {},
        posthoc_labels=posthoc_labels or {},
        shadow_count=shadow_count,
    ).model_dump()


@router.get("/rules")
def list_rules(
    status: str | None = Query(None),
    source_origin: str | None = Query(None),
    severity: str | None = Query(None),
    project: str | None = Query(None),
    scope: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    cfg = get_config()
    statuses = tuple(status.split(",")) if status else None
    source_origins = tuple(source_origin.split(",")) if source_origin else None
    severities = tuple(severity.split(",")) if severity else None
    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(
            db,
            statuses=statuses,
            project_id=project,
            global_only=(scope == "global"),
            project_scope_exact=(project is not None and scope != "global"),
            source_origins=source_origins,
            severities=severities,
        )

        total = len(rules)
        start = (page - 1) * per_page
        page_rules = rules[start : start + per_page]

        fire_counts: dict[str, int] = {}
        if page_rules:
            rule_ids = [r.id for r in page_rules]
            placeholders = ",".join("?" * len(rule_ids))
            rows = db.fetchall(
                f"SELECT rule_id, COUNT(*) as cnt FROM rule_fire_events "
                f"WHERE rule_id IN ({placeholders}) GROUP BY rule_id",
                tuple(rule_ids),
            )
            fire_counts = {row["rule_id"]: row["cnt"] for row in rows}

        result = {
            "data": [_rule_to_response(r, fire_count=fire_counts.get(r.id, 0)) for r in page_rules],
            "meta": {"total": total, "page": page, "per_page": per_page},
        }
    finally:
        db.close()

    return result


@router.get("/rules/{short_id}")
def show_rule(short_id: str):
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
        # Aggregate fire event statistics
        row = db.fetchone(
            "SELECT COUNT(*) as cnt, MAX(created_at) as last_at FROM rule_fire_events WHERE rule_id = ?",
            (rule.id,),
        )
        fire_count = row["cnt"] if row else 0
        fire_last_at = row["last_at"] if row else None

        levels_rows = db.fetchall(
            "SELECT level, COUNT(*) as cnt FROM rule_fire_events WHERE rule_id = ? GROUP BY level",
            (rule.id,),
        )
        fire_levels = {r["level"]: r["cnt"] for r in levels_rows}

        posthoc_rows = db.fetchall(
            "SELECT posthoc_label, COUNT(*) as cnt FROM rule_fire_events WHERE rule_id = ? AND posthoc_label IS NOT NULL GROUP BY posthoc_label",
            (rule.id,),
        )
        posthoc_labels = {r["posthoc_label"]: r["cnt"] for r in posthoc_rows}

        shadow_row = db.fetchone(
            "SELECT COUNT(*) as cnt FROM rule_shadow_events WHERE rule_id = ?",
            (rule.id,),
        )
        shadow_count = shadow_row["cnt"] if shadow_row else 0

        data = _rule_to_response(
            rule,
            fire_count=fire_count,
            fire_last_at=fire_last_at,
            fire_levels=fire_levels,
            posthoc_labels=posthoc_labels,
            shadow_count=shadow_count,
        )
    finally:
        db.close()
    return {"data": data}


@router.post("/rules/{short_id}/archive", dependencies=[Depends(require_write_auth)])
def archive_rule_endpoint(short_id: str):
    """Archive a rule (user-initiated). No manual promote/trust/suppress allowed."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
        if rule.status == "archived":
            return {"data": _rule_to_response(rule)}
        archive_rule(db, rule.id, "web_archive", now_iso())
        updated = fetch_rule_by_short_id(db, short_id)
        return {"data": _rule_to_response(updated)}
    finally:
        db.close()


@router.post("/rules/{short_id}/dismiss", dependencies=[Depends(require_write_auth)])
def dismiss_rule(short_id: str):
    """Dismiss a rule (alias for archive with dismiss reason)."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
        if rule.status == "archived":
            return {"data": _rule_to_response(rule)}
        archive_rule(db, rule.id, "web_dismiss", now_iso())
        updated = fetch_rule_by_short_id(db, short_id)
        return {"data": _rule_to_response(updated)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Reject manual lifecycle mutations
# ---------------------------------------------------------------------------


@router.post("/rules/{short_id}/promote")
def reject_promote(short_id: str):
    """Manual promote is not allowed. Lifecycle transitions are autonomous."""
    raise HTTPException(
        403,
        detail="Manual promote is not supported. "
        "Rule lifecycle transitions are driven autonomously by the quality flywheel.",
    )


@router.post("/rules/{short_id}/trust")
def reject_trust(short_id: str):
    """Manual trust is not allowed. Trust is earned through observed usefulness."""
    raise HTTPException(
        403,
        detail="Manual trust is not supported. "
        "Rules earn trusted status autonomously through observed usefulness.",
    )


@router.post("/rules/{short_id}/suppress")
def reject_suppress(short_id: str):
    """Manual suppress is not allowed. Suppression is evidence-driven."""
    raise HTTPException(
        403,
        detail="Manual suppress is not supported. "
        "Rules are suppressed autonomously when false-positive evidence accumulates.",
    )
