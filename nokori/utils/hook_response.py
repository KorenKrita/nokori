"""Format hook stdout JSON for Claude Code vs Cursor (per Cursor docs compatibility)."""

from __future__ import annotations

from .host import Host


def session_start_response(host: Host, injection_text: str | None) -> dict:
    if not injection_text:
        return {"continue": True}
    if host == Host.CURSOR:
        return {"continue": True, "additional_context": injection_text}
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": injection_text,
        },
    }


def user_prompt_submit_response(host: Host, injection_text: str | None) -> dict:
    if not injection_text:
        return {"continue": True}
    if host == Host.CURSOR:
        # Official beforeSubmitPrompt output: continue + user_message only.
        # additional_context is not documented here; best-effort for forward compatibility.
        return {"continue": True, "additional_context": injection_text}
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": injection_text,
        },
    }


def pre_tool_deny_response(
    host: Host,
    reason: str,
    *,
    user_message: str | None = None,
    agent_message: str | None = None,
) -> dict:
    if host == Host.CURSOR:
        agent = agent_message if agent_message is not None else reason
        user = user_message if user_message is not None else reason
        out: dict = {"permission": "deny", "agent_message": agent}
        if user:
            out["user_message"] = user
        return out
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }
