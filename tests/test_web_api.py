"""Tests for the Web UI API endpoints."""
from __future__ import annotations

import pytest

pytest.importorskip("httpx2")

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
                "when editing Python files",
                '[{"text":"editing Python files","kind":"strong_anchor","requires_concepts":["manual_trigger"]}]',
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
        assert body["data"][0]["trigger_variants"][0]["text"] == "editing Python files"

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
    def test_manual_lifecycle_rejections_always_forbidden(
        self, client_with_rule, action
    ):
        resp = client_with_rule.post(f"/api/rules/abc/{action}")
        assert resp.status_code == 403
        assert "not supported" in resp.json()["detail"]

    @pytest.mark.parametrize("action", ["promote", "trust", "suppress"])
    def test_manual_lifecycle_rejected_after_auth(self, client_with_rule, action):
        """After authenticating via GET /api/config, promote/trust/suppress still return 403."""
        client_with_rule.get("/api/config")
        resp = client_with_rule.post(f"/api/rules/abc/{action}")
        assert resp.status_code == 403
        assert "not supported" in resp.json()["detail"]

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


# --- Promotion Barriers ---


class TestPromotionBarriers:
    @staticmethod
    def _insert_rule(db, rule_id, short_id, status):
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
                    rule_id, short_id, 1, 1,
                    "v1", "v1",
                    "test trigger",
                    '[{"text":"test","kind":"strong_anchor","requires_concepts":["manual_trigger"]}]',
                    '{"en": ["test"]}',
                    "test action",
                    "transcript_extraction", status, "reminder",
                    3.0,
                    "global", now, now,
                ),
            )

    def test_candidate_barriers(self, cfg):
        db = open_db(cfg.db_path)
        try:
            self._insert_rule(db, "rule-b", "bbb", "candidate")
        finally:
            db.close()
        client = TestClient(create_app(cfg))
        resp = client.get("/api/lifecycle/rules/bbb/barriers")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["current_state"] == "candidate"
        assert data["target_state"] == "active"
        threshold_names = {t["name"] for t in data["thresholds"]}
        assert threshold_names == {
            "shadow_strong_match_count",
            "evaluated_shadow_match_count",
            "distinct_shadow_sessions",
            "counterfactual_would_help_high",
            "risky_or_near_miss_shadow_count",
            "shadow_false_positive_rate",
        }
        assert data["blocking"] == "shadow_strong_match_count"

    def test_trusted_barriers_returns_null(self, cfg):
        db = open_db(cfg.db_path)
        try:
            self._insert_rule(db, "rule-t", "ttt", "trusted")
        finally:
            db.close()
        client = TestClient(create_app(cfg))
        resp = client.get("/api/lifecycle/rules/ttt/barriers")
        assert resp.status_code == 200
        assert resp.json()["data"] is None

    def test_barriers_not_found(self, client_with_rule):
        resp = client_with_rule.get("/api/lifecycle/rules/zzz/barriers")
        assert resp.status_code == 404


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

    def test_config_editor_put_rolls_back_existing_file_on_reload_failure(
        self, client, cfg, monkeypatch
    ):
        """If Config.from_env fails after save_editor, the previous file content is restored."""
        from nokori.config_editor import config_path

        path = config_path(cfg.data_dir)
        original = 'log_level = "debug"\n'
        path.write_text(original, encoding="utf-8")

        # Authenticate so the write token cookie is set.
        client.get("/api/config")

        from nokori.errors import ConfigError
        from nokori.web.api import config_api

        def flaky_from_env(*args, **kwargs):
            # The reload inside config_editor_put must fail to trigger rollback.
            raise ConfigError("simulated reload failure")

        monkeypatch.setattr(config_api.Config, "from_env", flaky_from_env)

        resp = client.put(
            "/api/config/editor",
            json={"values": {"gate.enabled": False}, "set_keys": ["log_level"]},
        )
        assert resp.status_code == 500
        assert resp.json()["detail"] == "config reload failed"
        # File must be restored to the exact previous content.
        assert path.read_text(encoding="utf-8") == original

    def test_config_editor_put_rolls_back_missing_file_on_reload_failure(
        self, client, cfg, monkeypatch
    ):
        """If the config file did not exist before, rollback removes the newly written file."""
        from nokori.config_editor import config_path

        path = config_path(cfg.data_dir)
        assert not path.exists()

        client.get("/api/config")

        from nokori.errors import ConfigError
        from nokori.web.api import config_api

        def flaky_from_env(*args, **kwargs):
            raise ConfigError("simulated reload failure")

        monkeypatch.setattr(config_api.Config, "from_env", flaky_from_env)

        resp = client.put(
            "/api/config/editor",
            json={"values": {"gate.enabled": False}, "set_keys": []},
        )
        assert resp.status_code == 500
        assert resp.json()["detail"] == "config reload failed"
        # Rollback must delete the file that save_editor just created.
        assert not path.exists()


class TestStaticFiles:
    def test_spa_fallback_rejects_path_traversal(self, client):
        resp = client.get("/%2E%2E/app.py")
        assert resp.status_code != 200 or "def create_app" not in resp.text


# --- Embed ---


class TestEmbed:
    def test_embed_status(self, client):
        resp = client.get("/api/embed/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "running" in data
        assert "package_installed" in data
        assert "model_cached" in data
        assert isinstance(data["running"], bool)


# --- Config editor GET ---


class TestConfigEditorGet:
    def test_config_editor_get(self, client):
        resp = client.get("/api/config/editor")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "schema" in data or "groups" in data or "values" in data or isinstance(data, dict)


# --- Lifecycle detail endpoints ---


class TestLifecycleDetail:
    def test_fire_events(self, client_with_rule):
        resp = client_with_rule.get("/api/lifecycle/rules/abc/fire-events")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["level"] in ("hot", "warm", "gate")

    def test_shadow_events_empty(self, client_with_rule):
        resp = client_with_rule.get("/api/lifecycle/rules/abc/shadow-events")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_posthoc_summary(self, client_with_rule):
        resp = client_with_rule.get("/api/lifecycle/rules/abc/posthoc")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["short_id"] == "abc"
        assert "total_evaluated" in data

    def test_synthetic_eval_empty(self, client_with_rule):
        resp = client_with_rule.get("/api/lifecycle/rules/abc/synthetic-eval")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body

    def test_transitions(self, client_with_rule):
        resp = client_with_rule.get("/api/lifecycle/rules/abc/transitions")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, (list, dict))

    def test_fire_events_not_found(self, client):
        resp = client.get("/api/lifecycle/rules/zzz/fire-events")
        assert resp.status_code == 404


# --- Health ---

class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["db"]["status"] == "ok"
