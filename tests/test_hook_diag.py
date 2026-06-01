"""Hook diagnostic logging helpers."""
from __future__ import annotations

import logging

from nokori.utils.hook_diag import hook_diag_enabled, log_hook_enter, payload_summary
from nokori.utils.host import Host


def test_payload_summary_includes_tool_name():
    text = payload_summary({
        "session_id": "abc",
        "tool_name": "Shell",
        "tool_input": {"command": "ls"},
        "cwd": "/tmp",
    })
    assert "Shell" in text
    assert "session_id" in text


def test_hook_diag_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NOKORI_HOOK_DEBUG", raising=False)
    monkeypatch.setenv("NOKORI_LOG_LEVEL", "warn")
    assert not hook_diag_enabled()


def test_hook_diag_enabled_with_debug_env(monkeypatch):
    monkeypatch.setenv("NOKORI_HOOK_DEBUG", "1")
    assert hook_diag_enabled()


def test_hook_diag_host_cursor_path(monkeypatch):
    monkeypatch.setenv("NOKORI_HOOK_DEBUG", "1")
    log = logging.getLogger("test.hook_diag")
    records: list[str] = []

    class H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    log.addHandler(H())
    log.setLevel(logging.DEBUG)
    host = log_hook_enter(
        log,
        cli_event="pre-tool-use",
        payload={"transcript_path": "/Users/x/.cursor/projects/p/t.jsonl", "tool_name": "Shell"},
        raw_stdin_len=100,
        host=Host.CURSOR,
    )
    assert host is Host.CURSOR
    assert records and "tool_name=Shell" in records[0]
