import json
from pathlib import Path

import pytest

from nokori.config import Config
from nokori.extract.compressor import compress
from nokori.extract.extractor import _parse_candidates, extract
from nokori.extract.reader import read as read_transcript
from nokori.models import Turn


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, prompt, *, max_tokens=2000, timeout=30):
        self.calls += 1
        return self.response

    def complete_messages(self, system, user, *, max_tokens=2000, timeout=30):
        self.calls += 1
        return self.response


def test_extractor_parses_array(tmp_path):
    response = json.dumps([
        {
            "trigger": "Force push to a shared branch",
            "trigger_variants": ["git push --force"],
            "search_terms": {"en": ["force", "push"], "zh": ["强推"]},
            "behavior": "git push --force",
            "action": "use --force-with-lease",
            "rationale": "force push overwrites peers' work",
            "source_type": "correction",
            "confidence": "high",
        }
    ])
    cands, ok = extract("[User] dummy transcript\n", FakeLLM(response))
    assert ok and len(cands) == 1
    c = cands[0]
    assert c.confidence == "high"
    assert c.source_type == "correction"
    assert "git push --force" in c.trigger_variants
    assert c.search_terms["zh"] == ["强推"]


def test_extractor_parses_single_object():
    response = json.dumps({
        "trigger": "x", "action": "y",
        "source_type": "correction", "confidence": "high",
    })
    cands, ok = extract("nonempty", FakeLLM(response))
    assert ok and len(cands) == 1


def test_extractor_handles_fenced_json():
    response = "```json\n[]\n```"
    cands, ok = extract("nonempty", FakeLLM(response))
    assert ok and cands == []


def test_extractor_non_json_is_llm_failure():
    cands, ok = extract("nonempty", FakeLLM("this is not json {"))
    assert cands == [] and ok is False


def test_extractor_llm_failure_not_ok():
    class FailLLM:
        def complete(self, *a, **k):
            raise RuntimeError("down")

        def complete_messages(self, *a, **k):
            raise RuntimeError("down")

    cands, ok = extract("nonempty transcript", FailLLM())
    assert cands == [] and ok is False


def test_extractor_skips_invalid_source_type():
    response = json.dumps([{
        "trigger": "x", "action": "y",
        "source_type": "WAT", "confidence": "high",
    }])
    cands, ok = extract("nonempty", FakeLLM(response))
    assert not ok and cands == []


def test_extractor_returns_empty_on_empty_transcript():
    cands, ok = extract("", FakeLLM(json.dumps([{"trigger": "x", "action": "y"}])))
    assert ok and cands == []


def test_compressor_truncates_long_assistant():
    long = "X" * 1000
    turns = [
        Turn(role="human", content="hi"),
        Turn(role="assistant", content=long),
    ]
    out = compress(turns)
    assert "[User] hi" in out
    assert "..." in out
    assert len(out) < len(long)


def test_compressor_marks_tool_error():
    turns = [
        Turn(role="tool_use", content="", tool_name="Bash",
             input_summary="rm -rf /tmp/foo"),
        Turn(role="tool_result", content="", is_error=True,
             error_line="permission denied"),
    ]
    out = compress(turns)
    assert "[Tool: Bash]" in out
    assert "[Result: ERROR]" in out
    assert "permission denied" in out


def test_reader_parses_jsonl(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join([
        json.dumps({"type": "user", "message": "hello"}),
        json.dumps({"type": "assistant", "message": "hi back"}),
        json.dumps({"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}}),
        json.dumps({"type": "tool_result", "content": "out", "is_error": False}),
    ]) + "\n")
    turns = read_transcript(path)
    roles = [t.role for t in turns]
    assert roles == ["human", "assistant", "tool_use", "tool_result"]
    assert turns[0].content == "hello"
    assert turns[2].tool_name == "Bash"


def test_reader_tolerates_malformed_lines(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        "this is not json\n"
        + json.dumps({"type": "user", "message": "ok"})
        + "\n"
    )
    turns = read_transcript(path)
    assert len(turns) == 1
    assert turns[0].role == "human"


def test_llm_adapter_complete_when_not_extracting(monkeypatch):
    monkeypatch.setenv("NOKORI_LLM_BASE_URL", "http://example/v1")
    monkeypatch.setenv("NOKORI_LLM_MODEL", "test-model")
    monkeypatch.delenv("NOKORI_EXTRACTING", raising=False)
    from nokori.config import Config
    from nokori.llm.adapter import LLMAdapter

    cfg = Config.from_env()

    def fake_open(req, timeout=30):
        class Resp:
            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": '  {"ok": true}  '}}],
                }).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        return Resp()

    adapter = LLMAdapter(cfg, http_open=fake_open)
    assert adapter.complete("prompt") == '{"ok": true}'


def test_llm_adapter_skips_when_extracting_env_set(monkeypatch):
    monkeypatch.setenv("NOKORI_LLM_BASE_URL", "http://example/v1")
    monkeypatch.setenv("NOKORI_LLM_MODEL", "test-model")
    monkeypatch.setenv("NOKORI_EXTRACTING", "1")
    from nokori.config import Config
    from nokori.llm.adapter import LLMAdapter

    cfg = Config.from_env()
    adapter = LLMAdapter(cfg, http_open=lambda *a, **k: None)
    assert adapter.complete("prompt") is None


def test_mark_extracted_on_empty_text(tmp_path, monkeypatch):
    """Extract marks transcript as extracted even when compressed text is empty."""
    import os
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    from nokori.config import Config
    from nokori.db import open_db
    cfg = Config.from_env()
    path = tmp_path / "empty.jsonl"
    path.write_text("")  # empty transcript

    from nokori.commands.extract import _process_path
    cands, applied, finished = _process_path(path, None, cfg, dry_run=False)
    assert cands == 0
    assert applied == 0
    assert finished is True

    db = open_db(cfg.db_path)
    try:
        row = db.fetchone(
            "SELECT * FROM extract_state WHERE transcript_path = ?",
            (str(path),),
        )
        assert row is not None
        assert row["status"] == "done"
    finally:
        db.close()


def test_extract_llm_failure_does_not_mark_extracted(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    path = tmp_path / "t.jsonl"
    path.write_text('{"type":"user","message":{"content":"fix the bug"}}\n', encoding="utf-8")

    class FailLLM:
        def complete(self, *a, **k):
            raise RuntimeError("llm down")

        def complete_messages(self, *a, **k):
            raise RuntimeError("llm down")

    from nokori.commands import extract as extract_cmd
    from nokori.db import open_db

    monkeypatch.setattr(extract_cmd, "LLMAdapter", lambda cfg: FailLLM())
    cands, applied, finished = extract_cmd._process_path(path, None, cfg, dry_run=False)
    assert cands == 0 and applied == 0 and finished is False
    db = open_db(cfg.db_path)
    try:
        row = db.fetchone(
            "SELECT 1 FROM extract_state WHERE transcript_path = ?",
            (str(path.resolve()),),
        )
        assert row is None
    finally:
        db.close()


def test_batch_extract_keeps_job_on_merge_llm_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    path = tmp_path / "merge-fail.jsonl"
    path.write_text('{"type":"user","message":{"content":"fix bug"}}\n', encoding="utf-8")
    from nokori.extract import jobs as job_io

    job_io.write_job(cfg, path, "proj", path.stat().st_mtime)

    extract_response = json.dumps([{
        "trigger": "brand new trigger xyz",
        "action": "do the new thing",
        "source_type": "solution",
        "confidence": "medium",
    }])
    from nokori.commands import extract as extract_cmd
    from nokori.db import open_db
    from nokori.extract.extractor import Candidate
    from nokori.extract.merger import merge_candidate
    import argparse

    class SeqLLM:
        def __init__(self):
            self.n = 0

        def complete(self, prompt, *, max_tokens=2000, timeout=30):
            self.n += 1
            if self.n == 1:
                return extract_response
            raise RuntimeError("merge down")

        def complete_messages(self, system, user, *, max_tokens=2000, timeout=30):
            return self.complete(user, max_tokens=max_tokens, timeout=timeout)

    monkeypatch.setattr(extract_cmd, "LLMAdapter", lambda cfg: SeqLLM())
    db = open_db(cfg.db_path)
    try:
        merge_candidate(
            Candidate(
                trigger="existing rule seed",
                trigger_variants=[],
                search_terms={},
                behavior=None,
                action="seed action",
                rationale=None,
                source_type="correction",
                confidence="high",
            ),
            db,
            FakeLLM("[]"),
            project_id="proj",
        )
    finally:
        db.close()

    args = argparse.Namespace(session=None, dry_run=False)
    assert extract_cmd.run(args, cfg) == 0
    assert len(job_io.list_jobs(cfg)) == 1
    db = open_db(cfg.db_path)
    try:
        row = db.fetchone(
            "SELECT 1 FROM extract_state WHERE transcript_path = ?",
            (str(path.resolve()),),
        )
        assert row is None
    finally:
        db.close()


def test_write_job_updates_existing_project_id(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    path = tmp_path / "proj.jsonl"
    path.write_text('{"type":"user"}\n', encoding="utf-8")
    mtime = path.stat().st_mtime
    from nokori.extract import jobs as job_io

    job_io.write_job(cfg, path, "proj-a", mtime)
    job_io.write_job(cfg, path, "proj-b", mtime)
    job = job_io.read_job(cfg.jobs_dir / f"extract-{job_io.transcript_hash(path, mtime)}.json")
    assert job is not None
    assert job["project_id"] == "proj-b"


def test_batch_extract_keeps_job_on_llm_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    path = tmp_path / "job-fail.jsonl"
    path.write_text('{"type":"user","message":{"content":"fix bug"}}\n', encoding="utf-8")
    from nokori.extract import jobs as job_io

    job_io.write_job(cfg, path, "proj", path.stat().st_mtime)

    class FailLLM:
        def complete(self, *a, **k):
            raise RuntimeError("llm down")

        def complete_messages(self, *a, **k):
            raise RuntimeError("llm down")

    from nokori.commands import extract as extract_cmd
    import argparse

    monkeypatch.setattr(extract_cmd, "LLMAdapter", lambda cfg: FailLLM())
    args = argparse.Namespace(session=None, dry_run=False)
    assert extract_cmd.run(args, cfg) == 0
    assert len(job_io.list_jobs(cfg)) == 1


def test_extract_refreshes_job_when_transcript_mtime_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    path = tmp_path / "stale.jsonl"
    path.write_text('{"type":"user","message":{"content":"hi"}}\n', encoding="utf-8")
    mtime_old = path.stat().st_mtime
    from nokori.extract import jobs as job_io

    job_path = job_io.write_job(cfg, path, "proj", mtime_old)
    path.write_text(
        path.read_text(encoding="utf-8") + '{"type":"user","message":{"content":"more"}}\n',
        encoding="utf-8",
    )
    new_mtime = path.stat().st_mtime
    assert new_mtime != mtime_old

    from nokori.commands.extract import run
    import argparse

    args = argparse.Namespace(session=None, dry_run=True)
    assert run(args, cfg) == 0
    pending = job_io.list_jobs(cfg)
    assert len(pending) == 1
    job = job_io.read_job(pending[0])
    assert job is not None
    assert float(job["transcript_mtime"]) == float(new_mtime)


def test_extract_lock_exclusive(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    from nokori.extract.lock import acquire

    with acquire(cfg) as first:
        assert first is True
        with acquire(cfg) as second:
            assert second is False
