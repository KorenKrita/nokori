"""Tests for apply_merge_with_reeval extracted function."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nokori.db import Db, open_db


@pytest.fixture()
def db(tmp_path: Path) -> Db:
    return open_db(tmp_path / "test.db")


def _insert_active_rule(db: Db, rule_id: str, trigger: str = "test trigger"):
    import hashlib
    short = hashlib.sha256(rule_id.encode()).hexdigest()[:6]
    now = "2026-01-01T00:00:00Z"
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "trigger_variants, excluded_contexts, concepts, required_concept_groups, "
            "near_miss_examples, "
            "source_origin, status, severity, "
            "project_scope, created_at, updated_at) "
            "VALUES (?,?,7,1,'v1','v1',?,?,'[]','[]','[]','[]','[]',?,?,?,?,?,?)",
            (rule_id, short, trigger, "test action",
             "transcript_extraction", "active", "reminder",
             "global", now, now),
        )


class TestApplyMergeWithReeval:
    def test_returns_success_when_no_fields_changed(self, db: Db):
        from nokori.cold.integrate import MergeRevalOutcome, apply_merge_with_reeval

        _insert_active_rule(db, "target-1")
        rule_data = {"trigger_canonical": "test"}

        result = apply_merge_with_reeval(
            db,
            target_id="target-1",
            rule_data=rule_data,
            merge_op="merge_into_existing",
            merge_info={"existing_rule": {"id": "target-1"}},
            eval_cases=[],
            global_adversarial_cases=None,
            idf_stats=None,
        )

        assert isinstance(result, MergeRevalOutcome)
        assert result.success is True
        assert result.rule_id == "target-1"

    def test_returns_failure_when_recompilation_fails(self, db: Db):
        from nokori.cold.integrate import MergeRevalOutcome, apply_merge_with_reeval
        from nokori.matcher.compiler import CompilationError

        _insert_active_rule(db, "target-2")
        rule_data = {
            "trigger_canonical": "test",
            "variants": [{"text": "new variant", "kind": "strong_anchor", "requires_concepts": []}],
        }

        existing_rule = {
            "id": "target-2",
            "trigger_variants": "[]",
            "excluded_contexts": "[]",
        }
        with patch("nokori.cold.integrate.compile_rule", side_effect=CompilationError("bad")):
            result = apply_merge_with_reeval(
                db,
                target_id="target-2",
                rule_data=rule_data,
                merge_op="merge_into_existing",
                merge_info={"existing_rule": existing_rule},
                eval_cases=[],
                global_adversarial_cases=None,
                idf_stats=None,
            )

        assert isinstance(result, MergeRevalOutcome)
        assert result.success is False
        assert result.rule_id is None

    def test_interface_returns_dataclass(self):
        from nokori.cold.integrate import MergeRevalOutcome

        outcome = MergeRevalOutcome(success=True, rule_id="abc")
        assert outcome.success is True
        assert outcome.rule_id == "abc"
