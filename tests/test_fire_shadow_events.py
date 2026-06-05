"""Tests for fire and shadow event persistence and query functions."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from nokori.db import open_db, dumps_json
from nokori.events.fire import (
    count_evaluated_fire_events,
    create_fire_event,
    get_fire_events_for_session,
    mark_posthoc_label,
    update_first_observed_useful,
)
from nokori.events.shadow import (
    compute_context_fingerprint,
    count_shadow_evidence,
    create_shadow_event,
    is_duplicate_shadow_context,
)
from nokori.models import Rule


def _utcnow_iso(delta_days: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=delta_days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _make_db(tmp_path):
    db = open_db(tmp_path / "rules.db")
    return db


def _insert_rule(db, *, rule_id=None, status="active", rule_version=1) -> Rule:
    """Insert a minimal rule into the DB and return the corresponding Rule dataclass."""
    rule_id = rule_id or str(uuid.uuid4())
    short_id = rule_id[:6]
    now = _utcnow_iso()
    concepts = json.dumps(["concept_a", "concept_b"])
    required_concept_groups = json.dumps(["group_1"])
    excluded_contexts = json.dumps(["excluded_ctx"])

    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "status, severity, "
            "trigger_canonical, concepts, required_concept_groups, excluded_contexts, "
            "trigger_variants, "
            "action_instruction, "
            "domain_tags, tool_tags, path_patterns, "
            "source_origin, project_scope, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rule_id,
                short_id,
                1,
                rule_version,
                "pipeline_v1",
                "policy_v1",
                status,
                "reminder",
                "trigger text here",
                concepts,
                required_concept_groups,
                excluded_contexts,
                json.dumps(["variant_a"]),
                "do the action",
                json.dumps(["domain_web"]),
                json.dumps(["tool_git"]),
                json.dumps(["src/**"]),
                "transcript_extraction",
                "project",
                now,
                now,
            ),
        )

    return Rule(
        id=rule_id,
        short_id=short_id,
        schema_version=1,
        rule_version=rule_version,
        created_by_pipeline_version="pipeline_v1",
        runtime_policy_version="policy_v1",
        last_rewritten_by_role=None,
        status=status,
        severity="reminder",
        trigger_canonical="trigger text here",
        concepts=concepts,
        required_concept_groups=required_concept_groups,
        excluded_contexts=excluded_contexts,
        near_miss_examples=[],
        trigger_variants=["variant_a"],
        action_instruction="do the action",
        domain_tags=["domain_web"],
        tool_tags=["tool_git"],
        path_patterns=["src/**"],
        quality_score=0.0,
        evidence_support_score=0.0,
        specificity_score=0.0,
        retrieval_readiness_score=0.0,
        observed_usefulness_score=0.0,
        plausible_usefulness_score=0.0,
        false_positive_score=0.0,
        harmful_score=0.0,
        source_origin="transcript_extraction",
        activation_origin=None,
        first_observed_useful_at=None,
        trusted_at=None,
        suppressed_at=None,
        project_scope="project",
        project_id=None,
        archived_reason=None,
        replacement_id=None,
        created_at=now,
        updated_at=now,
    )


class TestCreateFireEvent:
    def test_stores_injected_snapshots(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            event_id = create_fire_event(
                db, rule, "session_1", "hash_abc", "hot", {"score": 0.9}
            )
            row = db.fetchone(
                "SELECT * FROM rule_fire_events WHERE id = ?", (event_id,)
            )
            assert row is not None
            assert row["injected_rule_version"] == rule.rule_version
            assert row["injected_trigger_snapshot"] == rule.trigger_canonical
            assert row["injected_action_snapshot"] == rule.action_instruction
        finally:
            db.close()


class TestCreateShadowEventMalformedJson:
    def test_malformed_json_snapshot_fields_fall_back_to_empty_lists(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = replace(
                _insert_rule(db),
                concepts="{not-json",
                required_concept_groups="{not-json",
                excluded_contexts="{not-json",
            )

            event_id = create_shadow_event(
                db,
                rule,
                session_id="session_shadow",
                status_at_match="candidate",
                shadow_type="candidate_probe",
                prompt_hash="hash_abc",
                matched_level="warm",
                decision_features={},
            )

            row = db.fetchone(
                "SELECT shadow_structured_snapshot FROM rule_shadow_events WHERE id = ?",
                (event_id,),
            )
            snapshot = json.loads(row["shadow_structured_snapshot"])
            assert snapshot["concepts"] == []
            assert snapshot["required_concept_groups"] == []
            assert snapshot["excluded_contexts"] == []
        finally:
            db.close()

    def test_stores_version_metadata(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            event_id = create_fire_event(
                db,
                rule,
                "session_1",
                "hash_abc",
                "warm",
                {"score": 0.8},
                idf_pool_version="idf_v2",
                runtime_policy_version="policy_v3",
                embedding_profile_version="embed_v1",
            )
            row = db.fetchone(
                "SELECT * FROM rule_fire_events WHERE id = ?", (event_id,)
            )
            assert row["trigger_idf_pool_version"] == "idf_v2"
            assert row["runtime_policy_version"] == "policy_v3"
            assert row["embedding_profile_version"] == "embed_v1"
        finally:
            db.close()


class TestMarkPosthocLabel:
    def test_updates_fire_event(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            event_id = create_fire_event(
                db, rule, "session_1", "hash_abc", "hot", {"score": 0.9}
            )
            mark_posthoc_label(db, event_id, "observed_useful", "useful_prevented_error", score=0.95)
            row = db.fetchone(
                "SELECT posthoc_label, posthoc_reason_code, posthoc_score "
                "FROM rule_fire_events WHERE id = ?",
                (event_id,),
            )
            assert row["posthoc_label"] == "observed_useful"
            assert row["posthoc_reason_code"] == "useful_prevented_error"
            assert row["posthoc_score"] == pytest.approx(0.95)
        finally:
            db.close()


class TestUpdateFirstObservedUseful:
    def test_sets_field_when_null_and_evidence_exists(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            event_id = create_fire_event(
                db, rule, "session_1", "hash_abc", "hot", {"score": 0.9}
            )
            mark_posthoc_label(db, event_id, "observed_useful", "useful_prevented_error")
            update_first_observed_useful(db, rule.id)
            row = db.fetchone(
                "SELECT first_observed_useful_at FROM rules WHERE id = ?",
                (rule.id,),
            )
            assert row["first_observed_useful_at"] is not None
        finally:
            db.close()

    def test_does_not_overwrite_when_already_set(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            # Manually set first_observed_useful_at
            sentinel = "2025-01-01T00:00:00Z"
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE rules SET first_observed_useful_at = ? WHERE id = ?",
                    (sentinel, rule.id),
                )
            event_id = create_fire_event(
                db, rule, "session_1", "hash_abc", "hot", {"score": 0.9}
            )
            mark_posthoc_label(db, event_id, "observed_useful", "useful_prevented_error")
            update_first_observed_useful(db, rule.id)
            row = db.fetchone(
                "SELECT first_observed_useful_at FROM rules WHERE id = ?",
                (rule.id,),
            )
            assert row["first_observed_useful_at"] == sentinel
        finally:
            db.close()

    def test_noop_when_no_observed_useful_events(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            # Fire event with a different label
            event_id = create_fire_event(
                db, rule, "session_1", "hash_abc", "hot", {"score": 0.9}
            )
            mark_posthoc_label(db, event_id, "irrelevant", "irrelevant_not_applicable")
            update_first_observed_useful(db, rule.id)
            row = db.fetchone(
                "SELECT first_observed_useful_at FROM rules WHERE id = ?",
                (rule.id,),
            )
            assert row["first_observed_useful_at"] is None
        finally:
            db.close()


class TestCountEvaluatedFireEvents:
    def test_returns_correct_counts_within_window(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            # Create events with various labels
            for label in ["observed_useful", "observed_useful", "irrelevant", "harmful"]:
                eid = create_fire_event(
                    db, rule, "session_1", "hash_abc", "hot", {"score": 0.5}
                )
                mark_posthoc_label(db, eid, label, "useful_improved_quality")

            # One event without label (should not count)
            create_fire_event(db, rule, "session_1", "hash_abc", "hot", {"score": 0.5})

            counts = count_evaluated_fire_events(db, rule.id, window_days=30)
            assert counts["observed_useful"] == 2
            assert counts["irrelevant"] == 1
            assert counts["harmful"] == 1
            assert counts["plausible_useful"] == 0
            assert counts["total_evaluated"] == 4
        finally:
            db.close()

    def test_excludes_events_outside_window(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            eid = create_fire_event(
                db, rule, "session_1", "hash_abc", "hot", {"score": 0.5}
            )
            mark_posthoc_label(db, eid, "observed_useful", "useful_improved_quality")
            # Backdate the event to 60 days ago
            old_ts = _utcnow_iso(-60)
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE rule_fire_events SET created_at = ? WHERE id = ?",
                    (old_ts, eid),
                )
            counts = count_evaluated_fire_events(db, rule.id, window_days=30)
            assert counts["total_evaluated"] == 0
        finally:
            db.close()


class TestCreateShadowEvent:
    def test_stores_shadow_snapshots_and_fingerprint(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db, status="candidate")
            fp = compute_context_fingerprint("hash_x", "tool_read", 3)
            event_id = create_shadow_event(
                db,
                rule,
                "session_2",
                "candidate",
                "candidate_probe",
                "hash_x",
                "warm",
                {"sim": 0.7},
                context_fingerprint=fp,
            )
            row = db.fetchone(
                "SELECT * FROM rule_shadow_events WHERE id = ?", (event_id,)
            )
            assert row["shadow_rule_version"] == rule.rule_version
            assert row["shadow_trigger_snapshot"] == rule.trigger_canonical
            assert row["context_fingerprint"] == fp
        finally:
            db.close()

    def test_records_status_at_match(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db, status="candidate")
            eid_candidate = create_shadow_event(
                db, rule, "s1", "candidate", "candidate_probe",
                "hash_a", "hot", {"sim": 0.8},
            )
            row = db.fetchone(
                "SELECT status_at_match FROM rule_shadow_events WHERE id = ?",
                (eid_candidate,),
            )
            assert row["status_at_match"] == "candidate"

            rule_supp = _insert_rule(db, status="suppressed")
            eid_suppressed = create_shadow_event(
                db, rule_supp, "s2", "suppressed", "suppression_recovery",
                "hash_b", "warm", {"sim": 0.6},
            )
            row2 = db.fetchone(
                "SELECT status_at_match FROM rule_shadow_events WHERE id = ?",
                (eid_suppressed,),
            )
            assert row2["status_at_match"] == "suppressed"
        finally:
            db.close()


class TestCountShadowEvidence:
    def test_deduplicates_by_context_fingerprint(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db, status="candidate")
            fp = compute_context_fingerprint("hash_dup", "tool_a", 1)
            # Insert two shadow events with same fingerprint
            from nokori.events.shadow import mark_shadow_label

            eid1 = create_shadow_event(
                db, rule, "s1", "candidate", "candidate_probe",
                "hash_dup", "hot", {"sim": 0.9},
                context_fingerprint=fp,
            )
            mark_shadow_label(db, eid1, "would_help_high")

            eid2 = create_shadow_event(
                db, rule, "s2", "candidate", "candidate_probe",
                "hash_dup", "hot", {"sim": 0.9},
                context_fingerprint=fp,
            )
            mark_shadow_label(db, eid2, "would_help_high")

            counts = count_shadow_evidence(db, rule.id, rule.rule_version)
            # Only one unique context even though two events share the fingerprint
            assert counts["unique_contexts"] == 1
            assert counts["would_help_high"] == 1
        finally:
            db.close()

    def test_counts_only_version_compatible_events(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule_v1 = _insert_rule(db, status="candidate", rule_version=1)
            rule_v2_id = rule_v1.id  # Same rule, but we'll insert events with version 2

            from nokori.events.shadow import mark_shadow_label

            # Event with version 1
            eid1 = create_shadow_event(
                db, rule_v1, "s1", "candidate", "candidate_probe",
                "hash_1", "hot", {"sim": 0.9},
                context_fingerprint="fp_unique_1",
            )
            mark_shadow_label(db, eid1, "would_help_high")

            # Manually insert event with version 2 (simulating rule version bump)
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO rule_shadow_events "
                    "(id, rule_id, session_id, shadow_rule_version, "
                    "shadow_trigger_snapshot, shadow_action_snapshot, "
                    "status_at_match, shadow_type, prompt_hash, matched_level, "
                    "decision_features, context_fingerprint, shadow_label, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        rule_v2_id,
                        "s2",
                        2,
                        "trigger text here",
                        "do the action",
                        "candidate",
                        "candidate_probe",
                        "hash_2",
                        "hot",
                        dumps_json({"sim": 0.8}),
                        "fp_unique_2",
                        "would_help_low",
                        _utcnow_iso(),
                    ),
                )

            # Count for version 1 only
            counts_v1 = count_shadow_evidence(db, rule_v2_id, 1)
            assert counts_v1["would_help_high"] == 1
            assert counts_v1["would_help_low"] == 0
            assert counts_v1["unique_contexts"] == 1

            # Count for version 2 only
            counts_v2 = count_shadow_evidence(db, rule_v2_id, 2)
            assert counts_v2["would_help_high"] == 0
            assert counts_v2["would_help_low"] == 1
            assert counts_v2["unique_contexts"] == 1
        finally:
            db.close()


class TestIsDuplicateShadowContext:
    def test_detects_duplicates(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db, status="candidate")
            fp = compute_context_fingerprint("hash_z", "tool_b", 5)
            create_shadow_event(
                db, rule, "s1", "candidate", "candidate_probe",
                "hash_z", "hot", {"sim": 0.9},
                context_fingerprint=fp,
            )
            assert is_duplicate_shadow_context(db, rule.id, fp) is True
        finally:
            db.close()

    def test_returns_false_for_new_context(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db, status="candidate")
            fp_new = compute_context_fingerprint("never_seen", "tool_c", 99)
            assert is_duplicate_shadow_context(db, rule.id, fp_new) is False
        finally:
            db.close()


class TestRunShadowCounterfactualEvaluation:
    """Tests for run_shadow_counterfactual_evaluation with a mock LLM."""

    def test_labels_unlabeled_shadow_events(self, tmp_path):
        from nokori.events.shadow import (
            run_shadow_counterfactual_evaluation,
            mark_shadow_label,
        )

        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db, status="candidate")

            # Create shadow events without labels
            eid1 = create_shadow_event(
                db, rule, "s_eval_1", "candidate", "candidate_probe",
                "hash_eval_1", "hot", {"sim": 0.9},
                context_fingerprint="fp_eval_1",
            )
            eid2 = create_shadow_event(
                db, rule, "s_eval_2", "candidate", "candidate_probe",
                "hash_eval_2", "warm", {"sim": 0.7},
                context_fingerprint="fp_eval_2",
            )

            # Verify events are unlabeled
            row1 = db.fetchone(
                "SELECT shadow_label FROM rule_shadow_events WHERE id = ?", (eid1,)
            )
            row2 = db.fetchone(
                "SELECT shadow_label FROM rule_shadow_events WHERE id = ?", (eid2,)
            )
            assert row1["shadow_label"] is None
            assert row2["shadow_label"] is None

            # Mock LLM that returns would_help_high
            import json

            class MockLLM:
                def call(self, *, system, user, role):
                    return json.dumps({
                        "label": "would_help_high",
                        "reasoning": "The rule would have prevented an error",
                    })

            summary = run_shadow_counterfactual_evaluation(db, MockLLM())

            assert summary["processed"] == 2
            assert summary["labeled"] == 2
            assert summary["failed"] == 0

            # Verify labels are applied
            row1 = db.fetchone(
                "SELECT shadow_label FROM rule_shadow_events WHERE id = ?", (eid1,)
            )
            row2 = db.fetchone(
                "SELECT shadow_label FROM rule_shadow_events WHERE id = ?", (eid2,)
            )
            assert row1["shadow_label"] == "would_help_high"
            assert row2["shadow_label"] == "would_help_high"
        finally:
            db.close()

    def test_handles_llm_failure_gracefully(self, tmp_path):
        from nokori.events.shadow import run_shadow_counterfactual_evaluation

        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db, status="suppressed")

            eid = create_shadow_event(
                db, rule, "s_fail", "suppressed", "suppression_recovery",
                "hash_fail", "hot", {"sim": 0.8},
                context_fingerprint="fp_fail",
            )

            # Mock LLM that raises an exception
            class FailingLLM:
                def call(self, *, system, user, role):
                    raise RuntimeError("LLM unavailable")

            summary = run_shadow_counterfactual_evaluation(db, FailingLLM())

            assert summary["processed"] == 1
            assert summary["failed"] == 1
            assert summary["labeled"] == 0

            # Event remains unlabeled
            row = db.fetchone(
                "SELECT shadow_label FROM rule_shadow_events WHERE id = ?", (eid,)
            )
            assert row["shadow_label"] is None
        finally:
            db.close()


class TestGetFireEventsForSession:
    def test_returns_all_events_for_session(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_xyz"
            eid1 = create_fire_event(
                db, rule, session, "hash_1", "hot", {"score": 0.9}
            )
            eid2 = create_fire_event(
                db, rule, session, "hash_2", "warm", {"score": 0.7}
            )
            # Different session, should not appear
            create_fire_event(
                db, rule, "other_session", "hash_3", "hot", {"score": 0.5}
            )
            events = get_fire_events_for_session(db, session)
            event_ids = {e["id"] for e in events}
            assert eid1 in event_ids
            assert eid2 in event_ids
            assert len(events) == 2
        finally:
            db.close()
