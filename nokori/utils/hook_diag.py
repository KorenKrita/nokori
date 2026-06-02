"""Structured hook diagnostics for Claude Code vs Cursor (written to hook.log)."""
from __future__ import annotations

import json
import os
from typing import Any

from .host import Host, effective_session_id

_MAX_FIELD = 400
_MAX_PROMPT = 120
_TRUE = frozenset({"1", "true", "yes", "on"})
# Safe without locking: hooks run as single-threaded subprocesses
_config_log_level: str | None = None


def set_diag_from_config(log_level: str) -> None:
    """Called at hook dispatch so [diag] can follow config.toml log_level."""
    global _config_log_level
    _config_log_level = (log_level or "").strip().lower() or None


def hook_diag_enabled() -> bool:
    """Emit [diag] lines only when explicitly debugging hooks."""
    if os.environ.get("NOKORI_HOOK_DEBUG", "").strip().lower() in _TRUE:
        return True
    level = os.environ.get("NOKORI_LOG_LEVEL", "").strip().lower()
    if not level and _config_log_level:
        level = _config_log_level
    if not level:
        level = "warn"
    return level == "debug"


def log_diag(log, msg: str, *args: Any, **kwargs: Any) -> None:
    if hook_diag_enabled():
        log.debug(msg, *args, **kwargs)


def _trunc(value: Any, limit: int = _MAX_FIELD) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    if len(text) > limit:
        return text[:limit] + f"...(+{len(text) - limit} chars)"
    return text


def _tool_fields(payload: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in (
        "tool_name",
        "tool",
        "tool_use_id",
        "tool_input",
        "input",
        "arguments",
        "command",
    ):
        if key in payload and payload[key] is not None:
            out[key] = _trunc(payload[key])
    return out


def _env_hints() -> str:
    parts: list[str] = []
    for name in (
        "CURSOR_TRACE_ID",
        "CURSOR_SESSION_ID",
        "CLAUDE_CODE_ENTRYPOINT",
        "NOKORI_DISABLED",
        "NOKORI_LOG_LEVEL",
        "NOKORI_HOOK_DEBUG",
    ):
        val = os.environ.get(name)
        if val:
            parts.append(f"{name}={_trunc(val, 80)}")
        else:
            parts.append(f"{name}=-")
    return " ".join(parts)


def payload_summary(payload: dict) -> str:
    """Single-line JSON-safe summary of hook stdin (no full prompt bodies)."""
    if not payload:
        return "{}"
    slim: dict[str, Any] = {}
    for key in sorted(payload.keys()):
        val = payload[key]
        if key in ("prompt", "user_message", "message", "content"):
            slim[key] = _trunc(val, _MAX_PROMPT)
        elif key in ("tool_input", "input", "arguments"):
            slim[key] = _trunc(val)
        elif isinstance(val, (dict, list)) and len(repr(val)) > _MAX_FIELD:
            slim[key] = _trunc(val)
        else:
            slim[key] = val
    return _trunc(slim, 2000)


def log_hook_enter(
    log,
    *,
    cli_event: str,
    payload: dict,
    raw_stdin_len: int,
    host: Host,
) -> None:
    if hook_diag_enabled():
        tools = _tool_fields(payload)
        log.debug(
            "[diag] hook_enter cli_event=%s host=%s stdin_bytes=%d session_id=%s "
            "cwd=%s transcript_path=%s tool_name=%s tool_fields=%s payload_keys=%s "
            "env=%s payload=%s",
            cli_event,
            host.value,
            raw_stdin_len,
            _trunc(effective_session_id(payload), 64),
            _trunc(payload.get("cwd"), 200) or "-",
            _trunc(payload.get("transcript_path") or payload.get("transcript"), 240) or "-",
            _trunc(payload.get("tool_name") or payload.get("tool"), 64) or "-",
            json.dumps(tools, ensure_ascii=False) if tools else "{}",
            ",".join(sorted(payload.keys())) if payload else "-",
            _env_hints(),
            payload_summary(payload),
        )


def log_hook_exit(log, *, cli_event: str, host: Host, response: dict) -> None:
    if not hook_diag_enabled():
        return
    top_keys = ",".join(sorted(response.keys())) if response else "-"
    hso = response.get("hookSpecificOutput") if isinstance(response, dict) else None
    deny = str(response.get("permission") or "") if isinstance(response, dict) else ""
    if not deny and isinstance(hso, dict):
        deny = str(hso.get("permissionDecision") or hso.get("permission") or "")
    log.debug(
        "[diag] hook_exit cli_event=%s host=%s response_keys=%s deny=%s "
        "response=%s",
        cli_event,
        host.value,
        top_keys,
        deny or "-",
        _trunc(response, 800),
    )
