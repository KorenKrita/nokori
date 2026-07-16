"""RetrievalEngine — thin orchestrator for the hot-path retrieval pipeline.

Interface: (prompt, formal_pool, shadow_pool) → RetrievalResult
Pipeline: scorer → evidence evaluator → injection selector
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..config import Config
from ..db import Db
from ..models import Rule, ScoredResult
from . import embedding as _embedding
from .evidence import evaluate_evidence
from .idf_stats import IdfPoolStats, build_idf_stats, store_idf_stats
from .scorer import SearchScorer
from .selector import select_injection

InteractionKind = Literal["hook", "cli"]


@dataclass(frozen=True)
class RetrievalResult:
    hot: list[ScoredResult]
    warm: list[ScoredResult]
    shadow_hot: list[ScoredResult]
    shadow_warm: list[ScoredResult]
    bm25_matches: int
    embed_mode: str
    bm25_rule_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TierResult:
    hot: list[ScoredResult]
    warm: list[ScoredResult]
    bm25_matches: int
    embed_mode: str
    bm25_rule_ids: frozenset[str] = frozenset()


class RetrievalEngine:
    """Session-scoped retrieval engine.

    Owns IDF cache state across prompts within a session.
    Orchestrates: SearchScorer → evaluate_evidence → select_injection.
    """

    def __init__(self, cfg: Config, db: Db) -> None:
        self._cfg = cfg
        self._db = db
        self._scorer = SearchScorer(cfg, db)
        self._last_stored_pool_version: str | None = None
        self._embed_enabled: bool | None = None
        self._embed_cached_pool: int = -1

    @property
    def cfg(self) -> Config:
        return self._cfg

    @property
    def db(self) -> Db:
        return self._db

    def retrieve(
        self,
        prompt: str,
        formal_rules: Sequence[Rule],
        shadow_rules: Sequence[Rule],
        *,
        interaction: InteractionKind = "hook",
        top_k: int = 10,
    ) -> RetrievalResult:
        """Main interface: retrieve, score, decide HOT/WARM/COLD for formal + shadow pools.

        Scores the union once (shared BM25/embed/IDF), then partitions and runs
        select_injection separately so shadow never steals formal injection slots.
        """
        formal_ids = {r.id for r in formal_rules}
        shadow_only = [r for r in shadow_rules if r.id not in formal_ids]
        combined = list(formal_rules) + shadow_only
        if not combined:
            return RetrievalResult([], [], [], [], 0, "off")

        effective_pool = len(combined)
        if self._embed_enabled is None or self._embed_cached_pool != effective_pool:
            self._embed_enabled = _embedding.auto_enabled(self._cfg, effective_pool)
            self._embed_cached_pool = effective_pool

        fused = self._scorer.score(
            prompt,
            combined,
            top_k=top_k,
            interaction=interaction,
            pool_size=effective_pool,
            embed_enabled=self._embed_enabled,
        )

        # IDF baseline from formal active/trusted (same as prior shadow path).
        idf_stats = self._build_idf_stats(combined, background_idf_rules=formal_rules or None)

        eligible = [
            applied
            for r in fused
            if (applied := evaluate_evidence(r, prompt, idf_stats=idf_stats)) is not None
        ]

        formal_eligible = [r for r in eligible if r.rule.id in formal_ids]
        shadow_eligible = [r for r in eligible if r.rule.id not in formal_ids]

        formal_selection = select_injection(
            formal_eligible,
            max_injection_chars=self._cfg.max_injection_chars,
            pool_size=idf_stats.rule_pool_size,
        )
        shadow_selection = select_injection(
            shadow_eligible,
            max_injection_chars=self._cfg.max_injection_chars,
            pool_size=idf_stats.rule_pool_size,
        )

        formal_bm25_ids = frozenset(
            r.rule.id for r in fused if r.bm25_score > 0 and r.rule.id in formal_ids
        )
        return RetrievalResult(
            hot=formal_selection.hot,
            warm=formal_selection.warm,
            shadow_hot=shadow_selection.hot,
            shadow_warm=shadow_selection.warm,
            bm25_matches=len(formal_bm25_ids),
            embed_mode=self._scorer.last_embed_mode,
            bm25_rule_ids=formal_bm25_ids,
        )

    def retrieve_and_tier(
        self,
        prompt: str,
        rules: Sequence[Rule],
        *,
        top_k: int = 10,
        interaction: InteractionKind = "cli",
        pool_size: int | None = None,
        background_idf_rules: Sequence[Rule] | None = None,
    ) -> TierResult:
        if not rules:
            return TierResult([], [], 0, "off")

        effective_pool = pool_size if pool_size is not None else len(rules)
        if self._embed_enabled is None or self._embed_cached_pool != effective_pool:
            self._embed_enabled = _embedding.auto_enabled(self._cfg, effective_pool)
            self._embed_cached_pool = effective_pool

        fused = self._scorer.score(
            prompt,
            rules,
            top_k=top_k,
            interaction=interaction,
            pool_size=pool_size,
            embed_enabled=self._embed_enabled,
        )

        idf_stats = self._build_idf_stats(rules, background_idf_rules)

        eligible = [
            applied
            for r in fused
            if (applied := evaluate_evidence(r, prompt, idf_stats=idf_stats)) is not None
        ]

        selection = select_injection(
            eligible,
            max_injection_chars=self._cfg.max_injection_chars,
            pool_size=idf_stats.rule_pool_size,
        )

        bm25_ids = frozenset(r.rule.id for r in fused if r.bm25_score > 0)
        bm25_count = len(bm25_ids)
        embed_mode = self._scorer.last_embed_mode
        return TierResult(selection.hot, selection.warm, bm25_count, embed_mode, bm25_ids)

    def _build_idf_stats(
        self,
        rules: Sequence[Rule],
        background_idf_rules: Sequence[Rule] | None,
    ) -> IdfPoolStats:
        idf_pool = background_idf_rules if background_idf_rules is not None else rules
        idf_stats = build_idf_stats([r for r in idf_pool if r.status in ("active", "trusted")])
        if idf_stats.rule_pool_size == 0:
            # Cold start: no active/trusted rules, use all rules (including candidate/draft)
            # as IDF baseline to avoid complete scoring degradation.
            idf_stats = build_idf_stats(rules)
        if idf_stats.pool_version != self._last_stored_pool_version:
            store_idf_stats(self._db, idf_stats)
            self._last_stored_pool_version = idf_stats.pool_version
        return idf_stats
