from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter

from nokori.db import fetch_rules, fetch_shadow_rules, open_db
from nokori.search.retrieve import retrieve_formal_and_shadow
from nokori.web.deps import get_config
from nokori.web.models import RetrieveRequest, RuleOut

router = APIRouter()


def _scored_to_dict(sr) -> dict:
    rule_dict = RuleOut(
        id=sr.rule.id,
        short_id=sr.rule.short_id,
        trigger_text=sr.rule.trigger_text,
        trigger_variants=sr.rule.trigger_variants,
        trigger_variants_zh=sr.rule.trigger_variants_zh,
        search_terms=sr.rule.search_terms,
        behavior=sr.rule.behavior,
        action=sr.rule.action,
        rationale=sr.rule.rationale,
        source_type=sr.rule.source_type,
        confidence=sr.rule.confidence,
        status=sr.rule.status,
        evidence_score=sr.rule.evidence_score,
        evidence_log=sr.rule.evidence_log,
        hit_count=sr.rule.hit_count,
        last_hit=sr.rule.last_hit,
        shadow_hit_count=sr.rule.shadow_hit_count,
        promotion_evidence=sr.rule.promotion_evidence,
        project_scope=sr.rule.project_scope,
        project_id=sr.rule.project_id,
        superseded_by=sr.rule.superseded_by,
        archived_reason=sr.rule.archived_reason,
        created_at=sr.rule.created_at,
        updated_at=sr.rule.updated_at,
    ).model_dump()
    return {
        "rule": rule_dict,
        "bm25_score": sr.bm25_score,
        "cosine": sr.cosine,
        "rrf_score": sr.rrf_score,
        "matched_tokens": list(sr.matched_tokens),
        "has_trigger_variant_match": sr.has_trigger_variant_match,
        "retrieval_hot": sr.retrieval_hot,
    }


@router.post("/retrieve")
def retrieve(body: RetrieveRequest):
    cfg = get_config()

    if not body.use_embedding:
        cfg = replace(cfg, embed_enabled=False)

    if not body.prompt.strip():
        return {
            "data": {
                "hot": [],
                "warm": [],
                "shadow_hot": [],
                "shadow_warm": [],
                "embed_mode": "off",
                "bm25_matches": 0,
            }
        }

    db = open_db(cfg.db_path)
    try:
        formal = fetch_rules(
            db, statuses=("active", "dormant"), project_id=body.project_id
        )
        shadow = (
            fetch_shadow_rules(db, project_id=body.project_id)
            if cfg.promotion_enabled
            else []
        )

        result, shadow_hot, shadow_warm = retrieve_formal_and_shadow(
            body.prompt, formal, shadow, db, cfg, interaction="cli"
        )
    finally:
        db.close()

    return {
        "data": {
            "hot": [_scored_to_dict(r) for r in result.hot],
            "warm": [_scored_to_dict(r) for r in result.warm],
            "shadow_hot": [_scored_to_dict(r) for r in shadow_hot],
            "shadow_warm": [_scored_to_dict(r) for r in shadow_warm],
            "embed_mode": result.embed_mode,
            "bm25_matches": result.bm25_matches,
        }
    }
