from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..config import Config
from ..db import Db, total_rule_count
from ..models import Rule, ScoredResult
from . import bm25, ranker
from . import embedding as embedding_search

InteractionKind = Literal["hook", "cli"]


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
    interaction: InteractionKind = "cli",
    pool_size: int | None = None,
) -> RetrievalResult:
    """BM25 + optional embedding RRF, then HOT/WARM tiering (formal + shadow pools).

    Local embedding uses a shared embed server (one loaded model for all hooks).
    Remote embed uses a shorter timeout on hook path (cfg.embed_hook_timeout_seconds).
    """
    if not rules:
        return RetrievalResult([], [], 0, "off")

    bm25_results = bm25.search(prompt, rules, top_k=top_k)
    embed_results: list[ScoredResult] = []
    embed_mode = "off"

    if pool_size is None:
        pool_size = total_rule_count(db)
    if embedding_search.auto_enabled(cfg, pool_size):
        if embedding_search.use_local(cfg):
            timeout = float(
                cfg.embed_hook_timeout_seconds if interaction == "hook" else 30
            )
            embed_results, embed_mode = embedding_search.search_local_shared(
                prompt,
                rules,
                db,
                cfg,
                top_k=top_k,
                timeout=timeout,
                interaction=interaction,
            )
        else:
            timeout = (
                cfg.embed_hook_timeout_seconds
                if interaction == "hook"
                else 10
            )
            client = embedding_search.EmbeddingClient(cfg)
            embed_results = embedding_search.search(
                prompt, rules, db, client, top_k=top_k, timeout=timeout
            )
            embed_mode = "remote"

    fused = ranker.rrf_fuse(bm25_results, embed_results)
    hot, warm = ranker.tier_results(fused)
    return RetrievalResult(hot, warm, len(bm25_results), embed_mode)
