"""Cold-path verification stage: synthetic evaluation and final admission policy."""
from __future__ import annotations

import json
from typing import Any

from ..db import Db
from ..policy import COLD_FAST_LANE, SourceOrigin
from ..utils.logging import get_logger
from ._llm_call import (
    CircuitBreakerOpenError,
    call_llm_role as _call_llm_role,
    prompt_text as _prompt_text,
    role_max_tokens as _role_max_tokens,
    role_timeout as _role_timeout,
)
from .roles import resolve_model_id, validate_role_output

log = get_logger("nokori.cold.verify")


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

    Note: these thresholds (from ColdFastLaneThresholds) are intentionally
    stricter than the normal admission accept thresholds in _enforce_admission_policy.
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
    if scores.get("action_clarity", 0) < thresholds.action_clarity_min:
        return False
    if scores.get("generalization_safety", 0) < thresholds.generalization_safety_min:
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
        "Produce diverse, tricky test cases. Near-miss cases must be genuinely hard to distinguish.\n\n"
        "IMPORTANT: Generate cases in BOTH English AND Chinese. At least one positive case "
        "and one near_miss case must have Chinese prompts, to verify bilingual matching works.\n\n"
        "Output a single JSON object with a \"cases\" array. Each case object has:\n\n"
        "REQUIRED fields per case:\n"
        "- \"prompt\" (string, REQUIRED): a realistic developer prompt/context\n"
        "- \"case_type\" (string, REQUIRED): one of \"positive\", \"medium_positive\", \"near_miss\", \"negative\"\n"
        "- \"expected_min_decision\" (string, REQUIRED): the minimum expected decision level.\n"
        "  For positive: \"warm\" or \"hot\". For medium_positive: \"cold\". For near_miss/negative: \"cold\".\n"
        "- \"expected_max_decision\" (string, REQUIRED): the maximum allowed decision level.\n"
        "  For positive: \"hot\". For medium_positive: \"warm\". For near_miss/negative: \"cold\".\n"
        "- \"rationale\" (string, REQUIRED): brief explanation of why this case should produce the expected decision\n\n"
        "Example output:\n"
        "```json\n"
        "{\n"
        "  \"cases\": [\n"
        "    {\n"
        "      \"prompt\": \"I need to force push to the main branch to fix a rebase issue\",\n"
        "      \"case_type\": \"positive\",\n"
        "      \"expected_min_decision\": \"warm\",\n"
        "      \"expected_max_decision\": \"hot\",\n"
        "      \"rationale\": \"Contains force push + shared branch context, rule should fire\"\n"
        "    },\n"
        "    {\n"
        "      \"prompt\": \"I want to push my changes to the release branch\",\n"
        "      \"case_type\": \"medium_positive\",\n"
        "      \"expected_min_decision\": \"cold\",\n"
        "      \"expected_max_decision\": \"warm\",\n"
        "      \"rationale\": \"Mentions pushing to shared branch but no force flag — partial match\"\n"
        "    },\n"
        "    {\n"
        "      \"prompt\": \"Let me push my changes to my personal feature branch\",\n"
        "      \"case_type\": \"near_miss\",\n"
        "      \"expected_min_decision\": \"cold\",\n"
        "      \"expected_max_decision\": \"cold\",\n"
        "      \"rationale\": \"Push without force, personal branch — rule should not fire\"\n"
        "    },\n"
        "    {\n"
        "      \"prompt\": \"How do I set up ESLint in my React project?\",\n"
        "      \"case_type\": \"negative\",\n"
        "      \"expected_min_decision\": \"cold\",\n"
        "      \"expected_max_decision\": \"cold\",\n"
        "      \"rationale\": \"Completely unrelated to git push\"\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n"
        "Output ONLY the JSON object, no markdown fences, no extra text."
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
        if isinstance(cases, list):
            return cases
        if isinstance(cases, dict) and "cases" in cases:
            return cases["cases"]
        raise ValueError("synthetic_eval_generator returned no cases")
    except CircuitBreakerOpenError:
        raise
    except (json.JSONDecodeError, ValueError):
        raise  # Propagate for retry (spec section 13)

