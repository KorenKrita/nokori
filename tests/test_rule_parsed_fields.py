"""Tests verifying that Rule fields are pre-parsed at construction from DB.

After this refactor, callers should access rule.concepts as list[dict]
directly, without needing loads_json().
"""

import pytest

from nokori.config import Config
from nokori.db import dumps_json, fetch_rules, open_db, row_to_rule
from nokori.db.queries import SEARCH_RULE_COLUMNS, row_to_search_rule


@pytest.fixture
def db_with_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    concepts = [{"id": "deploy", "label": "deploy", "aliases": []}]
    groups = [{"id": "primary", "all_of": ["deploy"]}]
    excluded = [{"id": "revert", "scope": "global", "patterns": ["revert"], "match_mode": "phrase"}]
    variants = [{"text": "deploy db", "kind": "strong_anchor", "requires_concepts": ["deploy"]}]
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, last_rewritten_by_role, "
            "status, severity, trigger_canonical, trigger_canonical_zh, "
            "concepts, concept_aliases, required_concept_groups, excluded_contexts, "
            "non_generalization_boundaries, near_miss_examples, "
            "trigger_variants, trigger_variants_zh, search_terms, "
            "action_instruction, action_instruction_zh, "
            "allowed_behavior, forbidden_behavior, "
            "domain_tags, tool_tags, path_patterns, language_hints, "
            "transcript_ref, evidence_quotes, "
            "quality_score, evidence_support_score, specificity_score, retrieval_readiness_score, "
            "observed_usefulness_score, plausible_usefulness_score, false_positive_score, harmful_score, "
            "source_origin, activation_origin, first_observed_useful_at, "
            "trusted_at, suppressed_at, project_scope, project_id, "
            "archived_reason, replacement_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "rule-1", "rule01", 6, 1,
                "1.0.0", "1.0.0", None,
                "active", "reminder",
                "deploy database", None,
                dumps_json(concepts),
                dumps_json(["deploy_alias"]),
                dumps_json(groups),
                dumps_json(excluded),
                dumps_json(["no generalize past deploy"]),
                dumps_json([]),
                dumps_json(variants),
                dumps_json([]),
                dumps_json({"en": ["deploy"], "zh": []}),
                "check first", None,
                dumps_json([]), dumps_json([]),
                dumps_json([]), dumps_json([]), dumps_json([]),
                dumps_json(["en", "zh"]),
                None, dumps_json([]),
                0.8, 0.7, 0.6, 0.5,
                0.0, 0.0, 0.0, 0.0,
                "transcript_extraction", None, None,
                None, None, "global", None,
                None, None,
                "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
            ),
        )
    yield db
    db.close()


class TestRuleParsedFields:
    def test_concepts_is_list_after_row_to_rule(self, db_with_rule):
        row = db_with_rule.fetchone("SELECT * FROM rules WHERE id = 'rule-1'")
        rule = row_to_rule(row)
        assert isinstance(rule.concepts, list)
        assert rule.concepts[0]["id"] == "deploy"

    def test_required_concept_groups_is_list(self, db_with_rule):
        row = db_with_rule.fetchone("SELECT * FROM rules WHERE id = 'rule-1'")
        rule = row_to_rule(row)
        assert isinstance(rule.required_concept_groups, list)
        assert rule.required_concept_groups[0]["all_of"] == ["deploy"]

    def test_excluded_contexts_is_list(self, db_with_rule):
        row = db_with_rule.fetchone("SELECT * FROM rules WHERE id = 'rule-1'")
        rule = row_to_rule(row)
        assert isinstance(rule.excluded_contexts, list)
        assert rule.excluded_contexts[0]["id"] == "revert"

    def test_trigger_variants_is_list(self, db_with_rule):
        row = db_with_rule.fetchone("SELECT * FROM rules WHERE id = 'rule-1'")
        rule = row_to_rule(row)
        assert isinstance(rule.trigger_variants, list)
        assert rule.trigger_variants[0]["text"] == "deploy db"

    def test_language_hints_is_list(self, db_with_rule):
        row = db_with_rule.fetchone("SELECT * FROM rules WHERE id = 'rule-1'")
        rule = row_to_rule(row)
        assert isinstance(rule.language_hints, list)
        assert "en" in rule.language_hints

    def test_concept_aliases_is_list(self, db_with_rule):
        row = db_with_rule.fetchone("SELECT * FROM rules WHERE id = 'rule-1'")
        rule = row_to_rule(row)
        assert isinstance(rule.concept_aliases, list)
        assert "deploy_alias" in rule.concept_aliases

    def test_non_generalization_boundaries_is_list(self, db_with_rule):
        row = db_with_rule.fetchone("SELECT * FROM rules WHERE id = 'rule-1'")
        rule = row_to_rule(row)
        assert isinstance(rule.non_generalization_boundaries, list)

    def test_search_terms_is_dict(self, db_with_rule):
        row = db_with_rule.fetchone("SELECT * FROM rules WHERE id = 'rule-1'")
        rule = row_to_rule(row)
        assert isinstance(rule.search_terms, dict)
        assert rule.search_terms["en"] == ["deploy"]

    def test_search_rule_row_skips_heavy_json(self, db_with_rule):
        row = db_with_rule.fetchone(f"SELECT {SEARCH_RULE_COLUMNS} FROM rules WHERE id = 'rule-1'")
        rule = row_to_search_rule(row)
        assert rule.concepts[0]["id"] == "deploy"
        assert rule.search_terms["en"] == ["deploy"]
        # Slim path leaves archival/review fields at defaults.
        assert rule.evidence_quotes == []
        assert rule.near_miss_examples == []
        assert rule.tool_tags == []

    def test_fetch_rules_for_retrieval(self, db_with_rule):
        rules = fetch_rules(db_with_rule, statuses=("active",), for_retrieval=True)
        assert len(rules) == 1
        assert rules[0].id == "rule-1"
        assert rules[0].trigger_canonical
        assert rules[0].evidence_quotes == []
