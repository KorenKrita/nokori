"""Dismiss phrase matching in UserPromptSubmit."""
from datetime import UTC, datetime

from nokori.config import Config
from nokori.db import open_db
from nokori.hooks.user_prompt_submit import _run_dismiss


def test_dismiss_basic(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "source_origin, status, severity, "
                "project_scope, project_id, created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
                (
                    "rule-d1", "abc123", "t", "a",
                    "transcript_extraction", "active", "reminder",
                    "global", None, now, now,
                ),
            )
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, "
                "prompt_hash, level, created_at) VALUES (?,?,?,?,?,?)",
                ("fe-1", "rule-d1", "sess1", "h1", "hot", now),
            )
        n = _run_dismiss(db, "please dismiss abc123", "sess1", cfg)
        assert n == 1
        row = db.fetchone("SELECT status FROM rules WHERE id='rule-d1'")
        assert row["status"] == "archived"
    finally:
        db.close()


def test_dismiss_uppercase_short_id(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "source_origin, status, severity, "
                "project_scope, project_id, created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
                (
                    "rule-d2", "abc123", "t", "a",
                    "transcript_extraction", "active", "reminder",
                    "global", None, now, now,
                ),
            )
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, "
                "prompt_hash, level, created_at) VALUES (?,?,?,?,?,?)",
                ("fe-2", "rule-d2", "sess1", "h1", "hot", now),
            )
        n = _run_dismiss(db, "dismiss ABC123", "sess1", cfg)
        assert n == 1
        row = db.fetchone("SELECT status FROM rules WHERE id='rule-d2'")
        assert row["status"] == "archived"
    finally:
        db.close()
