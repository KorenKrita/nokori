from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from ..models import ScoredResult

RRF_K = 60


def rrf_fuse(
    bm25_results: Sequence[ScoredResult],
    embed_results: Sequence[ScoredResult],
) -> list[ScoredResult]:
    """Reciprocal Rank Fusion of BM25 and embedding results."""
    scores: dict[str, float] = defaultdict(float)
    rule_map: dict[str, ScoredResult] = {}

    for rank, r in enumerate(bm25_results):
        scores[r.rule.id] += 1.0 / (RRF_K + rank + 1)
        rule_map[r.rule.id] = r

    for rank, r in enumerate(embed_results):
        scores[r.rule.id] += 1.0 / (RRF_K + rank + 1)
        if r.rule.id not in rule_map:
            rule_map[r.rule.id] = r
        else:
            rule_map[r.rule.id] = merge_scored_fields(rule_map[r.rule.id], r)

    fused: list[ScoredResult] = []
    for rule_id, score in sorted(scores.items(), key=lambda kv: -kv[1]):
        rec = rule_map[rule_id]
        fused.append(replace(rec, rrf_score=score))
    return fused


def merge_scored_fields(
    bm25_result: ScoredResult, embed_result: ScoredResult
) -> ScoredResult:
    """Combine fielded evidence from BM25 and embedding sources into one ScoredResult.

    BM25 provides token-level field matches; embedding provides cosine and
    embedding_only_match. The merged result preserves all fielded data from BM25
    and adds embedding evidence.
    """
    embedding_only = (
        embed_result.cosine is not None
        and not bm25_result.matched_trigger_tokens
        and not bm25_result.matched_variant_tokens
    )
    return replace(
        bm25_result,
        cosine=embed_result.cosine,
        embedding_only_match=embedding_only,
        embedding_profile_bucket=embed_result.embedding_profile_bucket,
        embedding_profile_version=embed_result.embedding_profile_version,
        embedding_profile_unknown=embed_result.embedding_profile_unknown,
    )
