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
        assert db.schema_version() == SCHEMA_VERSION == 5
        cols = {r[1] for r in db.fetchall("PRAGMA table_info(rules)")}
        assert "shadow_hit_count" in cols
        assert "cross_project_hits" not in cols
        assert "trigger_text_zh" in cols
        assert "behavior_zh" in cols
        assert "action_zh" in cols
        assert "rationale_zh" in cols
        assert "trigger_variants_zh" in cols
        es_cols = {r[1] for r in db.fetchall("PRAGMA table_info(extract_state)")}
        assert "last_byte_offset" in es_cols
    finally:
        db.close()


def _create_v2_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE rules (
            id TEXT PRIMARY KEY, short_id TEXT UNIQUE NOT NULL,
            trigger_text TEXT NOT NULL, trigger_variants TEXT NOT NULL DEFAULT '[]',
            search_terms TEXT NOT NULL DEFAULT '{}', behavior TEXT,
            action TEXT NOT NULL, rationale TEXT,
            source_type TEXT NOT NULL, confidence TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            evidence_score INTEGER NOT NULL DEFAULT 0,
            evidence_log TEXT NOT NULL DEFAULT '[]',
            hit_count INTEGER NOT NULL DEFAULT 0, last_hit TEXT,
            shadow_hit_count INTEGER NOT NULL DEFAULT 0,
            promotion_evidence TEXT NOT NULL DEFAULT '[]',
            project_scope TEXT NOT NULL DEFAULT 'project', project_id TEXT,
            superseded_by TEXT, archived_reason TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE extract_state (
            transcript_path TEXT PRIMARY KEY,
            transcript_mtime REAL NOT NULL,
            extracted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'done'
        );
        PRAGMA user_version = 2;
        """
    )
    conn.close()


def test_v2_to_v5_migration(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    _create_v2_db(db_path)
    db = open_db(db_path)
    try:
        assert db.schema_version() == 5
        es_cols = {r[1] for r in db.fetchall("PRAGMA table_info(extract_state)")}
        assert "last_byte_offset" in es_cols
        cols = {r[1] for r in db.fetchall("PRAGMA table_info(rules)")}
        assert "trigger_text_zh" in cols
        assert "behavior_zh" in cols
        assert "action_zh" in cols
        assert "rationale_zh" in cols
        assert "trigger_variants_zh" in cols
    finally:
        db.close()


def _create_v3_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE rules (
            id TEXT PRIMARY KEY, short_id TEXT UNIQUE NOT NULL,
            trigger_text TEXT NOT NULL, trigger_variants TEXT NOT NULL DEFAULT '[]',
            search_terms TEXT NOT NULL DEFAULT '{}', behavior TEXT,
            action TEXT NOT NULL, rationale TEXT,
            source_type TEXT NOT NULL, confidence TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            evidence_score INTEGER NOT NULL DEFAULT 0,
            evidence_log TEXT NOT NULL DEFAULT '[]',
            hit_count INTEGER NOT NULL DEFAULT 0, last_hit TEXT,
            shadow_hit_count INTEGER NOT NULL DEFAULT 0,
            promotion_evidence TEXT NOT NULL DEFAULT '[]',
            project_scope TEXT NOT NULL DEFAULT 'project', project_id TEXT,
            superseded_by TEXT, archived_reason TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE extract_state (
            transcript_path TEXT PRIMARY KEY,
            transcript_mtime REAL NOT NULL,
            extracted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'done',
            last_byte_offset INTEGER NOT NULL DEFAULT 0
        );
        PRAGMA user_version = 3;
        """
    )
    conn.close()


def test_v3_to_v5_migration(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    _create_v3_db(db_path)
    db = open_db(db_path)
    try:
        assert db.schema_version() == 5
        cols = {r[1] for r in db.fetchall("PRAGMA table_info(rules)")}
        assert "trigger_text_zh" in cols
        assert "behavior_zh" in cols
        assert "action_zh" in cols
        assert "rationale_zh" in cols
        assert "trigger_variants_zh" in cols
    finally:
        db.close()


def _create_v4_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE rules (
            id TEXT PRIMARY KEY, short_id TEXT UNIQUE NOT NULL,
            trigger_text TEXT NOT NULL, trigger_variants TEXT NOT NULL DEFAULT '[]',
            search_terms TEXT NOT NULL DEFAULT '{}', behavior TEXT,
            action TEXT NOT NULL, rationale TEXT,
            source_type TEXT NOT NULL, confidence TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            evidence_score INTEGER NOT NULL DEFAULT 0,
            evidence_log TEXT NOT NULL DEFAULT '[]',
            hit_count INTEGER NOT NULL DEFAULT 0, last_hit TEXT,
            shadow_hit_count INTEGER NOT NULL DEFAULT 0,
            promotion_evidence TEXT NOT NULL DEFAULT '[]',
            project_scope TEXT NOT NULL DEFAULT 'project', project_id TEXT,
            superseded_by TEXT, archived_reason TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            trigger_text_zh TEXT, behavior_zh TEXT,
            action_zh TEXT, rationale_zh TEXT
        );
        CREATE TABLE extract_state (
            transcript_path TEXT PRIMARY KEY,
            transcript_mtime REAL NOT NULL,
            extracted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'done',
            last_byte_offset INTEGER NOT NULL DEFAULT 0
        );
        PRAGMA user_version = 4;
        """
    )
    conn.close()


def test_v4_to_v5_migration(monkeypatch, tmp_path):
    db_path = tmp_path / "rules.db"
    _create_v4_db(db_path)
    db = open_db(db_path)
    try:
        assert db.schema_version() == 5
        cols = {r[1] for r in db.fetchall("PRAGMA table_info(rules)")}
        assert "trigger_variants_zh" in cols
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
