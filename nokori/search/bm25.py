from __future__ import annotations

import math
from collections import Counter, OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..db import loads_json
from ..models import Rule, ScoredResult
from .tokenizer import tokenize

K1 = 1.2
B = 0.75

# Reuse IDF/doc index when BM25-relevant fields are unchanged (not updated_at).
_INDEX_CACHE: OrderedDict[tuple, tuple] = OrderedDict()
_INDEX_CACHE_MAX = 64


def clear_index_cache() -> None:
    """Clear the module-level BM25 index cache (for tests)."""
    _INDEX_CACHE.clear()


# ---------------------------------------------------------------------------
# Fielded token extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FieldedDoc:
    rule: Rule
    trigger_tokens: frozenset[str]
    action_tokens: frozenset[str]
    search_tokens: frozenset[str]
    variant_tokens: frozenset[str]
    variant_phrases: list[list[str]]  # tokenized full phrases for exact match
    all_tf: Counter  # term frequency across all fields
    doc_len: int


def _tokenize_trigger(rule: Rule) -> list[str]:
    pieces: list[str] = []
    pieces.extend(tokenize(rule.trigger_canonical))
    if rule.trigger_canonical_zh:
        pieces.extend(tokenize(rule.trigger_canonical_zh))
    return pieces


def _tokenize_action(rule: Rule) -> list[str]:
    pieces: list[str] = []
    pieces.extend(tokenize(rule.action_instruction))
    if rule.action_instruction_zh:
        pieces.extend(tokenize(rule.action_instruction_zh))
    return pieces


def _tokenize_search(rule: Rule) -> list[str]:
    pieces: list[str] = []
    for terms in rule.search_terms.values():
        for t in terms:
            pieces.extend(tokenize(t))
    return pieces


def _tokenize_variants(rule: Rule) -> tuple[list[str], list[list[str]]]:
    pieces: list[str] = []
    phrases: list[list[str]] = []
    variants = (
        loads_json(rule.trigger_variants, [])
        if isinstance(rule.trigger_variants, str)
        else rule.trigger_variants
    )
    for v in variants:
        if isinstance(v, dict):
            v = v.get("text", "")
        toks = tokenize(v)
        pieces.extend(toks)
        if toks:
            phrases.append(toks)
    for v in rule.trigger_variants_zh:
        toks = tokenize(v)
        pieces.extend(toks)
        if toks:
            phrases.append(toks)
    return pieces, phrases


def _build_fielded_doc(rule: Rule) -> _FieldedDoc:
    trigger_toks = _tokenize_trigger(rule)
    action_toks = _tokenize_action(rule)
    search_toks = _tokenize_search(rule)
    variant_toks, variant_phrases = _tokenize_variants(rule)

    all_tokens = trigger_toks + action_toks + search_toks + variant_toks
    all_tf = Counter(all_tokens)

    return _FieldedDoc(
        rule=rule,
        trigger_tokens=frozenset(trigger_toks),
        action_tokens=frozenset(action_toks),
        search_tokens=frozenset(search_toks),
        variant_tokens=frozenset(variant_toks),
        variant_phrases=variant_phrases,
        all_tf=all_tf,
        doc_len=len(all_tokens),
    )


def _index_key(rules_list: list[Rule]) -> tuple:
    def _rule_key(r: Rule) -> tuple:
        terms = tuple(
            (lang, tuple(sorted(vals)))
            for lang, vals in sorted(r.search_terms.items())
        )
        return (
            r.id,
            r.trigger_canonical,
            r.action_instruction,
            r.trigger_variants
            if isinstance(r.trigger_variants, str)
            else tuple(r.trigger_variants),
            tuple(r.trigger_variants_zh),
            terms,
            r.trigger_canonical_zh,
            r.action_instruction_zh,
        )

    return tuple(_rule_key(r) for r in sorted(rules_list, key=lambda r: r.id))


def _build_index(rules_list: list[Rule]):
    fielded_docs = [_build_fielded_doc(rule) for rule in rules_list]
    n_docs = len(fielded_docs)
    avgdl = sum(d.doc_len for d in fielded_docs) / max(n_docs, 1)
    df: Counter[str] = Counter()
    for doc in fielded_docs:
        df.update(frozenset(doc.all_tf))
    idf: Mapping[str, float] = {
        term: math.log(1 + (n_docs - count + 0.5) / (count + 0.5))
        for term, count in df.items()
    }
    return fielded_docs, idf, avgdl


def _cached_index(rules_list: list[Rule]):
    key = _index_key(rules_list)
    if key in _INDEX_CACHE:
        _INDEX_CACHE.move_to_end(key)
        return _INDEX_CACHE[key]
    cached = _build_index(rules_list)
    _INDEX_CACHE[key] = cached
    while len(_INDEX_CACHE) > _INDEX_CACHE_MAX:
        _INDEX_CACHE.popitem(last=False)
    return cached


def _check_phrase_hit(query_tokens: list[str], phrases: list[list[str]]) -> bool:
    """True if any full variant phrase appears as a contiguous subsequence in query."""
    if not phrases:
        return False
    for phrase in phrases:
        plen = len(phrase)
        if plen == 0:
            continue
        for i in range(len(query_tokens) - plen + 1):
            if query_tokens[i : i + plen] == phrase:
                return True
    return False


def search(
    query: str, rules: Iterable[Rule], top_k: int = 5
) -> list[ScoredResult]:
    rules_list = list(rules)
    if not rules_list:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    fielded_docs, idf, avgdl = _cached_index(rules_list)

    qset = frozenset(query_tokens)
    scored: list[ScoredResult] = []
    for doc in fielded_docs:
        if not doc.all_tf:
            continue
        score = 0.0
        tf = doc.all_tf
        dl = doc.doc_len
        for term in qset:
            f = tf.get(term, 0)
            if f == 0:
                continue
            num = f * (K1 + 1)
            denom = f + K1 * (1 - B + B * dl / max(avgdl, 1))
            score += idf.get(term, 0.0) * (num / denom)
        if score <= 0:
            continue

        # Fielded token matching
        matched_trigger = qset & doc.trigger_tokens
        matched_action = qset & doc.action_tokens
        matched_search = qset & doc.search_tokens
        matched_variant = qset & doc.variant_tokens

        # Phrase-level variant hit
        strong_variant = _check_phrase_hit(query_tokens, doc.variant_phrases)

        # Match source flags
        has_trigger = bool(matched_trigger)
        action_only = bool(matched_action) and not has_trigger and not bool(matched_variant)
        search_only = bool(matched_search) and not has_trigger and not bool(matched_variant) and not bool(matched_action)

        scored.append(
            ScoredResult(
                rule=doc.rule,
                bm25_score=score,
                matched_trigger_tokens=frozenset(matched_trigger),
                matched_action_tokens=frozenset(matched_action),
                matched_search_tokens=frozenset(matched_search),
                matched_variant_tokens=frozenset(matched_variant),
                strong_variant_phrase_hit=strong_variant,
                action_only_match=action_only,
                search_only_match=search_only,
            )
        )

    scored.sort(key=lambda r: r.bm25_score, reverse=True)
    return scored[:top_k]
