"""Shared result dataclass for cold-path pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColdPipelineResult:
    """Outcome of a cold pipeline run for one candidate."""

    status: str  # "candidate", "active", "rejected", "pending_rewrite", "pending_split", "merged", "pending"
    rule_id: str | None
    rejection_reason: str | None
    scores: dict | None
