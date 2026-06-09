"""SearchScorer — internal seam owning BM25 + embedding + RRF fusion.

Interface: score(prompt, rules) → list[ScoredResult]
Implementation: BM25 scoring, optional embedding, RRF merge — all internal.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ..config import Config
from ..db import Db
from ..models import Rule, ScoredResult
from . import bm25, ranker
from . import embedding as embedding_search

InteractionKind = Literal["hook", "cli"]


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
        embed_results: list[ScoredResult] = []
        self.last_embed_mode = "off"

        effective_pool = pool_size if pool_size is not None else len(rules)
        if embedding_search.auto_enabled(self._cfg, effective_pool):
            if embedding_search.use_local(self._cfg):
                timeout = float(
                    self._cfg.embed_hook_timeout_seconds if interaction == "hook" else 30
                )
                embed_results, self.last_embed_mode = embedding_search.search_local_shared(
                    prompt, rules, self._db, self._cfg,
                    top_k=top_k, timeout=timeout, interaction=interaction,
                )
            else:
                timeout = (
                    self._cfg.embed_hook_timeout_seconds if interaction == "hook" else 10
                )
                client = embedding_search.EmbeddingClient(self._cfg)
                embed_results = embedding_search.search(
                    prompt, rules, self._db, client, top_k=top_k, timeout=timeout
                )
                self.last_embed_mode = "remote"

        return ranker.rrf_fuse(bm25_results, embed_results)
