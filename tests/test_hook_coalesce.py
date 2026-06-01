"""Hook coalesce when Claude + Cursor both register the same events."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.hooks import dispatch
from nokori.hooks.coalesce import (
    claim_key_for_event,
    coalesce_enabled,
    duplicate_passthrough,
    try_claim,
)
from nokori.utils.host import Host


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_GATE_ENABLED", "0")
    monkeypatch.setenv("NOKORI_EMBED_ENABLED", "0")
    return Config.from_env()


def test_claim_key_user_prompt_submit():
    payload = {
        "session_id": "s1",
        "generation_id": "g1",
        "prompt": "hello",
    }
    key = claim_key_for_event("user-prompt-submit", payload)
    assert key is not None
    assert key.startswith("user-prompt-submit|s1|g1|")


def test_try_claim_exclusive(cfg):
    key = "session-start|sess-a"
    assert try_claim(cfg, key, cli_event="session-start")
    assert not try_claim(cfg, key, cli_event="session-start")


def test_coalesce_disabled_always_wins(cfg, monkeypatch):
    monkeypatch.setenv("NOKORI_HOOK_COALESCE", "0")
    key = "session-start|sess-b"
    assert try_claim(cfg, key)
    assert try_claim(cfg, key)


def test_duplicate_passthrough_user_prompt_submit():
    out = duplicate_passthrough("user-prompt-submit", Host.CURSOR)
    assert "additionalContext" in out or out.get("continue") is not False


def test_dispatch_suppresses_duplicate_session_start(cfg, monkeypatch):
    monkeypatch.setenv("NOKORI_HOOK_COALESCE", "1")
    payload = {"session_id": "dup-sess", "cwd": "/tmp"}
    stdin = json.dumps(payload)
    key = claim_key_for_event("session-start", payload)
    assert key
    try_claim(cfg, key, cli_event="session-start")

    with patch("sys.stdin", io.StringIO(stdin)):
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = dispatch("session-start", cfg)
    assert rc == 0
    body = json.loads(out.getvalue())
    assert body  # passthrough response


def test_describe_dual_hook_registration(monkeypatch):
    from nokori.commands import install

    monkeypatch.setattr(
        install,
        "describe_claude_hooks",
        lambda: {"installed": True},
    )
    monkeypatch.setattr(
        install,
        "describe_cursor_hooks",
        lambda: {"installed": True},
    )
    dual = install.describe_dual_hook_registration()
    assert dual["both_installed"] is True
    assert "coalesce" in dual["note"].lower() or "both" in dual["note"].lower()


def test_coalesce_enabled_default(monkeypatch):
    monkeypatch.delenv("NOKORI_HOOK_COALESCE", raising=False)
    assert coalesce_enabled() is True
    monkeypatch.setenv("NOKORI_HOOK_COALESCE", "off")
    assert coalesce_enabled() is False
