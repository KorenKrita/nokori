import json
from unittest.mock import patch

from nokori.config import Config
from nokori.hooks import session_end
from nokori.utils import sessions
from nokori.utils.host import Host


def test_async_extract_deferred_when_other_sessions_active(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EXTRACT_MODE", "async")
    monkeypatch.setenv("NOKORI_EXTRACT_DEFER_ACTIVE", "1")
    cfg = Config.from_env()
    sessions.register(cfg, "other-session", "proj")

    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"user","message":{"content":"x"}}\n', encoding="utf-8")

    spawned: list[int] = []

    def fake_spawn(c):
        spawned.append(1)

    payload = {
        "session_id": "ending-session",
        "cwd": str(tmp_path),
        "transcript_path": str(transcript),
    }
    with patch.object(session_end, "_spawn_async_extract", fake_spawn):
        session_end.handle(payload, cfg, host=Host.CLAUDE)

    assert spawned == []

    sessions.end(cfg, "other-session")
    with patch.object(session_end, "_spawn_async_extract", fake_spawn):
        session_end.handle(payload, cfg, host=Host.CLAUDE)
    assert spawned == [1]
