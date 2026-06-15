"""Coverage tests for cold/integrate.py uncovered paths.

Covers: _run_merge_planner, _apply_merge_side_effects, _apply_non_destructive_merge,
_get_rule_data_for_fingerprint.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from nokori.cold.integrate import (
    _apply_merge_side_effects,
    _apply_non_destructive_merge,
    _get_rule_data_for_fingerprint,
    _run_merge_planner,
    insert_rule_from_pipeline,
)
from nokori.db import SCHEMA_VERSION, Db, open_db
from nokori.policy import RUNTIME_POLICY_VERSION


@pytest.fixture
def db(tmp_path):
    database = open_db(tmp_path / "test.db")
    yield database
    database.close()


def _insert_rule(db: Db, rule_id: str, **overrides) -> None:
    defaults = dict(
        id=rule_id,
        short_id=rule_id[:8],
        schema_version=SCHEMA_VERSION,
        rule_version=1,
        status="active",
        severity="reminder",
        trigger_canonical="avoid force push",
        action_instruction="use lease instead",
        runtime_policy_version=RUNTIME_POLICY_VERSION,
        created_by_pipeline_version="1.0.0",
        source_origin="transcript_extraction",
        project_scope="global",
        domain_tags=json.dumps(["git"]),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    with db.transaction() as tx:
        tx.execute(f"INSERT INTO rules ({cols}) VALUES ({placeholders})", tuple(defaults.values()))


class TestRunMergePlanner:
    def test_no_existing_rules_returns_keep_both(self, db):
        rule_data = {"trigger_canonical": "completely unique trigger xyz"}
        with patch("nokori.cold.integrate.find_merge_neighbors", return_value=[]):
            op, info = _run_merge_planner(db, MagicMock(), rule_data, "model-1")
        assert op == "keep_both"
        assert "no existing overlap" in info.get("merge_rationale", "")

    def test_merge_planner_calls_llm_with_existing_rules(self, db):
        _insert_rule(db, "existing-1")
        rule_data = {"trigger_canonical": "avoid force push to shared branch"}
        existing = [{"id": "existing-1", "trigger_canonical": "avoid force push", "status": "active"}]

        llm = MagicMock()
        llm.call_raw.return_value = json.dumps({
            "relation_shape": "new_narrower",
            "new_rule_safety": "safe",
            "operation_safety": "safe",
            "quality_winner": "new",
            "operation": "keep_both",
            "confidence": 0.85,
            "reason": "new rule is more specific",
            "target_rule_ids": ["existing-1"],
        })

        with patch("nokori.cold.integrate.find_merge_neighbors", return_value=existing):
            op, info = _run_merge_planner(db, llm, rule_data, "model-1")
        assert op == "keep_both"
        assert llm.call_raw.called
        assert "merge_rationale" in info
        assert info.get("relation_shape") == "new_narrower"

    def test_merge_planner_circuit_breaker_propagates(self, db):
        from nokori.cold._llm_call import CircuitBreakerOpenError

        rule_data = {"trigger_canonical": "test"}
        existing = [{"id": "x", "trigger_canonical": "test", "status": "active"}]

        with (
            patch("nokori.cold.integrate.find_merge_neighbors", return_value=existing),
            patch("nokori.cold.integrate._call_llm_role", side_effect=CircuitBreakerOpenError("open")),
        ):
            with pytest.raises(CircuitBreakerOpenError):
                _run_merge_planner(db, MagicMock(), rule_data, "model-1")


class TestApplyMergeSideEffects:
    def test_replace_existing_archives_target(self, db):
        _insert_rule(db, "old-rule", status="active")
        merge_info = {
            "existing_rule": {
                "id": "old-rule",
                "rule_version": 1,
                "status": "active",
                "runtime_policy_version": RUNTIME_POLICY_VERSION,
            },
            "merge_rationale": "new rule is better",
        }
        _apply_merge_side_effects(db, "new-rule-id", "replace_existing", merge_info)

        row = db.fetchone("SELECT status, replacement_id FROM rules WHERE id = 'old-rule'")
        assert row["status"] == "archived"
        assert row["replacement_id"] == "new-rule-id"

    def test_suppress_existing_suppresses_target(self, db):
        _insert_rule(db, "sup-rule", status="trusted")
        merge_info = {
            "existing_rule": {
                "id": "sup-rule",
                "rule_version": 1,
                "status": "trusted",
                "runtime_policy_version": RUNTIME_POLICY_VERSION,
            },
            "merge_rationale": "contradicts new evidence",
        }
        _apply_merge_side_effects(db, "new-id", "suppress_existing", merge_info)

        row = db.fetchone("SELECT status, suppressed_at FROM rules WHERE id = 'sup-rule'")
        assert row["status"] == "suppressed"
        assert row["suppressed_at"] is not None

    def test_archive_existing_archives_target(self, db):
        _insert_rule(db, "arc-rule", status="active")
        merge_info = {
            "existing_rule": {
                "id": "arc-rule",
                "rule_version": 1,
                "status": "active",
                "runtime_policy_version": RUNTIME_POLICY_VERSION,
            },
            "merge_rationale": "obsolete",
        }
        _apply_merge_side_effects(db, "new-id", "archive_existing", merge_info)

        row = db.fetchone("SELECT status FROM rules WHERE id = 'arc-rule'")
        assert row["status"] == "archived"

    def test_cas_fails_on_stale_version(self, db):
        _insert_rule(db, "stale-rule", status="active")
        merge_info = {
            "existing_rule": {
                "id": "stale-rule",
                "rule_version": 99,
                "status": "active",
                "runtime_policy_version": RUNTIME_POLICY_VERSION,
            },
            "merge_rationale": "test",
        }
        _apply_merge_side_effects(db, "new-id", "replace_existing", merge_info)

        row = db.fetchone("SELECT status FROM rules WHERE id = 'stale-rule'")
        assert row["status"] == "active"

    def test_non_destructive_op_does_nothing(self, db):
        _insert_rule(db, "keep-rule", status="active")
        merge_info = {"existing_rule": {"id": "keep-rule"}}
        _apply_merge_side_effects(db, "new-id", "keep_both", merge_info)

        row = db.fetchone("SELECT status FROM rules WHERE id = 'keep-rule'")
        assert row["status"] == "active"


class TestApplyNonDestructiveMerge:
    def test_adds_new_variants(self, db):
        _insert_rule(db, "var-rule", trigger_variants=json.dumps([
            {"text": "force push", "kind": "strong_anchor", "requires_concepts": []},
        ]))
        _apply_non_destructive_merge(
            db,
            "var-rule",
            {"variants": [{"text": "force push --force", "kind": "weak_recall", "requires_concepts": []}]},
            "merge_into_existing",
            {},
        )
        row = db.fetchone("SELECT trigger_variants, rule_version FROM rules WHERE id = 'var-rule'")
        variants = json.loads(row["trigger_variants"])
        assert len(variants) == 2
        assert row["rule_version"] == 2

    def test_does_not_duplicate_existing_variants(self, db):
        _insert_rule(db, "dup-rule", trigger_variants=json.dumps([
            {"text": "force push", "kind": "strong_anchor", "requires_concepts": []},
        ]))
        _apply_non_destructive_merge(
            db,
            "dup-rule",
            {"variants": [{"text": "force push", "kind": "strong_anchor", "requires_concepts": []}]},
            "merge_into_existing",
            {},
        )
        row = db.fetchone("SELECT trigger_variants, rule_version FROM rules WHERE id = 'dup-rule'")
        variants = json.loads(row["trigger_variants"])
        assert len(variants) == 1
        assert row["rule_version"] == 1

    def test_adds_new_excluded_contexts(self, db):
        _insert_rule(db, "exc-rule", excluded_contexts=json.dumps([
            {"id": "exc-1", "label": "test", "patterns": ["test"]},
        ]))
        _apply_non_destructive_merge(
            db,
            "exc-rule",
            {"excluded_contexts": [{"id": "exc-2", "label": "deploy", "patterns": ["deploy"]}]},
            "update_existing_fields",
            {},
        )
        row = db.fetchone("SELECT excluded_contexts FROM rules WHERE id = 'exc-rule'")
        contexts = json.loads(row["excluded_contexts"])
        assert len(contexts) == 2

    def test_adds_new_near_miss_examples(self, db):
        _insert_rule(db, "nm-rule", near_miss_examples=json.dumps(["existing example"]))
        _apply_non_destructive_merge(
            db,
            "nm-rule",
            {"near_miss_examples": ["new example"]},
            "merge_into_existing",
            {},
        )
        row = db.fetchone("SELECT near_miss_examples, rule_version FROM rules WHERE id = 'nm-rule'")
        examples = json.loads(row["near_miss_examples"])
        assert len(examples) == 2
        assert row["rule_version"] == 2

    def test_no_update_when_no_new_data(self, db):
        _insert_rule(db, "noop-rule", trigger_variants=json.dumps([
            {"text": "existing", "kind": "strong_anchor", "requires_concepts": []},
        ]))
        _apply_non_destructive_merge(
            db,
            "noop-rule",
            {"variants": [], "excluded_contexts": [], "near_miss_examples": []},
            "merge_into_existing",
            {},
        )
        row = db.fetchone("SELECT rule_version FROM rules WHERE id = 'noop-rule'")
        assert row["rule_version"] == 1


class TestGetRuleDataForFingerprint:
    def test_returns_data_for_existing_rule(self, db):
        _insert_rule(db, "fp-rule", domain_tags=json.dumps(["python"]))
        result = _get_rule_data_for_fingerprint(db, "fp-rule")
        assert result is not None
        assert result["trigger_canonical"] == "avoid force push"
        assert result["domain_tags"] == ["python"]

    def test_returns_none_for_missing_rule(self, db):
        assert _get_rule_data_for_fingerprint(db, "nonexistent") is None
