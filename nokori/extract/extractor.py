from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ..constants import MAX_EXTRACT_CANDIDATES
from ..llm.adapter import LLMAdapter
from ..llm.json_payload import parse_json_payload
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
            log.warning("extract parse failed (attempt %d), retrying", attempt + 1)
            last_error = "parse failed"
            continue
        return candidates, True
    log.warning("extract failed after %d attempts: %s", _EXTRACT_MAX_RETRIES + 1, last_error)
    return [], False


def _parse_candidates(raw: str) -> tuple[list[Candidate], bool]:
    data = parse_json_payload(raw)
    if data is None:
        log.warning(
            "extract LLM returned non-JSON; first 60 chars=%r",
            (raw or "").strip()[:60],
        )
        return [], False

    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = data
    else:
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


def _coerce(item: dict) -> Candidate:
    if "trigger" not in item or "action" not in item:
        raise ValueError("missing trigger or action keys")
    trigger = str(item["trigger"]).strip()
    action = str(item["action"]).strip()
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
    variants_zh_raw = item.get("trigger_variants_zh") or []
    if not isinstance(variants_zh_raw, list):
        variants_zh_raw = []
    trigger_variants_zh = [str(v).strip() for v in variants_zh_raw if str(v).strip()]
    evidence_raw = item.get("evidence_quotes") or []
    if not isinstance(evidence_raw, list):
        evidence_raw = []
    evidence_quotes = [str(q).strip() for q in evidence_raw if str(q).strip()]
    return Candidate(
        trigger=trigger,
        trigger_variants=variants,
        search_terms=terms,
        behavior=(str(item["behavior"]).strip() if item.get("behavior") else None),
        action=action,
        rationale=(str(item["rationale"]).strip() if item.get("rationale") else None),
        evidence_quotes=evidence_quotes,
        trigger_text_zh=trigger_text_zh,
        action_zh=action_zh,
        trigger_variants_zh=trigger_variants_zh,
    )
