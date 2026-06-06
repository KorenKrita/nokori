"""Database schema version and migration tests."""
import sqlite3
from pathlib import Path

import pytest

from nokori.db import SCHEMA_VERSION, open_db
from nokori.errors import DbError


def _create_v1_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE rules (id TEXT PRIMARY KEY);
        PRAGMA user_version = 1;
        """
    )
    conn.close()


def test_fresh_db_is_current_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    db = open_db(tmp_path / "rules.db")
    try:
        assert db.schema_version() == SCHEMA_VERSION
        cols = {r[1] for r in db.fetchall("PRAGMA table_info(rules)")}
        assert "trigger_canonical" in cols
        assert "action_instruction" in cols
        assert "severity" in cols
        assert "evidence_support_score" in cols
        assert "source_origin" in cols
        assert "replacement_id" in cols
        es_cols = {r[1] for r in db.fetchall("PRAGMA table_info(extract_state)")}
        assert "last_byte_offset" in es_cols
    finally:
        db.close()


def _create_old_db(path: Path, version: int) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        f"""
        CREATE TABLE rules (
            id TEXT PRIMARY KEY, short_id TEXT UNIQUE NOT NULL,
            trigger_text TEXT NOT NULL, action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        PRAGMA user_version = {version};
        """
    )
    conn.close()


def test_v2_schema_rejected(tmp_path):
    db_path = tmp_path / "rules.db"
    _create_old_db(db_path, 2)
    with pytest.raises(DbError, match="incompatible"):
        open_db(db_path)


def test_v3_schema_rejected(tmp_path):
    db_path = tmp_path / "rules.db"
    _create_old_db(db_path, 3)
    with pytest.raises(DbError, match="incompatible"):
        open_db(db_path)


def test_v4_schema_rejected(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    _create_old_db(db_path, 4)
    with pytest.raises(DbError, match="incompatible"):
        open_db(db_path)


def test_v5_schema_rejected(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    _create_old_db(db_path, 5)
    with pytest.raises(DbError, match="incompatible"):
        open_db(db_path)


def test_v1_schema_rejected(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    _create_v1_db(db_path)
    with pytest.raises(DbError, match="incompatible"):
        open_db(db_path)


def test_newer_schema_version_raises(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE rules (id TEXT PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(DbError, match="created by a newer nokori"):
        open_db(db_path)
