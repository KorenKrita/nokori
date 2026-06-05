"""Cold-path LLM role definitions, schemas, and helpers.

Pure utility module — no imports from nokori.config to avoid coupling.
All configuration is accepted as plain dicts/strings.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


# --- Role IDs ---

ROLE_IDS: tuple[str, ...] = (
    "extractor",
    "admission_judge",
    "rule_rewriter",
    "final_judge",
    "merge_planner",
    "synthetic_eval_generator",
    "posthoc_evaluator",
)


# --- Prompt Versions ---

PROMPT_VERSIONS: dict[str, str] = {
    "extractor": "1.0.0",
    "admission_judge": "1.0.0",
    "rule_rewriter": "1.0.0",
    "final_judge": "1.0.0",
    "merge_planner": "1.0.0",
    "synthetic_eval_generator": "1.0.0",
    "posthoc_evaluator": "1.0.0",
}


def compute_prompt_version(role: str, system_prompt: str) -> str:
    """Derive prompt version from content hash so prompt edits auto-invalidate cache."""
    base = PROMPT_VERSIONS.get(role, "1.0.0")
    content_hash = hashlib.sha256(system_prompt.encode()).hexdigest()[:8]
    return f"{base}-{content_hash}"


# --- Per-Role Default Config ---

DEFAULT_MAX_TOKENS: dict[str, int] = {
    "extractor": 4000,
    "admission_judge": 2000,
    "rule_rewriter": 4000,
    "final_judge": 2000,
    "merge_planner": 2000,
    "synthetic_eval_generator": 4000,
    "posthoc_evaluator": 2000,
}

DEFAULT_TIMEOUTS: dict[str, int] = {
    "extractor": 60,
    "admission_judge": 30,
    "rule_rewriter": 30,
    "final_judge": 30,
    "merge_planner": 30,
    "synthetic_eval_generator": 30,
    "posthoc_evaluator": 30,
}


# --- Output Schemas ---

EXTRACTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "trigger_draft": {"type": "string"},
                    "action_draft": {"type": "string"},
                    "behavior_draft": {"type": "string"},
                    "source_type": {
                        "type": "string",
                        "enum": ["correction", "preference", "solution", "anti_pattern"],
                    },
                    "confidence_guess": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "evidence_quotes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "non_generalization_boundaries": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "required_concepts_draft": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "excluded_contexts_draft": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "search_terms_draft": {"type": "object"},
                    "trigger_variants_draft": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "trigger_draft",
                    "action_draft",
                    "behavior_draft",
                    "source_type",
                    "confidence_guess",
                    "evidence_quotes",
                    "non_generalization_boundaries",
                    "required_concepts_draft",
                    "excluded_contexts_draft",
                    "search_terms_draft",
                    "trigger_variants_draft",
                ],
            },
        },
    },
    "required": ["candidates"],
}

ADMISSION_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {
                "overall_quality": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence_support": {"type": "number", "minimum": 0, "maximum": 1},
                "trigger_specificity": {"type": "number", "minimum": 0, "maximum": 1},
                "action_clarity": {"type": "number", "minimum": 0, "maximum": 1},
                "scope_control": {"type": "number", "minimum": 0, "maximum": 1},
                "generalization_safety": {"type": "number", "minimum": 0, "maximum": 1},
                "retrieval_readiness": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "overall_quality",
                "evidence_support",
                "trigger_specificity",
                "action_clarity",
                "scope_control",
                "generalization_safety",
                "retrieval_readiness",
            ],
        },
        "decision": {
            "type": "string",
            "enum": ["accept", "revise", "reject"],
        },
        "reasoning": {"type": "string"},
    },
    "required": ["scores", "decision", "reasoning"],
}

RULE_REWRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "trigger_canonical": {"type": "string"},
        "required_concept_groups": {"type": "array", "items": {"type": "object"}},
        "excluded_contexts": {"type": "array", "items": {"type": "object"}},
        "action_instruction": {"type": "string"},
        "severity": {"type": "string", "enum": ["reminder", "high_risk", "gate_eligible"]},
        "scope": {"type": "object"},
        "rewrite_rationale": {"type": "string"},
    },
    "required": [
        "trigger_canonical",
        "required_concept_groups",
        "excluded_contexts",
        "action_instruction",
        "severity",
        "scope",
        "rewrite_rationale",
    ],
}

FINAL_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["accept_active", "accept_candidate", "reject"],
        },
        "reasoning": {"type": "string"},
        "evidence_citations": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["decision", "reasoning", "evidence_citations"],
}

MERGE_PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relation_shape": {
            "type": "string",
            "enum": [
                "equivalent", "new_broader", "new_narrower", "overlap",
                "complementary", "contradiction", "obsolete", "unrelated",
                "split_required",
            ],
        },
        "new_rule_safety": {
            "type": "string",
            "enum": ["safe", "unsafe", "uncertain"],
        },
        "operation_safety": {
            "type": "string",
            "enum": ["safe", "unsafe", "uncertain"],
        },
        "quality_winner": {
            "type": "string",
            "enum": ["new", "existing", "both", "neither"],
        },
        "operation": {
            "type": "string",
            "enum": [
                "merge_into_existing", "update_existing_fields",
                "replace_existing", "keep_both", "reject_new",
                "suppress_existing", "archive_existing", "split_required",
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "target_rule_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "relation_shape", "new_rule_safety", "operation_safety",
        "quality_winner", "operation", "confidence", "reason",
    ],
}

SYNTHETIC_EVAL_GENERATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "case_type": {
                        "type": "string",
                        "enum": ["positive", "medium_positive", "near_miss", "negative"],
                    },
                    "expected_min_decision": {
                        "type": "string",
                        "enum": ["cold", "warm", "hot", "gate"],
                    },
                    "expected_max_decision": {
                        "type": "string",
                        "enum": ["cold", "warm", "hot", "gate"],
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["prompt", "case_type", "expected_min_decision", "expected_max_decision", "rationale"],
            },
        },
    },
    "required": ["cases"],
}

POSTHOC_EVALUATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "enum": [
                "observed_useful",
                "plausible_useful",
                "irrelevant",
                "harmful",
                "unclear",
            ],
        },
        "reason_code": {
            "type": "string",
            "enum": [
                "useful_prevented_error",
                "useful_improved_quality",
                "useful_followed_preference",
                "irrelevant_not_applicable",
                "irrelevant_redundant",
                "irrelevant_unused",
                "harmful_distracted",
                "harmful_wrong_scope",
                "harmful_blocked_valid_action",
            ],
        },
        "rule_application_evidence": {"type": "string"},
        "would_likely_have_happened_without_rule": {
            "type": "string",
            "enum": ["yes", "no", "unclear"],
        },
    },
    "required": [
        "label",
        "reason_code",
        "rule_application_evidence",
        "would_likely_have_happened_without_rule",
    ],
}

ROLE_SCHEMAS: dict[str, dict[str, Any]] = {
    "extractor": EXTRACTOR_SCHEMA,
    "admission_judge": ADMISSION_JUDGE_SCHEMA,
    "rule_rewriter": RULE_REWRITER_SCHEMA,
    "final_judge": FINAL_JUDGE_SCHEMA,
    "merge_planner": MERGE_PLANNER_SCHEMA,
    "synthetic_eval_generator": SYNTHETIC_EVAL_GENERATOR_SCHEMA,
    "posthoc_evaluator": POSTHOC_EVALUATOR_SCHEMA,
}


# --- Schema Validation Helpers ---


def _validate_type(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    """Minimal JSON schema type/required/enum validation. Returns error messages."""
    errors: list[str] = []
    expected_type = schema.get("type")

    if expected_type == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object, got {type(value).__name__}"]
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}.{req}: required field missing")
        props = schema.get("properties", {})
        for key, prop_schema in props.items():
            if key in value:
                errors.extend(_validate_type(value[key], prop_schema, f"{path}.{key}"))

    elif expected_type == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array, got {type(value).__name__}"]
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(value):
                errors.extend(_validate_type(item, items_schema, f"{path}[{i}]"))

    elif expected_type == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string, got {type(value).__name__}")
        elif "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: value {value!r} not in {schema['enum']}")

    elif expected_type == "number":
        if not isinstance(value, (int, float)):
            errors.append(f"{path}: expected number, got {type(value).__name__}")
        else:
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{path}: {value} < minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{path}: {value} > maximum {schema['maximum']}")

    elif expected_type == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected boolean, got {type(value).__name__}")

    return errors


def validate_role_output(role: str, raw_json_str: str) -> dict[str, Any]:
    """Parse and validate raw JSON output against a role's schema.

    Returns the parsed dict on success.
    Raises ValueError with details on parse or validation failure.
    """
    if role not in ROLE_SCHEMAS:
        raise ValueError(f"unknown role: {role}")

    from ..llm.json_payload import parse_json_payload

    try:
        data = json.loads(raw_json_str)
    except json.JSONDecodeError:
        data = parse_json_payload(raw_json_str)
        if data is None:
            raise ValueError(f"{role}: invalid JSON: could not extract JSON from response")

    if not isinstance(data, dict):
        raise ValueError(f"{role}: expected JSON object at top level, got {type(data).__name__}")

    data = _normalize_role_output(role, data)

    errors = _validate_type(data, ROLE_SCHEMAS[role], "$")
    if errors:
        raise ValueError(f"{role}: schema validation failed: {'; '.join(errors)}")

    return data


_ADMISSION_SCORE_KEYS = (
    "overall_quality", "evidence_support", "trigger_specificity",
    "action_clarity", "scope_control", "generalization_safety", "retrieval_readiness",
)


def _normalize_role_output(role: str, data: dict[str, Any]) -> dict[str, Any]:
    """Fix common LLM schema deviations before validation."""
    if role == "admission_judge":
        # Fix 1: scores flattened to top level
        if "scores" not in data:
            scores = {}
            for k in _ADMISSION_SCORE_KEYS:
                if k in data:
                    scores[k] = data.pop(k)
            if scores:
                data["scores"] = scores
        # Fix 2: numeric strings -> float
        if isinstance(data.get("scores"), dict):
            for k, v in data["scores"].items():
                if isinstance(v, str):
                    try:
                        data["scores"][k] = float(v)
                    except ValueError:
                        pass
        # Fix 3: missing reasoning
        if "reasoning" not in data:
            data["reasoning"] = data.pop("rationale", data.pop("reason", data.pop("explanation", "")))

    elif role == "final_judge":
        # Fix: missing evidence_citations
        if "evidence_citations" not in data:
            data["evidence_citations"] = data.pop("citations", data.pop("evidence", []))
        if isinstance(data.get("evidence_citations"), str):
            data["evidence_citations"] = [data["evidence_citations"]]
        if "reasoning" not in data:
            data["reasoning"] = data.pop("rationale", data.pop("reason", data.pop("explanation", "")))

    elif role == "merge_planner":
        # Fix: target_rule_ids missing or as single string
        if "target_rule_ids" not in data:
            tid = data.pop("target_rule_id", data.pop("target_id", None))
            data["target_rule_ids"] = [tid] if tid else []
        if isinstance(data.get("target_rule_ids"), str):
            data["target_rule_ids"] = [data["target_rule_ids"]]
        if "reason" not in data:
            data["reason"] = data.pop("reasoning", data.pop("rationale", ""))
        # Fix: confidence as string
        if isinstance(data.get("confidence"), str):
            try:
                data["confidence"] = float(data["confidence"])
            except ValueError:
                data["confidence"] = 0.5

    elif role == "rule_rewriter":
        # Fix: trigger/action field aliases
        if "trigger_canonical" not in data:
            data["trigger_canonical"] = data.pop("trigger", data.pop("trigger_text", ""))
        if "action_instruction" not in data:
            data["action_instruction"] = data.pop("action", data.pop("instruction", ""))
        # Fix: missing arrays
        if "required_concept_groups" not in data:
            data["required_concept_groups"] = data.pop("concepts", data.pop("concept_groups", []))
        if "excluded_contexts" not in data:
            data["excluded_contexts"] = data.pop("exclusions", [])
        # Fix: missing scope
        if "scope" not in data:
            scope = {}
            for k in ("domain_tags", "path_patterns", "tool_tags"):
                if k in data:
                    scope[k] = data.pop(k)
            data["scope"] = scope if scope else {"domain_tags": [], "path_patterns": [], "tool_tags": []}
        # Fix: missing rewrite_rationale
        if "rewrite_rationale" not in data:
            data["rewrite_rationale"] = data.pop("rationale", data.pop("reasoning", data.pop("reason", "")))
        # Fix: severity alias
        if "severity" not in data:
            data["severity"] = "reminder"

    elif role == "synthetic_eval_generator":
        # Fix: bare array → wrapped object
        # (handled elsewhere but normalize here too for consistency)
        pass

    elif role == "posthoc_evaluator":
        # Fix: field aliases
        if "rule_application_evidence" not in data:
            data["rule_application_evidence"] = data.pop("evidence", data.pop("application_evidence", ""))
        if "would_likely_have_happened_without_rule" not in data:
            data["would_likely_have_happened_without_rule"] = data.pop(
                "without_rule", data.pop("counterfactual", "unclear")
            )
        if "reason_code" not in data:
            data["reason_code"] = data.pop("reason", data.pop("code", "unclear"))

    return data


# Keep backward-compatible alias
parse_role_output = validate_role_output


# --- Idempotency Key ---


def job_key(role: str, model_id: str, input_hash: str) -> str:
    """Deterministic job key for deduplication and caching.

    Format: role:prompt_version:model_id:input_hash
    """
    if role not in PROMPT_VERSIONS:
        raise ValueError(f"unknown role: {role}")
    return f"{role}:{PROMPT_VERSIONS[role]}:{model_id}:{input_hash}"


# --- Role Model Resolution ---

PROVIDER_DEFAULT_MODEL: str = "claude-sonnet-4-6"


def resolve_model_id(
    role: str,
    role_models_dict: dict[str, str] | None = None,
    default_model: str | None = None,
) -> str:
    """Resolve the model id for a given role.

    Resolution order:
      1. role_models_dict[role] if provided and non-empty
      2. default_model if provided and non-empty
      3. PROVIDER_DEFAULT_MODEL

    No imports from nokori.config — accepts plain dicts/strings as params.
    """
    if role not in ROLE_IDS:
        raise ValueError(f"unknown role: {role}")

    if role_models_dict:
        model = role_models_dict.get(role)
        if model and model.strip():
            return model.strip()

    if default_model and default_model.strip():
        return default_model.strip()

    return PROVIDER_DEFAULT_MODEL
