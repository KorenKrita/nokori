"""Session touch mtime-based coalescing — skip redundant writes."""
from __future__ import annotations

import json
import os
from datetime import datetime
from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.utils import sessions
from nokori.utils.sessions import _TOUCH_INTERVAL_SECONDS


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    c = Config.from_env()
    c.ensure_dirs()
    return c


def test_first_touch_on_missing_file_registers(cfg):
    """If session file does not exist, touch falls through to register()."""
    sessions.touch(cfg, "sess-new")

    p = cfg.sessions_dir / "sess-new.json"
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-new"
    datetime.fromisoformat(data["last_activity"])


def test_immediate_second_touch_skipped(cfg):
    """Second touch within interval does NOT update the file."""
    sessions.register(cfg, "sess-2")
    p = cfg.sessions_dir / "sess-2.json"
    known_mtime = 1700000000.0
    os.utime(p, (known_mtime, known_mtime))
    content_before = p.read_text(encoding="utf-8")

    with patch("nokori.utils.sessions.time.time", return_value=known_mtime + _TOUCH_INTERVAL_SECONDS - 1):
        sessions.touch(cfg, "sess-2")

    assert p.read_text(encoding="utf-8") == content_before
    assert p.stat().st_mtime == known_mtime


def test_touch_writes_after_interval_elapsed(cfg):
    """Touch DOES update when mtime is older than _TOUCH_INTERVAL_SECONDS."""
    sessions.register(cfg, "sess-3")
    p = cfg.sessions_dir / "sess-3.json"

    known_mtime = 1700000000.0
    os.utime(p, (known_mtime, known_mtime))
    content_before = p.read_text(encoding="utf-8")

    with patch("nokori.utils.sessions.time.time", return_value=known_mtime + _TOUCH_INTERVAL_SECONDS + 1):
        with patch("nokori.utils.sessions.now_iso", return_value="2099-01-01 00:00:00"):
            sessions.touch(cfg, "sess-3")

    content_after = p.read_text(encoding="utf-8")
    assert content_after != content_before
    data = json.loads(content_after)
    assert data["last_activity"] == "2099-01-01 00:00:00"


def test_touch_writes_at_exact_boundary(cfg):
    """Touch writes when delta equals _TOUCH_INTERVAL_SECONDS (not strictly less)."""
    sessions.register(cfg, "sess-boundary")
    p = cfg.sessions_dir / "sess-boundary.json"
    known_mtime = 1700000000.0
    os.utime(p, (known_mtime, known_mtime))
    content_before = p.read_text(encoding="utf-8")

    with patch("nokori.utils.sessions.time.time", return_value=known_mtime + _TOUCH_INTERVAL_SECONDS):
        with patch("nokori.utils.sessions.now_iso", return_value="2099-01-01 00:00:00"):
            sessions.touch(cfg, "sess-boundary")

    content_after = p.read_text(encoding="utf-8")
    assert content_after != content_before
    data = json.loads(content_after)
    assert data["last_activity"] == "2099-01-01 00:00:00"


def test_touch_stat_failure_falls_through(cfg):
    """If stat raises OSError (e.g. file deleted between calls), touch still works."""
    sessions.register(cfg, "sess-4")
    p = cfg.sessions_dir / "sess-4.json"
    p.unlink()

    sessions.touch(cfg, "sess-4")

    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-4"
