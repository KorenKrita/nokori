from __future__ import annotations

import math
from collections import Counter, OrderedDict
from collections.abc import Iterable, Mapping

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


def _rule_doc_tokens(rule: Rule) -> list[str]:
    pieces: list[str] = []
    pieces.extend(tokenize(rule.trigger_text))
    if rule.trigger_text_zh:
        pieces.extend(tokenize(rule.trigger_text_zh))
    pieces.extend(tokenize(rule.action))
    if rule.action_zh:
        pieces.extend(tokenize(rule.action_zh))
    for v in rule.trigger_variants:
        pieces.extend(tokenize(v))
    for terms in rule.search_terms.values():
        for t in terms:
            pieces.extend(tokenize(t))
    return pieces


def _variant_tokens(rule: Rule) -> set[str]:
    tokens: set[str] = set()
    for v in rule.trigger_variants:
        tokens.update(tokenize(v))
    return tokens


def _index_key(rules_list: list[Rule]) -> tuple:
    def _rule_key(r: Rule) -> tuple:
        terms = tuple(
            (lang, tuple(sorted(vals)))
            for lang, vals in sorted(r.search_terms.items())
        )
        return (
            r.id,
            r.trigger_text,
            r.action,
            tuple(r.trigger_variants),
            terms,
            r.trigger_text_zh,
            r.action_zh,
            r.behavior_zh,
            r.rationale_zh,
        )

    return tuple(_rule_key(r) for r in sorted(rules_list, key=lambda r: r.id))


def _build_index(rules_list: list[Rule]):
    raw_docs = [(rule, _rule_doc_tokens(rule), _variant_tokens(rule)) for rule in rules_list]
    n_docs = len(raw_docs)
    avgdl = sum(len(d) for _, d, _ in raw_docs) / max(n_docs, 1)
    df: Counter[str] = Counter()
    docs = []
    for rule, doc_tokens, var_tokens in raw_docs:
        tf = Counter(doc_tokens)
        token_set = frozenset(tf)
        df.update(token_set)
        docs.append((rule, tf, token_set, len(doc_tokens), var_tokens))
    idf: Mapping[str, float] = {
        term: math.log(1 + (n_docs - count + 0.5) / (count + 0.5))
        for term, count in df.items()
    }
    return docs, idf, avgdl


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


def search(
    query: str, rules: Iterable[Rule], top_k: int = 5
) -> list[ScoredResult]:
    rules_list = list(rules)
    if not rules_list:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    docs, idf, avgdl = _cached_index(rules_list)

    qset = frozenset(query_tokens)
    scored: list[ScoredResult] = []
    for rule, tf, token_set, dl, var_tokens in docs:
        if not tf:
            continue
        score = 0.0
        for term in qset:
            f = tf.get(term, 0)
            if f == 0:
                continue
            num = f * (K1 + 1)
            denom = f + K1 * (1 - B + B * dl / max(avgdl, 1))
            score += idf.get(term, 0.0) * (num / denom)
        if score <= 0:
            continue
        matched = qset & token_set
        variant_match = bool(qset & var_tokens)
        scored.append(
            ScoredResult(
                rule=rule,
                bm25_score=score,
                matched_tokens=frozenset(matched),
                has_trigger_variant_match=variant_match,
            )
        )

    scored.sort(key=lambda r: r.bm25_score, reverse=True)
    return scored[:top_k]
