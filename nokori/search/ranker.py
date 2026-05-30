from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from ..models import ScoredResult

MIN_ABSOLUTE_SCORE = 0.005
RRF_K = 60


def rrf_fuse(
    bm25_results: Sequence[ScoredResult],
    embed_results: Sequence[ScoredResult],
) -> list[ScoredResult]:
    scores: dict[str, float] = defaultdict(float)
    rule_map: dict[str, ScoredResult] = {}

    for rank, r in enumerate(bm25_results):
        scores[r.rule.id] += 1.0 / (RRF_K + rank + 1)
        rule_map[r.rule.id] = r

    for rank, r in enumerate(embed_results):
        scores[r.rule.id] += 1.0 / (RRF_K + rank + 1)
        if r.rule.id not in rule_map:
            rule_map[r.rule.id] = r
        elif r.cosine is not None:
            rule_map[r.rule.id] = replace(rule_map[r.rule.id], cosine=r.cosine)

    fused: list[ScoredResult] = []
    for rule_id, score in sorted(scores.items(), key=lambda kv: -kv[1]):
        rec = rule_map[rule_id]
        fused.append(replace(rec, rrf_score=score))
    return fused


def meets_min_evidence(r: ScoredResult) -> bool:
    if len(r.matched_tokens) >= 2:
        return True
    if len(r.matched_tokens) >= 1 and r.has_trigger_variant_match:
        return True
    if r.cosine is not None and r.cosine >= 0.55:
        return True
    return False


def tier_results(
    results: Sequence[ScoredResult],
) -> tuple[list[ScoredResult], list[ScoredResult]]:
    """Split results into HOT and WARM tiers per product-spec §3.2 (docs/product-spec.md)."""
    if not results:
        return [], []
    top5 = list(results[:5])
    hot: list[ScoredResult] = []
    warm: list[ScoredResult] = []

    top1 = top5[0]
    top1_dominant = (
        len(top5) == 1
        or top1.rrf_score - top5[1].rrf_score > top5[1].rrf_score * 0.3
    )

    for i, r in enumerate(top5):
        if r.rrf_score < MIN_ABSOLUTE_SCORE:
            continue
        if not meets_min_evidence(r):
            continue
        if i == 0 and top1_dominant:
            if r.rule.status == "active":
                hot.append(r)
                continue
            if r.rule.status == "dormant":
                warm.append(replace(r, retrieval_hot=True))
                continue
        warm.append(r)
    return hot, warm
