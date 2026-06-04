"""Regression tests for review-round hook/extract/health fixes."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.gate import marker as marker_io
from nokori.models import Rule
from nokori.search import embedding


def _run(*args, env_extra=None, stdin: str = ""):
    env = {
        "PATH": "/usr/bin:/bin",
        "NOKORI_EMBED_ENABLED": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "nokori", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def _gate_denied(out: dict) -> bool:
    hso = out.get("hookSpecificOutput") or {}
    return hso.get("permissionDecision") == "deny"


def test_pre_tool_use_fail_open_without_injection_hash(tmp_path, monkeypatch):
    """No read_latest fallback: marker on disk but no fire events -> must not block."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    env = {"NOKORI_DATA_DIR": str(tmp_path)}
    r = _run(
        "add",
        "--trigger",
        "never force push",
        "--action",
        "use lease",
        "--source-type",
        "correction",
        "--confidence",
        "high",
        "--variants",
        "git push --force",
        env_extra=env,
    )
    assert r.returncode == 0, r.stderr
    sess = "no-hash-anchor"
    _run(
        "hook",
        "user-prompt-submit",
        env_extra=env,
        stdin=json.dumps({
            "session_id": sess,
            "cwd": str(tmp_path),
            "prompt": "git push --force",
        }),
    )
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        with db.transaction() as tx:
            tx.execute("DELETE FROM rule_fire_events")
    finally:
        db.close()
    r = _run(
        "hook",
        "pre-tool-use",
        env_extra=env,
        stdin=json.dumps({"session_id": sess, "tool_name": "Bash"}),
    )
    out = json.loads(r.stdout)
    assert not _gate_denied(out)


def test_install_rejects_corrupt_settings(tmp_path):
    home = tmp_path / "claude"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text("{not json", encoding="utf-8")
    r = _run(
        "install",
        env_extra={
            "NOKORI_DATA_DIR": str(tmp_path / "data"),
            "NOKORI_CLAUDE_HOME": str(home),
        },
    )
    assert r.returncode == 1
    assert "not valid JSON" in r.stderr


def test_extract_lock_busy_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    from nokori.extract import lock as extract_lock

    with extract_lock.acquire(cfg) as held:
        assert held
        r = _run("extract", env_extra={"NOKORI_DATA_DIR": str(tmp_path)})
    assert r.returncode == 2
    assert "already running" in r.stdout


def test_embedding_search_filters_model_version(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = "2026-01-01T00:00:00Z"
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "source_origin, status, severity, "
                "project_scope, created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?)",
                (
                    "r1", "abc123", "t", "a",
                    "transcript_extraction", "active", "reminder",
                    "global", now, now,
                ),
            )
        rule = Rule(
            id="r1",
            short_id="abc123",
            schema_version=1,
            rule_version=1,
            created_by_pipeline_version="v1",
            runtime_policy_version="v1",
            last_rewritten_by_role=None,
            status="active",
            severity="reminder",
            trigger_canonical="t",
            trigger_variants=[],
            search_terms={},
            action_instruction="a",
            project_scope="global",
            project_id=None,
            archived_reason=None,
            created_at=now,
            updated_at=now,
        )
        embedding._store_impl(db, "r1", [[1.0, 0.0]], "old-model")
        hits = embedding._search_impl([1.0, 0.0], [rule], db, 5, "new-model")
        assert hits == []
        hits = embedding._search_impl([1.0, 0.0], [rule], db, 5, "old-model")
        assert len(hits) == 1
    finally:
        db.close()
