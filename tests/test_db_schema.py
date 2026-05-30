"""Database schema version (v2 only; no v1 migration)."""
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


def test_fresh_db_is_schema_v2(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    db = open_db(tmp_path / "rules.db")
    try:
        assert db.schema_version() == SCHEMA_VERSION == 2
        cols = {r[1] for r in db.fetchall("PRAGMA table_info(rules)")}
        assert "shadow_hit_count" in cols
        assert "cross_project_hits" not in cols
    finally:
        db.close()


def test_v1_schema_rejected(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    _create_v1_db(db_path)
    with pytest.raises(DbError, match="not compatible with this nokori"):
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
