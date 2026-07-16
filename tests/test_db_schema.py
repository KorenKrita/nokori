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


def test_current_schema_remediation_adds_missing_column_once(tmp_path):
    """Older v10 DBs missing last_error get one-shot remediation, then skip."""
    from nokori.db.schema import _REMEDIATION_KEY, _SCHEMA_DDL, _remediation_done

    db_path = tmp_path / "rules.db"
    conn = sqlite3.connect(str(db_path))
    # Build current schema then drop last_error to simulate older v10 DDL.
    conn.executescript(_SCHEMA_DDL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    # SQLite cannot DROP COLUMN easily on all versions — recreate ingest table without it.
    conn.executescript(
        """
        CREATE TABLE transcript_ingest_jobs_old AS SELECT
            id, transcript_segment_ref, segment_hash, status, ttl_expires_at,
            extractor_prompt_version, pipeline_checkpoint, retries, created_at, updated_at
        FROM transcript_ingest_jobs;
        DROP TABLE transcript_ingest_jobs;
        CREATE TABLE transcript_ingest_jobs (
            id TEXT PRIMARY KEY,
            transcript_segment_ref TEXT,
            segment_hash TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            ttl_expires_at TEXT,
            extractor_prompt_version TEXT,
            pipeline_checkpoint TEXT,
            retries INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO transcript_ingest_jobs SELECT * FROM transcript_ingest_jobs_old;
        DROP TABLE transcript_ingest_jobs_old;
        """
    )
    conn.commit()
    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(transcript_ingest_jobs)")}
    assert "last_error" not in cols_before
    assert not _remediation_done(conn)
    conn.close()

    db = open_db(db_path)
    try:
        cols = {r[1] for r in db.fetchall("PRAGMA table_info(transcript_ingest_jobs)")}
        assert "last_error" in cols
        assert db.schema_version() == SCHEMA_VERSION
        marker = db.fetchone(
            "SELECT key FROM maintenance_meta WHERE key = ?",
            (_REMEDIATION_KEY,),
        )
        assert marker is not None
    finally:
        db.close()

    # Second open must not fail and marker stays set
    db2 = open_db(db_path)
    try:
        assert db2.schema_version() == SCHEMA_VERSION
        assert db2.fetchone(
            "SELECT 1 FROM maintenance_meta WHERE key = ?",
            (_REMEDIATION_KEY,),
        )
    finally:
        db2.close()


def test_fresh_db_marks_remediation_done(tmp_path):
    from nokori.db.schema import _REMEDIATION_KEY

    db = open_db(tmp_path / "fresh.db")
    try:
        row = db.fetchone(
            "SELECT 1 FROM maintenance_meta WHERE key = ?",
            (_REMEDIATION_KEY,),
        )
        assert row is not None
    finally:
        db.close()
