"""Tests for the Web UI API endpoints."""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from nokori.config import Config
from nokori.db import open_db
from nokori.utils.time import now_iso
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
def client_with_rule(cfg):
    db = open_db(cfg.db_path)
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, trigger_text, trigger_variants, "
            "search_terms, action, source_type, confidence, status, "
            "evidence_score, evidence_log, hit_count, shadow_hit_count, "
            "promotion_evidence, project_scope, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "rule-1", "abc", "when editing Python files", "[]",
                '{"en": ["python", "edit"]}',
                "use black formatter", "correction", "high", "active",
                3, "[]", 5, 0, "[]", "global", now, now,
            ),
        )
        tx.execute(
            "INSERT INTO injections (rule_id, session_id, prompt_hash, level, created_at) "
            "VALUES (?,?,?,?,?)",
            ("rule-1", "sess-1", "hash-1", "hot", now),
        )
        tx.execute(
            "INSERT INTO injections (rule_id, session_id, prompt_hash, level, created_at) "
            "VALUES (?,?,?,?,?)",
            ("rule-1", "sess-1", "hash-2", "warm", now),
        )
    db.close()
    app = create_app(cfg)
    return TestClient(app)


# --- Dashboard ---

class TestDashboard:
    def test_returns_rule_counts(self, client):
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "rules" in data
        assert data["rules"]["active"] == 0
        assert "injections_24h" in data
        assert "embed_server" in data
        assert "extract_pending" in data

    def test_with_data(self, client_with_rule):
        resp = client_with_rule.get("/api/dashboard")
        data = resp.json()["data"]
        assert data["rules"]["active"] == 1
        assert data["injections_24h"] == 2


# --- Rules ---

class TestRules:
    def test_list_empty(self, client):
        resp = client.get("/api/rules")
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 0

    def test_list_rules(self, client_with_rule):
        resp = client_with_rule.get("/api/rules")
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 1
        assert body["data"][0]["short_id"] == "abc"

    def test_filter_by_status(self, client_with_rule):
        resp = client_with_rule.get("/api/rules?status=dormant")
        assert resp.json()["meta"]["total"] == 0

    def test_show_rule(self, client_with_rule):
        resp = client_with_rule.get("/api/rules/abc")
        assert resp.status_code == 200
        assert resp.json()["data"]["trigger_text"] == "when editing Python files"

    def test_show_not_found(self, client_with_rule):
        resp = client_with_rule.get("/api/rules/zzz")
        assert resp.status_code == 404

    def test_edit_rule(self, client_with_rule):
        resp = client_with_rule.patch("/api/rules/abc", json={"action": "use ruff"})
        assert resp.status_code == 200
        assert resp.json()["data"]["action"] == "use ruff"

    def test_dismiss_rule(self, client_with_rule):
        resp = client_with_rule.post("/api/rules/abc/dismiss")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "archived"


# --- Retrieve ---

class TestRetrieve:
    def test_empty_prompt(self, client_with_rule):
        resp = client_with_rule.post(
            "/api/retrieve", json={"prompt": "", "use_embedding": False}
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["hot"] == []
        assert data["warm"] == []

    def test_matching_prompt(self, client_with_rule):
        resp = client_with_rule.post(
            "/api/retrieve",
            json={"prompt": "editing python files with black", "use_embedding": False},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["hot"]) + len(data["warm"]) > 0


# --- Injections ---

class TestInjections:
    def test_list(self, client_with_rule):
        resp = client_with_rule.get("/api/injections")
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 2

    def test_filter_level(self, client_with_rule):
        resp = client_with_rule.get("/api/injections?level=hot")
        assert resp.json()["meta"]["total"] == 1
        assert resp.json()["data"][0]["level"] == "hot"


# --- Extract ---

class TestExtract:
    def test_jobs(self, client):
        resp = client.get("/api/extract/jobs")
        assert resp.status_code == 200
        assert "pending" in resp.json()["data"]

    def test_state(self, client):
        resp = client.get("/api/extract/state")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)


# --- Lifecycle ---

class TestLifecycle:
    def test_promotion(self, client):
        resp = client.get("/api/lifecycle/promotion")
        assert resp.status_code == 200

    def test_maintenance(self, client):
        resp = client.get("/api/lifecycle/maintenance")
        assert resp.status_code == 200


# --- Config ---

class TestConfig:
    def test_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "data_dir" in data
        assert "gate_enabled" in data


# --- Health ---

class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert "db" in resp.json()["data"]


# --- CLI ---

class TestCLI:
    def test_web_subcommand_exists(self):
        from nokori.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["web"])
        assert args.command == "web"

    def test_web_port_flag(self):
        from nokori.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["web", "--port", "9999"])
        assert args.port == 9999

    def test_web_no_browser_flag(self):
        from nokori.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["web", "--no-browser"])
        assert args.no_browser is True
