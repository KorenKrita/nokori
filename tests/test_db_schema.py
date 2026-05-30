"""Database schema version and migrations."""
import sqlite3
from pathlib import Path

import pytest

from nokori.db import SCHEMA_VERSION, open_db
from nokori.errors import DbError


def _create_v1_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE rules (
            id TEXT PRIMARY KEY,
            short_id TEXT UNIQUE NOT NULL,
            trigger_text TEXT NOT NULL,
            trigger_variants TEXT NOT NULL DEFAULT '[]',
            search_terms TEXT NOT NULL DEFAULT '{}',
            behavior TEXT,
            action TEXT NOT NULL,
            rationale TEXT,
            source_type TEXT NOT NULL,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL,
            evidence_score INTEGER NOT NULL DEFAULT 0,
            evidence_log TEXT NOT NULL DEFAULT '[]',
            hit_count INTEGER NOT NULL DEFAULT 0,
            last_hit TEXT,
            cross_project_hits INTEGER NOT NULL DEFAULT 0,
            promotion_evidence TEXT NOT NULL DEFAULT '[]',
            project_scope TEXT NOT NULL DEFAULT 'project',
            project_id TEXT,
            superseded_by TEXT,
            archived_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
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


def test_migrate_v1_renames_shadow_hit_count(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    _create_v1_db(db_path)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    db = open_db(db_path)
    try:
        assert db.schema_version() == 2
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, action, source_type, "
                "confidence, status, project_scope, created_at, updated_at, "
                "shadow_hit_count) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "r1", "abc123", "t", "a", "correction", "high", "active",
                    "project", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 3,
                ),
            )
        row = db.fetchone("SELECT shadow_hit_count FROM rules WHERE id='r1'")
        assert row["shadow_hit_count"] == 3
    finally:
        db.close()


def test_newer_schema_version_raises(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE rules (id TEXT PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(DbError, match="newer than this nokori"):
        open_db(db_path)
