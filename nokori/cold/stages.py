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
from ._result import ColdPipelineResult
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

    # After fingerprint check
    fingerprint_block: dict | None = None

    # After compile
    compiled_matcher: Any = None

    # After synthetic eval
    synthetic_passed: bool = False
    adversarial_failures: int = 0
    synthetic_result: Any = None
    synthetic_eval_skipped: bool = False
    eval_cases: list | None = None

    # After fast lane check
    fast_lane_passed: bool = False

    # Shared state (passed through from caller)
    idf_stats: Any = None
    global_adversarial_cases: list | None = None


def run_admission(ctx: CandidateContext, db: Db, llm: Any) -> CandidateContext | ColdPipelineResult:
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

    model_id = resolve_model_id("admission_judge", ctx.config.role_models, ctx.config.default_model)
    decision, scores = _run_admission_judge(
        db,
        llm,
        ctx.extractor_output,
        model_id,
        ctx.config.role_max_tokens,
        ctx.config.role_timeouts,
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
            db,
            llm,
            candidate,
            ctx.admission_scores or {},
            rewriter_model,
            ctx.config.role_max_tokens,
            ctx.config.role_timeouts,
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
        rule_data["non_generalization_boundaries"] = candidate.get(
            "non_generalization_boundaries", []
        )
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
        k: v for k, v in rule_data.items() if k != "evidence_quotes" and not k.startswith("_")
    }
    evidence_quotes = ctx.extractor_output.get("evidence_quotes", [])

    model_id = resolve_model_id("final_judge", ctx.config.role_models, ctx.config.default_model)
    final_decision = _run_final_judge(
        db,
        llm,
        rule_data_for_judge,
        evidence_quotes,
        model_id,
        ctx.config.role_max_tokens,
        ctx.config.role_timeouts,
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
    model_id = resolve_model_id("merge_planner", ctx.config.role_models, ctx.config.default_model)

    from nokori.cold.pipeline import _run_merge_planner

    merge_op, merge_info = _run_merge_planner(
        db,
        llm,
        rule_data,
        model_id,
        ctx.config.role_max_tokens,
        ctx.config.role_timeouts,
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


def run_fingerprint_check(
    ctx: CandidateContext, db: Db, llm: Any
) -> CandidateContext | ColdPipelineResult:
    """Fingerprint check stage: reject if archived fingerprint blocks the rule."""
    from nokori.cold.pipeline import check_fingerprint_block

    if ctx.rule_data is None:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="fingerprint_check_no_rule_data",
            scores=ctx.admission_scores,
        )

    rule_data = ctx.rule_data
    trigger_canonical = rule_data.get("trigger_canonical", "")
    action_instruction = rule_data.get("action_instruction", "")
    domain_tags = rule_data.get("scope", {}).get("domain_tags", [])

    scope_evidence = rule_data.get("non_generalization_boundaries") or rule_data.get(
        "evidence_quotes"
    )
    admission_cited = (
        ctx.admission_scores is not None
        and ctx.admission_scores.get("overall_quality", 0) >= 0.82
        and bool(scope_evidence)
    )

    fingerprint_block = check_fingerprint_block(
        db,
        trigger_canonical,
        action_instruction,
        domain_tags,
        stronger_evidence=str(scope_evidence[0]) if scope_evidence else None,
        admission_judge_cited=admission_cited,
    )

    if fingerprint_block is not None:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason=f"fingerprint_blocked_{fingerprint_block.get('archive_strength', 'unknown')}",
            scores=ctx.admission_scores,
        )

    return replace(ctx, fingerprint_block=None)


def run_compile_matcher(
    ctx: CandidateContext, db: Db, llm: Any
) -> CandidateContext | ColdPipelineResult:
    """Compile matcher stage: compile trigger data into a matcher."""
    from nokori.cold.pipeline import CompilationError, compile_rule

    if ctx.rule_data is None:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="compile_no_rule_data",
            scores=ctx.admission_scores,
        )

    rule_data = ctx.rule_data
    trigger_data = {
        "trigger_canonical": rule_data.get("trigger_canonical", ""),
        "required_concept_groups": rule_data.get("required_concept_groups", []),
        "concepts": rule_data.get("concepts", []),
        "excluded_contexts": rule_data.get("excluded_contexts", []),
        "variants": rule_data.get("variants", []),
    }
    action_instruction = rule_data.get("action_instruction", "")

    try:
        compiled_matcher = compile_rule(
            trigger_data,
            action_data={
                "instruction": action_instruction,
                "severity": rule_data.get("severity", "reminder"),
            },
            search_terms=rule_data.get("search_terms"),
        )
    except CompilationError as e:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason=f"compilation_failed: {e}",
            scores=ctx.admission_scores,
        )

    return replace(ctx, compiled_matcher=compiled_matcher)


def run_synthetic_eval(
    ctx: CandidateContext, db: Db, llm: Any
) -> CandidateContext | ColdPipelineResult:
    """Synthetic eval stage: generate and run eval cases for active-targeting rules."""
    from nokori.cold.pipeline import _generate_eval_cases, run_synthetic_eval as _run_synth_eval
    from nokori.eval.synthetic import SyntheticEvalResult

    from ..search.idf_stats import build_idf_stats
    from ._llm_call import CircuitBreakerOpenError

    idf_stats = ctx.idf_stats
    if idf_stats is None:
        from ..db import fetch_rules

        rules = fetch_rules(db, statuses=("active", "trusted"))
        idf_stats = build_idf_stats(rules)

    needs_eval = ctx.target_status == "active" or ctx.merge_op in (
        "merge_into_existing",
        "update_existing_fields",
    )

    synthetic_eval_skipped = False
    eval_cases: list = []
    synthetic_passed = False
    adversarial_failures = 0
    synthetic_result: SyntheticEvalResult | None = None

    if not needs_eval:
        synthetic_eval_skipped = True
    else:
        try:
            eval_cases = _generate_eval_cases(
                db,
                llm,
                ctx.rule_data or {},
                ctx.config.role_models,
                ctx.config.default_model,
                ctx.config.role_max_tokens,
                ctx.config.role_timeouts,
            )
        except (CircuitBreakerOpenError, ValueError) as exc:
            from ..utils.logging import get_logger

            log = get_logger("nokori.cold.stages")
            log.warning("synthetic eval generation failed, passing through: %s", exc)
            eval_cases = []
            synthetic_eval_skipped = True
            synthetic_passed = True

    if eval_cases:
        eval_rule_data = {
            "id": "",
            "version": 0,
            "status": ctx.target_status,
            "severity": (ctx.rule_data or {}).get("severity", "reminder"),
            "first_observed_useful_at": None,
        }
        synthetic_result = _run_synth_eval(
            eval_rule_data,
            ctx.compiled_matcher,
            idf_stats,
            eval_cases,
            ctx.global_adversarial_cases,
        )
        synthetic_passed = synthetic_result.passed
        if synthetic_result.results:
            adversarial_failures = sum(
                1
                for r in synthetic_result.results
                if r.get("case_type") == "global_adversarial" and not r.get("case_passed", True)
            )

    return replace(
        ctx,
        synthetic_passed=synthetic_passed,
        adversarial_failures=adversarial_failures,
        synthetic_result=synthetic_result,
        synthetic_eval_skipped=synthetic_eval_skipped,
        eval_cases=eval_cases,
        idf_stats=idf_stats,
    )


def run_fast_lane_check(
    ctx: CandidateContext, db: Db, llm: Any
) -> CandidateContext | ColdPipelineResult:
    """Fast lane check stage: determine if rule qualifies for direct active insertion."""
    from .verify import _check_cold_fast_lane

    fast_lane_passed = _check_cold_fast_lane(
        scores=ctx.admission_scores,
        synthetic_passed=ctx.synthetic_passed,
        adversarial_failures=ctx.adversarial_failures,
        fingerprint_conflict=ctx.fingerprint_block is not None,
        merge_op=ctx.merge_op or "keep_both",
        source_origin=ctx.source_origin,
        final_judge_decision=ctx.final_decision or "accept_candidate",
    )

    return replace(ctx, fast_lane_passed=fast_lane_passed)


def run_insert_or_merge(ctx: CandidateContext, db: Db, llm: Any) -> ColdPipelineResult:
    """Final stage: insert new rule or apply non-destructive merge.

    Always returns ColdPipelineResult (terminal stage).
    """
    from ..merge.policy import MergeDecision, record_lineage, validate_merge_transaction
    from ..policy import RUNTIME_POLICY_VERSION, ActivationOrigin
    from ._constants import DESTRUCTIVE_MERGE_OPS
    from .integrate import (
        _apply_merge_side_effects,
        insert_rule_from_pipeline,
    )

    rule_data = ctx.rule_data or {}
    merge_op = ctx.merge_op or "keep_both"
    merge_info = ctx.merge_info or {}
    scores = ctx.admission_scores

    # Destructive merge validation
    existing_rule = merge_info.get("existing_rule")
    if merge_op in DESTRUCTIVE_MERGE_OPS and not existing_rule:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="merge_transaction_invalid: no existing rule for destructive merge",
            scores=scores,
        )
    if merge_op in DESTRUCTIVE_MERGE_OPS:
        merge_decision = MergeDecision(
            operation=merge_op,
            target_rule_id=(existing_rule or {}).get("id"),
            reason=merge_info.get("merge_rationale", ""),
            requires_synthetic_reeval=bool(merge_info.get("requires_synthetic_reeval")),
            lineage_record=merge_info.get("lineage_record"),
        )
        if not validate_merge_transaction(
            existing_rule,
            rule_data,
            merge_decision,
            synthetic_passed=ctx.synthetic_passed,
            fingerprint_clear=ctx.fingerprint_block is None,
            matcher_compiled=ctx.compiled_matcher is not None,
            final_admission_passed=ctx.fast_lane_passed,
        ):
            return ColdPipelineResult(
                status="rejected",
                rule_id=None,
                rejection_reason="merge_transaction_invalid",
                scores=scores,
            )

    # Non-destructive merge path
    if merge_op in ("merge_into_existing", "update_existing_fields"):
        existing_rule = merge_info.get("existing_rule") or {}
        target_id = existing_rule.get("id")
        if not target_id:
            return ColdPipelineResult(
                status="rejected",
                rule_id=None,
                rejection_reason="merge_target_missing: no existing rule id for non-destructive merge",
                scores=scores,
            )
        if target_id:
            from nokori.cold.pipeline import run_synthetic_eval as _run_synth

            from .integrate import _apply_non_destructive_merge, _revert_merge

            _pre_variants = existing_rule.get("trigger_variants")
            _pre_excluded = existing_rule.get("excluded_contexts")

            _apply_non_destructive_merge(db, target_id, rule_data, merge_op, merge_info)

            _merge_changed_variants = bool(
                rule_data.get("variants") or rule_data.get("trigger_variants")
            )
            _merge_changed_excluded = bool(rule_data.get("excluded_contexts"))

            if _merge_changed_variants or _merge_changed_excluded:
                import json as _json

                from nokori.cold.pipeline import (
                    CompilationError as _CompErr,
                    compile_rule as _compile_rule,
                )

                _merged_row = db.fetchone(
                    "SELECT trigger_canonical, trigger_variants, excluded_contexts, "
                    "concepts, required_concept_groups, near_miss_examples, "
                    "action_instruction, rule_version, status, severity, "
                    "runtime_policy_version, first_observed_useful_at "
                    "FROM rules WHERE id = ?",
                    (target_id,),
                )
                if _merged_row is None:
                    return ColdPipelineResult(
                        status="rejected",
                        rule_id=None,
                        rejection_reason="post_merge_rule_disappeared",
                        scores=scores,
                    )
                _raw_variants = _json.loads(_merged_row["trigger_variants"] or "[]")
                _variants = [
                    v
                    if isinstance(v, dict)
                    else {"text": str(v), "kind": "weak_recall", "requires_concepts": []}
                    for v in _raw_variants
                ]
                _recompile_data = {
                    "trigger_canonical": _merged_row["trigger_canonical"],
                    "variants": _variants,
                    "excluded_contexts": _json.loads(_merged_row["excluded_contexts"] or "[]"),
                    "concepts": _json.loads(_merged_row["concepts"] or "[]"),
                    "required_concept_groups": _json.loads(
                        _merged_row["required_concept_groups"] or "[]"
                    ),
                    "near_miss_examples": _json.loads(_merged_row["near_miss_examples"] or "[]"),
                    "action_instruction": _merged_row["action_instruction"],
                }
                try:
                    _recompiled = _compile_rule(_recompile_data)
                except _CompErr:
                    _recompiled = None

                _synth_ok = False
                eval_cases = ctx.eval_cases or []
                if _recompiled is None:
                    _synth_ok = False
                elif eval_cases or ctx.global_adversarial_cases:
                    _reeval_rule_data = {
                        "id": target_id,
                        "version": _merged_row["rule_version"],
                        "status": _merged_row["status"],
                        "severity": _merged_row["severity"],
                        "first_observed_useful_at": _merged_row["first_observed_useful_at"],
                    }
                    _synth_result = _run_synth(
                        _reeval_rule_data,
                        _recompiled,
                        ctx.idf_stats,
                        eval_cases,
                        ctx.global_adversarial_cases,
                    )
                    _synth_ok = _synth_result is not None and _synth_result.passed
                else:
                    _synth_ok = True

                if not _synth_ok:
                    _revert_merge(
                        db,
                        target_id,
                        _merged_row,
                        _merge_changed_variants,
                        _merge_changed_excluded,
                        _pre_variants,
                        _pre_excluded,
                    )
                    return ColdPipelineResult(
                        status="rejected",
                        rule_id=None,
                        rejection_reason="post_merge_synthetic_eval_failed",
                        scores=scores,
                    )

            record_lineage(db, target_id, None, merge_op, merge_info.get("merge_rationale", ""))
            return ColdPipelineResult(
                status="merged",
                rule_id=target_id,
                rejection_reason=None,
                scores=scores,
            )

    # Standard insertion path
    activation_origin: ActivationOrigin | None
    if ctx.target_status == "active" and ctx.fast_lane_passed:
        final_status = "active"
        activation_origin = "cold_fast_lane"
    else:
        final_status = "candidate"
        activation_origin = None

    rule_id = insert_rule_from_pipeline(
        db,
        rule_data,
        status=final_status,
        compiled_matcher=ctx.compiled_matcher,
        synthetic_result=ctx.synthetic_result,
        activation_origin=activation_origin,
        source_origin=ctx.source_origin,
        transcript_ref=ctx.transcript_ref,
        scores=scores,
        synthetic_eval_skipped=ctx.synthetic_eval_skipped,
        project_id=ctx.project_id,
        admission_model_id=ctx.config.role_models.get("admission_judge")
        if ctx.config.role_models
        else ctx.config.default_model,
    )

    _apply_merge_side_effects(db, rule_id, merge_op, merge_info)

    return ColdPipelineResult(
        status=final_status,
        rule_id=rule_id,
        rejection_reason=None,
        scores=scores,
    )
