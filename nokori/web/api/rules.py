from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from nokori.db import (
    archive_rule,
    dumps_json,
    fetch_rule_by_short_id,
    fetch_rules,
    open_db,
)
from nokori.utils.time import now_iso
from nokori.web.deps import get_config
from nokori.web.models import RuleEdit, RuleOut

router = APIRouter()


def _rule_to_dict(rule) -> dict:
    return RuleOut(
        id=rule.id,
        short_id=rule.short_id,
        trigger_text=rule.trigger_text,
        trigger_variants=rule.trigger_variants,
        search_terms=rule.search_terms,
        behavior=rule.behavior,
        action=rule.action,
        rationale=rule.rationale,
        source_type=rule.source_type,
        confidence=rule.confidence,
        status=rule.status,
        evidence_score=rule.evidence_score,
        evidence_log=rule.evidence_log,
        hit_count=rule.hit_count,
        last_hit=rule.last_hit,
        shadow_hit_count=rule.shadow_hit_count,
        promotion_evidence=rule.promotion_evidence,
        project_scope=rule.project_scope,
        project_id=rule.project_id,
        superseded_by=rule.superseded_by,
        archived_reason=rule.archived_reason,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    ).model_dump()


@router.get("/rules")
def list_rules(
    status: str | None = Query(None),
    source_type: str | None = Query(None),
    project: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    cfg = get_config()
    statuses = tuple(status.split(",")) if status else None
    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(db, statuses=statuses, project_id=project)
    finally:
        db.close()

    if source_type:
        allowed_types = set(source_type.split(","))
        rules = [r for r in rules if r.source_type in allowed_types]

    total = len(rules)
    start = (page - 1) * per_page
    page_rules = rules[start : start + per_page]
    return {
        "data": [_rule_to_dict(r) for r in page_rules],
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
    return {"data": _rule_to_dict(rule)}


@router.patch("/rules/{short_id}")
def edit_rule(short_id: str, body: RuleEdit):
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")

        updates: list[tuple[str, object]] = []
        if body.trigger_text is not None:
            updates.append(("trigger_text", body.trigger_text))
        if body.action is not None:
            updates.append(("action", body.action))
        if body.rationale is not None:
            updates.append(("rationale", body.rationale))
        if body.confidence is not None:
            updates.append(("confidence", body.confidence))
        if body.status is not None:
            updates.append(("status", body.status))
        if body.trigger_variants is not None:
            updates.append(("trigger_variants", dumps_json(body.trigger_variants)))
        if body.search_terms is not None:
            updates.append(("search_terms", dumps_json(body.search_terms)))

        if not updates:
            return {"data": _rule_to_dict(rule)}

        now = now_iso()
        sets = ", ".join(f"{col} = ?" for col, _ in updates)
        params: list = [val for _, val in updates]
        params.extend([now, rule.id])
        with db.transaction() as tx:
            tx.execute(
                f"UPDATE rules SET {sets}, updated_at = ? WHERE id = ?",
                tuple(params),
            )
        updated = fetch_rule_by_short_id(db, short_id)
        return {"data": _rule_to_dict(updated)}
    finally:
        db.close()


@router.post("/rules/{short_id}/dismiss")
def dismiss_rule(short_id: str):
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, short_id)
        if rule is None:
            raise HTTPException(404, detail=f"no rule with short_id {short_id!r}")
        if rule.status == "archived":
            return {"data": _rule_to_dict(rule)}
        archive_rule(db, rule.id, "web_dismiss", now_iso())
        updated = fetch_rule_by_short_id(db, short_id)
        return {"data": _rule_to_dict(updated)}
    finally:
        db.close()
