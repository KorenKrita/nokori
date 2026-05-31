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
    env = {"PATH": "/usr/bin:/bin"}
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
    """No read_latest fallback: marker on disk but no injections → must not block."""
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
            tx.execute("DELETE FROM injections")
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
                "INSERT INTO rules (id, short_id, trigger_text, action, "
                "source_type, confidence, status, project_scope, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "r1", "abc123", "t", "a", "correction", "high", "active",
                    "global", now, now,
                ),
            )
        rule = Rule(
            id="r1",
            short_id="abc123",
            trigger_text="t",
            trigger_variants=[],
            search_terms={},
            action="a",
            rationale=None,
            behavior=None,
            source_type="correction",
            confidence="high",
            status="active",
            project_scope="global",
            project_id=None,
            evidence_score=0,
            evidence_log=[],
            hit_count=0,
            last_hit=None,
            shadow_hit_count=0,
            promotion_evidence=[],
            superseded_by=None,
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


def test_health_http_401_is_fail(monkeypatch, tmp_path):
    import urllib.error

    from nokori.commands import health

    def fake_open(req, timeout=5):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, None,
        )

    with patch("urllib.request.urlopen", side_effect=fake_open):
        status, _ = health._probe_openai_post(
            "http://fake",
            "m",
            "k",
            path_suffix="/embeddings",
            payload={"model": "m", "input": "ping"},
        )
    assert status == "fail"


def test_health_embed_skip_when_off(monkeypatch, tmp_path):
    from nokori.commands import health

    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    status, detail = health._check_embed(cfg, 0)
    assert status == "skip"
    assert detail.startswith("off —")
    assert "embed.enabled=false" in detail


def test_health_embed_local_running(monkeypatch, tmp_path):
    from dataclasses import replace

    from nokori.commands import health

    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_ENABLED", "1")
    cfg = Config.from_env()
    cfg2 = replace(cfg, embed_enabled=True, embed_base_url=None, embed_model=None)
    with patch("nokori.search.embedding.local_embed_package_available", return_value=True):
        with patch("nokori.search.embedding.local_model_cached", return_value=True):
            with patch(
                "nokori.search.embed_ipc.server_status",
                return_value={"running": True, "pid": 99, "socket": "/tmp/s.sock"},
            ):
                status, detail = health._check_embed(cfg2, 0)
    assert status == "ok"
    assert "mode=local" in detail
    assert "server=running" in detail
    assert "weights=cached" in detail


def test_health_embed_remote_fail(monkeypatch, tmp_path):
    import urllib.error
    from dataclasses import replace

    from nokori.commands import health

    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_ENABLED", "1")
    cfg = Config.from_env()
    cfg2 = replace(
        cfg,
        embed_enabled=True,
        embed_base_url="http://fake/v1",
        embed_model="emb",
    )

    def fake_open(req, timeout=15):
        raise urllib.error.HTTPError(req.full_url, 503, "down", {}, None)

    with patch("urllib.request.urlopen", side_effect=fake_open):
        status, detail = health._check_embed(cfg2, 0)
    assert status == "fail"
    assert "mode=remote" in detail
    assert "503" in detail


def test_health_llm_probe_uses_post():
    import io
    from unittest.mock import MagicMock

    from nokori.commands import health

    seen: list[str] = []

    def fake_open(req, timeout=15):
        seen.append(req.method)
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b"{}"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    with patch("urllib.request.urlopen", side_effect=fake_open):
        status, _ = health._probe_openai_post(
            "http://fake/v1",
            "test-model",
            "k",
            path_suffix="/chat/completions",
            payload={
                "model": "test-model",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
        )
    assert status == "ok"
    assert seen == ["POST"]


def test_malformed_marker_rule_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    sess = "bad-rules"
    path = cfg.marker_path(sess, "deadbeef")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "session_id": sess,
            "prompt_hash": "deadbeef",
            "created_at": "2026-01-01T00:00:00Z",
            "rules": [{"short_id": "x"}],  # missing action, source_type
        }),
        encoding="utf-8",
    )
    m = marker_io.read(cfg, sess, prompt_hash_value="deadbeef")
    assert m is not None
    assert m.rules == []
