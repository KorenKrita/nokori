"""Tests for transcript partial-retry: offset gating, failure recording, dead state."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nokori.cold.jobs import (
    MAX_INGEST_RETRIES,
    enqueue_transcript_ingest,
    record_ingest_failure,
)
from nokori.db import Db, open_db


@pytest.fixture()
def db(tmp_path: Path) -> Db:
    return open_db(tmp_path / "test.db")


class TestRecordIngestFailure:
    def test_bumps_retries_on_pending_job(self, db: Db):
        enqueue_transcript_ingest(db, "t.jsonl", "seg_aaa", "v1")
        record_ingest_failure(db, "seg_aaa", "v1", "timeout error")
        row = db.fetchone(
            "SELECT retries, last_error, status FROM transcript_ingest_jobs "
            "WHERE segment_hash = ?",
            ("seg_aaa",),
        )
        assert row is not None
        assert row["retries"] == 1
        assert row["status"] == "pending"
        assert "timeout" in row["last_error"]

    def test_marks_dead_after_max_retries(self, db: Db):
        enqueue_transcript_ingest(db, "t.jsonl", "seg_bbb", "v1")
        for i in range(MAX_INGEST_RETRIES):
            record_ingest_failure(db, "seg_bbb", "v1", f"error {i}")
        row = db.fetchone(
            "SELECT retries, status, last_error FROM transcript_ingest_jobs "
            "WHERE segment_hash = ?",
            ("seg_bbb",),
        )
        assert row is not None
        assert row["status"] == "dead"
        assert row["retries"] == MAX_INGEST_RETRIES

    def test_noop_if_job_not_pending(self, db: Db):
        enqueue_transcript_ingest(db, "t.jsonl", "seg_ccc", "v1")
        # Mark as done
        with db.transaction() as tx:
            tx.execute(
                "UPDATE transcript_ingest_jobs SET status='done' WHERE segment_hash=?",
                ("seg_ccc",),
            )
        record_ingest_failure(db, "seg_ccc", "v1", "should be ignored")
        row = db.fetchone(
            "SELECT status, retries FROM transcript_ingest_jobs WHERE segment_hash=?",
            ("seg_ccc",),
        )
        assert row["status"] == "done"
        assert row["retries"] == 0


class TestDeadSegmentSkippedOnDedup:
    def test_dead_segment_not_reenqueued(self, db: Db):
        job_id = enqueue_transcript_ingest(db, "t.jsonl", "seg_dead", "v1")
        with db.transaction() as tx:
            tx.execute(
                "UPDATE transcript_ingest_jobs SET status='dead' WHERE id=?",
                (job_id,),
            )
        # Re-enqueue same segment: should return existing id (dedup skip)
        result_id = enqueue_transcript_ingest(db, "t.jsonl", "seg_dead", "v1")
        assert result_id == job_id
        # Confirm only 1 row
        rows = db.fetchall(
            "SELECT id FROM transcript_ingest_jobs WHERE segment_hash=?",
            ("seg_dead",),
        )
        assert len(rows) == 1


class TestExtractTranscriptOffsetGating:
    def _write_transcript(self, path: Path) -> None:
        path.write_text(
            '{"role":"user","content":"test prompt 1"}\n'
            '{"role":"assistant","content":"test response"}\n'
            '{"role":"user","content":"test prompt 2"}\n',
            encoding="utf-8",
        )

    def test_partial_failure_does_not_advance_offset(self, db: Db, tmp_path: Path):
        from nokori.extract.process import extract_transcript

        t_path = tmp_path / "session.jsonl"
        self._write_transcript(t_path)

        cfg = MagicMock()
        cfg.db_path = tmp_path / "test.db"
        cfg.llm_model = "test"
        cfg.role_models = {}
        cfg.role_max_tokens = {}
        cfg.role_timeouts = {}

        mock_candidates = [
            MagicMock(trigger="cand1", trigger_text_zh="", trigger_variants=[],
                      trigger_variants_zh=[], search_terms={}, required_concepts=[],
                      excluded_contexts=[], non_generalization_boundaries=[],
                      near_miss_examples=[], severity="reminder", domain_tags=[],
                      tool_tags=[], file_or_path_patterns=[], behavior="",
                      action="act1", action_zh="", evidence_quotes=[], rationale=None),
            MagicMock(trigger="cand2_fail", trigger_text_zh="", trigger_variants=[],
                      trigger_variants_zh=[], search_terms={}, required_concepts=[],
                      excluded_contexts=[], non_generalization_boundaries=[],
                      near_miss_examples=[], severity="reminder", domain_tags=[],
                      tool_tags=[], file_or_path_patterns=[], behavior="",
                      action="act2", action_zh="", evidence_quotes=[], rationale=None),
        ]

        pipeline_call_count = [0]

        def mock_pipeline(db_arg, llm_arg, **kwargs):
            pipeline_call_count[0] += 1
            if pipeline_call_count[0] == 2:
                raise RuntimeError("simulated cold pipeline failure")
            return MagicMock(status="done", rule_id="rule-1")

        with (
            patch("nokori.extract.process.extract_candidates", return_value=(mock_candidates, True)),
            patch("nokori.extract.process.run_cold_pipeline", side_effect=mock_pipeline),
            patch("nokori.extract.process.compress", return_value="compressed text"),
        ):
            cands, rules_created, all_ok = extract_transcript(t_path, "proj-1", cfg, db)

        assert all_ok is False
        assert rules_created == 1

        # Offset should NOT be advanced (failed segment is still retryable)
        row = db.fetchone("SELECT last_byte_offset FROM extract_state WHERE transcript_path=?",
                          (str(t_path),))
        assert row is None  # not marked extracted

    def test_all_dead_advances_offset(self, db: Db, tmp_path: Path):
        from nokori.extract.process import extract_transcript

        t_path = tmp_path / "session2.jsonl"
        self._write_transcript(t_path)

        cfg = MagicMock()
        cfg.db_path = tmp_path / "test.db"
        cfg.llm_model = "test"
        cfg.role_models = {}
        cfg.role_max_tokens = {}
        cfg.role_timeouts = {}

        mock_candidates = [
            MagicMock(trigger="cand_always_fail", trigger_text_zh="", trigger_variants=[],
                      trigger_variants_zh=[], search_terms={}, required_concepts=[],
                      excluded_contexts=[], non_generalization_boundaries=[],
                      near_miss_examples=[], severity="reminder", domain_tags=[],
                      tool_tags=[], file_or_path_patterns=[], behavior="",
                      action="act_fail", action_zh="", evidence_quotes=[], rationale=None),
        ]

        def mock_pipeline_always_fail(db_arg, llm_arg, **kwargs):
            raise RuntimeError("permanent failure")

        with (
            patch("nokori.extract.process.extract_candidates", return_value=(mock_candidates, True)),
            patch("nokori.extract.process.run_cold_pipeline", side_effect=mock_pipeline_always_fail),
            patch("nokori.extract.process.compress", return_value="compressed text"),
        ):
            # Run MAX_INGEST_RETRIES times to exhaust retries
            for _ in range(MAX_INGEST_RETRIES):
                cands, rules_created, all_ok = extract_transcript(t_path, "proj-1", cfg, db)

        assert all_ok is False

        # After N failures, segment is dead, offset should advance
        row = db.fetchone("SELECT last_byte_offset FROM extract_state WHERE transcript_path=?",
                          (str(t_path),))
        assert row is not None
        assert row["last_byte_offset"] > 0

    def test_success_path_unchanged(self, db: Db, tmp_path: Path):
        from nokori.extract.process import extract_transcript

        t_path = tmp_path / "session3.jsonl"
        self._write_transcript(t_path)

        cfg = MagicMock()
        cfg.db_path = tmp_path / "test.db"
        cfg.llm_model = "test"
        cfg.role_models = {}
        cfg.role_max_tokens = {}
        cfg.role_timeouts = {}

        mock_candidates = [
            MagicMock(trigger="cand_ok", trigger_text_zh="", trigger_variants=[],
                      trigger_variants_zh=[], search_terms={}, required_concepts=[],
                      excluded_contexts=[], non_generalization_boundaries=[],
                      near_miss_examples=[], severity="reminder", domain_tags=[],
                      tool_tags=[], file_or_path_patterns=[], behavior="",
                      action="act_ok", action_zh="", evidence_quotes=[], rationale=None),
        ]

        with (
            patch("nokori.extract.process.extract_candidates", return_value=(mock_candidates, True)),
            patch("nokori.extract.process.run_cold_pipeline",
                  return_value=MagicMock(status="done", rule_id="rule-ok")),
            patch("nokori.extract.process.compress", return_value="compressed text"),
        ):
            cands, rules_created, all_ok = extract_transcript(t_path, "proj-1", cfg, db)

        assert all_ok is True
        assert rules_created == 1

        # Offset should be advanced
        row = db.fetchone("SELECT last_byte_offset FROM extract_state WHERE transcript_path=?",
                          (str(t_path),))
        assert row is not None
        assert row["last_byte_offset"] > 0
