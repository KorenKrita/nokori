"""Partial extract retries: segment_hash dedup prevents double-processing."""
import json
from dataclasses import dataclass
from unittest.mock import patch

from nokori.commands import extract as extract_cmd
from nokori.config import Config
from nokori.db import open_db


@dataclass
class _FakeColdResult:
    status: str
    rule_id: str | None
    rejection_reason: str | None = None
    scores: dict | None = None


def test_partial_extract_retry_deduplicates_via_segment_hash(monkeypatch, tmp_path):
    """On partial failure + retry, transcript_ingest_jobs dedup prevents re-enqueue (job-level idempotency)."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    path = tmp_path / "partial.jsonl"
    path.write_text('{"type":"user","message":"fix deploy"}\n', encoding="utf-8")

    def _cand(trigger, action):
        return {
            "trigger": trigger,
            "trigger_zh": "",
            "trigger_variants": [],
            "trigger_variants_zh": [],
            "search_terms": {"en": [], "zh": []},
            "required_concepts": [],
            "excluded_contexts": [],
            "non_generalization_boundaries": [],
            "near_miss_examples": [],
            "severity": "reminder",
            "domain_tags": [],
            "tool_tags": [],
            "file_or_path_patterns": [],
            "behavior": "",
            "action": action,
            "action_zh": "",
            "rationale": "",
            "evidence_quotes": ["fix deploy"],
        }

    extract_payload = json.dumps({
        "candidates": [
            _cand("deploy prisma schema", "use migrate deploy"),
            _cand("totally unrelated new topic", "do something else"),
        ]
    })

    cold_call_count = [0]

    def fake_cold_pipeline(db, llm, *, transcript_ref, extractor_output, **kwargs):
        cold_call_count[0] += 1
        if "unrelated" in extractor_output.get("trigger", ""):
            raise RuntimeError("simulated cold pipeline failure")
        return _FakeColdResult(status="candidate", rule_id="rule-new-1")

    class FakeExtractLLM:
        def complete_role(self, role, system, user, *, max_tokens=3000, timeout=60):
            return extract_payload

        def complete_messages(self, system, user, *, max_tokens=3000, timeout=60):
            return extract_payload

        def configured(self):
            return True

        def _call_openai_compatible(self, system, user, max_tokens, timeout, model_id=None):
            return self.complete_messages(system, user, max_tokens=max_tokens, timeout=timeout)

    monkeypatch.setattr(extract_cmd, "LLMAdapter", lambda cfg: FakeExtractLLM())

    # First run: first candidate succeeds, second fails -> all_ok = False
    with patch("nokori.commands.extract.run_cold_pipeline", side_effect=fake_cold_pipeline):
        cands, rules, finished = extract_cmd._process_path(path, "proj", cfg, dry_run=False)

    assert cands == 2
    assert rules == 1
    assert finished is False  # partial failure

    # Verify transcript_ingest_jobs were created (dedup mechanism)
    db = open_db(cfg.db_path)
    try:
        jobs = db.fetchall(
            "SELECT segment_hash, status FROM transcript_ingest_jobs"
        )
        assert len(jobs) == 2  # both candidates enqueued
    finally:
        db.close()

    # Second run (retry): enqueue_transcript_ingest will dedup existing jobs
    cold_call_count[0] = 0
    with patch("nokori.commands.extract.run_cold_pipeline", side_effect=fake_cold_pipeline):
        cands2, rules2, finished2 = extract_cmd._process_path(path, "proj", cfg, dry_run=False)

    # Same candidates extracted again (transcript not marked)
    assert cands2 == 2
    # Retry still partially fails (the "unrelated" candidate still raises)
    assert finished2 is False
    # Cold pipeline called for both candidates on retry
    assert cold_call_count[0] == 2
    # Cold pipeline is still called for both (segment_hash dedup is at job level)
    # but the enqueue_transcript_ingest idempotently returns existing job ids
    db = open_db(cfg.db_path)
    try:
        jobs = db.fetchall(
            "SELECT segment_hash, status FROM transcript_ingest_jobs"
        )
        # Still just 2 jobs (dedup worked)
        assert len(jobs) == 2
    finally:
        db.close()
