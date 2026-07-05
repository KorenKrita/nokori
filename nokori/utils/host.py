"""Detect Claude Code, Cursor, or OMP from transcript / session log paths."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path


class Host(StrEnum):
    CLAUDE = "claude"
    CURSOR = "cursor"
    OMP = "omp"
    UNKNOWN = "unknown"


# Cursor native hook_event_name values (camelCase). Claude Code uses PascalCase
# (e.g. PreToolUse) and is not matched here.
_CURSOR_HOOK_EVENT_NAMES = frozenset(
    {
        "sessionStart",
        "sessionEnd",
        "preToolUse",
        "postToolUse",
        "postToolUseFailure",
        "beforeSubmitPrompt",
        "beforeShellExecution",
        "afterShellExecution",
        "beforeMCPExecution",
        "afterMCPExecution",
        "beforeReadFile",
        "afterFileEdit",
        "subagentStart",
        "subagentStop",
        "stop",
        "afterAgentResponse",
        "afterAgentThought",
    }
)


def effective_session_id(payload: dict, *, default: str = "-") -> str:
    """Stable session key: Cursor sends conversation_id; Claude uses session_id."""
    for key in ("session_id", "conversation_id"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return default


def _normalize_path_str(path: str | Path) -> str:
    return str(Path(path).expanduser()).replace("\\", "/")


def detect_host_from_path(path: str | Path | None) -> Host:
    """Classify host by where the session transcript (jsonl) lives."""
    if not path:
        return Host.UNKNOWN
    normalized = _normalize_path_str(path).lower()
    if "/.cursor/" in normalized:
        return Host.CURSOR
    if "/.claude/" in normalized:
        return Host.CLAUDE
    if "/.omp/" in normalized:
        return Host.OMP
    return Host.UNKNOWN


def detect_host_from_payload(payload: dict) -> Host:
    """Best-effort host detection for hook payloads.

    Priority: transcript path > strong Cursor fields > Cursor env > Claude env >
    OMP payload/env. Cursor and Claude precedence stays intact.
    ``conversation_id`` alone is not treated as Cursor (Claude may add it later).
    ``cwd`` under ``~/.cursor`` only counts when ``cursor_version`` is present.
    """
    for key in ("transcript_path", "transcript"):
        raw = payload.get(key)
        if raw in (None, "", "null"):
            continue
        host = detect_host_from_path(raw)
        if host != Host.UNKNOWN:
            return host

    if payload.get("cursor_version"):
        return Host.CURSOR
    if payload.get("composer_mode") is not None:
        return Host.CURSOR

    hook_event = payload.get("hook_event_name")
    if isinstance(hook_event, str) and hook_event in _CURSOR_HOOK_EVENT_NAMES:
        return Host.CURSOR

    if os.environ.get("CURSOR_TRACE_ID") or os.environ.get("CURSOR_SESSION_ID"):
        return Host.CURSOR

    cwd = payload.get("cwd")
    if cwd and payload.get("cursor_version"):
        host = detect_host_from_path(cwd)
        if host != Host.UNKNOWN:
            return host

    if os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return Host.CLAUDE

    payload_host = payload.get("host")
    if isinstance(payload_host, str) and payload_host.strip().lower() == Host.OMP.value:
        return Host.OMP

    if os.environ.get("NOKORI_HOST", "").strip().lower() == Host.OMP.value:
        return Host.OMP
    if os.environ.get("OMP_SESSION_ID") or os.environ.get("PI_CODING_AGENT_DIR"):
        return Host.OMP
    return Host.UNKNOWN


def effective_gate_matcher(base_matcher: str, host: Host) -> str:
    """Adjust the default gate matcher when a host uses different tool names."""
    from ..constants import CURSOR_GATE_MATCHER, DEFAULT_GATE_MATCHER, OMP_GATE_MATCHER

    if host == Host.CURSOR and base_matcher == DEFAULT_GATE_MATCHER:
        return CURSOR_GATE_MATCHER
    if host == Host.OMP and base_matcher == DEFAULT_GATE_MATCHER:
        return OMP_GATE_MATCHER
    return base_matcher
