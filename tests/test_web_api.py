"""Tests for the Web UI API endpoints."""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from dataclasses import replace

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
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, trigger_variants, "
            "search_terms, action_instruction, "
            "source_origin, status, severity, "
            "evidence_support_score, "
            "project_scope, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "rule-1", "abc", 1, 1,
                "v1", "v1",
                "when editing Python files", "[]",
                '{"en": ["python", "edit"]}',
                "use black formatter",
                "transcript_extraction", "active", "reminder",
                3.0,
                "global", now, now,
            ),
        )
        tx.execute(
            "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("fe-1", "rule-1", "sess-1", "hash-1", "hot", now),
        )
        tx.execute(
            "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("fe-2", "rule-1", "sess-1", "hash-2", "warm", now),
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
        assert "trusted" in data["rules"]
        assert "suppressed" in data["rules"]
        assert "dormant" not in data["rules"]
        assert "merged" not in data["rules"]
        assert "fire_events_24h" in data
        assert "fire_events_hot_24h" in data
        assert "embed_server" in data
        assert "extract_pending" in data

    def test_with_data(self, client_with_rule):
        resp = client_with_rule.get("/api/dashboard")
        data = resp.json()["data"]
        assert data["rules"]["active"] == 1
        assert data["rules"]["trusted"] == 0
        assert data["fire_events_24h"] == 2
        assert data["fire_events_hot_24h"] == 1


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
        resp = client_with_rule.get("/api/rules?status=suppressed")
        assert resp.json()["meta"]["total"] == 0

    def test_show_rule(self, client_with_rule):
        resp = client_with_rule.get("/api/rules/abc")
        assert resp.status_code == 200
        assert resp.json()["data"]["trigger_canonical"] == "when editing Python files"

    def test_show_not_found(self, client_with_rule):
        resp = client_with_rule.get("/api/rules/zzz")
        assert resp.status_code == 404

    def test_archive_requires_write_auth(self, client_with_rule):
        resp = client_with_rule.post("/api/rules/abc/archive")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "write authentication required"

    @pytest.mark.parametrize("action", ["promote", "trust", "suppress"])
    def test_manual_lifecycle_rejections_require_write_auth_first(
        self, client_with_rule, action
    ):
        resp = client_with_rule.post(f"/api/rules/abc/{action}")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "write authentication required"

    def test_archive_rule(self, client_with_rule):
        client_with_rule.get("/api/config")
        resp = client_with_rule.post("/api/rules/abc/archive")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "archived"

    def test_dismiss_rule(self, client_with_rule):
        client_with_rule.get("/api/config")
        resp = client_with_rule.post("/api/rules/abc/dismiss")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "archived"


class TestEmbedAuth:
    @pytest.mark.parametrize("path", ["/api/embed/start", "/api/embed/stop"])
    def test_embed_mutations_require_write_auth(self, client, path):
        resp = client.post(path)
        assert resp.status_code == 403
        assert resp.json()["detail"] == "write authentication required"


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

    def test_prompt_rejects_excessive_length(self, client_with_rule):
        resp = client_with_rule.post(
            "/api/retrieve", json={"prompt": "x" * 20001, "use_embedding": False}
        )
        assert resp.status_code == 422


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

    def test_config_editor_put_requires_write_auth(self, client):
        resp = client.put("/api/config/editor", json={"values": {}, "set_keys": []})
        assert resp.status_code == 403


class TestStaticFiles:
    def test_spa_fallback_rejects_path_traversal(self, client):
        resp = client.get("/%2E%2E/app.py")
        assert resp.status_code != 200 or "def create_app" not in resp.text


# --- Health ---

class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["db"]["status"] == "ok"
