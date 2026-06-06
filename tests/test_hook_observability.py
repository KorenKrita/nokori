"""Tests that hook instrumentation writes observability events correctly."""
from __future__ import annotations

import json

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.utils.host import Host


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    return Config.from_env()


@pytest.fixture
def db(cfg):
    d = open_db(cfg.db_path)
    yield d
    d.close()


class TestSessionStartEvent:
    def test_writes_event_on_normal_start(self, cfg, db):
        from nokori.hooks.session_start import handle

        payload = {"session_id": "test-sess-001", "cwd": "/tmp"}
        handle(payload, cfg, host=Host.CLAUDE)

        rows = db.fetchall(
            "SELECT * FROM hook_events WHERE source = 'session_start'"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["session_id"] == "test-sess-001"
        assert row["outcome"] == "ok"
        details = json.loads(row["details"])
        assert "embed_status" in details
        assert "rule_count" in details
        assert details["hot_cache_injected"] is False
        assert details["maintenance_ok"] is True


class TestUserPromptSubmitEvent:
    def test_writes_event_no_rules(self, cfg, db):
        from nokori.hooks.user_prompt_submit import handle

        payload = {"session_id": "test-sess-002", "prompt": "hello world", "cwd": "/tmp"}
        handle(payload, cfg, host=Host.CLAUDE)

        rows = db.fetchall(
            "SELECT * FROM hook_events WHERE source = 'user_prompt_submit'"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["session_id"] == "test-sess-002"
        assert row["outcome"] == "no_rules"
        assert row["prompt_snippet"] == "hello world"

    def test_writes_event_with_rules_in_pool(self, cfg, db, tmp_path):
        from nokori.hooks.user_prompt_submit import handle
        from nokori.utils.time import now_iso

        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules "
                "(id, short_id, schema_version, rule_version, runtime_policy_version, "
                "status, severity, trigger_canonical, action_instruction, "
                "concepts, required_concept_groups, trigger_variants, search_terms, "
                "project_scope, created_at, updated_at) "
                "VALUES (?, ?, 7, 1, '1.0.0', 'active', 'reminder', ?, ?, "
                "'[{\"id\": \"refactor\", \"label\": \"refactor\", \"aliases\": [{\"text\": \"refactor\", \"strength\": \"strong\"}], \"match_mode\": \"any\", \"required\": true}]', '[{\"id\": \"g1\", \"all_of\": [\"refactor\"]}]', "
                "'[{\"text\": \"refactor function\", \"kind\": \"strong_anchor\"}]', "
                "'{\"en\": [\"refactor\", \"function\", \"module\"]}', "
                "'global', ?, ?)",
                ("rule-001", "abc123", "refactor function to new module",
                 "ensure tests pass after refactoring",
                 now_iso(), now_iso()),
            )

        payload = {
            "session_id": "test-sess-003",
            "prompt": "refactor function to a separate module",
            "cwd": "/tmp",
        }
        handle(payload, cfg, host=Host.CLAUDE)

        rows = db.fetchall(
            "SELECT * FROM hook_events WHERE source = 'user_prompt_submit'"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["session_id"] == "test-sess-003"
        assert row["outcome"] in ("injected", "no_matches")
        assert row["prompt_snippet"] is not None


class TestPreToolUseEvent:
    def test_writes_event_gate_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_GATE_ENABLED", "0")
        cfg_local = Config.from_env()
        from nokori.hooks.pre_tool_use import handle

        payload = {"session_id": "test-sess-004", "tool_name": "Edit"}
        handle(payload, cfg_local, host=Host.CLAUDE)

        d = open_db(cfg_local.db_path)
        try:
            rows = d.fetchall(
                "SELECT * FROM hook_events WHERE source = 'pre_tool_use'"
            )
            assert len(rows) == 1
            assert rows[0]["outcome"] == "passed_gate_disabled"
            details = json.loads(rows[0]["details"])
            assert details["tool_name"] == "Edit"
        finally:
            d.close()

    def test_writes_event_no_marker(self, cfg):
        from nokori.hooks.pre_tool_use import handle

        payload = {"session_id": "test-sess-005", "tool_name": "Write"}
        handle(payload, cfg, host=Host.CLAUDE)

        d = open_db(cfg.db_path)
        try:
            rows = d.fetchall(
                "SELECT * FROM hook_events WHERE source = 'pre_tool_use'"
            )
            assert len(rows) == 1
            assert rows[0]["outcome"] == "passed_no_prompt_hash"
        finally:
            d.close()


class TestSessionEndEvent:
    def test_writes_event_on_end(self, cfg):
        from nokori.hooks.session_end import handle

        payload = {"session_id": "test-sess-006", "cwd": "/tmp"}
        handle(payload, cfg, host=Host.CLAUDE)

        d = open_db(cfg.db_path)
        try:
            rows = d.fetchall(
                "SELECT * FROM hook_events WHERE source = 'session_end'"
            )
            assert len(rows) == 1
            row = rows[0]
            assert row["session_id"] == "test-sess-006"
            details = json.loads(row["details"])
            assert "posthoc_enqueued" in details
            assert "extract_job_written" in details
            assert "async_extract_spawned" in details
        finally:
            d.close()
