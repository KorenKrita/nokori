"""Tests for nokori.lifecycle.transitions — the autonomous rule quality flywheel.

Covers all state transitions, CAS semantics, false-positive rate calculation,
derived score recomputation, and attribution down-weighting constraints.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nokori.db import open_db, Db
from nokori.lifecycle.transitions import (
    compute_false_positive_rate,
    evaluate_transitions,
    update_derived_scores,
)
from nokori.policy import (
    CandidateToActiveSingleSessionThresholds,
    CandidateToActiveThresholds,
    RECENT_TIME_WINDOW_DAYS,
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
) -> str:
    rid = rule_id or str(uuid.uuid4())
    short = rid[:8]
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, rule_version, runtime_policy_version, status, severity, "
            "trigger_canonical, concepts, action_instruction, "
            "replacement_id, suppressed_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
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


def _insert_feedback_event(db: Db, fire_event_id: str, label: str) -> None:
    eid = str(uuid.uuid4())
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_feedback_events (id, fire_event_id, label, created_at) "
            "VALUES (?,?,?,?)",
            (eid, fire_event_id, label, now),
        )


def _insert_review(db: Db, rule_id: str, overall_quality: float) -> None:
    now = _utcnow_iso()
    scores = json.dumps({"overall_quality": overall_quality})
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_reviews (rule_id, decision, scores, created_at) "
            "VALUES (?,?,?,?)",
            (rule_id, "accept_active", scores, now),
        )


# ---------------------------------------------------------------------------
# 1. candidate -> active (normal path)
# ---------------------------------------------------------------------------


class TestCandidateToActive:
    def test_promotion_with_sufficient_evidence(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # 3 strong matches (would_help_high + would_help_low)
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high")

            # Evaluated count >= 5
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_low")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_low")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "active"
            assert result.applied is True

            row = db.fetchone("SELECT status, rule_version FROM rules WHERE id = ?", (rid,))
            assert row["status"] == "active"
            assert row["rule_version"] == 2
        finally:
            db.close()

    def test_insufficient_sessions_blocks_promotion(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)

            sess1 = str(uuid.uuid4())
            # All in one session — needs distinct_sessions >= 2
            for _ in range(5):
                _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")

            result = evaluate_transitions(db, rid)
            assert result.new_status is None
            assert result.applied is False
        finally:
            db.close()

    def test_false_positive_blocks_promotion(self, tmp_path):
        """Zero false positives required (risky_or_near_miss_shadow_count_max=0)."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_low")
            # One risky event blocks promotion
            _insert_shadow_event(db, rid, session_id=sess2, label="risky")

            result = evaluate_transitions(db, rid)
            assert result.new_status is None
        finally:
            db.close()

    def test_synthetic_eval_not_passed_blocks(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=False)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())
            for s in [sess1, sess1, sess2, sess1, sess2]:
                _insert_shadow_event(db, rid, session_id=s, label="would_help_high")

            result = evaluate_transitions(db, rid)
            assert result.new_status is None
            assert "synthetic_eval" in result.reason
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 2. Single-session exception
# ---------------------------------------------------------------------------


class TestCandidateToActiveSingleSession:
    def test_single_session_with_quality_and_correction(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)
            _insert_review(db, rid, overall_quality=0.92)

            sess = str(uuid.uuid4())
            # 3 strong matches in one session, counterfactual_would_help_high >= 3
            _insert_shadow_event(
                db,
                rid,
                session_id=sess,
                label="would_help_high",
                decision_features={"user_correction": True},
            )
            _insert_shadow_event(db, rid, session_id=sess, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess, label="would_help_high")
            # Evaluated >= 5
            _insert_shadow_event(db, rid, session_id=sess, label="would_help_low")
            _insert_shadow_event(db, rid, session_id=sess, label="would_help_low")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "active"
            assert result.applied is True
            assert "single_session_exception" in result.reason
        finally:
            db.close()

    def test_single_session_without_correction_blocked(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)
            _insert_review(db, rid, overall_quality=0.92)

            sess = str(uuid.uuid4())
            for _ in range(5):
                _insert_shadow_event(db, rid, session_id=sess, label="would_help_high")

            # No correction / agent_miss — should not promote
            result = evaluate_transitions(db, rid)
            assert result.new_status is None
        finally:
            db.close()

    def test_single_session_low_quality_blocked(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)
            _insert_review(db, rid, overall_quality=0.70)  # Below 0.88

            sess = str(uuid.uuid4())
            _insert_shadow_event(
                db,
                rid,
                session_id=sess,
                label="would_help_high",
                decision_features={"user_correction": True},
            )
            for _ in range(4):
                _insert_shadow_event(db, rid, session_id=sess, label="would_help_high")

            result = evaluate_transitions(db, rid)
            assert result.new_status is None
        finally:
            db.close()

    def test_single_session_requires_counterfactuals_in_same_session(
        self, tmp_path, monkeypatch
    ):
        db = _fresh_db(tmp_path)
        try:
            import nokori.lifecycle.transitions as transitions

            monkeypatch.setattr(
                transitions,
                "CANDIDATE_TO_ACTIVE",
                CandidateToActiveThresholds(distinct_shadow_sessions_min=99),
            )
            monkeypatch.setattr(
                transitions,
                "CANDIDATE_TO_ACTIVE_SINGLE_SESSION",
                CandidateToActiveSingleSessionThresholds(
                    shadow_strong_match_count_min=2,
                    counterfactual_would_help_high_min=3,
                ),
            )
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)
            _insert_review(db, rid, overall_quality=0.92)

            sess_a = str(uuid.uuid4())
            sess_b = str(uuid.uuid4())
            _insert_shadow_event(
                db,
                rid,
                session_id=sess_a,
                label="would_help_high",
                decision_features={"user_correction": True},
            )
            _insert_shadow_event(db, rid, session_id=sess_a, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess_a, label="would_help_low")
            _insert_shadow_event(db, rid, session_id=sess_b, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess_b, label="would_help_low")

            result = evaluate_transitions(db, rid)

            assert result.new_status is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 3. candidate -> archived
# ---------------------------------------------------------------------------


class TestCandidateToArchived:
    def test_risky_count_archives(self, tmp_path):
        """risky >= 2 triggers archive."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")

            _insert_shadow_event(db, rid, label="risky")
            _insert_shadow_event(db, rid, label="risky")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "archived"
            assert result.applied is True
        finally:
            db.close()

    def test_irrelevant_count_archives(self, tmp_path):
        """irrelevant >= 5 triggers archive."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")

            for _ in range(5):
                _insert_shadow_event(db, rid, label="irrelevant")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "archived"
            assert "irrelevant" in result.reason
        finally:
            db.close()

    def test_covered_by_replacement_archives(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            replacement_id = str(uuid.uuid4())
            rid = _insert_rule(db, status="candidate", replacement_id=replacement_id)

            result = evaluate_transitions(db, rid)
            assert result.new_status == "archived"
            assert "replacement" in result.reason
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 4. active -> trusted
# ---------------------------------------------------------------------------


class TestActiveToTrusted:
    def test_promotion_with_sufficient_fire_evidence(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # observed_useful >= 3 across distinct_sessions >= 2
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess2, label="observed_useful",
                               reason_code="useful_followed_preference")

            # Pad to evaluated_fire >= 5
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess2, label="plausible_useful",
                               reason_code="useful_prevented_error")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "trusted"
            assert result.applied is True

            row = db.fetchone("SELECT status, trusted_at FROM rules WHERE id = ?", (rid,))
            assert row["status"] == "trusted"
            assert row["trusted_at"] is not None
        finally:
            db.close()

    def test_null_runtime_policy_version_does_not_lock_transition(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active", runtime_policy_version=None)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            _insert_fire_event(
                db, rid, session_id=sess1, label="observed_useful",
                reason_code="useful_prevented_error"
            )
            _insert_fire_event(
                db, rid, session_id=sess1, label="observed_useful",
                reason_code="useful_improved_quality"
            )
            _insert_fire_event(
                db, rid, session_id=sess2, label="observed_useful",
                reason_code="useful_followed_preference"
            )
            _insert_fire_event(
                db, rid, session_id=sess1, label="plausible_useful",
                reason_code="useful_improved_quality"
            )
            _insert_fire_event(
                db, rid, session_id=sess2, label="plausible_useful",
                reason_code="useful_prevented_error"
            )

            result = evaluate_transitions(db, rid)

            assert result.new_status == "trusted"
            assert result.applied is True
            row = db.fetchone(
                "SELECT status, runtime_policy_version FROM rules WHERE id = ?",
                (rid,),
            )
            assert row["status"] == "trusted"
            assert row["runtime_policy_version"] == RUNTIME_POLICY_VERSION
        finally:
            db.close()

    def test_legacy_null_posthoc_score_counts_as_strong_attribution(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")
            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess2, label="observed_useful",
                               reason_code="useful_followed_preference")
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess2, label="plausible_useful",
                               reason_code="useful_prevented_error")

            result = evaluate_transitions(db, rid)

            assert result.new_status == "trusted"
        finally:
            db.close()

    def test_weak_observed_useful_score_does_not_promote_to_trusted(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")
            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_prevented_error", posthoc_score=0.5)
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_improved_quality", posthoc_score=0.5)
            _insert_fire_event(db, rid, session_id=sess2, label="observed_useful",
                               reason_code="useful_followed_preference", posthoc_score=0.5)
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_improved_quality", posthoc_score=0.3)
            _insert_fire_event(db, rid, session_id=sess2, label="plausible_useful",
                               reason_code="useful_prevented_error", posthoc_score=0.3)

            result = evaluate_transitions(db, rid)

            assert result.new_status is None
        finally:
            db.close()

    def test_harmful_blocks_trusted_promotion(self, tmp_path):
        """harmful=0 required."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess2, label="observed_useful",
                               reason_code="useful_followed_preference")
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_improved_quality")
            # One harmful blocks
            _insert_fire_event(db, rid, session_id=sess2, label="harmful",
                               reason_code="harmful_distracted")

            result = evaluate_transitions(db, rid)
            # Harmful triggers suppression instead
            assert result.new_status == "suppressed"
        finally:
            db.close()

    def test_fp_rate_blocks_trusted_promotion(self, tmp_path):
        """FP_rate <= 0.15 required."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # 3 observed_useful
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess2, label="observed_useful",
                               reason_code="useful_followed_preference")

            # 2 FP events out of 5 total = 0.40 > 0.15
            _insert_fire_event(db, rid, session_id=sess1, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, session_id=sess2, label="irrelevant",
                               reason_code="irrelevant_not_applicable")

            result = evaluate_transitions(db, rid)
            # High FP rate should not allow trusted promotion
            assert result.new_status != "trusted"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 5. active -> suppressed
# ---------------------------------------------------------------------------


class TestActiveToSuppressed:
    def test_harmful_suppresses(self, tmp_path):
        """harmful >= 1 triggers suppression."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            _insert_fire_event(db, rid, label="harmful",
                               reason_code="harmful_blocked_valid_action")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "suppressed"
            assert result.applied is True

            row = db.fetchone("SELECT suppressed_at FROM rules WHERE id = ?", (rid,))
            assert row["suppressed_at"] is not None
        finally:
            db.close()

    def test_irrelevant_in_last_5_suppresses(self, tmp_path):
        """irrelevant >= 3 in last 5 evaluated fire events."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            # 3 irrelevant in last 5
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, label="plausible_useful",
                               reason_code="useful_improved_quality")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "suppressed"
        finally:
            db.close()

    def test_fp_rate_suppresses(self, tmp_path):
        """FP_rate >= 0.50 with sufficient denominator triggers suppression."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            # 3 FP events out of 5 total = 0.60 >= 0.50
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="harmful_wrong_scope")
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="harmful_blocked_valid_action")
            _insert_fire_event(db, rid, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, label="plausible_useful",
                               reason_code="useful_improved_quality")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "suppressed"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 6. trusted -> active (decay)
# ---------------------------------------------------------------------------


class TestTrustedToActive:
    def test_decay_with_no_useful_and_high_irrelevant(self, tmp_path):
        """evaluated >= 5, useful = 0, irrelevant >= 2, harmful = 0, FP >= 0.30.

        Must avoid triggering trusted->suppressed (FP >= 0.35, irrelevant_in_last_5 >= 3).
        Use 10 events: 2 irrelevant with FP reason + 1 irrelevant with non-FP reason
        = FP rate 3/10 = 0.30, irrelevant_in_last_5 = depends on ordering.
        Actually need: 3 FP events / 10 total = 0.30 >= 0.30 AND < 0.35.
        And irrelevant_in_last_5 < 3.
        """
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="trusted")

            sess = str(uuid.uuid4())
            # 10 events total, 0 observed_useful, 3 FP reason codes, irrelevant label = 2 in last 5
            # Last 5 (most recent first): plausible x3, irrelevant x2
            # So irrelevant_in_last_5 = 2 < 3 (no suppression via that path)
            # FP rate = 3/10 = 0.30 >= 0.30 (decay triggers) and < 0.35 (no suppression)
            # irrelevant label count total = 3 >= 2 (decay requires >= 2)

            # Older events (inserted first = oldest created_at due to days_ago=0 all same)
            # We need to control ordering: use days_ago to ensure last-5 ordering
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
            # Last 5 (most recent):
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

    def test_no_decay_when_useful_present(self, tmp_path):
        """observed_useful > 0 blocks decay (max is 0)."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="trusted")

            sess = str(uuid.uuid4())
            # 10 events, 1 observed_useful > max of 0, FP < 0.35 to avoid suppression
            # Use irrelevant_redundant (non-FP) for irrelevant events
            _insert_fire_event(db, rid, session_id=sess, label="observed_useful",
                               reason_code="useful_prevented_error", days_ago=9)
            _insert_fire_event(db, rid, session_id=sess, label="irrelevant",
                               reason_code="irrelevant_redundant", days_ago=8)
            _insert_fire_event(db, rid, session_id=sess, label="irrelevant",
                               reason_code="irrelevant_redundant", days_ago=7)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=6)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=5)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=4)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=3)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=2)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=1)
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=0)

            result = evaluate_transitions(db, rid)
            assert result.new_status is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 7. trusted -> suppressed
# ---------------------------------------------------------------------------


class TestTrustedToSuppressed:
    def test_harmful_suppresses_trusted(self, tmp_path):
        """harmful >= 1 triggers suppression from trusted."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="trusted")

            _insert_fire_event(db, rid, label="harmful",
                               reason_code="harmful_distracted")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "suppressed"
        finally:
            db.close()

    def test_irrelevant_in_last_5_suppresses_trusted(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="trusted")

            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, label="plausible_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, label="plausible_useful",
                               reason_code="useful_improved_quality")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "suppressed"
        finally:
            db.close()

    def test_fp_rate_suppresses_trusted(self, tmp_path):
        """FP_rate >= 0.35 with sufficient denominator triggers suppression."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="trusted")

            # 2 FP out of 5 = 0.40 >= 0.35
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, label="irrelevant",
                               reason_code="harmful_wrong_scope")
            _insert_fire_event(db, rid, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, label="plausible_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, label="plausible_useful",
                               reason_code="useful_improved_quality")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "suppressed"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 8. suppressed -> active (recovery)
# ---------------------------------------------------------------------------


class TestSuppressedToActive:
    def test_recovery_with_sufficient_shadow_evidence(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            suppressed_at = _utcnow_iso(-5)
            rid = _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # recovery_would_help_high >= 3, distinct_sessions >= 2
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high", shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high", shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high", shadow_type="suppression_recovery")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "active"
            assert "recovery" in result.reason
        finally:
            db.close()

    def test_recent_harmful_blocks_recovery(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            suppressed_at = _utcnow_iso(-5)
            rid = _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high", shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high", shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high", shadow_type="suppression_recovery")

            # Harmful fire event after suppression
            _insert_fire_event(db, rid, label="harmful",
                               reason_code="harmful_distracted", days_ago=2)

            result = evaluate_transitions(db, rid)
            assert result.new_status is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 9. suppressed -> archived
# ---------------------------------------------------------------------------


class TestSuppressedToArchived:
    def test_risky_after_suppression_archives(self, tmp_path):
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

    def test_no_recovery_before_ttl_archives(self, tmp_path):
        """No recovery evidence before TTL expiry triggers archive."""
        db = _fresh_db(tmp_path)
        try:
            # Suppressed beyond TTL
            suppressed_at = _utcnow_iso(-(SUPPRESSION_TTL_DAYS + 1))
            rid = _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)

            # No would_help_high evidence
            _insert_shadow_event(db, rid, label="would_help_low", shadow_type="suppression_recovery")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "archived"
            assert "ttl" in result.reason
        finally:
            db.close()

    def test_recovery_before_ttl_expiry_succeeds(self, tmp_path):
        """Recovery evidence BEFORE TTL expiry allows promotion to active."""
        db = _fresh_db(tmp_path)
        try:
            # Not yet expired (within TTL)
            suppressed_at = _utcnow_iso(-5)
            rid = _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # Meet recovery threshold
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high", shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high", shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high", shadow_type="suppression_recovery")

            result = evaluate_transitions(db, rid)
            # Recovery before TTL expiry → active
            assert result.new_status == "active"
        finally:
            db.close()

    def test_recovery_after_ttl_expiry_archives_regardless(self, tmp_path):
        """After TTL expiry, recovery evidence does NOT prevent archival (spec M14)."""
        db = _fresh_db(tmp_path)
        try:
            suppressed_at = _utcnow_iso(-(SUPPRESSION_TTL_DAYS + 1))
            rid = _insert_rule(db, status="suppressed", suppressed_at=suppressed_at)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high", shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high", shadow_type="suppression_recovery")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high", shadow_type="suppression_recovery")

            result = evaluate_transitions(db, rid)
            # TTL expired → archived regardless of recovery evidence
            assert result.new_status == "archived"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 9b. Counterfactual/plausible_useful cannot promote to trusted
# ---------------------------------------------------------------------------


class TestCounterfactualCannotPromoteToTrusted:
    def test_plausible_useful_cannot_promote_active_to_trusted(self, tmp_path):
        """plausible_useful events cannot promote active to trusted (spec 3.3)."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")
            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # Insert many plausible_useful (not observed_useful)
            for _ in range(5):
                _insert_fire_event(db, rid, session_id=sess1,
                                   label="plausible_useful", reason_code="useful_prevented_error")
            for _ in range(3):
                _insert_fire_event(db, rid, session_id=sess2,
                                   label="plausible_useful", reason_code="useful_improved_quality")

            result = evaluate_transitions(db, rid)
            # Should NOT promote to trusted (only observed_useful counts)
            assert result.new_status != "trusted"
        finally:
            db.close()

    def test_only_observed_useful_promotes_to_trusted(self, tmp_path):
        """Only observed_useful (not plausible) can promote active to trusted."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")
            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # 3 observed_useful across 2 sessions with 5+ evaluated total
            _insert_fire_event(db, rid, session_id=sess1,
                               label="observed_useful", reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess1,
                               label="observed_useful", reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess2,
                               label="observed_useful", reason_code="useful_followed_preference")
            _insert_fire_event(db, rid, session_id=sess1,
                               label="plausible_useful", reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess2,
                               label="plausible_useful", reason_code="useful_improved_quality")

            result = evaluate_transitions(db, rid)
            assert result.new_status == "trusted"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 10. CAS: stale job doesn't apply
# ---------------------------------------------------------------------------


class TestCAS:
    def test_stale_rule_version_prevents_transition(self, tmp_path):
        """If rule_version changed between read and write, CAS fails."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_low")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_low")

            # Simulate concurrent modification: bump rule_version
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE rules SET rule_version = rule_version + 1 WHERE id = ?",
                    (rid,),
                )

            result = evaluate_transitions(db, rid)
            # The evaluate reads version 2, but shadow events are tagged version 1
            # So the shadow evidence aggregation returns 0 (version-filtered).
            # The transition should not apply.
            assert result.applied is False
        finally:
            db.close()

    def test_old_policy_version_does_not_block_transition(self, tmp_path):
        """CAS checks runtime_policy_version match."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(
                db, status="active", runtime_policy_version="0.9.0"
            )

            sess1 = str(uuid.uuid4())
            _insert_fire_event(db, rid, session_id=sess1, label="harmful",
                               reason_code="harmful_distracted")

            # The rule has rpv="0.9.0" but _apply_transition uses it from the row.
            # The CAS WHERE clause checks runtime_policy_version, so the update
            # should still succeed because it reads and writes the same value.
            result = evaluate_transitions(db, rid)
            # With rpv="0.9.0" in the row, CAS should still match since
            # the code reads rpv from the row itself.
            assert result.applied is True
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 11. false_positive_rate calculation
# ---------------------------------------------------------------------------


class TestFalsePositiveRate:
    def test_basic_fp_calculation(self):
        events = {
            "total_evaluated": 10,
            "unclear": 0,  # unclear already excluded by SQL query
            "reason_counts": {
                "irrelevant_not_applicable": 2,
                "harmful_wrong_scope": 1,
            },
        }
        # fp = (2 + 1) / 10 = 3/10 = 0.3
        assert abs(compute_false_positive_rate(events) - 0.3) < 1e-6

    def test_denominator_uses_total_evaluated_directly(self):
        """unclear events are pre-filtered by SQL; denominator = total_evaluated."""
        events = {
            "total_evaluated": 5,
            "unclear": 0,
            "reason_counts": {
                "irrelevant_not_applicable": 1,
            },
        }
        # fp = 1 / 5 = 0.2
        assert abs(compute_false_positive_rate(events) - 0.2) < 1e-6

    def test_zero_denominator_returns_zero(self):
        events = {
            "total_evaluated": 0,
            "unclear": 0,
            "reason_counts": {},
        }
        assert compute_false_positive_rate(events) == 0.0

    def test_all_fp_reason_codes_counted(self):
        """All 4 FP reason codes contribute to numerator."""
        events = {
            "total_evaluated": 20,
            "unclear": 0,
            "reason_counts": {
                "irrelevant_not_applicable": 1,
                "harmful_wrong_scope": 1,
                "harmful_blocked_valid_action": 1,
                "harmful_distracted": 1,
                # Not FP:
                "useful_prevented_error": 5,
                "irrelevant_redundant": 3,
            },
        }
        # fp = 4 / 20 = 0.2
        assert abs(compute_false_positive_rate(events) - 0.2) < 1e-6

    def test_non_fp_reason_codes_excluded(self):
        """irrelevant_redundant is NOT in FALSE_POSITIVE_REASON_CODES."""
        events = {
            "total_evaluated": 10,
            "unclear": 0,
            "reason_counts": {
                "irrelevant_redundant": 5,
                "useful_prevented_error": 5,
            },
        }
        assert compute_false_positive_rate(events) == 0.0


# ---------------------------------------------------------------------------
# 12. Scores are derived caches recomputed from events
# ---------------------------------------------------------------------------


class TestDerivedScores:
    def test_scores_recomputed_from_events(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            sess = str(uuid.uuid4())
            _insert_fire_event(db, rid, session_id=sess, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess, label="observed_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess, label="irrelevant",
                               reason_code="irrelevant_not_applicable")
            _insert_fire_event(db, rid, session_id=sess, label="plausible_useful",
                               reason_code="useful_followed_preference")
            _insert_fire_event(db, rid, session_id=sess, label="harmful",
                               reason_code="harmful_distracted")

            update_derived_scores(db, rid)

            row = db.fetchone(
                "SELECT observed_usefulness_score, plausible_usefulness_score, "
                "false_positive_score, harmful_score FROM rules WHERE id = ?",
                (rid,),
            )
            # 2 observed_useful / 5 total = 0.4
            assert abs(row["observed_usefulness_score"] - 0.4) < 1e-6
            # 1 plausible_useful / 5 = 0.2
            assert abs(row["plausible_usefulness_score"] - 0.2) < 1e-6
            # 1 harmful / 5 = 0.2
            assert abs(row["harmful_score"] - 0.2) < 1e-6
            # FP: irrelevant_not_applicable(1) + harmful_distracted(1) = 2/5 = 0.4
            assert abs(row["false_positive_score"] - 0.4) < 1e-6
        finally:
            db.close()

    def test_scores_update_on_new_events(self, tmp_path):
        """Scores reflect current event state, not cached values."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            sess = str(uuid.uuid4())
            _insert_fire_event(db, rid, session_id=sess, label="observed_useful",
                               reason_code="useful_prevented_error")

            update_derived_scores(db, rid)
            row = db.fetchone(
                "SELECT observed_usefulness_score FROM rules WHERE id = ?", (rid,)
            )
            assert abs(row["observed_usefulness_score"] - 1.0) < 1e-6

            # Add more events
            _insert_fire_event(db, rid, session_id=sess, label="irrelevant",
                               reason_code="irrelevant_not_applicable")

            update_derived_scores(db, rid)
            row = db.fetchone(
                "SELECT observed_usefulness_score FROM rules WHERE id = ?", (rid,)
            )
            # Now 1/2 = 0.5
            assert abs(row["observed_usefulness_score"] - 0.5) < 1e-6
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 13. plausible_useful cannot promote to trusted
# ---------------------------------------------------------------------------


class TestPlausibleUsefulCannotPromote:
    def test_plausible_only_does_not_reach_trusted(self, tmp_path):
        """Only observed_useful counts toward trusted promotion."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # 5 plausible_useful, 0 observed_useful
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_followed_preference")
            _insert_fire_event(db, rid, session_id=sess2, label="plausible_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess2, label="plausible_useful",
                               reason_code="useful_improved_quality")

            result = evaluate_transitions(db, rid)
            assert result.new_status is None  # No promotion
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 14. observed_useful + attribution down-weight (would_have_happened=yes)
# ---------------------------------------------------------------------------


class TestAttributionDownWeight:
    def test_observed_useful_counted_in_distinct_sessions(self, tmp_path):
        """distinct_observed_useful_sessions counts only sessions with observed_useful."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())
            sess3 = str(uuid.uuid4())

            # observed_useful in sess1 and sess2
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess2, label="observed_useful",
                               reason_code="useful_improved_quality")
            # Only plausible in sess3 — does not count toward distinct sessions
            _insert_fire_event(db, rid, session_id=sess3, label="plausible_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess3, label="plausible_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess3, label="plausible_useful",
                               reason_code="useful_followed_preference")

            # Only 2 observed_useful < 3 required, but let's verify session counting
            result = evaluate_transitions(db, rid)
            # Not enough observed_useful (need 3), so no promotion
            assert result.new_status is None
        finally:
            db.close()

    def test_would_have_happened_marks_plausible_not_observed(self, tmp_path):
        """When would_have_happened=yes, the label should be plausible_useful,
        not observed_useful. This test verifies the transition engine treats them
        differently — plausible does not count toward observed_useful_count_min."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # Simulate: 3 events where agent would have done it anyway
            # (labeled plausible_useful instead of observed_useful)
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_improved_quality")
            _insert_fire_event(db, rid, session_id=sess2, label="plausible_useful",
                               reason_code="useful_followed_preference")
            _insert_fire_event(db, rid, session_id=sess2, label="plausible_useful",
                               reason_code="useful_prevented_error")
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_improved_quality")

            result = evaluate_transitions(db, rid)
            # Even though evaluated >= 5 and sessions >= 2,
            # observed_useful=0 < 3 required — no promotion
            assert result.new_status is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_archived_rule_does_not_transition(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="archived")
            result = evaluate_transitions(db, rid)
            assert result.new_status is None
            assert "terminal" in result.reason
        finally:
            db.close()

    def test_nonexistent_rule(self, tmp_path):
        db = _fresh_db(tmp_path)
        try:
            result = evaluate_transitions(db, "nonexistent-id")
            assert result.applied is False
            assert "not found" in result.reason
        finally:
            db.close()

    def test_fingerprint_dedup_in_shadow_evidence(self, tmp_path):
        """Duplicate fingerprints should not be double-counted."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate")
            _insert_synthetic_eval(db, rid, 1, passed=True)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())
            fp = "same_fingerprint"

            # Same fingerprint repeated — should count as 1
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high",
                                 fingerprint=fp)
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high",
                                 fingerprint=fp)
            # Different fingerprints
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_low")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_low")

            result = evaluate_transitions(db, rid)
            # Only 2 would_help_high (1 deduped) + 2 would_help_low = 4 strong
            # But would_help_high = 2 (>= 2 counterfactual needed)
            # evaluated = 4 < 5 required — should NOT promote
            assert result.new_status is None
        finally:
            db.close()

    def test_events_outside_window_not_counted(self, tmp_path):
        """Non-harmful events older than RECENT_EVENT_WINDOW are excluded.

        Harmful events do NOT decay by time (spec section 3.4):
        'Harmful events do not decay below suppression thresholds merely
        because time passes.'
        """
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # Old harmful events still count for suppression (lifetime)
            _insert_fire_event(db, rid, session_id=sess1, label="harmful",
                               reason_code="harmful_distracted",
                               days_ago=RECENT_TIME_WINDOW_DAYS + 5)

            # Recent benign events
            _insert_fire_event(db, rid, session_id=sess2, label="plausible_useful",
                               reason_code="useful_prevented_error")

            result = evaluate_transitions(db, rid)
            # Harmful DOES cause suppression regardless of age (spec 3.4)
            assert result.new_status == "suppressed"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 15. CAS concurrency
# ---------------------------------------------------------------------------


class TestCASConcurrency:
    def test_stale_job_does_not_apply(self, tmp_path):
        """CAS prevents stale transitions when rule was concurrently modified."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="candidate", rule_version=1)
            _insert_synthetic_eval(db, rid, 1, passed=True)

            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # Provide sufficient evidence for promotion
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_high")
            _insert_shadow_event(db, rid, session_id=sess1, label="would_help_low")
            _insert_shadow_event(db, rid, session_id=sess2, label="would_help_low")

            # Simulate concurrent modification: bump rule_version behind the scenes
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE rules SET rule_version = rule_version + 1 WHERE id = ?",
                    (rid,),
                )

            # evaluate_transitions reads version=2 but evidence was for version=1;
            # the CAS check should prevent applying the transition.
            result = evaluate_transitions(db, rid)
            assert result.applied is False

            # Status must remain unchanged
            row = db.fetchone("SELECT status, rule_version FROM rules WHERE id = ?", (rid,))
            assert row["status"] == "candidate"
            assert row["rule_version"] == 2
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 16. Fire event window is count-based (last 10), not time-based
# ---------------------------------------------------------------------------


class TestFireEventDualWindow:
    """Fire evidence uses BOTH count window (last 10) AND time window (30 days).

    Spec 3.4: metrics computed over events within both windows simultaneously.
    Events older than 30 days are excluded even if among last 10.
    """

    def test_recent_irrelevant_events_trigger_suppression(self, tmp_path):
        """Recent irrelevant events within both windows trigger suppression."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")
            sess1 = str(uuid.uuid4())

            # 3 irrelevant within 30 days → suppression
            _insert_fire_event(db, rid, session_id=sess1, label="irrelevant",
                               reason_code="irrelevant_not_applicable", days_ago=10)
            _insert_fire_event(db, rid, session_id=sess1, label="irrelevant",
                               reason_code="irrelevant_not_applicable", days_ago=8)
            _insert_fire_event(db, rid, session_id=sess1, label="irrelevant",
                               reason_code="irrelevant_not_applicable", days_ago=5)
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_prevented_error", days_ago=2)
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=1)

            result = evaluate_transitions(db, rid)
            assert result.new_status == "suppressed"
        finally:
            db.close()

    def test_old_events_excluded_from_dual_window(self, tmp_path):
        """Events older than 30 days are excluded even if within last 10."""
        db = _fresh_db(tmp_path)
        try:
            rid = _insert_rule(db, status="active")
            sess1 = str(uuid.uuid4())
            sess2 = str(uuid.uuid4())

            # 3 observed_useful from 45 days ago → excluded by time window
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_prevented_error", days_ago=45)
            _insert_fire_event(db, rid, session_id=sess1, label="observed_useful",
                               reason_code="useful_improved_quality", days_ago=42)
            _insert_fire_event(db, rid, session_id=sess2, label="observed_useful",
                               reason_code="useful_followed_preference", days_ago=40)
            # 2 recent → total evaluated = 2, below threshold for trusted
            _insert_fire_event(db, rid, session_id=sess1, label="plausible_useful",
                               reason_code="useful_improved_quality", days_ago=2)
            _insert_fire_event(db, rid, session_id=sess2, label="plausible_useful",
                               reason_code="useful_prevented_error", days_ago=1)

            result = evaluate_transitions(db, rid)
            # Old events excluded → insufficient for promotion
            assert result.new_status is None
        finally:
            db.close()
