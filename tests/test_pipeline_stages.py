"""Tests for typed cold-path pipeline stages.

Verifies each stage accepts CandidateContext and returns CandidateContext or
ColdPipelineResult, with typed fields replacing raw dict threading.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nokori.db import Db, open_db


@pytest.fixture()
def db(tmp_path: Path) -> Db:
    return open_db(tmp_path / "test_rules.db")


def _make_llm_mock(responses: dict[str, str | Exception]) -> MagicMock:
    """Build a mock LLM routing by system prompt keyword (first match wins).

    These tests exercise the full DB path (job enqueue, cache check, etc.)
    via call_llm_role, so they require a complete DB schema from open_db.
    """
    mock = MagicMock()

    def _call(*, model, system, user, max_tokens, timeout):
        for keyword, resp in responses.items():
            if keyword in system:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise ValueError(f"No mock response: {system[:80]}")

    mock.call_raw = MagicMock(side_effect=_call)
    return mock


def _make_extractor_output(**overrides) -> dict:
    base = {
        "trigger": "When using pytest parametrize",
        "action": "Use indirect=True",
        "evidence_quotes": ["User said: use indirect"],
        "severity": "reminder",
        "domain_tags": ["python"],
        "tool_tags": [],
        "required_concepts": ["pytest_parametrize"],
        "excluded_contexts": [],
        "trigger_variants": [],
        "search_terms": {"en": ["pytest"], "zh": []},
        "near_miss_examples": [],
        "non_generalization_boundaries": [],
    }
    base.update(overrides)
    return base


def _admission_json(decision: str, overall: float = 0.92, evidence: float = 0.93) -> str:
    return json.dumps({
        "decision": decision,
        "scores": {
            "overall_quality": overall,
            "evidence_support": evidence,
            "trigger_specificity": 0.90,
            "action_clarity": 0.85,
            "scope_control": 0.88,
            "generalization_safety": 0.87,
            "retrieval_readiness": 0.86,
        },
        "reasoning": "Test.",
    })


# ---------------------------------------------------------------------------
# CandidateContext creation
# ---------------------------------------------------------------------------


class TestCandidateContext:
    def test_create_from_extractor_output(self):
        from nokori.cold.stages import CandidateContext, PipelineConfig

        extractor_output = _make_extractor_output()
        config = PipelineConfig(
            role_models=None,
            default_model="test-model",
            role_max_tokens=None,
            role_timeouts=None,
        )

        ctx = CandidateContext(
            extractor_output=extractor_output,
            transcript_ref="test-transcript",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
        )

        assert ctx.extractor_output == extractor_output
        assert ctx.transcript_ref == "test-transcript"
        assert ctx.admission_decision is None
        assert ctx.admission_scores is None
        assert ctx.rule_data is None

    def test_context_is_immutable(self):
        from nokori.cold.stages import CandidateContext, PipelineConfig

        config = PipelineConfig(
            role_models=None, default_model="m", role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output={"trigger": "x", "evidence_quotes": ["e"]},
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
        )
        with pytest.raises(AttributeError):
            ctx.admission_decision = "accept"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Admission stage
# ---------------------------------------------------------------------------


class TestAdmissionStage:
    def test_accept_returns_context_with_decision(self, db: Db):
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_admission

        llm = _make_llm_mock({"admission judge": _admission_json("accept")})
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output=_make_extractor_output(),
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
        )

        result = run_admission(ctx, db, llm)

        # Result is a new CandidateContext (not ColdPipelineResult) with decision set
        assert isinstance(result, CandidateContext)
        assert result.admission_decision == "accept"
        assert result.admission_scores is not None
        assert result.admission_scores["overall_quality"] == 0.92

    def test_reject_returns_pipeline_result(self, db: Db):
        from nokori.cold.pipeline import ColdPipelineResult
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_admission

        llm = _make_llm_mock({
            "admission judge": _admission_json("reject", overall=0.3, evidence=0.4),
        })
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output=_make_extractor_output(
                trigger="Bad rule", action="Do nothing",
                evidence_quotes=["Weak evidence"],
                domain_tags=[], required_concepts=[],
            ),
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
        )

        result = run_admission(ctx, db, llm)

        assert isinstance(result, ColdPipelineResult)
        assert result.status == "rejected"
        assert result.rejection_reason == "admission_judge_rejected"

    def test_no_evidence_quotes_rejects_before_llm_call(self, db: Db):
        from nokori.cold.pipeline import ColdPipelineResult
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_admission

        llm = _make_llm_mock({})  # Should not be called
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output=_make_extractor_output(
                trigger="No evidence rule", action="Something",
                evidence_quotes=[],
            ),
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
        )

        result = run_admission(ctx, db, llm)

        assert isinstance(result, ColdPipelineResult)
        assert result.status == "rejected"
        assert result.rejection_reason == "no_transcript_evidence"
        llm.call_raw.assert_not_called()

    def test_revise_decision_returns_context(self, db: Db):
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_admission

        llm = _make_llm_mock({
            "admission judge": _admission_json("revise", overall=0.7, evidence=0.75),
        })
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output=_make_extractor_output(
                trigger="Revisable rule", action="Do something",
                evidence_quotes=["Some evidence"],
                domain_tags=[], required_concepts=[],
            ),
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
        )

        result = run_admission(ctx, db, llm)

        assert isinstance(result, CandidateContext)
        assert result.admission_decision == "revise"


# ---------------------------------------------------------------------------
# Build rule data stage (rewrite or direct conversion)
# ---------------------------------------------------------------------------


def _rewriter_json() -> str:
    return json.dumps({
        "trigger_canonical": "When using pytest parametrize with fixtures",
        "required_concept_groups": [{"id": "grp1", "all_of": ["concept_0"]}],
        "concepts": [
            {
                "id": "concept_0",
                "label": "pytest parametrize",
                "aliases": [{"text": "pytest parametrize", "strength": "strong"}],
                "match_mode": "any_alias",
                "required": True,
            }
        ],
        "variants": [
            {"text": "When using pytest parametrize with fixtures", "kind": "strong_anchor", "requires_concepts": ["concept_0"]},
        ],
        "excluded_contexts": [],
        "action_instruction": "Use indirect=True for fixture params",
        "severity": "reminder",
        "search_terms": {"en": ["pytest", "parametrize", "indirect"], "zh": []},
        "scope": {"domain_tags": ["python", "testing"], "file_or_path_patterns": [], "tool_tags": []},
        "rewrite_rationale": "Narrowed trigger.",
    })


class TestBuildRuleDataStage:
    def test_accept_path_builds_rule_data_from_candidate(self, db: Db):
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_build_rule_data

        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output={
                "trigger": "When using pytest parametrize",
                "action": "Use indirect=True",
                "evidence_quotes": ["User said: use indirect"],
                "severity": "reminder",
                "domain_tags": ["python"],
                "tool_tags": [],
                "required_concepts": ["pytest_parametrize"],
                "excluded_contexts": [],
                "trigger_variants": ["parametrize with fixtures"],
                "search_terms": {"en": ["pytest"], "zh": []},
                "near_miss_examples": [],
                "non_generalization_boundaries": [],
            },
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
            admission_decision="accept",
            admission_scores={"overall_quality": 0.92},
        )

        # No LLM call needed for accept path
        llm = MagicMock()
        result = run_build_rule_data(ctx, db, llm)

        assert isinstance(result, CandidateContext)
        assert result.rule_data is not None
        assert result.rule_data["trigger_canonical"] == "When using pytest parametrize"
        assert result.rule_data["action_instruction"] == "Use indirect=True"
        assert "variants" in result.rule_data
        llm.call_raw.assert_not_called()

    def test_revise_path_calls_rewriter(self, db: Db):
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_build_rule_data

        llm = _make_llm_mock({"rule rewriter": _rewriter_json()})
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output={
                "trigger": "When using pytest parametrize",
                "action": "Use indirect=True",
                "evidence_quotes": ["User said: use indirect"],
                "severity": "reminder",
                "domain_tags": ["python"],
                "tool_tags": [],
                "required_concepts": ["pytest_parametrize"],
                "excluded_contexts": [],
                "trigger_variants": [],
                "search_terms": {"en": ["pytest"], "zh": []},
                "near_miss_examples": [],
                "non_generalization_boundaries": [],
                "trigger_zh": None,
                "action_zh": None,
                "trigger_variants_zh": [],
            },
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
            admission_decision="revise",
            admission_scores={"overall_quality": 0.7, "evidence_support": 0.75},
        )

        result = run_build_rule_data(ctx, db, llm)

        assert isinstance(result, CandidateContext)
        assert result.rule_data is not None
        assert result.rule_data["trigger_canonical"] == "When using pytest parametrize with fixtures"
        # Preserves evidence_quotes from original candidate
        assert result.rule_data["evidence_quotes"] == ["User said: use indirect"]


# ---------------------------------------------------------------------------
# Final judge stage
# ---------------------------------------------------------------------------


def _final_judge_json(decision: str) -> str:
    return json.dumps({
        "decision": decision,
        "reasoning": "Test.",
        "evidence_citations": ["quote1"],
    })


class TestFinalJudgeStage:
    def test_accept_active_sets_target_status(self, db: Db):
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_final_judge

        llm = _make_llm_mock({"final judge": _final_judge_json("accept_active")})
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output={"evidence_quotes": ["e"]},
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
            admission_decision="accept",
            admission_scores={"overall_quality": 0.92},
            rule_data={
                "trigger_canonical": "test trigger",
                "action_instruction": "test action",
                "evidence_quotes": ["e"],
                "variants": [{"text": "test trigger", "kind": "weak_recall", "requires_concepts": []}],
            },
        )

        result = run_final_judge(ctx, db, llm)

        assert isinstance(result, CandidateContext)
        assert result.final_decision == "accept_active"
        assert result.target_status == "active"

    def test_reject_returns_pipeline_result(self, db: Db):
        from nokori.cold.pipeline import ColdPipelineResult
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_final_judge

        llm = _make_llm_mock({"final judge": _final_judge_json("reject")})
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output={"evidence_quotes": ["e"]},
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
            admission_decision="accept",
            admission_scores={"overall_quality": 0.92},
            rule_data={
                "trigger_canonical": "test",
                "action_instruction": "action",
                "evidence_quotes": ["e"],
                "variants": [],
            },
        )

        result = run_final_judge(ctx, db, llm)

        assert isinstance(result, ColdPipelineResult)
        assert result.status == "rejected"
        assert result.rejection_reason == "final_judge_rejected"


# ---------------------------------------------------------------------------
# Merge planner stage
# ---------------------------------------------------------------------------


def _merge_planner_json(operation: str = "keep_both", target_ids: list | None = None) -> str:
    return json.dumps({
        "relation_shape": "equivalent" if operation == "reject_new" else "unrelated",
        "new_rule_safety": "safe",
        "operation_safety": "safe",
        "quality_winner": "existing" if operation == "reject_new" else "neither",
        "operation": operation,
        "confidence": 0.9,
        "reason": "test reason",
        "target_rule_ids": target_ids or [],
    })


class TestMergePlannerStage:
    def test_keep_both_returns_context(self, db: Db):
        """Empty DB short-circuits to keep_both without LLM call (tests fallback path)."""
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_merge_planner

        llm = _make_llm_mock({"merge planner": _merge_planner_json("keep_both")})
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output={"evidence_quotes": ["e"]},
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
            admission_decision="accept",
            admission_scores={"overall_quality": 0.92},
            rule_data={
                "trigger_canonical": "test trigger",
                "action_instruction": "test action",
                "evidence_quotes": ["e"],
                "variants": [],
                "search_terms": {"en": ["test"], "zh": []},
            },
            final_decision="accept_active",
            target_status="active",
        )

        result = run_merge_planner(ctx, db, llm)

        assert isinstance(result, CandidateContext)
        assert result.merge_op == "keep_both"
        assert result.merge_info is not None

    def test_reject_new_returns_pipeline_result(self, db: Db):
        from unittest.mock import patch

        from nokori.cold.pipeline import ColdPipelineResult
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_merge_planner

        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output={"evidence_quotes": ["e"]},
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
            admission_decision="accept",
            admission_scores={"overall_quality": 0.92},
            rule_data={
                "trigger_canonical": "dup trigger",
                "action_instruction": "dup action",
                "evidence_quotes": ["e"],
                "variants": [],
                "search_terms": {"en": ["test"], "zh": []},
            },
            final_decision="accept_active",
            target_status="active",
        )

        # apply_merge_policy overrides LLM's reject_new in most cases,
        # so we patch the composed function to test the stage's dispatch logic.
        with patch(
            "nokori.cold.stages._run_merge_planner",
            return_value=("reject_new", {"merge_rationale": "duplicate", "target_rule_ids": []}),
        ):
            result = run_merge_planner(ctx, db, MagicMock())

        assert isinstance(result, ColdPipelineResult)
        assert result.status == "rejected"
        assert "merge_planner_reject_new" in result.rejection_reason


# ---------------------------------------------------------------------------
# Full staged pipeline integration
# ---------------------------------------------------------------------------


class TestStagedPipelineIntegration:
    """Verify that chaining stages produces the same outcome as the original pipeline."""

    def test_accept_flow_produces_rule_data_and_final_decision(self, db: Db):
        """Acceptance flow: admission(accept) -> build_rule_data -> final_judge(accept_active)."""
        from nokori.cold.stages import (
            CandidateContext,
            PipelineConfig,
            run_admission,
            run_build_rule_data,
            run_final_judge,
            run_merge_planner,
        )

        llm = _make_llm_mock({
            "admission judge": _admission_json("accept"),
            "final judge": _final_judge_json("accept_active"),
        })
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output={
                "trigger": "When using pytest parametrize with fixtures",
                "action": "Use indirect=True for fixture params",
                "evidence_quotes": ["User said: use indirect=True"],
                "severity": "reminder",
                "domain_tags": ["python", "testing"],
                "tool_tags": [],
                "required_concepts": ["pytest_parametrize"],
                "excluded_contexts": [],
                "trigger_variants": ["parametrize with fixtures"],
                "search_terms": {"en": ["pytest", "parametrize"], "zh": []},
                "near_miss_examples": [],
                "non_generalization_boundaries": [],
            },
            transcript_ref="test-transcript-001",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
        )

        # Stage 1: Admission
        result = run_admission(ctx, db, llm)
        assert isinstance(result, CandidateContext)
        assert result.admission_decision == "accept"

        # Stage 2: Build rule data
        result = run_build_rule_data(result, db, llm)
        assert isinstance(result, CandidateContext)
        assert result.rule_data is not None
        assert result.rule_data["trigger_canonical"] == "When using pytest parametrize with fixtures"

        # Stage 3: Final judge
        result = run_final_judge(result, db, llm)
        assert isinstance(result, CandidateContext)
        assert result.final_decision == "accept_active"
        assert result.target_status == "active"

        # Stage 4: Merge planner (no existing rules -> keep_both)
        result = run_merge_planner(result, db, llm)
        assert isinstance(result, CandidateContext)
        assert result.merge_op == "keep_both"

    def test_rejection_short_circuits(self, db: Db):
        """Early rejection: admission rejects -> pipeline terminates."""
        from nokori.cold.pipeline import ColdPipelineResult
        from nokori.cold.stages import CandidateContext, PipelineConfig, run_admission

        llm = _make_llm_mock({
            "admission judge": _admission_json("reject", overall=0.3, evidence=0.4),
        })
        config = PipelineConfig(
            role_models=None, default_model="test-model",
            role_max_tokens=None, role_timeouts=None,
        )
        ctx = CandidateContext(
            extractor_output=_make_extractor_output(
                trigger="Vague rule", action="Do something",
                evidence_quotes=["Weak"],
                domain_tags=[], required_concepts=[],
            ),
            transcript_ref="t",
            source_origin="transcript_extraction",
            project_id=None,
            config=config,
        )

        result = run_admission(ctx, db, llm)

        # Pipeline terminates — no further stages needed
        assert isinstance(result, ColdPipelineResult)
        assert result.status == "rejected"
