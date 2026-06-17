"""Cold-path integration stage: merge planning, compilation, and rule insertion."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from ..db import SCHEMA_VERSION, Db, dumps_json
from ..eval.synthetic import SyntheticEvalResult
from ..matcher.compiler import CompilationError, CompiledMatcher, compile_rule
from ..merge.policy import (
    apply_merge_policy,
    find_merge_neighbors,
    record_lineage,
)
from ..policy import RUNTIME_POLICY_VERSION, ActivationOrigin, SourceOrigin
from ..utils.ids import short_id_for
from ..utils.logging import get_logger
from ..utils.time import now_iso
from ._constants import DESTRUCTIVE_MERGE_OPS, PIPELINE_VERSION
from ._llm_call import (
    CircuitBreakerOpenError,
    call_llm_role as _call_llm_role,
    prompt_text as _prompt_text,
    role_max_tokens as _role_max_tokens,
    role_timeout as _role_timeout,
)
from .roles import PROMPT_VERSIONS, validate_role_output

log = get_logger("nokori.cold.integrate")


def _run_merge_planner(
    db: Db,
    llm: Any,
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
    existing_rules = _get_existing_rules_for_merge(db, rule_data, project_id=project_id)

    if not existing_rules:
        return "keep_both", {"merge_rationale": "no existing overlap", "target_rule_ids": []}

    system_prompt = (
        "You are a merge planner for an autonomous rule memory system. "
        "Determine the relationship between a new rule and existing rules. "
        "Do not replace trusted rules with plausible but weaker text.\n\n"
        "Output a single JSON object with these fields:\n\n"
        "REQUIRED fields:\n"
        '- "relation_shape" (string, REQUIRED): one of:\n'
        '  - "equivalent": same rule, different wording\n'
        '  - "new_broader": new rule covers more ground than existing\n'
        '  - "new_narrower": new rule is more specific than existing\n'
        '  - "overlap": partial overlap in scope\n'
        '  - "complementary": different aspects of same domain\n'
        '  - "contradiction": rules conflict with each other\n'
        '  - "obsolete": existing rule is outdated\n'
        '  - "unrelated": no meaningful relationship\n'
        '  - "split_required": new rule contains multiple independent concerns\n'
        '- "new_rule_safety" (string, REQUIRED): "safe", "unsafe", or "uncertain"\n'
        '- "operation_safety" (string, REQUIRED): "safe", "unsafe", or "uncertain"\n'
        '- "quality_winner" (string, REQUIRED): "new", "existing", "both", or "neither"\n'
        '- "operation" (string, REQUIRED): one of:\n'
        '  - "merge_into_existing", "update_existing_fields", "replace_existing",\n'
        '  - "keep_both", "reject_new", "suppress_existing", "archive_existing", "split_required"\n'
        '- "confidence" (number, REQUIRED): 0.0 to 1.0, how confident in this assessment\n'
        '- "reason" (string, REQUIRED): brief explanation\n\n'
        "OPTIONAL fields:\n"
        '- "target_rule_ids" (array of strings): IDs of existing rules this relates to\n\n'
        "Example output:\n"
        "```json\n"
        "{\n"
        '  "relation_shape": "new_narrower",\n'
        '  "new_rule_safety": "safe",\n'
        '  "operation_safety": "safe",\n'
        '  "quality_winner": "new",\n'
        '  "operation": "keep_both",\n'
        '  "confidence": 0.82,\n'
        '  "reason": "New rule covers a specific subset (Python testing) of the existing general testing rule. Both are valuable.",\n'
        '  "target_rule_ids": ["abc123-def456"]\n'
        "}\n"
        "```\n"
        "Output ONLY the JSON object, no markdown fences, no extra text."
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
            None,
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
            operation,
            decision.operation,
            planner_output.get("relation_shape"),
            planner_output.get("quality_winner"),
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
# Merge-with-reeval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeRevalOutcome:
    """Result of a non-destructive merge with optional synthetic re-evaluation."""

    success: bool
    rule_id: str | None = None


def apply_merge_with_reeval(
    db: Db,
    *,
    target_id: str,
    rule_data: dict[str, Any],
    merge_op: str,
    merge_info: dict[str, Any],
    eval_cases: list,
    global_adversarial_cases: list[dict[str, Any]] | None,
    idf_stats: Any,
) -> MergeRevalOutcome:
    """Apply a non-destructive merge and re-evaluate if variants/excluded_contexts changed.

    Reverts via CAS if re-evaluation fails. Returns outcome indicating success/failure.
    """
    from ..eval.synthetic import run_synthetic_eval

    existing_rule = merge_info.get("existing_rule") or {}
    _pre_variants = existing_rule.get("trigger_variants")
    _pre_excluded = existing_rule.get("excluded_contexts")

    _apply_non_destructive_merge(db, target_id, rule_data, merge_op, merge_info)

    _merge_changed_variants = bool(rule_data.get("variants"))
    _merge_changed_excluded = bool(rule_data.get("excluded_contexts"))

    if not (_merge_changed_variants or _merge_changed_excluded):
        return MergeRevalOutcome(success=True, rule_id=target_id)

    _merged_row = db.fetchone(
        "SELECT trigger_canonical, trigger_variants, excluded_contexts, "
        "concepts, required_concept_groups, near_miss_examples, "
        "action_instruction, rule_version, status, severity, "
        "runtime_policy_version, first_observed_useful_at "
        "FROM rules WHERE id = ?",
        (target_id,),
    )
    if _merged_row is None:
        log.warning("merge_reeval target rule disappeared after merge rule=%s", target_id)
        return MergeRevalOutcome(success=True, rule_id=target_id)

    _raw_variants = json.loads(_merged_row["trigger_variants"] or "[]")
    _variants = [
        v
        if isinstance(v, dict)
        else {
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
    try:
        if _recompiled is None:
            _synth_ok = False
        elif eval_cases or global_adversarial_cases:
            _reeval_rule_data = {
                "id": target_id,
                "version": _merged_row["rule_version"],
                "status": _merged_row["status"],
                "severity": _merged_row["severity"],
                "first_observed_useful_at": _merged_row["first_observed_useful_at"],
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
            _synth_ok = True
    except Exception:
        log.warning("merge_reeval synthetic eval error rule=%s", target_id, exc_info=True)
        _synth_ok = False

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
        return MergeRevalOutcome(success=False, rule_id=None)

    return MergeRevalOutcome(success=True, rule_id=target_id)


def _revert_merge(
    db: Db,
    target_id: str,
    merged_row: Any,
    changed_variants: bool,
    changed_excluded: bool,
    pre_variants: Any,
    pre_excluded: Any,
) -> None:
    """Revert merged fields via CAS (restore pre-merge values)."""
    from ..policy import RUNTIME_POLICY_VERSION

    _revert_version = merged_row["rule_version"]
    _revert_status = merged_row["status"]
    _revert_rpv = merged_row["runtime_policy_version"]
    _now_revert = now_iso()
    _revert_sets = []
    _revert_params: list = []
    if changed_variants:
        _revert_sets.append("trigger_variants = ?")
        _revert_params.append(
            pre_variants
            if isinstance(pre_variants, str)
            else dumps_json(pre_variants)
            if pre_variants is not None
            else None
        )
    if changed_excluded:
        _revert_sets.append("excluded_contexts = ?")
        _revert_params.append(
            pre_excluded
            if isinstance(pre_excluded, str)
            else dumps_json(pre_excluded)
            if pre_excluded is not None
            else None
        )
    if _revert_sets:
        _revert_sets.append("rule_version = rule_version + 1")
        # Intentionally advances policy version (project convention: CAS updates always push forward)
        _revert_sets.append("runtime_policy_version = ?")
        _revert_params.append(RUNTIME_POLICY_VERSION)
        _revert_sets.append("updated_at = ?")
        _revert_params.append(_now_revert)
        _revert_params.extend([target_id, _revert_version, _revert_status])
        _rpv_where = (
            "AND runtime_policy_version = ?"
            if _revert_rpv
            else "AND runtime_policy_version IS NULL"
        )
        _rpv_params = (_revert_rpv,) if _revert_rpv else ()
        with db.transaction() as tx:
            cur = tx.execute(
                f"UPDATE rules SET {', '.join(_revert_sets)} "
                f"WHERE id = ? AND rule_version = ? AND status = ? {_rpv_where}",
                tuple(_revert_params) + _rpv_params,
            )
            if cur.rowcount == 0:
                log.warning(
                    "merge_reeval revert CAS failed rule=%s version=%s",
                    target_id,
                    _revert_version,
                )


# ---------------------------------------------------------------------------
# Cold fast lane check
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
    admission_model_id: str | None = None,
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
    now = now_iso()
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
    non_generalization_boundaries_json = dumps_json(
        rule_data.get("non_generalization_boundaries", [])
    )
    project_scope = "project" if project_id else "global"

    with db.transaction() as tx:
        existing_short_ids = {row["short_id"] for row in tx.execute("SELECT short_id FROM rules")}
        short_id = short_id_for(rule_id, existing_short_ids)
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
                    admission_model_id,
                    PROMPT_VERSIONS.get("admission_judge"),
                    None,
                    dumps_json(scores),
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

    now = now_iso()
    reason = merge_info.get("merge_rationale", merge_op)
    # CAS over all 4 fields: rule_id, rule_version, status, runtime_policy_version (spec section 13)
    rpv_where = (
        "AND runtime_policy_version = ?" if existing_rpv else "AND runtime_policy_version IS NULL"
    )
    rpv_params: tuple = (existing_rpv,) if existing_rpv else ()

    cas_applied = False
    with db.transaction() as tx:
        if merge_op == "replace_existing":
            cur = tx.execute(
                "UPDATE rules SET status = 'archived', archived_reason = ?, "
                "replacement_id = ?, rule_version = rule_version + 1, "
                f"runtime_policy_version = ?, updated_at = ? "
                f"WHERE id = ? AND rule_version = ? AND status = ? {rpv_where}",
                (
                    reason,
                    new_rule_id,
                    RUNTIME_POLICY_VERSION,
                    now,
                    target_rule_id,
                    existing_version,
                    existing_status,
                    *rpv_params,
                ),
            )
            cas_applied = cur.rowcount == 1
        elif merge_op == "suppress_existing":
            cur = tx.execute(
                "UPDATE rules SET status = 'suppressed', suppressed_at = ?, "
                "rule_version = rule_version + 1, "
                f"runtime_policy_version = ?, updated_at = ? "
                f"WHERE id = ? AND rule_version = ? AND status = ? {rpv_where}",
                (
                    now,
                    RUNTIME_POLICY_VERSION,
                    now,
                    target_rule_id,
                    existing_version,
                    existing_status,
                    *rpv_params,
                ),
            )
            cas_applied = cur.rowcount == 1
        elif merge_op == "archive_existing":
            cur = tx.execute(
                "UPDATE rules SET status = 'archived', archived_reason = ?, "
                "rule_version = rule_version + 1, "
                f"runtime_policy_version = ?, updated_at = ? "
                f"WHERE id = ? AND rule_version = ? AND status = ? {rpv_where}",
                (
                    reason,
                    RUNTIME_POLICY_VERSION,
                    now,
                    target_rule_id,
                    existing_version,
                    existing_status,
                    *rpv_params,
                ),
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

    now = now_iso()
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
        raw_current = (
            json.loads(rule_row["trigger_variants"]) if rule_row["trigger_variants"] else []
        )
        current = [_variant_entry(v) for v in raw_current]
        normalized_new = [_variant_entry(v) for v in new_variants]
        current_texts = {_variant_text(v) for v in current}
        added = [v for v in normalized_new if _variant_text(v) not in current_texts]
        if added:
            merged = current + added
            updates.append("trigger_variants = ?")
            params.append(dumps_json(merged))

    # Merge new excluded contexts
    new_excluded = new_rule_data.get("excluded_contexts", [])
    if new_excluded:
        current = json.loads(rule_row["excluded_contexts"]) if rule_row["excluded_contexts"] else []
        current_keys = {_excluded_context_key(e) for e in current}
        added = [e for e in new_excluded if _excluded_context_key(e) not in current_keys]
        if added:
            merged = current + added
            updates.append("excluded_contexts = ?")
            params.append(dumps_json(merged))

    # Merge near-miss examples
    new_near_miss = new_rule_data.get("near_miss_examples", [])
    if new_near_miss:
        current = (
            json.loads(rule_row["near_miss_examples"]) if rule_row["near_miss_examples"] else []
        )
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
        rpv_where = (
            "AND runtime_policy_version = ?"
            if current_rpv
            else "AND runtime_policy_version IS NULL"
        )
        rpv_params: tuple = (current_rpv,) if current_rpv else ()
        params.append(target_rule_id)
        params.append(current_version)
        params.append(current_status)
        with db.transaction() as tx:
            cur = tx.execute(
                f"UPDATE rules SET {', '.join(updates)} "
                f"WHERE id = ? AND rule_version = ? AND status = ? {rpv_where}",
                tuple(params) + rpv_params,
            )
            if cur.rowcount == 0:
                log.warning(
                    "non_destructive_merge CAS failed rule=%s version=%s",
                    target_rule_id,
                    current_version,
                )


def _excluded_context_key(entry: Any) -> tuple[str, str]:
    if isinstance(entry, dict):
        entry_id = str(entry.get("id") or "").strip()
        if entry_id:
            return ("id", entry_id)
        return ("body", dumps_json(entry))
    return ("body", dumps_json(entry))
