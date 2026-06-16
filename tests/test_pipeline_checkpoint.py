"""Tests for cold pipeline checkpoint persistence and resume."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from nokori.cold.pipeline import (
    _CHECKPOINT_PIPELINE_VERSION,
    _load_checkpoint,
    _run_pipeline_staged,
    _stage_index,
    _write_checkpoint,
    run_cold_pipeline,
)
from nokori.cold.stages import CandidateContext, PipelineConfig
from nokori.db import Db, open_db


@pytest.fixture
def db(tmp_path):
    d = open_db(tmp_path / "test.db")
    yield d
    d.close()


class _FakeLlm:
    def __init__(self, responses: dict[str, str]):
        self._responses = responses

    def call_raw(self, model, system, user, max_tokens, timeout):
        for key, val in self._responses.items():
            if key.lower() in system.lower():
                return val
        raise ValueError(f"No mock response matched system prompt: {system[:80]}")


def _make_llm_mock(responses: dict[str, str]):
    return _FakeLlm(responses)


def _admission_json(decision="accept", overall=0.92, evidence=0.95):
    return json.dumps({
        "scores": {
            "overall_quality": overall,
            "evidence_support": evidence,
            "trigger_specificity": 0.88,
            "action_clarity": 0.90,
            "scope_control": 0.85,
            "generalization_safety": 0.80,
            "retrieval_readiness": 0.85,
        },
        "decision": decision,
        "reasoning": "Test admission",
    })


def _final_judge_json(decision="accept_active"):
    return json.dumps({
        "decision": decision,
        "reasoning": "Test final judge",
    })


def _merge_planner_json(operation="keep_both"):
    return json.dumps({
        "relation_shape": "unrelated",
        "new_rule_safety": "safe",
        "operation_safety": "safe",
        "quality_winner": "new",
        "operation": operation,
        "confidence": 0.9,
        "reason": "Test merge planner",
    })


def _extractor_candidate():
    return {
        "trigger": "When writing tests with pytest fixtures",
        "action": "Use conftest.py for shared fixtures",
        "behavior": "",
        "evidence_quotes": ["user: put fixtures in conftest", "assistant: fixed"],
        "required_concepts": ["pytest fixtures"],
        "excluded_contexts": [],
        "search_terms": {"en": ["pytest", "fixtures", "conftest"], "zh": []},
        "trigger_variants": ["pytest fixture usage"],
        "trigger_variants_zh": [],
        "near_miss_examples": [],
        "severity": "reminder",
        "domain_tags": ["python", "testing"],
        "tool_tags": [],
        "file_or_path_patterns": [],
    }


class TestStageIndex:
    def test_known_stages(self):
        assert _stage_index("admission") == 0
        assert _stage_index("build_rule_data") == 1
        assert _stage_index("merge_planner") == 3
        assert _stage_index("insert_or_merge") == 8

    def test_unknown_stage(self):
        assert _stage_index("nonexistent") == -1


class TestCheckpointWriteAndLoad:
    def test_write_and_load_checkpoint(self, db: Db):
        from nokori.cold.jobs import enqueue_transcript_ingest

        segment_hash = "abc123"
        enqueue_transcript_ingest(db, "ref1", segment_hash, "v1")

        config = PipelineConfig(
            role_models=None, default_model="m",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output={"evidence_quotes": ["e"]},
            transcript_ref="ref1",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
            admission_decision="accept",
            admission_scores={"overall_quality": 0.92},
        )

        _write_checkpoint(db, "ref1", segment_hash, "admission", ctx)

        loaded = _load_checkpoint(db, segment_hash)
        assert loaded is not None
        stage, fields = loaded
        assert stage == "admission"
        assert fields["admission_decision"] == "accept"
        assert fields["admission_scores"]["overall_quality"] == 0.92

    def test_load_returns_none_without_checkpoint(self, db: Db):
        assert _load_checkpoint(db, "nonexistent") is None

    def test_load_returns_none_on_version_mismatch(self, db: Db):
        from nokori.cold.jobs import enqueue_transcript_ingest

        segment_hash = "ver_mismatch"
        enqueue_transcript_ingest(db, "ref2", segment_hash, "v1")

        bad_checkpoint = json.dumps({
            "pipeline_version": "99.99.99",
            "stage": "admission",
            "context": {"admission_decision": "accept"},
        })
        with db.transaction() as tx:
            tx.execute(
                "UPDATE transcript_ingest_jobs SET pipeline_checkpoint = ? WHERE segment_hash = ?",
                (bad_checkpoint, segment_hash),
            )

        loaded = _load_checkpoint(db, segment_hash)
        assert loaded is None


class TestCheckpointResume:
    def test_resume_skips_completed_stages(self, db: Db):
        """Pipeline resumes from checkpoint, skipping already-completed stages."""
        from nokori.cold.jobs import enqueue_transcript_ingest

        candidate = _extractor_candidate()
        segment_hash = "resume_test"
        enqueue_transcript_ingest(db, "ref_resume", segment_hash, "v1")

        checkpoint_data = json.dumps({
            "pipeline_version": _CHECKPOINT_PIPELINE_VERSION,
            "stage": "final_judge",
            "context": {
                "extractor_output": candidate,
                "transcript_ref": "ref_resume",
                "source_origin": "transcript_extraction",
                "project_id": None,
                "admission_decision": "accept",
                "admission_scores": {"overall_quality": 0.92, "evidence_support": 0.95,
                                     "trigger_specificity": 0.88, "action_clarity": 0.90,
                                     "scope_control": 0.85, "generalization_safety": 0.80,
                                     "retrieval_readiness": 0.85},
                "rule_data": {
                    "trigger_canonical": "When writing tests with pytest fixtures",
                    "action_instruction": "Use conftest.py for shared fixtures",
                    "severity": "reminder",
                    "scope": {"domain_tags": ["python"], "tool_tags": [], "file_or_path_patterns": []},
                    "required_concept_groups": [{"id": "grp1", "all_of": ["concept_0"]}],
                    "concepts": [{"id": "concept_0", "label": "pytest fixtures",
                                  "aliases": [{"text": "pytest fixtures", "strength": "strong"}],
                                  "match_mode": "any_alias", "required": True}],
                    "excluded_contexts": [],
                    "variants": [{"text": "pytest fixture usage", "kind": "strong_anchor",
                                  "requires_concepts": ["concept_0"]}],
                    "search_terms": {"en": ["pytest", "fixtures"], "zh": []},
                    "evidence_quotes": ["user: put fixtures in conftest"],
                    "non_generalization_boundaries": [],
                },
                "final_decision": "accept_active",
                "target_status": "active",
                "merge_op": None,
                "merge_info": None,
                "fingerprint_block": None,
                "synthetic_passed": False,
                "adversarial_failures": 0,
                "synthetic_eval_skipped": False,
                "fast_lane_passed": False,
            },
        })
        with db.transaction() as tx:
            tx.execute(
                "UPDATE transcript_ingest_jobs SET pipeline_checkpoint = ? WHERE segment_hash = ?",
                (checkpoint_data, segment_hash),
            )

        llm = _make_llm_mock({
            "merge planner": _merge_planner_json("keep_both"),
        })

        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output=candidate,
            transcript_ref="ref_resume",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
        )

        with patch("nokori.cold.stages.check_fingerprint_block", return_value=None):
            result = _run_pipeline_staged(db, llm, ctx, segment_hash=segment_hash)

        assert result.status in ("candidate", "active")
        assert result.rule_id is not None


class TestStageTiming:
    def test_stage_logs_timing(self, db: Db):
        """Orchestrator logs stage name and duration."""
        logged_messages: list[str] = []

        def capture_info(msg, *args):
            logged_messages.append(msg % args if args else msg)

        llm = _make_llm_mock({
            "admission judge": _admission_json("accept"),
            "final judge": _final_judge_json("accept_active"),
            "merge planner": _merge_planner_json("keep_both"),
        })

        with patch("nokori.cold.stages.check_fingerprint_block", return_value=None), \
             patch("nokori.cold.pipeline.log.info", side_effect=capture_info):
            run_cold_pipeline(
                db, llm,
                transcript_ref="timing_test",
                extractor_output=_extractor_candidate(),
                default_model="test-model",
            )

        stage_logs = [m for m in logged_messages if "stage=" in m and "duration_ms=" in m]
        assert len(stage_logs) >= 5
        assert "stage=admission" in stage_logs[0]
