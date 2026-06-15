"""Tests for compute_promotion_barriers()."""
from __future__ import annotations

import uuid

import pytest

from nokori.db import open_db
from nokori.lifecycle.transitions import compute_promotion_barriers
from nokori.utils.time import now_iso


@pytest.fixture
def db(tmp_path):
    from dataclasses import replace

    from nokori.config import Config

    cfg = replace(Config.from_env(), data_dir=tmp_path)
    database = open_db(cfg.db_path)
    yield database
    database.close()


def _insert_rule(db, rule_id="rule-1", status="candidate", rule_version=1, suppressed_at=None):
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, trigger_variants, "
            "search_terms, action_instruction, "
            "source_origin, status, severity, "
            "evidence_support_score, "
            "project_scope, suppressed_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rule_id, "abc", 1, rule_version,
                "v1", "v1",
                "test trigger",
                '[{"text":"test","kind":"strong_anchor","requires_concepts":["manual_trigger"]}]',
                '{"en": ["test"]}',
                "test action",
                "transcript_extraction", status, "reminder",
                3.0,
                "global", suppressed_at, now, now,
            ),
        )


def _insert_shadow_events(db, rule_id, rule_version, events, shadow_type="candidate_probe"):
    """Insert shadow events. events is a list of (label, session_id) tuples."""
    now = now_iso()
    with db.transaction() as tx:
        for label, session_id in events:
            tx.execute(
                "INSERT INTO rule_shadow_events "
                "(id, rule_id, session_id, shadow_rule_version, shadow_label, "
                "shadow_type, context_fingerprint, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()), rule_id, session_id, rule_version,
                    label, shadow_type, str(uuid.uuid4()), now,
                ),
            )


def _insert_fire_events(db, rule_id, events):
    """Insert fire events. events is a list of (label, session_id, score) tuples."""
    now = now_iso()
    with db.transaction() as tx:
        for label, session_id, score in events:
            tx.execute(
                "INSERT INTO rule_fire_events "
                "(id, rule_id, session_id, prompt_hash, level, "
                "posthoc_label, posthoc_score, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()), rule_id, session_id,
                    str(uuid.uuid4()), "hot", label, score, now,
                ),
            )


class TestCandidateBarriers:
    def test_no_evidence_all_unmet(self, db):
        _insert_rule(db, status="candidate")
        result = compute_promotion_barriers(db, "rule-1", "candidate", 1)
        assert result is not None
        assert result["current_state"] == "candidate"
        assert result["target_state"] == "active"
        assert len(result["thresholds"]) == 6
        unmet_names = {t["name"] for t in result["thresholds"] if not t["met"]}
        assert unmet_names == {
            "shadow_strong_match_count",
            "evaluated_shadow_match_count",
            "distinct_shadow_sessions",
            "counterfactual_would_help_high",
        }
        assert result["blocking"] == "shadow_strong_match_count"

    def test_partial_evidence(self, db):
        _insert_rule(db, status="candidate")
        # 2 would_help_high from 1 session: strong=2 (< 3), evaluated=3 (< 5), sessions=1 (< 2)
        _insert_shadow_events(db, "rule-1", 1, [
            ("would_help_high", "sess-1"),
            ("would_help_high", "sess-1"),
            ("would_help_low", "sess-1"),
        ])
        result = compute_promotion_barriers(db, "rule-1", "candidate", 1)
        assert result["blocking"] is not None
        th_map = {t["name"]: t for t in result["thresholds"]}
        assert th_map["counterfactual_would_help_high"]["met"] is True  # 2 >= 2
        assert th_map["distinct_shadow_sessions"]["met"] is False  # 1 < 2
        assert th_map["evaluated_shadow_match_count"]["met"] is False  # 3 < 5

    def test_all_thresholds_met(self, db):
        _insert_rule(db, status="candidate")
        # Need: strong(would_help_high) >= 3, evaluated(sum of all labels) >= 5,
        # distinct_sessions >= 2, counterfactual(would_help_high) >= 2.
        # Use 7 events across 5 sessions to exceed all min thresholds.
        _insert_shadow_events(db, "rule-1", 1, [
            ("would_help_high", "sess-1"),
            ("would_help_high", "sess-1"),
            ("would_help_high", "sess-1"),
            ("would_help_high", "sess-2"),
            ("would_help_high", "sess-3"),
            ("would_help_high", "sess-4"),
            ("would_help_high", "sess-5"),
        ])
        result = compute_promotion_barriers(db, "rule-1", "candidate", 1)
        # All should be met
        assert result["blocking"] is None
        for th in result["thresholds"]:
            assert th["met"] is True


class TestActiveBarriers:
    def test_no_fire_evidence(self, db):
        _insert_rule(db, status="active")
        result = compute_promotion_barriers(db, "rule-1", "active", 1)
        assert result is not None
        assert result["current_state"] == "active"
        assert result["target_state"] == "trusted"
        assert len(result["thresholds"]) == 5
        # harmful_count (0 <= 0) and fp_rate (0 <= 0.15) are met with no evidence
        th_map = {t["name"]: t for t in result["thresholds"]}
        assert th_map["harmful_count"]["met"] is True
        assert th_map["recent_false_positive_rate"]["met"] is True
        assert th_map["observed_useful_count"]["met"] is False
        assert result["blocking"] == "observed_useful_count"

    def test_with_fire_evidence(self, db):
        _insert_rule(db, status="active")
        # 3 observed_useful strong, from 2 sessions, 5 total evaluated
        _insert_fire_events(db, "rule-1", [
            ("observed_useful", "sess-1", 0.9),
            ("observed_useful", "sess-1", 0.8),
            ("observed_useful", "sess-2", 0.9),
            ("irrelevant", "sess-3", None),
            ("irrelevant", "sess-3", None),
        ])
        result = compute_promotion_barriers(db, "rule-1", "active", 1)
        th_map = {t["name"]: t for t in result["thresholds"]}
        assert th_map["observed_useful_count"]["current"] == 3
        assert th_map["evaluated_fire_count"]["current"] == 5
        assert th_map["distinct_observed_useful_sessions"]["current"] == 2
        assert result["blocking"] is None
        for th in result["thresholds"]:
            assert th["met"] is True


class TestSuppressedBarriers:
    def test_suppressed_no_evidence(self, db):
        suppressed_at = now_iso()
        _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)
        result = compute_promotion_barriers(db, "rule-1", "suppressed", 1, suppressed_at)
        assert result is not None
        assert result["current_state"] == "suppressed"
        assert result["target_state"] == "active"
        assert len(result["thresholds"]) == 3
        th_map = {t["name"]: t for t in result["thresholds"]}
        assert th_map["shadow_recovery_would_help_high"]["met"] is False
        assert th_map["recent_harmful_count"]["met"] is True
        assert result["blocking"] == "shadow_recovery_would_help_high"

    def test_suppressed_with_harmful(self, db):
        suppressed_at = now_iso()
        _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)
        _insert_fire_events(db, "rule-1", [
            ("harmful", "sess-1", None),
        ])
        result = compute_promotion_barriers(db, "rule-1", "suppressed", 1, suppressed_at)
        th_map = {t["name"]: t for t in result["thresholds"]}
        assert th_map["recent_harmful_count"]["current"] == 1
        assert th_map["recent_harmful_count"]["met"] is False

    def test_suppressed_recovery_all_met(self, db):
        suppressed_at = now_iso()
        _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)
        _insert_shadow_events(db, "rule-1", 1, [
            ("would_help_high", "sess-1"),
            ("would_help_high", "sess-1"),
            ("would_help_high", "sess-2"),
        ], shadow_type="suppression_recovery")
        result = compute_promotion_barriers(db, "rule-1", "suppressed", 1, suppressed_at)
        assert result["blocking"] is None
        for th in result["thresholds"]:
            assert th["met"] is True


class TestTerminalStates:
    def test_trusted_returns_none(self, db):
        _insert_rule(db, status="trusted")
        result = compute_promotion_barriers(db, "rule-1", "trusted", 1)
        assert result is None

    def test_archived_returns_none(self, db):
        _insert_rule(db, status="archived")
        result = compute_promotion_barriers(db, "rule-1", "archived", 1)
        assert result is None
