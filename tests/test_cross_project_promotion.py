"""Tests for cross-project promotion: trusted rules with observed_useful
fire events across 3+ distinct projects get promoted to global scope.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nokori.db import Db, open_db
from nokori.events.fire import count_distinct_useful_projects
from nokori.lifecycle.transitions import evaluate_transitions
from nokori.policy import CROSS_PROJECT_PROMOTION_THRESHOLD, RUNTIME_POLICY_VERSION


@pytest.fixture
def db(tmp_path: Path) -> Db:
    database = open_db(tmp_path / "rules.db")
    yield database
    database.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso(delta_days: float = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=delta_days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_rule(
    db: Db,
    *,
    rule_id: str | None = None,
    status: str = "trusted",
    rule_version: int = 1,
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
            "project_scope, trusted_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                short,
                rule_version,
                RUNTIME_POLICY_VERSION,
                status,
                "reminder",
                "test trigger",
                "[]",
                "test action",
                project_scope,
                now,
                now,
                now,
            ),
        )
    return rid


def _insert_fire_event(
    db: Db,
    rule_id: str,
    *,
    project_id: str | None = None,
    label: str | None = None,
    session_id: str | None = None,
    days_ago: float = 0,
) -> str:
    eid = str(uuid.uuid4())
    sid = session_id or str(uuid.uuid4())
    ts = _utcnow_iso(-days_ago)
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_fire_events "
            "(id, rule_id, session_id, posthoc_label, project_id, level, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (eid, rule_id, sid, label, project_id, "warm", ts),
        )
    return eid


# ---------------------------------------------------------------------------
# Tests for count_distinct_useful_projects
# ---------------------------------------------------------------------------


class TestCountDistinctUsefulProjects:
    def test_returns_zero_with_no_events(self, db):
        rid = _insert_rule(db)
        assert count_distinct_useful_projects(db, rid) == 0

    def test_counts_distinct_projects(self, db):
        rid = _insert_rule(db)
        _insert_fire_event(db, rid, project_id="proj-a", label="observed_useful")
        _insert_fire_event(db, rid, project_id="proj-b", label="observed_useful")
        _insert_fire_event(db, rid, project_id="proj-c", label="observed_useful")
        assert count_distinct_useful_projects(db, rid) == 3

    def test_excludes_null_project_id(self, db):
        rid = _insert_rule(db)
        _insert_fire_event(db, rid, project_id="proj-a", label="observed_useful")
        _insert_fire_event(db, rid, project_id=None, label="observed_useful")
        _insert_fire_event(db, rid, project_id="proj-b", label="observed_useful")
        assert count_distinct_useful_projects(db, rid) == 2

    def test_excludes_non_useful_labels(self, db):
        rid = _insert_rule(db)
        _insert_fire_event(db, rid, project_id="proj-a", label="observed_useful")
        _insert_fire_event(db, rid, project_id="proj-b", label="irrelevant")
        _insert_fire_event(db, rid, project_id="proj-c", label="harmful")
        assert count_distinct_useful_projects(db, rid) == 1

    def test_deduplicates_same_project(self, db):
        rid = _insert_rule(db)
        _insert_fire_event(db, rid, project_id="proj-a", label="observed_useful")
        _insert_fire_event(db, rid, project_id="proj-a", label="observed_useful")
        _insert_fire_event(db, rid, project_id="proj-a", label="observed_useful")
        assert count_distinct_useful_projects(db, rid) == 1


# ---------------------------------------------------------------------------
# Tests for cross-project promotion in _evaluate_trusted
# ---------------------------------------------------------------------------


class TestCrossProjectPromotion:
    def test_no_promotion_below_threshold(self, db):
        """Below threshold projects with observed_useful -> no promotion."""
        rid = _insert_rule(db, project_scope="project")
        for i in range(CROSS_PROJECT_PROMOTION_THRESHOLD - 1):
            _insert_fire_event(
                db, rid, project_id=f"proj-{i}", label="observed_useful"
            )

        result = evaluate_transitions(db, rid)
        assert result.new_status is None
        assert result.applied is False
        assert result.reason == "no transition triggered"

        row = db.fetchone("SELECT project_scope FROM rules WHERE id = ?", (rid,))
        assert row["project_scope"] == "project"

    def test_promotion_at_threshold(self, db):
        """Exactly threshold distinct projects with observed_useful -> promote to global."""
        rid = _insert_rule(db, project_scope="project")
        for i in range(CROSS_PROJECT_PROMOTION_THRESHOLD):
            _insert_fire_event(
                db, rid, project_id=f"proj-{i}", label="observed_useful"
            )

        result = evaluate_transitions(db, rid)
        assert result.new_status is None
        assert result.applied is True
        assert result.reason == "cross_project_promotion"

        row = db.fetchone("SELECT project_scope FROM rules WHERE id = ?", (rid,))
        assert row["project_scope"] == "global"

    def test_no_promotion_without_observed_useful(self, db):
        """Threshold projects but without observed_useful label -> no promotion."""
        rid = _insert_rule(db, project_scope="project")
        labels = ["irrelevant", "plausible_useful", "unclear"]
        for i in range(CROSS_PROJECT_PROMOTION_THRESHOLD):
            _insert_fire_event(
                db, rid, project_id=f"proj-{i}", label=labels[i % len(labels)]
            )

        result = evaluate_transitions(db, rid)
        assert result.applied is False

        row = db.fetchone("SELECT project_scope FROM rules WHERE id = ?", (rid,))
        assert row["project_scope"] == "project"

    def test_already_global_no_op(self, db):
        """Rule already global -> no cross-project promotion attempt."""
        rid = _insert_rule(db, project_scope="global")
        for i in range(CROSS_PROJECT_PROMOTION_THRESHOLD):
            _insert_fire_event(
                db, rid, project_id=f"proj-{i}", label="observed_useful"
            )

        result = evaluate_transitions(db, rid)
        assert result.applied is False
        assert result.reason == "no transition triggered"

        row = db.fetchone(
            "SELECT project_scope, rule_version FROM rules WHERE id = ?", (rid,)
        )
        assert row["project_scope"] == "global"
        assert row["rule_version"] == 1

    def test_null_project_id_excluded(self, db):
        """Events with project_id=None don't count toward threshold."""
        rid = _insert_rule(db, project_scope="project")
        for i in range(CROSS_PROJECT_PROMOTION_THRESHOLD - 1):
            _insert_fire_event(
                db, rid, project_id=f"proj-{i}", label="observed_useful"
            )
        for _ in range(CROSS_PROJECT_PROMOTION_THRESHOLD):
            _insert_fire_event(
                db, rid, project_id=None, label="observed_useful"
            )

        result = evaluate_transitions(db, rid)
        assert result.applied is False
        assert result.reason == "no transition triggered"

        row = db.fetchone("SELECT project_scope FROM rules WHERE id = ?", (rid,))
        assert row["project_scope"] == "project"

    def test_promotion_writes_observability_event(self, db):
        """Promotion writes an observability event with correct details."""
        rid = _insert_rule(db, project_scope="project")
        for i in range(CROSS_PROJECT_PROMOTION_THRESHOLD):
            _insert_fire_event(db, rid, project_id=f"proj-{i}", label="observed_useful")

        evaluate_transitions(db, rid)

        events = db.fetchall(
            "SELECT * FROM hook_events WHERE source = 'lifecycle_transition' "
            "AND outcome = 'cross_project_promotion'"
        )
        assert len(events) == 1

    def test_promotion_above_threshold(self, db):
        """Above threshold distinct projects -> still promotes."""
        rid = _insert_rule(db, project_scope="project")
        for i in range(CROSS_PROJECT_PROMOTION_THRESHOLD + 2):
            _insert_fire_event(
                db, rid, project_id=f"proj-{i}", label="observed_useful"
            )

        result = evaluate_transitions(db, rid)
        assert result.applied is True
        assert result.reason == "cross_project_promotion"

        row = db.fetchone("SELECT project_scope FROM rules WHERE id = ?", (rid,))
        assert row["project_scope"] == "global"

