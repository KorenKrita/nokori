"""Tests for the shared restore_orphaned_archived utility."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nokori.db import Db, open_db
from nokori.lifecycle.maintenance import restore_orphaned_archived


@pytest.fixture()
def db(tmp_path: Path) -> Db:
    return open_db(tmp_path / "test.db")


def _utcnow_iso(delta_days: int = 0) -> str:
    dt = datetime.now(UTC) + timedelta(days=delta_days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_rule(db: Db, id_: str, status: str, replacement_id: str | None = None):
    short = hashlib.sha256(id_.encode()).hexdigest()[:6]
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "source_origin, status, severity, "
            "project_scope, created_at, updated_at, replacement_id) "
            "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
            (id_, short, f"trigger {id_}", f"action {id_}",
             "transcript_extraction", status, "reminder",
             "global", now, now, replacement_id),
        )


class TestRestoreOrphanedArchived:
    def test_restores_when_target_missing(self, db: Db):

        _insert_rule(db, "archived-1", "archived", replacement_id="deleted-target")
        restored = restore_orphaned_archived(db)
        assert restored == 1

        row = db.fetchone("SELECT status, replacement_id FROM rules WHERE id = ?", ("archived-1",))
        assert row["status"] == "candidate"
        assert row["replacement_id"] is None

    def test_restores_when_target_suppressed(self, db: Db):

        _insert_rule(db, "target-1", "suppressed")
        _insert_rule(db, "archived-1", "archived", replacement_id="target-1")

        restored = restore_orphaned_archived(db, include_inactive_targets=True)
        assert restored == 1

        row = db.fetchone("SELECT status FROM rules WHERE id = ?", ("archived-1",))
        assert row["status"] == "candidate"

    def test_does_not_restore_when_target_active(self, db: Db):

        _insert_rule(db, "target-1", "active")
        _insert_rule(db, "archived-1", "archived", replacement_id="target-1")

        restored = restore_orphaned_archived(db)
        assert restored == 0

        row = db.fetchone("SELECT status FROM rules WHERE id = ?", ("archived-1",))
        assert row["status"] == "archived"

    def test_does_not_restore_when_target_active_even_with_include_inactive(self, db: Db):

        _insert_rule(db, "target-1", "active")
        _insert_rule(db, "archived-1", "archived", replacement_id="target-1")

        restored = restore_orphaned_archived(db, include_inactive_targets=True)
        assert restored == 0

    def test_multiple_orphans_restored(self, db: Db):

        _insert_rule(db, "archived-1", "archived", replacement_id="gone-1")
        _insert_rule(db, "archived-2", "archived", replacement_id="gone-2")
        _insert_rule(db, "archived-3", "archived", replacement_id="active-target")
        _insert_rule(db, "active-target", "active")

        restored = restore_orphaned_archived(db)
        assert restored == 2

    def test_no_archived_rules_returns_zero(self, db: Db):
        assert restore_orphaned_archived(db) == 0

    def test_archived_without_replacement_id_not_restored(self, db: Db):
        _insert_rule(db, "archived-no-ref", "archived", replacement_id=None)
        assert restore_orphaned_archived(db) == 0

    def test_does_not_restore_when_target_suppressed_without_include_inactive(self, db: Db):
        _insert_rule(db, "target-1s", "suppressed")
        _insert_rule(db, "archived-1s", "archived", replacement_id="target-1s")

        restored = restore_orphaned_archived(db)
        assert restored == 0

        row = db.fetchone("SELECT status FROM rules WHERE id = ?", ("archived-1s",))
        assert row["status"] == "archived"
