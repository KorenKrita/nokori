"""Tests for nokori.merge.policy -- deterministic merge policy (section 8.4)."""

import pytest

from nokori.db import open_db
from nokori.merge.policy import (
    MergeDecision,
    apply_merge_policy,
    check_trusted_replacement,
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
# 1. Planner output has all required fields
# ---------------------------------------------------------------------------


def test_planner_output_has_required_fields():
    """apply_merge_policy consumes all required planner fields without error."""
    planner = _planner()
    # Must contain these keys
    required = {
        "relation_shape",
        "new_rule_safety",
        "operation_safety",
        "quality_winner",
        "confidence",
        "reason",
    }
    assert required.issubset(planner.keys())
    result = apply_merge_policy(planner, None, _new_rule())
    assert isinstance(result, MergeDecision)


# ---------------------------------------------------------------------------
# 2. Low-confidence unrelated -> keep_both
# ---------------------------------------------------------------------------


def test_low_confidence_unrelated_keeps_both():
    """Low confidence with unrelated relation results in keep_both (cannot promote)."""
    planner = _planner(relation="unrelated", confidence=0.3)
    result = apply_merge_policy(planner, _existing(), _new_rule())
    assert result.operation == "keep_both"
    assert "unrelated" in result.reason


# ---------------------------------------------------------------------------
# 3. Unsafe new_rule_safety -> reject_new
# ---------------------------------------------------------------------------


def test_unsafe_new_rule_safety_rejects():
    """new_rule_safety=unsafe always triggers reject_new."""
    planner = _planner(
        relation="equivalent",
        new_rule_safety="unsafe",
        confidence=0.95,
    )
    result = apply_merge_policy(planner, _existing(), _new_rule())
    assert result.operation == "reject_new"
    assert "unsafe" in result.reason


# ---------------------------------------------------------------------------
# 4. Unsafe operation_safety + safe new rule -> keep_both
# ---------------------------------------------------------------------------


def test_unsafe_operation_safety_safe_new_keeps_both():
    """operation_safety=unsafe but new rule is safe -> keep_both."""
    planner = _planner(
        relation="equivalent",
        new_rule_safety="safe",
        operation_safety="unsafe",
        confidence=0.9,
    )
    result = apply_merge_policy(planner, _existing(), _new_rule())
    assert result.operation == "keep_both"
    assert "operation_safety" in result.reason


# ---------------------------------------------------------------------------
# 5. Trusted replacement requires higher bar
# ---------------------------------------------------------------------------


def test_trusted_replacement_requires_all_conditions():
    """Replacing a trusted rule requires all higher-bar conditions."""
    existing = _existing(
        status="trusted",
        evidence_support_score=0.6,
        observed_usefulness_score=0.2,
        first_observed_useful_at=None,
    )
    planner = _planner(
        relation="equivalent",
        quality_winner="new",
        confidence=0.95,
    )
    # Missing synthetic_strictly_improves -> check_trusted_replacement fails
    new_rule = _new_rule(evidence_support_score=0.8, synthetic_strictly_improves=False)
    result = apply_merge_policy(planner, existing, new_rule)
    # Falls through to fallback keep_both since trusted replacement check fails
    assert result.operation == "keep_both"

    # All conditions met -> replace_existing
    new_rule_full = _new_rule(
        evidence_support_score=0.8,
        synthetic_strictly_improves=True,
    )
    result2 = apply_merge_policy(planner, existing, new_rule_full)
    assert result2.operation == "replace_existing"
    assert result2.requires_synthetic_reeval is True


def test_check_trusted_replacement_rejects_low_evidence():
    """check_trusted_replacement returns False when new evidence < existing."""
    existing = _existing(evidence_support_score=0.9, observed_usefulness_score=0.2)
    new_rule = _new_rule(evidence_support_score=0.5, synthetic_strictly_improves=True)
    planner = _planner(relation="equivalent", quality_winner="new")
    assert check_trusted_replacement(existing, new_rule, planner) is False


# ---------------------------------------------------------------------------
# 6. Equivalent + safe + quality_winner=new + not trusted -> replace_existing
# ---------------------------------------------------------------------------


def test_equivalent_safe_quality_new_not_trusted_replaces():
    """Non-trusted existing + equivalent + quality_winner=new -> replace_existing."""
    existing = _existing(status="candidate")
    planner = _planner(
        relation="equivalent",
        quality_winner="new",
        confidence=0.9,
    )
    new_rule = _new_rule(action_instruction="different action")
    result = apply_merge_policy(planner, existing, new_rule)
    assert result.operation == "replace_existing"
    assert result.lineage_record is not None
    assert result.lineage_record["operation"] == "replace_existing"


# ---------------------------------------------------------------------------
# 7. split_required returns split status
# ---------------------------------------------------------------------------


def test_split_required_returns_split():
    """relation_shape=split_required produces split_required operation."""
    planner = _planner(relation="split_required", confidence=0.9)
    result = apply_merge_policy(planner, _existing(), _new_rule())
    assert result.operation == "split_required"
    assert "split" in result.reason


# ---------------------------------------------------------------------------
# 8. Merge transaction validation
# ---------------------------------------------------------------------------


def test_validate_merge_transaction_destructive_requires_all_gates():
    """Destructive ops (replace/suppress/archive) require all three gates."""
    decision = MergeDecision(
        operation="replace_existing",
        target_rule_id="x",
        reason="test",
        requires_synthetic_reeval=True,
        lineage_record=None,
    )
    # All pass
    assert validate_merge_transaction(
        _existing(), _new_rule(), decision,
        synthetic_passed=True, fingerprint_clear=True, matcher_compiled=True,
    ) is True

    # Each gate failing individually blocks the transaction
    assert validate_merge_transaction(
        _existing(), _new_rule(), decision,
        synthetic_passed=False, fingerprint_clear=True, matcher_compiled=True,
    ) is False
    assert validate_merge_transaction(
        _existing(), _new_rule(), decision,
        synthetic_passed=True, fingerprint_clear=False, matcher_compiled=True,
    ) is False
    assert validate_merge_transaction(
        _existing(), _new_rule(), decision,
        synthetic_passed=True, fingerprint_clear=True, matcher_compiled=False,
    ) is False


def test_validate_merge_transaction_non_destructive_always_passes():
    """Non-destructive ops (keep_both, reject_new, merge_into_existing) always pass validation."""
    for op in ("keep_both", "reject_new", "merge_into_existing"):
        decision = MergeDecision(
            operation=op,
            target_rule_id="x",
            reason="test",
            requires_synthetic_reeval=False,
            lineage_record=None,
        )
        assert validate_merge_transaction(
            _existing(), _new_rule(), decision,
            synthetic_passed=False, fingerprint_clear=False, matcher_compiled=False,
        ) is True


# ---------------------------------------------------------------------------
# 9. Complementary -> keep_both
# ---------------------------------------------------------------------------


def test_complementary_relation_keeps_both():
    """complementary relation always produces keep_both."""
    planner = _planner(relation="complementary", confidence=0.9)
    result = apply_merge_policy(planner, _existing(), _new_rule())
    assert result.operation == "keep_both"
    assert "complementary" in result.reason


# ---------------------------------------------------------------------------
# 10. record_lineage creates entry
# ---------------------------------------------------------------------------


def test_record_lineage_creates_entry(db):
    """record_lineage inserts into rule_lineage table."""
    record_lineage(db, "old-1", "new-1", "replace_existing", "quality upgrade")
    row = db.fetchone("SELECT * FROM rule_lineage WHERE old_rule_id = ?", ("old-1",))
    assert row is not None
    assert row["new_rule_id"] == "new-1"
    assert row["operation"] == "replace_existing"
    assert row["reason"] == "quality upgrade"
    assert row["created_at"] is not None
