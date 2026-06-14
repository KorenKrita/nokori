from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter

from nokori.db import fetch_rules, fetch_shadow_rules, open_db
from nokori.search.engine import RetrievalEngine
from nokori.web.deps import get_config
from nokori.web.models import RetrieveRequest

router = APIRouter()


def _scored_to_dict(sr) -> dict:
    """Convert a ScoredResult to a dict with fielded evidence and eligibility."""
    rule = sr.rule

    rule_dict = {
        "id": rule.id,
        "short_id": rule.short_id,
        "schema_version": rule.schema_version,
        "rule_version": rule.rule_version,
        "status": rule.status,
        "severity": rule.severity,
        "trigger_canonical": rule.trigger_canonical,
        "action_instruction": rule.action_instruction,
        "project_scope": rule.project_scope,
        "project_id": rule.project_id,
        "quality_score": rule.quality_score,
        "observed_usefulness_score": rule.observed_usefulness_score,
        "false_positive_score": rule.false_positive_score,
    }

    # Fielded evidence (decision features)
    decision_features = {
        "trigger_idf_sum": sr.trigger_idf_sum,
        "trigger_coverage": sr.trigger_coverage,
        "distinct_trigger_terms": sr.distinct_trigger_terms,
        "strong_variant_phrase_hit": sr.strong_variant_phrase_hit,
        "weak_variant_recall_hit": sr.weak_variant_recall_hit,
        "required_concepts_match": sr.required_concepts_match,
        "excluded_context_hit": sr.excluded_context_hit,
        "excluded_context_override_passed": sr.excluded_context_override_passed,
        "action_only_match": sr.action_only_match,
        "search_only_match": sr.search_only_match,
        "embedding_only_match": sr.embedding_only_match,
        "matched_trigger_tokens": sorted(sr.matched_trigger_tokens),
        "matched_variant_tokens": sorted(sr.matched_variant_tokens),
        "matched_action_tokens": sorted(sr.matched_action_tokens),
        "matched_search_tokens": sorted(sr.matched_search_tokens),
        "decision_reason": sr.decision_reason,
    }
    if sr.cosine is not None:
        decision_features["embedding_cosine"] = sr.cosine
    if sr.embedding_profile_bucket is not None:
        decision_features["embedding_profile_bucket"] = sr.embedding_profile_bucket

    # Eligibility result
    level = sr.level  # already computed by retrieve pipeline
    # "cold" is an API-only display value indicating the rule did not reach any
    # injection level; it is NOT part of the InjectionLevel domain model.
    eligibility = {
        "decision": level if level else "cold",
        "eligible": level is not None and level != "cold",
        "reason": sr.decision_reason,
        "trigger_evidence_passed": sr.trigger_evidence_passed,
        "penalties": list(sr.decision_penalties),
    }

    return {
        "rule": rule_dict,
        "bm25_score": sr.bm25_score,
        "cosine": sr.cosine,
        "rrf_score": sr.rrf_score,
        "ranking_utility": sr.ranking_utility,
        "decision_reason": sr.decision_reason,
        "decision_features": decision_features,
        "eligibility": eligibility,
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
        formal = fetch_rules(db, statuses=("active", "trusted"), project_id=body.project_id)
        shadow = fetch_shadow_rules(db, project_id=body.project_id) if cfg.promotion_enabled else []

        engine = RetrievalEngine(cfg, db)
        result = engine.retrieve(body.prompt, formal, shadow, interaction="cli")
    finally:
        db.close()

    return {
        "data": {
            "hot": [_scored_to_dict(r) for r in result.hot],
            "warm": [_scored_to_dict(r) for r in result.warm],
            "shadow_hot": [_scored_to_dict(r) for r in result.shadow_hot],
            "shadow_warm": [_scored_to_dict(r) for r in result.shadow_warm],
            "embed_mode": result.embed_mode,
            "bm25_matches": result.bm25_matches,
        }
    }
