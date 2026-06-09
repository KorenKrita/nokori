"""Backward-compatible retrieval API delegating to RetrievalEngine.

External consumers (tests, CLI commands) that import from this module continue to work.
New code should use RetrievalEngine directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..config import Config
from ..db import Db
from ..models import Rule, ScoredResult
from .engine import RetrievalEngine

InteractionKind = Literal["hook", "cli"]

_module_engine: RetrievalEngine | None = None


def _get_engine(cfg: Config, db: Db) -> RetrievalEngine:
    """Reuse a module-level engine to preserve IDF cache across calls.

    Single-threaded: hook processes serve one session sequentially.
    """
    global _module_engine
    if _module_engine is None or _module_engine.db is not db or _module_engine.cfg is not cfg:
        _module_engine = RetrievalEngine(cfg, db)
    return _module_engine


def _reset_engine() -> None:
    """Reset module-level engine for test isolation."""
    global _module_engine
    _module_engine = None


@dataclass(frozen=True)
class RetrievalResult:
    hot: list[ScoredResult]
    warm: list[ScoredResult]
    bm25_matches: int
    embed_mode: str
    bm25_rule_ids: frozenset[str] = frozenset()


def retrieve_and_tier(
    prompt: str,
    rules: Sequence[Rule],
    db: Db,
    cfg: Config,
    *,
    top_k: int = 10,
    interaction: InteractionKind = "cli",
    pool_size: int | None = None,
    background_idf_rules: Sequence[Rule] | None = None,
) -> RetrievalResult:
    """Backward-compat wrapper. Delegates to RetrievalEngine.retrieve_and_tier."""
    engine = _get_engine(cfg, db)
    result = engine.retrieve_and_tier(
        prompt,
        rules,
        top_k=top_k,
        interaction=interaction,
        pool_size=pool_size,
        background_idf_rules=background_idf_rules,
    )
    return RetrievalResult(
        hot=result.hot,
        warm=result.warm,
        bm25_matches=result.bm25_matches,
        embed_mode=result.embed_mode,
        bm25_rule_ids=result.bm25_rule_ids,
    )


def retrieve_formal_and_shadow(
    prompt: str,
    formal_rules: Sequence[Rule],
    shadow_rules: Sequence[Rule],
    db: Db,
    cfg: Config,
    *,
    pool_size: int | None = None,
    interaction: InteractionKind = "hook",
) -> tuple[RetrievalResult, list[ScoredResult], list[ScoredResult]]:
    """Backward-compat wrapper using module-level retrieve_and_tier (patchable)."""
    formal_ids = {r.id for r in formal_rules}
    shadow_only = [r for r in shadow_rules if r.id not in formal_ids]
    combined = list(formal_rules) + shadow_only
    if not combined:
        empty = RetrievalResult([], [], 0, "off")
        return empty, [], []

    effective_pool = pool_size if pool_size is not None else len(combined)
    formal_result = retrieve_and_tier(
        prompt,
        formal_rules,
        db,
        cfg,
        top_k=10,
        interaction=interaction,
        pool_size=effective_pool,
    )
    shadow_result = retrieve_and_tier(
        prompt,
        shadow_only,
        db,
        cfg,
        top_k=10,
        interaction=interaction,
        pool_size=effective_pool,
        background_idf_rules=formal_rules,
    )
    return formal_result, shadow_result.hot, shadow_result.warm
