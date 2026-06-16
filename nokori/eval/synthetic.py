"""Synthetic retrieval evaluation runner for the autonomous rule quality flywheel.

Implements section 6.6 of the flywheel plan. Executes deterministic retrieval
tests against compiled matchers and applicability logic to verify that a rule
retrieves correctly on positive cases and does NOT inject on near-miss,
negative, or global adversarial cases.

The synthetic_eval_generator LLM role produces the cases; this module runs
them deterministically without LLM calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..matcher.compiler import COMPILER_VERSION as MATCHER_COMPILER_VERSION, CompiledMatcher
from ..matcher.runtime import MatchResult, evaluate_match
from ..policy import RUNTIME_POLICY_VERSION
from ..search.applicability import ApplicabilityDecision, evaluate_applicability
from ..search.idf_stats import TOKENIZER_VERSION, IdfPoolStats, compute_trigger_idf_sum
from ..utils.time import now_iso

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DECISION_RANK: dict[str, int] = {"cold": 0, "warm": 1, "hot": 2, "gate": 3}

# TODO: import from source module once a canonical definition exists
CONCEPT_COMPILER_VERSION: str = "1.0.0"
# TODO: import from source module once a canonical definition exists
EMBEDDING_PROFILE_VERSION: str = "1.0.0"
BENCHMARK_VERSION: str = "1.0.0"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyntheticEvalResult:
    """Outcome of synthetic retrieval evaluation for a single rule."""

    rule_id: str
    rule_version: int
    runtime_policy_version: str
    tokenizer_version: str
    matcher_compiler_version: str
    concept_compiler_version: str
    embedding_profile_version: str
    trigger_idf_pool_version: str
    benchmark_version: str
    cases: list[dict[str, Any]]
    results: list[dict[str, Any]]
    passed: bool


# ---------------------------------------------------------------------------
# Decision comparison helpers
# ---------------------------------------------------------------------------


def decision_meets_min(actual: str, expected_min: str) -> bool:
    """Check whether actual decision meets or exceeds expected_min.

    Returns True if actual >= expected_min in the DECISION_RANK ordering.
    """
    return DECISION_RANK.get(actual, -1) >= DECISION_RANK.get(expected_min, 0)


def decision_within_max(actual: str, expected_max: str) -> bool:
    """Check whether actual decision is at or below expected_max.

    Returns True if actual <= expected_max in the DECISION_RANK ordering.
    """
    return DECISION_RANK.get(actual, 99) <= DECISION_RANK.get(expected_max, 0)


# ---------------------------------------------------------------------------
# Core evaluation runner
# ---------------------------------------------------------------------------


def _evaluate_single_case(
    case: dict[str, Any],
    rule_data: dict[str, Any],
    compiled_matcher: CompiledMatcher,
    idf_stats: IdfPoolStats,
) -> dict[str, Any]:
    """Run matcher + applicability on a single eval case and return result dict."""
    prompt = case["prompt"]
    case_type = case["case_type"]

    # Run the deterministic matcher
    match_result: MatchResult = evaluate_match(
        matcher=compiled_matcher,
        prompt_text=prompt,
    )

    # Compute trigger IDF sum from matched anchors
    matched_tokens = list(match_result.matched_trigger_anchors)
    trigger_idf_sum = compute_trigger_idf_sum(matched_tokens, idf_stats)

    # Determine distinct trigger terms (non-generic matched anchors)
    distinct_trigger_terms = len(match_result.matched_trigger_anchors)

    # Run applicability evaluation
    # Use active status and reminder severity as the baseline for eval
    # (the eval tests whether the rule CAN inject, not state permissions)
    rule_status = rule_data.get("status", "active")
    rule_severity = rule_data.get("severity", "reminder")

    applicability_result = evaluate_applicability(
        rule_status=rule_status,
        rule_severity=rule_severity,
        rule_first_observed_useful_at=rule_data.get("first_observed_useful_at"),
        trigger_idf_sum=trigger_idf_sum,
        trigger_coverage=match_result.trigger_coverage,
        distinct_trigger_terms=distinct_trigger_terms,
        strong_variant_phrase_hit=len(match_result.strong_variant_hits) > 0,
        required_concepts_match=match_result.required_concepts_match,
        excluded_context_hit=len(match_result.excluded_context_hits) > 0,
        excluded_context_override_passed=match_result.excluded_context_override_passed,
        action_only_match=match_result.action_only_match,
        search_only_match=match_result.search_only_match,
        embedding_only_match=False,
        idf_stats_available=idf_stats.rule_pool_size > 0,
        pool_size=idf_stats.rule_pool_size,
        has_tool_input=False,
        tool_evidence_passed=False,
    )

    actual_decision: ApplicabilityDecision = applicability_result.decision

    # Determine pass/fail for this case
    case_passed = _case_passes(case_type, actual_decision, case)

    return {
        "prompt": prompt,
        "case_type": case_type,
        "actual_decision": actual_decision,
        "trigger_coverage": match_result.trigger_coverage,
        "trigger_idf_sum": trigger_idf_sum,
        "required_concepts_match": match_result.required_concepts_match,
        "strong_variant_hits": list(match_result.strong_variant_hits),
        "excluded_context_hits": list(match_result.excluded_context_hits),
        "excluded_context_override_passed": match_result.excluded_context_override_passed,
        "case_passed": case_passed,
        "reason": applicability_result.reason,
    }


def _case_passes(case_type: str, actual_decision: str, case: dict[str, Any]) -> bool:
    """Determine whether a single case passed based on type and expectations."""
    if case_type == "positive":
        expected_min = case.get("expected_min_decision", "warm")
        return decision_meets_min(actual_decision, expected_min)

    if case_type == "medium_positive":
        # May warm but need not hot. Must not be cold.
        expected_max = case.get("expected_max_decision", "warm")
        within_ceiling = decision_within_max(actual_decision, expected_max)
        # Per spec: medium_positive may warm but need not hot.
        # If it's cold, that's acceptable (not a hard failure for medium).
        # But if it exceeds expected_max, that's a failure.
        if actual_decision == "cold":
            return True  # medium_positive being cold is acceptable
        return within_ceiling

    if case_type == "near_miss":
        expected_max = case.get("expected_max_decision", "cold")
        return decision_within_max(actual_decision, expected_max)

    if case_type == "negative":
        expected_max = case.get("expected_max_decision", "cold")
        return decision_within_max(actual_decision, expected_max)

    if case_type == "global_adversarial":
        # Must always be cold
        return actual_decision == "cold"

    # Unknown case type: fail conservatively
    return False


def run_synthetic_eval(
    rule_data: dict[str, Any],
    compiled_matcher: CompiledMatcher,
    idf_stats: IdfPoolStats,
    eval_cases: list[dict[str, Any]],
    global_adversarial_cases: list[dict[str, Any]] | None = None,
) -> SyntheticEvalResult:
    """Run synthetic retrieval evaluation for a rule.

    For each case, runs evaluate_match then evaluate_applicability and compares
    the actual decision against expected thresholds per case type.

    Pass rules:
    - All positive cases must achieve at least expected_min_decision (warm or better)
    - All near_miss cases must be at most expected_max_decision (cold)
    - All negative cases must be cold
    - All global adversarial cases must be cold
    - medium_positive: may warm but need not hot

    Failure:
    - Any near_miss or negative that would inject -> fail
    - Any global adversarial that would inject -> fail

    Args:
        rule_data: Dict with rule metadata (status, severity, first_observed_useful_at, id, version).
        compiled_matcher: Pre-compiled matcher for this rule.
        idf_stats: Current IDF pool statistics.
        eval_cases: Rule-local generated eval cases.
        global_adversarial_cases: Checked-in global adversarial cases.

    Returns:
        SyntheticEvalResult with per-case results and overall pass/fail.
    """
    all_cases: list[dict[str, Any]] = list(eval_cases)

    # Add global adversarial cases with case_type marker
    if global_adversarial_cases:
        all_cases.extend(
            {**adv_case, "case_type": "global_adversarial"}
            for adv_case in global_adversarial_cases
        )

    results: list[dict[str, Any]] = []
    overall_passed = True

    for case in all_cases:
        result = _evaluate_single_case(case, rule_data, compiled_matcher, idf_stats)
        results.append(result)

        if not result["case_passed"]:
            overall_passed = False

    # Additional hard failure checks: any near_miss/negative/adversarial that injects
    for result in results:
        case_type = result["case_type"]
        actual = result["actual_decision"]
        if (
            case_type in ("near_miss", "negative", "global_adversarial")
            and DECISION_RANK.get(actual, 0) >= DECISION_RANK["warm"]
        ):
            overall_passed = False

    # Check that at least one positive case exists and passes
    positive_cases = [r for r in results if r["case_type"] == "positive"]
    if not positive_cases or not all(r["case_passed"] for r in positive_cases):
        overall_passed = False

    return SyntheticEvalResult(
        rule_id=rule_data.get("id", ""),
        rule_version=rule_data.get("version", 0),
        runtime_policy_version=RUNTIME_POLICY_VERSION,
        tokenizer_version=TOKENIZER_VERSION,
        matcher_compiler_version=MATCHER_COMPILER_VERSION,
        concept_compiler_version=CONCEPT_COMPILER_VERSION,
        embedding_profile_version=EMBEDDING_PROFILE_VERSION,
        trigger_idf_pool_version=idf_stats.pool_version,
        benchmark_version=BENCHMARK_VERSION,
        cases=[dict(c.items()) for c in all_cases],
        results=results,
        passed=overall_passed,
    )


# ---------------------------------------------------------------------------
# Eval case prompt generation
# ---------------------------------------------------------------------------


def generate_eval_cases_prompt(
    rule_trigger: str,
    rule_action: str,
    concepts: list[dict[str, Any]],
    near_miss_examples: list[str],
) -> str:
    """Build the prompt for the synthetic_eval_generator LLM role.

    This prompt instructs the LLM to produce diverse positive, medium_positive,
    near_miss, and negative eval cases for a specific rule.

    Args:
        rule_trigger: The rule's canonical trigger text.
        rule_action: The rule's action instruction.
        concepts: List of concept dicts (id, label, aliases).
        near_miss_examples: Author-provided near-miss examples from the rule.

    Returns:
        Formatted prompt string for the synthetic_eval_generator role.
    """
    concepts_text = json.dumps(concepts, indent=2, ensure_ascii=False)
    near_miss_text = json.dumps(near_miss_examples, ensure_ascii=False)

    return f"""\
Generate synthetic evaluation cases for the following rule.

<rule_trigger>
{rule_trigger}
</rule_trigger>

<rule_action>
{rule_action}
</rule_action>

<required_concepts>
{concepts_text}
</required_concepts>

<near_miss_examples>
{near_miss_text}
</near_miss_examples>

Generate cases in these categories:

1. POSITIVE (3-5 cases): Prompts where this rule clearly applies. Must contain
   the required concepts and trigger evidence. Vary the phrasing, context length,
   and surrounding text. Expected: at least warm.

2. MEDIUM_POSITIVE (2-3 cases): Prompts where the rule topic is present but
   context is ambiguous or partial. Expected: may warm, may cold.

3. NEAR_MISS (3-5 cases): Prompts that look similar but the rule should NOT
   apply. Use the near_miss_examples as inspiration. These must share vocabulary
   with the trigger but differ in intent or scope. Expected: must be cold.

4. NEGATIVE (2-3 cases): Completely unrelated prompts that happen to contain
   one or two tokens from the trigger area. Expected: must be cold.

Rules for case generation:
- Near-miss cases must be genuinely tricky. Do not make them obviously unrelated.
- Positive cases must vary in length and style (short prompt, long prompt,
  tool context, code context).
- Do not reuse exact text from the trigger or near_miss_examples.
- Each case must include a realistic prompt a developer might write.

Output strict JSON array:
[
  {{"prompt": "...", "case_type": "positive", "expected_min_decision": "warm", "rationale": "Contains required concepts and trigger evidence"}},
  {{"prompt": "...", "case_type": "medium_positive", "expected_max_decision": "warm", "rationale": "Topic present but context is ambiguous"}},
  {{"prompt": "...", "case_type": "near_miss", "expected_max_decision": "cold", "rationale": "Shares vocabulary but different intent"}},
  {{"prompt": "...", "case_type": "negative", "expected_max_decision": "cold", "rationale": "Completely unrelated topic"}}
]"""


# ---------------------------------------------------------------------------
# Stale eval invalidation
# ---------------------------------------------------------------------------


def is_eval_stale(
    stored: SyntheticEvalResult,
    current_idf_pool_version: str,
    current_matcher_version: str,
    current_rule_version: int,
) -> bool:
    """Check whether stored eval results match current component versions."""
    return (
        stored.trigger_idf_pool_version != current_idf_pool_version
        or stored.matcher_compiler_version != current_matcher_version
        or stored.rule_version != current_rule_version
    )


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------


def store_eval_result(db: Any, result: SyntheticEvalResult) -> None:
    """Persist a SyntheticEvalResult to the rule_synthetic_evals table.

    Args:
        db: Database instance with transaction() context manager.
        result: The eval result to store.
    """
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_synthetic_evals "
            "(rule_id, rule_version, runtime_policy_version, tokenizer_version, "
            "matcher_compiler_version, concept_compiler_version, "
            "embedding_profile_version, trigger_idf_pool_version, "
            "benchmark_version, eval_cases, eval_results, expected_decisions, "
            "passed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.rule_id,
                result.rule_version,
                result.runtime_policy_version,
                result.tokenizer_version,
                result.matcher_compiler_version,
                result.concept_compiler_version,
                result.embedding_profile_version,
                result.trigger_idf_pool_version,
                result.benchmark_version,
                json.dumps(result.cases, ensure_ascii=False),
                json.dumps(result.results, ensure_ascii=False),
                json.dumps(
                    [
                        {
                            "case_type": c.get("case_type"),
                            "expected_min_decision": c.get("expected_min_decision"),
                            "expected_max_decision": c.get("expected_max_decision"),
                        }
                        for c in result.cases
                    ],
                    ensure_ascii=False,
                ),
                1 if result.passed else 0,
                now_iso(),
            ),
        )
