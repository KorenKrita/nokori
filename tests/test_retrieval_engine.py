"""Integration tests for RetrievalEngine — verifies behavior through the public interface."""

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.models import Rule
from nokori.search.engine import RetrievalEngine, RetrievalResult


def _make_rule(
    rule_id: str,
    *,
    status: str = "active",
    severity: str = "reminder",
    trigger: str = "deploy database migration",
    action: str = "run migration check first",
    schema_version: int = 6,
) -> Rule:
    return Rule(
        id=rule_id,
        short_id=rule_id[:6],
        schema_version=schema_version,
        rule_version=1,
        created_by_pipeline_version="1.0.0",
        runtime_policy_version="1.0.0",
        last_rewritten_by_role=None,
        status=status,
        severity=severity,
        trigger_canonical=trigger,
        action_instruction=action,
        concepts=[{"id": "deploy", "label": "deploy", "aliases": [{"text": "deploy", "strength": "strong"}], "match_mode": "phrase", "required": True}],
        required_concept_groups=[{"id": "primary", "all_of": ["deploy"]}],
        trigger_variants=[{"text": "deploy database", "kind": "strong_anchor", "requires_concepts": ["deploy"]}],
        search_terms={"en": ["deploy", "migration"], "zh": []},
        source_origin="transcript_extraction",
        project_scope="global",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    yield RetrievalEngine(cfg, db)
    db.close()


class TestEmptyPool:
    def test_no_rules_returns_empty_result(self, engine):
        result = engine.retrieve("deploy the database", [], [])
        assert result.hot == []
        assert result.warm == []
        assert result.shadow_hot == []
        assert result.shadow_warm == []
        assert result.bm25_matches == 0
        assert result.embed_mode == "off"


class TestFormalPoolTiering:
    def test_trusted_rule_with_strong_evidence_gets_hot(self, engine):
        rule = _make_rule("trusted-1", status="trusted")
        result = engine.retrieve("deploy database migration now", [rule], [])
        assert any(r.rule.id == "trusted-1" for r in result.hot)

    def test_active_rule_without_observed_useful_gets_warm(self, engine):
        rule = _make_rule("active-1", status="active")
        result = engine.retrieve("deploy database migration now", [rule], [])
        hot_ids = {r.rule.id for r in result.hot}
        warm_ids = {r.rule.id for r in result.warm}
        assert "active-1" not in hot_ids
        assert "active-1" in warm_ids

    def test_active_rule_with_observed_useful_and_strong_evidence_gets_hot(self, engine):
        from dataclasses import replace
        rule = replace(_make_rule("active-useful"), first_observed_useful_at="2026-01-01T00:00:00Z")
        result = engine.retrieve("deploy database migration now", [rule], [])
        assert any(r.rule.id == "active-useful" for r in result.hot)


class TestShadowPoolIsolation:
    def test_candidate_rule_appears_in_shadow_not_formal(self, engine):
        candidate = _make_rule("cand-1", status="candidate")
        result = engine.retrieve("deploy database migration now", [], [candidate])
        assert not result.hot
        assert not result.warm
        shadow_ids = {r.rule.id for r in result.shadow_hot + result.shadow_warm}
        assert "cand-1" in shadow_ids

    def test_suppressed_rule_appears_in_shadow_not_formal(self, engine):
        suppressed = _make_rule("supp-1", status="suppressed")
        result = engine.retrieve("deploy database migration now", [], [suppressed])
        assert not result.hot
        assert not result.warm
        shadow_ids = {r.rule.id for r in result.shadow_hot + result.shadow_warm}
        assert "supp-1" in shadow_ids


class TestOverlapDedup:
    def test_rule_in_both_pools_only_counts_in_formal(self, engine):
        """A rule present in both formal and shadow pools is excluded from shadow scoring."""
        shared = _make_rule("shared-1", status="active")
        result = engine.retrieve("deploy database migration now", [shared], [shared])
        formal_ids = {r.rule.id for r in result.hot + result.warm}
        shadow_ids = {r.rule.id for r in result.shadow_hot + result.shadow_warm}
        assert "shared-1" in formal_ids
        assert "shared-1" not in shadow_ids


class TestInsufficientEvidence:
    def test_unrelated_prompt_does_not_inject_rule(self, engine):
        """A prompt with zero matching tokens for the rule's trigger produces no injection."""
        rule = _make_rule("unrelated-1", status="trusted", trigger="kubernetes pod restart")
        result = engine.retrieve("please review my CSS styling", [rule], [])
        all_ids = {r.rule.id for r in result.hot + result.warm}
        assert "unrelated-1" not in all_ids


class TestIdfCacheDedup:
    def test_same_engine_does_not_write_idf_stats_twice(self, engine):
        """IDF stats are written once per pool version; subsequent calls skip the write."""
        rule = _make_rule("cache-1", status="trusted")
        engine.retrieve("deploy database migration", [rule], [])
        engine.retrieve("deploy database migration again", [rule], [])

        rows = engine.db.fetchall(
            "SELECT COUNT(*) as cnt FROM trigger_idf_stats"
        )
        assert rows[0]["cnt"] == 1


class TestSelectionBudget:
    def test_excess_rules_spill_from_warm_when_budget_exhausted(self, tmp_path, monkeypatch):
        """When char budget is tiny, extra eligible rules are excluded from warm."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_MAX_INJECTION_CHARS", "50")
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            engine = RetrievalEngine(cfg, db)
            rules = [
                _make_rule(f"budget-{i}", status="trusted", action="x" * 100)
                for i in range(5)
            ]
            result = engine.retrieve("deploy database migration now", rules, [])
            total_injected = len(result.hot) + len(result.warm)
            assert 1 <= total_injected < 5
        finally:
            db.close()
