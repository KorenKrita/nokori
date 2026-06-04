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


def _rule_to_response(rule) -> dict:
    """Convert a Rule dataclass to a RuleResponse dict with all structured fields."""
    return RuleResponse(
        id=rule.id,
        short_id=rule.short_id,
        schema_version=rule.schema_version,
        rule_version=rule.rule_version,
        created_by_pipeline_version=rule.created_by_pipeline_version,
        runtime_policy_version=rule.runtime_policy_version,
        last_rewritten_by_role=rule.last_rewritten_by_role,
        status=rule.status,
        severity=rule.severity,
        trigger_canonical=rule.trigger_canonical,
        trigger_canonical_zh=rule.trigger_canonical_zh,
        concepts=loads_json(rule.concepts, []),
        required_concept_groups=loads_json(rule.required_concept_groups, []),
        excluded_contexts=loads_json(rule.excluded_contexts, []),
        near_miss_examples=rule.near_miss_examples,
        trigger_variants=rule.trigger_variants,
        trigger_variants_zh=rule.trigger_variants_zh,
        search_terms=rule.search_terms,
        action_instruction=rule.action_instruction,
        action_instruction_zh=rule.action_instruction_zh,
        allowed_behavior=rule.allowed_behavior,
        forbidden_behavior=rule.forbidden_behavior,
        domain_tags=rule.domain_tags,
        tool_tags=rule.tool_tags,
        path_patterns=rule.path_patterns,
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
    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(
            db,
            statuses=statuses,
            project_id=project,
            global_only=(scope == "global"),
            project_scope_exact=(project is not None and scope != "global"),
        )
    finally:
        db.close()

    if source_origin:
        allowed_origins = set(source_origin.split(","))
        rules = [r for r in rules if r.source_origin in allowed_origins]

    if severity:
        allowed_severities = set(severity.split(","))
        rules = [r for r in rules if r.severity in allowed_severities]

    total = len(rules)
    start = (page - 1) * per_page
    page_rules = rules[start : start + per_page]
    return {
        "data": [_rule_to_response(r) for r in page_rules],
        "meta": {"total": total, "page": page, "per_page": per_page},
    }


@router.get("/rules/{short_id}")
def show_rule(short_id: str):
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
    finally:
        db.close()
    if rule is None:
        raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
    return {"data": _rule_to_response(rule)}


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


@router.post("/rules/{short_id}/promote", dependencies=[Depends(require_write_auth)])
def reject_promote(short_id: str):
    """Manual promote is not allowed. Lifecycle transitions are autonomous."""
    raise HTTPException(
        403,
        detail="Manual promote is not supported. "
        "Rule lifecycle transitions are driven autonomously by the quality flywheel.",
    )


@router.post("/rules/{short_id}/trust", dependencies=[Depends(require_write_auth)])
def reject_trust(short_id: str):
    """Manual trust is not allowed. Trust is earned through observed usefulness."""
    raise HTTPException(
        403,
        detail="Manual trust is not supported. "
        "Rules earn trusted status autonomously through observed usefulness.",
    )


@router.post("/rules/{short_id}/suppress", dependencies=[Depends(require_write_auth)])
def reject_suppress(short_id: str):
    """Manual suppress is not allowed. Suppression is evidence-driven."""
    raise HTTPException(
        403,
        detail="Manual suppress is not supported. "
        "Rules are suppressed autonomously when false-positive evidence accumulates.",
    )
