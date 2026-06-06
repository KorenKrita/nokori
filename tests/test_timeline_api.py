"""Tests for the timeline and monitor API endpoints."""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from dataclasses import replace

from fastapi.testclient import TestClient

from nokori.config import Config
from nokori.db import open_db
from nokori.events.observability import write_error, write_event
from nokori.web.app import create_app


@pytest.fixture
def cfg(tmp_path):
    base = Config.from_env()
    return replace(base, data_dir=tmp_path)


@pytest.fixture
def client(cfg):
    app = create_app(cfg)
    return TestClient(app)


@pytest.fixture
def seeded_db(cfg):
    db = open_db(cfg.db_path)
    write_event(db, source="session_start", session_id="s1", outcome="ok", details={"rule_count": 5})
    write_event(db, source="user_prompt_submit", session_id="s1", outcome="injected", prompt_snippet="hello", details={"hot_count": 2})
    write_event(db, source="pre_tool_use", session_id="s1", outcome="passed_no_marker", details={"tool_name": "Edit"})
    write_event(db, source="session_end", session_id="s1", outcome="ok", details={"extract_job_written": True})
    write_event(db, source="session_start", session_id="s2", outcome="ok")
    write_event(db, source="cold_pipeline", outcome="active", details={"trigger_preview": "test rule"})
    write_error(db, source="cold_pipeline", role="extractor", error_type="timeout", message="timed out", model_id="deepseek-v4")
    write_error(db, source="cold_pipeline", role="judge", error_type="json_parse", message="invalid json", model_id="gpt-4o")
    db.close()


class TestTimelineAPI:
    def test_get_timeline_empty(self, client):
        resp = client.get("/api/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["count"] == 0

    def test_get_timeline_all(self, client, seeded_db):
        resp = client.get("/api/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 6

    def test_get_timeline_session_filter(self, client, seeded_db):
        resp = client.get("/api/timeline?session_id=s1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 4
        for event in data["events"]:
            assert event["session_id"] == "s1"

    def test_get_timeline_source_filter(self, client, seeded_db):
        resp = client.get("/api/timeline?source=pre_tool_use")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["events"][0]["outcome"] == "passed_no_marker"

    def test_get_timeline_pagination(self, client, seeded_db):
        resp = client.get("/api/timeline?limit=2")
        data = resp.json()
        assert data["count"] == 2
        assert data["has_more"] is True
        first_id = data["events"][1]["id"]

        resp2 = client.get(f"/api/timeline?after_id={first_id}&limit=50")
        data2 = resp2.json()
        assert data2["count"] == 4
        assert data2["has_more"] is False

    def test_get_timeline_details_parsed(self, client, seeded_db):
        resp = client.get("/api/timeline?source=session_start&session_id=s1")
        data = resp.json()
        assert data["count"] == 1
        event = data["events"][0]
        assert isinstance(event["details"], dict)
        assert event["details"]["rule_count"] == 5


class TestTimelineSessionsAPI:
    def test_get_sessions_empty(self, client):
        resp = client.get("/api/timeline/sessions")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    def test_get_sessions_list(self, client, seeded_db):
        resp = client.get("/api/timeline/sessions")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 2
        session_ids = {s["session_id"] for s in sessions}
        assert session_ids == {"s1", "s2"}
        assert "last_active" in sessions[0]
        assert "event_count" in sessions[0]


class TestMonitorOverviewAPI:
    def test_overview_empty(self, client):
        resp = client.get("/api/monitor/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_events"] == 0
        assert data["total_errors"] == 0

    def test_overview_with_data(self, client, seeded_db):
        resp = client.get("/api/monitor/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_events"] == 6
        assert data["total_errors"] == 2
        assert len(data["events_by_source"]) > 0
        assert len(data["error_summary"]) > 0

    def test_overview_session_filter(self, client, seeded_db):
        resp = client.get("/api/monitor/overview?session_id=s1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_events"] == 4


class TestMonitorErrorsAPI:
    def test_errors_group_by_role(self, client, seeded_db):
        resp = client.get("/api/monitor/errors?group_by=role")
        assert resp.status_code == 200
        data = resp.json()
        assert data["group_by"] == "role"
        assert len(data["errors"]) == 2

    def test_errors_group_by_model(self, client, seeded_db):
        resp = client.get("/api/monitor/errors?group_by=model_id")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) == 2

    def test_errors_group_by_type(self, client, seeded_db):
        resp = client.get("/api/monitor/errors?group_by=error_type")
        assert resp.status_code == 200
        data = resp.json()
        by_type = {r["error_type"]: r["count"] for r in data["errors"]}
        assert by_type["timeout"] == 1
        assert by_type["json_parse"] == 1


class TestMonitorErrorTrendAPI:
    def test_trend_empty(self, client):
        resp = client.get("/api/monitor/errors/trend")
        assert resp.status_code == 200
        assert resp.json()["trend"] == []

    def test_trend_with_data(self, client, seeded_db):
        resp = client.get("/api/monitor/errors/trend")
        assert resp.status_code == 200
        trend = resp.json()["trend"]
        assert len(trend) > 0
        assert "day" in trend[0]
        assert "error_type" in trend[0]
        assert "count" in trend[0]
