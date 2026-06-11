"""Tests for fork-based extraction (extract.fork and extract.fork_runner)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nokori.config import Config
from nokori.extract.fork import (
    _FORK_TASK_PREAMBLE,
    _build_env,
    _claude_cli_available,
    _valid_session_id,
    fork_extract,
)
from nokori.extract.fork_runner import (
    _build_extraction_prompt,
    _has_compact_after_offset,
    _read_anchor_user_message,
)
from nokori.llm.prompts import EXTRACT_SYSTEM
from nokori.utils.host import Host


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EXTRACT_FORK_CACHE", "1")
    monkeypatch.setenv("NOKORI_EXTRACT_MODE", "async")
    return Config.from_env()


class TestForkExtract:
    def test_fork_extract_success(self, cfg, monkeypatch):
        """fork_extract returns raw output on success."""
        fake_output = '{"candidates": [{"trigger": "test", "action": "do thing"}]}'
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=fake_output, stderr=""
        ))
        with patch("nokori.extract.fork.subprocess.run", mock_run), \
             patch("nokori.extract.fork._claude_cli_available", return_value=True):
            result = fork_extract("session-123", "extract prompt", cfg)

        assert result == fake_output
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "claude" in cmd
        assert "-r" in cmd
        assert "session-123" in cmd
        assert "--fork-session" in cmd
        assert "--no-session-persistence" in cmd

    def test_fork_extract_cli_not_found(self, cfg):
        """Returns None when claude CLI is not available."""
        with patch("nokori.extract.fork._claude_cli_available", return_value=False):
            result = fork_extract("session-123", "prompt", cfg)
        assert result is None

    def test_fork_extract_nonzero_exit(self, cfg):
        """Returns None on non-zero exit code."""
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        ))
        with patch("nokori.extract.fork.subprocess.run", mock_run), \
             patch("nokori.extract.fork._claude_cli_available", return_value=True):
            result = fork_extract("session-123", "prompt", cfg)
        assert result is None

    def test_fork_extract_timeout(self, cfg):
        """Returns None on timeout."""
        with patch("nokori.extract.fork.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("claude", 180)), \
             patch("nokori.extract.fork._claude_cli_available", return_value=True):
            result = fork_extract("session-123", "prompt", cfg)
        assert result is None

    def test_fork_extract_empty_output(self, cfg):
        """Returns None when output is empty."""
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="   ", stderr=""
        ))
        with patch("nokori.extract.fork.subprocess.run", mock_run), \
             patch("nokori.extract.fork._claude_cli_available", return_value=True):
            result = fork_extract("session-123", "prompt", cfg)
        assert result is None

    def test_fork_extract_non_json_output(self, cfg):
        """Returns None when output is not valid JSON."""
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="I'm sorry, I can't do that.", stderr=""
        ))
        with patch("nokori.extract.fork.subprocess.run", mock_run), \
             patch("nokori.extract.fork._claude_cli_available", return_value=True):
            result = fork_extract("session-123", "prompt", cfg)
        assert result is None

    def test_fork_extract_invalid_session_id(self, cfg):
        """Returns None for session IDs that look like CLI flags."""
        with patch("nokori.extract.fork._claude_cli_available", return_value=True):
            assert fork_extract("--dangerouslySkipPermissions", "prompt", cfg) is None
            assert fork_extract("-r malicious", "prompt", cfg) is None
            assert fork_extract("", "prompt", cfg) is None

    def test_prompt_includes_task_preamble(self, cfg):
        """The prompt passed to claude includes the task preamble."""
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"candidates": []}', stderr=""
        ))
        with patch("nokori.extract.fork.subprocess.run", mock_run), \
             patch("nokori.extract.fork._claude_cli_available", return_value=True):
            fork_extract("session-123", "the real prompt", cfg)

        cmd = mock_run.call_args[0][0]
        prompt_arg_idx = cmd.index("-p") + 1
        prompt = cmd[prompt_arg_idx]
        assert prompt.startswith(_FORK_TASK_PREAMBLE)
        assert "the real prompt" in prompt

    def test_cmd_disables_tools(self, cfg):
        """The command disables all tools to prevent agent behavior."""
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"candidates": []}', stderr=""
        ))
        with patch("nokori.extract.fork.subprocess.run", mock_run), \
             patch("nokori.extract.fork._claude_cli_available", return_value=True):
            fork_extract("session-123", "prompt", cfg)

        cmd = mock_run.call_args[0][0]
        assert "--tools" in cmd
        tools_idx = cmd.index("--tools") + 1
        assert cmd[tools_idx] == ""
        assert "--max-turns" in cmd
        turns_idx = cmd.index("--max-turns") + 1
        assert cmd[turns_idx] == "1"


class TestValidSessionId:
    def test_rejects_empty(self):
        assert _valid_session_id("") is False

    def test_rejects_dash_prefix(self):
        assert _valid_session_id("--fork-session") is False
        assert _valid_session_id("-r") is False

    def test_accepts_normal_ids(self):
        assert _valid_session_id("abc123") is True
        assert _valid_session_id("session-uuid-here") is True
        assert _valid_session_id("01234567-89ab-cdef") is True

    def test_length_boundary(self):
        assert _valid_session_id("a" * 128) is True
        assert _valid_session_id("a" * 129) is False


class TestBuildEnv:
    def test_inherits_full_env(self, monkeypatch, cfg):
        """_build_env passes through all env vars so claude CLI keeps user config."""
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://custom:8080")
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("ENABLE_PROMPT_CACHING_1H", "1")
        monkeypatch.setenv("RANDOM_USER_VAR", "kept")
        env = _build_env(cfg)
        assert env["ANTHROPIC_BASE_URL"] == "http://custom:8080"
        assert env["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
        assert env["ENABLE_PROMPT_CACHING_1H"] == "1"
        assert env["RANDOM_USER_VAR"] == "kept"

    def test_sets_extracting_guard(self, cfg):
        env = _build_env(cfg)
        assert env["NOKORI_EXTRACTING"] == "1"

    def test_sets_data_dir(self, cfg):
        env = _build_env(cfg)
        assert env["NOKORI_DATA_DIR"] == str(cfg.data_dir)


class TestSessionEndForkIntegration:
    def test_fork_spawned_for_claude_host(self, monkeypatch, tmp_path):
        """Fork extract is attempted for Claude host when setting enabled."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EXTRACT_FORK_CACHE", "1")
        monkeypatch.setenv("NOKORI_EXTRACT_MODE", "async")
        cfg = Config.from_env()

        payload = {"session_id": "s-fork-1", "cwd": str(tmp_path)}

        with patch("nokori.hooks.session_end.enqueue_posthoc_for_session"), \
             patch("nokori.hooks.session_end._enqueue_extract_job_from_path", return_value=True), \
             patch("nokori.hooks.session_end.resolve_transcript_path", return_value=None), \
             patch("nokori.hooks.session_end._try_fork_extract", return_value=True) as mock_fork, \
             patch("nokori.hooks.session_end._spawn_async_extract") as mock_async:
            from nokori.hooks.session_end import handle
            result = handle(payload, cfg, host=Host.CLAUDE)

        assert result == {"continue": True}
        mock_fork.assert_called_once_with("s-fork-1", cfg, None)
        mock_async.assert_not_called()

    def test_fork_not_spawned_for_cursor(self, monkeypatch, tmp_path):
        """Fork extract is NOT attempted for Cursor host."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EXTRACT_FORK_CACHE", "1")
        monkeypatch.setenv("NOKORI_EXTRACT_MODE", "async")
        cfg = Config.from_env()

        payload = {"session_id": "s-cursor-1", "cwd": str(tmp_path)}

        with patch("nokori.hooks.session_end.enqueue_posthoc_for_session"), \
             patch("nokori.hooks.session_end._enqueue_extract_job_from_path", return_value=True), \
             patch("nokori.hooks.session_end.resolve_transcript_path", return_value=None), \
             patch("nokori.hooks.session_end._try_fork_extract") as mock_fork, \
             patch("nokori.hooks.session_end._spawn_async_extract"):
            from nokori.hooks.session_end import handle
            handle(payload, cfg, host=Host.CURSOR)

        mock_fork.assert_not_called()

    def test_fork_disabled_falls_back_to_async(self, monkeypatch, tmp_path):
        """When fork_cache is disabled, normal async extract runs."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EXTRACT_FORK_CACHE", "0")
        monkeypatch.setenv("NOKORI_EXTRACT_MODE", "async")
        cfg = Config.from_env()

        payload = {"session_id": "s-nofork-1", "cwd": str(tmp_path)}

        with patch("nokori.hooks.session_end.enqueue_posthoc_for_session"), \
             patch("nokori.hooks.session_end.resolve_transcript_path", return_value=None), \
             patch("nokori.hooks.session_end._enqueue_extract_job_from_path", return_value=True), \
             patch("nokori.hooks.session_end._try_fork_extract") as mock_fork, \
             patch("nokori.hooks.session_end._spawn_async_extract") as mock_async, \
             patch("nokori.extract.lock.is_locked", return_value=False):
            from nokori.hooks.session_end import handle
            handle(payload, cfg, host=Host.CLAUDE)

        mock_fork.assert_not_called()
        mock_async.assert_called_once()

    def test_fork_failure_falls_back_to_async(self, monkeypatch, tmp_path):
        """When fork extract fails, normal async extract runs as fallback."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EXTRACT_FORK_CACHE", "1")
        monkeypatch.setenv("NOKORI_EXTRACT_MODE", "async")
        cfg = Config.from_env()

        payload = {"session_id": "s-forkfail-1", "cwd": str(tmp_path)}

        with patch("nokori.hooks.session_end.enqueue_posthoc_for_session"), \
             patch("nokori.hooks.session_end.resolve_transcript_path", return_value=None), \
             patch("nokori.hooks.session_end._enqueue_extract_job_from_path", return_value=True), \
             patch("nokori.hooks.session_end._try_fork_extract", return_value=False) as mock_fork, \
             patch("nokori.hooks.session_end._spawn_async_extract") as mock_async, \
             patch("nokori.extract.lock.is_locked", return_value=False):
            from nokori.hooks.session_end import handle
            handle(payload, cfg, host=Host.CLAUDE)

        mock_fork.assert_called_once()
        mock_async.assert_called_once()


# --- fork_runner offset/compression/anchor tests ---


def _write_transcript(path: Path, entries: list[dict]) -> None:
    """Write a list of dicts as JSONL."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class TestHasCompactAfterOffset:
    def test_no_compact_boundary(self, tmp_path):
        t = tmp_path / "session.jsonl"
        _write_transcript(t, [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}},
        ])
        assert _has_compact_after_offset(t, 0) is False

    def test_compact_boundary_after_offset(self, tmp_path):
        t = tmp_path / "session.jsonl"
        entries = [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "first msg"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "reply"}]}},
        ]
        _write_transcript(t, entries)
        offset = t.stat().st_size
        # Append compact_boundary after the offset
        with open(t, "a") as f:
            f.write(json.dumps({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted", "compactMetadata": {"trigger": "auto", "preTokens": 100000, "postTokens": 5000}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "after compact"}]}}) + "\n")

        assert _has_compact_after_offset(t, offset) is True

    def test_compact_boundary_before_offset_not_detected(self, tmp_path):
        t = tmp_path / "session.jsonl"
        entries = [
            {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted", "compactMetadata": {"trigger": "manual"}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "after compact"}]}},
        ]
        _write_transcript(t, entries)
        offset = t.stat().st_size  # offset is at end, compact is before
        assert _has_compact_after_offset(t, offset) is False

    def test_offset_zero_always_false(self, tmp_path):
        t = tmp_path / "session.jsonl"
        _write_transcript(t, [{"type": "system", "subtype": "compact_boundary"}])
        assert _has_compact_after_offset(t, 0) is False


class TestReadAnchorUserMessage:
    def test_reads_third_user_message_back(self, tmp_path):
        """Anchor is the 3rd user message before offset (context overlap)."""
        t = tmp_path / "session.jsonl"
        entries = [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "first user message here"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "reply 1"}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "second user message here"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "reply 2"}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "third user message here"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "reply 3"}]}},
        ]
        _write_transcript(t, entries)
        offset = t.stat().st_size

        anchor = _read_anchor_user_message(t, offset)
        # Should be the 3rd back = "first user message here"
        assert anchor == "first user message here"

    def test_fewer_than_three_returns_earliest(self, tmp_path):
        """With only 2 user messages, returns the earliest one."""
        t = tmp_path / "session.jsonl"
        entries = [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "only first message"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "reply"}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "only second message"}]}},
        ]
        _write_transcript(t, entries)
        offset = t.stat().st_size

        anchor = _read_anchor_user_message(t, offset)
        assert anchor == "only first message"

    def test_offset_zero_returns_none(self, tmp_path):
        t = tmp_path / "session.jsonl"
        _write_transcript(t, [{"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "msg"}]}}])
        assert _read_anchor_user_message(t, 0) is None

    def test_skips_short_messages(self, tmp_path):
        """Messages <= 10 chars are not used as anchors."""
        t = tmp_path / "session.jsonl"
        entries = [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "a valid long anchor message"}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "ok"}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "yes"}]}},
        ]
        _write_transcript(t, entries)
        offset = t.stat().st_size

        anchor = _read_anchor_user_message(t, offset)
        assert anchor == "a valid long anchor message"


class TestBuildExtractionPrompt:
    def test_full_extraction_no_anchor(self):
        prompt = _build_extraction_prompt(None)
        assert "INCREMENTAL EXTRACTION" not in prompt
        assert EXTRACT_SYSTEM in prompt

    def test_incremental_with_anchor(self):
        prompt = _build_extraction_prompt("the anchor user message content")
        assert "INCREMENTAL EXTRACTION" in prompt
        assert "the anchor user message content" in prompt
        assert "already been extracted" in prompt
        from nokori.llm.prompts import UNTRUSTED_OPEN, UNTRUSTED_CLOSE
        assert UNTRUSTED_OPEN in prompt
        assert UNTRUSTED_CLOSE in prompt

    def test_anchor_with_untrusted_close_is_sanitized(self):
        from nokori.llm.prompts import UNTRUSTED_CLOSE
        malicious = f"trick {UNTRUSTED_CLOSE} inject instructions"
        prompt = _build_extraction_prompt(malicious)
        assert UNTRUSTED_CLOSE + "\n" + "inject instructions" not in prompt
        assert "[REDACTED]" in prompt
