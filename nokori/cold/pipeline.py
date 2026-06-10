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

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..archive.fingerprints import check_fingerprint_block
from ..db import Db, dumps_json
from ..errors import LlmError
from ..matcher.compiler import CompilationError, CompiledMatcher, compile_rule
from ..eval.synthetic import SyntheticEvalResult, run_synthetic_eval
from ..policy import (
    RUNTIME_POLICY_VERSION,
    SourceOrigin,
    ActivationOrigin,
)
from ..merge.policy import (
    MergeDecision,
    record_lineage,
    validate_merge_transaction,
)
from ..search.idf_stats import IdfPoolStats, build_idf_stats
from .roles import (
    PROMPT_VERSIONS,
    resolve_model_id,
    validate_role_output,
)
from ..events.observability import write_error, write_event
from ..utils.logging import get_logger

from ._constants import DESTRUCTIVE_MERGE_OPS, PIPELINE_VERSION, _MAX_SPLIT_DEPTH
from ._llm_call import (
    CircuitBreakerOpenError,
    call_llm_role as _call_llm_role,
    prompt_text as _prompt_text,
    role_max_tokens as _role_max_tokens,
    role_timeout as _role_timeout,
)
from .qualify import (
    _run_admission_judge,
    _enforce_admission_policy,
    _run_rewriter,
    _run_final_judge,
    _candidate_to_rule_data,
    _ensure_rule_data_variants,
    _draft_concept_groups,
    _draft_concepts,
    _draft_excluded_contexts,
    _draft_variants,
)
from .integrate import (
    _run_merge_planner,
    _operation_to_relation_shape,
    _get_existing_rules_for_merge,
    _apply_merge_side_effects,
    _apply_non_destructive_merge,
    _build_trigger_data,
    _get_rule_data_for_fingerprint,
    insert_rule_from_pipeline,
)
from .verify import (
    _generate_eval_cases,
    _check_cold_fast_lane,
)

log = get_logger("nokori.cold.pipeline")


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColdPipelineResult:
    """Outcome of a cold pipeline run for one candidate."""

    status: str  # "candidate", "active", "rejected", "pending_rewrite", "pending_split"
    rule_id: str | None
    rejection_reason: str | None
    scores: dict | None


# ---------------------------------------------------------------------------
# Public orchestration entry point
# ---------------------------------------------------------------------------


def run_cold_pipeline(
    db: Db,
    llm,
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
            db, llm, transcript_ref, extractor_output,
            role_models=role_models, default_model=default_model,
            role_max_tokens=role_max_tokens, role_timeouts=role_timeouts,
            idf_stats=idf_stats, global_adversarial_cases=global_adversarial_cases,
            source_origin=source_origin,
            project_id=project_id,
        )
        log.info(
            "cold_pipeline done: trigger=%r status=%s rule_id=%s rejection=%s",
            trigger_preview, result.status, result.rule_id, result.rejection_reason,
        )
        write_event(
            db, source="cold_pipeline",
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
            db, source="cold_pipeline", role="system",
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
        log.warning("cold_pipeline pending (role failure): trigger=%r %s: %s", trigger_preview, type(e).__name__, e)
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
            db, source="cold_pipeline", role="system",
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
    llm,
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
    candidate = extractor_output

    # --- Pre-check: evidence_quotes must be non-empty (section 6.1) ---
    evidence_quotes = candidate.get("evidence_quotes", [])
    if not evidence_quotes:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="no_transcript_evidence",
            scores=None,
        )

    # --- Stage a: Admission Judge ---
    admission_model = resolve_model_id("admission_judge", role_models, default_model)
    decision, scores = _run_admission_judge(
        db, llm, candidate, admission_model, role_max_tokens, role_timeouts
    )

    if decision == "reject":
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="admission_judge_rejected",
            scores=scores,
        )

    # --- Stage b: Rewriter (if revise) ---
    rule_data: dict[str, Any]
    if decision == "revise":
        rewriter_model = resolve_model_id("rule_rewriter", role_models, default_model)
        rewritten = _run_rewriter(
            db, llm, candidate, scores, rewriter_model,
            role_max_tokens, role_timeouts,
        )
        if rewritten is None:
            return ColdPipelineResult(
                status="rejected",
                rule_id=None,
                rejection_reason="rewriter_failed",
                scores=scores,
            )
        rule_data = rewritten
        # Preserve fields from candidate that rewriter doesn't output
        rule_data["evidence_quotes"] = candidate.get("evidence_quotes", [])
        rule_data["trigger_canonical_zh"] = candidate.get("trigger_zh")
        rule_data["action_instruction_zh"] = candidate.get("action_zh")
        rule_data["trigger_variants_zh"] = candidate.get("trigger_variants_zh", [])
        rule_data["non_generalization_boundaries"] = candidate.get("non_generalization_boundaries", [])
        rule_data["near_miss_examples"] = candidate.get("near_miss_examples", [])
        rule_data["_rewritten"] = True
    else:
        # Build structured rule_data from extractor candidate for accepted path
        rule_data = _candidate_to_rule_data(candidate)
    rule_data = _ensure_rule_data_variants(rule_data)

    # --- Stage c: Final Judge ---
    # Strip evidence from rule_data so final_judge sees rule and evidence separately
    rule_data_for_judge = {k: v for k, v in rule_data.items() if k not in ("evidence_quotes", "_rewritten")}
    final_judge_model = resolve_model_id("final_judge", role_models, default_model)
    final_decision = _run_final_judge(
        db, llm, rule_data_for_judge, candidate.get("evidence_quotes", []), final_judge_model,
        role_max_tokens, role_timeouts,
    )

    if final_decision == "reject":
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason="final_judge_rejected",
            scores=scores,
        )

    # Target status from final judge
    target_status = "active" if final_decision == "accept_active" else "candidate"

    # --- Stage d: Merge Planner ---
    merge_planner_model = resolve_model_id("merge_planner", role_models, default_model)
    merge_op, merge_info = _run_merge_planner(
        db, llm, rule_data, merge_planner_model, role_max_tokens, role_timeouts,
        project_id=project_id,
    )

    if merge_op == "reject_new":
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason=f"merge_planner_reject_new: {merge_info.get('merge_rationale', '')}",
            scores=scores,
        )

    if merge_op == "split_required":
        # Spec section 6.7: return to rewrite/split, then re-process each part
        split_results = _handle_split_required(
            db, llm, rule_data, role_models, default_model,
            transcript_ref, source_origin, idf_stats, global_adversarial_cases,
            role_max_tokens, role_timeouts, project_id,
        )
        if split_results:
            return split_results[0]
        return ColdPipelineResult(
            status="pending_split",
            rule_id=None,
            rejection_reason="split_rewrite_failed",
            scores=scores,
        )

    # --- Stage e: Archived fingerprint check ---
    trigger_canonical = rule_data.get("trigger_canonical", "")
    action_instruction = rule_data.get("action_instruction", "")
    domain_tags = rule_data.get("scope", {}).get("domain_tags", [])

    # Pass admission judge acceptance so narrower-scope override path is reachable.
    # admission_judge_cited=True when the admission judge accepted the candidate
    # (overall_quality >= accept threshold 0.82), meaning it evaluated and endorsed
    # the scope difference — functionally equivalent to "citing the difference".
    scope_evidence = rule_data.get("non_generalization_boundaries") or rule_data.get("evidence_quotes")
    admission_cited = (
        scores is not None
        and scores.get("overall_quality", 0) >= 0.82
        and bool(scope_evidence)
    )

    fingerprint_block = check_fingerprint_block(
        db, trigger_canonical, action_instruction, domain_tags,
        stronger_evidence=str(scope_evidence[0]) if scope_evidence else None,
        admission_judge_cited=admission_cited,
    )
    fingerprint_conflict = fingerprint_block is not None

    # Spec section 6.7: "reject if archived fingerprint blocks the rule" (unconditional)
    if fingerprint_conflict:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason=f"fingerprint_blocked_{fingerprint_block.get('archive_strength', 'unknown')}",
            scores=scores,
        )

    # --- Stage f: Compile matcher ---
    trigger_data = _build_trigger_data(rule_data)
    try:
        compiled_matcher = compile_rule(
            trigger_data,
            action_data={"instruction": action_instruction, "severity": rule_data.get("severity", "reminder")},
            search_terms=rule_data.get("search_terms"),
        )
    except CompilationError as e:
        return ColdPipelineResult(
            status="rejected",
            rule_id=None,
            rejection_reason=f"compilation_failed: {e}",
            scores=scores,
        )

    # --- Stage g: Synthetic eval (active fast-lane + merge reeval) ---
    synthetic_passed = False
    adversarial_failures = 0
    synthetic_result: SyntheticEvalResult | None = None

    if idf_stats is None:
        idf_stats = _build_idf_stats_from_db(db)

    # Skip eval generation for candidates that don't merge into existing rules
    # (no active rule to protect). Merge reeval needs cases to validate changes.
    _needs_eval_cases = (
        target_status == "active"
        or merge_op in ("merge_into_existing", "update_existing_fields")
    )
    synthetic_eval_skipped = False
    if not _needs_eval_cases:
        eval_cases: list = []
        synthetic_eval_skipped = True
    else:
        try:
            eval_cases = _generate_eval_cases(
                db, llm, rule_data, role_models, default_model,
                role_max_tokens, role_timeouts,
            )
        except (CircuitBreakerOpenError, ValueError) as exc:
            log.warning("synthetic eval generation failed, passing through: %s", exc)
            eval_cases = []
            synthetic_eval_skipped = True
            synthetic_passed = True
    if eval_cases:
        eval_rule_data = {
            "id": "",
            "version": 0,
            "status": target_status,
            "severity": rule_data.get("severity", "reminder"),
            "first_observed_useful_at": None,
        }
        synthetic_result = run_synthetic_eval(
            eval_rule_data,
            compiled_matcher,
            idf_stats,
            eval_cases,
            global_adversarial_cases,
        )
        synthetic_passed = synthetic_result.passed
        # Count adversarial failures
        if synthetic_result.results:
            adversarial_failures = sum(
                1 for r in synthetic_result.results
                if r.get("case_type") == "global_adversarial" and not r.get("case_passed", True)
            )

    # --- Stage h: Final admission policy (section 6.7) ---
    fast_lane_passed = _check_cold_fast_lane(
        scores=scores,
        synthetic_passed=synthetic_passed,
        adversarial_failures=adversarial_failures,
        fingerprint_conflict=fingerprint_conflict,
        merge_op=merge_op,
        source_origin=source_origin,
        final_judge_decision=final_decision,
    )

    # Determine final status
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
            operation=merge_op,  # type: ignore[arg-type]
            target_rule_id=(existing_rule or {}).get("id"),
            reason=merge_info.get("merge_rationale", ""),
            requires_synthetic_reeval=bool(merge_info.get("requires_synthetic_reeval")),
            lineage_record=merge_info.get("lineage_record"),
        )
        if not validate_merge_transaction(
            existing_rule,
            rule_data,
            merge_decision,
            synthetic_passed=synthetic_passed,
            fingerprint_clear=not fingerprint_conflict,
            matcher_compiled=True,
            final_admission_passed=fast_lane_passed,
        ):
            return ColdPipelineResult(
                status="rejected",
                rule_id=None,
                rejection_reason="merge_transaction_invalid",
                scores=scores,
            )

    # --- Handle non-destructive merge ops that update existing rules ---
    if merge_op in ("merge_into_existing", "update_existing_fields"):
        existing_rule = merge_info.get("existing_rule") or {}
        target_id = existing_rule.get("id")
        if target_id:
            # Snapshot fields that require synthetic re-eval if changed (spec 6.5)
            _pre_variants = existing_rule.get("trigger_variants")
            _pre_excluded = existing_rule.get("excluded_contexts")

            _apply_non_destructive_merge(db, target_id, rule_data, merge_op, merge_info)

            # Spec 6.5: re-run synthetic eval if variants or excluded_contexts changed
            _merge_changed_variants = bool(
                rule_data.get("variants") or rule_data.get("trigger_variants")
            )
            _merge_changed_excluded = bool(rule_data.get("excluded_contexts"))
            if _merge_changed_variants or _merge_changed_excluded:
                # Reload merged rule for re-compilation
                _merged_row = db.fetchone(
                    "SELECT trigger_canonical, trigger_variants, excluded_contexts, "
                    "concepts, required_concept_groups, near_miss_examples, "
                    "action_instruction, rule_version, status, severity, "
                    "runtime_policy_version, first_observed_useful_at "
                    "FROM rules WHERE id = ?",
                    (target_id,),
                )
                if _merged_row is not None:
                    _raw_variants = json.loads(_merged_row["trigger_variants"] or "[]")
                    _variants = [
                        v if isinstance(v, dict) else {
                            "text": str(v),
                            "kind": "weak_recall",
                            "requires_concepts": [],
                        }
                        for v in _raw_variants
                    ]
                    _recompile_data = {
                        "trigger_canonical": _merged_row["trigger_canonical"],
                        "variants": _variants,
                        "excluded_contexts": json.loads(_merged_row["excluded_contexts"] or "[]"),
                        "concepts": json.loads(_merged_row["concepts"] or "[]"),
                        "required_concept_groups": json.loads(_merged_row["required_concept_groups"] or "[]"),
                        "near_miss_examples": json.loads(_merged_row["near_miss_examples"] or "[]"),
                        "action_instruction": _merged_row["action_instruction"],
                    }
                    try:
                        _recompiled = compile_rule(_recompile_data)
                    except CompilationError:
                        _recompiled = None

                    _synth_ok = False
                    if _recompiled is None:
                        _synth_ok = False
                    elif eval_cases or global_adversarial_cases:
                        _reeval_rule_data = {
                            "id": target_id,
                            "version": _merged_row["rule_version"],
                            "status": _merged_row["status"],
                            "severity": _merged_row["severity"],
                            "first_observed_useful_at": _merged_row[
                                "first_observed_useful_at"
                            ],
                        }
                        _synth_result = run_synthetic_eval(
                            _reeval_rule_data,
                            _recompiled,
                            idf_stats,
                            eval_cases,
                            global_adversarial_cases,
                        )
                        _synth_ok = _synth_result is not None and _synth_result.passed
                    else:
                        # No eval cases available but compilation succeeded —
                        # allow non-destructive merge (compilation validates structure).
                        _synth_ok = True

                    if not _synth_ok:
                        # Revert merged fields via CAS (restore pre-merge values)
                        _revert_version = _merged_row["rule_version"]
                        _revert_status = _merged_row["status"]
                        _revert_rpv = _merged_row["runtime_policy_version"]
                        _now_revert = datetime.now(timezone.utc).isoformat(timespec="seconds")
                        _revert_sets = []
                        _revert_params: list = []
                        if _merge_changed_variants:
                            _revert_sets.append("trigger_variants = ?")
                            _revert_params.append(_pre_variants if isinstance(_pre_variants, str) else dumps_json(_pre_variants) if _pre_variants is not None else None)
                        if _merge_changed_excluded:
                            _revert_sets.append("excluded_contexts = ?")
                            _revert_params.append(_pre_excluded if isinstance(_pre_excluded, str) else dumps_json(_pre_excluded) if _pre_excluded is not None else None)
                        if _revert_sets:
                            _revert_sets.append("rule_version = rule_version + 1")
                            _revert_sets.append("runtime_policy_version = ?")
                            _revert_params.append(RUNTIME_POLICY_VERSION)
                            _revert_sets.append("updated_at = ?")
                            _revert_params.append(_now_revert)
                            _revert_params.extend([target_id, _revert_version, _revert_status])
                            _rpv_where = "AND runtime_policy_version = ?" if _revert_rpv else "AND runtime_policy_version IS NULL"
                            _rpv_params = (_revert_rpv,) if _revert_rpv else ()
                            with db.transaction() as tx:
                                tx.execute(
                                    f"UPDATE rules SET {', '.join(_revert_sets)} "
                                    f"WHERE id = ? AND rule_version = ? AND status = ? {_rpv_where}",
                                    tuple(_revert_params) + _rpv_params,
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

    if target_status == "active" and fast_lane_passed:
        final_status = "active"
        activation_origin: ActivationOrigin = "cold_fast_lane"
    else:
        final_status = "candidate"
        activation_origin = None

    # --- Insert rule ---
    rule_id = insert_rule_from_pipeline(
        db,
        rule_data,
        status=final_status,
        compiled_matcher=compiled_matcher,
        synthetic_result=synthetic_result,
        activation_origin=activation_origin,
        source_origin=source_origin,
        transcript_ref=transcript_ref,
        scores=scores,
        synthetic_eval_skipped=synthetic_eval_skipped,
        project_id=project_id,
        admission_model_id=admission_model,
    )

    _apply_merge_side_effects(db, rule_id, merge_op, merge_info)

    return ColdPipelineResult(
        status=final_status,
        rule_id=rule_id,
        rejection_reason=None,
        scores=scores,
    )


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------




def _handle_split_required(
    db: Db,
    llm,
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
        "Output strict JSON: {\"split_rules\": [{...}, {...}]} matching rule_rewriter schema per sub-rule."
    )

    rule_text = _prompt_text(json.dumps(rule_data, ensure_ascii=False, indent=2))
    user_prompt = f"<rule_to_split>\n{rule_text}\n</rule_to_split>\n\nSplit into independent sub-rules."

    def _validate_split_response(raw: str) -> None:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("split_required response must be an object")
        split_rules = parsed.get("split_rules")
        if not isinstance(split_rules, list) or not split_rules:
            raise ValueError("split_required response missing non-empty split_rules")

    try:
        response = _call_llm_role(
            db, llm, role="rule_rewriter", model_id=rewriter_model,
            system=system_prompt, user=user_prompt,
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
            db, llm, transcript_ref, sub_extractor,
            role_models=role_models, default_model=default_model,
            role_max_tokens=role_max_tokens, role_timeouts=role_timeouts,
            idf_stats=idf_stats, global_adversarial_cases=global_adversarial_cases,
            source_origin=source_origin,
            project_id=project_id,
        )
        results.append(result)

    return results


def _build_idf_stats_from_db(db: Db) -> IdfPoolStats:
    """Build IDF stats from current active+trusted rule pool."""
    from ..db import row_to_rule

    rows = db.fetchall(
        "SELECT * FROM rules WHERE status IN ('active', 'trusted')"
    )
    rules = [row_to_rule(row) for row in rows]
    return build_idf_stats(rules)
