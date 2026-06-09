"""Backward-compatible selection API.

Selection logic has been absorbed into search.engine. This module re-exports
public symbols for test and external consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nokori.models import ScoredResult
from nokori.search.engine import (
    compute_utility,
    mmr_penalty as _engine_mmr_penalty,
    select_injection as _engine_select_injection,
)
from nokori.policy import WARM_HARD_MAX


@dataclass(frozen=True)
class SelectionResult:
    """Immutable result of injection selection."""

    hot: list[ScoredResult] = field(default_factory=list)
    warm: list[ScoredResult] = field(default_factory=list)
    shadow_matches: list[ScoredResult] = field(default_factory=list)


def mmr_penalty(
    candidate_tokens: frozenset[str],
    selected_tokens_list: list[frozenset[str]],
) -> float:
    """Max Jaccard similarity with any selected rule * penalty weight."""
    return _engine_mmr_penalty(candidate_tokens, selected_tokens_list)


def select_injection(
    eligible_results: list[ScoredResult],
    max_injection_chars: int,
    warm_hard_max: int = WARM_HARD_MAX,
    pool_size: int = 0,
) -> SelectionResult:
    """Backward-compat wrapper delegating to engine._select_injection."""
    result = _engine_select_injection(
        eligible_results,
        max_injection_chars=max_injection_chars,
        warm_hard_max=warm_hard_max,
        pool_size=pool_size,
    )
    return SelectionResult(
        hot=result.hot,
        warm=result.warm,
        shadow_matches=result.shadow_matches,
    )
