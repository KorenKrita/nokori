"""Typed cold-path pipeline stages.

Each stage takes a CandidateContext and returns either:
- A new CandidateContext with additional fields populated (continue)
- A ColdPipelineResult (terminate: rejected/pending)

Exception propagation: stages let CircuitBreakerOpenError and ValueError
propagate to the caller (pipeline orchestrator), which handles them as
"pending" status per spec section 13.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..db import Db
from .pipeline import ColdPipelineResult
from .integrate import _run_merge_planner
from .qualify import (
    _candidate_to_rule_data,
    _ensure_rule_data_variants,
    _run_admission_judge,
    _run_final_judge,
    _run_rewriter,
)
from .roles import resolve_model_id


@dataclass(frozen=True)
class PipelineConfig:
    """Constant configuration for a pipeline run."""

    role_models: dict[str, str] | None
    default_model: str | None
    role_max_tokens: dict[str, int] | None
    role_timeouts: dict[str, int] | None


@dataclass(frozen=True)
class CandidateContext:
    """Typed state flowing through cold-path pipeline stages."""

    # Input (set at creation)
    extractor_output: dict[str, Any]
    transcript_ref: str
    source_origin: str
    project_id: str | None
    config: PipelineConfig

    # After admission stage
    admission_decision: str | None = None
    admission_scores: dict | None = None

    # After rewrite / candidate_to_rule_data
    rule_data: dict[str, Any] | None = None

    # After final judge
    final_decision: str | None = None
    target_status: str | None = None

    # After merge planner
    merge_op: str | None = None
    merge_info: dict | None = None

    # After compile
    compiled_matcher: Any = None

    # After synthetic eval
    synthetic_passed: bool = False
    adversarial_failures: int = 0
    synthetic_result: Any = None
    synthetic_eval_skipped: bool = False

    # After fast lane check
    fast_lane_passed: bool = False


def run_admission(
    ctx: CandidateContext, db: Db, llm: Any
) -> CandidateContext | ColdPipelineResult:
    """Admission stage: pre-check evidence, then run admission judge.

    Returns CandidateContext with admission_decision/scores on accept/revise,
    or ColdPipelineResult on rejection.
    """
    evidence_quotes = ctx.extractor_output.get("evidence_quotes", [])
    if not evidence_quotes:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="no_transcript_evidence",
            scores=None,
        )

    model_id = resolve_model_id(
        "admission_judge", ctx.config.role_models, ctx.config.default_model
    )
    decision, scores = _run_admission_judge(
        db, llm, ctx.extractor_output, model_id,
        ctx.config.role_max_tokens, ctx.config.role_timeouts,
    )

    if decision == "reject":
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="admission_judge_rejected",
            scores=scores,
        )

    return replace(ctx, admission_decision=decision, admission_scores=scores)


def run_build_rule_data(
    ctx: CandidateContext, db: Db, llm: Any
) -> CandidateContext | ColdPipelineResult:
    """Build structured rule_data: rewrite if revise, else convert directly.

    Returns CandidateContext with rule_data populated, or ColdPipelineResult on failure.
    """
    candidate = ctx.extractor_output

    if ctx.admission_decision == "revise":
        rewriter_model = resolve_model_id(
            "rule_rewriter", ctx.config.role_models, ctx.config.default_model
        )
        rule_data = _run_rewriter(
            db, llm, candidate, ctx.admission_scores or {},
            rewriter_model, ctx.config.role_max_tokens, ctx.config.role_timeouts,
        )
        if rule_data is None:
            return ColdPipelineResult(
                status="rejected",
                rule_id=None,
                rejection_reason="rewriter_failed",
                scores=ctx.admission_scores,
            )
        rule_data["evidence_quotes"] = candidate.get("evidence_quotes", [])
        rule_data["trigger_canonical_zh"] = candidate.get("trigger_zh")
        rule_data["action_instruction_zh"] = candidate.get("action_zh")
        rule_data["trigger_variants_zh"] = candidate.get("trigger_variants_zh", [])
        rule_data["non_generalization_boundaries"] = candidate.get("non_generalization_boundaries", [])
        rule_data["near_miss_examples"] = candidate.get("near_miss_examples", [])
        rule_data["_rewritten"] = True
    else:
        rule_data = _candidate_to_rule_data(candidate)

    rule_data = _ensure_rule_data_variants(rule_data)
    return replace(ctx, rule_data=rule_data)


def run_final_judge(
    ctx: CandidateContext, db: Db, llm: Any
) -> CandidateContext | ColdPipelineResult:
    """Final judge stage: decide accept_active / accept_candidate / reject.

    Returns CandidateContext with final_decision and target_status set,
    or ColdPipelineResult on rejection.
    """
    if ctx.rule_data is None:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="final_judge_no_rule_data",
            scores=ctx.admission_scores,
        )
    rule_data = ctx.rule_data
    rule_data_for_judge = {
        k: v for k, v in rule_data.items()
        if k != "evidence_quotes" and not k.startswith("_")
    }
    evidence_quotes = ctx.extractor_output.get("evidence_quotes", [])

    model_id = resolve_model_id(
        "final_judge", ctx.config.role_models, ctx.config.default_model
    )
    final_decision = _run_final_judge(
        db, llm, rule_data_for_judge, evidence_quotes, model_id,
        ctx.config.role_max_tokens, ctx.config.role_timeouts,
    )

    if final_decision == "reject":
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="final_judge_rejected",
            scores=ctx.admission_scores,
        )

    target_status = "active" if final_decision == "accept_active" else "candidate"
    return replace(ctx, final_decision=final_decision, target_status=target_status)


def run_merge_planner(
    ctx: CandidateContext, db: Db, llm: Any
) -> CandidateContext | ColdPipelineResult:
    """Merge planner stage: check new rule against existing rules.

    Returns CandidateContext with merge_op/merge_info set,
    or ColdPipelineResult on reject_new.

    Note: split_required is returned as a normal CandidateContext — the caller
    (pipeline orchestrator) is responsible for handling it via _handle_split_required.
    """
    if ctx.rule_data is None:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="merge_planner_no_rule_data",
            scores=ctx.admission_scores,
        )
    rule_data = ctx.rule_data
    model_id = resolve_model_id(
        "merge_planner", ctx.config.role_models, ctx.config.default_model
    )

    merge_op, merge_info = _run_merge_planner(
        db, llm, rule_data, model_id,
        ctx.config.role_max_tokens, ctx.config.role_timeouts,
        project_id=ctx.project_id,
    )

    if merge_op == "reject_new":
        rationale = merge_info.get("merge_rationale", "")
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason=f"merge_planner_reject_new: {rationale}",
            scores=ctx.admission_scores,
        )

    return replace(ctx, merge_op=merge_op, merge_info=merge_info)
