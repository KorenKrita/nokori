"""Cursor host detection, transcript roots, gate matcher, JSONL reader."""
from __future__ import annotations

from pathlib import Path

from nokori.config import Config
from nokori.constants import CURSOR_GATE_MATCHER, DEFAULT_GATE_MATCHER
from nokori.extract.reader import read
from nokori.hooks.pre_tool_use import _tool_matches_gate
from nokori.gate import prompt_ack
from nokori.utils.hook_response import (
    pre_tool_deny_response,
    session_start_response,
    user_prompt_submit_response,
)
from nokori.utils.transcript import (
    resolve_transcript_path,
    transcript_resolve_failure_reason,
)
from nokori.utils.host import (
    Host,
    detect_host_from_path,
    detect_host_from_payload,
    effective_gate_matcher,
    effective_session_id,
)
from nokori.utils.transcript import is_path_allowed, resolve_transcript_path


def test_detect_host_from_cursor_transcript_path():
    p = Path.home() / ".cursor/projects/foo/agent-transcripts/s.jsonl"
    assert detect_host_from_path(p) == Host.CURSOR


def test_detect_host_from_claude_transcript_path():
    p = Path.home() / ".claude/projects/foo/s.jsonl"
    assert detect_host_from_path(p) == Host.CLAUDE


def test_detect_host_from_payload_transcript_path():
    payload = {
        "transcript_path": str(
            Path.home() / ".cursor/projects/x/agent-transcripts/t.jsonl"
        ),
    }
    assert detect_host_from_payload(payload) == Host.CURSOR


def test_effective_gate_matcher_cursor_default():
    assert effective_gate_matcher(DEFAULT_GATE_MATCHER, Host.CURSOR) == CURSOR_GATE_MATCHER
    assert (
        effective_gate_matcher("Shell|Write", Host.CURSOR) == "Shell|Write"
    )


def test_cursor_transcript_under_allowed_root(tmp_path, monkeypatch):
    cursor_root = tmp_path / ".cursor" / "projects" / "p" / "agent-transcripts"
    cursor_root.mkdir(parents=True)
    transcript = cursor_root / "sess.jsonl"
    transcript.write_text('{"role":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n')
    monkeypatch.setattr(
        "nokori.utils.transcript._allowed_roots",
        lambda: [tmp_path / ".cursor"],
    )
    assert is_path_allowed(transcript)
    resolved = resolve_transcript_path({"transcript_path": str(transcript)})
    assert resolved == transcript


def test_reader_cursor_nested_content(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        '{"role":"assistant","message":{"content":['
        '{"type":"text","text":"thinking"},'
        '{"type":"tool_use","name":"Shell","input":{"command":"ls"}}'
        "]}}\n",
        encoding="utf-8",
    )
    turns = read(transcript)
    assert len(turns) == 2
    assert turns[0].role == "assistant"
    assert "thinking" in turns[0].content
    assert turns[1].role == "tool_use"
    assert turns[1].tool_name == "Shell"


def test_detect_host_cursor_version_and_hook_event():
    assert detect_host_from_payload({"cursor_version": "2.0"}) == Host.CURSOR
    assert detect_host_from_payload({"hook_event_name": "sessionStart"}) == Host.CURSOR
    assert detect_host_from_payload({"composer_mode": "agent"}) == Host.CURSOR


def test_effective_session_id_prefers_conversation_id():
    assert effective_session_id({"conversation_id": "conv-1"}) == "conv-1"
    assert effective_session_id({"session_id": "sess-1"}) == "sess-1"
    assert effective_session_id(
        {"session_id": "sess-1", "conversation_id": "conv-1"},
    ) == "sess-1"


def test_resolve_transcript_from_cursor_env(tmp_path, monkeypatch):
    cursor_root = tmp_path / ".cursor" / "projects" / "p" / "agent-transcripts"
    cursor_root.mkdir(parents=True)
    transcript = cursor_root / "sess.jsonl"
    transcript.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"hello"}]}}\n'
    )
    monkeypatch.setenv("CURSOR_TRANSCRIPT_PATH", str(transcript))
    monkeypatch.setattr(
        "nokori.utils.transcript._allowed_roots",
        lambda: [tmp_path / ".cursor"],
    )
    assert resolve_transcript_path({}) == transcript
    monkeypatch.delenv("CURSOR_TRANSCRIPT_PATH", raising=False)
    assert "unset" in transcript_resolve_failure_reason({})


def test_hook_response_shapes():
    inj = "[nokori] rule text"
    assert session_start_response(Host.CURSOR, inj) == {
        "continue": True,
        "additional_context": inj,
    }
    assert user_prompt_submit_response(Host.CURSOR, inj) == {
        "continue": True,
        "additional_context": inj,
    }
    deny = pre_tool_deny_response(
        Host.CURSOR, "blocked", user_message="for user", agent_message="for agent"
    )
    assert deny == {
        "permission": "deny",
        "user_message": "for user",
        "agent_message": "for agent",
    }
    claude_deny = pre_tool_deny_response(Host.CLAUDE, "blocked")
    assert claude_deny["continue"] is True
    assert claude_deny["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_host_transcript_beats_conversation_id():
    claude_path = str(Path.home() / ".claude/projects/p/s.jsonl")
    assert (
        detect_host_from_payload(
            {
                "transcript_path": claude_path,
                "conversation_id": "conv-cursor-shaped",
                "cursor_version": "9.9",
            }
        )
        == Host.CLAUDE
    )


def test_conversation_id_alone_is_not_cursor():
    assert detect_host_from_payload({"conversation_id": "conv-only"}) == Host.UNKNOWN


def test_claude_pascalcase_hook_event_not_cursor():
    assert (
        detect_host_from_payload(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess",
                "tool_name": "Bash",
            }
        )
        == Host.UNKNOWN
    )


def test_reader_tool_result_and_multi_text(tmp_path):
    transcript = tmp_path / "blocks.jsonl"
    transcript.write_text(
        '{"role":"user","message":{"content":['
        '{"type":"text","text":"line one"},'
        '{"type":"text","text":"line two"},'
        '{"type":"tool_result","content":"ok","is_error":false},'
        '{"type":"tool_result","content":"boom","is_error":true}'
        "]}}\n",
        encoding="utf-8",
    )
    turns = read(transcript)
    assert len(turns) == 3
    assert turns[0].role == "human"
    assert turns[0].content == "line one\nline two"
    assert turns[1].role == "tool_result"
    assert turns[1].content == "ok"
    assert turns[2].role == "tool_result"
    assert turns[2].is_error is True
    assert turns[2].error_line == "boom"


def test_reader_skips_empty_text_blocks(tmp_path):
    transcript = tmp_path / "empty.jsonl"
    transcript.write_text(
        '{"role":"assistant","message":{"content":['
        '{"type":"text","text":""},'
        '{"type":"tool_use","name":"Read","input":{}}'
        "]}}\n",
        encoding="utf-8",
    )
    turns = read(transcript)
    assert len(turns) == 1
    assert turns[0].role == "tool_use"


def test_try_claim_deferred_exclusive(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    from concurrent.futures import ThreadPoolExecutor

    wins = 0

    def _claim() -> None:
        nonlocal wins
        if prompt_ack.try_claim_deferred(cfg, "sess", "gen-1", "abc123"):
            wins += 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: _claim(), range(16)))
    assert wins == 1
    assert prompt_ack.deferred_done(cfg, "sess", "gen-1", "abc123")


def test_gate_shell_matches_when_cursor_transcript_in_payload():
    host = detect_host_from_payload(
        {
            "transcript_path": str(
                Path.home() / ".cursor/projects/p/agent-transcripts/x.jsonl"
            ),
        }
    )
    matcher = effective_gate_matcher(DEFAULT_GATE_MATCHER, host)
    assert _tool_matches_gate("Shell", matcher)
    assert not _tool_matches_gate("Shell", DEFAULT_GATE_MATCHER)
