"""Cold-path pipeline orchestration for the autonomous rule quality flywheel.

Implements sections 6.1-6.7 of the flywheel plan. Coordinates LLM roles
(extractor, admission judge, rewriter, final judge, merge planner, synthetic
eval generator) and deterministic policy gates to produce durable rules.

Pipeline invariants:
- Rejected candidates are never stored as durable rules.
- Failed role outputs remain in job state only.
- Matcher compilation is required before insertion.
- source_origin restrictions are enforced for external material.
- CAS-style version checks protect against concurrent mutations.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from typing import Any

from ..db import Db, dumps_json
from ..errors import LlmError
from ..events.observability import write_error, write_event
from ..policy import (
    SourceOrigin,
)
from ..search.idf_stats import IdfPoolStats, build_idf_stats
from ..utils.logging import get_logger
from ..utils.time import now_iso
from ._constants import _MAX_SPLIT_DEPTH
from ._llm_call import (
    CircuitBreakerOpenError,
    call_llm_role as _call_llm_role,
    prompt_text as _prompt_text,
    role_max_tokens as _role_max_tokens,
    role_timeout as _role_timeout,
)
from .roles import (
    resolve_model_id,
)
from .stages import (
    CandidateContext,
    PipelineConfig,
    run_admission,
    run_build_rule_data,
    run_compile_matcher,
    run_fast_lane_check,
    run_final_judge,
    run_fingerprint_check,
    run_insert_or_merge,
    run_merge_planner,
    run_synthetic_eval as run_synthetic_eval_stage,
)

log = get_logger("nokori.cold.pipeline")


# ---------------------------------------------------------------------------
# Pipeline orchestration — stage-based
# ---------------------------------------------------------------------------

STAGE_CHAIN: list[tuple[str, Any, bool]] = [
    ("admission", run_admission, True),
    ("build_rule_data", run_build_rule_data, True),
    ("final_judge", run_final_judge, True),
    ("merge_planner", run_merge_planner, True),
    ("fingerprint_check", run_fingerprint_check, False),
    ("compile_matcher", run_compile_matcher, False),
    ("synthetic_eval", run_synthetic_eval_stage, True),
    ("fast_lane_check", run_fast_lane_check, False),
    ("insert_or_merge", run_insert_or_merge, False),
]

_CHECKPOINT_PIPELINE_VERSION = "1.0.0"


def _stage_index(stage_name: str) -> int:
    for i, (name, _, _cp) in enumerate(STAGE_CHAIN):
        if name == stage_name:
            return i
    return -1


def _write_checkpoint(
    db: Db,
    transcript_ref: str,
    segment_hash: str | None,
    stage_name: str,
    ctx: CandidateContext,
) -> None:
    """Persist checkpoint after a successful stage."""
    serializable_fields = {
        "extractor_output": ctx.extractor_output,
        "transcript_ref": ctx.transcript_ref,
        "source_origin": ctx.source_origin,
        "project_id": ctx.project_id,
        "admission_decision": ctx.admission_decision,
        "admission_scores": ctx.admission_scores,
        "rule_data": ctx.rule_data,
        "final_decision": ctx.final_decision,
        "target_status": ctx.target_status,
        "merge_op": ctx.merge_op,
        "merge_info": ctx.merge_info,
        "fingerprint_block": ctx.fingerprint_block,
        "synthetic_passed": ctx.synthetic_passed,
        "adversarial_failures": ctx.adversarial_failures,
        "synthetic_eval_skipped": ctx.synthetic_eval_skipped,
        "fast_lane_passed": ctx.fast_lane_passed,
    }
    checkpoint = dumps_json(
        {
            "pipeline_version": _CHECKPOINT_PIPELINE_VERSION,
            "stage": stage_name,
            "context": serializable_fields,
        }
    )
    if segment_hash:
        with db.transaction() as tx:
            tx.execute(
                "UPDATE transcript_ingest_jobs SET pipeline_checkpoint = ?, updated_at = ? "
                "WHERE segment_hash = ? AND status = 'pending'",
                (checkpoint, now_iso(), segment_hash),
            )


def _load_checkpoint(
    db: Db,
    segment_hash: str | None,
) -> tuple[str, dict] | None:
    """Load checkpoint for a pending ingest job. Returns (stage_name, context_fields) or None."""
    if not segment_hash:
        return None
    row = db.fetchone(
        "SELECT pipeline_checkpoint FROM transcript_ingest_jobs "
        "WHERE segment_hash = ? AND status = 'pending' AND pipeline_checkpoint IS NOT NULL",
        (segment_hash,),
    )
    if row is None or row["pipeline_checkpoint"] is None:
        return None
    try:
        data = json.loads(row["pipeline_checkpoint"])
        if data.get("pipeline_version") != _CHECKPOINT_PIPELINE_VERSION:
            return None
        return data["stage"], data["context"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _run_pipeline_staged(
    db: Db,
    llm: Any,
    ctx: CandidateContext,
    segment_hash: str | None = None,
) -> ColdPipelineResult:
    """Execute the cold pipeline as a chain of typed stages with checkpoint persistence."""
    checkpoint = _load_checkpoint(db, segment_hash)
    start_idx = 0
    if checkpoint is not None:
        checkpoint_stage, checkpoint_fields = checkpoint
        idx = _stage_index(checkpoint_stage)
        if idx >= 0:
            start_idx = idx + 1
            ctx = replace(
                ctx,
                admission_decision=checkpoint_fields.get("admission_decision"),
                admission_scores=checkpoint_fields.get("admission_scores"),
                rule_data=checkpoint_fields.get("rule_data"),
                final_decision=checkpoint_fields.get("final_decision"),
                target_status=checkpoint_fields.get("target_status"),
                merge_op=checkpoint_fields.get("merge_op"),
                merge_info=checkpoint_fields.get("merge_info"),
                fingerprint_block=checkpoint_fields.get("fingerprint_block"),
                synthetic_passed=checkpoint_fields.get("synthetic_passed", False),
                adversarial_failures=checkpoint_fields.get("adversarial_failures", 0),
                synthetic_eval_skipped=checkpoint_fields.get("synthetic_eval_skipped", False),
                fast_lane_passed=checkpoint_fields.get("fast_lane_passed", False),
            )
            log.info("checkpoint resume: stage=%s start_idx=%d", checkpoint_stage, start_idx)

    for _i, (name, stage_fn, should_checkpoint) in enumerate(
        STAGE_CHAIN[start_idx:], start=start_idx
    ):
        t0 = time.monotonic()
        result = stage_fn(ctx, db, llm)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info("stage=%s duration_ms=%d", name, elapsed_ms)

        if isinstance(result, ColdPipelineResult):
            return result

        ctx = result

        # Handle split_required after merge_planner (before fingerprint check)
        if name == "merge_planner" and ctx.merge_op == "split_required":
            _write_checkpoint(db, ctx.transcript_ref, segment_hash, name, ctx)
            return ColdPipelineResult(
                status="pending_split",
                rule_id=None,
                rejection_reason=None,
                scores=ctx.admission_scores,
            )

        if should_checkpoint:
            _write_checkpoint(db, ctx.transcript_ref, segment_hash, name, ctx)

    raise RuntimeError("pipeline exhausted stages without terminal result")


# ---------------------------------------------------------------------------
# Result dataclass (re-exported from _result to avoid circular imports)
# ---------------------------------------------------------------------------

from ._result import ColdPipelineResult  # noqa: E402

# ---------------------------------------------------------------------------
# Public orchestration entry point
# ---------------------------------------------------------------------------


def run_cold_pipeline(
    db: Db,
    llm: Any,
    transcript_ref: str,
    extractor_output: dict[str, Any],
    *,
    role_models: dict[str, str] | None = None,
    default_model: str | None = None,
    role_max_tokens: dict[str, int] | None = None,
    role_timeouts: dict[str, int] | None = None,
    idf_stats: IdfPoolStats | None = None,
    global_adversarial_cases: list[dict[str, Any]] | None = None,
    source_origin: SourceOrigin = "transcript_extraction",
    project_id: str | None = None,
) -> ColdPipelineResult:
    """Run the full cold pipeline for one extractor output candidate.

    Pipeline stages (plan sections 6.1-6.7):
      a) Admission Judge: evaluate quality scores, decide accept/revise/reject
      b) If revise: Rule Rewriter -> re-evaluate with Final Judge
      c) If accept: Final Judge decides accept_active / accept_candidate / reject
      d) Merge Planner: check against existing rules
      e) Archived fingerprint check
      f) Compile matcher (must succeed for durable insertion)
      g) Synthetic eval (if targeting active)
      h) Final admission policy (section 6.7)

    Args:
        db: Database handle.
        llm: LLM client with a call(model, system, user, max_tokens, timeout) interface.
        transcript_ref: Reference to the source transcript segment.
        extractor_output: Validated extractor role output dict.
        role_models: Per-role model id overrides.
        default_model: Fallback model id.
        idf_stats: Pre-built IDF pool stats. Built on demand if None.
        global_adversarial_cases: Checked-in adversarial eval cases.
        source_origin: Origin type for this candidate.
        project_id: Optional project scope for transcript-derived rules.

    Returns:
        ColdPipelineResult with final status, rule_id (if inserted), rejection reason, and scores.
    """
    trigger_preview = str(extractor_output.get("trigger", ""))[:60]
    trigger_preview_zh = str(extractor_output.get("trigger_zh", ""))[:60] or None
    try:
        result = _run_cold_pipeline_inner(
            db,
            llm,
            transcript_ref,
            extractor_output,
            role_models=role_models,
            default_model=default_model,
            role_max_tokens=role_max_tokens,
            role_timeouts=role_timeouts,
            idf_stats=idf_stats,
            global_adversarial_cases=global_adversarial_cases,
            source_origin=source_origin,
            project_id=project_id,
        )
        log.info(
            "cold_pipeline done: trigger=%r status=%s rule_id=%s rejection=%s",
            trigger_preview,
            result.status,
            result.rule_id,
            result.rejection_reason,
        )
        write_event(
            db,
            source="cold_pipeline",
            outcome=result.status,
            details={
                "trigger_preview": trigger_preview,
                "trigger_preview_zh": trigger_preview_zh,
                "rule_id": result.rule_id,
                "rejection_reason": result.rejection_reason,
                "scores": result.scores,
                "transcript_ref": transcript_ref,
                "source_origin": source_origin,
                "project_id": project_id,
            },
        )
        return result
    except CircuitBreakerOpenError as e:
        # Spec section 5.2: paused jobs remain pending, not rejected
        log.warning("cold_pipeline pending (circuit breaker): trigger=%r %s", trigger_preview, e)
        write_error(
            db,
            source="cold_pipeline",
            role="system",
            error_type="circuit_breaker",
            message=str(e),
            details={"trigger_preview": trigger_preview},
        )
        return ColdPipelineResult(
            status="pending",
            rule_id=None,
            rejection_reason=f"circuit_breaker_pending: {e}",
            scores=None,
        )
    except (RuntimeError, OSError, TimeoutError, ConnectionError, ValueError, LlmError) as e:
        # Spec section 13: failed role calls leave jobs pending for retry
        log.warning(
            "cold_pipeline pending (role failure): trigger=%r %s: %s",
            trigger_preview,
            type(e).__name__,
            e,
        )
        if isinstance(e, TimeoutError):
            error_type = "timeout"
        elif isinstance(e, ConnectionError):
            error_type = "connection"
        elif isinstance(e, OSError):
            error_type = "io"
        elif isinstance(e, ValueError):
            error_type = "validation"
        elif isinstance(e, LlmError):
            error_type = "llm"
        else:
            error_type = "runtime"
        write_error(
            db,
            source="cold_pipeline",
            role="system",
            error_type=error_type,
            message=f"{type(e).__name__}: {e}",
            details={"trigger_preview": trigger_preview},
        )
        return ColdPipelineResult(
            status="pending",
            rule_id=None,
            rejection_reason=f"role_failure_pending: {type(e).__name__}: {e}",
            scores=None,
        )


def _run_cold_pipeline_inner(
    db: Db,
    llm: Any,
    transcript_ref: str,
    extractor_output: dict[str, Any],
    *,
    role_models: dict[str, str] | None = None,
    default_model: str | None = None,
    role_max_tokens: dict[str, int] | None = None,
    role_timeouts: dict[str, int] | None = None,
    idf_stats: IdfPoolStats | None = None,
    global_adversarial_cases: list[dict[str, Any]] | None = None,
    source_origin: SourceOrigin = "transcript_extraction",
    project_id: str | None = None,
) -> ColdPipelineResult:
    config = PipelineConfig(
        role_models=role_models,
        default_model=default_model,
        role_max_tokens=role_max_tokens,
        role_timeouts=role_timeouts,
    )
    ctx = CandidateContext(
        extractor_output=extractor_output,
        transcript_ref=transcript_ref,
        source_origin=source_origin,
        project_id=project_id,
        config=config,
        idf_stats=idf_stats,
        global_adversarial_cases=global_adversarial_cases,
    )

    segment_text = f"{transcript_ref}::{extractor_output.get('trigger', '')}::{extractor_output.get('action', '')}"
    segment_hash = hashlib.sha256(segment_text.encode("utf-8")).hexdigest()[:16]

    result = _run_pipeline_staged(db, llm, ctx, segment_hash=segment_hash)

    # Handle split_required: delegate to split handler
    if result.status == "pending_split":
        checkpoint = _load_checkpoint(db, segment_hash)
        rule_data = None
        scores = None
        if checkpoint is not None:
            _, cp_fields = checkpoint
            rule_data = cp_fields.get("rule_data")
            scores = cp_fields.get("admission_scores")
        if rule_data is None:
            return result

        split_results = _handle_split_required(
            db,
            llm,
            rule_data,
            role_models,
            default_model,
            transcript_ref,
            source_origin,
            idf_stats,
            global_adversarial_cases,
            role_max_tokens,
            role_timeouts,
            project_id,
        )
        if split_results:
            return split_results[0]
        return ColdPipelineResult(
            status="pending_split",
            rule_id=None,
            rejection_reason="split_rewrite_failed",
            scores=scores,
        )

    return result


# ---------------------------------------------------------------------------
# Stage implementations (legacy — kept for split handler)
# ---------------------------------------------------------------------------


def _handle_split_required(
    db: Db,
    llm: Any,
    rule_data: dict[str, Any],
    role_models: dict[str, str] | None,
    default_model: str | None,
    transcript_ref: str,
    source_origin: SourceOrigin,
    idf_stats: IdfPoolStats | None,
    global_adversarial_cases: list[dict[str, Any]] | None,
    role_max_tokens: dict[str, int] | None = None,
    role_timeouts: dict[str, int] | None = None,
    project_id: str | None = None,
    _split_depth: int = 0,
) -> list[ColdPipelineResult]:
    if _split_depth >= _MAX_SPLIT_DEPTH:
        log.warning("split_required recursion depth exceeded; rejecting")
        return []
    """Handle split_required by invoking rewriter to produce sub-rules, then re-process each.

    Spec section 6.7: 'return to rewrite/split if merge operation is split_required'.
    The rewriter is asked to split the rule into independent sub-rules.
    """
    rewriter_model = resolve_model_id("rule_rewriter", role_models, default_model)

    system_prompt = (
        "You are a rule rewriter for an autonomous memory system. "
        "This rule has been flagged as containing multiple independent triggers/actions. "
        "Split it into separate, focused rules. Each must have its own trigger, action, "
        "required_concept_groups, and excluded_contexts. "
        'Output strict JSON: {"split_rules": [{...}, {...}]} matching rule_rewriter schema per sub-rule.'
    )

    rule_text = _prompt_text(json.dumps(rule_data, ensure_ascii=False, indent=2))
    user_prompt = (
        f"<rule_to_split>\n{rule_text}\n</rule_to_split>\n\nSplit into independent sub-rules."
    )

    def _validate_split_response(raw: str) -> None:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("split_required response must be an object")
        split_rules = parsed.get("split_rules")
        if not isinstance(split_rules, list) or not split_rules:
            raise ValueError("split_required response missing non-empty split_rules")

    try:
        response = _call_llm_role(
            db,
            llm,
            role="rule_rewriter",
            model_id=rewriter_model,
            system=system_prompt,
            user=user_prompt,
            max_tokens=_role_max_tokens("rule_rewriter", role_max_tokens),
            timeout=_role_timeout("rule_rewriter", role_timeouts),
            validate_response=_validate_split_response,
        )
        data = json.loads(response)
        sub_rules = data.get("split_rules", []) if isinstance(data, dict) else []
        if not sub_rules:
            return []
    except (CircuitBreakerOpenError, ValueError):
        raise

    # Re-process each sub-rule through the pipeline
    results: list[ColdPipelineResult] = []
    for sub_rule in sub_rules[:3]:
        sub_extractor = {
            "trigger": sub_rule.get("trigger_canonical", ""),
            "action": sub_rule.get("action_instruction", ""),
            "behavior": "",
            "evidence_quotes": rule_data.get("evidence_quotes", ["split from parent"]),
            "non_generalization_boundaries": [],
            "required_concepts": [],
            "excluded_contexts": [],
            "search_terms": {},
            "trigger_variants": [],
            "trigger_variants_zh": [],
            "near_miss_examples": [],
            "severity": sub_rule.get("severity", "reminder"),
            "domain_tags": sub_rule.get("scope", {}).get("domain_tags", []),
            "tool_tags": sub_rule.get("scope", {}).get("tool_tags", []),
            "file_or_path_patterns": sub_rule.get("scope", {}).get("file_or_path_patterns", []),
        }
        result = run_cold_pipeline(
            db,
            llm,
            transcript_ref,
            sub_extractor,
            role_models=role_models,
            default_model=default_model,
            role_max_tokens=role_max_tokens,
            role_timeouts=role_timeouts,
            idf_stats=idf_stats,
            global_adversarial_cases=global_adversarial_cases,
            source_origin=source_origin,
            project_id=project_id,
        )
        results.append(result)

    return results


def _build_idf_stats_from_db(db: Db) -> IdfPoolStats:
    """Build IDF stats from current active+trusted rule pool."""
    from ..db import fetch_rules

    rules = fetch_rules(db, statuses=("active", "trusted"))
    return build_idf_stats(rules)
