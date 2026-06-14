"""Characterization tests for lifecycle transitions.

These tests capture the current behavior of the full evaluate_transitions()
pipeline with known seeded data. They serve as a regression safety net:
if the refactored pure evaluators diverge from the original behavior,
these tests will catch it.

Each test seeds a DB with a specific rule state and events, then asserts
the transition outcome (status change + reason substring).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nokori.db import Db, open_db
from nokori.lifecycle.transitions import evaluate_transitions
from nokori.policy import (
    RUNTIME_POLICY_VERSION,
    SUPPRESSION_TTL_DAYS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso(delta_days: float = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=delta_days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _fresh_db(tmp_path: Path) -> Db:
    return open_db(tmp_path / "rules.db")


def _insert_rule(
    db: Db,
    *,
    rule_id: str | None = None,
    status: str = "candidate",
    rule_version: int = 1,
    runtime_policy_version: str = RUNTIME_POLICY_VERSION,
    replacement_id: str | None = None,
    suppressed_at: str | None = None,
    project_scope: str = "project",
) -> str:
    rid = rule_id or str(uuid.uuid4())
    short = rid[:8]
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, rule_version, runtime_policy_version, status, severity, "
            "trigger_canonical, concepts, action_instruction, "
            "replacement_id, suppressed_at, project_scope, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                short,
                rule_version,
                runtime_policy_version,
                status,
                "reminder",
                "test trigger",
                "[]",
                "test action",
                replacement_id,
                suppressed_at,
                project_scope,
                now,
                now,
            ),
        )
    return rid


def _insert_synthetic_eval(db: Db, rule_id: str, rule_version: int, passed: bool) -> None:
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_synthetic_evals (rule_id, rule_version, passed, created_at) "
            "VALUES (?,?,?,?)",
            (rule_id, rule_version, 1 if passed else 0, now),
        )


def _insert_shadow_event(
    db: Db,
    rule_id: str,
    *,
    rule_version: int = 1,
    session_id: str | None = None,
    label: str = "would_help_high",
    fingerprint: str | None = None,
    days_ago: float = 0,
    shadow_type: str = "candidate_probe",
    decision_features: dict | None = None,
) -> str:
    eid = str(uuid.uuid4())
    sid = session_id or str(uuid.uuid4())
    ts = _utcnow_iso(-days_ago)
    fp = fingerprint or str(uuid.uuid4())
    status_at_match = "suppressed" if shadow_type == "suppression_recovery" else "candidate"
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_shadow_events "
            "(id, rule_id, session_id, shadow_rule_version, shadow_label, "
            "context_fingerprint, status_at_match, shadow_type, prompt_hash, "
            "matched_level, decision_features, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid,
                rule_id,
                sid,
                rule_version,
                label,
                fp,
                status_at_match,
                shadow_type,
                "hash",
                "warm_candidate",
                json.dumps(decision_features or {}),
                ts,
            ),
        )
    return eid


def _insert_fire_event(
    db: Db,
    rule_id: str,
    *,
    session_id: str | None = None,
    label: str | None = None,
    reason_code: str | None = None,
    posthoc_score: float | None = None,
    days_ago: float = 0,
) -> str:
    eid = str(uuid.uuid4())
    sid = session_id or str(uuid.uuid4())
    ts = _utcnow_iso(-days_ago)
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_fire_events "
            "(id, rule_id, session_id, posthoc_label, posthoc_reason_code, "
            "posthoc_score, level, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (eid, rule_id, sid, label, reason_code, posthoc_score, "warm", ts),
        )
    return eid



# ---------------------------------------------------------------------------
# Characterization: candidate transitions
# ---------------------------------------------------------------------------


class TestCharacterizationCandidate:
    """Capture known-good candidate transition outcomes."""

    def test_candidate_no_evidence_stays(self, tmp_path):
        """Candidate with no evidence stays candidate."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            result = evaluate_transitions(db, rid)
            assert result.new_status is None
            assert result.applied is False
        finally:
            db.close()

    def test_candidate_promotes_normal_path(self, tmp_path):
        """Candidate promotes with sufficient multi-session shadow evidence."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)
            sess1, sess2 = str(uuid.uuid4()), str(uuid.uuid4())
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_low")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_low")
            result = evaluate_transitions(db, rid)
            assert result.new_status == "active"
            assert result.applied is True
        finally:
            db.close()

    def test_candidate_archives_on_risky(self, tmp_path):
        """Candidate archives when risky count >= 2."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_shadow_event(db, rid, label="risky")
            _insert_shadow_event(db, rid, label="risky")
            result = evaluate_transitions(db, rid)
            assert result.new_status == "archived"
        finally:
            db.close()

    def test_candidate_archives_on_replacement(self, tmp_path):
        """Candidate with replacement_id gets archived."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate", replacement_id="some-id")
            result = evaluate_transitions(db, rid)
            assert result.new_status == "archived"
            assert "replacement" in result.reason
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Characterization: active transitions
# ---------------------------------------------------------------------------


class TestCharacterizationActive:
    """Capture known-good active transition outcomes."""

    def test_active_no_events_stays(self, tmp_path):
        """Active rule with no fire events stays active."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")
            result = evaluate_transitions(db, rid)
            assert result.new_status is None
        finally:
            db.close()

    def test_active_suppresses_on_harmful(self, tmp_path):
        """Active suppresses on any harmful event."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")
            _insert_fire_event(db, rid, label="harmful", reason_code="harmful_distracted")
            result = evaluate_transitions(db, rid)
            assert result.new_status == "suppressed"
        finally:
            db.close()

    def test_active_promotes_to_trusted(self, tmp_path):
        """Active promotes to trusted with sufficient observed_useful."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")
            s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
            _insert_fire_event(db, rid, session_id=s1, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=s1, label="observed_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=s2, label="observed_useful",
                               reason_code="useful_followed_preference")
            _insert_fire_event(db, rid, session_id=s1, label="plausible_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=s2, label="plausible_useful",
                               reason_code="useful_prevented_error")
            result = evaluate_transitions(db, rid)
            assert result.new_status == "trusted"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Characterization: trusted transitions
# ---------------------------------------------------------------------------


class TestCharacterizationTrusted:
    """Capture known-good trusted transition outcomes."""

    def test_trusted_no_events_stays(self, tmp_path):
        """Trusted rule with no fire events stays trusted."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="trusted")
            result = evaluate_transitions(db, rid)
            assert result.new_status is None
        finally:
            db.close()

    def test_trusted_suppresses_on_harmful(self, tmp_path):
        """Trusted suppresses on harmful event."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="trusted")
            _insert_fire_event(db, rid, label="harmful", reason_code="harmful_distracted")
            result = evaluate_transitions(db, rid)
            assert result.new_status == "suppressed"
        finally:
            db.close()

    def test_trusted_decays_to_active(self, tmp_path):
        """Trusted decays to active when evidence shows degradation."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="trusted")
            sess = str(uuid.uuid4())
            # 10 events: 0 useful, 3 irrelevant with FP codes, 7 plausible
            # FP rate = 3/10 = 0.30 >= 0.30, irrelevant >= 2
            _insert_fire_event(db, rid, session_id=sess, label="irrelevant",
                               reason_code="irrelevant_not_applicable", days_ago=9)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=8)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=7)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=6)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=5)
            _insert_fire_event(db, rid, session_id=sess, label="irrelevant",
                               reason_code="irrelevant_not_applicable", days_ago=4)
            _insert_fire_event(db, rid, session_id=sess, label="irrelevant",
                               reason_code="irrelevant_not_applicable", days_ago=3)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=2)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=1)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=0)
            result = evaluate_transitions(db, rid)
            assert result.new_status == "active"
            assert "decay" in result.reason
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Characterization: suppressed transitions
# ---------------------------------------------------------------------------


class TestCharacterizationSuppressed:
    """Capture known-good suppressed transition outcomes."""

    def test_suppressed_recovers_with_evidence(self, tmp_path):
        """Suppressed recovers to active with sufficient recovery evidence."""
        db = _fresh_db(tmp_path)
        try:
            suppressed_at = _utcnow_iso(-5)
            rid = _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)
            s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
            _insert_shadow_event(db, rid, session_id=s1, label="would_help_high",
                                 shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=s1, label="would_help_high",
                                 shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=s2, label="would_help_high",
                                 shadow_type="suppression_recovery")
            result = evaluate_transitions(db, rid)
            assert result.new_status == "active"
            assert "recovery" in result.reason
        finally:
            db.close()

    def test_suppressed_archives_on_ttl_expiry(self, tmp_path):
        """Suppressed archives after TTL expires without recovery."""
        db = _fresh_db(tmp_path)
        try:
            suppressed_at = _utcnow_iso(-(SUPPRESSION_TTL_DAYS + 1))
            rid = _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)
            _insert_shadow_event(db, rid, label="would_help_low",
                                 shadow_type="suppression_recovery")
            result = evaluate_transitions(db, rid)
            assert result.new_status == "archived"
        finally:
            db.close()

    def test_suppressed_archives_on_risky_after_suppression(self, tmp_path):
        """Suppressed archives when risky events appear after suppression."""
        db = _fresh_db(tmp_path)
        try:
            suppressed_at = _utcnow_iso(-5)
            rid = _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)
            _insert_shadow_event(db, rid, label="risky", shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, label="risky", shadow_type="suppression_recovery")
            result = evaluate_transitions(db, rid)
            assert result.new_status == "archived"
        finally:
            db.close()

    def test_suppressed_null_suppressed_at_stays(self, tmp_path):
        """Suppressed rule with NULL suppressed_at cannot be evaluated."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="suppressed", suppressed_at=None)
            result = evaluate_transitions(db, rid)
            assert result.new_status is None
            assert "missing" in result.reason
        finally:
            db.close()
