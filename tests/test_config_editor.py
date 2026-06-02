import os

import pytest

from nokori.config import Config
from nokori.config_editor import get_editor_state, save_editor
from nokori.config_file import config_path, load_document
from nokori.errors import ConfigError


def _clear_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("NOKORI_"):
            monkeypatch.delenv(k, raising=False)


def test_editor_defaults_when_file_empty(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    state = get_editor_state(cfg, "zh")
    assert state["values"]["log_level"] == "warn"
    assert state["values"]["gate.enabled"] is True
    assert state["set_keys"] == []
    assert state["locale"] == "zh"
    assert state["schema"]["sections"][0]["label"] == "常规"


def test_save_writes_only_changed_keys(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    state = get_editor_state(cfg)
    values = dict(state["values"])
    values["log_level"] = "debug"
    values["gate.enabled"] = False
    save_editor(cfg, values=values, embed_mode="local", initial_set_keys=set())
    doc = load_document(config_path(tmp_path))
    assert doc["log_level"] == "debug"
    assert doc["gate"]["enabled"] is False
    assert "max_injection_chars" not in doc


def test_save_removes_key_when_reset_to_default(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    path = config_path(tmp_path)
    path.write_text('log_level = "debug"\n', encoding="utf-8")
    cfg = Config.from_env()
    state = get_editor_state(cfg)
    values = dict(state["values"])
    values["log_level"] = "warn"
    save_editor(
        cfg,
        values=values,
        embed_mode="local",
        initial_set_keys=set(state["set_keys"]),
    )
    doc = load_document(path)
    assert "log_level" not in doc


def test_embed_mode_local_clears_remote(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    path = config_path(tmp_path)
    path.write_text(
        '[embed]\nbase_url = "http://x/v1"\nmodel = "e"\n',
        encoding="utf-8",
    )
    cfg = Config.from_env()
    state = get_editor_state(cfg)
    save_editor(
        cfg,
        values=state["values"],
        embed_mode="local",
        initial_set_keys=set(state["set_keys"]),
    )
    doc = load_document(path)
    assert "embed" not in doc or "base_url" not in doc.get("embed", {})


def test_secret_unchanged_when_empty(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    path = config_path(tmp_path)
    path.write_text('[llm]\napi_key = "secret"\n', encoding="utf-8")
    cfg = Config.from_env()
    state = get_editor_state(cfg)
    values = dict(state["values"])
    values["llm.api_key"] = None
    save_editor(
        cfg,
        values=values,
        embed_mode="local",
        initial_set_keys=set(state["set_keys"]),
    )
    doc = load_document(path)
    assert doc["llm"]["api_key"] == "secret"


def test_invalid_int_raises(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    state = get_editor_state(cfg)
    values = dict(state["values"])
    values["gate.ttl_seconds"] = "nope"
    with pytest.raises(ConfigError):
        save_editor(cfg, values=values, embed_mode="local", initial_set_keys=set())
