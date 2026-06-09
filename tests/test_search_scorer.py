"""Integration tests for SearchScorer — the internal scoring seam of RetrievalEngine."""

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.models import Rule, ScoredResult
from nokori.search.scorer import SearchScorer


def _make_rule(rule_id: str = "scorer-1", trigger: str = "deploy database migration") -> Rule:
    return Rule(
        id=rule_id,
        short_id=rule_id[:6],
        schema_version=6,
        rule_version=1,
        created_by_pipeline_version="1.0.0",
        runtime_policy_version="1.0.0",
        last_rewritten_by_role=None,
        status="active",
        severity="reminder",
        trigger_canonical=trigger,
        action_instruction="check migration status first",
        concepts=[{"id": "deploy", "label": "deploy", "aliases": [{"text": "deploy", "strength": "strong"}], "match_mode": "phrase", "required": True}],
        required_concept_groups=[{"id": "primary", "all_of": ["deploy"]}],
        trigger_variants=[{"text": "deploy database", "kind": "strong_anchor", "requires_concepts": ["deploy"]}],
        search_terms={"en": ["deploy", "migration", "database"], "zh": []},
        source_origin="transcript_extraction",
        project_scope="global",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def scorer(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        yield SearchScorer(cfg, db)
    finally:
        db.close()


class TestSearchScorer:
    def test_score_returns_scored_results_for_matching_rules(self, scorer):
        rules = [_make_rule()]
        results = scorer.score("deploy database migration now", rules)
        assert len(results) > 0
        assert all(isinstance(r, ScoredResult) for r in results)

    def test_score_returns_empty_for_no_rules(self, scorer):
        results = scorer.score("deploy database", [])
        assert results == []

    def test_score_returns_empty_for_unrelated_prompt(self, scorer):
        rule = Rule(
            id="unrel-1", short_id="unrel1", schema_version=6, rule_version=1,
            created_by_pipeline_version="1.0.0", runtime_policy_version="1.0.0",
            last_rewritten_by_role=None, status="active", severity="reminder",
            trigger_canonical="kubernetes pod restart",
            action_instruction="check pod health",
            concepts=[{"id": "k8s", "label": "kubernetes", "aliases": [{"text": "kubernetes", "strength": "strong"}], "match_mode": "phrase", "required": True}],
            required_concept_groups=[{"id": "primary", "all_of": ["k8s"]}],
            trigger_variants=[{"text": "pod restart", "kind": "strong_anchor", "requires_concepts": ["k8s"]}],
            search_terms={"en": ["kubernetes", "pod", "restart"], "zh": []},
            source_origin="transcript_extraction", project_scope="global",
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        )
        results = scorer.score("review my CSS styling", [rule])
        assert results == []

    def test_bm25_score_populated(self, scorer):
        rules = [_make_rule()]
        results = scorer.score("deploy database migration now", rules)
        assert any(r.bm25_score > 0 for r in results)

    def test_rrf_score_populated(self, scorer):
        rules = [_make_rule()]
        results = scorer.score("deploy database migration", rules)
        assert any(r.rrf_score > 0 for r in results)
