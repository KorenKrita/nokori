from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_GATE_MATCHER
from .errors import ConfigError


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

_CONFIG_FILE_NAME = "config.toml"


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


# --- Config file → flat dict mapping ---

_TOML_TO_ENV = {
    ("data_dir",): "NOKORI_DATA_DIR",
    ("max_injection_chars",): "NOKORI_MAX_INJECTION_CHARS",
    ("gate", "enabled"): "NOKORI_GATE_ENABLED",
    ("gate", "ttl_seconds"): "NOKORI_GATE_TTL_SECONDS",
    ("gate", "matcher"): "NOKORI_GATE_MATCHER",
    ("extract", "mode"): "NOKORI_EXTRACT_MODE",
    ("extract", "defer_when_active"): "NOKORI_EXTRACT_DEFER_ACTIVE",
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


def _get_nested(d: dict, keys: tuple[str, ...]):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
        if d is None:
            return None
    return d


def _resolve_file_values(data_dir_hint: str) -> dict[str, str]:
    """Read config.toml and return a flat {ENV_NAME: value_str} dict."""
    data_dir = Path(data_dir_hint).expanduser().resolve()
    config_path = data_dir / _CONFIG_FILE_NAME
    if not config_path.exists():
        default_dir = Path("~/.nokori").expanduser().resolve()
        if data_dir != default_dir:
            return {}
        config_path = default_dir / _CONFIG_FILE_NAME
    toml = _load_toml(config_path)
    if not toml:
        return {}
    flat: dict[str, str] = {}
    for keys, env_name in _TOML_TO_ENV.items():
        val = _get_nested(toml, keys)
        if val is None:
            continue
        if isinstance(val, bool):
            flat[env_name] = "1" if val else "0"
        else:
            flat[env_name] = str(val)
    return flat


# --- Env + file resolution helpers ---


def _get(name: str, file_values: dict[str, str]) -> str | None:
    """Env var takes priority over config file value."""
    env = os.environ.get(name)
    if env is not None:
        return env
    return file_values.get(name)


def _bool_val(name: str, default: bool, file_values: dict[str, str]) -> bool:
    raw = _get(name, file_values)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (got {raw!r})")


def _int_val(name: str, default: int, file_values: dict[str, str], *, min_value: int | None = None) -> int:
    raw = _get(name, file_values)
    if raw is None or raw.strip() == "":
        return default
    try:
        n = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer (got {raw!r})") from e
    if min_value is not None and n < min_value:
        raise ConfigError(f"{name} must be >= {min_value} (got {n})")
    return n


def _str_or_none_val(name: str, file_values: dict[str, str]) -> str | None:
    raw = _get(name, file_values)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _str_val(name: str, default: str, file_values: dict[str, str]) -> str:
    raw = _get(name, file_values)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _enum_val(name: str, default: str, choices: tuple[str, ...], file_values: dict[str, str]) -> str:
    raw = _str_val(name, default, file_values)
    if raw not in choices:
        raise ConfigError(f"{name} must be one of {choices} (got {raw!r})")
    return raw


_GATE_MATCHER_MAX_LEN = 512


def _gate_matcher_val(file_values: dict[str, str]) -> str:
    raw = _str_val(
        "NOKORI_GATE_MATCHER", DEFAULT_GATE_MATCHER, file_values
    )
    if len(raw) > _GATE_MATCHER_MAX_LEN:
        raise ConfigError(
            f"NOKORI_GATE_MATCHER exceeds {_GATE_MATCHER_MAX_LEN} characters"
        )
    return raw


def _expand_path(p: str) -> Path:
    return Path(p).expanduser().resolve()


@dataclass(frozen=True)
class Config:
    data_dir: Path
    max_injection_chars: int
    gate_enabled: bool
    gate_ttl_seconds: int
    gate_matcher: str
    extract_mode: str
    extract_defer_when_active: bool
    llm_base_url: str | None
    llm_model: str | None
    llm_api_key: str | None
    embed_enabled: bool
    embed_base_url: str | None
    embed_model: str | None
    embed_api_key: str | None
    embed_dimensions: int
    embed_chunk_size: int
    embed_chunk_count: int
    embed_hook_timeout_seconds: int
    embed_server_idle_seconds: int
    embed_server_auto_start: bool
    hot_cache_enabled: bool
    session_idle_seconds: int
    promotion_enabled: bool
    strict: bool
    disabled: bool
    dismiss_phrase: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        data_dir_raw = os.environ.get("NOKORI_DATA_DIR") or "~/.nokori"
        file_values = _resolve_file_values(data_dir_raw)
        data_dir = _expand_path(_str_val("NOKORI_DATA_DIR", "~/.nokori", file_values))
        return cls(
            data_dir=data_dir,
            max_injection_chars=_int_val("NOKORI_MAX_INJECTION_CHARS", 1500, file_values, min_value=0),
            gate_enabled=_bool_val("NOKORI_GATE_ENABLED", True, file_values),
            gate_ttl_seconds=_int_val("NOKORI_GATE_TTL_SECONDS", 600, file_values, min_value=0),
            gate_matcher=_gate_matcher_val(file_values),
            extract_mode=_enum_val("NOKORI_EXTRACT_MODE", "manual", ("manual", "async"), file_values),
            extract_defer_when_active=_bool_val(
                "NOKORI_EXTRACT_DEFER_ACTIVE", False, file_values
            ),
            llm_base_url=_str_or_none_val("NOKORI_LLM_BASE_URL", file_values),
            llm_model=_str_or_none_val("NOKORI_LLM_MODEL", file_values),
            llm_api_key=_str_or_none_val("NOKORI_LLM_API_KEY", file_values),
            embed_enabled=_bool_val("NOKORI_EMBED_ENABLED", False, file_values),
            embed_base_url=_str_or_none_val("NOKORI_EMBED_BASE_URL", file_values),
            embed_model=_str_or_none_val("NOKORI_EMBED_MODEL", file_values),
            embed_api_key=_str_or_none_val("NOKORI_EMBED_API_KEY", file_values),
            embed_dimensions=_int_val("NOKORI_EMBED_DIMENSIONS", 0, file_values, min_value=0),
            embed_chunk_size=_int_val("NOKORI_EMBED_CHUNK_SIZE", 512, file_values, min_value=16),
            embed_chunk_count=_int_val("NOKORI_EMBED_CHUNK_COUNT", 3, file_values, min_value=1),
            embed_hook_timeout_seconds=_int_val(
                "NOKORI_HOOK_EMBED_TIMEOUT", 2, file_values, min_value=1
            ),
            embed_server_idle_seconds=_int_val(
                "NOKORI_EMBED_SERVER_IDLE", 3600, file_values, min_value=60
            ),
            embed_server_auto_start=_bool_val(
                "NOKORI_EMBED_SERVER_AUTO_START", True, file_values
            ),
            hot_cache_enabled=_bool_val("NOKORI_HOT_CACHE", True, file_values),
            session_idle_seconds=_int_val(
                "NOKORI_SESSION_IDLE_SECONDS", 1800, file_values, min_value=60
            ),
            promotion_enabled=_bool_val("NOKORI_PROMOTION_ENABLED", True, file_values),
            strict=_bool_val("NOKORI_STRICT", False, file_values),
            disabled=_bool_val("NOKORI_DISABLED", False, file_values),
            dismiss_phrase=_str_val("NOKORI_DISMISS_PHRASE", "dismiss", file_values),
            log_level=_str_val("NOKORI_LOG_LEVEL", "warn", file_values),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rules.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "active_sessions"

    def _safe_session_id(self, session_id: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)

    def marker_dir(self, session_id: str) -> Path:
        """Per-session gate markers (one file per user prompt_hash)."""
        return self.data_dir / "gate_markers" / self._safe_session_id(session_id)

    def marker_path(self, session_id: str, prompt_hash: str) -> Path:
        return self.marker_dir(session_id) / f"{prompt_hash}.json"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.logs_dir, self.jobs_dir, self.sessions_dir):
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                p.chmod(0o700)
            except OSError:
                pass
        config_path = self.data_dir / _CONFIG_FILE_NAME
        if config_path.exists():
            try:
                config_path.chmod(0o600)
            except OSError:
                pass
