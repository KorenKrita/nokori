"""Verify Config dataclass fields stay in sync with config_schema FIELDS.

Catches the bug class where a developer adds a Config field but forgets
to add the corresponding schema entry (or vice versa).
"""
import dataclasses
import os

import pytest

from nokori.config import Config
from nokori.config_editor import _effective_values
from nokori.config_schema import FIELDS, derive_env_map
from nokori.errors import ConfigError

# Config fields that are computed/internal and intentionally have no
# direct schema entry. Each must have a comment justifying exclusion.
_EXCLUDED_CONFIG_FIELDS = frozenset({
    "embed_chunk_size_configured",   # computed: whether user explicitly set chunk_size
    "embed_chunk_count_configured",  # computed: whether user explicitly set chunk_count
    "role_models",                   # dict populated from [models] section; schema has per-role entries
    "role_max_tokens",               # dict populated from [models.limits]; schema has per-role entries
    "role_timeouts",                 # dict populated from [models.timeouts]; schema has per-role entries
})

# Schema entries that map to Config dict fields (role_models, etc.)
# rather than to a single scalar Config attribute.
_SCHEMA_DICT_FIELD_PREFIXES = ("models.",)


def _schema_id_to_config_field(schema_id: str) -> str:
    """Convert schema dotted ID to Config flat field name."""
    return schema_id.replace(".", "_")


def test_every_config_field_has_schema_entry():
    """Non-excluded Config fields must have a matching schema entry."""
    config_fields = {f.name for f in dataclasses.fields(Config)}
    schema_config_names = set()
    for field in FIELDS:
        if any(field.id.startswith(p) for p in _SCHEMA_DICT_FIELD_PREFIXES):
            continue
        schema_config_names.add(_schema_id_to_config_field(field.id))

    testable = config_fields - _EXCLUDED_CONFIG_FIELDS
    missing_in_schema = testable - schema_config_names
    assert not missing_in_schema, (
        f"Config fields without schema entry (add to config_schema.py or _EXCLUDED_CONFIG_FIELDS): "
        f"{sorted(missing_in_schema)}"
    )


def test_every_scalar_schema_entry_has_config_field():
    """Non-dict schema entries must map to a Config dataclass field."""
    config_fields = {f.name for f in dataclasses.fields(Config)}
    for field in FIELDS:
        if any(field.id.startswith(p) for p in _SCHEMA_DICT_FIELD_PREFIXES):
            continue
        expected_config_field = _schema_id_to_config_field(field.id)
        assert expected_config_field in config_fields, (
            f"Schema field {field.id!r} expects Config.{expected_config_field} but it doesn't exist"
        )


def test_excluded_fields_actually_exist():
    """Prevent stale entries in the exclusion set."""
    config_fields = {f.name for f in dataclasses.fields(Config)}
    stale = _EXCLUDED_CONFIG_FIELDS - config_fields
    assert not stale, f"_EXCLUDED_CONFIG_FIELDS contains non-existent fields: {sorted(stale)}"


# ---------------------------------------------------------------------------
# Golden snapshot: derive_env_map() must equal the previous hand-maintained literal
# ---------------------------------------------------------------------------

EXPECTED_LEGACY_MAP = {
    ("data_dir",): "NOKORI_DATA_DIR",
    ("max_injection_chars",): "NOKORI_MAX_INJECTION_CHARS",
    ("gate", "enabled"): "NOKORI_GATE_ENABLED",
    ("gate", "ttl_seconds"): "NOKORI_GATE_TTL_SECONDS",
    ("gate", "matcher"): "NOKORI_GATE_MATCHER",
    ("extract", "mode"): "NOKORI_EXTRACT_MODE",
    ("extract", "defer_when_active"): "NOKORI_EXTRACT_DEFER_ACTIVE",
    ("extract", "fork_cache"): "NOKORI_EXTRACT_FORK_CACHE",
    ("llm", "base_url"): "NOKORI_LLM_BASE_URL",
    ("llm", "model"): "NOKORI_LLM_MODEL",
    ("llm", "api_key"): "NOKORI_LLM_API_KEY",
    ("embed", "enabled"): "NOKORI_EMBED_ENABLED",
    ("embed", "base_url"): "NOKORI_EMBED_BASE_URL",
    ("embed", "model"): "NOKORI_EMBED_MODEL",
    ("embed", "api_key"): "NOKORI_EMBED_API_KEY",
    ("embed", "dimensions"): "NOKORI_EMBED_DIMENSIONS",
    ("embed", "chunk_size"): "NOKORI_EMBED_CHUNK_SIZE",
    ("embed", "chunk_count"): "NOKORI_EMBED_CHUNK_COUNT",
    ("embed", "hook_timeout_seconds"): "NOKORI_HOOK_EMBED_TIMEOUT",
    ("embed", "server_idle_seconds"): "NOKORI_EMBED_SERVER_IDLE",
    ("embed", "server_auto_start"): "NOKORI_EMBED_SERVER_AUTO_START",
    ("hot_cache", "enabled"): "NOKORI_HOT_CACHE",
    ("session", "idle_seconds"): "NOKORI_SESSION_IDLE_SECONDS",
    ("promotion", "enabled"): "NOKORI_PROMOTION_ENABLED",
    ("strict",): "NOKORI_STRICT",
    ("disabled",): "NOKORI_DISABLED",
    ("dismiss_phrase",): "NOKORI_DISMISS_PHRASE",
    ("log_level",): "NOKORI_LOG_LEVEL",
}


def test_derive_env_map_matches_legacy_literal():
    """derive_env_map() must produce the exact same map as the old hand-maintained literal."""
    derived = derive_env_map()
    assert derived == EXPECTED_LEGACY_MAP


# ---------------------------------------------------------------------------
# Exclusive-group validation: embed_backend
# ---------------------------------------------------------------------------


def _clear_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("NOKORI_"):
            monkeypatch.delenv(k, raising=False)


class TestExclusiveGroupValidation:
    """Load-time invariant: embed_backend exclusive_group."""

    def test_embed_enabled_model_set_base_url_missing_raises(self, monkeypatch, tmp_path):
        """embed enabled + model set + base_url MISSING -> ConfigError containing 'embed.base_url'."""
        _clear_env(monkeypatch)
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EMBED_ENABLED", "1")
        monkeypatch.setenv("NOKORI_EMBED_MODEL", "text-embedding-3-small")
        with pytest.raises(ConfigError, match="embed.base_url"):
            Config.from_env()

    def test_embed_enabled_base_url_set_model_missing_raises(self, monkeypatch, tmp_path):
        """embed enabled + base_url set + model MISSING -> ConfigError containing 'embed.model'."""
        _clear_env(monkeypatch)
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EMBED_ENABLED", "1")
        monkeypatch.setenv("NOKORI_EMBED_BASE_URL", "https://api.openai.com/v1")
        with pytest.raises(ConfigError, match="embed.model"):
            Config.from_env()

    def test_embed_enabled_both_set_no_error(self, monkeypatch, tmp_path):
        """embed enabled + both base_url and model set -> no error."""
        _clear_env(monkeypatch)
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EMBED_ENABLED", "1")
        monkeypatch.setenv("NOKORI_EMBED_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setenv("NOKORI_EMBED_MODEL", "text-embedding-3-small")
        cfg = Config.from_env()
        assert cfg.embed_enabled is True
        assert cfg.embed_base_url == "https://api.openai.com/v1"
        assert cfg.embed_model == "text-embedding-3-small"

    def test_embed_enabled_neither_set_no_error(self, monkeypatch, tmp_path):
        """embed enabled + neither base_url nor model set (local variant) -> no error."""
        _clear_env(monkeypatch)
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EMBED_ENABLED", "1")
        cfg = Config.from_env()
        assert cfg.embed_enabled is True
        assert cfg.embed_base_url is None
        assert cfg.embed_model is None

    def test_embed_disabled_remote_fields_missing_no_error(self, monkeypatch, tmp_path):
        """embed disabled + remote fields missing -> no error."""
        _clear_env(monkeypatch)
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EMBED_ENABLED", "0")
        cfg = Config.from_env()
        assert cfg.embed_enabled is False


# ---------------------------------------------------------------------------
# _effective_values equivalence: derived loop produces expected keys/values
# ---------------------------------------------------------------------------


class TestEffectiveValues:
    """Verify _effective_values derives all non-models.* keys from FIELDS."""

    def _make_cfg(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("NOKORI_LLM_MODEL", "gpt-4")
        monkeypatch.setenv("NOKORI_LLM_API_KEY", "sk-secret")
        monkeypatch.setenv("NOKORI_EMBED_BASE_URL", "https://embed.example.com/v1")
        monkeypatch.setenv("NOKORI_EMBED_MODEL", "nomic")
        monkeypatch.setenv("NOKORI_EMBED_API_KEY", "sk-embed")
        monkeypatch.setenv("NOKORI_EMBED_ENABLED", "1")
        return Config.from_env()

    def test_all_non_models_fields_present(self, monkeypatch, tmp_path):
        """Every non-models.* FieldDef.id appears as a key in _effective_values."""
        cfg = self._make_cfg(monkeypatch, tmp_path)
        effective = _effective_values(cfg)
        for field in FIELDS:
            if field.id.startswith("models."):
                continue
            assert field.id in effective, f"Missing key: {field.id}"

    def test_data_dir_is_string(self, monkeypatch, tmp_path):
        cfg = self._make_cfg(monkeypatch, tmp_path)
        effective = _effective_values(cfg)
        assert effective["data_dir"] == str(cfg.data_dir)
        assert isinstance(effective["data_dir"], str)

    def test_secret_fields_masked(self, monkeypatch, tmp_path):
        cfg = self._make_cfg(monkeypatch, tmp_path)
        effective = _effective_values(cfg)
        assert effective["llm.api_key"] == "***"
        assert effective["embed.api_key"] == "***"

    def test_secret_fields_empty_when_unset(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        effective = _effective_values(cfg)
        assert effective["llm.api_key"] == ""
        assert effective["embed.api_key"] == ""

    def test_string_none_becomes_empty(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        effective = _effective_values(cfg)
        # llm.base_url is None on cfg, should be "" in effective
        assert cfg.llm_base_url is None
        assert effective["llm.base_url"] == ""

    def test_int_and_bool_direct(self, monkeypatch, tmp_path):
        cfg = self._make_cfg(monkeypatch, tmp_path)
        effective = _effective_values(cfg)
        assert effective["max_injection_chars"] == 1500
        assert effective["gate.enabled"] is True
        assert effective["embed.dimensions"] == 0
        assert effective["disabled"] is False
