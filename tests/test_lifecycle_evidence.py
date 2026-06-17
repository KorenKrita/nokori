"""Tests for nokori.lifecycle.evidence — the deep evidence aggregation module.

Tests fire label counting, strong-attribution weighting (spec 10.2),
lifetime-harmful non-decay (spec 3.4), false_positive_rate,
shadow fingerprint/task dedup, candidate extras, count_harmful_since,
and rule_fire_stats.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nokori.db import Db, open_db
from nokori.lifecycle.evidence import (
    FireStats,
    count_harmful_since,
    gather_candidate_extras,
    gather_fire_evidence,
    gather_shadow_evidence,
    rule_fire_stats,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso(delta_days: float = 0) -> str:
    dt = datetime.now(UTC) + timedelta(days=delta_days)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture()
def db(tmp_path):
    d = open_db(tmp_path / "rules.db")
    yield d
    d.close()


def _insert_rule(
    db: Db,
    *,
    rule_id: str | None = None,
    status: str = "active",
    rule_version: int = 1,
) -> str:
    rid = rule_id or str(uuid.uuid4())
    short = rid[:8]
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, rule_version, runtime_policy_version, status, severity, "
            "trigger_canonical, concepts, action_instruction, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid, short, rule_version, "1.0.0", status, "reminder",
                "test trigger", "[]", "test action", now, now,
            ),
        )
    return rid


def _insert_fire_event(
    db: Db,
    rule_id: str,
    *,
    session_id: str | None = None,
    label: str | None = None,
    reason_code: str | None = None,
    posthoc_score: float | None = None,
    level: str = "warm",
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
            (eid, rule_id, sid, label, reason_code, posthoc_score, level, ts),
        )
    return eid


def _insert_shadow_event(
    db: Db,
    rule_id: str,
    *,
    rule_version: int = 1,
    session_id: str | None = None,
    label: str = "would_help_high",
    fingerprint: str | None = None,
    prompt_hash: str = "hash",
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
                eid, rule_id, sid, rule_version, label, fp, status_at_match,
                shadow_type, prompt_hash, "warm_candidate",
                json.dumps(decision_features or {}), ts,
            ),
        )
    return eid


def _insert_synthetic_eval(db: Db, rule_id: str, rule_version: int, passed: bool) -> None:
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_synthetic_evals (rule_id, rule_version, passed, created_at) "
            "VALUES (?,?,?,?)",
            (rule_id, rule_version, 1 if passed else 0, now),
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
# gather_fire_evidence tests
# ---------------------------------------------------------------------------


class TestGatherFireEvidence:
    def test_label_counting(self, db):
        """Counts by label are accurate for recent events."""
        rid = _insert_rule(db, status="active")
        _insert_fire_event(db, rid, label="observed_useful")
        _insert_fire_event(db, rid, label="observed_useful")
        _insert_fire_event(db, rid, label="irrelevant")
        _insert_fire_event(db, rid, label="harmful")

        fire = gather_fire_evidence(db, rid, window_days=30)
        assert fire["observed_useful"] == 2
        assert fire["irrelevant"] == 1
        assert fire["harmful"] == 1
        assert fire["total_evaluated"] == 4

    def test_strong_attribution_weighting(self, db):
        """Spec 10.2: only strong-attribution events (score > 0.5 or NULL) count as strong."""
        rid = _insert_rule(db, status="active")
        sess = str(uuid.uuid4())

        # NULL score = legacy = strong
        _insert_fire_event(db, rid, session_id=sess, label="observed_useful", posthoc_score=None)
        # High score = strong
        _insert_fire_event(db, rid, session_id=sess, label="observed_useful", posthoc_score=0.8)
        # Low score = weak, excluded from strong count
        _insert_fire_event(db, rid, session_id=sess, label="observed_useful", posthoc_score=0.3)
        # Exactly 0.5 = weak (threshold is > 0.5, not >=)
        _insert_fire_event(db, rid, session_id=sess, label="observed_useful", posthoc_score=0.5)

        fire = gather_fire_evidence(db, rid, window_days=30)
        assert fire["observed_useful"] == 4
        assert fire["observed_useful_strong"] == 2  # only NULL and 0.8

    def test_distinct_strong_useful_sessions(self, db):
        """Counts distinct sessions with strong useful events."""
        rid = _insert_rule(db, status="active")
        sess1 = str(uuid.uuid4())
        sess2 = str(uuid.uuid4())
        sess3 = str(uuid.uuid4())

        _insert_fire_event(db, rid, session_id=sess1, label="observed_useful", posthoc_score=None)
        _insert_fire_event(db, rid, session_id=sess2, label="observed_useful", posthoc_score=0.9)
        # sess3 has only weak attribution
        _insert_fire_event(db, rid, session_id=sess3, label="observed_useful", posthoc_score=0.2)

        fire = gather_fire_evidence(db, rid, window_days=30)
        assert fire["distinct_strong_useful_sessions"] == 2

    def test_lifetime_harmful_not_decayed(self, db):
        """Spec 3.4: lifetime harmful counts old events that are outside the time window."""
        rid = _insert_rule(db, status="active")
        # Harmful event 60 days ago (outside 30-day window)
        _insert_fire_event(db, rid, label="harmful", days_ago=60)
        # Harmful event within window
        _insert_fire_event(db, rid, label="harmful", days_ago=5)

        fire = gather_fire_evidence(db, rid, window_days=30)
        # Only the recent one is in the windowed count
        assert fire["harmful"] == 1
        # But lifetime counts both
        assert fire["lifetime_harmful"] == 2

    def test_irrelevant_in_last_5(self, db):
        """Counts irrelevant events in the most recent 5 evaluated events."""
        rid = _insert_rule(db, status="active")
        # Insert 7 events, most recent first
        _insert_fire_event(db, rid, label="irrelevant", days_ago=0)
        _insert_fire_event(db, rid, label="irrelevant", days_ago=1)
        _insert_fire_event(db, rid, label="observed_useful", days_ago=2)
        _insert_fire_event(db, rid, label="irrelevant", days_ago=3)
        _insert_fire_event(db, rid, label="observed_useful", days_ago=4)
        # These are outside the last 5
        _insert_fire_event(db, rid, label="irrelevant", days_ago=5)
        _insert_fire_event(db, rid, label="irrelevant", days_ago=6)

        fire = gather_fire_evidence(db, rid, window_days=30)
        # In last 5: irrelevant, irrelevant, observed_useful, irrelevant, observed_useful
        assert fire["irrelevant_in_last_5"] == 3

    def test_false_positive_rate_from_reason_codes(self, db):
        """FP rate is computed from FP reason codes, not from the 'irrelevant' label."""
        rid = _insert_rule(db, status="active")
        # 2 FP reason codes out of 4 total
        _insert_fire_event(db, rid, label="irrelevant", reason_code="irrelevant_not_applicable")
        _insert_fire_event(db, rid, label="harmful", reason_code="harmful_wrong_scope")
        _insert_fire_event(db, rid, label="observed_useful")
        _insert_fire_event(db, rid, label="observed_useful")

        fire = gather_fire_evidence(db, rid, window_days=30)
        # 2 FP events / 4 total = 0.5
        assert fire["false_positive_rate"] == 0.5

    def test_empty_events(self, db):
        """Returns zero counts for a rule with no events."""
        rid = _insert_rule(db, status="active")
        fire = gather_fire_evidence(db, rid, window_days=30)
        assert fire["total_evaluated"] == 0
        assert fire["lifetime_harmful"] == 0
        assert fire["observed_useful_strong"] == 0


# ---------------------------------------------------------------------------
# gather_shadow_evidence tests
# ---------------------------------------------------------------------------


class TestGatherShadowEvidence:
    def test_fingerprint_dedup(self, db):
        """Events with duplicate context_fingerprint are deduplicated."""
        rid = _insert_rule(db, status="candidate")
        shared_fp = "fingerprint_abc"
        sess = str(uuid.uuid4())

        # Two events with same fingerprint — only first counted
        _insert_shadow_event(db, rid, session_id=sess, fingerprint=shared_fp, days_ago=0)
        _insert_shadow_event(db, rid, session_id=sess, fingerprint=shared_fp, days_ago=1)
        # One with unique fingerprint
        _insert_shadow_event(db, rid, session_id=sess, fingerprint="unique_fp", days_ago=2)

        shadow = gather_shadow_evidence(db, rid, 1, window_days=30)
        assert shadow["would_help_high"] == 2  # deduped: shared_fp + unique_fp

    def test_task_dedup(self, db):
        """Events in same session with same prompt_hash prefix are task-deduped.

        Task dedup groups by: same prompt_hash prefix (first 8 chars) OR
        within 3 consecutive positions. To isolate prefix grouping, place
        target events > 3 positions apart within the same session (4 filler
        events with different prefixes in between).
        """
        rid = _insert_rule(db, status="candidate")
        sess1 = str(uuid.uuid4())

        # Target event A (position 0): prefix "abcdefgh"
        _insert_shadow_event(db, rid, session_id=sess1, prompt_hash="abcdefgh_1", days_ago=5)
        # 4 filler events (positions 1-4): different prefix, breaks consecutive rule
        for i in range(4):
            _insert_shadow_event(
                db, rid, session_id=sess1,
                prompt_hash=f"filler{i:02d}_x", days_ago=4 - i * 0.5,
            )
        # Target event B (position 5): same prefix "abcdefgh", position diff = 5 > 3
        _insert_shadow_event(db, rid, session_id=sess1, prompt_hash="abcdefgh_2", days_ago=0)

        shadow = gather_shadow_evidence(db, rid, 1, window_days=30)
        # All 6 events have unique fingerprints → all counted in raw
        assert shadow["would_help_high"] == 6
        # Task dedup: position 0 starts a group with prefix "abcdefgh".
        # Positions 1-3 are within consecutive range (j-i <= 3) so absorbed.
        # Position 4 (j-i=4, different prefix) starts a new group.
        # Position 5 (j-i=5, but same prefix "abcdefgh") is absorbed into group 0.
        # Result: 2 task groups.
        assert shadow["task_deduped_count"] == 2

    def test_distinct_sessions(self, db):
        """Counts unique sessions correctly."""
        rid = _insert_rule(db, status="candidate")
        sess1 = str(uuid.uuid4())
        sess2 = str(uuid.uuid4())

        _insert_shadow_event(db, rid, session_id=sess1)
        _insert_shadow_event(db, rid, session_id=sess1)
        _insert_shadow_event(db, rid, session_id=sess2)

        shadow = gather_shadow_evidence(db, rid, 1, window_days=30)
        assert shadow["distinct_sessions"] == 2

    def test_shadow_fp_rate_not_computed_here(self, db):
        """Shadow FP rate is computed in transitions._gather_candidate_evidence, not here.

        gather_shadow_evidence returns raw label counts; the FP rate is
        computed by the caller using (irrelevant + near_miss) / task_deduped_count.
        """
        rid = _insert_rule(db, status="candidate")
        _insert_shadow_event(db, rid, label="would_help_high")
        _insert_shadow_event(db, rid, label="irrelevant")
        _insert_shadow_event(db, rid, label="near_miss")

        shadow = gather_shadow_evidence(db, rid, 1, window_days=30)
        assert shadow["irrelevant"] == 1
        assert shadow["near_miss"] == 1
        assert shadow["would_help_high"] == 1

    def test_since_iso_filter(self, db):
        """Only counts events after since_iso."""
        rid = _insert_rule(db, status="suppressed")
        sess = str(uuid.uuid4())

        # Event before cutoff
        _insert_shadow_event(
            db, rid, session_id=sess, days_ago=10,
            shadow_type="suppression_recovery",
        )
        # Event after cutoff
        _insert_shadow_event(
            db, rid, session_id=sess, days_ago=1,
            shadow_type="suppression_recovery",
        )

        cutoff = _utcnow_iso(-5)  # 5 days ago
        shadow = gather_shadow_evidence(
            db, rid, 1, since_iso=cutoff, shadow_type="suppression_recovery"
        )
        assert shadow["would_help_high"] == 1


# ---------------------------------------------------------------------------
# gather_candidate_extras tests
# ---------------------------------------------------------------------------


class TestGatherCandidateExtras:
    def test_synthetic_eval_passed(self, db):
        rid = _insert_rule(db, status="candidate")
        _insert_synthetic_eval(db, rid, 1, passed=True)

        extras = gather_candidate_extras(db, rid, 1)
        assert extras["synthetic_eval_passed"] is True

    def test_synthetic_eval_not_passed(self, db):
        rid = _insert_rule(db, status="candidate")
        _insert_synthetic_eval(db, rid, 1, passed=False)

        extras = gather_candidate_extras(db, rid, 1)
        assert extras["synthetic_eval_passed"] is False

    def test_admission_quality(self, db):
        rid = _insert_rule(db, status="candidate")
        _insert_review(db, rid, 0.85)

        extras = gather_candidate_extras(db, rid, 1)
        assert extras["admission_quality"] == 0.85

    def test_miss_evidence_from_observed_agent_miss(self, db):
        rid = _insert_rule(db, status="candidate")
        _insert_shadow_event(
            db, rid,
            label="would_help_high",
            decision_features={"observed_agent_miss": True},
        )

        extras = gather_candidate_extras(db, rid, 1)
        assert extras["has_miss_evidence"] is True

    def test_miss_evidence_from_user_correction(self, db):
        rid = _insert_rule(db, status="candidate")
        _insert_shadow_event(
            db, rid,
            label="would_help_high",
            decision_features={"user_correction": True},
        )

        extras = gather_candidate_extras(db, rid, 1)
        assert extras["has_miss_evidence"] is True

    def test_no_miss_evidence(self, db):
        rid = _insert_rule(db, status="candidate")
        _insert_shadow_event(
            db, rid,
            label="would_help_high",
            decision_features={"some_other_key": True},
        )

        extras = gather_candidate_extras(db, rid, 1)
        assert extras["has_miss_evidence"] is False


# ---------------------------------------------------------------------------
# count_harmful_since tests
# ---------------------------------------------------------------------------


class TestCountHarmfulSince:
    def test_counts_only_after_timestamp(self, db):
        rid = _insert_rule(db, status="suppressed")
        _insert_fire_event(db, rid, label="harmful", days_ago=10)
        _insert_fire_event(db, rid, label="harmful", days_ago=2)
        _insert_fire_event(db, rid, label="harmful", days_ago=1)

        cutoff = _utcnow_iso(-5)  # 5 days ago
        count = count_harmful_since(db, rid, cutoff)
        assert count == 2

    def test_excludes_non_harmful(self, db):
        rid = _insert_rule(db, status="suppressed")
        _insert_fire_event(db, rid, label="harmful", days_ago=1)
        _insert_fire_event(db, rid, label="irrelevant", days_ago=1)

        cutoff = _utcnow_iso(-5)
        count = count_harmful_since(db, rid, cutoff)
        assert count == 1


# ---------------------------------------------------------------------------
# rule_fire_stats tests
# ---------------------------------------------------------------------------


class TestRuleFireStats:
    def test_empty_stats(self, db):
        rid = _insert_rule(db, status="active")
        stats = rule_fire_stats(db, rid)
        assert isinstance(stats, FireStats)
        assert stats.total == 0
        assert stats.last_at is None
        assert stats.by_level == {}
        assert stats.by_label == {}
        assert stats.shadow_count == 0

    def test_by_level_breakdown(self, db):
        rid = _insert_rule(db, status="active")
        _insert_fire_event(db, rid, label="observed_useful", level="hot")
        _insert_fire_event(db, rid, label="observed_useful", level="hot")
        _insert_fire_event(db, rid, label="irrelevant", level="warm")

        stats = rule_fire_stats(db, rid)
        assert stats.total == 3
        assert stats.by_level == {"hot": 2, "warm": 1}

    def test_by_label_breakdown(self, db):
        rid = _insert_rule(db, status="active")
        _insert_fire_event(db, rid, label="observed_useful")
        _insert_fire_event(db, rid, label="observed_useful")
        _insert_fire_event(db, rid, label="harmful")
        _insert_fire_event(db, rid, label=None)  # no label

        stats = rule_fire_stats(db, rid)
        assert stats.total == 4
        assert stats.by_label == {"observed_useful": 2, "harmful": 1}

    def test_shadow_count(self, db):
        rid = _insert_rule(db, status="active")
        _insert_shadow_event(db, rid, label="would_help_high")
        _insert_shadow_event(db, rid, label="irrelevant")

        stats = rule_fire_stats(db, rid)
        assert stats.shadow_count == 2

    def test_last_at(self, db):
        rid = _insert_rule(db, status="active")
        _insert_fire_event(db, rid, label="observed_useful", days_ago=5)
        _insert_fire_event(db, rid, label="observed_useful", days_ago=0)

        stats = rule_fire_stats(db, rid)
        assert stats.last_at is not None
        assert stats.last_at >= _utcnow_iso(-1)
        assert stats.total == 2
