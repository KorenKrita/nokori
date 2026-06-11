"""Coverage tests for posthoc/evaluator.py uncovered paths.

Covers: build_posthoc_prompt, parse_posthoc_output alias resolution,
run_posthoc_evaluation (LLM retry, cache, failure modes).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from nokori.posthoc.evaluator import (
    _POSTHOC_MAX_ATTEMPTS,
    _POSTHOC_RESULT_CACHE,
    build_posthoc_prompt,
    compute_attribution_weight,
    parse_posthoc_output,
    run_posthoc_evaluation,
)


class TestBuildPosthocPrompt:
    def test_basic_prompt_structure(self):
        result = build_posthoc_prompt({
            "injected_suggestion": "use lease instead of force push",
            "injection_context": "user asked to push to main",
            "transcript_window": "assistant: I'll use a lease-based push",
        })
        assert "## Prior Reminder" in result
        assert "use lease instead of force push" in result
        assert "## Context That Triggered the Reminder" in result
        assert "user asked to push to main" in result
        assert "## Transcript Window After Reminder" in result

    def test_feedback_section_included_when_present(self):
        result = build_posthoc_prompt({
            "injected_suggestion": "test",
            "injection_context": "ctx",
            "transcript_window": "window",
            "feedback": "user said thanks",
        })
        assert "## Agent/CLI Feedback" in result
        assert "user said thanks" in result

    def test_feedback_section_absent_when_none(self):
        result = build_posthoc_prompt({
            "injected_suggestion": "test",
            "injection_context": "ctx",
            "transcript_window": "window",
            "feedback": None,
        })
        assert "## Agent/CLI Feedback" not in result

    def test_decision_features_serialized(self):
        result = build_posthoc_prompt({
            "injected_suggestion": "test",
            "injection_context": "ctx",
            "transcript_window": "window",
            "decision_features": {"trigger_idf_sum": 3.5, "bm25_score": 0.8},
        })
        assert "## Decision Features" in result
        assert "trigger_idf_sum" in result

    def test_empty_input_produces_valid_prompt(self):
        result = build_posthoc_prompt({})
        assert "## Prior Reminder" in result
        assert "## Instructions" in result


class TestParsePosthocOutputAliases:
    def test_without_rule_alias_normalized(self):
        data = json.dumps({
            "label": "observed_useful",
            "reason_code": "useful_prevented_error",
            "without_rule": "no",
        })
        result = parse_posthoc_output(data)
        assert result["would_likely_have_happened_without_rule"] == "no"

    def test_counterfactual_alias_normalized(self):
        data = json.dumps({
            "label": "harmful",
            "reason_code": "harmful_distracted",
            "counterfactual": "yes",
        })
        result = parse_posthoc_output(data)
        assert result["would_likely_have_happened_without_rule"] == "yes"

    def test_missing_attribution_defaults_to_unclear(self):
        data = json.dumps({
            "label": "irrelevant",
            "reason_code": "irrelevant_not_applicable",
        })
        result = parse_posthoc_output(data)
        assert result["would_likely_have_happened_without_rule"] == "unclear"

    def test_reason_alias_from_reason_field(self):
        data = json.dumps({
            "label": "observed_useful",
            "reason": "useful_prevented_error",
            "would_likely_have_happened_without_rule": "no",
        })
        result = parse_posthoc_output(data)
        assert result["reason_code"] == "useful_prevented_error"

    def test_reason_alias_from_code_field(self):
        data = json.dumps({
            "label": "harmful",
            "code": "harmful_wrong_scope",
            "would_likely_have_happened_without_rule": "yes",
        })
        result = parse_posthoc_output(data)
        assert result["reason_code"] == "harmful_wrong_scope"

    def test_invalid_reason_alias_raises(self):
        data = json.dumps({
            "label": "observed_useful",
            "reason": "not_a_valid_code",
            "would_likely_have_happened_without_rule": "no",
        })
        with pytest.raises(ValueError, match="reason_code"):
            parse_posthoc_output(data)

    def test_missing_reason_code_no_alias_raises(self):
        data = json.dumps({
            "label": "observed_useful",
            "would_likely_have_happened_without_rule": "no",
        })
        with pytest.raises(ValueError, match="missing required field 'reason_code'"):
            parse_posthoc_output(data)

    def test_non_dict_response_raises(self):
        with pytest.raises(ValueError, match="expected JSON object"):
            parse_posthoc_output('"just a string"')

    def test_completely_invalid_json_raises(self):
        with pytest.raises(ValueError, match="invalid JSON"):
            parse_posthoc_output("not json at all {{{")


class TestRunPosthocEvaluation:
    def setup_method(self):
        _POSTHOC_RESULT_CACHE.clear()

    def test_successful_evaluation(self):
        llm = MagicMock()
        llm.call.return_value = json.dumps({
            "label": "observed_useful",
            "reason_code": "useful_prevented_error",
            "rule_application_evidence": "agent followed the rule",
            "would_likely_have_happened_without_rule": "no",
        })
        result = run_posthoc_evaluation(llm, {
            "injected_suggestion": "use lease",
            "injection_context": "push to main",
            "transcript_window": "used lease",
        })
        assert result is not None
        assert result["label"] == "observed_useful"
        assert result["attribution_weight"] == 1.0

    def test_idempotency_cache(self):
        llm = MagicMock()
        llm.call.return_value = json.dumps({
            "label": "irrelevant",
            "reason_code": "irrelevant_not_applicable",
            "rule_application_evidence": "not used",
            "would_likely_have_happened_without_rule": "yes",
        })
        evaluator_input = {
            "injected_suggestion": "test",
            "injection_context": "ctx",
            "transcript_window": "window",
        }
        result1 = run_posthoc_evaluation(llm, evaluator_input)
        result2 = run_posthoc_evaluation(llm, evaluator_input)
        assert result1 == result2
        assert llm.call.call_count == 1

    def test_llm_failure_retries_then_returns_none(self):
        llm = MagicMock()
        llm.call.side_effect = RuntimeError("timeout")
        result = run_posthoc_evaluation(llm, {
            "injected_suggestion": "x",
            "injection_context": "y",
            "transcript_window": "z",
        })
        assert result is None
        assert llm.call.call_count == _POSTHOC_MAX_ATTEMPTS

    def test_parse_failure_retries_then_returns_none(self):
        llm = MagicMock()
        llm.call.return_value = "not valid json {{"
        result = run_posthoc_evaluation(llm, {
            "injected_suggestion": "x",
            "injection_context": "y",
            "transcript_window": "z",
        })
        assert result is None
        assert llm.call.call_count == _POSTHOC_MAX_ATTEMPTS

    def test_first_attempt_fails_second_succeeds(self):
        llm = MagicMock()
        llm.call.side_effect = [
            RuntimeError("transient"),
            json.dumps({
                "label": "plausible_useful",
                "reason_code": "useful_improved_quality",
                "rule_application_evidence": "quality improved",
                "would_likely_have_happened_without_rule": "unclear",
            }),
        ]
        result = run_posthoc_evaluation(llm, {
            "injected_suggestion": "improve",
            "injection_context": "code review",
            "transcript_window": "better code",
        })
        assert result is not None
        assert result["label"] == "plausible_useful"
        assert result["attribution_weight"] == 0.3


class TestComputeAttributionWeightEdgeCases:
    def test_unclear_label_returns_zero(self):
        assert compute_attribution_weight({"label": "unclear", "would_likely_have_happened_without_rule": "no"}) == 0.0

    def test_unknown_label_returns_zero(self):
        assert compute_attribution_weight({"label": "something_else"}) == 0.0
