import os

import pytest

from nokori.config import Config
from nokori.errors import ConfigError


def _clear_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("NOKORI_"):
            monkeypatch.delenv(k, raising=False)


def test_defaults(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.data_dir == tmp_path.resolve()
    assert cfg.max_injection_chars == 1500
    assert cfg.gate_enabled is True
    assert cfg.gate_ttl_seconds == 600
    assert cfg.extract_mode == "manual"
    assert cfg.embed_dimensions == 384
    assert cfg.disabled is False
    assert cfg.dismiss_phrase == "dismiss"


def test_bool_parsing(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_GATE_ENABLED", "off")
    monkeypatch.setenv("NOKORI_DISABLED", "yes")
    cfg = Config.from_env()
    assert cfg.gate_enabled is False
    assert cfg.disabled is True


def test_int_parsing(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_GATE_TTL_SECONDS", "120")
    cfg = Config.from_env()
    assert cfg.gate_ttl_seconds == 120


def test_invalid_int_raises(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_DIMENSIONS", "abc")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_invalid_extract_mode(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EXTRACT_MODE", "wat")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_paths(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.db_path == tmp_path.resolve() / "rules.db"
    assert cfg.logs_dir == tmp_path.resolve() / "logs"
    assert cfg.jobs_dir == tmp_path.resolve() / "jobs"
    assert cfg.sessions_dir == tmp_path.resolve() / "active_sessions"
    cfg.ensure_dirs()
    assert cfg.logs_dir.is_dir()
    assert cfg.jobs_dir.is_dir()


def test_marker_path_sanitizes_session(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    p = cfg.marker_path("abc/../etc")
    assert ".." not in p.name
    assert p.parent == tmp_path.resolve()
