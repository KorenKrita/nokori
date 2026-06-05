"""Tests for nokori.merge.policy -- deterministic merge policy (section 8.4)."""

import json

import pytest

from nokori.db import open_db
from nokori.merge.policy import (
    MergeDecision,
    apply_merge_policy,
    check_trusted_replacement,
    find_merge_neighbors,
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


def test_validate_merge_transaction_excluded_context_change_requires_gates():
    decision = MergeDecision(
        operation="update_existing_fields",
        target_rule_id="rule-1",
        reason="changed exclusion",
        requires_synthetic_reeval=False,
        lineage_record={"changed_fields": ["excluded_contexts"]},
    )

    assert validate_merge_transaction(
        {"id": "rule-1"},
        {"excluded_contexts": [{"id": "ex1"}]},
        decision,
        synthetic_passed=False,
        fingerprint_clear=True,
        matcher_compiled=True,
    ) is False


def test_validate_merge_transaction_variant_alias_change_requires_gates():
    decision = MergeDecision(
        operation="update_existing_fields",
        target_rule_id="rule-1",
        reason="changed variants",
        requires_synthetic_reeval=False,
        lineage_record={"changed_fields": ["variants"]},
    )

    assert validate_merge_transaction(
        {"id": "rule-1"},
        {"variants": [{"text": "git push --force"}]},
        decision,
        synthetic_passed=False,
        fingerprint_clear=True,
        matcher_compiled=True,
    ) is False


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


# ---------------------------------------------------------------------------
# 11. suppress_existing on contradiction
# ---------------------------------------------------------------------------


def test_suppress_existing_on_contradiction():
    """contradiction + quality_winner=new + safe + weak history -> suppress_existing."""
    existing = _existing(
        observed_usefulness_score=0.1,
        false_positive_score=0.5,
    )
    planner = _planner(
        relation="contradiction",
        quality_winner="new",
        operation="suppress_existing",
        confidence=0.9,
    )
    result = apply_merge_policy(planner, existing, _new_rule())
    assert result.operation == "suppress_existing"
    assert result.lineage_record is not None
    assert result.lineage_record["operation"] == "suppress_existing"


# ---------------------------------------------------------------------------
# 12. archive_existing for system-created harmful
# ---------------------------------------------------------------------------


def test_archive_existing_system_harmful():
    """System-created rule with harmful history -> archive_existing."""
    existing = _existing(
        status="trusted",
        source_origin="transcript_extraction",
        activation_origin=None,
        harmful_score=0.7,
        quality_score=0.2,
        action_instruction="do something harmful",
    )
    planner = _planner(
        relation="equivalent",
        quality_winner="new",
        confidence=0.9,
    )
    new_rule = _new_rule(user_archived_opposite=False)
    result = apply_merge_policy(planner, existing, new_rule)
    assert result.operation == "archive_existing"
    assert result.lineage_record is not None
    assert result.lineage_record["operation"] == "archive_existing"


# ---------------------------------------------------------------------------
# 13. update_existing_fields with improvement
# ---------------------------------------------------------------------------


def test_update_existing_fields_with_improvement():
    """equivalent + safe + active + unchanged action + more variants -> update_existing_fields."""
    existing = _existing(
        status="active",
        action_instruction="always use semicolons",
    )
    planner = _planner(
        relation="equivalent",
        quality_winner="new",
        confidence=0.9,
    )
    new_rule = _new_rule(
        action_instruction="always use semicolons",
        trigger_variants=["use semicolons", "semicolon usage", "add semicolons"],
    )
    result = apply_merge_policy(planner, existing, new_rule)
    assert result.operation == "update_existing_fields"
    assert result.lineage_record is not None
    assert result.lineage_record["operation"] == "update_existing_fields"


def test_update_existing_fields_with_excluded_context_improvement():
    """equivalent + safe + unchanged action + more exclusions -> update_existing_fields."""
    existing = _existing(
        status="active",
        action_instruction="always use semicolons",
    )
    planner = _planner(
        relation="equivalent",
        quality_winner="new",
        confidence=0.9,
    )
    new_rule = _new_rule(action_instruction="always use semicolons")
    new_rule["excluded_contexts"] = [{"id": "ex1", "patterns": ["generated file"]}]

    result = apply_merge_policy(planner, existing, new_rule)

    assert result.operation == "update_existing_fields"


# ---------------------------------------------------------------------------
# 14. merge_into_existing with quality_winner=both
# ---------------------------------------------------------------------------


def test_merge_into_existing_quality_both():
    """equivalent + safe + quality_winner=both -> merge_into_existing."""
    existing = _existing(status="active")
    planner = _planner(
        relation="equivalent",
        quality_winner="both",
        confidence=0.9,
    )
    result = apply_merge_policy(planner, existing, _new_rule())
    assert result.operation == "merge_into_existing"
    assert result.lineage_record is not None
    assert result.lineage_record["operation"] == "merge_into_existing"


# ---------------------------------------------------------------------------
# 15. find_merge_neighbors returns related rules
# ---------------------------------------------------------------------------


def _insert_rule(
    db,
    rule_id,
    short_id,
    trigger,
    action,
    status="active",
    project_scope="global",
    project_id=None,
):
    """Insert a minimal rule into the DB for neighbor retrieval tests."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "trigger_canonical, action_instruction, status, severity, "
            "source_origin, project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rule_id, short_id, 6, 1, trigger, action, status, "reminder",
             "transcript_extraction", project_scope, project_id, now, now),
        )


def test_find_merge_neighbors_returns_related_rules(db):
    """Rules with overlapping trigger text are returned as merge neighbors."""
    _insert_rule(db, "rule-a", "ra", "always use typescript strict mode", "enable strict")
    _insert_rule(db, "rule-b", "rb", "prefer typescript strict checking", "check strict")

    rule_data = {
        "id": "new-candidate",
        "trigger_canonical": "use typescript strict mode for all files",
        "action_instruction": "enforce strict",
    }
    neighbors = find_merge_neighbors(db, rule_data)
    neighbor_ids = [n["id"] for n in neighbors]
    assert "rule-a" in neighbor_ids
    assert "rule-b" in neighbor_ids


def test_find_merge_neighbors_matches_v6_variant_dicts(db):
    """V6 variant objects participate in merge-neighbor phrase recall."""
    _insert_rule(db, "rule-a", "ra", "alpha beta", "gamma delta")
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET trigger_variants = ? WHERE id = ?",
            (
                json.dumps(
                    [{"text": "git push --force", "strength": "strong_anchor"}]
                ),
                "rule-a",
            ),
        )
    _insert_rule(db, "rule-b", "rb", "recent unrelated", "recent unrelated")
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET updated_at = ? WHERE id = ?",
            ("2999-01-01T00:00:00Z", "rule-b"),
        )

    rule_data = {
        "id": "new-candidate",
        "trigger_canonical": "omega sigma",
        "variants": [{"text": "git push --force", "strength": "strong_anchor"}],
        "action_instruction": "theta lambda",
    }

    neighbors = find_merge_neighbors(db, rule_data, limit=1)

    assert [n["id"] for n in neighbors] == ["rule-a"]


def test_find_merge_neighbors_filters_to_global_and_current_project(db):
    """Project-scoped cold merges must not see unrelated project rules."""
    _insert_rule(
        db,
        "rule-global",
        "rg",
        "use typescript strict mode",
        "enable strict",
    )
    _insert_rule(
        db,
        "rule-current",
        "rc",
        "use typescript strict mode",
        "enable strict",
        project_scope="project",
        project_id="proj-a",
    )
    _insert_rule(
        db,
        "rule-other",
        "ro",
        "use typescript strict mode",
        "enable strict",
        project_scope="project",
        project_id="proj-b",
    )

    rule_data = {
        "id": "new-candidate",
        "trigger_canonical": "use typescript strict mode for all files",
        "action_instruction": "enforce strict",
    }

    scoped = find_merge_neighbors(db, rule_data, project_id="proj-a")
    scoped_ids = {n["id"] for n in scoped}
    assert "rule-global" in scoped_ids
    assert "rule-current" in scoped_ids
    assert "rule-other" not in scoped_ids

    global_only = find_merge_neighbors(db, rule_data, project_id=None)
    assert {n["id"] for n in global_only} == {"rule-global"}


def test_find_merge_neighbors_reads_domain_tags_from_rule_scope(db):
    """Cold rule_data stores domain/tool tags under scope."""
    _insert_rule(db, "rule-domain", "rd", "alpha beta", "domain action")
    _insert_rule(db, "rule-recent", "rr", "recent unrelated", "recent action")
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET domain_tags = ?, updated_at = ? WHERE id = ?",
            (json.dumps(["python"]), "2000-01-01T00:00:00Z", "rule-domain"),
        )
        tx.execute(
            "UPDATE rules SET updated_at = ? WHERE id = ?",
            ("2999-01-01T00:00:00Z", "rule-recent"),
        )

    rule_data = {
        "id": "new-candidate",
        "trigger_canonical": "omega sigma",
        "action_instruction": "theta lambda",
        "scope": {"domain_tags": ["python"]},
    }

    neighbors = find_merge_neighbors(db, rule_data, limit=1)

    assert [n["id"] for n in neighbors] == ["rule-domain"]
