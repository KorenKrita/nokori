from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping

from ..models import Rule, ScoredResult
from .tokenizer import tokenize

K1 = 1.2
B = 0.75

# Reuse IDF/doc index when the rule set is unchanged (same ids + updated_at).
_INDEX_CACHE: dict[tuple[tuple[str, str], ...], tuple] = {}
_INDEX_CACHE_MAX = 64


def _rule_doc_tokens(rule: Rule) -> list[str]:
    pieces: list[str] = []
    pieces.extend(tokenize(rule.trigger_text))
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


def _index_key(rules_list: list[Rule]) -> tuple[tuple[str, str], ...]:
    return tuple((r.id, r.updated_at) for r in sorted(rules_list, key=lambda r: r.id))


def _build_index(rules_list: list[Rule]):
    docs = [(rule, _rule_doc_tokens(rule), _variant_tokens(rule)) for rule in rules_list]
    n_docs = len(docs)
    avgdl = sum(len(d) for _, d, _ in docs) / max(n_docs, 1)
    df: Counter[str] = Counter()
    for _, doc, _ in docs:
        df.update(set(doc))
    idf: Mapping[str, float] = {
        term: math.log(1 + (n_docs - count + 0.5) / (count + 0.5))
        for term, count in df.items()
    }
    return docs, idf, avgdl


def _cached_index(rules_list: list[Rule]):
    key = _index_key(rules_list)
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    cached = _build_index(rules_list)
    if len(_INDEX_CACHE) >= _INDEX_CACHE_MAX:
        _INDEX_CACHE.clear()
    _INDEX_CACHE[key] = cached
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

    qset = set(query_tokens)
    scored: list[ScoredResult] = []
    for rule, doc, var_tokens in docs:
        if not doc:
            continue
        tf = Counter(doc)
        dl = len(doc)
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
        matched = qset & set(doc)
        variant_match = bool(qset & var_tokens)
        scored.append(
            ScoredResult(
                rule=rule,
                bm25_score=score,
                matched_tokens=matched,
                has_trigger_variant_match=variant_match,
            )
        )

    scored.sort(key=lambda r: r.bm25_score, reverse=True)
    return scored[:top_k]
