from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..config import Config
from ..db import Db
from ..models import Rule, ScoredResult
from . import bm25, ranker
from . import embedding as embedding_search


@dataclass(frozen=True)
class RetrievalResult:
    hot: list[ScoredResult]
    warm: list[ScoredResult]
    bm25_matches: int
    embed_mode: str  # off | local | remote


def retrieve_and_tier(
    prompt: str,
    rules: Sequence[Rule],
    db: Db,
    cfg: Config,
    *,
    top_k: int = 10,
) -> RetrievalResult:
    """BM25 + optional embedding RRF, then HOT/WARM tiering (formal + shadow pools)."""
    if not rules:
        return RetrievalResult([], [], 0, "off")

    bm25_results = bm25.search(prompt, rules, top_k=top_k)
    embed_results: list[ScoredResult] = []
    embed_mode = "off"

    if embedding_search.auto_enabled(cfg, len(rules)):
        if embedding_search.use_local(cfg):
            client = embedding_search.LocalEmbeddingClient(cfg)
            if client.available():
                embed_results = embedding_search.search_local(
                    prompt, rules, db, client, top_k=top_k
                )
                embed_mode = "local"
        else:
            client = embedding_search.EmbeddingClient(cfg)
            embed_results = embedding_search.search(
                prompt, rules, db, client, top_k=top_k
            )
            embed_mode = "remote"

    fused = ranker.rrf_fuse(bm25_results, embed_results)
    hot, warm = ranker.tier_results(fused)
    return RetrievalResult(hot, warm, len(bm25_results), embed_mode)
