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
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ..archive.fingerprints import check_fingerprint_block
from ..db import Db, SCHEMA_VERSION, dumps_json
from ..matcher.compiler import CompilationError, CompiledMatcher, compile_rule
from ..eval.synthetic import SyntheticEvalResult, run_synthetic_eval
from ..policy import (
    COLD_FAST_LANE,
    RUNTIME_POLICY_VERSION,
    SourceOrigin,
    ActivationOrigin,
)
from ..search.tokenizer import tokenize
from ..merge.policy import (
    MergeDecision,
    apply_merge_policy,
    find_merge_neighbors,
    record_lineage,
    validate_merge_transaction,
)
from ..search.idf_stats import IdfPoolStats, build_idf_stats
from .jobs import (
    enqueue_job,
    get_cached_output,
    is_circuit_breaker_open,
    mark_job_complete,
    mark_job_failed,
)
from ..utils.logging import get_logger

log = get_logger("nokori.cold.pipeline")
from .roles import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUTS,
    PROMPT_VERSIONS,
    resolve_model_id,
    validate_role_output,
)


# ---------------------------------------------------------------------------
# Pipeline version
# ---------------------------------------------------------------------------

class CircuitBreakerOpenError(RuntimeError):
    """Raised when a circuit breaker is open — job should remain pending, not rejected."""
    pass


PIPELINE_VERSION: str = "1.0.0"
DESTRUCTIVE_MERGE_OPS: frozenset[str] = frozenset((
    "replace_existing",
    "suppress_existing",
    "archive_existing",
))


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
        return result
    except CircuitBreakerOpenError as e:
        # Spec section 5.2: paused jobs remain pending, not rejected
        log.warning("cold_pipeline pending (circuit breaker): trigger=%r %s", trigger_preview, e)
        return ColdPipelineResult(
            status="pending",
            rule_id=None,
            rejection_reason=f"circuit_breaker_pending: {e}",
            scores=None,
        )
    except (RuntimeError, OSError, TimeoutError, ConnectionError, ValueError) as e:
        # Spec section 13: failed role calls leave jobs pending for retry
        log.warning("cold_pipeline pending (role failure): trigger=%r %s: %s", trigger_preview, type(e).__name__, e)
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
        # Deterministic scope check: reject if rewriter broadened scope
        original_rule_data = _candidate_to_rule_data(candidate)
        if _rewriter_broadened_scope(original_rule_data, rewritten):
            return ColdPipelineResult(
                status="rejected",
                rule_id=None,
                rejection_reason="rewriter_broadened_scope",
                scores=scores,
            )
        rule_data = rewritten
        # Preserve fields from candidate that rewriter doesn't output
        rule_data["evidence_quotes"] = candidate.get("evidence_quotes", [])
        rule_data["trigger_canonical_zh"] = candidate.get("trigger_zh")
        rule_data["action_instruction_zh"] = candidate.get("action_zh")
        rule_data["trigger_variants_zh"] = candidate.get("trigger_variants_zh", [])
        rule_data["non_generalization_boundaries"] = candidate.get("non_generalization_boundaries", [])
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

    # --- Stage g: Synthetic eval (for both active and candidate targeting) ---
    synthetic_passed = False
    adversarial_failures = 0
    synthetic_result: SyntheticEvalResult | None = None

    if idf_stats is None:
        idf_stats = _build_idf_stats_from_db(db)

    synthetic_eval_skipped = False
    try:
        eval_cases = _generate_eval_cases(
            db, llm, rule_data, role_models, default_model,
            role_max_tokens, role_timeouts,
        )
    except (CircuitBreakerOpenError, ValueError):
        if target_status == "active":
            raise  # Active requires synthetic eval — propagate to pending
        eval_cases = []  # Candidate can proceed without eval
        synthetic_eval_skipped = True
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


def _prompt_text(value: str) -> str:
    """Fence untrusted content with unique boundary markers (spec section 5)."""
    boundary = "---UNTRUSTED-CONTENT-BOUNDARY---"
    return f"{boundary}\n{value}\n{boundary}"


def _llm_input_hash(role: str, system: str, user: str, model_id: str = "") -> str:
    payload = dumps_json({
        "role": role,
        "prompt_version": PROMPT_VERSIONS.get(role),
        "model_id": model_id,
        "system": system,
        "user": user,
    })
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _call_llm_role(
    db: Db,
    llm,
    *,
    role: str,
    model_id: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int,
    validate_response: Callable[[str], None] | None = None,
) -> str:
    """Call an LLM role through the durable idempotency/circuit-breaker layer."""
    from .roles import compute_prompt_version
    prompt_version = compute_prompt_version(role, system)
    input_hash = _llm_input_hash(role, system, user, model_id)

    if is_circuit_breaker_open(db, role, model_id=model_id):
        raise CircuitBreakerOpenError(f"circuit breaker open for role {role}")

    cached = get_cached_output(db, role, model_id, prompt_version, input_hash)
    if cached is not None:
        log.info("role=%s model=%s cache_hit=true", role, model_id)
        return cached

    log.info("role=%s model=%s calling LLM (max_tokens=%d timeout=%ds)", role, model_id, max_tokens, timeout)
    job_id = enqueue_job(db, role, model_id, prompt_version, input_hash)
    try:
        response = llm.call(
            model=model_id,
            system=system,
            user=user,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except Exception as exc:
        error_info = f"{type(exc).__name__}: {exc}"
        mark_job_failed(db, job_id, error_info=error_info)
        log.warning("role=%s model=%s LLM call failed: %s", role, model_id, error_info)
        raise

    # Only cache validated output; malformed/schema-invalid responses must be
    # retryable instead of becoming permanent done-job cache entries.
    try:
        if validate_response is not None:
            validate_response(response)
        else:
            json.loads(response)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        error_info = f"schema validation failed: {exc}"
        mark_job_failed(db, job_id, error_info=error_info)
        log.warning("role=%s model=%s validation failed: %s", role, model_id, error_info)
        raise ValueError(f"LLM role {role} returned invalid output: {exc}") from exc
    log.info("role=%s model=%s call OK (response_len=%d)", role, model_id, len(response))
    mark_job_complete(db, job_id, response)
    return response


def _role_max_tokens(
    role: str, role_max_tokens: dict[str, int] | None
) -> int:
    if role_max_tokens and role_max_tokens.get(role):
        return role_max_tokens[role]
    return DEFAULT_MAX_TOKENS[role]


def _role_timeout(role: str, role_timeouts: dict[str, int] | None) -> int:
    if role_timeouts and role_timeouts.get(role):
        return role_timeouts[role]
    return DEFAULT_TIMEOUTS[role]


def _run_admission_judge(
    db: Db,
    llm,
    candidate: dict[str, Any],
    model_id: str,
    role_max_tokens: dict[str, int] | None = None,
    role_timeouts: dict[str, int] | None = None,
) -> tuple[str, dict]:
    """Run admission judge on a candidate.

    Returns:
        Tuple of (decision, scores). decision = "accept" | "revise" | "reject".
    """
    system_prompt = (
        "You are an admission judge for an autonomous rule memory system. "
        "Evaluate whether this candidate rule deserves lifecycle entry. "
        "Reject broad, unsupported, or untestable rules. "
        "You must cite evidence for any positive decision.\n\n"
        "CRITICAL for evidence_support scoring: the evidence_quotes field contains "
        "verbatim transcript excerpts. Verify that these quotes actually support "
        "the trigger and action claimed. If the quotes are unrelated to the rule's "
        "topic, score evidence_support near 0 regardless of how plausible the rule sounds.\n\n"
        "Output strict JSON:\n"
        '{"scores":{"overall_quality":0.0-1.0,"evidence_support":0.0-1.0,'
        '"trigger_specificity":0.0-1.0,"action_clarity":0.0-1.0,'
        '"scope_control":0.0-1.0,"generalization_safety":0.0-1.0,'
        '"retrieval_readiness":0.0-1.0},'
        '"decision":"accept|revise|reject","reasoning":"..."}'
    )

    candidate_text = _prompt_text(json.dumps(candidate, ensure_ascii=False, indent=2))
    user_prompt = (
        f"<candidate_rule>\n{candidate_text}\n</candidate_rule>\n\n"
        "Evaluate this candidate. Score each dimension 0.0-1.0 and decide: accept, revise, or reject. "
        "Pay special attention to whether evidence_quotes genuinely support the claimed trigger/action."
    )

    try:
        response = _call_llm_role(
            db,
            llm,
            role="admission_judge",
            model_id=model_id,
            system=system_prompt,
            user=user_prompt,
            max_tokens=_role_max_tokens("admission_judge", role_max_tokens),
            timeout=_role_timeout("admission_judge", role_timeouts),
            validate_response=lambda raw: validate_role_output("admission_judge", raw),
        )
        result = validate_role_output("admission_judge", response)
        llm_decision = result["decision"]
        scores = result["scores"]
        # Enforce deterministic policy over LLM decision (spec section 6.2/6.7)
        decision = _enforce_admission_policy(llm_decision, scores)
        log.info(
            "admission_judge: llm_decision=%s policy_decision=%s scores={overall=%.2f evidence=%.2f specificity=%.2f scope=%.2f}",
            llm_decision, decision,
            scores.get("overall_quality", 0), scores.get("evidence_support", 0),
            scores.get("trigger_specificity", 0), scores.get("scope_control", 0),
        )
        return decision, scores
    except CircuitBreakerOpenError:
        raise
    except ValueError:
        raise  # Propagate for retry (spec section 13: failed role = pending)


def _enforce_admission_policy(decision: str, scores: dict) -> str:
    """Enforce deterministic admission policy over LLM decision (spec section 6.2).

    LLM roles are advisory; the database state transition is made by
    deterministic policy over LLM outputs (section 6.7). Policy is
    bidirectional — can upgrade revise->accept or downgrade accept->revise.

    accept requires: overall >= 0.82, evidence >= 0.85, specificity >= 0.75, scope >= 0.75
    revise requires: overall >= 0.55, evidence >= 0.70
    otherwise: reject
    """
    overall = scores.get("overall_quality", 0.0)
    evidence = scores.get("evidence_support", 0.0)
    specificity = scores.get("trigger_specificity", 0.0)
    scope = scores.get("scope_control", 0.0)

    # Deterministic policy is authoritative regardless of LLM decision
    if overall >= 0.82 and evidence >= 0.85 and specificity >= 0.75 and scope >= 0.75:
        return "accept"
    if overall >= 0.55 and evidence >= 0.70:
        return "revise"
    return "reject"


def _run_rewriter(
    db: Db,
    llm,
    candidate: dict[str, Any],
    judge_feedback: dict,
    model_id: str,
    role_max_tokens: dict[str, int] | None = None,
    role_timeouts: dict[str, int] | None = None,
) -> dict | None:
    """Run rule rewriter to improve a revisable candidate.

    Returns:
        Rewritten structured rule data dict, or None on failure.
    """
    system_prompt = (
        "You are a rule rewriter for an autonomous memory system. "
        "Narrow and structure the candidate without inventing facts or broadening beyond evidence. "
        "Separate trigger, action, variants, search_terms, required_concepts, and excluded_contexts.\n\n"
        "Output strict JSON:\n"
        '{"trigger_canonical":"...","required_concept_groups":[{"concepts":["term"],"match":"all|any"}],'
        '"excluded_contexts":[{"pattern":"...","scope":"trigger|action"}],'
        '"action_instruction":"...","severity":"reminder|high_risk|gate_eligible",'
        '"scope":{"domain_tags":[],"path_patterns":[],"tool_tags":[]},"rewrite_rationale":"..."}'
    )

    candidate_text = _prompt_text(json.dumps(candidate, ensure_ascii=False, indent=2))
    feedback_text = _prompt_text(json.dumps(judge_feedback, ensure_ascii=False, indent=2))
    user_prompt = (
        f"<candidate_rule>\n{candidate_text}\n</candidate_rule>\n\n"
        f"<judge_feedback>\n{feedback_text}\n</judge_feedback>\n\n"
        "Rewrite this candidate to address the feedback. Do not broaden scope beyond the evidence."
    )

    try:
        response = _call_llm_role(
            db,
            llm,
            role="rule_rewriter",
            model_id=model_id,
            system=system_prompt,
            user=user_prompt,
            max_tokens=_role_max_tokens("rule_rewriter", role_max_tokens),
            timeout=_role_timeout("rule_rewriter", role_timeouts),
            validate_response=lambda raw: validate_role_output("rule_rewriter", raw),
        )
        return validate_role_output("rule_rewriter", response)
    except CircuitBreakerOpenError:
        raise
    except ValueError:
        raise  # Propagate for retry (spec section 13)


def _run_final_judge(
    db: Db,
    llm,
    rule_data: dict[str, Any],
    original_evidence: list[str],
    model_id: str,
    role_max_tokens: dict[str, int] | None = None,
    role_timeouts: dict[str, int] | None = None,
) -> str:
    """Run final judge on structured rule data.

    Returns:
        Decision string: "accept_active" | "accept_candidate" | "reject".
    """
    system_prompt = (
        "You are the final judge for an autonomous rule memory system. "
        "Verify the structured rule against original evidence. "
        "Do not let rewriter polish hide weak evidence. "
        "You must cite evidence for any accept decision.\n\n"
        "Output strict JSON:\n"
        '{"decision":"accept_active|accept_candidate|reject",'
        '"reasoning":"...","evidence_citations":["quote1","quote2"]}'
    )

    rule_text = _prompt_text(json.dumps(rule_data, ensure_ascii=False, indent=2))
    evidence_text = _prompt_text(json.dumps(original_evidence, ensure_ascii=False))
    user_prompt = (
        f"<structured_rule>\n{rule_text}\n</structured_rule>\n\n"
        f"<original_evidence>\n{evidence_text}\n</original_evidence>\n\n"
        "Decide: accept_active (narrow, evidence-rich, low-near-miss), "
        "accept_candidate (good but needs shadow proof), or reject."
    )

    try:
        response = _call_llm_role(
            db,
            llm,
            role="final_judge",
            model_id=model_id,
            system=system_prompt,
            user=user_prompt,
            max_tokens=_role_max_tokens("final_judge", role_max_tokens),
            timeout=_role_timeout("final_judge", role_timeouts),
            validate_response=lambda raw: validate_role_output("final_judge", raw),
        )
        result = validate_role_output("final_judge", response)
        decision = result["decision"]
        log.info("final_judge: decision=%s", decision)
        return decision
    except CircuitBreakerOpenError:
        raise
    except ValueError:
        raise  # Propagate for retry (spec section 13)


def _run_merge_planner(
    db: Db,
    llm,
    rule_data: dict[str, Any],
    model_id: str,
    role_max_tokens: dict[str, int] | None = None,
    role_timeouts: dict[str, int] | None = None,
    project_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run merge planner to check against existing rules.

    Returns:
        Tuple of (operation, full_merge_info).
    """
    # Retrieve existing rules for comparison
    existing_rules = _get_existing_rules_for_merge(
        db, rule_data, project_id=project_id
    )

    if not existing_rules:
        return "keep_both", {"merge_rationale": "no existing overlap", "target_rule_ids": []}

    system_prompt = (
        "You are a merge planner for an autonomous rule memory system. "
        "Determine the relationship between a new rule and existing rules. "
        "Do not replace trusted rules with plausible but weaker text.\n\n"
        "Output strict JSON:\n"
        '{"relation_shape":"equivalent|new_broader|new_narrower|overlap|complementary|contradiction|obsolete|unrelated|split_required",'
        '"new_rule_safety":"safe|unsafe|uncertain",'
        '"operation_safety":"safe|unsafe|uncertain",'
        '"quality_winner":"new|existing|both|neither",'
        '"operation":"merge_into_existing|update_existing_fields|replace_existing|keep_both|reject_new|suppress_existing|archive_existing|split_required",'
        '"confidence":0.0-1.0,"reason":"...","target_rule_ids":["id1"]}'
    )

    rule_text = _prompt_text(json.dumps(rule_data, ensure_ascii=False, indent=2))
    existing_text = _prompt_text(json.dumps(existing_rules, ensure_ascii=False, indent=2))
    user_prompt = (
        f"<new_rule>\n{rule_text}\n</new_rule>\n\n"
        f"<existing_rules>\n{existing_text}\n</existing_rules>\n\n"
        "Determine relation_shape, safety, quality_winner, and operation. "
        "Consider trust levels and evidence strength of existing rules."
    )

    try:
        response = _call_llm_role(
            db,
            llm,
            role="merge_planner",
            model_id=model_id,
            system=system_prompt,
            user=user_prompt,
            max_tokens=_role_max_tokens("merge_planner", role_max_tokens),
            timeout=_role_timeout("merge_planner", role_timeouts),
            validate_response=lambda raw: validate_role_output("merge_planner", raw),
        )
        result = validate_role_output("merge_planner", response)
        operation = result["operation"]
        target_ids = result.get("target_rule_ids") or []
        existing = next(
            (r for r in existing_rules if r["id"] in target_ids),
            existing_rules[0],
        )
        planner_output = {
            "relation_shape": result.get("relation_shape", "unrelated"),
            "new_rule_safety": result.get("new_rule_safety", "safe"),
            "operation_safety": result.get("operation_safety", "safe"),
            "quality_winner": result.get("quality_winner", "neither"),
            "operation": operation,
            "confidence": result.get("confidence", 0.5),
            "reason": result.get("reason", ""),
        }
        decision = apply_merge_policy(planner_output, existing, rule_data)
        log.info(
            "merge_planner: llm_op=%s policy_op=%s relation=%s quality_winner=%s",
            operation, decision.operation,
            planner_output.get("relation_shape"), planner_output.get("quality_winner"),
        )
        return decision.operation, {
            **planner_output,
            "merge_rationale": decision.reason,
            "target_rule_ids": [decision.target_rule_id] if decision.target_rule_id else [],
            "policy_decision": decision.operation,
            "requires_synthetic_reeval": decision.requires_synthetic_reeval,
            "existing_rule": existing,
            "lineage_record": decision.lineage_record,
        }
    except CircuitBreakerOpenError:
        raise
    except ValueError:
        raise  # Propagate for retry (spec section 13)


def _operation_to_relation_shape(operation: str) -> str:
    if operation in {"merge", "replace"}:
        return "equivalent"
    if operation == "split":
        return "split_required"
    return "unrelated"


# ---------------------------------------------------------------------------
# Cold fast lane check
# ---------------------------------------------------------------------------


def _check_cold_fast_lane(
    scores: dict | None,
    synthetic_passed: bool,
    adversarial_failures: int,
    fingerprint_conflict: bool,
    merge_op: str,
    source_origin: SourceOrigin = "transcript_extraction",
    final_judge_decision: str = "accept_active",
) -> bool:
    """Check all cold fast lane thresholds from policy (section 3.3).

    Returns True only if ALL thresholds pass for direct active insertion.
    external_source_material CANNOT use cold fast lane (spec acceptance criteria).
    """
    if final_judge_decision != "accept_active":
        return False

    if source_origin == "external_source_material":
        return False

    if not scores:
        return False

    thresholds = COLD_FAST_LANE

    # Quality scores
    if scores.get("overall_quality", 0) < thresholds.admission_overall_quality_min:
        return False
    if scores.get("evidence_support", 0) < thresholds.evidence_support_min:
        return False
    if scores.get("trigger_specificity", 0) < thresholds.trigger_specificity_min:
        return False
    if scores.get("scope_control", 0) < thresholds.scope_control_min:
        return False

    # Synthetic eval
    if not synthetic_passed:
        return False

    # Adversarial failures
    if adversarial_failures > thresholds.global_adversarial_failures_max:
        return False

    # Fingerprint conflict
    if fingerprint_conflict:
        return False

    # Merge operation must not require split/rewrite (spec 3.3 condition 9)
    if merge_op in thresholds.merge_operation_must_not_require:
        return False

    return True


# ---------------------------------------------------------------------------
# Rule insertion
# ---------------------------------------------------------------------------


def insert_rule_from_pipeline(
    db: Db,
    rule_data: dict[str, Any],
    status: str,
    compiled_matcher: CompiledMatcher,
    synthetic_result: SyntheticEvalResult | None = None,
    activation_origin: ActivationOrigin | None = None,
    source_origin: SourceOrigin = "transcript_extraction",
    transcript_ref: str | None = None,
    scores: dict | None = None,
    synthetic_eval_skipped: bool = False,
    project_id: str | None = None,
) -> str:
    """Insert a rule from the cold pipeline into the rules table.

    Requires successful matcher compilation. Uses CAS-style version checks
    to prevent concurrent insertion conflicts.

    Args:
        db: Database handle.
        rule_data: Structured rule data from rewriter or candidate conversion.
        status: Target status ("candidate" or "active").
        compiled_matcher: Successfully compiled matcher (proves compilability).
        synthetic_result: Optional synthetic eval result for active rules.
        activation_origin: How the rule became active (if active).
        source_origin: Origin classification.
        transcript_ref: Source transcript reference.
        scores: Admission judge quality scores.

    Returns:
        The new rule's id.
    """
    rule_id = str(uuid.uuid4())
    short_id = rule_id[:8]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    scores = scores or {}

    # Extract structured fields
    trigger_canonical = rule_data.get("trigger_canonical", "")
    action_instruction = rule_data.get("action_instruction", "")
    severity = rule_data.get("severity", "reminder")
    scope = rule_data.get("scope", {})

    # Serialize JSON fields
    concepts_json = dumps_json(rule_data.get("concepts", []))
    # Extract concept_aliases from concepts structure (each concept may have an "aliases" key)
    raw_concepts = rule_data.get("concepts", [])
    concept_aliases_list = []
    if isinstance(raw_concepts, list):
        for c in raw_concepts:
            if isinstance(c, dict) and "aliases" in c:
                concept_aliases_list.extend(c["aliases"])
    concept_aliases_json = dumps_json(concept_aliases_list)
    required_concept_groups_json = dumps_json(rule_data.get("required_concept_groups", []))
    excluded_contexts_json = dumps_json(rule_data.get("excluded_contexts", []))
    near_miss_json = dumps_json(rule_data.get("near_miss_examples", []))
    variants_json = dumps_json(rule_data.get("variants", []))
    variants_zh_json = dumps_json(rule_data.get("trigger_variants_zh", []))
    search_terms_json = dumps_json(rule_data.get("search_terms", {}))
    allowed_behavior_json = dumps_json(rule_data.get("allowed_behavior", []))
    forbidden_behavior_json = dumps_json(rule_data.get("forbidden_behavior", []))
    domain_tags_json = dumps_json(scope.get("domain_tags", []))
    tool_tags_json = dumps_json(scope.get("tool_tags", []))
    path_patterns_json = dumps_json(scope.get("file_or_path_patterns", []))
    language_hints_json = dumps_json(rule_data.get("language_hints", []))
    evidence_quotes_json = dumps_json(rule_data.get("evidence_quotes", []))
    non_generalization_boundaries_json = dumps_json(rule_data.get("non_generalization_boundaries", []))
    project_scope = "project" if project_id else "global"

    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules ("
            "id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, last_rewritten_by_role, "
            "status, severity, "
            "trigger_canonical, trigger_canonical_zh, "
            "concepts, concept_aliases, required_concept_groups, excluded_contexts, "
            "non_generalization_boundaries, "
            "near_miss_examples, trigger_variants, trigger_variants_zh, search_terms, "
            "action_instruction, action_instruction_zh, "
            "allowed_behavior, forbidden_behavior, "
            "domain_tags, tool_tags, path_patterns, language_hints, "
            "evidence_quotes, "
            "quality_score, evidence_support_score, specificity_score, retrieval_readiness_score, "
            "observed_usefulness_score, plausible_usefulness_score, false_positive_score, harmful_score, "
            "source_origin, activation_origin, transcript_ref, "
            "synthetic_eval_skipped, "
            "project_scope, project_id, "
            "created_at, updated_at"
            ") VALUES ("
            "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?"
            ")",
            (
                rule_id,
                short_id,
                SCHEMA_VERSION,
                1,  # rule_version
                PIPELINE_VERSION,
                RUNTIME_POLICY_VERSION,
                "rule_rewriter" if rule_data.get("_rewritten") else None,
                status,
                severity,
                trigger_canonical,
                rule_data.get("trigger_canonical_zh"),
                concepts_json,
                concept_aliases_json,
                required_concept_groups_json,
                excluded_contexts_json,
                non_generalization_boundaries_json,
                near_miss_json,
                variants_json,
                variants_zh_json,
                search_terms_json,
                action_instruction,
                rule_data.get("action_instruction_zh"),
                allowed_behavior_json,
                forbidden_behavior_json,
                domain_tags_json,
                tool_tags_json,
                path_patterns_json,
                language_hints_json,
                evidence_quotes_json,
                scores.get("overall_quality", 0.0),
                scores.get("evidence_support", 0.0),
                scores.get("trigger_specificity", 0.0),
                scores.get("retrieval_readiness", 0.0),
                0.0,  # observed_usefulness_score
                0.0,  # plausible_usefulness_score
                0.0,  # false_positive_score
                0.0,  # harmful_score
                source_origin,
                activation_origin,
                transcript_ref,
                1 if synthetic_eval_skipped else 0,
                project_scope,
                project_id,
                now,
                now,
            ),
        )

        # Store review record for auditability
        if scores:
            tx.execute(
                "INSERT INTO rule_reviews "
                "(role, model_id, prompt_version, input_hash, output_json, "
                "scores, decision, rule_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "admission_judge",
                    None,
                    PROMPT_VERSIONS.get("admission_judge"),
                    None,
                    None,
                    dumps_json(scores),
                    status,
                    rule_id,
                    now,
                ),
            )

    # Store synthetic eval result if available
    if synthetic_result is not None:
        from ..eval.synthetic import store_eval_result

        # Update rule_id on the result for storage
        patched_result = SyntheticEvalResult(
            rule_id=rule_id,
            rule_version=1,
            runtime_policy_version=synthetic_result.runtime_policy_version,
            tokenizer_version=synthetic_result.tokenizer_version,
            matcher_compiler_version=synthetic_result.matcher_compiler_version,
            concept_compiler_version=synthetic_result.concept_compiler_version,
            embedding_profile_version=synthetic_result.embedding_profile_version,
            trigger_idf_pool_version=synthetic_result.trigger_idf_pool_version,
            benchmark_version=synthetic_result.benchmark_version,
            cases=synthetic_result.cases,
            results=synthetic_result.results,
            passed=synthetic_result.passed,
        )
        store_eval_result(db, patched_result)

    return rule_id


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _rewriter_broadened_scope(original: dict[str, Any], rewritten: dict[str, Any]) -> bool:
    """Check if the rewriter broadened the rule's scope beyond the original candidate.

    Returns True (reject) if the rewritten rule has MORE required_concept_groups
    or expanded domain_tags compared to the original.
    """
    original_groups = original.get("required_concept_groups", [])
    rewritten_groups = rewritten.get("required_concept_groups", [])

    # More concept groups = broader matching surface
    if len(rewritten_groups) > len(original_groups):
        return True

    # Check domain_tags expansion
    original_tags = set(original.get("scope", {}).get("domain_tags", []))
    rewritten_tags = set(rewritten.get("scope", {}).get("domain_tags", []))

    # Rewritten has tags not present in original = broader scope
    if rewritten_tags - original_tags:
        return True

    return False


def _candidate_to_rule_data(candidate: dict[str, Any]) -> dict[str, Any]:
    """Convert raw extractor candidate output to structured rule_data format."""
    return {
        "trigger_canonical": candidate.get("trigger_draft", candidate.get("trigger", "")),
        "trigger_canonical_zh": candidate.get("trigger_zh"),
        "action_instruction": candidate.get("action_draft", candidate.get("action", "")),
        "action_instruction_zh": candidate.get("action_zh"),
        "severity": "reminder",
        "required_concept_groups": _draft_concept_groups(candidate),
        "concepts": _draft_concepts(candidate),
        "excluded_contexts": _draft_excluded_contexts(candidate),
        "variants": _draft_variants(candidate),
        "trigger_variants_zh": candidate.get("trigger_variants_zh", []),
        "near_miss_examples": candidate.get("near_miss_examples", []),
        "search_terms": candidate.get("search_terms_draft", candidate.get("search_terms", {})),
        "scope": {
            "domain_tags": candidate.get("domain_tags", []),
            "tool_tags": candidate.get("tool_tags", []),
            "file_or_path_patterns": candidate.get("path_patterns", []),
        },
        "evidence_quotes": candidate.get("evidence_quotes", []),
        "non_generalization_boundaries": candidate.get("non_generalization_boundaries", []),
        "allowed_behavior": [],
        "forbidden_behavior": [],
    }


def _ensure_rule_data_variants(rule_data: dict[str, Any]) -> dict[str, Any]:
    """Ensure compiled durable rule data has at least one v6 variant."""
    variants = rule_data.get("variants") or []
    if variants:
        return rule_data
    groups = rule_data.get("required_concept_groups") or []
    required_concepts = [
        concept_id
        for group in groups
        if isinstance(group, dict)
        for concept_id in group.get("all_of", [])
    ]
    trigger = str(rule_data.get("trigger_canonical") or "").strip()
    if not trigger:
        return rule_data
    strong_anchor = bool(required_concepts and len(tokenize(trigger)) >= 2)
    updated = dict(rule_data)
    updated["variants"] = [{
        "text": trigger,
        "kind": "strong_anchor" if strong_anchor else "weak_recall",
        "requires_concepts": required_concepts if strong_anchor else [],
    }]
    return updated


def _draft_concept_groups(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Build minimal concept groups from draft concepts."""
    concepts_draft = candidate.get("required_concepts_draft", [])
    if not concepts_draft:
        # Fallback: single group from trigger text
        return [{"id": "primary", "all_of": ["primary_concept"]}]

    concept_ids = [f"concept_{i}" for i in range(len(concepts_draft))]
    return [{"id": "primary_group", "all_of": concept_ids}]


def _draft_concepts(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Build concept entries from draft concepts."""
    concepts_draft = candidate.get("required_concepts_draft", [])
    if not concepts_draft:
        trigger = candidate.get("trigger_draft", candidate.get("trigger", ""))
        return [{
            "id": "primary_concept",
            "label": trigger[:80],
            "aliases": [{"text": trigger[:80], "strength": "strong"}],
            "match_mode": "any_alias",
            "required": True,
        }]

    result = []
    for i, concept_text in enumerate(concepts_draft):
        result.append({
            "id": f"concept_{i}",
            "label": concept_text[:80],
            "aliases": [{"text": concept_text, "strength": "strong"}],
            "match_mode": "any_alias",
            "required": True,
        })
    return result


def _draft_excluded_contexts(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Build excluded context entries from draft."""
    excluded_draft = candidate.get("excluded_contexts_draft", [])
    result = []
    for i, ctx_text in enumerate(excluded_draft):
        result.append({
            "id": f"excluded_{i}",
            "label": ctx_text[:80],
            "patterns": [ctx_text],
            "match_mode": "phrase",
            "scope": "global",
            "window_tokens": 12,
            "override_allowed": False,
            "override_requires": [],
        })
    return result


def _draft_variants(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Build variant entries from draft trigger variants."""
    variants_draft = candidate.get("trigger_variants_draft", candidate.get("trigger_variants", []))
    trigger = candidate.get("trigger_draft", candidate.get("trigger", ""))
    required_concepts = [
        concept_id
        for group in _draft_concept_groups(candidate)
        for concept_id in group.get("all_of", [])
    ]
    result = []
    seen: set[str] = set()
    for text in [trigger, *variants_draft]:
        text = str(text).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        strong_anchor = bool(required_concepts and len(tokenize(text)) >= 2)
        result.append({
            "text": text,
            "kind": "strong_anchor" if strong_anchor else "weak_recall",
            "requires_concepts": required_concepts if strong_anchor else [],
        })
    return result


def _apply_merge_side_effects(
    db: Db,
    new_rule_id: str,
    merge_op: str,
    merge_info: dict[str, Any],
) -> None:
    """Apply validated destructive merge side effects after inserting new rule.

    Uses CAS (rule_version + status) to prevent concurrent mutation (spec section 13).
    """
    if merge_op not in DESTRUCTIVE_MERGE_OPS:
        return

    existing_rule = merge_info.get("existing_rule") or {}
    target_rule_id = existing_rule.get("id")
    if not target_rule_id:
        return

    existing_version = existing_rule.get("rule_version")
    existing_status = existing_rule.get("status")
    existing_rpv = existing_rule.get("runtime_policy_version")
    if existing_version is None or existing_status is None:
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reason = merge_info.get("merge_rationale", merge_op)
    # CAS over all 4 fields: rule_id, rule_version, status, runtime_policy_version (spec section 13)
    rpv_where = "AND runtime_policy_version = ?" if existing_rpv else "AND runtime_policy_version IS NULL"
    rpv_params: tuple = (existing_rpv,) if existing_rpv else ()

    cas_applied = False
    with db.transaction() as tx:
        if merge_op == "replace_existing":
            cur = tx.execute(
                "UPDATE rules SET status = 'archived', archived_reason = ?, "
                "replacement_id = ?, rule_version = rule_version + 1, "
                f"runtime_policy_version = ?, updated_at = ? "
                f"WHERE id = ? AND rule_version = ? AND status = ? {rpv_where}",
                (reason, new_rule_id, RUNTIME_POLICY_VERSION, now,
                 target_rule_id, existing_version, existing_status, *rpv_params),
            )
            cas_applied = cur.rowcount == 1
        elif merge_op == "suppress_existing":
            cur = tx.execute(
                "UPDATE rules SET status = 'suppressed', suppressed_at = ?, "
                "rule_version = rule_version + 1, "
                f"runtime_policy_version = ?, updated_at = ? "
                f"WHERE id = ? AND rule_version = ? AND status = ? {rpv_where}",
                (now, RUNTIME_POLICY_VERSION, now,
                 target_rule_id, existing_version, existing_status, *rpv_params),
            )
            cas_applied = cur.rowcount == 1
        elif merge_op == "archive_existing":
            cur = tx.execute(
                "UPDATE rules SET status = 'archived', archived_reason = ?, "
                "rule_version = rule_version + 1, "
                f"runtime_policy_version = ?, updated_at = ? "
                f"WHERE id = ? AND rule_version = ? AND status = ? {rpv_where}",
                (reason, RUNTIME_POLICY_VERSION, now,
                 target_rule_id, existing_version, existing_status, *rpv_params),
            )
            cas_applied = cur.rowcount == 1

    if not cas_applied:
        return

    record_lineage(db, target_rule_id, new_rule_id, merge_op, reason)

    # Create replacement-strength fingerprint on replace (spec section 11)
    if merge_op == "replace_existing":
        rule_data = _get_rule_data_for_fingerprint(db, target_rule_id)
        if rule_data:
            from ..archive.fingerprints import create_archived_fingerprint_from_data
            create_archived_fingerprint_from_data(
                db,
                rule_id=target_rule_id,
                trigger_canonical=rule_data.get("trigger_canonical", ""),
                action_instruction=rule_data.get("action_instruction", ""),
                domain_tags=rule_data.get("domain_tags", []),
                strength="replacement",
            )


def _build_trigger_data(rule_data: dict[str, Any]) -> dict[str, Any]:
    """Build trigger_data dict suitable for compile_rule from structured rule_data."""
    return {
        "required_concept_groups": rule_data.get("required_concept_groups", []),
        "concepts": rule_data.get("concepts", []),
        "excluded_contexts": rule_data.get("excluded_contexts", []),
        "variants": rule_data.get("variants", []),
    }


def _get_existing_rules_for_merge(
    db: Db,
    rule_data: dict[str, Any],
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve existing active/trusted rules with potential overlap for merge planning."""
    trigger = rule_data.get("trigger_canonical", "")
    if not trigger:
        return []

    return find_merge_neighbors(db, rule_data, limit=20, project_id=project_id)


def _generate_eval_cases(
    db: Db,
    llm,
    rule_data: dict[str, Any],
    role_models: dict[str, str] | None,
    default_model: str | None,
    role_max_tokens: dict[str, int] | None = None,
    role_timeouts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Generate synthetic eval cases using the synthetic_eval_generator role."""
    from ..eval.synthetic import generate_eval_cases_prompt

    model_id = resolve_model_id("synthetic_eval_generator", role_models, default_model)

    trigger = rule_data.get("trigger_canonical", "")
    action = rule_data.get("action_instruction", "")
    concepts = rule_data.get("concepts", [])
    near_miss = rule_data.get("near_miss_examples", [])

    prompt = generate_eval_cases_prompt(trigger, action, concepts, near_miss)

    system_prompt = (
        "You are a synthetic evaluation case generator for an autonomous rule memory system. "
        "Produce diverse, tricky test cases. Near-miss cases must be genuinely hard to distinguish. "
        'Output strict JSON object: {"cases": [...]}'
    )

    def _validate_eval_cases_response(raw: str) -> None:
        parsed = json.loads(raw)
        case_list: list | None = None
        if isinstance(parsed, list):
            case_list = parsed
        elif isinstance(parsed, dict) and "cases" in parsed:
            validate_role_output("synthetic_eval_generator", raw)
            case_list = parsed["cases"]
        if case_list is None:
            raise ValueError("synthetic_eval_generator returned no cases")
        has_positive = False
        for case in case_list:
            if not isinstance(case, dict):
                raise ValueError(f"eval case must be an object: {case}")
            if "prompt" not in case:
                raise ValueError(f"eval case missing 'prompt' key: {case}")
            if "case_type" not in case:
                raise ValueError(f"eval case missing 'case_type' key: {case}")
            if case.get("case_type") == "positive":
                has_positive = True
        if not has_positive:
            raise ValueError("synthetic_eval_generator returned no positive cases")

    try:
        response = _call_llm_role(
            db,
            llm,
            role="synthetic_eval_generator",
            model_id=model_id,
            system=system_prompt,
            user=_prompt_text(prompt),
            max_tokens=_role_max_tokens(
                "synthetic_eval_generator", role_max_tokens
            ),
            timeout=_role_timeout(
                "synthetic_eval_generator", role_timeouts
            ),
            validate_response=_validate_eval_cases_response,
        )
        cases = json.loads(response)
        # Accept both array and {cases:[...]} formats for robustness
        case_list: list | None = None
        if isinstance(cases, list):
            case_list = cases
        elif isinstance(cases, dict) and "cases" in cases:
            validate_role_output("synthetic_eval_generator", response)
            case_list = cases["cases"]
        if case_list is None:
            raise ValueError("synthetic_eval_generator returned no cases")
        # Validate each case has required 'prompt' key
        has_positive = False
        for case in case_list:
            if not isinstance(case, dict):
                raise ValueError(f"eval case must be an object: {case}")
            if "prompt" not in case:
                raise ValueError(f"eval case missing 'prompt' key: {case}")
            if "case_type" not in case:
                raise ValueError(f"eval case missing 'case_type' key: {case}")
            if case.get("case_type") == "positive":
                has_positive = True
        if not has_positive:
            raise ValueError("synthetic_eval_generator returned no positive cases")
        return case_list
    except CircuitBreakerOpenError:
        raise
    except (json.JSONDecodeError, ValueError):
        raise  # Propagate for retry (spec section 13)


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
) -> list[ColdPipelineResult]:
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
            "trigger_draft": sub_rule.get("trigger_canonical", ""),
            "action_draft": sub_rule.get("action_instruction", ""),
            "behavior_draft": "",
            "source_type": "solution",
            "confidence_guess": "medium",
            "evidence_quotes": rule_data.get("evidence_quotes", ["split from parent"]),
            "non_generalization_boundaries": [],
            "required_concepts_draft": [],
            "excluded_contexts_draft": [],
            "search_terms_draft": {},
            "trigger_variants_draft": [],
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


def _get_rule_data_for_fingerprint(db: Db, rule_id: str) -> dict | None:
    """Read minimal rule data needed for fingerprint creation."""
    row = db.fetchone(
        "SELECT trigger_canonical, action_instruction, domain_tags FROM rules WHERE id = ?",
        (rule_id,),
    )
    if row is None:
        return None
    from ..db import loads_json
    return {
        "trigger_canonical": row["trigger_canonical"] or "",
        "action_instruction": row["action_instruction"] or "",
        "domain_tags": loads_json(row["domain_tags"], []) if row["domain_tags"] else [],
    }


def _apply_non_destructive_merge(
    db: Db,
    target_rule_id: str,
    new_rule_data: dict[str, Any],
    merge_op: str,
    merge_info: dict[str, Any],
) -> None:
    """Apply merge_into_existing or update_existing_fields to an existing rule.

    Uses CAS (rule_version + status + runtime_policy_version) per spec section 13.
    Updates trigger variants, concepts, exclusions, or examples from the new data
    without changing status or action semantics.
    """
    # Read current state atomically for CAS
    rule_row = db.fetchone(
        "SELECT rule_version, status, runtime_policy_version, "
        "trigger_variants, excluded_contexts, near_miss_examples "
        "FROM rules WHERE id = ?",
        (target_rule_id,),
    )
    if rule_row is None:
        return

    current_version = rule_row["rule_version"]
    current_status = rule_row["status"]
    current_rpv = rule_row["runtime_policy_version"]

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updates: list[str] = []
    params: list = []

    def _variant_text(variant: Any) -> str:
        return str(variant.get("text", "") if isinstance(variant, dict) else variant)

    def _variant_entry(variant: Any) -> dict[str, Any]:
        if isinstance(variant, dict):
            return variant
        return {
            "text": str(variant),
            "kind": "weak_recall",
            "requires_concepts": [],
        }

    # Merge new variants into existing
    new_variants = new_rule_data.get("variants", [])
    if new_variants:
        raw_current = json.loads(rule_row["trigger_variants"]) if rule_row["trigger_variants"] else []
        current = [_variant_entry(v) for v in raw_current]
        normalized_new = [_variant_entry(v) for v in new_variants]
        current_texts = {_variant_text(v) for v in current}
        added = [
            v for v in normalized_new if _variant_text(v) not in current_texts
        ]
        if added:
            merged = current + added
            updates.append("trigger_variants = ?")
            params.append(dumps_json(merged))

    # Merge new excluded contexts
    new_excluded = new_rule_data.get("excluded_contexts", [])
    if new_excluded:
        current = json.loads(rule_row["excluded_contexts"]) if rule_row["excluded_contexts"] else []
        current_ids = {e.get("id", "") for e in current}
        added = [e for e in new_excluded if e.get("id", "") not in current_ids]
        if added:
            merged = current + added
            updates.append("excluded_contexts = ?")
            params.append(dumps_json(merged))

    # Merge near-miss examples
    new_near_miss = new_rule_data.get("near_miss_examples", [])
    if new_near_miss:
        current = json.loads(rule_row["near_miss_examples"]) if rule_row["near_miss_examples"] else []
        added = [nm for nm in new_near_miss if nm not in current]
        if added:
            merged = current + added
            updates.append("near_miss_examples = ?")
            params.append(dumps_json(merged))

    if updates:
        updates.append("rule_version = rule_version + 1")
        updates.append("runtime_policy_version = ?")
        params.append(RUNTIME_POLICY_VERSION)
        updates.append("updated_at = ?")
        params.append(now)
        # CAS: verify rule hasn't changed concurrently
        rpv_where = "AND runtime_policy_version = ?" if current_rpv else "AND runtime_policy_version IS NULL"
        rpv_params: tuple = (current_rpv,) if current_rpv else ()
        params.append(target_rule_id)
        params.append(current_version)
        params.append(current_status)
        with db.transaction() as tx:
            tx.execute(
                f"UPDATE rules SET {', '.join(updates)} "
                f"WHERE id = ? AND rule_version = ? AND status = ? {rpv_where}",
                tuple(params) + rpv_params,
            )


def _build_idf_stats_from_db(db: Db) -> IdfPoolStats:
    """Build IDF stats from current active+trusted rule pool."""
    from ..db import row_to_rule

    rows = db.fetchall(
        "SELECT * FROM rules WHERE status IN ('active', 'trusted')"
    )
    rules = [row_to_rule(row) for row in rows]
    return build_idf_stats(rules)
