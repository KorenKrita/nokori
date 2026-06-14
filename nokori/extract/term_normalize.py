"""Normalize extract search_terms and trigger_variants after LLM parse."""

from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")

# zh phrases that only locate a correction, not useful for retrieval
_ZH_DROP_EXACT = frozenset(
    {
        "不对",
        "不是这里",
        "不是这个意思",
        "换一个",
        "别在这里",
        "别用",
    }
)

_VARIANT_ACTOR_PREFIXES = (
    "user ",
    "the user ",
    "assistant ",
    "the assistant ",
    "when the user ",
    "when the assistant ",
)


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def split_mixed_term(term: str) -> tuple[list[str], list[str]]:
    """Split one term into (en_tokens, zh_phrases)."""
    en: list[str] = []
    zh: list[str] = []
    for m in _CJK_RE.finditer(term):
        chunk = m.group().strip()
        if chunk:
            zh.append(chunk)
    for m in _LATIN_TOKEN_RE.finditer(term):
        token = m.group().strip()
        if token:
            en.append(token)
    return en, zh


def _should_drop_zh(term: str) -> bool:
    t = term.strip()
    if not t:
        return True
    if t in _ZH_DROP_EXACT:
        return True
    # Short "不是…" locator phrases (~≤6 han chars), not substantive lessons.
    # Mixed strings with Latin (e.g. 不是用mock而是用真实数据库) are kept via CJK span length.
    if t.startswith("不是"):
        cjk_len = sum(len(m.group()) for m in _CJK_RE.finditer(t))
        if cjk_len <= 6:
            return True
    return False


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        key = x.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out


def normalize_search_terms(terms: dict[str, list[str]]) -> dict[str, list[str]]:
    en_out: list[str] = []
    zh_out: list[str] = []

    for lang, items in terms.items():
        if not isinstance(items, list):
            continue
        bucket_en = lang.lower() in ("en", "english")
        bucket_zh = lang.lower() in ("zh", "chinese", "cn")
        for raw in items:
            term = str(raw).strip()
            if not term:
                continue
            if bucket_en or (not bucket_zh and not has_cjk(term)):
                if has_cjk(term):
                    split_en, split_zh = split_mixed_term(term)
                    en_out.extend(split_en)
                    zh_out.extend(split_zh)
                else:
                    en_out.append(term)
            elif bucket_zh or has_cjk(term):
                if has_cjk(term) and _LATIN_TOKEN_RE.search(term):
                    split_en, split_zh = split_mixed_term(term)
                    en_out.extend(split_en)
                    zh_out.extend(split_zh)
                elif has_cjk(term):
                    zh_out.append(term)
                else:
                    en_out.append(term)
            else:
                en_out.append(term)

    en_out = _dedupe_preserve(en_out)
    zh_out = _dedupe_preserve([t for t in zh_out if not _should_drop_zh(t)])

    out: dict[str, list[str]] = {}
    if en_out:
        out["en"] = en_out
    if zh_out:
        out["zh"] = zh_out
    return out


def normalize_trigger_variants(variants: list[str]) -> list[str]:
    out: list[str] = []
    for v in variants:
        s = str(v).strip()
        if not s:
            continue
        lower = s.casefold()
        if lower.startswith(_VARIANT_ACTOR_PREFIXES):
            continue
        out.append(s)
    return out[:3]
