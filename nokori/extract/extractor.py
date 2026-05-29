from __future__ import annotations

import json
from dataclasses import dataclass

from ..llm.adapter import LLMAdapter
from ..llm.prompts import EXTRACT_PROMPT
from ..utils.logging import get_logger

log = get_logger("nokori.extract.extractor")

_VALID_SOURCE_TYPES = ("correction", "preference", "solution", "anti_pattern")
_VALID_CONFIDENCE = ("high", "medium")


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


def extract(transcript: str, llm: LLMAdapter) -> list[Candidate]:
    if not transcript.strip():
        return []
    prompt = EXTRACT_PROMPT.replace("{transcript}", transcript)
    try:
        raw = llm.complete(prompt, max_tokens=3000, timeout=60)
    except Exception as e:
        log.warning("extract LLM call failed: %s", type(e).__name__)
        return []
    if raw is None:
        return []
    return _parse_candidates(raw)


def _parse_candidates(raw: str) -> list[Candidate]:
    text = raw.strip()
    if text.startswith("```"):
        text = _strip_fence(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("extract LLM returned non-JSON; first 60 chars=%r", text[:60])
        return []

    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = data
    else:
        return []

    out: list[Candidate] = []
    for item in items:
        try:
            cand = _coerce(item)
        except (KeyError, TypeError, ValueError) as e:
            log.warning("extract candidate skipped: %s", e)
            continue
        out.append(cand)
    return out


def _strip_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _coerce(item: dict) -> Candidate:
    trigger = str(item["trigger"]).strip()
    action = str(item["action"]).strip()
    if not trigger or not action:
        raise ValueError("missing trigger or action")
    source_type = str(item.get("source_type") or "correction").strip()
    if source_type not in _VALID_SOURCE_TYPES:
        raise ValueError(f"bad source_type {source_type!r}")
    confidence = str(item.get("confidence") or "medium").strip()
    if confidence not in _VALID_CONFIDENCE:
        raise ValueError(f"bad confidence {confidence!r}")
    variants = item.get("trigger_variants") or []
    if not isinstance(variants, list):
        variants = []
    variants = [str(v).strip() for v in variants if str(v).strip()]
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
    return Candidate(
        trigger=trigger,
        trigger_variants=variants,
        search_terms=terms,
        behavior=(str(item["behavior"]).strip() if item.get("behavior") else None),
        action=action,
        rationale=(str(item["rationale"]).strip() if item.get("rationale") else None),
        source_type=source_type,
        confidence=confidence,
    )
