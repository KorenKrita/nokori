"""prompt_submit_ack files and cursor deferred preToolUse branching."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from nokori.config import Config
from nokori.gate import prompt_ack
from nokori.gate.marker import prompt_hash
from nokori.hooks.cursor_deferred import maybe_deferred_pre_tool_use
from nokori.utils.host import Host
from nokori.utils.prompt_text import normalize_prompt_for_hash


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_GATE_ENABLED", "0")
    monkeypatch.setenv("NOKORI_EMBED_ENABLED", "0")
    return Config.from_env()


def test_normalize_user_query_wrapper():
    raw = "<user_query>\n你好\n</user_query>"
    assert normalize_prompt_for_hash(raw) == "你好"


def test_prompt_ack_record_and_exists(cfg):
    ph = prompt_hash("hello")
    assert not prompt_ack.exists(cfg, "sess-1", ph)
    prompt_ack.record(cfg, "sess-1", ph)
    assert prompt_ack.exists(cfg, "sess-1", ph)


def test_deferred_skips_when_submit_ack_present(cfg, tmp_path, monkeypatch):
    cursor_root = tmp_path / ".cursor" / "projects" / "p" / "agent-transcripts"
    cursor_root.mkdir(parents=True)
    transcript = cursor_root / "sess.jsonl"
    transcript.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"'
        '<user_query>upgrade prisma</user_query>"}]}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "nokori.utils.transcript._allowed_roots",
        lambda: [tmp_path / ".cursor"],
    )
    prompt = normalize_prompt_for_hash("upgrade prisma")
    ph = prompt_hash(prompt)
    prompt_ack.record(cfg, "sess", ph)

    out = maybe_deferred_pre_tool_use(
        {
            "hook_event_name": "preToolUse",
            "cursor_version": "1.0",
            "session_id": "sess",
            "generation_id": "gen-1",
            "transcript_path": str(transcript),
            "tool_name": "Shell",
        },
        cfg,
        "sess",
        "Shell",
        Host.CURSOR,
    )
    assert out is None


def test_deferred_marks_generation_without_rules(cfg, tmp_path, monkeypatch):
    cursor_root = tmp_path / ".cursor" / "projects" / "p" / "agent-transcripts"
    cursor_root.mkdir(parents=True)
    transcript = cursor_root / "sess.jsonl"
    transcript.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"unique noop"}]}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "nokori.utils.transcript._allowed_roots",
        lambda: [tmp_path / ".cursor"],
    )

    out = maybe_deferred_pre_tool_use(
        {
            "hook_event_name": "preToolUse",
            "cursor_version": "1.0",
            "session_id": "sess",
            "generation_id": "gen-2",
            "transcript_path": str(transcript),
            "tool_name": "Grep",
        },
        cfg,
        "sess",
        "Grep",
        Host.CURSOR,
    )
    assert out is None
    ph = prompt_hash("unique noop")
    done = cfg.data_dir / "cursor_deferred" / "sess" / f"gen-2_{ph}.json"
    assert done.is_file()
    meta = json.loads(done.read_text(encoding="utf-8"))
    assert meta["generation_id"] == "gen-2"
    assert meta["prompt_hash"] == ph


def test_deferred_done_keyed_by_generation_and_prompt_hash(cfg, tmp_path, monkeypatch):
    """Same generation_id + different prompts, or same prompt + different generations."""
    cursor_root = tmp_path / ".cursor" / "projects" / "p" / "agent-transcripts"
    cursor_root.mkdir(parents=True)
    transcript = cursor_root / "sess.jsonl"
    monkeypatch.setattr(
        "nokori.utils.transcript._allowed_roots",
        lambda: [tmp_path / ".cursor"],
    )

    def run_once(prompt_text: str, generation_id: str) -> None:
        transcript.write_text(
            '{"role":"user","message":{"content":[{"type":"text","text":"'
            + prompt_text.replace("\\", "\\\\").replace('"', '\\"')
            + '"}]}}\n',
            encoding="utf-8",
        )
        maybe_deferred_pre_tool_use(
            {
                "hook_event_name": "preToolUse",
                "cursor_version": "1.0",
                "session_id": "sess",
                "generation_id": generation_id,
                "transcript_path": str(transcript),
                "tool_name": "Grep",
            },
            cfg,
            "sess",
            "Grep",
            Host.CURSOR,
        )

    run_once("first prompt", "gen-shared")
    ph_a = prompt_hash("first prompt")
    assert (cfg.data_dir / "cursor_deferred" / "sess" / f"gen-shared_{ph_a}.json").is_file()

    run_once("first prompt", "gen-shared")
    assert maybe_deferred_pre_tool_use(
        {
            "hook_event_name": "preToolUse",
            "cursor_version": "1.0",
            "session_id": "sess",
            "generation_id": "gen-shared",
            "transcript_path": str(transcript),
            "tool_name": "Grep",
        },
        cfg,
        "sess",
        "Grep",
        Host.CURSOR,
    ) is None

    run_once("second prompt", "gen-shared")
    ph_b = prompt_hash("second prompt")
    assert (cfg.data_dir / "cursor_deferred" / "sess" / f"gen-shared_{ph_b}.json").is_file()

    run_once("first prompt", "gen-other")
    assert (cfg.data_dir / "cursor_deferred" / "sess" / f"gen-other_{ph_a}.json").is_file()


def test_deferred_without_generation_id_uses_prompt_hash_only(cfg, tmp_path, monkeypatch):
    """No generation_id: second preToolUse for same prompt must not re-deny."""
    cursor_root = tmp_path / ".cursor" / "projects" / "p" / "agent-transcripts"
    cursor_root.mkdir(parents=True)
    transcript = cursor_root / "sess.jsonl"
    transcript.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"same turn"}]}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "nokori.utils.transcript._allowed_roots",
        lambda: [tmp_path / ".cursor"],
    )
    ph = prompt_hash("same turn")
    base_payload = {
        "hook_event_name": "preToolUse",
        "cursor_version": "1.0",
        "session_id": "sess",
        "transcript_path": str(transcript),
        "tool_name": "Grep",
    }

    maybe_deferred_pre_tool_use(
        base_payload, cfg, "sess", "Grep", Host.CURSOR
    )
    assert prompt_ack.deferred_done(cfg, "sess", "", ph)

    second = maybe_deferred_pre_tool_use(
        base_payload, cfg, "sess", "Grep", Host.CURSOR
    )
    assert second is None
    assert prompt_ack.deferred_done(cfg, "sess", "", ph)


def test_deferred_done_without_generation_id(cfg):
    ph = prompt_hash("hello")
    assert not prompt_ack.deferred_done(cfg, "sess", "", ph)
    prompt_ack.mark_deferred_done(cfg, "sess", "", prompt_hash=ph)
    assert prompt_ack.deferred_done(cfg, "sess", "", ph)
    safe_ph = cfg._safe_session_id(ph)
    assert (cfg.data_dir / "cursor_deferred" / "sess" / f"{safe_ph}.json").is_file()


def test_cleanup_session_removes_ack_and_deferred(cfg):
    ph = prompt_hash("hello")
    prompt_ack.record(cfg, "sess-1", ph)
    prompt_ack.mark_deferred_done(cfg, "sess-1", "gen", prompt_hash=ph)
    assert prompt_ack.cleanup_session(cfg, "sess-1") >= 2
    assert not prompt_ack.exists(cfg, "sess-1", ph)
    assert not prompt_ack.deferred_done(cfg, "sess-1", "gen", ph)


def test_prune_stale_drops_old_ack_files(cfg):
    ph = prompt_hash("old")
    prompt_ack.record(cfg, "sess-old", ph)
    safe_sess = cfg._safe_session_id("sess-old")
    path = cfg.data_dir / "prompt_submit_ack" / safe_sess / f"{ph}.json"
    old = (datetime.now(UTC) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    path.write_text(
        json.dumps({"session_id": "sess-old", "prompt_hash": ph, "recorded_at": old}),
        encoding="utf-8",
    )
    assert prompt_ack.prune_stale(cfg, max_age_hours=24) >= 1
    assert not path.is_file()
