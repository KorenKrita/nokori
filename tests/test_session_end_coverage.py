"""Coverage tests for hooks/session_end.py.

Covers: posthoc enqueue, extract job creation, transcript window population,
fork extract attempt, observability event writing, error paths.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.hooks.session_end import (
    _enqueue_extract_job_from_path,
    _extract_session_turns,
    handle,
)
from nokori.utils.host import Host


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EXTRACT_MODE", "manual")
    cfg = Config.from_env()
    cfg.ensure_dirs()
    yield cfg, tmp_path


class TestSessionEndHandle:
    def test_disabled_returns_immediately(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_DISABLED", "1")
        cfg = Config.from_env()
        result = handle({"session_id": "test-sess"}, cfg, host=Host.CLAUDE)
        assert result == {"continue": True}

    def test_basic_session_end_no_transcript(self, session_env):
        cfg, tmp_path = session_env
        payload = {"session_id": "sess-end-1", "cwd": str(tmp_path)}
        with patch("nokori.hooks.session_end.resolve_transcript_path", return_value=None):
            result = handle(payload, cfg, host=Host.CLAUDE)
        assert result == {"continue": True}

    def test_session_end_with_transcript(self, session_env):
        cfg, tmp_path = session_env
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text('{"role":"user","content":"hello"}\n')

        payload = {"session_id": "sess-end-2", "cwd": str(tmp_path)}
        with patch("nokori.hooks.session_end.resolve_transcript_path", return_value=transcript):
            result = handle(payload, cfg, host=Host.CLAUDE)
        assert result == {"continue": True}

    def test_session_end_posthoc_db_open_fails(self, session_env):
        from nokori.errors import DbError

        cfg, tmp_path = session_env
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text('{"role":"user","content":"test"}\n')
        payload = {"session_id": "sess-end-3", "cwd": str(tmp_path)}
        with (
            patch("nokori.hooks.context.open_db", side_effect=DbError("db locked")),
            patch("nokori.hooks.session_end.resolve_transcript_path", return_value=transcript) as mock_rtp,
        ):
            result = handle(payload, cfg, host=Host.CLAUDE)
            assert result == {"continue": True}
            mock_rtp.assert_called_once()


class TestExtractJobEnqueue:
    def test_no_transcript_path_returns_none(self, session_env):
        cfg, _ = session_env
        assert _enqueue_extract_job_from_path(None, {}, cfg) is None

    def test_nonexistent_path_returns_none(self, session_env):
        cfg, tmp_path = session_env
        assert _enqueue_extract_job_from_path(tmp_path / "ghost.jsonl", {}, cfg) is None

    def test_valid_transcript_creates_job(self, session_env):
        cfg, tmp_path = session_env
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text('{"role":"user"}\n')

        result = _enqueue_extract_job_from_path(
            transcript,
            {"cwd": str(tmp_path)},
            cfg,
        )
        assert result is not None
        assert result.exists()
        assert list(cfg.jobs_dir.glob("extract-*.json"))


class TestExtractSessionTurns:
    def test_empty_payload_returns_empty(self):
        assert _extract_session_turns({}) == []

    def test_messages_list_parsed(self):
        payload = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
        }
        turns = _extract_session_turns(payload)
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"

    def test_conversation_key_also_works(self):
        payload = {
            "conversation": [
                {"role": "user", "content": "test", "tool_name": "Bash", "tool_input": "ls"},
            ]
        }
        turns = _extract_session_turns(payload)
        assert len(turns) == 1
        assert turns[0]["tool_name"] == "Bash"

    def test_non_dict_messages_skipped(self):
        payload = {"messages": ["string_entry", {"role": "user", "content": "ok"}]}
        turns = _extract_session_turns(payload)
        assert len(turns) == 1

    def test_non_list_messages_returns_empty(self):
        payload = {"messages": "not a list"}
        assert _extract_session_turns(payload) == []
