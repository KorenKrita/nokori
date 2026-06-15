import pytest

from nokori.db import SCHEMA_VERSION, open_db
from nokori.errors import DbError


def test_open_db_creates_schema(tmp_path):
    path = tmp_path / "rules.db"
    db = open_db(path)
    try:
        assert path.exists()
        assert db.schema_version() == SCHEMA_VERSION
        names = {
            row["name"]
            for row in db.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for required in (
            "rules",
            "rule_embeddings",
            "rule_fire_events",
            "rule_shadow_events",
            "extract_state",
            "maintenance_meta",
        ):
            assert required in names
    finally:
        db.close()


def test_open_db_idempotent(tmp_path):
    path = tmp_path / "rules.db"
    open_db(path).close()
    db = open_db(path)
    try:
        assert db.schema_version() == SCHEMA_VERSION
    finally:
        db.close()


def test_check_constraints(tmp_path):
    import sqlite3

    db = open_db(tmp_path / "rules.db")
    try:
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, created_at, updated_at) "
                "VALUES ('a', 'aaaaaa', 1, 1, 'v1', 'v1', 't', 'a', "
                "'active', 'reminder', 'project', "
                "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
        try:
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                    "created_by_pipeline_version, runtime_policy_version, "
                    "trigger_canonical, action_instruction, "
                    "status, severity, project_scope, created_at, updated_at) "
                    "VALUES ('b', 'bbbbbb', 1, 1, 'v1', 'v1', 't', 'a', "
                    "'NOT_VALID_STATUS', 'reminder', 'project', "
                    "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
                )
            assert False, "expected CHECK violation"
        except sqlite3.IntegrityError:
            pass
    finally:
        db.close()


def test_wal_mode(tmp_path):
    db = open_db(tmp_path / "rules.db")
    try:
        row = db.fetchone("PRAGMA journal_mode")
        assert row[0].lower() == "wal"
    finally:
        db.close()


def test_nested_transaction_rejected(tmp_path):
    db = open_db(tmp_path / "rules.db")
    try:
        with db.transaction():
            with pytest.raises(DbError, match="nested"):
                with db.transaction():
                    pass
    finally:
        db.close()
