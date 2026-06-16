"""Tests for _maybe_spawn_cold_eval in session_start hook."""
import json
from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.hooks import session_start
from nokori.hooks.session_start import _maybe_spawn_cold_eval
from nokori.lifecycle.maintenance import cold_eval_due, mark_cold_eval_run


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    return Config.from_env()


@pytest.fixture
def db(cfg):
    d = open_db(cfg.db_path)
    yield d
    d.close()


def _insert_unlabeled_shadow_event(db, rule_id="r1"):
    """Insert a minimal candidate rule and an unlabeled shadow event."""
    from nokori.utils.time import now_iso

    ts = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT OR IGNORE INTO rules (id, short_id, trigger_canonical, action_instruction, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, 'candidate', ?, ?)",
            (rule_id, rule_id[:8], "test trigger", "test action", ts, ts),
        )
        tx.execute(
            "INSERT INTO rule_shadow_events (id, rule_id, session_id, "
            "status_at_match, shadow_type, created_at) "
            "VALUES (?, ?, 'sess1', 'candidate', 'candidate_probe', ?)",
            (f"se_{rule_id}", rule_id, ts),
        )


def _insert_pending_posthoc_job(db):
    """Insert a minimal pending posthoc job."""
    from nokori.utils.time import now_iso

    ts = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT OR IGNORE INTO rules (id, short_id, trigger_canonical, action_instruction, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
            ("r_ph", "r_ph1234", "trigger", "action", ts, ts),
        )
        tx.execute(
            "INSERT INTO rule_fire_events (id, rule_id, session_id, created_at) "
            "VALUES (?, ?, 'sess1', ?)",
            ("fe1", "r_ph", ts),
        )
        tx.execute(
            "INSERT INTO posthoc_jobs (id, fire_event_id, status, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            ("pj1", "fe1", ts, ts),
        )


def test_cold_eval_spawns_when_unlabeled_shadow_events(db, cfg):
    _insert_unlabeled_shadow_event(db)

    with patch("subprocess.Popen") as mock_popen:
        result = _maybe_spawn_cold_eval(db, cfg)

    assert result is True
    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert "maintain" in cmd


def test_cold_eval_spawns_when_pending_posthoc(db, cfg):
    _insert_pending_posthoc_job(db)

    with patch("subprocess.Popen") as mock_popen:
        result = _maybe_spawn_cold_eval(db, cfg)

    assert result is True
    mock_popen.assert_called_once()


def test_cold_eval_skips_when_nothing_pending(db, cfg):
    with patch("subprocess.Popen") as mock_popen:
        result = _maybe_spawn_cold_eval(db, cfg)

    assert result is False
    mock_popen.assert_not_called()


def test_cold_eval_skips_when_recently_run(db, cfg):
    _insert_unlabeled_shadow_event(db)
    mark_cold_eval_run(db)

    with patch("subprocess.Popen") as mock_popen:
        result = _maybe_spawn_cold_eval(db, cfg)

    assert result is False
    mock_popen.assert_not_called()


def test_cold_eval_due_respects_interval(db):
    assert cold_eval_due(db, 1) is True
    mark_cold_eval_run(db)
    assert cold_eval_due(db, 1) is False


def test_cold_eval_passes_anthropic_env(db, cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    _insert_unlabeled_shadow_event(db)

    with patch("subprocess.Popen") as mock_popen:
        _maybe_spawn_cold_eval(db, cfg)

    env = mock_popen.call_args[1]["env"]
    assert env.get("ANTHROPIC_API_KEY") == "sk-test-key"


def test_cold_eval_popen_failure_returns_false(db, cfg):
    _insert_unlabeled_shadow_event(db)

    with patch("subprocess.Popen", side_effect=OSError("spawn failed")):
        result = _maybe_spawn_cold_eval(db, cfg)

    assert result is False


def test_handle_includes_cold_eval_in_event(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    _insert_unlabeled_shadow_event(db)
    db.close()

    with patch("subprocess.Popen"):
        resp = session_start.handle(
            {"session_id": "test-sess", "cwd": str(tmp_path)},
            cfg,
            host=session_start.Host.CLAUDE,
        )

    assert resp.get("continue") is not False

    db = open_db(cfg.db_path)
    try:
        row = db.fetchone(
            "SELECT details FROM hook_events WHERE source = 'session_start' "
            "ORDER BY created_at DESC LIMIT 1"
        )
    finally:
        db.close()

    details = json.loads(row["details"])
    assert details["cold_eval_spawned"] is True
