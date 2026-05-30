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
    assert cfg.embed_dimensions == 0
    assert cfg.disabled is False
    assert cfg.dismiss_phrase == "dismiss"
    assert cfg.hot_cache_enabled is True
    assert cfg.embed_hook_timeout_seconds == 2
    assert cfg.embed_server_idle_seconds == 3600
    assert cfg.promotion_enabled is True


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


def test_config_toml_loaded(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'log_level = "debug"\n'
        '\n'
        '[llm]\n'
        'base_url = "http://localhost:11434/v1"\n'
        'model = "qwen2.5:7b"\n'
        'api_key = "sk-test"\n'
        '\n'
        '[embed]\n'
        'base_url = "http://localhost:11434/v1"\n'
        'model = "nomic-embed"\n'
        'dimensions = 768\n'
        '\n'
        '[gate]\n'
        'ttl_seconds = 300\n'
        'enabled = false\n'
    )
    cfg = Config.from_env()
    assert cfg.llm_base_url == "http://localhost:11434/v1"
    assert cfg.llm_model == "qwen2.5:7b"
    assert cfg.llm_api_key == "sk-test"
    assert cfg.embed_base_url == "http://localhost:11434/v1"
    assert cfg.embed_model == "nomic-embed"
    assert cfg.embed_dimensions == 768
    assert cfg.gate_ttl_seconds == 300
    assert cfg.gate_enabled is False
    assert cfg.log_level == "debug"


def test_env_overrides_config_toml(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_LLM_MODEL", "override-model")
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[llm]\n'
        'base_url = "http://from-file/v1"\n'
        'model = "from-file-model"\n'
    )
    cfg = Config.from_env()
    assert cfg.llm_model == "override-model"
    assert cfg.llm_base_url == "http://from-file/v1"


def test_missing_config_toml_is_fine(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.llm_base_url is None
