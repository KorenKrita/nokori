from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (got {raw!r})")


def _int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        n = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer (got {raw!r})") from e
    if min_value is not None and n < min_value:
        raise ConfigError(f"{name} must be >= {min_value} (got {n})")
    return n


def _str_or_none(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw


def _str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw


def _enum(name: str, default: str, choices: tuple[str, ...]) -> str:
    raw = _str(name, default)
    if raw not in choices:
        raise ConfigError(f"{name} must be one of {choices} (got {raw!r})")
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
    disabled: bool
    dismiss_phrase: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = _expand_path(_str("NOKORI_DATA_DIR", "~/.nokori"))
        return cls(
            data_dir=data_dir,
            max_injection_chars=_int("NOKORI_MAX_INJECTION_CHARS", 1500, min_value=0),
            gate_enabled=_bool("NOKORI_GATE_ENABLED", True),
            gate_ttl_seconds=_int("NOKORI_GATE_TTL_SECONDS", 600, min_value=0),
            gate_matcher=_str(
                "NOKORI_GATE_MATCHER", "Edit|Write|MultiEdit|Bash|NotebookEdit"
            ),
            extract_mode=_enum("NOKORI_EXTRACT_MODE", "manual", ("manual", "async")),
            llm_base_url=_str_or_none("NOKORI_LLM_BASE_URL"),
            llm_model=_str_or_none("NOKORI_LLM_MODEL"),
            llm_api_key=_str_or_none("NOKORI_LLM_API_KEY"),
            embed_enabled=_bool("NOKORI_EMBED_ENABLED", False),
            embed_base_url=_str_or_none("NOKORI_EMBED_BASE_URL"),
            embed_model=_str_or_none("NOKORI_EMBED_MODEL"),
            embed_api_key=_str_or_none("NOKORI_EMBED_API_KEY"),
            embed_dimensions=_int("NOKORI_EMBED_DIMENSIONS", 384, min_value=1),
            embed_chunk_size=_int("NOKORI_EMBED_CHUNK_SIZE", 512, min_value=16),
            embed_chunk_count=_int("NOKORI_EMBED_CHUNK_COUNT", 3, min_value=1),
            disabled=_bool("NOKORI_DISABLED", False),
            dismiss_phrase=_str("NOKORI_DISMISS_PHRASE", "dismiss"),
            log_level=_str("NOKORI_LOG_LEVEL", "warn"),
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

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def marker_path(self, session_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
        return self.data_dir / f"pending-ack-{safe}.marker"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.logs_dir, self.jobs_dir, self.sessions_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)
