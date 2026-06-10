import dataclasses
import json
from types import SimpleNamespace


from nokori.config import Config
from nokori.extract.compressor import compress
from nokori.extract.extractor import extract
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

    def complete_role(self, role, system, user, *, max_tokens=2000, timeout=30):
        self.calls += 1
        return self.response


def _valid_candidate(**overrides):
    base = {
        "trigger": "Force push to a shared branch",
        "trigger_zh": "强推到共享分支",
        "trigger_variants": ["git push --force"],
        "trigger_variants_zh": ["强推"],
        "search_terms": {"en": ["force", "push"], "zh": []},
        "required_concepts": ["force push"],
        "excluded_contexts": [],
        "non_generalization_boundaries": [],
        "near_miss_examples": ["push to personal branch"],
        "severity": "reminder",
        "domain_tags": ["git"],
        "tool_tags": ["Bash"],
        "file_or_path_patterns": [],
        "behavior": "used git push --force",
        "action": "use --force-with-lease",
        "action_zh": "使用 --force-with-lease",
        "rationale": "force push overwrites peers work",
        "evidence_quotes": ["user said: use --force-with-lease"],
    }
    base.update(overrides)
    return base


def test_extractor_parses_candidates():
    response = json.dumps({"candidates": [
        _valid_candidate(
            search_terms={"en": ["force", "push"], "zh": ["强推"]},
        )
    ]})
    cands, ok = extract("[User] dummy transcript\n", FakeLLM(response))
    assert ok and len(cands) == 1
    c = cands[0]
    assert "git push --force" in c.trigger_variants
    assert c.search_terms["zh"] == ["强推"]
    assert c.required_concepts == ["force push"]
    assert c.excluded_contexts == []
    assert c.severity == "reminder"
    assert c.domain_tags == ["git"]
    assert c.tool_tags == ["Bash"]
    assert c.near_miss_examples == ["push to personal branch"]
    assert c.evidence_quotes == ["user said: use --force-with-lease"]


def test_extractor_parses_optional_fields_empty_string():
    """Empty string _zh fields are treated as None by _opt_str."""
    response = json.dumps({"candidates": [
        _valid_candidate(trigger_zh="", action_zh="", trigger_variants_zh=[])
    ]})
    cands, ok = extract("nonempty", FakeLLM(response))
    assert ok and len(cands) == 1
    c = cands[0]
    assert c.trigger_text_zh is None
    assert c.action_zh is None
    assert c.trigger_variants_zh == []


def test_coerce_missing_zh_keys_returns_none():
    """_coerce handles missing _zh keys gracefully via _opt_str."""
    from nokori.extract.extractor import _coerce

    item = {
        "trigger": "Force push to a shared branch",
        "trigger_variants": ["git push --force"],
        "search_terms": {"en": ["force", "push"], "zh": []},
        "required_concepts": ["force push"],
        "excluded_contexts": [],
        "non_generalization_boundaries": [],
        "near_miss_examples": [],
        "severity": "reminder",
        "domain_tags": ["git"],
        "tool_tags": ["Bash"],
        "file_or_path_patterns": [],
        "behavior": "used git push --force",
        "action": "use --force-with-lease",
        "rationale": "force push overwrites peers work",
        "evidence_quotes": [],
    }
    c = _coerce(item)
    assert c.trigger_text_zh is None
    assert c.action_zh is None
    assert c.trigger_variants_zh == []


def test_extractor_skips_cjk_trigger():
    response = json.dumps({"candidates": [
        _valid_candidate(trigger="在学城文档中创建锚点"),
        _valid_candidate(trigger="Creating anchor links in wiki documents"),
    ]})
    cands, ok = extract("nonempty", FakeLLM(response))
    assert ok and len(cands) == 1
    assert cands[0].trigger.startswith("Creating")


def test_extractor_handles_empty_candidates():
    response = json.dumps({"candidates": []})
    cands, ok = extract("nonempty", FakeLLM(response))
    assert ok and cands == []


def test_extractor_handles_fenced_json():
    inner = json.dumps({"candidates": [_valid_candidate()]})
    response = f"```json\n{inner}\n```"
    cands, ok = extract("nonempty", FakeLLM(response))
    assert ok and len(cands) == 1


def test_extractor_missing_candidates_key_is_failure():
    response = json.dumps({"other_key": "value"})
    cands, ok = extract("nonempty", FakeLLM(response))
    assert cands == [] and ok is False


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


def test_extractor_returns_empty_on_empty_transcript():
    cands, ok = extract("", FakeLLM(json.dumps({"candidates": [_valid_candidate()]})))
    assert ok and cands == []


def test_compressor_preserves_full_assistant():
    long = "X" * 1000
    turns = [
        Turn(role="human", content="hi"),
        Turn(role="assistant", content=long),
    ]
    out = compress(turns)
    assert "[User] hi" in out
    assert long in out


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


def test_process_path_passes_transcript_evidence_and_role_limits(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg = dataclasses.replace(
        cfg,
        role_max_tokens={"admission_judge": 1234},
        role_timeouts={"admission_judge": 56},
    )
    path = tmp_path / "evidence.jsonl"
    path.write_text(
        '{"type":"user","message":{"content":"When deploy fails, use migrate deploy."}}\n',
        encoding="utf-8",
    )

    from nokori.commands import extract as extract_cmd

    class FakeExtractLLM:
        def complete_role(self, *_args, **_kwargs):
            return json.dumps({"candidates": [
                _valid_candidate(
                    trigger="deploy fails",
                    action="use migrate deploy",
                    evidence_quotes=["When deploy fails, use migrate deploy."],
                    required_concepts=["deploy fails"],
                )
            ]})

        def complete_messages(self, *_args, **_kwargs):
            return self.complete_role(*_args, **_kwargs)

    captured: dict = {}

    def fake_cold_pipeline(db, llm, *, transcript_ref, extractor_output, **kwargs):
        captured["extractor_output"] = extractor_output
        captured["kwargs"] = kwargs
        return SimpleNamespace(status="candidate", rule_id="rule-1")

    monkeypatch.setattr(extract_cmd, "LLMAdapter", lambda cfg: FakeExtractLLM())
    monkeypatch.setattr(extract_cmd, "run_cold_pipeline", fake_cold_pipeline)

    cands, applied, finished = extract_cmd._process_path(
        path, "proj", cfg, dry_run=False
    )

    assert cands == 1
    assert applied == 1
    assert finished is True
    assert captured["extractor_output"]["evidence_quotes"]
    assert "deploy" in captured["extractor_output"]["evidence_quotes"][0]
    assert captured["kwargs"]["project_id"] == "proj"
    assert captured["kwargs"]["role_max_tokens"] == cfg.role_max_tokens
    assert captured["kwargs"]["role_timeouts"] == cfg.role_timeouts


def test_batch_extract_consumes_job_on_cold_pipeline_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    path = tmp_path / "merge-fail.jsonl"
    path.write_text('{"type":"user","message":{"content":"fix bug"}}\n', encoding="utf-8")
    from nokori.extract import jobs as job_io

    job_io.write_job(cfg, path, "proj", path.stat().st_mtime)

    extract_response = json.dumps({"candidates": [
        _valid_candidate(
            trigger="brand new trigger xyz",
            action="do the new thing",
            evidence_quotes=["fix bug"],
            required_concepts=["trigger xyz"],
        )
    ]})
    from nokori.commands import extract as extract_cmd
    from nokori.db import open_db
    from nokori.utils.time import now_iso
    import argparse

    class SeqLLM:
        def __init__(self):
            self.n = 0

        def configured(self):
            return False

        def complete(self, prompt, *, max_tokens=2000, timeout=30):
            self.n += 1
            if self.n == 1:
                return extract_response
            raise RuntimeError("merge down")

        def complete_messages(self, system, user, *, max_tokens=2000, timeout=30):
            return self.complete(user, max_tokens=max_tokens, timeout=timeout)

        def complete_role(self, role, system, user, *, max_tokens=2000, timeout=30):
            return self.complete(user, max_tokens=max_tokens, timeout=timeout)

        def _fallback_claude_cli(self, system, user, timeout):
            return self.complete_messages(system, user, max_tokens=2000, timeout=timeout)

    monkeypatch.setattr(extract_cmd, "LLMAdapter", lambda cfg: SeqLLM())
    db = open_db(cfg.db_path)
    try:
        now = now_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, project_id, "
                "created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','1.0.0',?,?,?,?,?,?,?,?)",
                ("seed-id", "seed1234", "existing rule seed", "seed action",
                 "active", "reminder", "project", "proj", now, now),
            )
    finally:
        db.close()

    args = argparse.Namespace(session=None, dry_run=False)
    assert extract_cmd.run(args, cfg) == 0
    # In the v6 cold pipeline, LLM failures during merge are handled gracefully
    # (pipeline returns "rejected" or falls back to keep_both) so jobs are consumed.
    assert len(job_io.list_jobs(cfg)) == 0
    db = open_db(cfg.db_path)
    try:
        row = db.fetchone(
            "SELECT 1 FROM extract_state WHERE transcript_path = ?",
            (str(path.resolve()),),
        )
        # Job was processed (consumed), so extract_state is marked done
        assert row is not None
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

    job_io.write_job(cfg, path, "proj", mtime_old)
    path.write_text(
        path.read_text(encoding="utf-8") + '{"type":"user","message":{"content":"more"}}\n',
        encoding="utf-8",
    )
    new_mtime = path.stat().st_mtime
    assert new_mtime != mtime_old

    from nokori.commands import extract as extract_cmd
    import argparse

    class NoOpLLM:
        def complete(self, *a, **k):
            return '{"candidates": []}'

        def complete_messages(self, *a, **k):
            return '{"candidates": []}'

    monkeypatch.setattr(extract_cmd, "LLMAdapter", lambda cfg: NoOpLLM())
    args = argparse.Namespace(session=None, dry_run=True)
    assert extract_cmd.run(args, cfg) == 0
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


def test_extractor_parses_zh_fields():
    """_zh fields are extracted from LLM JSON when present."""
    response = json.dumps({"candidates": [
        _valid_candidate(
            trigger_zh="强制推送到共享分支",
            action_zh="使用 --force-with-lease",
            trigger_variants_zh=["强推到共享分支"],
        )
    ]})
    cands, ok = extract("[User] dummy transcript\n", FakeLLM(response))
    assert ok and len(cands) == 1
    c = cands[0]
    assert c.trigger_text_zh == "强制推送到共享分支"
    assert c.action_zh == "使用 --force-with-lease"
    assert c.trigger_variants_zh == ["强推到共享分支"]
