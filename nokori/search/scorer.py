"""SearchScorer — internal seam owning BM25 + embedding + RRF fusion.

Interface: score(prompt, rules) → list[ScoredResult]
Implementation: BM25 scoring, optional embedding, RRF merge — all internal.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

from ..config import Config
from ..db import Db
from ..models import Rule, ScoredResult
from . import bm25, embedding as embedding_search

InteractionKind = Literal["hook", "cli"]

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


def merge_scored_fields(bm25_result: ScoredResult, embed_result: ScoredResult) -> ScoredResult:
    """Combine fielded evidence from BM25 and embedding sources."""
    embedding_only = (
        embed_result.cosine is not None
        and not bm25_result.matched_trigger_tokens
        and not bm25_result.matched_variant_tokens
        and not bm25_result.matched_action_tokens
        and not bm25_result.matched_search_tokens
    )
    return replace(
        bm25_result,
        cosine=embed_result.cosine,
        embedding_only_match=embedding_only,
        embedding_profile_bucket=embed_result.embedding_profile_bucket,
        embedding_profile_version=embed_result.embedding_profile_version,
        embedding_profile_unknown=embed_result.embedding_profile_unknown,
    )


class SearchScorer:
    """Scores rules against a prompt using BM25 + optional embedding RRF."""

    def __init__(self, cfg: Config, db: Db) -> None:
        self._cfg = cfg
        self._db = db
        self.last_embed_mode: str = "off"

    def score(
        self,
        prompt: str,
        rules: Sequence[Rule],
        *,
        top_k: int = 10,
        interaction: InteractionKind = "cli",
        pool_size: int | None = None,
    ) -> list[ScoredResult]:
        if not rules:
            self.last_embed_mode = "off"
            return []

        bm25_results = bm25.search(prompt, rules, top_k=top_k)

        embed_results, self.last_embed_mode = embedding_search.search_auto(
            prompt,
            rules,
            self._db,
            self._cfg,
            top_k=top_k,
            interaction=interaction,
            pool_size=pool_size,
        )

        return rrf_fuse(bm25_results, embed_results)
