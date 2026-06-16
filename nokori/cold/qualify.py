"""Cold-path qualification stage: admission, rewrite, and final judgment."""

from __future__ import annotations

import json
from typing import Any

from ..db import Db
from ..search.tokenizer import tokenize
from ..utils.logging import get_logger
from ._llm_call import (
    CircuitBreakerOpenError,
    call_llm_role as _call_llm_role,
    prompt_text as _prompt_text,
    role_max_tokens as _role_max_tokens,
    role_timeout as _role_timeout,
)
from .roles import validate_role_output

log = get_logger("nokori.cold.qualify")


def _run_admission_judge(
    db: Db,
    llm: Any,
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
        "Score calibration (0.0-1.0):\n"
        "- overall_quality: your composite assessment. 0.85+ = high-quality rule ready for use; "
        "0.6-0.85 = has potential but needs revision; below 0.6 = not worth keeping.\n"
        "- evidence_support: 0.9+ = quotes directly prove the correction happened; "
        "0.7-0.9 = quotes are related but don't perfectly match trigger/action; "
        "below 0.7 = quotes are tangential or fabricated.\n"
        "- trigger_specificity: 0.9+ = trigger describes one narrow scenario; "
        "0.7-0.9 = somewhat broad but still actionable; "
        "below 0.7 = too vague to reliably match.\n"
        "- action_clarity: 0.9+ = action is a clear imperative the assistant can follow directly; "
        "0.7-0.9 = understandable but slightly ambiguous; "
        "below 0.7 = vague or confusing, assistant wouldn't know what to do.\n"
        "- scope_control: 0.9+ = clear boundaries, won't fire on unrelated prompts; "
        "0.7-0.9 = mostly bounded but some edge cases; "
        "below 0.7 = could fire on many unrelated contexts.\n"
        "- generalization_safety: 0.9+ = rule is specific to the evidenced scenario; "
        "0.7-0.9 = slightly broader than evidence but still reasonable; "
        "0.65-0.7 = borderline, rule may stretch beyond evidence in edge cases; "
        "below 0.65 = rule generalizes well beyond what evidence supports.\n"
        "- retrieval_readiness: holistic assessment of whether this rule will actually be "
        "found and matched at runtime. The raw fields you see will be compiled into a "
        "matcher — each required_concept becomes a concept with its text as the ONLY alias, "
        "and trigger_variants become strong_anchor phrases requiring ALL concepts to match. "
        "Score LOW if the compiled structure will be too rigid to match real prompts.\n"
        "  Consider ALL of:\n"
        "  (a) required_concepts phrasing: are they short distinctive phrases (2-3 words) that "
        "a developer would actually type? Or full sentences / overly specific wording?\n"
        "  (b) trigger_variants coverage: are there enough short variants (2-5 words) covering "
        "different ways a user might express this? (commands, natural language, abbreviations)\n"
        "  (c) search_terms bilingual quality: both en and zh have distinctive keywords? "
        "Are they specific enough to recall this rule but not so generic they match everything?\n"
        "  (d) trigger distinctiveness: does the trigger contain domain-specific terms "
        "(not just common words like 'when using' or 'how to')?\n"
        "  (e) required_concepts count: if only 1 concept, a single phrase must match — "
        "fragile. 2+ concepts with short distinctive text is more robust.\n"
        "Score: 0.9+ = strong on all dimensions, will match reliably; "
        "0.7-0.9 = acceptable but needs rewriter to add alias diversity / variant granularity; "
        "below 0.7 = too rigid or generic, rule will rarely match real prompts.\n\n"
        "Output a single JSON object with these fields:\n\n"
        "REQUIRED fields:\n"
        '- "scores" (object): quality scores, each a number from 0.0 to 1.0\n'
        '  - "overall_quality" (number, REQUIRED): composite quality assessment, 0.0-1.0\n'
        '  - "evidence_support" (number, REQUIRED): how well evidence_quotes support the rule, 0.0-1.0\n'
        '  - "trigger_specificity" (number, REQUIRED): how specific/narrow the trigger is, 0.0-1.0\n'
        '  - "action_clarity" (number, REQUIRED): how clear and actionable the instruction is, 0.0-1.0\n'
        '  - "scope_control" (number, REQUIRED): how well-bounded the scope is, 0.0-1.0\n'
        '  - "generalization_safety" (number, REQUIRED): how safe from over-generalization, 0.0-1.0\n'
        '  - "retrieval_readiness" (number, REQUIRED): whether trigger has enough retrievable terms, 0.0-1.0\n'
        '- "decision" (string, REQUIRED): one of "accept", "revise", "reject"\n'
        '- "reasoning" (string, REQUIRED): brief explanation of your decision\n\n'
        "Example output:\n"
        "```json\n"
        "{\n"
        '  "scores": {\n'
        '    "overall_quality": 0.85,\n'
        '    "evidence_support": 0.90,\n'
        '    "trigger_specificity": 0.80,\n'
        '    "action_clarity": 0.88,\n'
        '    "scope_control": 0.82,\n'
        '    "generalization_safety": 0.75,\n'
        '    "retrieval_readiness": 0.78\n'
        "  },\n"
        '  "decision": "accept",\n'
        '  "reasoning": "Strong transcript evidence directly supports the trigger and action. Scope is narrow enough for reliable retrieval."\n'
        "}\n"
        "```\n"
        "Output ONLY the JSON object, no markdown fences, no extra text."
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
            "admission_judge: llm_decision=%s policy_decision=%s scores={overall=%.2f evidence=%.2f specificity=%.2f action=%.2f scope=%.2f generalization=%.2f}",
            llm_decision,
            decision,
            scores.get("overall_quality", 0),
            scores.get("evidence_support", 0),
            scores.get("trigger_specificity", 0),
            scores.get("action_clarity", 0),
            scores.get("scope_control", 0),
            scores.get("generalization_safety", 0),
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

    accept requires: overall >= 0.82, evidence >= 0.85, specificity >= 0.75,
                     scope >= 0.75, action_clarity >= 0.70, generalization_safety >= 0.65,
                     retrieval_readiness >= 0.80
    revise requires: overall >= 0.55, evidence >= 0.70
    otherwise: reject
    """
    # Default 0.0 for missing fields is intentional: absent scores fail thresholds conservatively
    overall = scores.get("overall_quality", 0.0)
    evidence = scores.get("evidence_support", 0.0)
    specificity = scores.get("trigger_specificity", 0.0)
    scope = scores.get("scope_control", 0.0)
    action_clarity = scores.get("action_clarity", 0.0)
    generalization_safety = scores.get("generalization_safety", 0.0)
    retrieval_readiness = scores.get("retrieval_readiness", 0.0)

    # Deterministic policy is authoritative regardless of LLM decision
    if (
        overall >= 0.82
        and evidence >= 0.85
        and specificity >= 0.75
        and scope >= 0.75
        and action_clarity >= 0.70
        and generalization_safety >= 0.65
        and retrieval_readiness >= 0.80
    ):
        return "accept"
    if overall >= 0.55 and evidence >= 0.70:
        return "revise"
    return "reject"


def _run_rewriter(
    db: Db,
    llm: Any,
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
        "Separate trigger, action, concepts, variants, search_terms, and excluded_contexts.\n\n"
        "Output a single JSON object with these fields:\n\n"
        "REQUIRED fields:\n"
        '- "trigger_canonical" (string, REQUIRED): the canonical trigger text, concise and specific\n'
        '- "concepts" (array of objects, REQUIRED): concept definitions used for matching. Each object:\n'
        '  - "id" (string): short identifier (e.g. "force_push", "shared_branch")\n'
        '  - "label" (string): human-readable label\n'
        '  - "aliases" (array of objects): each alias has:\n'
        '    - "text" (string): distinctive keyword or short phrase\n'
        '    - "strength" (string): "strong" (must match as substring) or "weak" (assists recall only)\n'
        '    - "requires_neighbor" (array of strings, REQUIRED when strength="weak"): other alias texts that must appear '
        'nearby for this weak alias to count. E.g. a weak alias "output" needs requires_neighbor=["test"] so it only '
        'matches when "test" is also present. Strong aliases do NOT need this field.\n'
        "    CONSTRAINTS: Strong aliases must NOT be a single common/stop word (like 'the', 'is', 'do', 'when', 'how', "
        "'use', 'run', 'for', 'with'). Use multi-word phrases or domain-specific terms for strong aliases.\n"
        "    IMPORTANT: include BOTH English AND Chinese aliases for each concept so the rule matches "
        'regardless of prompt language. E.g. [{"text": "force push", "strength": "strong"}, '
        '{"text": "强推", "strength": "strong"}, {"text": "git push --force", "strength": "strong"}, '
        '{"text": "push", "strength": "weak", "requires_neighbor": ["force"]}]\n'
        '  - "match_mode" (string): "all_terms" (all words in alias must appear) or "any_alias" (exact phrase substring)\n'
        '  - "required" (boolean): true if this concept MUST match for the rule to fire\n'
        '- "required_concept_groups" (array of objects, REQUIRED): each object has:\n'
        '  - "id" (string): group identifier\n'
        '  - "all_of" (array of strings): concept IDs that must all be present. '
        "Every ID listed MUST exist in the concepts array above.\n"
        '- "variants" (array of objects, REQUIRED): trigger phrasing variants in BOTH languages. Each object:\n'
        '  - "text" (string): a short phrase (2-5 words, MUST be multi-word for strong_anchor). '
        "Include both English AND Chinese variants as separate entries.\n"
        '  - "kind" (string): "strong_anchor" (text must appear as exact substring in prompt — high precision) or "weak_recall" (only helps BM25 retrieval — no substring requirement)\n'
        '  - "requires_concepts" (array of strings): concept IDs required alongside this variant. '
        "MUST be non-empty for strong_anchor (at least one concept ID). Use [] for weak_recall. "
        "Every ID listed MUST exist in the concepts array.\n"
        '- "excluded_contexts" (array of objects, REQUIRED): situations where the rule should NOT fire. Each:\n'
        '  - "id" (string): unique identifier (e.g. "exc_personal")\n'
        '  - "label" (string): human-readable description\n'
        '  - "patterns" (array of strings): text patterns to match. Include BOTH English AND Chinese patterns '
        "so exclusion works regardless of prompt language.\n"
        '  - "match_mode" (string): "phrase" (substring) or "all_terms" (all words present)\n'
        '  - "scope" (string): "global", "trigger", or "tool_input_only"\n'
        '- "action_instruction" (string, REQUIRED): what the agent should do\n'
        '- "severity" (string, REQUIRED): one of "reminder", "high_risk", "gate_eligible"\n'
        '- "search_terms" (object, REQUIRED): BM25 retrieval keywords — terms a developer would type when this scenario arises.\n'
        '  - "en" (array of strings): Latin-script terms (commands, identifiers, tool names, flags)\n'
        '  - "zh" (array of strings): CJK terms (if applicable, else empty [])\n'
        '- "scope" (object, REQUIRED): matching scope constraints\n'
        '  - "domain_tags" (array of strings): e.g. ["git", "testing"]\n'
        '  - "file_or_path_patterns" (array of strings): e.g. ["*.py", "tests/"]\n'
        '  - "tool_tags" (array of strings): e.g. ["Bash", "Write"]\n\n'
        "REQUIRED continued:\n"
        '- "rewrite_rationale" (string, REQUIRED): explanation of what was changed and why\n\n'
        "Example output:\n"
        "```json\n"
        "{\n"
        '  "trigger_canonical": "When force-pushing to a shared branch without --force-with-lease",\n'
        '  "concepts": [\n'
        '    {"id": "force_push", "label": "force push", "aliases": [{"text": "force push", "strength": "strong"}, {"text": "强推", "strength": "strong"}, {"text": "push --force", "strength": "strong"}, {"text": "push -f", "strength": "strong"}], "match_mode": "any_alias", "required": true},\n'
        '    {"id": "shared_branch", "label": "shared branch", "aliases": [{"text": "shared branch", "strength": "strong"}, {"text": "共享分支", "strength": "strong"}, {"text": "main branch", "strength": "strong"}, {"text": "origin main", "strength": "weak"}], "match_mode": "any_alias", "required": true}\n'
        "  ],\n"
        '  "required_concept_groups": [\n'
        '    {"id": "grp1", "all_of": ["force_push", "shared_branch"]}\n'
        "  ],\n"
        '  "variants": [\n'
        '    {"text": "force push", "kind": "strong_anchor", "requires_concepts": ["force_push"]},\n'
        '    {"text": "git push --force", "kind": "strong_anchor", "requires_concepts": ["force_push"]},\n'
        '    {"text": "强推到共享分支", "kind": "strong_anchor", "requires_concepts": ["force_push", "shared_branch"]},\n'
        '    {"text": "force push main", "kind": "strong_anchor", "requires_concepts": ["force_push", "shared_branch"]},\n'
        '    {"text": "overwrite remote", "kind": "weak_recall", "requires_concepts": []}\n'
        "  ],\n"
        '  "excluded_contexts": [\n'
        '    {"id": "exc_personal", "label": "personal branch", "patterns": ["personal branch", "feature branch", "个人分支"], "match_mode": "phrase", "scope": "global"}\n'
        "  ],\n"
        '  "action_instruction": "Use --force-with-lease instead of --force to prevent overwriting others\' work.",\n'
        '  "severity": "high_risk",\n'
        '  "search_terms": {"en": ["force-with-lease", "git push", "--force", "shared branch"], "zh": ["强推", "共享分支", "远程分支"]},\n'
        '  "scope": {\n'
        '    "domain_tags": ["git"],\n'
        '    "file_or_path_patterns": [],\n'
        '    "tool_tags": ["Bash"]\n'
        "  },\n"
        '  "rewrite_rationale": "Narrowed from generic \'git push\' to specifically force-push on shared branches. Added bilingual aliases and short variants."\n'
        "}\n"
        "```\n"
        "Output ONLY the JSON object, no markdown fences, no extra text."
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
    llm: Any,
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
        "Output a single JSON object with these fields:\n\n"
        "REQUIRED fields:\n"
        '- "decision" (string, REQUIRED): one of:\n'
        '  - "accept_active": narrow, evidence-rich, low-near-miss rule ready for active use\n'
        '  - "accept_candidate": good rule but needs shadow proof before active\n'
        '  - "reject": insufficient evidence or too broad\n\n'
        "REQUIRED continued:\n"
        '- "reasoning" (string, REQUIRED): explanation of your decision\n\n'
        "OPTIONAL fields:\n"
        '- "evidence_citations" (array of strings): verbatim quotes from the evidence that support your decision\n\n'
        "Example output:\n"
        "```json\n"
        "{\n"
        '  "decision": "accept_candidate",\n'
        '  "reasoning": "Rule has solid evidence but trigger is moderately broad. Needs shadow observation to confirm precision.",\n'
        '  "evidence_citations": [\n'
        '    "user said: always use --force-with-lease not --force",\n'
        '    "the assistant corrected itself after the user\'s feedback"\n'
        "  ]\n"
        "}\n"
        "```\n"
        "Output ONLY the JSON object, no markdown fences, no extra text."
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
        decision: str = result["decision"]
        log.info("final_judge: decision=%s", decision)
        return decision
    except CircuitBreakerOpenError:
        raise
    except ValueError:
        raise  # Propagate for retry (spec section 13)


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
        "trigger_canonical": candidate.get("trigger", ""),
        "trigger_canonical_zh": candidate.get("trigger_zh"),
        "action_instruction": candidate.get("action", ""),
        "action_instruction_zh": candidate.get("action_zh"),
        "severity": candidate.get("severity", "reminder"),
        "required_concept_groups": _draft_concept_groups(candidate),
        "concepts": _draft_concepts(candidate),
        "excluded_contexts": _draft_excluded_contexts(candidate),
        "variants": _draft_variants(candidate),
        "trigger_variants_zh": candidate.get("trigger_variants_zh", []),
        "near_miss_examples": candidate.get("near_miss_examples", []),
        "search_terms": candidate.get("search_terms", {}),
        "scope": {
            "domain_tags": candidate.get("domain_tags", []),
            "tool_tags": candidate.get("tool_tags", []),
            "file_or_path_patterns": candidate.get("file_or_path_patterns", []),
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
    updated["variants"] = [
        {
            "text": trigger,
            "kind": "strong_anchor" if strong_anchor else "weak_recall",
            "requires_concepts": required_concepts if strong_anchor else [],
        }
    ]
    return updated


def _draft_concept_groups(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Build minimal concept groups from draft concepts.

    When no explicit concepts are provided, derives a fallback concept from the
    trigger text so that the rule has at least one concept group and can match.
    """
    from ..matcher.compiler import _is_single_generic_token as _is_generic

    concepts_draft = candidate.get("required_concepts", [])
    if not concepts_draft:
        trigger = str(candidate.get("trigger") or "").strip()
        if not trigger:
            return []
        return [{"id": "primary_group", "all_of": ["concept_0"]}]

    # Must match the same filtering logic as _draft_concepts
    valid_ids = []
    for i, concept_text in enumerate(concepts_draft):
        parts = [p.strip() for p in concept_text.split(" / ") if p.strip()]
        if not parts:
            parts = [concept_text]
        parts = [p for p in parts if not _is_generic(p)]
        if parts:
            valid_ids.append(f"concept_{i}")
    if not valid_ids:
        return []
    return [{"id": "primary_group", "all_of": valid_ids}]


def _draft_concepts(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Build concept entries from draft concepts.

    When no explicit concepts are provided, derives a fallback concept from the
    trigger text with match_mode=all_terms so substring matching isn't required.
    """
    concepts_draft = candidate.get("required_concepts", [])
    if not concepts_draft:
        trigger = str(candidate.get("trigger") or "").strip()
        if not trigger:
            return []
        # Intentionally conservative: all tokens must appear to avoid false matches.
        # required=False: aliases won't contribute to trigger_coverage anchors, but
        # the concept group still requires this concept to match for
        # required_concepts_match. Rule can fire via other evidence paths.
        return [
            {
                "id": "concept_0",
                "label": trigger[:80],
                "aliases": [{"text": trigger[:120], "strength": "strong"}],
                "match_mode": "all_terms",
                "required": False,
            }
        ]

    from ..matcher.compiler import _is_single_generic_token

    result = []
    for i, concept_text in enumerate(concepts_draft):
        # Parse " / " separated aliases (e.g. "force push / git push --force / 强推")
        parts = [p.strip() for p in concept_text.split(" / ") if p.strip()]
        if not parts:
            parts = [concept_text]
        # Filter out single generic tokens that compiler would reject as strong aliases
        parts = [p for p in parts if not _is_single_generic_token(p)]
        if not parts:
            continue
        aliases = [{"text": p, "strength": "strong"} for p in parts]
        result.append(
            {
                "id": f"concept_{i}",
                "label": parts[0][:80],
                "aliases": aliases,
                "match_mode": "any_alias",
                "required": True,
            }
        )
    return result


def _draft_excluded_contexts(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Build excluded context entries from draft."""
    excluded_draft = candidate.get("excluded_contexts", [])
    result = []
    for i, ctx_text in enumerate(excluded_draft):
        # Parse " / " separated patterns (e.g. "personal branch / 个人分支")
        patterns = [p.strip() for p in ctx_text.split(" / ") if p.strip()]
        if not patterns:
            patterns = [ctx_text]
        result.append(
            {
                "id": f"excluded_{i}",
                "label": patterns[0][:80],
                "patterns": patterns,
                "match_mode": "phrase",
                "scope": "global",
                "window_tokens": 12,
                "override_allowed": False,
                "override_requires": [],
            }
        )
    return result


def _draft_variants(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Build variant entries from draft trigger variants."""
    variants_draft = candidate.get("trigger_variants", [])
    trigger = candidate.get("trigger", "")
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
        result.append(
            {
                "text": text,
                "kind": "strong_anchor" if strong_anchor else "weak_recall",
                "requires_concepts": required_concepts if strong_anchor else [],
            }
        )
    return result
