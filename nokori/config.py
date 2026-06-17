from __future__ import annotations

import contextlib
import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config_schema import derive_env_map
from .constants import DEFAULT_GATE_MATCHER
from .errors import ConfigError
from .utils.ids import safe_session_id

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

_CONFIG_FILE_NAME = "config.toml"


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


# --- Config file → flat dict mapping ---
#
# Derived from config_schema.FIELDS (single source of truth). Each FieldDef with
# a non-None `env_name` contributes one (path_tuple → ENV_NAME) entry. Adding a
# new config key means editing FIELDS only; this map follows automatically. The
# golden-snapshot test in tests/test_config_schema_coherence.py guards against
# drift by asserting derive_env_map() equals the previous hand-maintained literal.
_TOML_TO_ENV: dict[tuple[str, ...], str] = derive_env_map()


def _get_nested(doc: dict, path: tuple[str, ...]) -> object:
    """Traverse nested dict by key path. Returns None if any key is missing."""
    cur = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _resolve_file_values(data_dir_hint: str) -> tuple[dict[str, str], dict]:
    """Read config.toml and return (flat {ENV_NAME: value_str} dict, raw TOML dict)."""
    data_dir = Path(data_dir_hint).expanduser().resolve()
    config_path = data_dir / _CONFIG_FILE_NAME
    if not config_path.exists():
        default_dir = Path("~/.nokori").expanduser().resolve()
        if data_dir != default_dir:
            return {}, {}
        config_path = default_dir / _CONFIG_FILE_NAME
    toml = _load_toml(config_path)
    if not toml:
        return {}, {}
    flat: dict[str, str] = {}
    for keys, env_name in _TOML_TO_ENV.items():
        val = _get_nested(toml, keys)
        if val is None:
            continue
        if isinstance(val, bool):
            flat[env_name] = "1" if val else "0"
        else:
            flat[env_name] = str(val)
    return flat, toml


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
    raise ConfigError(
        f"{name} must be a boolean (got {raw!r})",
        remediation=f"Check {name} environment variable or its corresponding key in ~/.nokori/config.toml",
    )


def _int_val(
    name: str, default: int, file_values: dict[str, str], *, min_value: int | None = None
) -> int:
    raw = _get(name, file_values)
    if raw is None or raw.strip() == "":
        return default
    try:
        n = int(raw)
    except ValueError as e:
        raise ConfigError(
            f"{name} must be an integer (got {raw!r})",
            remediation=f"Check {name} environment variable or its corresponding key in ~/.nokori/config.toml",
        ) from e
    if min_value is not None and n < min_value:
        raise ConfigError(
            f"{name} must be >= {min_value} (got {n})",
            remediation=f"Check {name} environment variable or its corresponding key in ~/.nokori/config.toml",
        )
    return n


def _str_or_none_val(name: str, file_values: dict[str, str]) -> str | None:
    raw = _get(name, file_values)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


_LOG_LEVELS = frozenset({"debug", "info", "warning", "warn", "error"})

# --- Per-role model configuration ---

_ROLE_IDS: tuple[str, ...] = (
    "extractor",
    "admission_judge",
    "rule_rewriter",
    "final_judge",
    "merge_planner",
    "synthetic_eval_generator",
    "posthoc_evaluator",
)

_DEFAULT_MAX_TOKENS: dict[str, int] = {
    "extractor": 4000,
    "admission_judge": 2000,
    "rule_rewriter": 4000,
    "final_judge": 2000,
    "merge_planner": 3000,
    "synthetic_eval_generator": 4000,
    "posthoc_evaluator": 3000,
}

_DEFAULT_TIMEOUTS: dict[str, int] = {
    "extractor": 60,
    "admission_judge": 30,
    "rule_rewriter": 60,
    "final_judge": 30,
    "merge_planner": 45,
    "synthetic_eval_generator": 60,
    "posthoc_evaluator": 45,
}


def _resolve_role_models(toml: dict) -> dict[str, str]:
    """Resolve per-role model IDs from [models] section + env overrides."""
    models_section = toml.get("models", {}) if toml else {}
    result: dict[str, str] = {}
    for role in _ROLE_IDS:
        env_name = f"NOKORI_MODEL_{role.upper()}"
        env_val = os.environ.get(env_name)
        if env_val and env_val.strip():
            result[role] = env_val.strip()
        elif isinstance(models_section.get(role), str) and models_section[role].strip():
            result[role] = models_section[role].strip()
    return result


def _resolve_role_max_tokens(toml: dict) -> dict[str, int]:
    """Resolve per-role max_tokens from [models.limits] with defaults."""
    limits = _get_nested(toml, ("models", "limits")) if toml else None
    if not isinstance(limits, dict):
        limits = {}
    result: dict[str, int] = {}
    for role in _ROLE_IDS:
        key = f"{role}_max_tokens"
        val = limits.get(key)
        if isinstance(val, int) and val > 0:
            result[role] = val
        else:
            result[role] = _DEFAULT_MAX_TOKENS[role]
    return result


def _resolve_role_timeouts(toml: dict) -> dict[str, int]:
    """Resolve per-role timeouts from [models.timeouts] with defaults."""
    timeouts = _get_nested(toml, ("models", "timeouts")) if toml else None
    if not isinstance(timeouts, dict):
        timeouts = {}
    result: dict[str, int] = {}
    for role in _ROLE_IDS:
        key = f"{role}_timeout"
        val = timeouts.get(key)
        if isinstance(val, int) and val > 0:
            result[role] = val
        else:
            result[role] = _DEFAULT_TIMEOUTS[role]
    return result


def _log_level_val(name: str, default: str, file_values: dict[str, str]) -> str:
    raw = _get(name, file_values)
    if raw is None:
        return default
    level = raw.strip().lower()
    if level not in _LOG_LEVELS:
        raise ConfigError(f"{name} must be one of debug, info, warn, warning, error (got {raw!r})")
    return "warn" if level == "warning" else level


def _str_val(name: str, default: str, file_values: dict[str, str]) -> str:
    raw = _get(name, file_values)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _enum_val(
    name: str, default: str, choices: tuple[str, ...], file_values: dict[str, str]
) -> str:
    raw = _str_val(name, default, file_values)
    if raw not in choices:
        raise ConfigError(f"{name} must be one of {choices} (got {raw!r})")
    return raw


_GATE_MATCHER_MAX_LEN = 512

_LOCALHOST_HOSTS = frozenset(("localhost", "127.0.0.1", "::1"))


def _config_log() -> logging.Logger:
    return logging.getLogger("nokori.config")


def _validate_url_scheme(url: str | None, field_name: str) -> str | None:
    """Validate URL scheme is http or https.

    Returns None (disables the feature) if scheme is unsupported.
    Warns but allows http on non-localhost hosts.
    """
    if not url:
        return None
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        _config_log().warning(
            "%s: unsupported scheme %r (only http/https allowed); disabling",
            field_name,
            scheme or "<missing>",
        )
        return None
    if scheme == "http" and parsed.hostname and parsed.hostname not in _LOCALHOST_HOSTS:
        _config_log().warning(
            "%s: using plaintext HTTP to %s — API keys may be exposed; consider HTTPS",
            field_name,
            parsed.hostname,
        )
    return url


def _value_explicitly_set(name: str, file_values: dict[str, str]) -> bool:
    """True when env or config.toml provides a non-empty value (matches _int_val)."""
    raw = _get(name, file_values)
    return raw is not None and raw.strip() != ""


def _gate_matcher_val(file_values: dict[str, str]) -> str:
    raw = _str_val("NOKORI_GATE_MATCHER", DEFAULT_GATE_MATCHER, file_values)
    if len(raw) > _GATE_MATCHER_MAX_LEN:
        raise ConfigError(f"NOKORI_GATE_MATCHER exceeds {_GATE_MATCHER_MAX_LEN} characters")
    return raw


def _expand_path(p: str) -> Path:
    return Path(p).expanduser().resolve()


# --- Load-time relation invariants (exclusive_group semantics) ---
#
# Authoritative gate for `exclusive_group` relations. Previously these were
# only checked ad hoc at use sites (embedding.py, merge/policy.py, status.py,
# health.py); now `Config.from_env` fails loud so the user learns the config is
# inconsistent at load time rather than via silent mis-routing.
#
# Scope: `embed_backend` is the only exclusive_group with a relation invariant
# today. For it, "remote intent" is signalled by EITHER embed.base_url OR
# embed.model being set (a user who configures one intends remote). When remote
# is intended AND embed.enabled is true, BOTH embed.base_url and embed.model
# must be present (embed.api_key is optional — some providers don't require it).
# Local-only fields (hook_timeout_seconds, server_idle_seconds, server_auto_start)
# always have values via defaults, so they can't signal variant intent.


def _validate_exclusive_groups(cfg: Config) -> None:
    """Validate `exclusive_group` relation invariants after env+file resolution.

    For `embed_backend`: if `embed.enabled` is true and the remote variant is
    intended (at least one of embed.base_url / embed.model is set), then all
    remote-required fields must be set. Raise ConfigError naming missing field(s).
    """
    if not cfg.embed_enabled:
        return  # embed disabled — no invariant to enforce

    # Detect remote intent: any remote-required field set means user intends remote.
    # The two remote-required fields are base_url and model; if neither is set,
    # the user has not opted into remote (local variant is in effect).
    base_url_set = bool(cfg.embed_base_url)
    model_set = bool(cfg.embed_model)
    remote_intended = base_url_set or model_set
    if not remote_intended:
        return  # local variant — nothing to require

    missing: list[str] = []
    if not base_url_set:
        missing.append("embed.base_url")
    if not model_set:
        missing.append("embed.model")
    if missing:
        joined = ", ".join(missing)
        raise ConfigError(
            f"embed.enabled is true with remote variant intended, "
            f"but missing required field(s): {joined}",
            remediation=(
                "Set both embed.base_url and embed.model in config.toml or via "
                "NOKORI_EMBED_BASE_URL / NOKORI_EMBED_MODEL, "
                "or disable embed (embed.enabled = false) for local-only mode."
            ),
        )


@dataclass(frozen=True)
class Config:
    data_dir: Path
    max_injection_chars: int
    gate_enabled: bool
    gate_ttl_seconds: int
    gate_matcher: str
    extract_mode: str
    extract_defer_when_active: bool
    extract_fork_cache: bool
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
    embed_chunk_size_configured: bool
    embed_chunk_count_configured: bool
    embed_hook_timeout_seconds: int
    embed_server_idle_seconds: int
    embed_server_auto_start: bool
    hot_cache_enabled: bool
    session_idle_seconds: int
    promotion_enabled: bool
    strict: bool
    disabled: bool
    dismiss_phrase: str
    role_models: dict[str, str]
    role_max_tokens: dict[str, int]
    role_timeouts: dict[str, int]
    log_level: str

    @classmethod
    def from_env(cls) -> Config:
        data_dir_raw = os.environ.get("NOKORI_DATA_DIR") or "~/.nokori"
        file_values, raw_toml = _resolve_file_values(data_dir_raw)
        data_dir = _expand_path(_str_val("NOKORI_DATA_DIR", "~/.nokori", file_values))

        cfg = cls(
            data_dir=data_dir,
            max_injection_chars=_int_val(
                "NOKORI_MAX_INJECTION_CHARS", 1500, file_values, min_value=0
            ),
            gate_enabled=_bool_val("NOKORI_GATE_ENABLED", True, file_values),
            gate_ttl_seconds=_int_val("NOKORI_GATE_TTL_SECONDS", 600, file_values, min_value=0),
            gate_matcher=_gate_matcher_val(file_values),
            extract_mode=_enum_val(
                "NOKORI_EXTRACT_MODE", "manual", ("manual", "async"), file_values
            ),
            extract_defer_when_active=_bool_val("NOKORI_EXTRACT_DEFER_ACTIVE", False, file_values),
            extract_fork_cache=_bool_val("NOKORI_EXTRACT_FORK_CACHE", False, file_values),
            llm_base_url=_validate_url_scheme(
                _str_or_none_val("NOKORI_LLM_BASE_URL", file_values), "NOKORI_LLM_BASE_URL"
            ),
            llm_model=_str_or_none_val("NOKORI_LLM_MODEL", file_values),
            llm_api_key=_str_or_none_val("NOKORI_LLM_API_KEY", file_values),
            embed_enabled=_bool_val("NOKORI_EMBED_ENABLED", False, file_values),
            embed_base_url=_validate_url_scheme(
                _str_or_none_val("NOKORI_EMBED_BASE_URL", file_values), "NOKORI_EMBED_BASE_URL"
            ),
            embed_model=_str_or_none_val("NOKORI_EMBED_MODEL", file_values),
            embed_api_key=_str_or_none_val("NOKORI_EMBED_API_KEY", file_values),
            embed_dimensions=_int_val("NOKORI_EMBED_DIMENSIONS", 0, file_values, min_value=0),
            embed_chunk_size=_int_val("NOKORI_EMBED_CHUNK_SIZE", 4000, file_values, min_value=16),
            embed_chunk_count=_int_val("NOKORI_EMBED_CHUNK_COUNT", 2, file_values, min_value=1),
            embed_chunk_size_configured=_value_explicitly_set(
                "NOKORI_EMBED_CHUNK_SIZE", file_values
            ),
            embed_chunk_count_configured=_value_explicitly_set(
                "NOKORI_EMBED_CHUNK_COUNT", file_values
            ),
            embed_hook_timeout_seconds=_int_val(
                "NOKORI_HOOK_EMBED_TIMEOUT", 2, file_values, min_value=1
            ),
            embed_server_idle_seconds=_int_val(
                "NOKORI_EMBED_SERVER_IDLE", 3600, file_values, min_value=60
            ),
            embed_server_auto_start=_bool_val("NOKORI_EMBED_SERVER_AUTO_START", True, file_values),
            hot_cache_enabled=_bool_val("NOKORI_HOT_CACHE", True, file_values),
            session_idle_seconds=_int_val(
                "NOKORI_SESSION_IDLE_SECONDS", 1800, file_values, min_value=60
            ),
            promotion_enabled=_bool_val("NOKORI_PROMOTION_ENABLED", True, file_values),
            strict=_bool_val("NOKORI_STRICT", False, file_values),
            disabled=_bool_val("NOKORI_DISABLED", False, file_values),
            dismiss_phrase=_str_val("NOKORI_DISMISS_PHRASE", "dismiss", file_values),
            role_models=_resolve_role_models(raw_toml),
            role_max_tokens=_resolve_role_max_tokens(raw_toml),
            role_timeouts=_resolve_role_timeouts(raw_toml),
            log_level=_log_level_val("NOKORI_LOG_LEVEL", "warn", file_values),
        )
        _validate_exclusive_groups(cfg)
        return cfg

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
        return safe_session_id(session_id)

    def marker_dir(self, session_id: str) -> Path:
        """Per-session gate markers (one file per user prompt_hash)."""
        return self.data_dir / "gate_markers" / self._safe_session_id(session_id)

    def marker_path(self, session_id: str, prompt_hash: str) -> Path:
        return self.marker_dir(session_id) / f"{prompt_hash}.json"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.logs_dir, self.jobs_dir, self.sessions_dir):
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
            with contextlib.suppress(OSError):
                p.chmod(0o700)
        config_path = self.data_dir / _CONFIG_FILE_NAME
        if config_path.exists():
            with contextlib.suppress(OSError):
                config_path.chmod(0o600)
