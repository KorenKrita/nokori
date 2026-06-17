"""Tests for apply_merge_with_reeval extracted function."""

from __future__ import annotations

import json
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

    def test_revert_succeeds_on_synth_failure(self, db: Db):
        """Synth eval fails → revert restores pre-merge fields, version advances."""
        from nokori.cold.integrate import MergeRevalOutcome, apply_merge_with_reeval

        _insert_active_rule(db, "target-3", trigger="test trigger three")
        # Set initial variants/excluded_contexts so we can verify revert
        with db.transaction() as tx:
            tx.execute(
                "UPDATE rules SET trigger_variants = ?, excluded_contexts = ? WHERE id = ?",
                ('[{"text":"orig","kind":"weak_recall","requires_concepts":[]}]', '[{"id":"ctx_a","pattern":"ctx_a"}]', "target-3"),
            )

        existing_rule = {
            "id": "target-3",
            "trigger_variants": '[{"text":"orig","kind":"weak_recall","requires_concepts":[]}]',
            "excluded_contexts": '[{"id":"ctx_a","pattern":"ctx_a"}]',
        }
        rule_data = {
            "trigger_canonical": "test trigger three",
            "variants": [{"text": "new merged", "kind": "weak_recall", "requires_concepts": []}],
            "excluded_contexts": [{"id": "ctx_b", "pattern": "ctx_b"}],
        }

        # compile_rule succeeds but synth eval fails
        with patch("nokori.eval.synthetic.run_synthetic_eval", return_value=None):
            result = apply_merge_with_reeval(
                db,
                target_id="target-3",
                rule_data=rule_data,
                merge_op="merge_into_existing",
                merge_info={"existing_rule": existing_rule},
                eval_cases=[{"dummy": True}],
                global_adversarial_cases=None,
                idf_stats=None,
            )

        assert isinstance(result, MergeRevalOutcome)
        assert result.success is False
        assert result.rule_id is None

        # Verify DB: fields reverted, version advanced
        row = db.fetchone("SELECT trigger_variants, excluded_contexts, rule_version FROM rules WHERE id = ?", ("target-3",))
        assert row is not None
        variants_after = json.loads(row["trigger_variants"])
        excluded_after = json.loads(row["excluded_contexts"])
        assert variants_after == [{"text": "orig", "kind": "weak_recall", "requires_concepts": []}]
        assert excluded_after == [{"id": "ctx_a", "pattern": "ctx_a"}]
        # rule_version: started at 1, merge bumps +1, revert bumps +1 = 3
        assert row["rule_version"] == 3

    def test_revert_cas_failure_leaves_merged_state(self, db: Db):
        """CAS failure (concurrent modification) → rule retains merged state.

        Option A behavior locked: CAS failure = no-op, rule stays merged.
        See 06-17-revert-cas-test PRD.
        """
        from nokori.cold.integrate import MergeRevalOutcome, apply_merge_with_reeval

        _insert_active_rule(db, "target-4", trigger="cas trigger")
        with db.transaction() as tx:
            tx.execute(
                "UPDATE rules SET trigger_variants = ?, excluded_contexts = ? WHERE id = ?",
                ('[{"text":"orig","kind":"weak_recall","requires_concepts":[]}]', '[{"id":"ctx_orig","pattern":"ctx_orig"}]', "target-4"),
            )

        existing_rule = {
            "id": "target-4",
            "trigger_variants": '[{"text":"orig","kind":"weak_recall","requires_concepts":[]}]',
            "excluded_contexts": '[{"id":"ctx_orig","pattern":"ctx_orig"}]',
        }
        rule_data = {
            "trigger_canonical": "cas trigger",
            "variants": [{"text": "merged variant", "kind": "weak_recall", "requires_concepts": []}],
            "excluded_contexts": [{"id": "ctx_merged", "pattern": "ctx_merged"}],
        }

        # Simulate concurrent modification: after merge applies but before revert,
        # bump rule_version so the revert CAS WHERE clause won't match.
        original_revert = None

        def _intercept_revert(db_arg, target_id, merged_row, *args, **kwargs):
            # Bump rule_version to simulate concurrent modification
            with db_arg.transaction() as tx:
                tx.execute("UPDATE rules SET rule_version = rule_version + 10 WHERE id = ?", (target_id,))
            # Now call the real _revert_merge — CAS will fail (rowcount=0)
            return original_revert(db_arg, target_id, merged_row, *args, **kwargs)

        import nokori.cold.integrate as integrate_mod
        original_revert = integrate_mod._revert_merge

        with patch("nokori.eval.synthetic.run_synthetic_eval", return_value=None), \
             patch("nokori.cold.integrate._revert_merge", side_effect=_intercept_revert):
            result = apply_merge_with_reeval(
                db,
                target_id="target-4",
                rule_data=rule_data,
                merge_op="merge_into_existing",
                merge_info={"existing_rule": existing_rule},
                eval_cases=[{"dummy": True}],
                global_adversarial_cases=None,
                idf_stats=None,
            )

        assert isinstance(result, MergeRevalOutcome)
        assert result.success is False

        # Verify DB: rule retains MERGED state (revert failed, no-op)
        row = db.fetchone("SELECT trigger_variants, excluded_contexts FROM rules WHERE id = ?", ("target-4",))
        assert row is not None
        variants_after = json.loads(row["trigger_variants"])
        excluded_after = json.loads(row["excluded_contexts"])
        # Should contain the merged values (NOT reverted to orig)
        assert variants_after == [
            {"text": "orig", "kind": "weak_recall", "requires_concepts": []},
            {"text": "merged variant", "kind": "weak_recall", "requires_concepts": []},
        ]
        assert excluded_after == [
            {"id": "ctx_orig", "pattern": "ctx_orig"},
            {"id": "ctx_merged", "pattern": "ctx_merged"},
        ]

    def test_interface_returns_dataclass(self):
        from nokori.cold.integrate import MergeRevalOutcome

        outcome = MergeRevalOutcome(success=True, rule_id="abc")
        assert outcome.success is True
        assert outcome.rule_id == "abc"
