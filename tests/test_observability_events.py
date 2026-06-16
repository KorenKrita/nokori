"""Tests for the observability event writing infrastructure.

Validates: write_event/write_error fail-open semantics, query helpers,
maintenance cleanup, and schema v6->v7 migration.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from nokori.db import SCHEMA_VERSION, open_db
from nokori.events.observability import (
    query_errors,
    query_events,
    write_error,
    write_event,
)
from nokori.lifecycle.maintenance import _set_last_run, run_observability_cleanup
from nokori.utils.time import iso_of


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    d = open_db(db_path)
    yield d
    d.close()


class TestWriteEvent:
    def test_basic_write(self, db):
        write_event(
            db,
            source="session_start",
            session_id="sess-123",
            outcome="embed_kickstart_ok",
            details={"embed_mode": "local", "rule_count": 42},
        )
        rows = db.fetchall("SELECT * FROM hook_events")
        assert len(rows) == 1
        row = rows[0]
        assert row["source"] == "session_start"
        assert row["session_id"] == "sess-123"
        assert row["outcome"] == "embed_kickstart_ok"
        detail = json.loads(row["details"])
        assert detail["embed_mode"] == "local"
        assert detail["rule_count"] == 42
        from datetime import datetime as _dt
        _dt.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")

    def test_null_session_id(self, db):
        write_event(db, source="cli_extract", outcome="extracted_3")
        rows = db.fetchall("SELECT * FROM hook_events")
        assert len(rows) == 1
        assert rows[0]["session_id"] is None

    def test_prompt_snippet_stored(self, db):
        write_event(
            db,
            source="user_prompt_submit",
            session_id="s1",
            outcome="injected_3",
            prompt_snippet="帮我重构这个函数到新的模块里...",
            details={"hot_count": 2, "warm_count": 1},
        )
        row = db.fetchone("SELECT prompt_snippet FROM hook_events")
        assert row["prompt_snippet"] == "帮我重构这个函数到新的模块里..."

    def test_fail_open_on_closed_db(self, tmp_path):
        db_path = tmp_path / "test_closed.db"
        d = open_db(db_path)
        d.close()
        result = write_event(d, source="session_start", outcome="test")
        assert result is None

    def test_multiple_events_ordered(self, db):
        for i in range(5):
            write_event(db, source=f"source_{i}", outcome=f"outcome_{i}")
        rows = db.fetchall("SELECT * FROM hook_events ORDER BY rowid ASC")
        assert len(rows) == 5
        assert rows[0]["source"] == "source_0"
        assert rows[4]["source"] == "source_4"

    def test_details_none_stored_as_null(self, db):
        write_event(db, source="pre_tool_use", outcome="noop")
        row = db.fetchone("SELECT details FROM hook_events")
        assert row["details"] is None

    def test_returns_event_id(self, db):
        event_id = write_event(db, source="test", outcome="ok")
        assert event_id is not None
        assert len(event_id) == 36  # UUID format

    def test_uuid_uniqueness(self, db):
        ids = set()
        for _ in range(10):
            eid = write_event(db, source="test", outcome="ok")
            ids.add(eid)
        assert len(ids) == 10


class TestWriteError:
    def test_basic_error(self, db):
        write_error(
            db,
            source="cold_extract",
            role="extractor",
            error_type="json_parse",
            message="Expecting ',' delimiter: line 3 column 5",
            session_id="sess-456",
            model_id="deepseek-v4-pro",
            details={"raw_response_prefix": "```json\n{invalid..."},
        )
        rows = db.fetchall("SELECT * FROM error_events")
        assert len(rows) == 1
        row = rows[0]
        assert row["source"] == "cold_extract"
        assert row["role"] == "extractor"
        assert row["model_id"] == "deepseek-v4-pro"
        assert row["error_type"] == "json_parse"
        assert "delimiter" in row["message"]
        assert row["session_id"] == "sess-456"

    def test_null_session_and_model(self, db):
        write_error(
            db,
            source="maintenance",
            role="system",
            error_type="connection",
            message="embed server unreachable",
        )
        row = db.fetchone("SELECT * FROM error_events")
        assert row["session_id"] is None
        assert row["model_id"] is None

    def test_fail_open_on_closed_db(self, tmp_path):
        db_path = tmp_path / "test_closed2.db"
        d = open_db(db_path)
        d.close()
        result = write_error(d, source="hook", role="system", error_type="unknown", message="x")
        assert result is None

    def test_returns_event_id(self, db):
        eid = write_error(db, source="cold", role="judge", error_type="timeout", message="t")
        assert eid is not None
        assert len(eid) == 36

    def test_default_role_is_system(self, db):
        db.conn.execute(
            "INSERT INTO error_events (id, source, error_type, message, created_at) "
            "VALUES ('test-1', 'hook', 'timeout', 'msg', '2026-01-01T00:00:00Z')"
        )
        row = db.fetchone("SELECT role FROM error_events WHERE id = 'test-1'")
        assert row["role"] == "system"


class TestQueryEvents:
    def test_query_with_session_filter(self, db):
        write_event(db, source="session_start", session_id="s1", outcome="ok")
        write_event(db, source="session_start", session_id="s2", outcome="ok")
        write_event(db, source="cli_extract", outcome="done")
        results = query_events(db, session_id="s1")
        assert len(results) == 1
        assert results[0]["session_id"] == "s1"

    def test_query_with_source_filter(self, db):
        write_event(db, source="session_start", session_id="s1", outcome="ok")
        write_event(db, source="pre_tool_use", session_id="s1", outcome="noop")
        results = query_events(db, source="pre_tool_use")
        assert len(results) == 1
        assert results[0]["source"] == "pre_tool_use"

    def test_query_all_no_filter(self, db):
        write_event(db, source="a", outcome="1")
        write_event(db, source="b", outcome="2")
        results = query_events(db)
        assert len(results) == 2

    def test_query_after_cursor(self, db):
        write_event(db, source="a", outcome="1")
        first = db.fetchone("SELECT id FROM hook_events")
        write_event(db, source="b", outcome="2")
        results = query_events(db, after_id=first["id"])
        assert len(results) == 1
        assert results[0]["source"] == "b"

    def test_query_limit(self, db):
        for i in range(10):
            write_event(db, source=f"s{i}", outcome=f"o{i}")
        results = query_events(db, limit=3)
        assert len(results) == 3

    def test_query_combined_filters(self, db):
        write_event(db, source="pre_tool_use", session_id="s1", outcome="blocked")
        write_event(db, source="pre_tool_use", session_id="s2", outcome="noop")
        write_event(db, source="session_start", session_id="s1", outcome="ok")
        results = query_events(db, session_id="s1", source="pre_tool_use")
        assert len(results) == 1
        assert results[0]["outcome"] == "blocked"

    def test_query_returns_oldest_first(self, db):
        write_event(db, source="first", outcome="1")
        write_event(db, source="second", outcome="2")
        write_event(db, source="third", outcome="3")
        results = query_events(db)
        assert results[0]["source"] == "first"
        assert results[2]["source"] == "third"


class TestQueryErrors:
    def test_query_errors_group_by_role(self, db):
        write_error(db, source="cold", role="extractor", error_type="timeout", message="t")
        write_error(db, source="cold", role="extractor", error_type="timeout", message="t")
        write_error(db, source="cold", role="judge", error_type="json_parse", message="j")
        results = query_errors(db, group_by="role")
        assert len(results) == 2
        by_role = {r["role"]: r["count"] for r in results}
        assert by_role["extractor"] == 2
        assert by_role["judge"] == 1

    def test_query_errors_group_by_error_type(self, db):
        write_error(db, source="cold", role="extractor", error_type="timeout", message="t")
        write_error(db, source="cold", role="judge", error_type="timeout", message="t")
        write_error(db, source="cold", role="judge", error_type="json_parse", message="j")
        results = query_errors(db, group_by="error_type")
        by_type = {r["error_type"]: r["count"] for r in results}
        assert by_type["timeout"] == 2
        assert by_type["json_parse"] == 1

    def test_query_errors_with_session_filter(self, db):
        write_error(db, source="hook", role="system", error_type="timeout", message="t", session_id="s1")
        write_error(db, source="hook", role="system", error_type="timeout", message="t", session_id="s2")
        results = query_errors(db, group_by="role", session_id="s1")
        assert len(results) == 1
        assert results[0]["count"] == 1

    def test_query_errors_with_since_filter(self, db):
        old_time = iso_of(datetime.now(UTC) - timedelta(days=10))
        db.conn.execute(
            "INSERT INTO error_events (id, source, role, error_type, message, created_at) "
            "VALUES ('old-1', 'cold', 'extractor', 'timeout', 't', ?)",
            (old_time,),
        )
        write_error(db, source="cold", role="judge", error_type="json_parse", message="j")
        since = iso_of(datetime.now(UTC) - timedelta(days=1))
        results = query_errors(db, group_by="role", since=since)
        assert len(results) == 1
        assert results[0]["role"] == "judge"

    def test_query_errors_invalid_group_by_defaults_to_role(self, db):
        write_error(db, source="cold", role="extractor", error_type="timeout", message="t")
        results = query_errors(db, group_by="invalid_column")
        assert len(results) == 1
        assert "role" in results[0]


class TestObservabilityCleanup:
    def test_deletes_old_events(self, db):
        old_time = iso_of(datetime.now(UTC) - timedelta(days=31))

        db.conn.execute(
            "INSERT INTO hook_events (id, source, outcome, created_at) VALUES (?, ?, ?, ?)",
            ("old-1", "session_start", "ok", old_time),
        )
        db.conn.execute(
            "INSERT INTO error_events (id, source, role, error_type, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("old-err-1", "cold", "extractor", "timeout", "t", old_time),
        )
        write_event(db, source="session_start", outcome="recent")
        write_error(db, source="cold", role="judge", error_type="json_parse", message="j")

        deleted = run_observability_cleanup(db, force=True)
        assert deleted["hook_events_deleted"] == 1
        assert deleted["error_events_deleted"] == 1

        assert len(db.fetchall("SELECT * FROM hook_events")) == 1
        assert len(db.fetchall("SELECT * FROM error_events")) == 1

    def test_skips_when_not_due(self, db):
        _set_last_run(db, "observability_cleanup")
        write_event(db, source="test", outcome="x")
        deleted = run_observability_cleanup(db)
        assert deleted["hook_events_deleted"] == 0
        assert len(db.fetchall("SELECT * FROM hook_events")) == 1

    def test_keeps_recent_events(self, db):
        write_event(db, source="recent1", outcome="ok")
        write_event(db, source="recent2", outcome="ok")
        write_error(db, source="cold", role="judge", error_type="timeout", message="m")

        deleted = run_observability_cleanup(db, force=True)
        assert deleted["hook_events_deleted"] == 0
        assert deleted["error_events_deleted"] == 0
        assert len(db.fetchall("SELECT * FROM hook_events")) == 2
        assert len(db.fetchall("SELECT * FROM error_events")) == 1


class TestSchemaMigration:
    def test_fresh_db_has_observability_tables(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        d = open_db(db_path)
        try:
            assert d.schema_version() == SCHEMA_VERSION
            tables = {
                r["name"]
                for r in d.fetchall(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "hook_events" in tables
            assert "error_events" in tables
        finally:
            d.close()

    def test_v6_db_migrates_to_latest(self, tmp_path):
        import sqlite3

        from nokori.db import SCHEMA_VERSION

        db_path = tmp_path / "v6.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            "CREATE TABLE rules (id TEXT PRIMARY KEY, status TEXT DEFAULT 'candidate');\n"
            "CREATE TABLE maintenance_meta (key TEXT PRIMARY KEY, last_run TEXT NOT NULL);\n"
            "PRAGMA user_version = 6;\n"
        )
        conn.close()

        d = open_db(db_path)
        try:
            assert d.schema_version() == SCHEMA_VERSION
            tables = {
                r["name"]
                for r in d.fetchall(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "hook_events" in tables
            assert "error_events" in tables

            eid = write_event(d, source="test", outcome="ok")
            assert eid is not None
            err_id = write_error(d, source="test", role="system", error_type="test", message="m")
            assert err_id is not None
        finally:
            d.close()

    def test_v5_db_raises_error(self, tmp_path):
        import sqlite3

        from nokori.errors import DbError

        db_path = tmp_path / "v5.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            "CREATE TABLE rules (id TEXT PRIMARY KEY);\n"
            "PRAGMA user_version = 5;\n"
        )
        conn.close()

        with pytest.raises(DbError, match="incompatible"):
            open_db(db_path)
