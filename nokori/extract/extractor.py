from __future__ import annotations

from dataclasses import dataclass

from ..constants import MAX_EXTRACT_CANDIDATES
from ..llm.adapter import LLMAdapter
from ..llm.json_payload import parse_json_payload
from ..llm.prompts import EXTRACT_SYSTEM, wrap_untrusted
from ..utils.logging import get_logger

from .term_normalize import normalize_search_terms, normalize_trigger_variants

log = get_logger("nokori.extract.extractor")

_VALID_SOURCE_TYPES = ("correction", "preference", "solution", "anti_pattern")
_VALID_CONFIDENCE = ("high", "medium")


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
    source_type: str
    confidence: str
    trigger_zh: str | None = None
    behavior_zh: str | None = None
    action_zh: str | None = None
    rationale_zh: str | None = None


def _opt_str(item: dict, key: str) -> str | None:
    return (str(item[key]).strip() or None) if item.get(key) else None


def extract(transcript: str, llm: LLMAdapter) -> tuple[list[Candidate], bool]:
    """Returns (candidates, llm_ok). llm_ok=False when the LLM call failed (not empty parse)."""
    if not transcript.strip():
        return [], True
    user_content = wrap_untrusted(transcript)
    try:
        raw = llm.complete_messages(
            EXTRACT_SYSTEM, user_content, max_tokens=3000, timeout=60,
        )
    except Exception as e:
        log.warning("extract LLM call failed: %s", type(e).__name__)
        return [], False
    if raw is None:
        return [], False
    candidates, parse_ok = _parse_candidates(raw)
    if not parse_ok:
        return [], False
    return candidates, True


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
    source_type = str(item.get("source_type") or "correction").strip()
    if source_type not in _VALID_SOURCE_TYPES:
        raise ValueError(f"bad source_type {source_type!r}")
    confidence = str(item.get("confidence") or "medium").strip()
    if confidence not in _VALID_CONFIDENCE:
        raise ValueError(f"bad confidence {confidence!r}")
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
    trigger_zh = _opt_str(item, "trigger_zh")
    behavior_zh = _opt_str(item, "behavior_zh")
    action_zh = _opt_str(item, "action_zh")
    rationale_zh = _opt_str(item, "rationale_zh")
    return Candidate(
        trigger=trigger,
        trigger_variants=variants,
        search_terms=terms,
        behavior=(str(item["behavior"]).strip() if item.get("behavior") else None),
        action=action,
        rationale=(str(item["rationale"]).strip() if item.get("rationale") else None),
        source_type=source_type,
        confidence=confidence,
        trigger_zh=trigger_zh,
        behavior_zh=behavior_zh,
        action_zh=action_zh,
        rationale_zh=rationale_zh,
    )
