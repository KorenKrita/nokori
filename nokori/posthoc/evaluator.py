"""Posthoc evaluation role — judges one fire event from bounded evidence.

The evaluator is partially blind: it sees the injected suggestion in neutral
wording and the bounded outcome window, but not historical scores, rule status,
promotion targets, or desired labels.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Labels and reason codes (section 10.2)
# ---------------------------------------------------------------------------

POSTHOC_LABELS: tuple[str, ...] = (
    "observed_useful",
    "plausible_useful",
    "irrelevant",
    "harmful",
    "unclear",
)

POSTHOC_REASON_CODES: tuple[str, ...] = (
    "useful_prevented_error",
    "useful_improved_quality",
    "useful_followed_preference",
    "irrelevant_not_applicable",
    "irrelevant_redundant",
    "irrelevant_unused",
    "harmful_distracted",
    "harmful_wrong_scope",
    "harmful_blocked_valid_action",
)

ATTRIBUTION_ANSWERS: tuple[str, ...] = ("yes", "no", "unclear")

# ---------------------------------------------------------------------------
# PosthocOutput schema (stricter than the roles.py POSTHOC_EVALUATOR_SCHEMA,
# which predates the final section-10.2 spec)
# ---------------------------------------------------------------------------

POSTHOC_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "enum": list(POSTHOC_LABELS),
        },
        "reason_code": {
            "type": "string",
            "enum": list(POSTHOC_REASON_CODES),
        },
        "rule_application_evidence": {"type": "string"},
        "would_likely_have_happened_without_rule": {
            "type": "string",
            "enum": list(ATTRIBUTION_ANSWERS),
        },
    },
    "required": [
        "label",
        "reason_code",
        "would_likely_have_happened_without_rule",
    ],
}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

POSTHOC_SYSTEM_PROMPT: str = """\
You are an impartial evaluator judging whether a prior reminder was useful \
during a coding assistant session.

You will receive:
- A prior reminder that was shown to the assistant (phrased neutrally).
- The prompt/context that triggered the reminder.
- A bounded transcript window showing what happened after the reminder.
- Any agent or CLI feedback tied to this event.
- Decision features from the time the reminder was shown.

Your job:
1. Determine whether the assistant's behavior shows influence from the reminder.
2. Assess whether the outcome would likely have been the same without it.
3. Assign a label and reason code.

Rules:
- Judge only this single event, not the rule's general quality.
- Do not reward based on any score or status — you have none.
- The reminder is phrased as "a prior reminder suggested X" — treat it as one \
possible influence among many.
- Look for concrete behavioral evidence: did the assistant do something it \
otherwise would not have, avoid an error, follow an unusual preference?
- If evidence is ambiguous, prefer "unclear" over speculation.

Label guidance:
- "observed_useful": The transcript shows the assistant explicitly following \
the reminder's advice in a way it would not have done otherwise. \
Example: reminder says "use --force-with-lease", assistant uses --force-with-lease \
and the transcript shows it considered --force first.
- "plausible_useful": The outcome is consistent with the reminder helping \
but you cannot confirm the assistant actually used it. \
Example: reminder says "check disk space", assistant checks disk space, \
but it might have done so anyway.
- "irrelevant": The reminder's topic has nothing to do with what happened.
- "harmful": The reminder actively misled or distracted the assistant. \
Use reason_code harmful_* to specify how.
- "unclear": Cannot determine from available information.

The "would_likely_have_happened_without_rule" field is an independent counterfactual \
judgment. If you label "observed_useful" but also answer "yes" (it would have \
happened anyway), that means the assistant's behavior coincidentally aligned with \
the reminder but wasn't caused by it — which is actually "plausible_useful" or \
"irrelevant", not "observed_useful". Use "observed_useful" only when the answer is \
"no" (the reminder made the difference).

Return a single JSON object (no markdown fences, no extra text):
{{
  "label": "{label_options}",
  "reason_code": "{reason_code_options}",
  "rule_application_evidence": "<specific evidence from the transcript>",
  "would_likely_have_happened_without_rule": "{attribution_options}"
}}
""".format(
    label_options="|".join(POSTHOC_LABELS),
    reason_code_options="|".join(POSTHOC_REASON_CODES),
    attribution_options="|".join(ATTRIBUTION_ANSWERS),
)

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_posthoc_prompt(evaluator_input: dict) -> str:
    """Format evaluator input into a user message.

    Expected keys in evaluator_input:
      - injected_suggestion: str (the action text, neutralized)
      - injection_context: str (prompt/context that caused injection)
      - transcript_window: str (bounded transcript after injection)
      - feedback: str | None (agent/CLI feedback if any)
      - decision_features: dict (features from injection time)

    Must NOT include: rule status, historical scores, promotion target.
    """
    suggestion = evaluator_input.get("injected_suggestion", "")
    context = evaluator_input.get("injection_context", "")
    transcript = evaluator_input.get("transcript_window", "")
    feedback = evaluator_input.get("feedback")
    features = evaluator_input.get("decision_features", {})

    parts: list[str] = []

    parts.append("## Prior Reminder")
    parts.append(f"A prior reminder suggested: {suggestion}")

    parts.append("\n## Context That Triggered the Reminder")
    parts.append(context)

    parts.append("\n## Transcript Window After Reminder")
    parts.append(transcript)

    if feedback:
        parts.append("\n## Agent/CLI Feedback")
        parts.append(feedback)

    if features:
        parts.append("\n## Decision Features at Injection Time")
        parts.append(json.dumps(features, indent=2))

    parts.append("\n## Instructions")
    parts.append(
        "Based on the above, produce a single JSON object with: "
        "label, reason_code, rule_application_evidence, "
        "would_likely_have_happened_without_rule."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------


def parse_posthoc_output(raw_json: str) -> dict:
    """Validate raw LLM output against PosthocOutput schema.

    Returns parsed dict on success.
    Raises ValueError on parse or validation failure.
    """
    from ..llm.json_payload import parse_json_payload

    text = raw_json.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = parse_json_payload(text)
        if data is None:
            raise ValueError(
                "posthoc_evaluator: invalid JSON: could not extract JSON from response"
            )

    if not isinstance(data, dict):
        raise ValueError(f"posthoc_evaluator: expected JSON object, got {type(data).__name__}")

    # Normalize field aliases
    if "would_likely_have_happened_without_rule" not in data:
        val = None
        for alias in ("without_rule", "counterfactual"):
            if alias in data:
                val = data.pop(alias)
                break
        data["would_likely_have_happened_without_rule"] = val if val is not None else "unclear"
    if "reason_code" not in data:
        val = None
        for alias in ("reason", "code"):
            if alias in data:
                val = data.pop(alias)
                break
        if val is None:
            raise ValueError(
                "posthoc_evaluator: missing required field 'reason_code' (no alias found)"
            )
        if val not in POSTHOC_REASON_CODES:
            raise ValueError(
                f"posthoc_evaluator: alias-popped reason_code {val!r} not in POSTHOC_REASON_CODES"
            )
        data["reason_code"] = val

    # Validate required fields
    for field in POSTHOC_OUTPUT_SCHEMA["required"]:
        if field not in data:
            raise ValueError(f"posthoc_evaluator: missing required field '{field}'")

    # Validate enum values
    label = data.get("label")
    if label not in POSTHOC_LABELS:
        raise ValueError(
            f"posthoc_evaluator: invalid label {label!r}, must be one of {POSTHOC_LABELS}"
        )

    reason_code = data.get("reason_code")
    if reason_code not in POSTHOC_REASON_CODES:
        raise ValueError(
            f"posthoc_evaluator: invalid reason_code {reason_code!r}, "
            f"must be one of {POSTHOC_REASON_CODES}"
        )

    attribution = data.get("would_likely_have_happened_without_rule")
    if attribution not in ATTRIBUTION_ANSWERS:
        raise ValueError(
            f"posthoc_evaluator: invalid attribution {attribution!r}, "
            f"must be one of {ATTRIBUTION_ANSWERS}"
        )

    return data


# ---------------------------------------------------------------------------
# Attribution weight (section 10.2 rubric)
# ---------------------------------------------------------------------------


def compute_attribution_weight(posthoc_output: dict) -> float:
    """Compute attribution weight from posthoc output.

    Attribution weight is for informational/audit purposes only; lifecycle uses event counts.

    From section 10.2:
      observed_useful + would_not_have_happened (no)  = 1.0 (strong)
      observed_useful + unclear                        = 0.5 (weak)
      observed_useful + would_have_happened (yes)      = 0.0 (redundant)
      plausible_useful (any attribution)               = 0.3
      irrelevant                                       = -0.5
      harmful                                          = -2.0
      unclear                                          = 0.0
    """
    label = posthoc_output.get("label")
    attribution = posthoc_output.get("would_likely_have_happened_without_rule")

    if label == "observed_useful":
        if attribution == "no":
            return 1.0
        if attribution == "unclear":
            return 0.5
        # attribution == "yes" -> treat as redundant
        return 0.0

    if label == "plausible_useful":
        return 0.3

    if label == "irrelevant":
        return -0.5

    if label == "harmful":
        return -2.0

    # unclear or unrecognized
    return 0.0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


_POSTHOC_RESULT_CACHE: dict[str, dict | None] = {}
_POSTHOC_CACHE_MAX_SIZE: int = 256


def _compute_input_hash(evaluator_input: dict) -> str:
    """Compute a stable hash of the evaluator input for idempotency."""
    serialized = json.dumps(evaluator_input, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


_POSTHOC_MAX_ATTEMPTS = 2


def run_posthoc_evaluation(llm: Any, evaluator_input: dict) -> dict | None:
    """Call LLM with posthoc role, parse and validate output.

    Args:
        llm: An object with a .call(system: str, user: str, **kwargs) method
             that returns a string response.
        evaluator_input: Dict with keys as expected by build_posthoc_prompt.

    Returns:
        Validated posthoc output dict with added 'attribution_weight' field,
        or None if LLM call fails or output is unparseable after retries.
    """
    # Idempotency: check if this exact input was already processed
    input_hash = _compute_input_hash(evaluator_input)
    if input_hash in _POSTHOC_RESULT_CACHE:
        # LRU: move to end on access
        cached = _POSTHOC_RESULT_CACHE.pop(input_hash)
        _POSTHOC_RESULT_CACHE[input_hash] = cached
        return cached

    user_message = build_posthoc_prompt(evaluator_input)

    for _attempt in range(_POSTHOC_MAX_ATTEMPTS):
        try:
            raw_response = llm.call(
                system=POSTHOC_SYSTEM_PROMPT,
                user=user_message,
                role="posthoc_evaluator",
            )
        except Exception as exc:
            logger.warning("posthoc_evaluator LLM call failed (attempt %d): %s", _attempt + 1, exc)
            continue

        try:
            result = parse_posthoc_output(raw_response)
        except ValueError as exc:
            logger.warning("posthoc_evaluator parse failed (attempt %d): %s", _attempt + 1, exc)
            continue

        result["attribution_weight"] = compute_attribution_weight(result)

        # Cache the result (evict oldest if at capacity)
        if len(_POSTHOC_RESULT_CACHE) >= _POSTHOC_CACHE_MAX_SIZE:
            oldest_key = next(iter(_POSTHOC_RESULT_CACHE))
            del _POSTHOC_RESULT_CACHE[oldest_key]
        _POSTHOC_RESULT_CACHE[input_hash] = result

        return result

    return None
