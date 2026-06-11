"""Tests verifying that hot-path retrieval uses the same matcher as cold-path evaluation.

The key property: if a rule matches a prompt via matcher.evaluate_match(), the engine
should produce a non-empty injection result; if it doesn't match, the engine should not inject.
"""

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.matcher.compiler import compile_rule
from nokori.matcher.runtime import evaluate_match
from nokori.models import Rule
from nokori.search.engine import RetrievalEngine
from nokori.search.evidence import trigger_data_for_rule


def _make_rule(
    rule_id: str = "match-1",
    *,
    trigger: str = "force push shared branch",
    concepts: list | None = None,
    groups: list | None = None,
    excluded_contexts: list | None = None,
    status: str = "trusted",
) -> Rule:
    if concepts is None:
        concepts = [{"id": "force_push", "label": "force push", "aliases": [{"text": "force push", "strength": "strong"}, {"text": "git push --force", "strength": "strong"}], "match_mode": "phrase", "required": True}]
    if groups is None:
        groups = [{"id": "primary", "all_of": ["force_push"]}]
    if excluded_contexts is None:
        excluded_contexts = [{"id": "revert_context", "scope": "global", "patterns": ["revert.*force push"], "match_mode": "regex"}]
    return Rule(
        id=rule_id,
        short_id=rule_id[:6],
        schema_version=6,
        rule_version=1,
        created_by_pipeline_version="1.0.0",
        runtime_policy_version="1.0.0",
        last_rewritten_by_role=None,
        status=status,
        severity="reminder",
        trigger_canonical=trigger,
        action_instruction="use git lease instead",
        concepts=concepts,
        required_concept_groups=groups,
        excluded_contexts=excluded_contexts,
        trigger_variants=[{"text": "force push", "kind": "strong_anchor", "requires_concepts": ["force_push"]}, {"text": "git push --force", "kind": "strong_anchor", "requires_concepts": ["force_push"]}],
        search_terms={"en": ["force", "push", "git"], "zh": []},
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


class TestMatcherAgreement:
    """Engine retrieval agrees with standalone matcher evaluation."""

    def test_matching_prompt_produces_injection(self, engine):
        rule = _make_rule()
        result = engine.retrieve("git push --force origin main", [rule], [])
        injected_ids = {r.rule.id for r in result.hot + result.warm}
        assert "match-1" in injected_ids

    def test_excluded_context_blocks_injection(self, engine):
        """Excluded context hit → COLD (not injected) for trusted rules."""
        rule = _make_rule(status="trusted")
        result = engine.retrieve("how to revert a force push safely", [rule], [])
        injected_ids = {r.rule.id for r in result.hot + result.warm}
        assert "match-1" not in injected_ids

    def test_engine_and_matcher_agree_on_match(self, engine):
        """If compile_rule + evaluate_match says it matches, engine should inject."""
        rule = _make_rule()
        prompt = "force push shared branch to remote"

        trigger_data = trigger_data_for_rule(rule)
        assert trigger_data is not None
        compiled = compile_rule(trigger_data, search_terms=rule.search_terms)
        match = evaluate_match(compiled, prompt)
        assert match.required_concepts_match

        result = engine.retrieve(prompt, [rule], [])
        injected_ids = {r.rule.id for r in result.hot + result.warm}
        assert rule.id in injected_ids

    def test_engine_and_matcher_agree_on_exclusion_for_active(self, engine):
        """If excluded_context fires on an active rule, engine should not inject."""
        rule = _make_rule(status="active")
        prompt = "revert the force push that broke staging"

        trigger_data = trigger_data_for_rule(rule)
        compiled = compile_rule(trigger_data, search_terms=rule.search_terms)
        match = evaluate_match(compiled, prompt)
        assert match.excluded_context_hits

        result = engine.retrieve(prompt, [rule], [])
        injected_ids = {r.rule.id for r in result.hot + result.warm}
        assert rule.id not in injected_ids


class TestIdfColdStart:
    """IDF cold-start fallback uses unfiltered rules when no active/trusted exist."""

    def test_candidate_only_pool_uses_cold_start_fallback(self, engine):
        """When all rules are candidates (no active/trusted), IDF still computes via shadow path."""
        rule = _make_rule(status="candidate")
        result = engine.retrieve("force push shared branch", [], [rule])
        assert rule.id in {r.rule.id for r in result.shadow_hot + result.shadow_warm}
