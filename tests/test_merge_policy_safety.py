"""Safety gate tests for nokori.merge.policy (section 8.4 safety invariants).

Covers:
- operation_safety="unsafe_new_strategy" handling
- validate_merge_transaction gate enforcement
- Lineage recording during merges
- Low confidence rejection for destructive relations
- Archived fingerprint conflict blocking
"""

from __future__ import annotations

import pytest

from nokori.db import open_db
from nokori.merge.policy import (
    MergeDecision,
    apply_merge_policy,
    record_lineage,
    validate_merge_transaction,
)


@pytest.fixture()
def db(tmp_path):
    d = open_db(tmp_path / "rules.db")
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _planner(
    relation="unrelated",
    new_rule_safety="safe",
    operation_safety="safe",
    quality_winner="new",
    confidence=0.9,
    reason="test",
    operation="keep_both",
):
    return {
        "relation_shape": relation,
        "new_rule_safety": new_rule_safety,
        "operation_safety": operation_safety,
        "quality_winner": quality_winner,
        "confidence": confidence,
        "reason": reason,
        "operation": operation,
    }


def _existing(
    *,
    id="existing-1",
    status="active",
    source_origin="transcript_extraction",
    activation_origin=None,
    observed_usefulness_score=0.5,
    false_positive_score=0.1,
    harmful_score=0.0,
    quality_score=0.6,
    evidence_support_score=0.5,
    first_observed_useful_at="2025-01-01T00:00:00Z",
    action_instruction="do something",
):
    return {
        "id": id,
        "status": status,
        "source_origin": source_origin,
        "activation_origin": activation_origin,
        "observed_usefulness_score": observed_usefulness_score,
        "false_positive_score": false_positive_score,
        "harmful_score": harmful_score,
        "quality_score": quality_score,
        "evidence_support_score": evidence_support_score,
        "first_observed_useful_at": first_observed_useful_at,
        "action_instruction": action_instruction,
    }


def _new_rule(**overrides):
    base = {
        "id": "new-1",
        "action_instruction": "do something new",
        "evidence_support_score": 0.7,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. operation_safety="unsafe" keeps both when new rule itself is safe
# ---------------------------------------------------------------------------


class TestOperationSafetyUnsafe:
    def test_unsafe_operation_safe_new_keeps_both(self):
        """When operation_safety is unsafe but new_rule_safety is safe, keep_both."""
        planner = _planner(
            relation="equivalent",
            operation_safety="unsafe",
            new_rule_safety="safe",
            quality_winner="new",
            confidence=0.95,
        )
        result = apply_merge_policy(planner, _existing(), _new_rule())
        assert result.operation == "keep_both"
        assert "operation_safety" in result.reason

    def test_unsafe_operation_unsafe_new_rejects(self):
        """When both operation_safety and new_rule_safety are unsafe, reject_new."""
        planner = _planner(
            relation="equivalent",
            operation_safety="unsafe",
            new_rule_safety="unsafe",
            quality_winner="new",
            confidence=0.95,
        )
        result = apply_merge_policy(planner, _existing(), _new_rule())
        assert result.operation == "reject_new"
        assert "unsafe" in result.reason


# ---------------------------------------------------------------------------
# 2. Low confidence blocks destructive operations
# ---------------------------------------------------------------------------


class TestLowConfidenceDestructiveRelations:
    @pytest.mark.parametrize(
        "relation",
        ["equivalent", "obsolete", "new_broader", "new_narrower", "contradiction"],
    )
    def test_low_confidence_destructive_relation_rejects(self, relation):
        """Confidence below threshold on destructive relation triggers reject_new."""
        planner = _planner(
            relation=relation,
            quality_winner="new",
            confidence=0.5,  # below 0.65 threshold
        )
        result = apply_merge_policy(planner, _existing(), _new_rule())
        assert result.operation == "reject_new"
        assert "confidence" in result.reason

    def test_high_confidence_destructive_relation_proceeds(self):
        """Confidence above threshold does not trigger the low-confidence gate."""
        planner = _planner(
            relation="equivalent",
            quality_winner="new",
            confidence=0.9,
        )
        existing = _existing(status="candidate")
        result = apply_merge_policy(planner, existing, _new_rule())
        # Should proceed to replace_existing (equivalent + quality_winner=new + candidate)
        assert result.operation == "replace_existing"


# ---------------------------------------------------------------------------
# 3. Archived fingerprint conflict blocks new rule
# ---------------------------------------------------------------------------


class TestArchivedFingerprintConflict:
    def test_fingerprint_conflict_rejects_new(self):
        """When new_rule_data has archived_fingerprint_conflict=True, reject_new."""
        planner = _planner(
            relation="equivalent",
            quality_winner="new",
            confidence=0.95,
        )
        new_rule = _new_rule(archived_fingerprint_conflict=True)
        result = apply_merge_policy(planner, _existing(), new_rule)
        assert result.operation == "reject_new"
        assert "fingerprint" in result.reason

    def test_no_fingerprint_conflict_proceeds(self):
        """Without fingerprint conflict, processing continues normally."""
        planner = _planner(relation="unrelated", confidence=0.9)
        new_rule = _new_rule(archived_fingerprint_conflict=False)
        result = apply_merge_policy(planner, _existing(), new_rule)
        assert result.operation == "keep_both"


# ---------------------------------------------------------------------------
# 4. validate_merge_transaction enforces all four gates for destructive ops
# ---------------------------------------------------------------------------


class TestValidateMergeTransactionGates:
    @pytest.mark.parametrize(
        "operation",
        ["replace_existing", "suppress_existing", "archive_existing", "update_existing_fields"],
    )
    def test_each_destructive_op_requires_all_gates(self, operation):
        """Each destructive operation type requires synthetic, fingerprint, matcher, and admission."""
        decision = MergeDecision(
            operation=operation,
            target_rule_id="x",
            reason="test",
            requires_synthetic_reeval=True,
            lineage_record=None,
        )
        # All gates pass -> True
        assert validate_merge_transaction(
            _existing(), _new_rule(), decision,
            synthetic_passed=True, fingerprint_clear=True, matcher_compiled=True,
            final_admission_passed=True,
        ) is True

        # Each gate failing individually -> False
        assert validate_merge_transaction(
            _existing(), _new_rule(), decision,
            synthetic_passed=False, fingerprint_clear=True, matcher_compiled=True,
            final_admission_passed=True,
        ) is False

        assert validate_merge_transaction(
            _existing(), _new_rule(), decision,
            synthetic_passed=True, fingerprint_clear=False, matcher_compiled=True,
            final_admission_passed=True,
        ) is False

        assert validate_merge_transaction(
            _existing(), _new_rule(), decision,
            synthetic_passed=True, fingerprint_clear=True, matcher_compiled=False,
            final_admission_passed=True,
        ) is False

        assert validate_merge_transaction(
            _existing(), _new_rule(), decision,
            synthetic_passed=True, fingerprint_clear=True, matcher_compiled=True,
            final_admission_passed=False,
        ) is False

    @pytest.mark.parametrize("operation", ["keep_both", "reject_new", "split_required"])
    def test_non_destructive_ops_bypass_gates(self, operation):
        """Non-destructive operations pass validation regardless of gate results."""
        decision = MergeDecision(
            operation=operation,
            target_rule_id="x",
            reason="test",
            requires_synthetic_reeval=False,
            lineage_record=None,
        )
        assert validate_merge_transaction(
            _existing(), _new_rule(), decision,
            synthetic_passed=False, fingerprint_clear=False, matcher_compiled=False,
            final_admission_passed=False,
        ) is True


# ---------------------------------------------------------------------------
# 5. Lineage recording during merges
# ---------------------------------------------------------------------------


class TestLineageRecording:
    def test_record_lineage_stores_all_fields(self, db):
        """record_lineage persists old_rule_id, new_rule_id, operation, reason, and timestamp."""
        record_lineage(db, "old-rule-1", "new-rule-1", "replace_existing", "quality upgrade")
        row = db.fetchone(
            "SELECT * FROM rule_lineage WHERE old_rule_id = ? AND new_rule_id = ?",
            ("old-rule-1", "new-rule-1"),
        )
        assert row is not None
        assert row["operation"] == "replace_existing"
        assert row["reason"] == "quality upgrade"
        assert row["created_at"] is not None

    def test_lineage_record_attached_to_replace_decision(self):
        """replace_existing decision includes lineage_record with operation metadata."""
        planner = _planner(
            relation="equivalent",
            quality_winner="new",
            confidence=0.9,
            reason="new rule has better evidence",
        )
        existing = _existing(status="candidate")
        result = apply_merge_policy(planner, existing, _new_rule())
        assert result.operation == "replace_existing"
        assert result.lineage_record is not None
        assert result.lineage_record["old_rule_id"] == "existing-1"
        assert result.lineage_record["operation"] == "replace_existing"
        assert result.lineage_record["reason"] == "new rule has better evidence"

    def test_keep_both_has_no_lineage_record(self):
        """keep_both decisions do not generate lineage records."""
        planner = _planner(relation="unrelated", confidence=0.9)
        result = apply_merge_policy(planner, _existing(), _new_rule())
        assert result.operation == "keep_both"
        assert result.lineage_record is None
