from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ..cold.roles import validate_role_output
from ..constants import MAX_EXTRACT_CANDIDATES
from ..llm.adapter import LLMAdapter
from ..llm.prompts import EXTRACT_SYSTEM, wrap_untrusted
from ..utils.logging import get_logger

from .term_normalize import normalize_search_terms, normalize_trigger_variants

log = get_logger("nokori.extract.extractor")



def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


@dataclass
class Candidate:
    trigger: str
    trigger_variants: list[str]
    search_terms: dict[str, list[str]]
    behavior: str | None
    action: str
    rationale: str | None
    evidence_quotes: list[str] = dataclasses.field(default_factory=list)
    trigger_text_zh: str | None = None
    action_zh: str | None = None
    trigger_variants_zh: list[str] = dataclasses.field(default_factory=list)
    required_concepts: list[str] = dataclasses.field(default_factory=list)
    excluded_contexts: list[str] = dataclasses.field(default_factory=list)
    non_generalization_boundaries: list[str] = dataclasses.field(default_factory=list)
    near_miss_examples: list[str] = dataclasses.field(default_factory=list)
    severity: str = "reminder"
    domain_tags: list[str] = dataclasses.field(default_factory=list)
    tool_tags: list[str] = dataclasses.field(default_factory=list)
    file_or_path_patterns: list[str] = dataclasses.field(default_factory=list)


def _opt_str(item: dict, key: str) -> str | None:
    return (str(item[key]).strip() or None) if item.get(key) else None


_EXTRACT_MAX_RETRIES = 2


def extract(transcript: str, llm: LLMAdapter) -> tuple[list[Candidate], bool]:
    """Returns (candidates, llm_ok). llm_ok=False when the LLM call failed (not empty parse)."""
    if not transcript.strip():
        return [], True
    user_content = wrap_untrusted(transcript)
    last_error: str | None = None
    for attempt in range(_EXTRACT_MAX_RETRIES + 1):
        try:
            raw = llm.complete_messages(
                EXTRACT_SYSTEM, user_content, max_tokens=3000, timeout=60,
            )
        except Exception as e:
            log.warning("extract LLM call failed (attempt %d): %s", attempt + 1, type(e).__name__)
            last_error = str(e)
            continue
        if raw is None:
            last_error = "LLM returned None"
            continue
        candidates, parse_ok = _parse_candidates(raw)
        if not parse_ok:
            if attempt < _EXTRACT_MAX_RETRIES:
                log.warning("extract parse failed (attempt %d), retrying", attempt + 1)
            last_error = "parse failed"
            continue
        return candidates, True
    log.warning("extract failed after %d attempts: %s", _EXTRACT_MAX_RETRIES + 1, last_error)
    return [], False


def _parse_candidates(raw: str) -> tuple[list[Candidate], bool]:
    try:
        validated = validate_role_output("extractor", raw)
        items = validated.get("candidates", [])
    except (ValueError, TypeError) as e:  # TypeError: raw may be None from LLM
        log.warning("extract schema validation failed: %s", e)
        return [], False

    out: list[Candidate] = []
    had_items = bool(items)
    for item in items:
        try:
            cand = _coerce(item)
        except (KeyError, TypeError, ValueError) as e:
            log.warning("extract candidate skipped: %s", e)
            continue
        out.append(cand)
    if had_items and not out:
        return [], False
    if len(out) > MAX_EXTRACT_CANDIDATES:
        log.warning(
            "extract truncated %d candidates to %d",
            len(out),
            MAX_EXTRACT_CANDIDATES,
        )
        out = out[:MAX_EXTRACT_CANDIDATES]
    return out, True


def _coerce_str_list(item: dict, key: str) -> list[str]:
    raw = item.get(key) or []
    if not isinstance(raw, list):
        return []
    return [str(v).strip() for v in raw if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip()]


def _coerce(item: dict) -> Candidate:
    trigger = str(item.get("trigger") or "").strip()
    action = str(item.get("action") or "").strip()
    if not trigger or not action:
        raise ValueError("missing trigger or action")
    if _has_cjk(trigger):
        raise ValueError("trigger must be English (CJK in trigger)")
    variants = item.get("trigger_variants") or []
    if not isinstance(variants, list):
        variants = []
    variants = normalize_trigger_variants(
        [str(v).strip() for v in variants if str(v).strip()]
    )
    terms_raw = item.get("search_terms") or {}
    if not isinstance(terms_raw, dict):
        terms_raw = {}
    terms: dict[str, list[str]] = {}
    for lang, items_ in terms_raw.items():
        if not isinstance(items_, list):
            continue
        cleaned = [str(x).strip() for x in items_ if str(x).strip()]
        if cleaned:
            terms[str(lang)] = cleaned
    terms = normalize_search_terms(terms)
    trigger_text_zh = _opt_str(item, "trigger_zh")
    action_zh = _opt_str(item, "action_zh")
    trigger_variants_zh = _coerce_str_list(item, "trigger_variants_zh")
    evidence_quotes = _coerce_str_list(item, "evidence_quotes")
    required_concepts = _coerce_str_list(item, "required_concepts")
    excluded_contexts = _coerce_str_list(item, "excluded_contexts")
    non_gen = _coerce_str_list(item, "non_generalization_boundaries")
    near_miss = _coerce_str_list(item, "near_miss_examples")

    severity = str(item.get("severity", "reminder")).strip()
    if severity not in ("reminder", "high_risk", "gate_eligible"):
        log.warning("unknown severity %r, defaulting to 'reminder'", severity)
        severity = "reminder"

    domain_tags = _coerce_str_list(item, "domain_tags")
    tool_tags = _coerce_str_list(item, "tool_tags")
    path_patterns = _coerce_str_list(item, "file_or_path_patterns")

    return Candidate(
        trigger=trigger,
        trigger_variants=variants,
        search_terms=terms,
        behavior=_opt_str(item, "behavior"),
        action=action,
        rationale=_opt_str(item, "rationale"),
        evidence_quotes=evidence_quotes,
        trigger_text_zh=trigger_text_zh,
        action_zh=action_zh,
        trigger_variants_zh=trigger_variants_zh,
        required_concepts=required_concepts,
        excluded_contexts=excluded_contexts,
        non_generalization_boundaries=non_gen,
        near_miss_examples=near_miss,
        severity=severity,
        domain_tags=domain_tags,
        tool_tags=tool_tags,
        file_or_path_patterns=path_patterns,
    )
