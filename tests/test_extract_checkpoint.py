"""Partial extract retries must not double-count same_extraction evidence."""
import json

from nokori.commands import extract as extract_cmd
from nokori.config import Config
from nokori.db import fetch_rules, open_db
from nokori.extract import checkpoint as merge_checkpoint
from nokori.extract.extractor import Candidate
from nokori.extract.merger import merge_candidate
from tests.test_merger import FakeMergeLLM


def test_partial_extract_retry_skips_checkpointed_candidates(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    path = tmp_path / "partial.jsonl"
    path.write_text('{"type":"user","message":"fix deploy"}\n', encoding="utf-8")

    db = open_db(cfg.db_path)
    try:
        merge_candidate(
            Candidate(
                trigger="deploy prisma migration",
                trigger_variants=[],
                search_terms={},
                behavior=None,
                action="use migrate deploy",
                rationale=None,
                source_type="correction",
                confidence="medium",
            ),
            db,
            FakeMergeLLM("[]"),
            project_id="proj",
        )
        rule = fetch_rules(db, statuses=("candidate",))[0]
        score_before = rule.evidence_score or 0
    finally:
        db.close()

    extract_payload = json.dumps([
        {
            "trigger": "deploy prisma schema",
            "action": "use migrate deploy",
            "source_type": "correction",
            "confidence": "medium",
        },
        {
            "trigger": "totally unrelated new topic",
            "action": "do something else",
            "source_type": "solution",
            "confidence": "medium",
        },
    ])

    class SeqLLM:
        def __init__(self):
            self.extract_calls = 0
            self.merge_calls = 0

        def complete_messages(self, system, user, *, max_tokens=3000, timeout=60):
            if "NEW CANDIDATE" in user or "EXISTING RULES" in user:
                self.merge_calls += 1
                if "deploy prisma schema" in user:
                    return json.dumps({
                        "relationships": [
                            {"existing_id": rule.id, "judgment": "A", "reasoning": "same"},
                        ]
                    })
                raise RuntimeError("merge down on second candidate")
            self.extract_calls += 1
            return extract_payload

    monkeypatch.setattr(extract_cmd, "LLMAdapter", lambda cfg: SeqLLM())

    _, _, finished = extract_cmd._process_path(path, "proj", cfg, dry_run=False)
    assert finished is False
    assert len(merge_checkpoint.load_merged_keys(cfg, path)) == 1

    db = open_db(cfg.db_path)
    try:
        rule = fetch_rules(db, statuses=("candidate",))[0]
        score_after_first = rule.evidence_score or 0
        assert score_after_first == score_before + 1
    finally:
        db.close()

    _, _, _finished2 = extract_cmd._process_path(path, "proj", cfg, dry_run=False)

    db = open_db(cfg.db_path)
    try:
        rule = fetch_rules(db, statuses=("candidate",))[0]
        assert (rule.evidence_score or 0) == score_after_first
    finally:
        db.close()
